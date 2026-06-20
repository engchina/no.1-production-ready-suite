"""Select AI 実クライアントの決定論テスト(fake pool/connection、実 DB 非依存)。"""

from collections.abc import Callable, Mapping
from typing import Any

import pytest

from app.clients.oracle import SelectAiUnavailableError
from app.clients.select_ai import (
    SelectAiAgentResult,
    SelectAiClient,
    SelectAiGenerateAction,
)
from app.config import Settings
from app.select_ai import build_provisioning_plan
from app.select_ai.provisioning import SelectAiProvisioningSpec

# responder: (sql, binds) -> スカラ値 / Exception / None
Responder = Callable[[str, Mapping[str, object]], object]


class _FakeCursor:
    def __init__(self, conn: "_FakeConnection") -> None:
        self._conn = conn
        self._result: object = None

    def execute(self, sql: str, binds: Mapping[str, object] | None = None) -> None:
        b = dict(binds or {})
        self._conn.executed.append((sql, b))
        outcome = self._conn.responder(sql, b)
        if isinstance(outcome, Exception):
            raise outcome
        self._result = outcome

    def fetchone(self) -> Any:
        return None if self._result is None else (self._result,)

    def fetchall(self) -> Any:
        return [] if self._result is None else [(self._result,)]

    def close(self) -> None:
        pass


class _FakeConnection:
    def __init__(self, responder: Responder) -> None:
        self.responder = responder
        self.executed: list[tuple[str, dict[str, object]]] = []
        self.committed = 0
        self.rolled_back = 0
        self.closed = 0

    def cursor(self) -> _FakeCursor:
        return _FakeCursor(self)

    def commit(self) -> None:
        self.committed += 1

    def rollback(self) -> None:
        self.rolled_back += 1

    def close(self) -> None:
        self.closed += 1


class _FakePool:
    def __init__(self, connection: _FakeConnection) -> None:
        self._connection = connection

    def acquire(self) -> _FakeConnection:
        return self._connection

    def close(self) -> None:
        pass


async def _inline_runner(operation: Callable[[], Any]) -> Any:
    """db_call_runner を同期実行(スレッドを使わず決定論化)。"""
    return operation()


def _client(responder: Responder) -> tuple[SelectAiClient, _FakeConnection]:
    conn = _FakeConnection(responder)
    client = SelectAiClient(Settings(), pool=_FakePool(conn), db_call_runner=_inline_runner)
    return client, conn


def _executed_sql(conn: _FakeConnection) -> str:
    return "\n".join(sql for sql, _ in conn.executed)


@pytest.mark.asyncio
async def test_generate_sets_profile_then_generates() -> None:
    def responder(sql: str, binds: Mapping[str, object]) -> object:
        if "GENERATE(" in sql:
            return "SELECT * FROM employee"
        return None

    client, conn = _client(responder)
    result = await client.generate(
        "社員一覧", profile_name="N2SPR_HR", action=SelectAiGenerateAction.SHOWSQL
    )

    assert result.text == "SELECT * FROM employee"
    assert result.action == "showsql"
    # SET_PROFILE → GENERATE の順で実行される。
    assert "SET_PROFILE" in conn.executed[0][0]
    assert "GENERATE(" in conn.executed[1][0]
    assert conn.executed[1][1]["action"] == "showsql"
    assert conn.executed[1][1]["profile_name"] == "N2SPR_HR"


@pytest.mark.asyncio
async def test_generate_can_skip_set_profile() -> None:
    client, conn = _client(lambda sql, binds: "SQL" if "GENERATE(" in sql else None)
    await client.generate("q", profile_name="P", set_profile=False)
    assert "SET_PROFILE" not in _executed_sql(conn)


@pytest.mark.asyncio
async def test_generate_requires_profile() -> None:
    client, _ = _client(lambda sql, binds: None)
    with pytest.raises(SelectAiUnavailableError):
        await client.generate("q", profile_name="  ")


@pytest.mark.asyncio
async def test_generate_truncates_to_max_result_chars() -> None:
    client, _ = _client(lambda sql, binds: "x" * 100 if "GENERATE(" in sql else None)
    result = await client.generate("q", profile_name="P", max_result_chars=10)
    assert result.text == "x" * 10


@pytest.mark.asyncio
async def test_generate_reads_clob_lob() -> None:
    class _Lob:
        def read(self) -> str:
            return "CLOB_SQL"

    client, _ = _client(lambda sql, binds: _Lob() if "GENERATE(" in sql else None)
    result = await client.generate("q", profile_name="P")
    assert result.text == "CLOB_SQL"


