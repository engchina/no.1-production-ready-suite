"""組み込み Runtime（OpenAI Agents SDK + OCI Enterprise AI。#754）の決定論テスト。

モデルは SDK の `ScriptedModel`（台本どおりに応答する）に差し替え、OCI へは接続しない。
ツールはテスト用に登録し、ポリシー・承認・step・成果物・再開を確かめる。
"""

from __future__ import annotations

import contextlib
from collections.abc import Iterator
from typing import Any

import anyio
import pytest
from agents.testing import ScriptedModel, assistant_message, function_call
from pytest import MonkeyPatch

from app.features.agent import builtin_runtime
from app.features.agent.builtin_runtime import ModelTarget
from app.features.agent.runtime import (
    BUILTIN_RUNTIME_ID,
    AgentProfile,
    ApprovalDecisionRequest,
    RunCreateRequest,
    RunStatus,
    builtin_resume_pending,
    runtime_repository,
)
from app.features.agent.skills import AgentSkillDefinition, SkillMcpRequirement, skill_registry
from app.features.agent.tools import (
    ToolDefinition,
    ToolInvocationContext,
    ToolPermissionLevel,
    tool_registry,
)

LOOKUP = "test754_lookup"
WRITE = "test754_write"
SKILL_ID = "test754-skill"
AGENT_ID = "test754-agent"
USER_UUID = "11111111-2222-3333-4444-555555555555"


class _Calls:
    def __init__(self) -> None:
        self.items: list[tuple[str, dict[str, Any], ToolInvocationContext]] = []

    def handler(self, name: str) -> Any:
        def handle(arguments: dict[str, Any], context: ToolInvocationContext) -> dict[str, Any]:
            self.items.append((name, dict(arguments), context))
            return {"name": name, "rows": 3}

        return handle


def _schema() -> dict[str, Any]:
    return {
        "type": "object",
        "properties": {"query": {"type": "string"}},
        "required": ["query"],
        "additionalProperties": False,
    }


@pytest.fixture
def calls() -> Iterator[_Calls]:
    recorded = _Calls()
    for name, level, side_effects in (
        (LOOKUP, ToolPermissionLevel.READ, False),
        (WRITE, ToolPermissionLevel.WRITE, True),
    ):
        tool_registry.register(
            ToolDefinition(
                name=name,
                description=f"{name} のテスト用ツール",
                input_schema=_schema(),
                output_schema={"type": "object"},
                permission_level=level,
                side_effects=side_effects,
            ),
            recorded.handler(name),
        )
    skill_registry.upsert_custom(
        AgentSkillDefinition(
            id=SKILL_ID,
            name="テストの Skill",
            instructions="売上は test754_lookup で調べる。",
            mcp_requirements=[
                SkillMcpRequirement(server_id="control-plane", tool_names=[LOOKUP, WRITE])
            ],
        )
    )
    runtime_repository.create_agent(
        AgentProfile(
            id=AGENT_ID,
            name="テストの業務 Agent",
            instructions="経理の質問に答える。",
            skill_ids=[SKILL_ID],
        )
    )
    try:
        yield recorded
    finally:
        # このテストの Run も消す（削除した Agent を参照する Run が残ると、同じ worker の後の
        # snapshot の検証が失敗する）。
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
        for name in (LOOKUP, WRITE):
            tool_registry._definitions.pop(name, None)  # noqa: SLF001 - テスト用の登録を戻す
            tool_registry._handlers.pop(name, None)  # noqa: SLF001


def _script(monkeypatch: MonkeyPatch, *steps: Any) -> ScriptedModel:
    model = ScriptedModel(list(steps))
    monkeypatch.setattr(
        builtin_runtime,
        "resolve_model_target",
        lambda model_id="": ModelTarget(
            model_id=model_id or "test-model",
            endpoint="https://oci.example",
            project_ocid="",
            api_key="k",
        ),
    )
    monkeypatch.setattr(builtin_runtime, "model_factory", lambda _target: model)
    return model


def _create_run(goal: str = "今月の売上を教えて") -> str:
    run = runtime_repository.create_builtin_run(
        RunCreateRequest(goal=goal, agent_id=AGENT_ID), created_by_user_uuid=USER_UUID
    )
    assert run.runtime_id == BUILTIN_RUNTIME_ID
    assert run.status == RunStatus.QUEUED
    return run.id


def test_instructions_and_tools_come_from_agent_and_skills(calls: _Calls) -> None:
    del calls
    instructions = builtin_runtime.compose_instructions("経理の質問に答える。", [SKILL_ID])
    assert "経理の質問に答える。" in instructions
    assert "# Skill: テストの Skill" in instructions
    assert builtin_runtime.agent_tool_names([SKILL_ID]) == [LOOKUP, WRITE]
    tools = {
        tool.name: tool for tool in builtin_runtime.build_function_tools("run_x", [LOOKUP, WRITE])
    }
    # 読み取りのツールは承認なし、書き込み（副作用あり）のツールは既定の policy で承認が必要。
    assert tools[LOOKUP].needs_approval is False
    assert tools[WRITE].needs_approval is True
    assert tools[LOOKUP].strict_json_schema is True


