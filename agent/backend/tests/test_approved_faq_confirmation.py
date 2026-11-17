"""RAG の登録済みQ&Aの候補を、利用者が回答前の確認で選ぶ（#5134・#5136）。

RAG の回答のツール（`rag_search` / `rag_chat`）が候補だけを返したら
（`status=approved_faq_candidates`）、Agent は候補の質問と「どれでもない（通常どおり回答）」を
確認のカードにする（モデルに選ばせない）。
利用者の選択は次の Run で実行環境が候補を返したツールの `approved_faq_id` / `skip_approved_faq`
にする（モデルに id を写させない。2 つの引数はモデルに見せない）。利用者に確認しない Run は
`skip_approved_faq`。RAG の回答をそのまま返すスキル（#5342）では、候補・選んだ Q&A の回答で Run が
終わる（モデルは回答を書かない）。
"""

from __future__ import annotations

import contextlib
import json
from collections.abc import Iterator
from copy import deepcopy
from typing import Any

import anyio
import pytest
from agents import FunctionTool
from agents.testing import ScriptedModel, assistant_message, function_call
from mcp_support import (
    DEFAULT_OUTPUTS,
    FakeProductMcp,
    fake_product_mcp,
    rag_approved_faq_answer,
    rag_approved_faq_candidates,
)
from pytest import MonkeyPatch

from app.features.agent import builtin_runtime
from app.features.agent.answer_evidence import answer_evidence
from app.features.agent.answer_outcome import answer_outcome
from app.features.agent.builtin_runtime import ModelTarget
from app.features.agent.confirmation import (
    APPROVED_FAQ_ITEM_ID,
    APPROVED_FAQ_NONE_OPTION_ID,
    APPROVED_FAQ_REASON,
    RESPONSE_METADATA_KEY,
    ConfirmationAnswer,
    approved_faq_choice,
    rag_conditions,
)
from app.features.agent.runtime import (
    EVALUATION_DRY_RUN_KEY,
    AgentProfile,
    AgentProfilePatch,
    RunCreateRequest,
    RunState,
    RunStatus,
    RunStep,
    run_support_task,
    runtime_repository,
)
from app.features.agent.skills import (
    AgentSkillDefinition,
    FinalAnswerTool,
    SkillMcpRequirement,
    skill_registry,
)
from app.features.agent.support_task import approved_faq_schema, rag_next_step
from app.features.agent.tools import ToolCall, ToolPolicy, ToolResult
from app.settings import get_settings

SKILL_ID = "test5134-skill"
AGENT_ID = "test5134-agent"
USER_UUID = "11111111-2222-3333-4444-777777777777"
PROFILE = "bv-faq"
QUESTION = "合成の質問"
CANDIDATES = (("faq-1", "合成の登録済みの質問 A", 0.97), ("faq-2", "合成の登録済みの質問 B", 0.93))


def _rag_search(argument: Any) -> dict[str, Any]:
    if argument.approved_faq_id:
        return rag_approved_faq_answer(argument.approved_faq_id, CANDIDATES[0][1], "合成の回答 A")
    if argument.skip_approved_faq:
        output: dict[str, Any] = deepcopy(DEFAULT_OUTPUTS["rag_search"])
        return output
    return rag_approved_faq_candidates(*CANDIDATES)


@pytest.fixture
def mcp(monkeypatch: MonkeyPatch) -> Iterator[FakeProductMcp]:
    fake = fake_product_mcp(monkeypatch, outputs={"rag_search": _rag_search})
    monkeypatch.setattr(get_settings(), "agent_tool_grounding_required", False)
    monkeypatch.setattr(builtin_runtime, "_active_policy", lambda: ToolPolicy())
    skill_registry.upsert_custom(
        AgentSkillDefinition(
            id=SKILL_ID,
            name=SKILL_ID,
            instructions="資料は rag_search で調べる。",
            mcp_requirements=[SkillMcpRequirement(server_id="rag", tool_names=["rag_search"])],
        )
    )
    runtime_repository.create_agent(AgentProfile(id=AGENT_ID, name=AGENT_ID, skill_ids=[SKILL_ID]))
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
        with contextlib.suppress(KeyError, ValueError):
            skill_registry.remove(SKILL_ID)


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


def _run(goal: str = QUESTION, *, metadata: dict[str, Any] | None = None) -> RunState:
    run = runtime_repository.create_builtin_run(
        RunCreateRequest(goal=goal, agent_id=AGENT_ID, metadata=metadata or {}),
        created_by_user_uuid=USER_UUID,
    )
    anyio.run(builtin_runtime.execute_run, run.id)
    return runtime_repository.get_run(run.id)


