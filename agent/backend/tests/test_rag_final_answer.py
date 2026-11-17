"""RAG の回答をそのまま Run の回答にするスキルと、消えたスキル・ツールの扱い（#5342）。"""

from __future__ import annotations

import contextlib
import json
from collections.abc import Iterator
from copy import deepcopy
from typing import Any

import anyio
import pytest
from agents.testing import ScriptedModel, assistant_message, function_call
from mcp_support import DEFAULT_OUTPUTS, FakeProductMcp, fake_product_mcp
from pytest import MonkeyPatch

from app.features.agent import builtin_runtime
from app.features.agent.builtin_runtime import (
    SKILL_UNAVAILABLE_CODE,
    ModelTarget,
    final_answer_tool_names,
    rag_final_answer,
)
from app.features.agent.runtime import (
    AgentProfile,
    RunCreateRequest,
    RunState,
    RunStatus,
    runtime_repository,
)
from app.features.agent.skills import (
    AgentSkillDefinition,
    FinalAnswerTool,
    SkillMcpRequirement,
    skill_registry,
)
from app.features.agent.tools import ToolPolicy
from app.settings import get_settings

USER_UUID = "11111111-2222-3333-4444-535353535353"
AGENT_ID = "test5342-agent"
FINAL_SKILL = "test5342-final"
DATA_SKILL = "test5342-data"
RAG_ANSWER = "合成の RAG の回答"
ORIGINS = {("rag", "rag_chat"): "rag__rag_chat", ("rag", "rag_search"): "rag__rag_search"}


def _skill(skill_id: str, *, final: bool) -> AgentSkillDefinition:
    return AgentSkillDefinition(
        id=skill_id,
        name=skill_id,
        instructions="資料は rag_chat で調べる。",
        mcp_requirements=[SkillMcpRequirement(server_id="rag", tool_names=["rag_chat"])],
        final_answer_tools=(
            [FinalAnswerTool(server_id="rag", tool_name="rag_chat")] if final else []
        ),
    )


@pytest.fixture
def skills() -> Iterator[None]:
    skill_registry.upsert_custom(_skill(FINAL_SKILL, final=True))
    skill_registry.upsert_custom(_skill(DATA_SKILL, final=False))
    try:
        yield
    finally:
        for skill_id in (FINAL_SKILL, DATA_SKILL):
            with contextlib.suppress(KeyError, ValueError):
                skill_registry.remove(skill_id)


def test_final_answer_tools_skip_tools_also_used_without_the_declaration(skills: None) -> None:
    assert final_answer_tool_names([FINAL_SKILL], ORIGINS) == frozenset({"rag__rag_chat"})
    # 同じツールを宣言なしで使うスキルも選んだ Agent は、モデルが回答を書く。
    assert final_answer_tool_names([FINAL_SKILL, DATA_SKILL], ORIGINS) == frozenset()
    assert final_answer_tool_names([DATA_SKILL], ORIGINS) == frozenset()
    # モデルに渡していないツールは対象にしない。
    assert final_answer_tool_names([FINAL_SKILL], {}) == frozenset()


def test_rag_final_answer_stops_on_answers_candidates_and_clarifications() -> None:
    assert rag_final_answer(json.dumps({"answer": RAG_ANSWER, "outcome": "answered"})) == RAG_ANSWER
    assert rag_final_answer({"status": "approved_faq_candidates", "answer": ""}) == ""
    asked = {"answer": "対象はどれですか。", "outcome": "needs_clarification"}
    assert rag_final_answer(asked) == "対象はどれですか。"
    # ツールの失敗・空の回答・JSON でない結果はモデルに返す。
    assert rag_final_answer({"error": "失敗", "error_code": "MCP_TOOL_INTERNAL_ERROR"}) is None
    assert rag_final_answer({"answer": "  ", "outcome": "answered"}) is None
    assert rag_final_answer("not json") is None


