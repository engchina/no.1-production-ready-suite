"""業務 Agent の品質評価（#776）の決定論テスト。

業務 Agent の回答と判定のモデルは、どちらも SDK の `ScriptedModel`（台本どおりに応答する）に
差し替え、OCI へは接続しない。ケースは 1 件ずつ「回答 → 判定」の順にモデルを呼ぶ。
"""

from __future__ import annotations

import contextlib
import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import anyio
import pytest
from agents.testing import ScriptedModel, assistant_message, function_call
from pytest import MonkeyPatch
from security_support import ProductionAuth, client, enable_production_auth, login

from app.features.agent import builtin_runtime, evaluation
from app.features.agent.builtin_runtime import ModelTarget
from app.features.agent.control_plane_store import (
    FileItemStore,
    restore_control_plane,
    set_control_plane_store,
)
from app.features.agent.evaluation import (
    CaseStatus,
    EvaluationCase,
    EvaluationCaseResult,
    EvaluationJob,
    JobStatus,
    JudgeVerdict,
    evaluation_set_store,
    evaluation_store,
    run_evaluation_job,
)
from app.features.agent.evaluation_excel import parse_cases_xlsx
from app.features.agent.runtime import (
    EVALUATION_DRY_RUN_MESSAGE,
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
def agent(tmp_path: Path) -> Iterator[None]:
    set_control_plane_store(FileItemStore(tmp_path / "items.json"))
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
    evaluation_set_store.clear()
    try:
        yield
    finally:
        evaluation_store.clear()
        evaluation_set_store.clear()
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
        set_control_plane_store(None)


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


def _job(*cases: EvaluationCase, set_id: str = "evset_test") -> EvaluationJob:
    return evaluation_store.create(
        EvaluationJob(
            agent_id=AGENT_ID,
            agent_name="経理の Agent",
            set_id=set_id,
            set_name="経理の評価",
            created_by_user_uuid=USER_UUID,
            results=[EvaluationCaseResult(case=case) for case in cases],
        )
    )


def _case(case_id: str, question: str, expected: str, tools: list[str] | None = None) -> Any:
    return EvaluationCase(
        id=case_id, question=question, expected=expected, expected_tools=tools or []
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
        _case("c1", "今月の売上は？", "300 万円"),
        _case("c2", "今月の契約件数は？", "12 件"),
        _case("c3", "今月の経費は？", "50 万円"),
    )

    anyio.run(run_evaluation_job, job.id)

    finished = evaluation_store.get(job.id)
    assert finished.status == JobStatus.COMPLETED
    first, second, third = finished.results
    assert first.status == CaseStatus.JUDGED
    assert first.judgement is not None and first.judgement.verdict == JudgeVerdict.CORRECT
    assert second.judgement is not None and second.judgement.missing_points == ["契約件数は 12 件"]
    assert third.status == CaseStatus.JUDGE_FAILED
    assert third.error and "判定のモデルの呼び出しに失敗しました" in third.error
    summary = finished.summary
    assert (summary.total, summary.correct, summary.incorrect, summary.errors) == (3, 1, 1, 1)
    assert summary.pass_rate == pytest.approx(1 / 3)
    assert summary.average_score == pytest.approx(0.6)
    # 期待するツールを指定していないケースは、ツールの選択を判定しない。
    assert (summary.tool_cases, summary.tool_accuracy) == (0, None)

    run = runtime_repository.get_run(str(first.run_id))
    assert run.created_by_user_uuid == USER_UUID
    assert run.metadata["evaluation_job_id"] == job.id
    assert run.metadata["evaluation_dry_run"] is True
    judge_input = model.calls[1].input
    assert isinstance(judge_input, list)
    prompt = str(judge_input[0]["content"])
    assert "## 期待する回答の要点\n300 万円" in prompt
    assert "## 実際の回答\n今月の売上は 300 万円です。" in prompt


def test_tools_needing_approval_are_not_run_and_tool_selection_is_judged(
    monkeypatch: MonkeyPatch, agent: None
) -> None:
    """評価の Run は承認が要るツールを実行せず（dry-run）、回答まで続ける（#776）。"""
    del agent
    executed: list[Any] = []

    def record(arguments: Any, _context: Any) -> dict[str, Any]:
        executed.append(arguments)
        return {}

    tool_registry._handlers[WRITE] = record  # noqa: SLF001 - 実行されたかを数える
    model = _script(
        monkeypatch,
        [function_call(WRITE, {"query": "登録"}, call_id="call-776")],
        [assistant_message("登録の内容を確認しました。実際の登録は承認の後に行われます。")],
        _judgement("correct", 0.9, "登録の手順を答えています。"),
        [assistant_message("登録しませんでした。")],
        _judgement("incorrect", 0.1, "ツールを使っていません。"),
    )
    job = _job(
        _case("with-tool", "取引先を登録して", "登録の手順", [WRITE]),
        _case("without-tool", "取引先を登録して", "登録の手順", ["rag_search"]),
    )

    anyio.run(run_evaluation_job, job.id)

    first, second = evaluation_store.get(job.id).results
    assert first.status == CaseStatus.JUDGED
    assert first.tool_calls == [WRITE]
    assert first.tool_selection_correct is True
    assert (second.tool_calls, second.tool_selection_correct) == ([], False)
    # 承認が要るツールは実行していない。step には「評価中のため実行していない」を残す。
    assert executed == []
    run = runtime_repository.get_run(str(first.run_id))
    assert run.status == RunStatus.COMPLETED
    [step] = run.steps
    assert (step.status, step.tool_result and step.tool_result.error_code) == (
        "cancelled",
        "evaluation.dry_run",
    )
    # モデルには実行していないことを伝えて、回答を完成させる。
    assert EVALUATION_DRY_RUN_MESSAGE in str(model.calls[1].input)
    summary = evaluation_store.get(job.id).summary
    assert (summary.tool_cases, summary.tool_correct, summary.tool_accuracy) == (2, 1, 0.5)


def test_failed_runs_are_not_judged(monkeypatch: MonkeyPatch, agent: None) -> None:
    del agent
    _script(monkeypatch, RuntimeError("model down"))
    job = _job(_case("c1", "今月の売上は？", "300 万円"))

    anyio.run(run_evaluation_job, job.id)

    [failed] = evaluation_store.get(job.id).results
    assert failed.status == CaseStatus.RUN_FAILED
    assert failed.error and "モデルの呼び出しに失敗しました" in failed.error
    summary = evaluation_store.get(job.id).summary
    assert (summary.errors, summary.pass_rate, summary.average_score) == (1, 0.0, None)


def test_cancelled_job_does_not_run_remaining_cases(monkeypatch: MonkeyPatch, agent: None) -> None:
    del agent
    model = _script(monkeypatch)
    job = _job(_case("a", "a", "b"), _case("c", "c", "d"))
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


SET_BODY: dict[str, Any] = {
    "agent_id": AGENT_ID,
    "name": "経理の評価",
    "description": "月次の問い合わせ",
    "cases": [
        {"question": "今月の売上は？", "expected": "300 万円", "expected_tools": [" rag_search "]},
        {"id": "custom", "question": "経費は？", "expected": "50 万円"},
    ],
}


def test_evaluation_sets_api(agent: None, no_background: list[str], tmp_path: Path) -> None:
    del agent
    created = client.post("/api/evaluation-sets", json=SET_BODY)
    assert created.status_code == 200, created.text
    evaluation_set = created.json()["data"]
    assert [case["id"] for case in evaluation_set["cases"]] == ["case-1", "custom"]
    assert evaluation_set["cases"][0]["expected_tools"] == ["rag_search"]
    set_id = evaluation_set["id"]
    # 保存先に残る（再起動しても使える）。
    assert set_id in json.loads((tmp_path / "items.json").read_text())["evaluation_set"]

    listed = client.get("/api/evaluation-sets", params={"agent_id": AGENT_ID}).json()["data"]
    assert [(item["id"], item["case_count"]) for item in listed["sets"]] == [(set_id, 2)]

    updated = client.put(
        f"/api/evaluation-sets/{set_id}", json={**SET_BODY, "name": "経理の評価（改）"}
    )
    assert updated.json()["data"]["name"] == "経理の評価（改）"
    moved = client.put(f"/api/evaluation-sets/{set_id}", json={**SET_BODY, "agent_id": "default"})
    assert moved.status_code == 422

    started = client.post("/api/evaluations", json={"set_id": set_id})
    assert started.status_code == 202, started.text
    job = started.json()["data"]
    assert (job["set_id"], job["set_name"], len(job["results"])) == (set_id, "経理の評価（改）", 2)
    assert client.post("/api/evaluations", json={"set_id": set_id}).status_code == 409
    # 実行中の評価が使っている評価セットは削除できない。
    assert client.delete(f"/api/evaluation-sets/{set_id}").status_code == 409
    client.post(f"/api/evaluations/{job['id']}/cancel")
    sets = client.get("/api/evaluation-sets").json()["data"]["sets"]
    assert sets[0]["last_job_status"] == "cancelled"
    assert client.delete(f"/api/evaluation-sets/{set_id}").status_code == 200
    assert client.get(f"/api/evaluation-sets/{set_id}").status_code == 404


def test_previous_run_of_the_same_set_is_compared(agent: None) -> None:
    del agent
    first = _job(_case("c1", "q", "x"), set_id="evset_compare")
    evaluation_store.update(first.id, status=JobStatus.COMPLETED)
    evaluation_store.update_result(
        first.id,
        0,
        status=CaseStatus.JUDGED,
        judgement=evaluation.EvaluationJudgement(
            verdict=JudgeVerdict.INCORRECT, score=0.0, summary="誤り", missing_points=[]
        ),
    )
    second = _job(_case("c1", "q", "x"), set_id="evset_compare")

    data = client.get(f"/api/evaluations/{second.id}").json()["data"]

    assert data["previous_job_id"] == first.id
    assert data["previous_summary"]["pass_rate"] == 0.0
    # 前回が無い評価は比較しない。
    assert client.get(f"/api/evaluations/{first.id}").json()["data"]["previous_summary"] is None


def test_cases_can_be_imported_and_exported_as_excel(agent: None) -> None:
    del agent
    template = client.get("/api/evaluation-sets/template.xlsx")
    assert template.status_code == 200
    assert template.headers["content-type"].startswith("application/vnd.openxmlformats")
    parsed = client.post(
        "/api/evaluation-sets/parse-xlsx",
        files={"file": ("cases.xlsx", template.content, "application/octet-stream")},
    )
    assert parsed.status_code == 200, parsed.text
    [sample] = parsed.json()["data"]["cases"]
    assert (sample["id"], sample["expected_tools"]) == ("expense-deadline", ["rag_search"])

    set_id = client.post("/api/evaluation-sets", json=SET_BODY).json()["data"]["id"]
    exported = client.get(f"/api/evaluation-sets/{set_id}/cases.xlsx")
    assert exported.status_code == 200
    cases = parse_cases_xlsx(exported.content)
    assert [(case.id, case.question, case.expected_tools) for case in cases] == [
        ("case-1", "今月の売上は？", ["rag_search"]),
        ("custom", "経費は？", []),
    ]

    broken = client.post(
        "/api/evaluation-sets/parse-xlsx",
        files={"file": ("cases.xlsx", b"not excel", "application/octet-stream")},
    )
    assert broken.status_code == 422
    assert "Excel" in broken.text


def test_sets_and_jobs_are_restored_and_running_jobs_are_interrupted(agent: None) -> None:
    del agent
    set_id = client.post("/api/evaluation-sets", json=SET_BODY).json()["data"]["id"]
    job = _job(_case("c1", "q", "x"), set_id=set_id)
    evaluation_store.update(job.id, status=JobStatus.RUNNING)

    evaluation_store.clear()
    evaluation_set_store.clear()
    restored = restore_control_plane()

    assert (restored["evaluation_set"], restored["evaluation_job"]) == (1, 1)
    assert evaluation_set_store.get(set_id).name == "経理の評価"
    interrupted = evaluation_store.get(job.id)
    assert interrupted.status == JobStatus.FAILED
    assert interrupted.error and "再起動" in interrupted.error
    assert interrupted.results[0].status == CaseStatus.CANCELLED


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
        [{"question": "q", "expected": "x", "expected_tools": [f"t{i}" for i in range(11)]}],
    ],
    ids=["empty", "blank-question", "duplicate-id", "too-many", "too-many-tools"],
)
def test_invalid_sets_are_rejected(agent: None, cases: list[dict[str, Any]]) -> None:
    del agent
    response = client.post("/api/evaluation-sets", json={**SET_BODY, "cases": cases})
    assert response.status_code == 422