def _execute(run_id: str) -> RunState:
    anyio.run(builtin_runtime.execute_run, run_id)
    return runtime_repository.get_run(run_id)


def _search(call_id: str, **arguments: Any) -> list[Any]:
    return [function_call("rag__rag_search", {"query": QUESTION, **arguments}, call_id=call_id)]


def _tool_output(model: ScriptedModel, call_index: int, call_id: str) -> dict[str, Any]:
    for item in model.calls[call_index].input:
        if isinstance(item, dict) and item.get("call_id") == call_id and "output" in item:
            loaded: dict[str, Any] = json.loads(str(item["output"]))
            return loaded
    raise AssertionError(f"{call_id} の結果がモデルの入力にありません")


def _search_schema(model: ScriptedModel) -> dict[str, Any]:
    tool = next(
        tool
        for tool in model.calls[0].tools
        if isinstance(tool, FunctionTool) and tool.name == "rag__rag_search"
    )
    return tool.params_json_schema


def _outcome(run: RunState) -> dict[str, Any]:
    answer = next(item for item in run.artifacts if item.kind == "answer")
    assert isinstance(answer.content, dict)
    outcome: dict[str, Any] = answer.content["outcome"]
    return outcome


def _first_run_with_card(monkeypatch: MonkeyPatch) -> RunState:
    _script(
        monkeypatch,
        # モデルが自分で Q&A の id を渡しても、実行環境は使わない。
        _search("call-1", search_answer_profile_id=PROFILE, approved_faq_id="faq-x"),
        [assistant_message("候補から当てはまる Q&A を選んでください。")],
    )
    return _run()


def test_candidates_become_a_card_and_the_model_never_sees_the_faq_arguments(
    monkeypatch: MonkeyPatch, mcp: FakeProductMcp
) -> None:
    model = _script(
        monkeypatch,
        _search("call-1", search_answer_profile_id=PROFILE, approved_faq_id="faq-x"),
        [assistant_message("候補から当てはまる Q&A を選んでください。")],
    )
    first = _run()

    assert first.status == RunStatus.COMPLETED
    # 2 つの引数はモデルに見せず、モデルが渡した値も送らない（候補を返させる）。
    properties = _search_schema(model)["properties"]
    assert "approved_faq_id" not in properties and "skip_approved_faq" not in properties
    [sent] = mcp.calls_of("rag_search")
    assert "approved_faq_id" not in sent["arguments"]
    assert "skip_approved_faq" not in sent["arguments"]
    # モデルへの結果には、カードを待つ次の手。
    assert _tool_output(model, 1, "call-1")["next_step"]["action"] == "wait_for_approved_faq_choice"
    [card] = first.confirmations
    assert (card.source, card.can_skip, card.round) == ("rag", False, 1)
    [item] = card.items
    assert item.id == APPROVED_FAQ_ITEM_ID
    assert item.reason == APPROVED_FAQ_REASON
    assert [option.label for option in item.options] == [
        CANDIDATES[0][1],
        CANDIDATES[1][1],
        "どれでもない（通常どおり回答）",
    ]
    assert item.options[-1].id == APPROVED_FAQ_NONE_OPTION_ID
    # 候補の状態は失敗・根拠不足ではなく、利用者への確認。
    assert (_outcome(first)["value"], _outcome(first)["basis"]) == (
        "needs_clarification",
        "approved_faq_candidates",
    )


def test_the_chosen_faq_is_sent_by_the_runtime_in_the_next_run(
    monkeypatch: MonkeyPatch, mcp: FakeProductMcp
) -> None:
    first = _first_run_with_card(monkeypatch)
    [card] = first.confirmations
    outcome = runtime_repository.decide_confirmation(
        first.id,
        card.id,
        action="answer",
        answers=[ConfirmationAnswer(item_id=APPROVED_FAQ_ITEM_ID, option_ids=["option_2"])],
    )
    assert outcome.next_run is not None
    response = outcome.next_run.metadata[RESPONSE_METADATA_KEY]
    # Q&A の選択は業務ガイドの条件ではない。
    assert rag_conditions(response) == {}

    model = _script(
        monkeypatch,
        # 2 回目は別の検索・回答プロファイルで呼んでも、候補を返したプロファイルで Q&A を引く。
        _search("call-2"),
        _search("call-3", search_answer_profile_id=PROFILE),
        [assistant_message("合成の回答 A")],
    )
    second = _execute(outcome.next_run.id)

    assert second.status == RunStatus.COMPLETED
    calls = mcp.calls_of("rag_search")[1:]
    assert calls[0]["arguments"]["approved_faq_id"] == "faq-2"
    assert calls[0]["arguments"]["search_answer_profile_id"] == PROFILE
    assert "skip_approved_faq" not in calls[0]["arguments"]
    # 選んだ Q&A は 1 回だけ渡し、続きの検索は候補を返させない。
    assert calls[1]["arguments"]["skip_approved_faq"] is True
    assert "approved_faq_id" not in calls[1]["arguments"]
    instructions = str(model.calls[0].system_instructions)
    assert f"登録済みQ&A「{CANDIDATES[1][1]}」を選びました" in instructions
    assert "faq-2" not in instructions
    assert second.confirmations == []
    state = run_support_task(second)
    assert state is not None and APPROVED_FAQ_ITEM_ID not in state["known_conditions"]