def test_retired_rag_tools_in_saved_skills_are_replaced_on_read() -> None:
    requirement = SkillMcpRequirement.model_validate(
        {
            "server_id": "rag",
            "tool_names": ["rag_retrieve_evidence", "rag_lookup_guides", "rag_search"],
        }
    )
    assert requirement.tool_names == ["rag_search"]
    # 消したツールだけの宣言は、空（接続のすべてのツール）に広げない。
    only = SkillMcpRequirement.model_validate(
        {"server_id": "rag", "tool_names": ["rag_lookup_guides"]}
    )
    assert only.tool_names == ["rag_lookup_guides"]


def test_final_answer_tool_must_be_declared() -> None:
    with pytest.raises(ValueError, match="使う MCP のツールにも宣言してください"):
        AgentSkillDefinition(
            id="test5342-invalid",
            name="x",
            final_answer_tools=[FinalAnswerTool(server_id="rag", tool_name="rag_chat")],
        )


@pytest.fixture
def mcp(monkeypatch: MonkeyPatch, skills: None) -> Iterator[FakeProductMcp]:
    chat = deepcopy(DEFAULT_OUTPUTS["rag_chat"])
    chat.update(answer=RAG_ANSWER, outcome="answered", status="completed")
    fake = fake_product_mcp(monkeypatch, outputs={"rag_chat": chat})
    monkeypatch.setattr(get_settings(), "agent_tool_grounding_required", False)
    monkeypatch.setattr(builtin_runtime, "_active_policy", lambda: ToolPolicy())
    try:
        yield fake
    finally:
        repository: Any = runtime_repository
        with repository._lock:  # noqa: SLF001 - テストの後始末
            for run_id in [
                run_id for run_id, run in repository._runs.items() if run.agent_id == AGENT_ID
            ]:
                repository._runs.pop(run_id)
        with contextlib.suppress(KeyError, ValueError):
            runtime_repository.delete_agent(AGENT_ID)


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


def _run(skill_ids: list[str]) -> RunState:
    runtime_repository.create_agent(AgentProfile(id=AGENT_ID, name=AGENT_ID, skill_ids=skill_ids))
    run = runtime_repository.create_builtin_run(
        RunCreateRequest(goal="合成の質問", agent_id=AGENT_ID, draft=True),
        created_by_user_uuid=USER_UUID,
    )
    anyio.run(builtin_runtime.execute_run, run.id)
    return runtime_repository.get_run(run.id)


def _answer(run: RunState) -> dict[str, Any]:
    answer = next(item for item in run.artifacts if item.kind == "answer")
    assert isinstance(answer.content, dict)
    return answer.content


def test_rag_only_skill_returns_the_rag_answer_without_another_model_turn(
    monkeypatch: MonkeyPatch, mcp: FakeProductMcp
) -> None:
    model = _script(
        monkeypatch, [function_call("rag__rag_chat", {"query": "合成の質問"}, call_id="call-1")]
    )

    run = _run([FINAL_SKILL])

    assert run.status == RunStatus.COMPLETED, run.events[-1].message
    assert len(model.calls) == 1
    content = _answer(run)
    assert content["text"] == RAG_ANSWER
    assert (content["outcome"]["value"], content["outcome"]["basis"]) == ("answered", "rag_chat")
    # 元の質問は実行環境が付ける。
    [sent] = mcp.calls_of("rag_chat")
    assert sent["arguments"]["original_question"] == "合成の質問"
    # 引用は RAG の used_in_answer から。
    evidence = next(item for item in run.artifacts if item.kind == "answer_evidence")
    assert isinstance(evidence.content, dict)
    used = [item["used_in_answer"] for item in DEFAULT_OUTPUTS["rag_chat"]["evidence"]]
    assert [item["cited"] for item in evidence.content["items"]] == used


def _answers_by_query(mcp: FakeProductMcp) -> None:
    def chat(argument: Any) -> dict[str, Any]:
        output = deepcopy(DEFAULT_OUTPUTS["rag_chat"])
        output.update(answer=f"{argument.query}の回答", outcome="answered", status="completed")
        return output

    mcp.outputs["rag_chat"] = chat