@pytest.fixture
def auth(monkeypatch: MonkeyPatch) -> Iterator[ProductionAuth]:
    yield enable_production_auth(monkeypatch)
    set_security_service(None)


def test_evaluation_permissions(
    auth: ProductionAuth, agent: None, no_background: list[str]
) -> None:
    del agent
    auth.user_with_permissions("runs-776", [MENU_RUNS], agent_ids=[AGENT_ID])
    denied = client.post("/api/evaluation-sets", json=SET_BODY, headers=login("runs-776"))
    assert denied.status_code == 403

    # 評価の Run は始めた利用者の Run なので、利用できない業務 Agent の評価セットは作れない。
    auth.user_with_permissions("eval-other-776", [MENU_EVALUATION], agent_ids=["default"])
    other = login("eval-other-776")
    assert client.post("/api/evaluation-sets", json=SET_BODY, headers=other).status_code == 403

    evaluator = auth.user_with_permissions("eval-776", [MENU_EVALUATION], agent_ids=[AGENT_ID])
    headers = login("eval-776")
    set_id = client.post("/api/evaluation-sets", json=SET_BODY, headers=headers).json()["data"][
        "id"
    ]
    created = client.post("/api/evaluations", json={"set_id": set_id}, headers=headers)
    assert created.status_code == 202, created.text
    job_id = created.json()["data"]["id"]
    assert evaluation_store.get(job_id).created_by_user_uuid == evaluator.user_uuid
    # 利用できない業務 Agent の評価セット・評価は一覧に出さず、読めない。
    assert client.get("/api/evaluation-sets", headers=other).json()["data"]["sets"] == []
    assert client.get("/api/evaluations", headers=other).json()["data"]["jobs"] == []
    assert client.get(f"/api/evaluations/{job_id}", headers=other).status_code == 404
    assert client.get(f"/api/evaluation-sets/{set_id}", headers=other).status_code == 404


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