@pytest.mark.parametrize(
    "answer",
    [
        ConfirmationAnswer(item_id=APPROVED_FAQ_ITEM_ID, option_ids=[APPROVED_FAQ_NONE_OPTION_ID]),
        ConfirmationAnswer(item_id=APPROVED_FAQ_ITEM_ID, other_text="合成の補足"),
    ],
    ids=["none_of_these", "free_text_only"],
)
def test_none_of_the_candidates_searches_with_skip(
    monkeypatch: MonkeyPatch, mcp: FakeProductMcp, answer: ConfirmationAnswer
) -> None:
    first = _first_run_with_card(monkeypatch)
    outcome = runtime_repository.decide_confirmation(
        first.id, first.confirmations[0].id, action="answer", answers=[answer]
    )
    assert outcome.next_run is not None
    model = _script(
        monkeypatch,
        _search("call-2", search_answer_profile_id=PROFILE),
        [assistant_message("根拠付き回答")],
    )
    second = _execute(outcome.next_run.id)

    assert second.status == RunStatus.COMPLETED
    sent = mcp.calls_of("rag_search")[-1]["arguments"]
    assert sent["skip_approved_faq"] is True and "approved_faq_id" not in sent
    instructions = str(model.calls[0].system_instructions)
    assert "どれも選びませんでした" in instructions
    if answer.other_text:
        assert "利用者の補足:「合成の補足」" in instructions
    assert second.confirmations == []


@pytest.mark.parametrize(
    "setup",
    ["assume", "unattended"],
    ids=["assume_policy", "unattended_run"],
)
def test_runs_that_do_not_ask_search_with_skip(
    monkeypatch: MonkeyPatch, mcp: FakeProductMcp, setup: str
) -> None:
    metadata: dict[str, Any] = {}
    if setup == "assume":
        runtime_repository.patch_agent(AGENT_ID, AgentProfilePatch(clarification_policy="assume"))
        runtime_repository.publish_agent(AGENT_ID)
    else:
        metadata = {EVALUATION_DRY_RUN_KEY: True}
    _script(
        monkeypatch,
        _search("call-1", search_answer_profile_id=PROFILE),
        [assistant_message("根拠付き回答")],
    )
    run = _run(metadata=metadata)

    sent = mcp.calls_of("rag_search")[-1]["arguments"]
    assert sent["skip_approved_faq"] is True
    assert run.confirmations == []


def test_choice_and_next_step_are_deterministic() -> None:
    """選択の読み取り・次の手・schema の外し方（決定的な部品）。"""
    response = {
        "source": "rag",
        "action": "answer",
        "entries": [{"item_id": APPROVED_FAQ_ITEM_ID, "option_ids": ["option_1"]}],
        "context": {
            "approved_faq": {
                "tool": "rag__rag_search",
                "search_answer_profile_id": PROFILE,
                "faq_ids": {"option_1": "faq-1"},
            }
        },
    }
    assert approved_faq_choice(response) == {
        "tool": "rag__rag_search",
        "search_answer_profile_id": PROFILE,
        "approved_faq_id": "faq-1",
    }
    # 業務ガイドの条件の確認の答えは Q&A の選択ではない。
    assert approved_faq_choice({**response, "context": {}}) is None
    assert approved_faq_choice(None) is None

    step = rag_next_step(rag_approved_faq_candidates(*CANDIDATES), [])
    assert step is not None and step["action"] == "wait_for_approved_faq_choice"
    assert rag_next_step(rag_approved_faq_answer("faq-1", "q", "a"), []) is None

    schema = {
        "type": "object",
        "properties": {
            "query": {},
            "approved_faq_id": {},
            "skip_approved_faq": {},
            "skip_clarification": {},
        },
        "required": ["query"],
    }
    assert set(approved_faq_schema("rag__rag_search", schema)["properties"]) == {"query"}
    assert set(approved_faq_schema("rag__rag_chat", schema)["properties"]) == {"query"}
    assert approved_faq_schema("rag__rag_read_source", schema) is schema


