"""NL2SQL クエリ API(generate / execute の 2 段ゲート)のテスト。"""

from collections.abc import Mapping

from pytest import MonkeyPatch

from app.api.routes import nl2sql as nl2sql_route
from app.clients.oracle import SelectAiUnavailableError
from app.clients.select_ai import (
    SelectAiAgentResult,
    SelectAiGenerateAction,
    SelectAiGenerateResult,
    SqlExecutionResult,
)
from app.config import get_settings
from app.main import app
from tests.support import AsgiTestClient

client = AsgiTestClient(app)


class _FakeClient:
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
        return SelectAiGenerateResult(
            profile_name=profile_name, action=action.value, text="SELECT 1 FROM dual"
        )

    async def run_team(
        self, prompt: str, *, team_name: str, conversation_id: str | None = None
    ) -> SelectAiAgentResult:
        return SelectAiAgentResult(team_name=team_name, conversation_id=None, reply="", raw="")

    async def run_select(
        self, sql: str, *, binds: Mapping[str, object] | None = None, max_rows: int = 1000
    ) -> SqlExecutionResult:
        return SqlExecutionResult(columns=("N",), rows=((1,),), row_count=1, truncated=False)


def _patch_client(monkeypatch: MonkeyPatch) -> None:
    monkeypatch.setattr(nl2sql_route, "SelectAiClient", lambda *args, **kwargs: _FakeClient())


def test_generate_returns_sql_and_guardrail(monkeypatch: MonkeyPatch) -> None:
    _patch_client(monkeypatch)
    monkeypatch.setattr(get_settings(), "oracle_select_ai_profile", "N2SPR_HR")

    resp = client.post("/api/nl2sql/generate", json={"question": "社員一覧を見せて"})

    assert resp.status_code == 200
    data = resp.json()["data"]
    assert data["generated_sql"] == "SELECT 1 FROM dual"
    assert data["generation_backend"] == "select_ai"
    assert data["guardrail"]["allowed"] is True
    assert data["profile_name"] == "N2SPR_HR"


def test_generate_returns_503_when_profile_unavailable(monkeypatch: MonkeyPatch) -> None:
    _patch_client(monkeypatch)
    monkeypatch.setattr(get_settings(), "oracle_select_ai_profile", "")

    resp = client.post("/api/nl2sql/generate", json={"question": "社員一覧"})

    assert resp.status_code == 503


def test_generate_rejects_empty_question() -> None:
    resp = client.post("/api/nl2sql/generate", json={"question": ""})
    assert resp.status_code == 422


def test_execute_runs_approved_select(monkeypatch: MonkeyPatch) -> None:
    _patch_client(monkeypatch)

    resp = client.post(
        "/api/nl2sql/execute",
        json={"sql": "SELECT * FROM employee", "allowed_objects": ["EMPLOYEE"]},
    )

    assert resp.status_code == 200
    data = resp.json()["data"]
    assert data["executed"] is True
    assert data["result"]["row_count"] == 1
    assert data["result"]["columns"] == ["N"]


def test_execute_blocks_non_select(monkeypatch: MonkeyPatch) -> None:
    _patch_client(monkeypatch)

    resp = client.post("/api/nl2sql/execute", json={"sql": "DROP TABLE employee"})

    assert resp.status_code == 200
    data = resp.json()["data"]
    assert data["executed"] is False
    assert data["blocked_reason"]
    assert data["result"] is None