def test_run_calls_tool_and_records_answer(monkeypatch: MonkeyPatch, calls: _Calls) -> None:
    model = _script(
        monkeypatch,
        [function_call(LOOKUP, {"query": "売上"}, call_id="call-1")],
        [assistant_message("今月の売上は 3 件です。")],
    )
    run_id = _create_run()

    anyio.run(builtin_runtime.execute_run, run_id)

    run = runtime_repository.get_run(run_id)
    assert run.status == RunStatus.COMPLETED
    assert [(name, args) for name, args, _ in calls.items] == [(LOOKUP, {"query": "売上"})]
    # ツールは Run の利用者として呼ぶ（RAG / NL2SQL のサービストークンの sub。#233）。
    assert calls.items[0][2].user_uuid == USER_UUID
    assert calls.items[0][2].run_id == run_id
    [step] = run.steps
    assert step.status == "completed"
    assert step.tool_call is not None and step.tool_call.trace_id == "call-1"
    answer = next(item for item in run.artifacts if item.kind == "answer")
    assert answer.content == {"text": "今月の売上は 3 件です。"}
    # モデルには Agent と Skill の指示と、ツールの結果を渡している。
    first, second = model.calls
    assert "経理の質問に答える。" in str(first.system_instructions)
    assert '"rows": 3' in str(second.input)


def test_tool_requiring_approval_waits_and_resumes(monkeypatch: MonkeyPatch, calls: _Calls) -> None:
    _script(
        monkeypatch,
        [function_call(WRITE, {"query": "登録"}, call_id="call-w")],
        [assistant_message("登録しました。")],
    )
    run_id = _create_run("登録して")

    anyio.run(builtin_runtime.execute_run, run_id)

    waiting = runtime_repository.get_run(run_id)
    assert waiting.status == RunStatus.WAITING_APPROVAL
    assert calls.items == []
    [approval] = waiting.approvals
    assert approval.tool_call.name == WRITE
    assert approval.tool_call.arguments == {"query": "登録"}

    decided = runtime_repository.decide_approval(
        approval.id, ApprovalDecisionRequest(approved=True, decided_by="approver1")
    )
    assert decided.status == RunStatus.QUEUED
    assert builtin_resume_pending(decided)

    anyio.run(builtin_runtime.resume_run, run_id)

    run = runtime_repository.get_run(run_id)
    assert run.status == RunStatus.COMPLETED
    assert [name for name, _, _ in calls.items] == [WRITE]
    # 承認後のツールも承認者ではなく Run の利用者として呼ぶ（MCP のサービストークンの sub。#233）。
    [(_, _, context)] = calls.items
    assert context.user_uuid == USER_UUID
    assert context.approval_id == approval.id
    # 承認済みの step を実行して完了にする（step を重複して作らない）。
    assert [step.status for step in run.steps] == ["completed"]
    assert run.steps[0].approval_id == approval.id
    assert "_builtin_sdk_state" not in run.metadata


def test_rejected_tool_is_not_executed(monkeypatch: MonkeyPatch, calls: _Calls) -> None:
    _script(
        monkeypatch,
        [function_call(WRITE, {"query": "登録"}, call_id="call-r")],
        [assistant_message("承認されなかったため登録していません。")],
    )
    run_id = _create_run("登録して")
    anyio.run(builtin_runtime.execute_run, run_id)
    [approval] = runtime_repository.get_run(run_id).approvals

    runtime_repository.decide_approval(approval.id, ApprovalDecisionRequest(approved=False))
    anyio.run(builtin_runtime.resume_run, run_id)

    run = runtime_repository.get_run(run_id)
    assert run.status == RunStatus.COMPLETED
    assert calls.items == []
    assert [step.status for step in run.steps] == ["cancelled"]


def test_missing_model_settings_fail_the_run(monkeypatch: MonkeyPatch, calls: _Calls) -> None:
    del calls

    def missing(_model_id: str = "") -> ModelTarget:
        raise builtin_runtime.BuiltinRuntimeError("runtime.model_not_configured", "モデル未設定")

    monkeypatch.setattr(builtin_runtime, "resolve_model_target", missing)
    run_id = _create_run()

    anyio.run(builtin_runtime.execute_run, run_id)

    run = runtime_repository.get_run(run_id)
    assert run.status == RunStatus.FAILED
    assert run.events[-1].payload["error_code"] == "runtime.model_not_configured"


def test_cancelled_run_is_not_started(monkeypatch: MonkeyPatch, calls: _Calls) -> None:
    model = _script(monkeypatch, [assistant_message("実行されない")])
    run_id = _create_run()
    runtime_repository.cancel_run(run_id)

    anyio.run(builtin_runtime.execute_run, run_id)

    assert runtime_repository.get_run(run_id).status == RunStatus.CANCELLED
    assert model.calls == ()
    assert calls.items == []