def test_candidates_after_a_chosen_answer_do_not_keep_the_card_outcome() -> None:
    """最後の rag_search が候補でなければ、候補の状態の対応にしない。"""

    def step(output: dict[str, Any], index: int) -> RunStep:
        return RunStep(
            id=f"step-{index}",
            run_id="run-1",
            index=index,
            status="completed",
            tool_call=ToolCall(name="rag__rag_search", arguments={"query": QUESTION}),
            tool_result=ToolResult(name="rag__rag_search", success=True, output=output),
        )

    steps = [
        step(rag_approved_faq_candidates(*CANDIDATES), 0),
        step(rag_approved_faq_answer("faq-1", CANDIDATES[0][1], "合成の回答 A"), 1),
    ]
    assert answer_outcome("合成の回答 A", steps=steps)["basis"] == "rag_search"
    assert answer_outcome("", steps=steps[:1])["basis"] == "approved_faq_candidates"


# ---- rag_chat の回答をそのまま返すスキル（#5342） ----

CHAT_SKILL_ID = "test5342-chat-skill"
CHAT_AGENT_ID = "test5342-chat-agent"
FAQ_ANSWER = "合成の登録済みの回答 B"
CHAT_ANSWER = "合成のチャットの回答"


def _rag_chat(argument: Any) -> dict[str, Any]:
    if argument.approved_faq_id:
        return rag_approved_faq_answer(argument.approved_faq_id, CANDIDATES[1][1], FAQ_ANSWER)
    if argument.skip_approved_faq:
        output: dict[str, Any] = deepcopy(DEFAULT_OUTPUTS["rag_chat"])
        output["answer"] = CHAT_ANSWER
        return output
    return rag_approved_faq_candidates(*CANDIDATES)


@pytest.fixture
def chat_mcp(monkeypatch: MonkeyPatch) -> Iterator[FakeProductMcp]:
    """rag_chat の回答をそのまま返すスキル（組み込みの rag-chat と同じ形）の業務 Agent。"""
    fake = fake_product_mcp(monkeypatch, outputs={"rag_chat": _rag_chat})
    monkeypatch.setattr(get_settings(), "agent_tool_grounding_required", False)
    monkeypatch.setattr(builtin_runtime, "_active_policy", lambda: ToolPolicy())
    skill_registry.upsert_custom(
        AgentSkillDefinition(
            id=CHAT_SKILL_ID,
            name=CHAT_SKILL_ID,
            instructions="資料は rag_chat で調べる。",
            mcp_requirements=[SkillMcpRequirement(server_id="rag", tool_names=["rag_chat"])],
            final_answer_tools=[FinalAnswerTool(server_id="rag", tool_name="rag_chat")],
        )
    )
    runtime_repository.create_agent(
        AgentProfile(id=CHAT_AGENT_ID, name=CHAT_AGENT_ID, skill_ids=[CHAT_SKILL_ID])
    )
    try:
        yield fake
    finally:
        repository: Any = runtime_repository
        with repository._lock:  # noqa: SLF001 - テストの後始末
            for run_id in [
                run_id for run_id, run in repository._runs.items() if run.agent_id == CHAT_AGENT_ID
            ]:
                repository._runs.pop(run_id)
        with contextlib.suppress(KeyError, ValueError):
            runtime_repository.delete_agent(CHAT_AGENT_ID)
        with contextlib.suppress(KeyError, ValueError):
            skill_registry.remove(CHAT_SKILL_ID)


def _chat(call_id: str, **arguments: Any) -> list[Any]:
    return [function_call("rag__rag_chat", {"query": QUESTION, **arguments}, call_id=call_id)]


def _chat_run(metadata: dict[str, Any] | None = None) -> RunState:
    run = runtime_repository.create_builtin_run(
        RunCreateRequest(goal=QUESTION, agent_id=CHAT_AGENT_ID, metadata=metadata or {}),
        created_by_user_uuid=USER_UUID,
    )
    anyio.run(builtin_runtime.execute_run, run.id)
    return runtime_repository.get_run(run.id)


def _answer_text(run: RunState) -> str:
    answer = next(item for item in run.artifacts if item.kind == "answer")
    assert isinstance(answer.content, dict)
    return str(answer.content["text"])