def _tool_outputs(model: ScriptedModel, call_index: int) -> list[str]:
    return [
        str(item.get("output"))
        for item in model.calls[call_index].input
        if isinstance(item, dict) and item.get("type") == "function_call_output"
    ]


def test_parallel_answer_tool_calls_return_to_the_model_to_combine(
    monkeypatch: MonkeyPatch, mcp: FakeProductMcp
) -> None:
    _answers_by_query(mcp)
    model = _script(
        monkeypatch,
        [
            function_call("rag__rag_chat", {"query": "合成の質問 X"}, call_id="call-1"),
            function_call("rag__rag_chat", {"query": "合成の質問 Y"}, call_id="call-2"),
        ],
        [assistant_message("合成の質問 X の回答。合成の質問 Y の回答。")],
    )

    run = _run([FINAL_SKILL])

    assert run.status == RunStatus.COMPLETED, run.events[-1].message
    # どちらかの回答で終えず、両方の結果をモデルに返して回答を書かせる。
    assert len(model.calls) == 2
    outputs = _tool_outputs(model, 1)
    assert any("合成の質問 Xの回答" in output for output in outputs)
    assert any("合成の質問 Yの回答" in output for output in outputs)
    content = _answer(run)
    assert content["text"] == "合成の質問 X の回答。合成の質問 Y の回答。"
    # 回答の対応はモデルの回答で決める（RAG の回答をそのまま返したときの basis ではない）。
    assert content["outcome"]["basis"] != "rag_chat"


def test_answer_tool_called_again_after_parallel_answers_does_not_end_the_run(
    monkeypatch: MonkeyPatch, mcp: FakeProductMcp
) -> None:
    _answers_by_query(mcp)
    model = _script(
        monkeypatch,
        [
            function_call("rag__rag_chat", {"query": "合成の質問 X"}, call_id="call-1"),
            function_call("rag__rag_chat", {"query": "合成の質問 Y"}, call_id="call-2"),
        ],
        [function_call("rag__rag_chat", {"query": "合成の質問 Z"}, call_id="call-3")],
        [assistant_message("モデルが合わせた回答")],
    )

    run = _run([FINAL_SKILL])

    assert run.status == RunStatus.COMPLETED, run.events[-1].message
    assert len(model.calls) == 3
    assert _answer(run)["text"] == "モデルが合わせた回答"


def test_data_skill_lets_the_model_write_the_answer(
    monkeypatch: MonkeyPatch, mcp: FakeProductMcp
) -> None:
    model = _script(
        monkeypatch,
        [function_call("rag__rag_chat", {"query": "合成の質問"}, call_id="call-1")],
        [assistant_message("モデルが合わせた回答")],
    )

    run = _run([DATA_SKILL])

    assert run.status == RunStatus.COMPLETED, run.events[-1].message
    assert len(model.calls) == 2
    assert "モデルが合わせた回答" in _answer(run)["text"]


def test_run_with_an_unavailable_skill_fails(monkeypatch: MonkeyPatch, mcp: FakeProductMcp) -> None:
    _script(monkeypatch, [assistant_message("呼ばれない")])
    runtime_repository.create_agent(
        AgentProfile(id=AGENT_ID, name=AGENT_ID, skill_ids=[FINAL_SKILL])
    )
    skill_registry.remove(FINAL_SKILL)
    agent = next(item for item in runtime_repository.list_agents() if item.id == AGENT_ID)
    assert agent.unavailable_skill_ids == [FINAL_SKILL]
    run = runtime_repository.create_builtin_run(
        RunCreateRequest(goal="合成の質問", agent_id=AGENT_ID, draft=True),
        created_by_user_uuid=USER_UUID,
    )

    anyio.run(builtin_runtime.execute_run, run.id)

    failed = runtime_repository.get_run(run.id)
    assert failed.status == RunStatus.FAILED
    assert SKILL_UNAVAILABLE_CODE in json.dumps(
        [event.model_dump(mode="json") for event in failed.events], ensure_ascii=False
    )
