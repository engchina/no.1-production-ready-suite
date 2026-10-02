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