def _dispatch_until_settled(run_id: str) -> None:
    """runtime-dispatcher で、この Run が承認待ちか終了になるまで claim と実行を繰り返す。"""
    from app.features.agent.runtime_dispatcher import dispatch_once

    for _ in range(20):
        status = runtime_repository.get_run(run_id).status
        if status in {RunStatus.WAITING_APPROVAL, RunStatus.COMPLETED, RunStatus.FAILED}:
            return
        anyio.run(dispatch_once, "worker-754")
    raise AssertionError(f"run did not settle: {runtime_repository.get_run(run_id).status}")


def test_api_run_is_executed_and_resumed_by_dispatcher(
    monkeypatch: MonkeyPatch, calls: _Calls
) -> None:
    """API は Run を queued で作り、dispatcher（本番の別プロセス）が実行・承認後の再開をする。"""
    from security_support import client

    from app.settings import get_settings

    monkeypatch.setattr(get_settings(), "agent_runtime_dispatch_mode", "dispatcher")
    _script(
        monkeypatch,
        [function_call(WRITE, {"query": "登録"}, call_id="call-api")],
        [assistant_message("登録しました。")],
    )

    created = client.post("/api/runs", json={"goal": "登録して", "agent_id": AGENT_ID})
    assert created.status_code == 200, created.text
    run_id = created.json()["data"]["id"]
    assert created.json()["data"]["status"] == "queued"
    assert created.json()["data"]["runtime_id"] == BUILTIN_RUNTIME_ID

    _dispatch_until_settled(run_id)
    waiting = runtime_repository.get_run(run_id)
    assert waiting.status == RunStatus.WAITING_APPROVAL
    approval_id = waiting.approvals[0].id

    decided = client.post(f"/api/approvals/{approval_id}/decision", json={"approved": True})
    assert decided.status_code == 200, decided.text
    assert decided.json()["data"]["status"] == "queued"
    # 決定者はログイン中の利用者（local はローカル利用者）。
    assert decided.json()["data"]["approvals"][0]["decided_by"] == "local"

    _dispatch_until_settled(run_id)
    run = runtime_repository.get_run(run_id)
    assert run.status == RunStatus.COMPLETED
    assert [name for name, _, _ in calls.items] == [WRITE]


def test_runtime_status_reports_model_and_missing_settings(monkeypatch: MonkeyPatch) -> None:
    from security_support import client

    monkeypatch.setattr(
        builtin_runtime,
        "resolve_model_target",
        lambda _model_id="": ModelTarget(
            model_id="xai.grok-4", endpoint="https://oci.example", project_ocid="", api_key="secret"
        ),
    )
    ready = client.get("/api/runtime/status").json()["data"]
    assert ready["ready"] is True
    assert ready["model_id"] == "xai.grok-4"
    assert ready["sdk"] == "openai-agents"
    assert "secret" not in str(ready)

    def missing(_model_id: str = "") -> ModelTarget:
        raise builtin_runtime.BuiltinRuntimeError("runtime.model_not_configured", "モデル未設定")

    monkeypatch.setattr(builtin_runtime, "resolve_model_target", missing)
    blocked = client.get("/api/runtime/status").json()["data"]
    assert blocked["ready"] is False
    assert blocked["error_code"] == "runtime.model_not_configured"


def test_tracing_is_disabled() -> None:
    """業務データを含む trace を外部（OpenAI）へ送らない。"""
    from agents.tracing import get_trace_provider

    provider = get_trace_provider()
    assert getattr(provider, "_disabled", True) is True


def test_oci_model_omits_empty_tools_for_xai() -> None:
    """OCI の xAI のモデルは空の `tools` を 400 で拒否するため、送る前に外す（実環境で確認）。"""

    class _Responses:
        def __init__(self) -> None:
            self.calls: list[dict[str, Any]] = []

        async def create(self, **kwargs: Any) -> dict[str, Any]:
            self.calls.append(kwargs)
            return {}

    class _Client:
        def __init__(self) -> None:
            self.responses = _Responses()
            self.base_url = "https://oci.example/openai/v1"

    client = _Client()
    model = builtin_runtime.OciResponsesModel(model="xai.grok-4.3", openai_client=client)  # type: ignore[arg-type]
    wrapped: Any = model._get_client()  # noqa: SLF001 - SDK の内部の差し替えを確かめる
    assert wrapped.base_url == "https://oci.example/openai/v1"

    anyio.run(lambda: wrapped.responses.create(model="m", input="x", tools=[], tool_choice="auto"))
    anyio.run(lambda: wrapped.responses.create(model="m", input="x", tools=[{"type": "function"}]))

    assert client.responses.calls[0] == {"model": "m", "input": "x"}
    assert client.responses.calls[1]["tools"] == [{"type": "function"}]
