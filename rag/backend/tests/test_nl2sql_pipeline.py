"""NL2SQL オーケストレーション(pipeline)の決定論テスト。"""

from collections.abc import Mapping

import pytest

from app.clients.oracle import SelectAiUnavailableError
from app.clients.select_ai import (
    SelectAiAgentResult,
    SelectAiGenerateAction,
    SelectAiGenerateResult,
    SqlExecutionResult,
)
from app.config import Settings
from app.nl2sql.pipeline import Nl2SqlPipeline


class _StubClient:
    """SelectAiExecutor を構造的に満たす決定論スタブ。"""

    def __init__(self, *, sql: str = "SELECT 1 FROM dual", agent_reply: str = "") -> None:
        self._sql = sql
        self._agent_reply = agent_reply
        self.generate_calls: list[tuple[str, str]] = []
        self.run_team_calls: list[str] = []
        self.run_select_calls: list[tuple[str, int]] = []

    async def generate(
        self,
        prompt: str,
        *,
        profile_name: str,
        action: SelectAiGenerateAction = SelectAiGenerateAction.SHOWSQL,
        set_profile: bool = True,
        max_result_chars: int | None = None,
    ) -> SelectAiGenerateResult:
        if not profile_name.strip():
            raise SelectAiUnavailableError("Select AI の profile 名が必要です。")
        self.generate_calls.append((prompt, profile_name))
        return SelectAiGenerateResult(
            profile_name=profile_name, action=action.value, text=self._sql
        )

    async def run_team(
        self,
        prompt: str,
        *,
        team_name: str,
        conversation_id: str | None = None,
    ) -> SelectAiAgentResult:
        self.run_team_calls.append(team_name)
        return SelectAiAgentResult(
            team_name=team_name,
            conversation_id=conversation_id,
            reply=self._agent_reply,
            raw=self._agent_reply,
        )

    async def run_select(
        self,
        sql: str,
        *,
        binds: Mapping[str, object] | None = None,
        max_rows: int = 1000,
    ) -> SqlExecutionResult:
        self.run_select_calls.append((sql, max_rows))
        return SqlExecutionResult(columns=("VALUE",), rows=((1,),), row_count=1, truncated=False)


@pytest.mark.asyncio
async def test_generate_single_stage_produces_guarded_sql() -> None:
    stub = _StubClient(sql="SELECT employee_name FROM employee")
    pipeline = Nl2SqlPipeline(Settings(), select_ai_client=stub)

    outcome = await pipeline.generate("社員一覧", profile_name="N2SPR_HR")

    assert outcome.generation_backend == "select_ai"
    assert outcome.generated_sql == "SELECT employee_name FROM employee"
    assert outcome.guardrail.allowed is True
    assert outcome.profile_name == "N2SPR_HR"
    assert stub.generate_calls and not stub.run_team_calls


@pytest.mark.asyncio
async def test_generate_flags_unsafe_sql_via_guardrail() -> None:
    stub = _StubClient(sql="DROP TABLE employee")
    pipeline = Nl2SqlPipeline(Settings(), select_ai_client=stub)

    outcome = await pipeline.generate("全部消して", profile_name="P")

    assert outcome.generated_sql == "DROP TABLE employee"
    assert outcome.guardrail.allowed is False
    assert outcome.guardrail.statement_type == "DDL"


@pytest.mark.asyncio
async def test_generate_agent_path_extracts_sql_from_reply() -> None:
    reply = "件数を出します。\n```sql\nSELECT count(*) FROM employee\n```"
    stub = _StubClient(agent_reply=reply)
    settings = Settings(
        nl2sql_router_profile="complexity_aware", nl2sql_router_complexity_threshold=2
    )
    pipeline = Nl2SqlPipeline(settings, select_ai_client=stub)

    outcome = await pipeline.generate("部門ごとの平均給与を高い順に並べて", team_name="N2STM_HR")

    assert outcome.generation_backend == "select_ai_agent"
    assert outcome.generated_sql == "SELECT count(*) FROM employee"
    assert outcome.narration == reply
    assert stub.run_team_calls == ["N2STM_HR"]
    assert not stub.generate_calls


@pytest.mark.asyncio
async def test_generate_without_profile_raises_unavailable() -> None:
    stub = _StubClient()
    pipeline = Nl2SqlPipeline(Settings(oracle_select_ai_profile=""), select_ai_client=stub)

    with pytest.raises(SelectAiUnavailableError):
        await pipeline.generate("社員一覧")


@pytest.mark.asyncio
async def test_execute_runs_approved_select() -> None:
    stub = _StubClient()
    pipeline = Nl2SqlPipeline(Settings(), select_ai_client=stub)

    outcome = await pipeline.execute("SELECT * FROM employee", allowed_objects=("EMPLOYEE",))

    assert outcome.executed is True
    assert outcome.blocked_reason is None
    assert outcome.result is not None
    assert outcome.result.row_count == 1
    assert stub.run_select_calls and stub.run_select_calls[0][1] == 1000


@pytest.mark.asyncio
async def test_execute_blocks_non_select_without_running() -> None:
    stub = _StubClient()
    pipeline = Nl2SqlPipeline(Settings(), select_ai_client=stub)

    outcome = await pipeline.execute("DELETE FROM employee")

    assert outcome.executed is False
    assert outcome.blocked_reason
    assert outcome.result is None
    assert stub.run_select_calls == []
