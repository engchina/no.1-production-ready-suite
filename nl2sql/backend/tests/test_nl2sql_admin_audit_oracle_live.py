"""実 Oracle AI Database で、DB 管理の監査が表 NL2SQL_ADMIN_AUDIT_LOG に残るか（#2264）。

通常 CI では実行しない。`NL2SQL_RUN_ORACLE_INTEGRATION=1` の明示指定時だけ、migration 023 の表に
DB 管理チャット・管理 SQL を実行の実行を残して読み戻し、finally で自分が書いた行と一時の表を消す。
LLM は呼ばない（生成は通さず、実行の経路だけを確かめる）。資格情報は出力しない。
"""

from __future__ import annotations

import contextlib
import os
import uuid
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from dotenv import dotenv_values

from app.features.nl2sql.admin_audit_store import AdminAuditRecord, AdminAuditStore
from app.features.nl2sql.models import DbAdminChatExecuteRequest, DbAdminExecuteRequest
from app.features.nl2sql.oracle_adapter import OracleNl2SqlAdapter
from app.features.nl2sql.service import Nl2SqlService, SchemaRefreshMutationSync
from app.features.nl2sql.store import MemoryNl2SqlStore
from app.security.request_actor import actor_scope
from app.settings import get_settings

pytestmark = pytest.mark.skipif(
    os.getenv("NL2SQL_RUN_ORACLE_INTEGRATION") != "1",
    reason="NL2SQL_RUN_ORACLE_INTEGRATION=1 の明示指定が必要です。",
)


@pytest.fixture
def live_settings() -> Iterator[Any]:
    """conftest が PLATFORM_ORACLE_USER=APP を強制するため、共通 .env の接続ユーザーへ戻す。"""
    settings = get_settings()
    env_user = str(
        dotenv_values(Path(__file__).resolve().parents[3] / "platform" / ".env").get(
            "PLATFORM_ORACLE_USER"
        )
        or os.environ.get("PLATFORM_ORACLE_USER", "")
    )
    original_user = settings.oracle_user
    if env_user:
        settings.oracle_user = env_user
    try:
        yield settings
    finally:
        settings.oracle_user = original_user


class _LiveService(Nl2SqlService):
    def __init__(self, adapter: OracleNl2SqlAdapter) -> None:
        super().__init__(store=MemoryNl2SqlStore())
        self._oracle_adapter = adapter
        self._admin_audit_store = AdminAuditStore(adapter.state_connection)

    def _use_oracle_runtime(self) -> bool:
        return True


def _delete_audits(adapter: OracleNl2SqlAdapter, actor: str) -> None:
    with adapter.connection() as conn, conn.cursor() as cursor:
        cursor.execute(
            "DELETE FROM NL2SQL_ADMIN_AUDIT_LOG WHERE ACTOR_USER_UUID = :actor", {"actor": actor}
        )
        conn.commit()


def _audits(adapter: OracleNl2SqlAdapter, actor: str) -> list[AdminAuditRecord]:
    return [
        item
        for item in AdminAuditStore(adapter.state_connection).list_recent(1000)
        if item.actor_user_uuid == actor
    ]


def test_store_keeps_full_sql_text_as_clob_on_oracle(live_settings: Any) -> None:
    adapter = OracleNl2SqlAdapter(live_settings)
    actor = f"t2264-{uuid.uuid4().hex[:12]}"
    long_sql = "SELECT 1 FROM DUAL\n-- " + "あ" * 40000
    try:
        AdminAuditStore(adapter.state_connection).append(
            AdminAuditRecord(
                source="db_admin_chat",
                operation="db_admin_chat_execute",
                actor_user_uuid=actor,
                outcome="success",
                executed=True,
                statement_count=1,
                success_count=1,
                statement_types=["SELECT"],
                duration_ms=12,
                sql_text=long_sql,
                detail={"returned_rows": 1},
            )
        )
        [record] = _audits(adapter, actor)
        assert record.sql_text == long_sql
        assert record.statement_types == ["SELECT"]
        assert record.detail == {"returned_rows": 1}
        assert record.outcome == "success"
    finally:
        _delete_audits(adapter, actor)


def test_chat_and_admin_sql_executions_are_audited_in_the_same_table(
    live_settings: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    adapter = OracleNl2SqlAdapter(live_settings)
    service = _LiveService(adapter)
    monkeypatch.setattr(
        service,
        "_submit_schema_refresh_after_admin_mutation",
        lambda **_kwargs: SchemaRefreshMutationSync(job_id="t2264-refresh"),
    )
    actor = f"t2264-{uuid.uuid4().hex[:12]}"
    table = f"T2264_{uuid.uuid4().hex[:8].upper()}"
    try:
        with actor_scope(actor, is_system_admin=True):
            # DB 管理チャットの SELECT は確認語なしで実行し、監査に残す。
            select = service.execute_db_admin_chat_sql(
                DbAdminChatExecuteRequest(sql="SELECT 1 AS N FROM DUAL")
            )
            assert select.executed is True
            assert select.execution_context == "admin_control_plane"
            # SELECT 以外は確認語が無ければ実行しない（監査にも残さない）。
            pending = service.execute_db_admin_chat_sql(
                DbAdminChatExecuteRequest(sql=f"CREATE TABLE {table} (ID NUMBER)")
            )
            assert pending.executed is False
            assert {item.status for item in pending.statements} == {"confirmation_required"}
            created = service.execute_db_admin_chat_sql(
                DbAdminChatExecuteRequest(
                    sql=f"CREATE TABLE {table} (ID NUMBER)", confirmation="ADMIN_EXECUTE"
                )
            )
            assert created.committed is True
            # 方針で止める文は実行せず、止めたことを残す。
            blocked = service.execute_db_admin_chat_sql(
                DbAdminChatExecuteRequest(
                    sql="ALTER SYSTEM SET OPEN_CURSORS = 300", confirmation="ADMIN_EXECUTE"
                )
            )
            assert blocked.executed is False
            # 管理 SQL を実行も同じ表に残す（1 つの対象を消す DROP は管理 SQL では ADMIN_EXECUTE）。
            dropped = service._execute_db_admin_sql(
                DbAdminExecuteRequest(sql=f"DROP TABLE {table} PURGE", confirmation="ADMIN_EXECUTE")
            )
            assert dropped.committed is True

        records = sorted(_audits(adapter, actor), key=lambda item: item.created_at)
        assert [(item.source, item.outcome) for item in records] == [
            ("db_admin_chat", "success"),
            ("db_admin_chat", "success"),
            ("db_admin_chat", "blocked"),
            ("admin_sql", "success"),
        ]
        assert records[0].statement_types == ["SELECT"]
        assert records[0].confirmation_given is False
        assert records[1].confirmation_given is True
        assert records[1].committed is True
        assert records[1].sql_text == f"CREATE TABLE {table} (ID NUMBER)"
        assert records[2].executed is False
        assert records[3].operation == "db_admin_execute"
    finally:
        # 既に消えていれば何もしない。
        with (
            adapter.connection() as conn,
            conn.cursor() as cursor,
            contextlib.suppress(Exception),
        ):
            cursor.execute(f"DROP TABLE {table} PURGE")  # nosec B608 - test-local name
        _delete_audits(adapter, actor)
