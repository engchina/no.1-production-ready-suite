"""業務 Agent の品質評価（#776）の決定論テスト。

業務 Agent の回答と判定のモデルは、どちらも SDK の `ScriptedModel`（台本どおりに応答する）に
差し替え、OCI へは接続しない。ケースは 1 件ずつ「回答 → 判定」の順にモデルを呼ぶ。
"""

from __future__ import annotations

import contextlib
import json
from collections.abc import Iterator
from typing import Any

import anyio
import pytest
from agents.testing import ScriptedModel, assistant_message, function_call
from pytest import MonkeyPatch
from security_support import ProductionAuth, client, enable_production_auth, login

from app.features.agent import builtin_runtime, evaluation
from app.features.agent.builtin_runtime import ModelTarget
from app.features.agent.evaluation import (
    CaseStatus,
    EvaluationCase,
    EvaluationCaseResult,
    EvaluationJob,
    JobStatus,
    JudgeVerdict,
    evaluation_store,
    run_evaluation_job,
)
from app.features.agent.runtime import (
    AgentProfile,
    RunCreateRequest,
    RunStatus,
    runtime_repository,
)
from app.features.agent.skills import AgentSkillDefinition, SkillMcpRequirement, skill_registry
from app.features.agent.tools import ToolDefinition, ToolPermissionLevel, tool_registry
from app.security.permissions import MENU_EVALUATION, MENU_RUNS
from app.security.service import set_security_service

AGENT_ID = "eval-776-agent"
SKILL_ID = "eval-776-skill"
WRITE = "eval776_register"
USER_UUID = "11111111-2222-3333-4444-555555555555"


@pytest.fixture
def agent() -> Iterator[None]:
    tool_registry.register(
        ToolDefinition(
            name=WRITE,
            description="評価のテスト用の登録ツール",
            input_schema={
                "type": "object",
                "properties": {"query": {"type": "string"}},
                "required": ["query"],
                "additionalProperties": False,
            },
            output_schema={"type": "object"},
            permission_level=ToolPermissionLevel.WRITE,
            side_effects=True,
        ),
        lambda _arguments, _context: {},
    )
    skill_registry.upsert_custom(
        AgentSkillDefinition(
            id=SKILL_ID,
            name="評価のテストの Skill",
            instructions="登録は eval776_register で行う。",
            mcp_requirements=[SkillMcpRequirement(server_id="control-plane", tool_names=[WRITE])],
        )
    )
    runtime_repository.create_agent(
        AgentProfile(
            id=AGENT_ID,
            name="経理の Agent",
            instructions="経理の質問に答える。",
            skill_ids=[SKILL_ID],
        )
    )
    evaluation_store.clear()
    try:
        yield
    finally:
        evaluation_store.clear()
        repository: Any = runtime_repository
        with repository._lock:  # noqa: SLF001 - テストの後始末
            for run_id in [
                run_id for run_id, run in repository._runs.items() if run.agent_id == AGENT_ID
            ]:
                run = repository._runs.pop(run_id)
                for approval in run.approvals:
                    repository._approvals.pop(approval.id, None)
        with contextlib.suppress(KeyError, ValueError):
            runtime_repository.delete_agent(AGENT_ID)
        with contextlib.suppress(KeyError, ValueError):
            skill_registry.remove(SKILL_ID)
        tool_registry._definitions.pop(WRITE, None)  # noqa: SLF001 - テスト用の登録を戻す
        tool_registry._handlers.pop(WRITE, None)  # noqa: SLF001


def _script(monkeypatch: MonkeyPatch, *steps: Any) -> ScriptedModel:
    model = ScriptedModel(list(steps))
    monkeypatch.setattr(
        builtin_runtime,
        "resolve_model_target",
        lambda model_id="": ModelTarget(
            model_id=model_id or "judge-model",
            endpoint="https://oci.example",
            project_ocid="",
            api_key="k",
        ),
    )
    monkeypatch.setattr(builtin_runtime, "model_factory", lambda _target: model)
    return model


def _judgement(verdict: str, score: float, summary: str, missing: list[str] | None = None) -> Any:
    return [
        assistant_message(
            json.dumps(
                {
                    "verdict": verdict,
                    "score": score,
                    "summary": summary,
                    "missing_points": missing or [],
                },
                ensure_ascii=False,
            )
        )
    ]


def _job(*cases: tuple[str, str]) -> EvaluationJob:
    return evaluation_store.create(
        EvaluationJob(
            agent_id=AGENT_ID,
            agent_name="経理の Agent",
            created_by_user_uuid=USER_UUID,
            results=[
                EvaluationCaseResult(
                    case=EvaluationCase(id=f"c{index}", question=question, expected=expected)
                )
                for index, (question, expected) in enumerate(cases, start=1)
            ],
        )
    )