def test_chat_candidates_end_the_run_with_the_card(
    monkeypatch: MonkeyPatch, chat_mcp: FakeProductMcp
) -> None:
    # RAG の結果で Run が終わる（モデルを呼び直さない。台本はツールの呼び出しの 1 回だけ）。
    model = _script(monkeypatch, _chat("call-1", search_answer_profile_id=PROFILE))
    first = _chat_run()

    assert first.status == RunStatus.COMPLETED, first.events[-1].message
    assert len(model.calls) == 1
    properties = next(
        tool
        for tool in model.calls[0].tools
        if isinstance(tool, FunctionTool) and tool.name == "rag__rag_chat"
    ).params_json_schema["properties"]
    assert not {"approved_faq_id", "skip_approved_faq", "skip_clarification"} & set(properties)
    [sent] = chat_mcp.calls_of("rag_chat")
    assert not {"approved_faq_id", "skip_approved_faq", "skip_clarification"} & set(
        sent["arguments"]
    )
    [card] = first.confirmations
    [item] = card.items
    assert item.reason == APPROVED_FAQ_REASON
    assert [option.label for option in item.options][-1] == "どれでもない（通常どおり回答）"
    assert _answer_text(first)
    assert (_outcome(first)["value"], _outcome(first)["basis"]) == (
        "needs_clarification",
        "approved_faq_candidates",
    )


def test_chat_chosen_faq_answer_is_the_final_answer(
    monkeypatch: MonkeyPatch, chat_mcp: FakeProductMcp
) -> None:
    _script(monkeypatch, _chat("call-1", search_answer_profile_id=PROFILE))
    first = _chat_run()
    [card] = first.confirmations
    outcome = runtime_repository.decide_confirmation(
        first.id,
        card.id,
        action="answer",
        answers=[ConfirmationAnswer(item_id=APPROVED_FAQ_ITEM_ID, option_ids=["option_2"])],
    )
    assert outcome.next_run is not None

    model = _script(monkeypatch, _chat("call-2"))
    second = _execute(outcome.next_run.id)

    assert second.status == RunStatus.COMPLETED, second.events[-1].message
    assert len(model.calls) == 1
    calls = chat_mcp.calls_of("rag_chat")[1:]
    assert calls[0]["arguments"]["approved_faq_id"] == "faq-2"
    assert calls[0]["arguments"]["search_answer_profile_id"] == PROFILE
    instructions = str(model.calls[0].system_instructions)
    assert f"登録済みQ&A「{CANDIDATES[1][1]}」を選びました" in instructions
    # 登録済みの回答が、そのまま Run の回答になる（モデルは書かない）。
    assert _answer_text(second) == FAQ_ANSWER
    assert second.confirmations == []
    recorded = _outcome(second)
    assert (recorded["value"], recorded["basis"]) == ("answered", "rag_chat")
    assert "approved_faq" in recorded["signals"]
    evidence = next(item for item in second.artifacts if item.kind == "answer_evidence")
    assert isinstance(evidence.content, dict)
    [faq_item] = [
        item for item in evidence.content["items"] if item.get("approved_faq_id") == "faq-2"
    ]
    assert (faq_item["excerpt"], faq_item["cited"]) == (FAQ_ANSWER, True)


def test_chat_runs_that_do_not_ask_skip_candidates_and_clarification(
    monkeypatch: MonkeyPatch, chat_mcp: FakeProductMcp
) -> None:
    _script(monkeypatch, _chat("call-1", search_answer_profile_id=PROFILE))
    run = _chat_run(metadata={EVALUATION_DRY_RUN_KEY: True})

    sent = chat_mcp.calls_of("rag_chat")[-1]["arguments"]
    assert sent["skip_approved_faq"] is True
    # 確認を返させず、RAG が残った条件を分岐で答える。
    assert sent["skip_clarification"] is True
    assert run.confirmations == []
    assert _answer_text(run) == CHAT_ANSWER


def test_rag_search_chosen_faq_is_recorded_as_the_basis() -> None:
    """rag_search の経路で選んだ Q&A も、回答の対応の印と回答の根拠に残る（振る舞いは変えない）。"""
    step = RunStep(
        id="step-0",
        run_id="run-1",
        index=0,
        status="completed",
        tool_call=ToolCall(name="rag__rag_search", arguments={"query": QUESTION}),
        tool_result=ToolResult(
            name="rag__rag_search",
            success=True,
            output=rag_approved_faq_answer("faq-1", CANDIDATES[0][1], "合成の回答 A"),
        ),
    )
    outcome = answer_outcome("合成の回答 A", steps=[step])
    assert (outcome["value"], outcome["basis"]) == ("answered", "rag_search")
    assert "approved_faq" in outcome["signals"]
    [item] = answer_evidence([step], "合成の回答 A")["items"]
    assert (item["approved_faq_id"], item["excerpt"]) == ("faq-1", "合成の回答 A")