@pytest.mark.asyncio
async def test_create_conversation_returns_id() -> None:
    client, _ = _client(lambda sql, binds: "CONV-123" if "CREATE_CONVERSATION" in sql else None)
    assert await client.create_conversation() == "CONV-123"


@pytest.mark.asyncio
async def test_create_conversation_empty_raises() -> None:
    client, _ = _client(lambda sql, binds: "" if "CREATE_CONVERSATION" in sql else None)
    with pytest.raises(SelectAiUnavailableError):
        await client.create_conversation()


@pytest.mark.asyncio
async def test_run_team_extracts_reply_from_json() -> None:
    payload = '{"reply": "部門は10件です", "content": "ignored"}'
    client, conn = _client(lambda sql, binds: payload if "RUN_TEAM" in sql else None)
    result = await client.run_team("件数は?", team_name="N2STM_HR")
    assert isinstance(result, SelectAiAgentResult)
    assert result.reply == "部門は10件です"
    assert result.raw == payload
    assert "params" not in conn.executed[0][1]


@pytest.mark.asyncio
async def test_run_team_with_conversation_uses_params() -> None:
    client, conn = _client(lambda sql, binds: "plain text" if "RUN_TEAM" in sql else None)
    result = await client.run_team("q", team_name="T", conversation_id="CONV-1")
    assert result.reply == "plain text"  # 非 JSON はそのまま本文
    sql, binds = conn.executed[0]
    assert "params =>" in sql
    assert '"conversation_id":"CONV-1"' in str(binds["params"]).replace(" ", "")


@pytest.mark.asyncio
async def test_run_team_requires_team() -> None:
    client, _ = _client(lambda sql, binds: None)
    with pytest.raises(SelectAiUnavailableError):
        await client.run_team("q", team_name="")


@pytest.mark.asyncio
async def test_check_privileges_all_present() -> None:
    client, _ = _client(lambda sql, binds: None)
    result = await client.check_privileges()
    assert result.dbms_cloud_ai is True
    assert result.dbms_cloud_ai_agent is True
    assert result.ok is True


@pytest.mark.asyncio
async def test_check_privileges_detects_missing_execute() -> None:
    def responder(sql: str, binds: Mapping[str, object]) -> object:
        if "DBMS_CLOUD_AI_AGENT" in sql:
            return Exception(
                "ORA-06550: line 1, PLS-00201: identifier 'DBMS_CLOUD_AI_AGENT' must be declared"
            )
        return None

    client, _ = _client(responder)
    result = await client.check_privileges()
    assert result.dbms_cloud_ai is True
    assert result.dbms_cloud_ai_agent is False
    assert result.ok is False


def _plan():
    spec = SelectAiProvisioningSpec(
        config_fingerprint="hr:tokyo",
        model="meta.llama",
        region="ap-osaka-1",
        compartment_id="ocid1.compartment.oc1..aaa",
        object_list=(("ADMIN", "EMPLOYEE"),),
        endpoint_id="ocid1.endpoint.oc1..bbb",
    )
    return build_provisioning_plan(spec)


@pytest.mark.asyncio
async def test_apply_provisioning_executes_all_in_transaction() -> None:
    plan = _plan()
    client, conn = _client(lambda sql, binds: None)
    result = await client.apply_provisioning_plan(plan)

    assert result.provisioned is True
    assert result.executed_kinds == ("profile", "tool", "agent", "task", "team")
    assert result.config_hash == plan.config_hash
    assert conn.committed == 1
    assert conn.rolled_back == 0
    assert len(conn.executed) == 5


@pytest.mark.asyncio
async def test_apply_provisioning_skips_when_config_hash_unchanged() -> None:
    plan = _plan()
    client, conn = _client(lambda sql, binds: None)
    result = await client.apply_provisioning_plan(plan, current_config_hash=plan.config_hash)

    assert result.provisioned is False
    assert result.skipped_reason == "config_hash_unchanged"
    assert result.executed_kinds == ()
    assert conn.executed == []
    assert conn.committed == 0


@pytest.mark.asyncio
async def test_apply_provisioning_rolls_back_on_error() -> None:
    plan = _plan()

    def responder(sql: str, binds: Mapping[str, object]) -> object:
        if "CREATE_AGENT" in sql:
            return Exception("ORA-00001: simulated failure")
        return None

    client, conn = _client(responder)
    with pytest.raises(Exception, match="simulated failure"):
        await client.apply_provisioning_plan(plan)
    assert conn.rolled_back == 1
    assert conn.committed == 0