def test_cases_are_run_as_agent_runs_and_judged(monkeypatch: MonkeyPatch, agent: None) -> None:
    del agent
    model = _script(
        monkeypatch,
        [assistant_message("今月の売上は 300 万円です。")],
        _judgement("correct", 1.0, "要点を満たしています。"),
        [assistant_message("分かりません。")],
        _judgement("incorrect", 0.2, "件数が答えられていません。", ["契約件数は 12 件"]),
        [assistant_message("経費は 50 万円です。")],
        # 判定のモデルが schema に合わない出力を返したケース。
        [assistant_message("判定できません")],
    )
    job = _job(
        ("今月の売上は？", "300 万円"),
        ("今月の契約件数は？", "12 件"),
        ("今月の経費は？", "50 万円"),
    )

    anyio.run(run_evaluation_job, job.id)

    finished = evaluation_store.get(job.id)
    assert finished.status == JobStatus.COMPLETED
    first, second, third = finished.results
    assert first.status == CaseStatus.JUDGED
    assert first.judgement is not None and first.judgement.verdict == JudgeVerdict.CORRECT
    assert first.answer == "今月の売上は 300 万円です。"
    assert second.judgement is not None and second.judgement.missing_points == ["契約件数は 12 件"]
    assert third.status == CaseStatus.JUDGE_FAILED
    assert third.error and "判定のモデルの呼び出しに失敗しました" in third.error
    summary = finished.summary
    assert (summary.total, summary.correct, summary.incorrect, summary.errors) == (3, 1, 1, 1)
    assert summary.pass_rate == pytest.approx(1 / 3)
    assert summary.average_score == pytest.approx(0.6)

    # 評価の Run は始めた利用者の Run で、評価の job と分かる印を持つ。
    run = runtime_repository.get_run(str(first.run_id))
    assert run.created_by_user_uuid == USER_UUID
    assert run.metadata["evaluation_job_id"] == job.id
    assert run.metadata["evaluation_case_id"] == "c1"
    # 判定には質問・期待する要点・実際の回答を渡している。
    judge_input = model.calls[1].input
    assert isinstance(judge_input, list)
    prompt = str(judge_input[0]["content"])
    assert "## 期待する回答の要点\n300 万円" in prompt
    assert "## 実際の回答\n今月の売上は 300 万円です。" in prompt


def test_runs_needing_approval_or_failing_are_not_judged(
    monkeypatch: MonkeyPatch, agent: None
) -> None:
    del agent
    _script(
        monkeypatch,
        [function_call(WRITE, {"query": "登録"}, call_id="call-776")],
        RuntimeError("model down"),
    )
    job = _job(("登録して", "登録した"), ("今月の売上は？", "300 万円"))

    anyio.run(run_evaluation_job, job.id)

    approval, failed = evaluation_store.get(job.id).results
    assert approval.status == CaseStatus.NEEDS_APPROVAL
    assert approval.error and "承認が必要" in approval.error
    # 承認待ちの Run は取り消す（評価の Run を残して承認を待たせない）。
    assert runtime_repository.get_run(str(approval.run_id)).status == RunStatus.CANCELLED
    assert failed.status == CaseStatus.RUN_FAILED
    assert failed.error and "モデルの呼び出しに失敗しました" in failed.error
    summary = evaluation_store.get(job.id).summary
    assert (summary.errors, summary.pass_rate, summary.average_score) == (2, 0.0, None)


def test_cancelled_job_does_not_run_remaining_cases(monkeypatch: MonkeyPatch, agent: None) -> None:
    del agent
    model = _script(monkeypatch)
    job = _job(("a", "b"), ("c", "d"))
    evaluation_store.cancel(job.id)

    anyio.run(run_evaluation_job, job.id)

    cancelled = evaluation_store.get(job.id)
    assert cancelled.status == JobStatus.CANCELLED
    assert [item.status for item in cancelled.results] == [CaseStatus.CANCELLED] * 2
    assert model.calls == ()


@pytest.fixture
def no_background(monkeypatch: MonkeyPatch) -> list[str]:
    """API のテストは job を作るだけ（実行は上のテストで確かめる）。"""
    started: list[str] = []

    async def record(job_id: str) -> None:
        started.append(job_id)

    monkeypatch.setattr("app.features.agent.router.run_evaluation_job", record)
    return started


