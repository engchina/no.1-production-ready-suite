"""Select AI 実クライアント(Oracle Autonomous Database への実呼び出し)。

NL2SQL の中核 ``DBMS_CLOUD_AI`` / ``DBMS_CLOUD_AI_AGENT`` を実 DB で実行する。
低レベルの接続/プールは ``app.clients.oracle.OracleClient`` の共有プールを再利用し、
本モジュールは Select AI 固有の呼び出しに専念する。

提供機能:
- ``generate``: ``SET_PROFILE`` → ``DBMS_CLOUD_AI.GENERATE``(showsql/runsql/narrate/explainsql/chat)
- ``run_team``: ``DBMS_CLOUD_AI_AGENT.RUN_TEAM``(多段 + 会話)
- ``create_conversation``: ``DBMS_CLOUD_AI.CREATE_CONVERSATION``
- ``check_privileges``: ``DBMS_CLOUD_AI(_AGENT)`` の EXECUTE 権限診断(sql-assist 由来)
- ``apply_provisioning_plan``: ``ProvisioningPlan`` を冪等実行(``config_hash`` 一致で skip)

CI は実 ADB 非依存。fake pool/connection を注入し決定論でテストする。確定スタックは不変。
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from enum import StrEnum
from typing import Any, cast

from app.clients.oracle import (
    OracleConnectionProtocol,
    OraclePoolProtocol,
    SelectAiUnavailableError,
)
from app.config import Settings, get_settings
from app.select_ai.provisioning import ProvisioningPlan

logger = logging.getLogger(__name__)

type DbCallRunner = Callable[[Callable[[], Any]], Awaitable[Any]]

SELECT_AI_PROFILE_REQUIRED = "Select AI の profile 名が必要です。"
SELECT_AI_TEAM_REQUIRED = "Select AI Agent の team 名が必要です。"
SELECT_AI_CONVERSATION_FAILED = "Select AI Agent 用 conversation_id を作成できませんでした。"

# 権限不足(コンパイル時)を示す Oracle エラーコード。
_MISSING_PRIVILEGE_MARKERS = ("PLS-00201", "PLS-00904")


class SelectAiGenerateAction(StrEnum):
    """Select AI ``GENERATE`` の action。"""

    SHOWSQL = "showsql"
    RUNSQL = "runsql"
    NARRATE = "narrate"
    EXPLAINSQL = "explainsql"
    CHAT = "chat"


_SET_PROFILE_SQL = "BEGIN DBMS_CLOUD_AI.SET_PROFILE(profile_name => :name); END;"
_GENERATE_SQL = (
    "SELECT DBMS_CLOUD_AI.GENERATE("
    "prompt => :prompt, profile_name => :profile_name, action => :action"
    ") AS result_text FROM dual"
)
_CREATE_CONVERSATION_SQL = "SELECT DBMS_CLOUD_AI.CREATE_CONVERSATION AS conversation_id FROM dual"
_RUN_TEAM_SQL = (
    "SELECT DBMS_CLOUD_AI_AGENT.RUN_TEAM("
    "team_name => :team_name, prompt => :prompt"
    ") AS result_text FROM dual"
)
_RUN_TEAM_WITH_PARAMS_SQL = (
    "SELECT DBMS_CLOUD_AI_AGENT.RUN_TEAM("
    "team_name => :team_name, prompt => :prompt, params => :params"
    ") AS result_text FROM dual"
)
# 権限プローブ(存在しない名前 + 例外握り潰し)。EXECUTE 不足ならコンパイル時 PLS-00201 が出る。
_PRIV_PROBE_CLOUD_AI = (
    "BEGIN DBMS_CLOUD_AI.SET_PROFILE(profile_name => :name); "
    "EXCEPTION WHEN OTHERS THEN NULL; END;"
)
_PRIV_PROBE_CLOUD_AI_AGENT = (
    "BEGIN DBMS_CLOUD_AI_AGENT.DROP_TEAM(team_name => :name); "
    "EXCEPTION WHEN OTHERS THEN NULL; END;"
)
_PRIV_PROBE_NAME = "___nl2sql_priv_probe___"


@dataclass(frozen=True)
class SelectAiGenerateResult:
    """``GENERATE`` の結果。"""

    profile_name: str
    action: str
    text: str


@dataclass(frozen=True)
class SelectAiAgentResult:
    """``RUN_TEAM`` の結果(reply は JSON から抽出した本文、raw は生応答)。"""

    team_name: str
    conversation_id: str | None
    reply: str
    raw: str


@dataclass(frozen=True)
class PrivilegeCheckResult:
    """Select AI 関連 package の EXECUTE 権限診断。"""

    dbms_cloud_ai: bool
    dbms_cloud_ai_agent: bool

    @property
    def ok(self) -> bool:
        """両 package を実行可能か。"""
        return self.dbms_cloud_ai and self.dbms_cloud_ai_agent


@dataclass(frozen=True)
class ProvisioningResult:
    """プロビジョニング実行結果(skip した場合は ``provisioned=False``)。"""

    team_name: str
    config_hash: str
    provisioned: bool
    skipped_reason: str | None
    executed_kinds: tuple[str, ...]


@dataclass(frozen=True)
class SqlExecutionResult:
    """承認済み SELECT の実行結果(read-only)。"""

    columns: tuple[str, ...]
    rows: tuple[tuple[object, ...], ...]
    row_count: int
    truncated: bool


async def _default_db_call_runner(operation: Callable[[], Any]) -> Any:
    """同期 python-oracledb 呼び出しを event loop 外の thread で実行する。"""
    return await asyncio.to_thread(operation)


def _coerce_text(value: object) -> str:
    """CLOB/LOB や任意値を文字列へ変換する(``.read()`` を尊重)。"""
    if value is None:
        return ""
    reader = getattr(value, "read", None)
    if callable(reader):
        try:
            return str(reader())
        except Exception:  # noqa: BLE001 - LOB 読み取り失敗は文字列化で代替
            return str(value)
    return str(value)


def _fetch_scalar(
    connection: OracleConnectionProtocol, sql: str, binds: Mapping[str, object]
) -> object:
    """1 行 1 列のスカラを取得する。"""
    cursor = connection.cursor()
    try:
        cursor.execute(sql, dict(binds))
        row = cursor.fetchone()
        if not row:
            return None
        return row[0]
    finally:
        cursor.close()


def _execute_statement(
    connection: OracleConnectionProtocol, sql: str, binds: Mapping[str, object]
) -> None:
    """結果を取らない文(PL/SQL ブロック等)を実行する。"""
    cursor = connection.cursor()
    try:
        cursor.execute(sql, dict(binds))
    finally:
        cursor.close()


def _is_missing_privilege(error: Exception) -> bool:
    """エラーが EXECUTE 権限不足(コンパイル時)を示すか。"""
    message = str(error).upper()
    return any(marker in message for marker in _MISSING_PRIVILEGE_MARKERS)


def _coerce_cell(value: object) -> object:
    """結果セルを JSON 親和な値へ正規化する(LOB は ``.read()``、それ以外の非基本型は文字列化)。"""
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    reader = getattr(value, "read", None)
    if callable(reader):
        try:
            return str(reader())
        except Exception:  # noqa: BLE001 - LOB 読み取り失敗は文字列化で代替
            return str(value)
    return str(value)


def _extract_reply(raw: str) -> str:
    """RUN_TEAM の JSON 応答から本文(reply/content 等)を抽出する。"""
    text = (raw or "").strip()
    if not text:
        return ""
    try:
        obj = json.loads(text)
    except (ValueError, TypeError):
        return text
    if isinstance(obj, dict):
        for key in ("reply", "content", "final_answer", "response", "answer"):
            value = obj.get(key)
            if isinstance(value, str) and value.strip():
                return value
        return json.dumps(obj, ensure_ascii=False, indent=2)
    if isinstance(obj, list):
        return json.dumps(obj, ensure_ascii=False, indent=2)
    return text


class SelectAiClient:
    """Oracle Select AI / Select AI Agent の実呼び出しクライアント。"""

    def __init__(
        self,
        settings: Settings | None = None,
        *,
        pool: OraclePoolProtocol | None = None,
        db_call_runner: DbCallRunner | None = None,
    ) -> None:
        self._settings = settings or get_settings()
        self._pool_instance = pool
        self._db_call_runner = db_call_runner or _default_db_call_runner

    async def generate(
        self,
        prompt: str,
        *,
        profile_name: str,
        action: SelectAiGenerateAction = SelectAiGenerateAction.SHOWSQL,
        set_profile: bool = True,
        max_result_chars: int | None = None,
    ) -> SelectAiGenerateResult:
        """``SET_PROFILE`` → ``DBMS_CLOUD_AI.GENERATE`` を同一セッションで実行する。"""
        profile = (profile_name or "").strip()
        if not profile:
            raise SelectAiUnavailableError(SELECT_AI_PROFILE_REQUIRED)
        action_value = SelectAiGenerateAction(action).value
        limit = max_result_chars or self._settings.oracle_select_ai_max_result_chars

        def _op(connection: OracleConnectionProtocol) -> str:
            if set_profile:
                _execute_statement(connection, _SET_PROFILE_SQL, {"name": profile})
            value = _fetch_scalar(
                connection,
                _GENERATE_SQL,
                {"prompt": prompt, "profile_name": profile, "action": action_value},
            )
            return _coerce_text(value)

        text = cast(str, await self._with_connection(_op))
        return SelectAiGenerateResult(profile_name=profile, action=action_value, text=text[:limit])

    async def create_conversation(self) -> str:
        """``DBMS_CLOUD_AI.CREATE_CONVERSATION`` で会話 ID を発行する。"""

        def _op(connection: OracleConnectionProtocol) -> str:
            return _coerce_text(_fetch_scalar(connection, _CREATE_CONVERSATION_SQL, {}))

        conversation_id = cast(str, await self._with_connection(_op)).strip()
        if not conversation_id:
            raise SelectAiUnavailableError(SELECT_AI_CONVERSATION_FAILED)
        return conversation_id

    async def run_team(
        self,
        prompt: str,
        *,
        team_name: str,
        conversation_id: str | None = None,
    ) -> SelectAiAgentResult:
        """``DBMS_CLOUD_AI_AGENT.RUN_TEAM`` で多段 NL2SQL を実行する。"""
        team = (team_name or "").strip()
        if not team:
            raise SelectAiUnavailableError(SELECT_AI_TEAM_REQUIRED)
        conversation = (conversation_id or "").strip() or None

        def _op(connection: OracleConnectionProtocol) -> str:
            if conversation:
                params = json.dumps({"conversation_id": conversation}, ensure_ascii=False)
                raw = _fetch_scalar(
                    connection,
                    _RUN_TEAM_WITH_PARAMS_SQL,
                    {"team_name": team, "prompt": prompt, "params": params},
                )
            else:
                raw = _fetch_scalar(
                    connection, _RUN_TEAM_SQL, {"team_name": team, "prompt": prompt}
                )
            return _coerce_text(raw)

        raw = cast(str, await self._with_connection(_op))
        return SelectAiAgentResult(
            team_name=team,
            conversation_id=conversation,
            reply=_extract_reply(raw),
            raw=raw,
        )

    async def run_select(
        self,
        sql: str,
        *,
        binds: Mapping[str, object] | None = None,
        max_rows: int = 1000,
    ) -> SqlExecutionResult:
        """承認済み read-only SELECT を実行して行を取得する。

        ガードレールで read-only 検証済みの SQL を前提とする。``max_rows`` を超えた分は
        切り捨て(``truncated=True``)。**実行前の人手承認は呼び出し側の責務。**
        """
        bound = dict(binds or {})

        def _op(connection: OracleConnectionProtocol) -> tuple[tuple[str, ...], list[Any], bool]:
            cursor = connection.cursor()
            try:
                cursor.execute(sql, bound)
                description = getattr(cursor, "description", None) or []
                columns = tuple(str(col[0]) for col in description)
                fetched = list(cursor.fetchall())
                truncated = len(fetched) > max_rows
                return columns, fetched[:max_rows], truncated
            finally:
                cursor.close()

        columns, fetched, truncated = cast(
            "tuple[tuple[str, ...], list[Any], bool]", await self._with_connection(_op)
        )
        rows = tuple(tuple(_coerce_cell(cell) for cell in row) for row in fetched)
        return SqlExecutionResult(
            columns=columns, rows=rows, row_count=len(rows), truncated=truncated
        )

    async def check_privileges(self) -> PrivilegeCheckResult:
        """``DBMS_CLOUD_AI(_AGENT)`` の EXECUTE 権限を診断する。"""

        def _op(connection: OracleConnectionProtocol) -> tuple[bool, bool]:
            return (
                self._probe_privilege(connection, _PRIV_PROBE_CLOUD_AI),
                self._probe_privilege(connection, _PRIV_PROBE_CLOUD_AI_AGENT),
            )

        cloud_ai, cloud_ai_agent = cast("tuple[bool, bool]", await self._with_connection(_op))
        return PrivilegeCheckResult(dbms_cloud_ai=cloud_ai, dbms_cloud_ai_agent=cloud_ai_agent)

    async def apply_provisioning_plan(
        self,
        plan: ProvisioningPlan,
        *,
        current_config_hash: str | None = None,
    ) -> ProvisioningResult:
        """``ProvisioningPlan`` を冪等実行する。``config_hash`` 一致なら skip(drift なし)。"""
        if current_config_hash is not None and current_config_hash == plan.config_hash:
            return ProvisioningResult(
                team_name=plan.names.team_name,
                config_hash=plan.config_hash,
                provisioned=False,
                skipped_reason="config_hash_unchanged",
                executed_kinds=(),
            )

        def _op(connection: OracleConnectionProtocol) -> tuple[str, ...]:
            executed: list[str] = []
            for statement in plan.statements:
                _execute_statement(connection, statement.sql, statement.binds)
                executed.append(statement.kind)
            return tuple(executed)

        executed_kinds = cast("tuple[str, ...]", await self._in_transaction(_op))
        return ProvisioningResult(
            team_name=plan.names.team_name,
            config_hash=plan.config_hash,
            provisioned=True,
            skipped_reason=None,
            executed_kinds=executed_kinds,
        )

    # --- 内部: 接続/実行 ---

    @staticmethod
    def _probe_privilege(connection: OracleConnectionProtocol, sql: str) -> bool:
        """権限プローブを実行し、PLS-00201 等が出なければ実行可能と判定する。"""
        try:
            _execute_statement(connection, sql, {"name": _PRIV_PROBE_NAME})
        except Exception as exc:  # noqa: BLE001 - 権限以外のエラーは package 到達=権限ありと解釈
            return not _is_missing_privilege(exc)
        return True

    async def _with_connection(self, op: Callable[[OracleConnectionProtocol], Any]) -> Any:
        return await self._db_call_runner(lambda: self._run_with_connection(op))

    async def _in_transaction(self, op: Callable[[OracleConnectionProtocol], Any]) -> Any:
        return await self._db_call_runner(lambda: self._run_in_transaction(op))

    def _run_with_connection(self, op: Callable[[OracleConnectionProtocol], Any]) -> Any:
        connection = self._pool().acquire()
        try:
            return op(connection)
        finally:
            connection.close()

    def _run_in_transaction(self, op: Callable[[OracleConnectionProtocol], Any]) -> Any:
        connection = self._pool().acquire()
        try:
            result = op(connection)
            connection.commit()
            return result
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def _pool(self) -> OraclePoolProtocol:
        if self._pool_instance is not None:
            return self._pool_instance
        from app.clients.oracle import OracleClient

        return OracleClient(self._settings).connection_pool()