def test_evaluation_api(agent: None, no_background: list[str]) -> None:
    del agent
    body = {
        "agent_id": AGENT_ID,
        "cases": [
            {"question": "今月の売上は？", "expected": "300 万円"},
            {"id": "custom", "question": "経費は？", "expected": "50 万円"},
        ],
    }
    created = client.post("/api/evaluations", json=body)
    assert created.status_code == 202, created.text
    job = created.json()["data"]
    assert [item["case"]["id"] for item in job["results"]] == ["case-1", "custom"]
    assert job["summary"]["total"] == 2
    # ジョブの実行は 1 つずつ。
    assert client.post("/api/evaluations", json=body).status_code == 409

    listed = client.get("/api/evaluations").json()["data"]["jobs"]
    assert [item["id"] for item in listed] == [job["id"]]
    assert (
        client.get(f"/api/evaluations/{job['id']}").json()["data"]["agent_name"] == "経理の Agent"
    )
    # 実行中は削除できず、取り消してから削除する。
    assert client.delete(f"/api/evaluations/{job['id']}").status_code == 409
    cancelled = client.post(f"/api/evaluations/{job['id']}/cancel")
    assert cancelled.json()["data"]["status"] == "cancelled"
    assert client.delete(f"/api/evaluations/{job['id']}").status_code == 200
    assert client.get(f"/api/evaluations/{job['id']}").status_code == 404


@pytest.mark.parametrize(
    "cases",
    [
        [],
        [{"question": "  ", "expected": "x"}],
        [
            {"id": "a", "question": "q", "expected": "x"},
            {"id": "a", "question": "q", "expected": "y"},
        ],
        [{"question": f"q{index}", "expected": "x"} for index in range(51)],
    ],
    ids=["empty", "blank-question", "duplicate-id", "too-many"],
)
def test_invalid_cases_are_rejected(
    agent: None, no_background: list[str], cases: list[dict[str, str]]
) -> None:
    del agent
    response = client.post("/api/evaluations", json={"agent_id": AGENT_ID, "cases": cases})
    assert response.status_code == 422
    assert no_background == []


@pytest.fixture
def auth(monkeypatch: MonkeyPatch) -> Iterator[ProductionAuth]:
    yield enable_production_auth(monkeypatch)
    set_security_service(None)


def test_evaluation_permissions(
    auth: ProductionAuth, agent: None, no_background: list[str]
) -> None:
    del agent
    body = {"agent_id": AGENT_ID, "cases": [{"question": "q", "expected": "x"}]}
    auth.user_with_permissions("runs-776", [MENU_RUNS], agent_ids=[AGENT_ID])
    assert client.post("/api/evaluations", json=body, headers=login("runs-776")).status_code == 403

    # 評価の Run は始めた利用者の Run なので、利用できない業務 Agent は評価できない。
    auth.user_with_permissions("eval-other-776", [MENU_EVALUATION], agent_ids=["default"])
    other = login("eval-other-776")
    assert client.post("/api/evaluations", json=body, headers=other).status_code == 403

    evaluator = auth.user_with_permissions("eval-776", [MENU_EVALUATION], agent_ids=[AGENT_ID])
    created = client.post("/api/evaluations", json=body, headers=login("eval-776"))
    assert created.status_code == 202, created.text
    job_id = created.json()["data"]["id"]
    assert evaluation_store.get(job_id).created_by_user_uuid == evaluator.user_uuid
    # 利用できない業務 Agent の評価は一覧にも出さない。
    assert client.get("/api/evaluations", headers=other).json()["data"]["jobs"] == []
    assert client.get(f"/api/evaluations/{job_id}", headers=other).status_code == 404


def test_settle_waits_for_the_dispatcher(monkeypatch: MonkeyPatch, agent: None) -> None:
    """dispatcher のモードでは自分で実行せず、別プロセスの実行を待つ（二重に実行しない）。"""
    del agent
    from app.settings import get_settings

    monkeypatch.setattr(get_settings(), "agent_runtime_dispatch_mode", "dispatcher")
    monkeypatch.setattr(evaluation, "settle_poll_seconds", 0.01)
    executed: list[str] = []

    async def fake_execute(run_id: str) -> None:
        executed.append(run_id)

    monkeypatch.setattr(builtin_runtime, "execute_run", fake_execute)
    run = runtime_repository.create_builtin_run(
        RunCreateRequest(goal="q", agent_id=AGENT_ID), created_by_user_uuid=USER_UUID
    )

    async def dispatcher_completes() -> None:
        await anyio.sleep(0.05)
        runtime_repository.cancel_run(run.id)

    async def scenario() -> Any:
        async with anyio.create_task_group() as group:
            group.start_soon(dispatcher_completes)
            settled = await evaluation._settle_run(run.id)  # noqa: SLF001 - 待ち方の確認
        return settled

    settled = anyio.run(scenario)
    assert settled.status == RunStatus.CANCELLED
    assert executed == []
