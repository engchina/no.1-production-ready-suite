"""回答前の確認（#1839）。

決定論のテスト。モデルは SDK の `ScriptedModel`、RAG / NL2SQL は契約どおりの fake の MCP
（`mcp_support`）にする。

- Agent 自身の確認（`ask_user_confirmation`）: Run は `waiting_confirmation` で止まり、答え・
  スキップで同じ Run を再開する（ツールの結果が利用者の答え）。やめると Run を取り消し、会話から
  外して質問を返す。
- 業務 Agent の方針（前提を示して回答する）・確認の上限・無人の Run では確認しない。
- ツールの結果の確認（NL2SQL・RAG）: 完了した Run にカードの確認を出し、答えを構造化して次の Run に
  渡す（RAG は分かっている条件、NL2SQL は `nl2sql_query` の引数）。
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
from fastapi.testclient import TestClient
from mcp_support import (
    DEFAULT_OUTPUTS,
    FakeProductMcp,
    fake_product_mcp,
    nl2sql_needs_clarification,
)
from pydantic import ValidationError
from pytest import MonkeyPatch

from app.features.agent import builtin_runtime, router
from app.features.agent.builtin_runtime import ModelTarget
from app.features.agent.confirmation import (
    CONFIRMATION_TOOL,
    RESPONSE_METADATA_KEY,
    ROUND_METADATA_KEY,
    SKIPPED_INSTRUCTION,
    WITHDRAWN_METADATA_KEY,
    ConfirmationAnswer,
    ConfirmationAnswerError,
    ConfirmationBudget,
    ConfirmationItem,
    ConfirmationOption,
    ConfirmationRequest,
    item_from_arguments,
    nl2sql_answer,
    policy_instructions,
    rag_conditions,
    response_instructions,
    validate_answers,
    validation_premises,
)
from app.features.agent.runtime import (
    EVALUATION_DRY_RUN_KEY,
    AgentProfile,
    AgentProfilePatch,
    ConfirmationClosedError,
    RunCreateRequest,
    RunState,
    RunStatus,
    reserved_run_metadata_keys,
    run_support_task,
    runtime_repository,
)
from app.features.agent.skills import AgentSkillDefinition, SkillMcpRequirement, skill_registry
from app.features.agent.support_task import assume_step, nl2sql_skip_step
from app.features.agent.tools import ToolPolicy
from app.main import app
from app.settings import get_settings

SKILL_ID = "test1839-skill"
AGENT_ID = "test1839-agent"
USER_UUID = "11111111-2222-3333-4444-666666666666"
PROFILE = "profile-sales"
GOAL = "売上を教えて"
RAG_QUESTION = {
    "condition_id": "screen",
    "label": "画面",
    "question": "どの画面の操作ですか？",
    "options": ["受注入力", "請求"],
}
ASK_ARGUMENTS = {
    "question": "どの期間の売上を見ますか？",
    "reason": "期間が質問から決められません。",
    "options": [
        {"label": "今月", "description": ""},
        {"label": "今年度", "description": "4 月から"},
    ],
    "multiple": False,
}


def _rag_search(argument: Any) -> dict[str, Any]:
    output: dict[str, Any] = deepcopy(DEFAULT_OUTPUTS["rag_search"])
    if "conditions" not in argument.model_fields_set:
        output.update(
            answer="どの画面か確かめてください。",
            outcome="needs_clarification",
            clarifications=[RAG_QUESTION],
        )
    return output


def _nl2sql_query(argument: Any) -> dict[str, Any]:
    if argument.requirement_session_id is None:
        return nl2sql_needs_clarification()
    output: dict[str, Any] = deepcopy(DEFAULT_OUTPUTS["nl2sql_query"])
    return output


@pytest.fixture
def mcp(monkeypatch: MonkeyPatch) -> Iterator[FakeProductMcp]:
    fake = fake_product_mcp(
        monkeypatch, outputs={"nl2sql_query": _nl2sql_query, "rag_search": _rag_search}
    )
    monkeypatch.setattr(get_settings(), "agent_final_validation_enabled", False)
    # 台本のモデルはツールを呼ばずに答えるので、ツールで確かめていない回答の拒答（#1835）は外す
    # （ここで確かめるのは確認の往復。拒答のガードは test_tool_grounding.py）。
    monkeypatch.setattr(get_settings(), "agent_tool_grounding_required", False)
    monkeypatch.setattr(
        builtin_runtime, "_active_policy", lambda: ToolPolicy(allow={"nl2sql__nl2sql_query"})
    )
    skill_registry.upsert_custom(
        AgentSkillDefinition(
            id=SKILL_ID,
            name=SKILL_ID,
            instructions="資料は rag_search、数値は nl2sql_query で調べる。",
            mcp_requirements=[
                SkillMcpRequirement(server_id="rag", tool_names=["rag_search"]),
                SkillMcpRequirement(server_id="nl2sql", tool_names=["nl2sql_query"]),
            ],
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


def _run(
    goal: str = GOAL, *, thread_id: str | None = None, metadata: dict[str, Any] | None = None
) -> RunState:
    run = runtime_repository.create_builtin_run(
        RunCreateRequest(
            goal=goal, agent_id=AGENT_ID, thread_id=thread_id, metadata=metadata or {}
        ),
        created_by_user_uuid=USER_UUID,
    )
    anyio.run(builtin_runtime.execute_run, run.id)
    return runtime_repository.get_run(run.id)


def _resume(run_id: str) -> RunState:
    anyio.run(builtin_runtime.resume_run, run_id)
    return runtime_repository.get_run(run_id)


def _execute(run_id: str) -> RunState:
    anyio.run(builtin_runtime.execute_run, run_id)
    return runtime_repository.get_run(run_id)


def _ask(call_id: str, **extra: Any) -> list[Any]:
    return [function_call(CONFIRMATION_TOOL, {**ASK_ARGUMENTS, **extra}, call_id=call_id)]


def _tool_output(model: ScriptedModel, call_index: int, call_id: str) -> dict[str, Any]:
    for item in model.calls[call_index].input:
        if isinstance(item, dict) and item.get("call_id") == call_id and "output" in item:
            loaded: dict[str, Any] = json.loads(str(item["output"]))
            return loaded
    raise AssertionError(f"{call_id} の結果がモデルの入力にありません")


def _tool_names(model: ScriptedModel, call_index: int = 0) -> set[str]:
    return {tool.name for tool in model.calls[call_index].tools if isinstance(tool, FunctionTool)}


def _set_policy(policy: str, max_rounds: int = 2) -> None:
    runtime_repository.patch_agent(
        AGENT_ID,
        AgentProfilePatch(clarification_policy=policy, clarification_max_rounds=max_rounds),
    )
    runtime_repository.publish_agent(AGENT_ID)


# ---- 決定的な部品 ------------------------------------------------------------------------------


def test_tool_arguments_become_one_item_with_short_options() -> None:
    item = item_from_arguments(
        {
            "question": "  どの期間？ ",
            "reason": "理由",
            "options": [
                {"label": "今月", "description": "x"},
                {"label": "今月", "description": "重複は外す"},
                {"label": "", "description": "空は外す"},
                {"label": "その他", "description": "画面が出すので外す"},
                *({"label": f"選択肢{index}", "description": ""} for index in range(12)),
            ],
            "multiple": True,
        }
    )
    assert (item.prompt, item.reason, item.selection) == ("どの期間？", "理由", "multiple")
    assert [option.label for option in item.options][:2] == ["今月", "選択肢0"]
    assert len(item.options) == 8
    assert [option.id for option in item.options][:2] == ["option_1", "option_2"]


def test_answers_are_validated_against_the_items() -> None:
    request = ConfirmationRequest(
        source="agent",
        items=[
            ConfirmationItem(
                id="item_1",
                prompt="どの期間？",
                options=[ConfirmationOption(id="a", label="今月")],
            )
        ],
    )
    [answer] = validate_answers(
        request, [ConfirmationAnswer(item_id="item_1", option_ids=["a", "a"])]
    )
    assert answer.option_ids == ["a"]
    assert validate_answers(request, [ConfirmationAnswer(item_id="item_1", other_text=" 先月 ")])[
        0
    ].other_text == ("先月")
    with pytest.raises(ConfirmationAnswerError):
        validate_answers(request, [])
    with pytest.raises(ConfirmationAnswerError):
        validate_answers(request, [ConfirmationAnswer(item_id="item_1", option_ids=["z"])])
    with pytest.raises(ConfirmationAnswerError):
        # 1 つだけ選ぶ確認事項では、選択肢と自由入力はどちらか一方。
        validate_answers(
            request, [ConfirmationAnswer(item_id="item_1", option_ids=["a"], other_text="x")]
        )
    with pytest.raises(ValidationError):
        ConfirmationAnswer(item_id="", option_ids=[])


def test_budget_and_policy_instructions() -> None:
    budget = ConfirmationBudget(policy="confirm", max_rounds=2, used=1)
    assert budget.asks and budget.remaining == 1
    exposed = policy_instructions(budget, tool_exposed=True)
    assert "あと 1 回" in exposed
    # 業務の手順・規程・業務データの質問は、曖昧でも先に資料・業務データのツールで調べる（#1881）。
    assert "を呼ぶ前に、先に資料・業務データのツールで調べてください" in exposed
    assert "結果を見ても決まらないときだけ ask_user_confirmation で聞いてください" in exposed
    # 推薦の候補が割れたら、本文の表で選ばせずに確認のツールで聞く（#1881）。
    assert "本文の表や箇条書きで選ばせずに ask_user_confirmation で聞いてください" in exposed
    assert budget.reserve() == 2
    assert budget.reserve() is None
    assert "上限（2 回）に達しました" in policy_instructions(budget, tool_exposed=False)
    assume = ConfirmationBudget(policy="assume")
    assert not assume.asks
    assert "前提を示して回答します" in policy_instructions(assume, tool_exposed=False)
    assert policy_instructions(ConfirmationBudget(unattended=True), tool_exposed=False) == ""
    # 「確認せずに回答」を選んだ Run（#1903）: 方針は前提を示して回答し、NL2SQL の確認は
    # skip_clarification で呼び直させる。
    skipped = ConfirmationBudget(policy="confirm", skipped=True)
    assert skipped.policy == "assume" and not skipped.asks and skipped.reserve() is None
    text = policy_instructions(skipped, tool_exposed=True)
    assert "「確認せずに回答」を選びました" in text
    assert "skip_clarification: true" in text
    assert "ask_user_confirmation" not in text


def test_nl2sql_skip_step_retries_once_then_answers_with_assumptions() -> None:
    output = nl2sql_needs_clarification()
    arguments = {"question": GOAL, "profile_id": PROFILE}
    step = nl2sql_skip_step(output, arguments, tool_name="nl2sql__nl2sql_query")
    assert step is not None and step["action"] == "retry_without_clarification"
    assert step["arguments"] == {
        "question": GOAL,
        "profile_id": PROFILE,
        "requirement_session_id": "qs-1",
        "skip_clarification": True,
    }
    # skip_clarification で呼んでも確認事項が返ったら、呼び直させずに前提を示して答えさせる。
    again = nl2sql_skip_step(output, {**step["arguments"]}, tool_name="nl2sql__nl2sql_query")
    assert again is not None and again["action"] == "answer_with_assumptions"
    assert nl2sql_skip_step({"status": "completed"}, arguments, tool_name="q") is None


def test_assume_step_keeps_nl2sql_but_answers_rag_with_assumptions() -> None:
    ask = {"action": "ask_clarification", "reason": "needs_clarification", "questions": [{}]}
    assumed = assume_step(ask)
    assert assumed is not None and assumed["action"] == "answer_with_assumptions"
    nl2sql = {**ask, "kind": "nl2sql"}
    assert assume_step(nl2sql) is nl2sql
    other = {"action": "handoff"}
    assert assume_step(other) is other


def test_response_instructions_pass_structured_answers() -> None:
    rag = {
        "source": "rag",
        "action": "answer",
        "entries": [
            {
                "item_id": "screen",
                "prompt": "どの画面？",
                "selected": ["受注入力"],
                "other_text": "",
            }
        ],
    }
    assert rag_conditions(rag) == {"screen": "受注入力"}
    text = response_instructions(rag)
    assert '"screen": "受注入力"' in text and "rag_search の conditions" in text

    entry = {
        "item_id": "item-period",
        "prompt": "どの列？",
        "selected": ["受注日"],
        "option_ids": ["c-order"],
        "other_text": "",
    }
    assert nl2sql_answer(entry) == {"item_id": "item-period", "selected_candidate_ids": ["c-order"]}
    # 候補と自由入力の両方は、どちらも捨てずに 1 つの free_text にする。
    both = nl2sql_answer({**entry, "other_text": "月末締め"})
    assert both == {"item_id": "item-period", "free_text": "受注日。その他: 月末締め"}
    nl2sql = {
        "source": "nl2sql",
        "action": "answer",
        "entries": [entry],
        "context": {"requirement_session_id": "qs-1", "profile_id": PROFILE, "question": GOAL},
    }
    text = response_instructions(nl2sql)
    assert '"requirement_session_id": "qs-1"' in text
    assert '"selected_candidate_ids": ["c-order"]' in text
    skipped = response_instructions({"source": "rag", "action": "skip", "entries": [entry]})
    assert "確認せずに回答を求めました" in skipped
    assert response_instructions(None) == ""


def test_confirmation_metadata_is_reserved() -> None:
    assert reserved_run_metadata_keys({RESPONSE_METADATA_KEY: {}, ROUND_METADATA_KEY: 1}) == [
        RESPONSE_METADATA_KEY,
        ROUND_METADATA_KEY,
    ]


def test_agent_profile_keeps_the_policy_in_versions(mcp: FakeProductMcp) -> None:
    _set_policy("assume", 3)
    agent = next(item for item in runtime_repository.list_agents() if item.id == AGENT_ID)
    assert (agent.clarification_policy, agent.clarification_max_rounds) == ("assume", 3)
    published = agent.published()
    assert published is not None
    assert (published.clarification_policy, published.clarification_max_rounds) == ("assume", 3)
    # 0 は既定（2 回）。1 回は選べない（NL2SQL の業務プロファイルと同じ）。
    assert AgentProfilePatch(clarification_max_rounds=0).clarification_max_rounds == 0
    with pytest.raises(ValidationError):
        AgentProfilePatch(clarification_max_rounds=1)
    with pytest.raises(ValidationError):
        AgentProfilePatch(clarification_max_rounds=7)
    with pytest.raises(ValidationError):
        AgentProfilePatch(clarification_policy="never")


# ---- Agent 自身の確認（ask_user_confirmation） -----------------------------------------------


def test_agent_confirmation_waits_and_the_answer_resumes_the_same_run(
    monkeypatch: MonkeyPatch, mcp: FakeProductMcp
) -> None:
    model = _script(
        monkeypatch,
        _ask("call-ask"),
        [assistant_message("今年度の売上は 1,200 です。")],
    )
    run = _run()
    assert run.status == RunStatus.WAITING_CONFIRMATION
    assert CONFIRMATION_TOOL in _tool_names(model)
    assert "回答前の確認" in str(model.calls[0].system_instructions)
    [confirmation] = run.confirmations
    assert (confirmation.source, confirmation.call_id, confirmation.round) == (
        "agent",
        "call-ask",
        1,
    )
    assert confirmation.items[0].prompt == "どの期間の売上を見ますか？"
    assert [option.label for option in confirmation.items[0].options] == ["今月", "今年度"]
    assert run.approvals == []
    # 処理の段階は「回答前の確認の答えを待っています」。
    assert any(getattr(event, "kind", None) == "confirmation" for event in run.progress_events)

    outcome = runtime_repository.decide_confirmation(
        run.id,
        confirmation.id,
        action="answer",
        answers=[ConfirmationAnswer(item_id="item_1", option_ids=["option_2"])],
    )
    assert outcome.run.status == RunStatus.QUEUED and outcome.next_run is None
    resumed = _resume(run.id)
    assert resumed.status == RunStatus.COMPLETED
    seen = _tool_output(model, 1, "call-ask")
    assert (seen["status"], seen["selected"]) == ("answered", ["今年度"])
    assert resumed.confirmations[0].status == "answered"
    # 確認のツールの呼び出しも step（監査）に残る。
    assert any(
        step.tool_call is not None and step.tool_call.name == CONFIRMATION_TOOL
        for step in resumed.steps
    )
    with pytest.raises(ConfirmationClosedError):
        runtime_repository.decide_confirmation(run.id, confirmation.id, action="skip")


def test_agent_confirmation_skip_tells_the_model_to_answer_with_assumptions(
    monkeypatch: MonkeyPatch, mcp: FakeProductMcp
) -> None:
    model = _script(monkeypatch, _ask("call-ask"), [assistant_message("前提: 今月として…")])
    run = _run()
    runtime_repository.decide_confirmation(run.id, run.confirmations[0].id, action="skip")
    resumed = _resume(run.id)
    assert resumed.status == RunStatus.COMPLETED
    seen = _tool_output(model, 1, "call-ask")
    assert seen["status"] == "skipped"
    assert "前提" in seen["instruction"]


def test_agent_confirmation_skip_retries_nl2sql_without_a_card(
    monkeypatch: MonkeyPatch, mcp: FakeProductMcp
) -> None:
    """Agent 自身の確認に「確認せずに回答」を選んだ Run の続き（#1903）。

    NL2SQL が確認事項を返しても、カードを出さずに skip_clarification で呼び直させる。
    """
    query = {"question": "全期間の売上合計金額を教えてください。", "profile_id": PROFILE}
    skip = {**query, "requirement_session_id": "qs-1", "skip_clarification": True}
    model = _script(
        monkeypatch,
        _ask("call-ask"),
        [function_call("nl2sql__nl2sql_query", query, call_id="call-1")],
        [function_call("nl2sql__nl2sql_query", skip, call_id="call-2")],
        [assistant_message("前提: 全期間・受注日で集計しました。営業が 1,200 です。")],
    )
    run = _run()
    runtime_repository.decide_confirmation(run.id, run.confirmations[0].id, action="skip")
    resumed = _resume(run.id)
    assert resumed.status == RunStatus.COMPLETED
    # 再開した Run の指示は「確認せずに回答」の方針（NL2SQL は skip_clarification で呼び直す）。
    instructions = str(model.calls[1].system_instructions)
    assert "「確認せずに回答」を選びました" in instructions
    assert "skip_clarification: true" in instructions
    assert "nl2sql__nl2sql_query" in instructions
    # NL2SQL の確認事項の結果には、聞かずに呼び直す次の手と引数を付ける。
    next_step = _tool_output(model, 2, "call-1")["next_step"]
    assert next_step["action"] == "retry_without_clarification"
    assert next_step["arguments"] == skip
    sent = mcp.calls_of("nl2sql_query")[-1]["arguments"]
    assert (sent["requirement_session_id"], sent["skip_clarification"]) == ("qs-1", True)
    # カードは出さない（Agent 自身の確認のスキップだけが残る）・確かめ中の問いも残さない。
    assert [(item.source, item.status) for item in resumed.confirmations] == [("agent", "skipped")]
    state = run_support_task(resumed)
    assert state is not None and state["pending_clarifications"] == []


def test_agent_confirmation_skip_never_shows_a_tool_card(
    monkeypatch: MonkeyPatch, mcp: FakeProductMcp
) -> None:
    """呼び直さずに本文で答えても、NL2SQL・RAG の確認のカードは出さない（#1903 のガード）。"""
    query = {"question": GOAL, "profile_id": PROFILE}
    model = _script(
        monkeypatch,
        _ask("call-ask"),
        [
            function_call("nl2sql__nl2sql_query", query, call_id="call-1"),
            function_call("rag__rag_search", {"query": "売上 定義"}, call_id="call-2"),
        ],
        _ask("call-ask-2", question="どのテーブルから売上金額を取得しますか？"),
        [assistant_message("前提: 全期間で集計する想定です。")],
    )
    run = _run()
    runtime_repository.decide_confirmation(run.id, run.confirmations[0].id, action="skip")
    resumed = _resume(run.id)
    assert resumed.status == RunStatus.COMPLETED
    assert _tool_output(model, 2, "call-2")["next_step"]["action"] == "answer_with_assumptions"
    # 2 回目の ask_user_confirmation は中断せず、確認せずに答える指示を返す。
    again = _tool_output(model, 3, "call-ask-2")
    assert (again["status"], again["instruction"]) == ("skipped", SKIPPED_INSTRUCTION)
    assert [(item.source, item.status) for item in resumed.confirmations] == [("agent", "skipped")]
    state = run_support_task(resumed)
    assert state is not None and state["pending_clarifications"] == []


def test_agent_confirmation_answer_keeps_the_nl2sql_card(
    monkeypatch: MonkeyPatch, mcp: FakeProductMcp
) -> None:
    """答えた（スキップではない）ときは今どおり、NL2SQL の確認はカードにする（#1903）。"""
    query = {"question": GOAL, "profile_id": PROFILE}
    _script(
        monkeypatch,
        _ask("call-ask"),
        [function_call("nl2sql__nl2sql_query", query, call_id="call-1")],
        [assistant_message("どの日付の列で期間を絞りますか？")],
    )
    run = _run()
    runtime_repository.decide_confirmation(
        run.id,
        run.confirmations[0].id,
        action="answer",
        answers=[ConfirmationAnswer(item_id="item_1", option_ids=["option_1"])],
    )
    resumed = _resume(run.id)
    assert resumed.status == RunStatus.COMPLETED
    assert [(item.source, item.status, item.round) for item in resumed.confirmations] == [
        ("agent", "answered", 1),
        ("nl2sql", "pending", 2),
    ]


def test_agent_confirmation_cancel_withdraws_the_turn_and_returns_the_question(
    monkeypatch: MonkeyPatch, mcp: FakeProductMcp
) -> None:
    _script(monkeypatch, [assistant_message("前の回答")])
    first = _run("前の質問")
    _script(monkeypatch, _ask("call-ask"))
    run = _run(thread_id=first.thread_id)
    outcome = runtime_repository.decide_confirmation(
        run.id, run.confirmations[0].id, action="cancel"
    )
    assert outcome.question == GOAL
    cancelled = runtime_repository.get_run(run.id)
    assert cancelled.status == RunStatus.CANCELLED
    assert cancelled.metadata[WITHDRAWN_METADATA_KEY] is True
    assert cancelled.confirmations[0].status == "cancelled"
    thread = runtime_repository.get_thread(str(first.thread_id), user_uuid=USER_UUID)
    assert [item.id for item in thread.runs] == [first.id]
    [summary] = [
        item
        for item in runtime_repository.list_threads(user_uuid=USER_UUID, agent_id=AGENT_ID)
        if item.thread_id == first.thread_id
    ]
    assert summary.run_count == 1


def test_assume_policy_does_not_offer_the_tool(
    monkeypatch: MonkeyPatch, mcp: FakeProductMcp
) -> None:
    _set_policy("assume")
    model = _script(monkeypatch, [assistant_message("前提を示して答えます。")])
    run = _run()
    assert run.status == RunStatus.COMPLETED
    assert CONFIRMATION_TOOL not in _tool_names(model)
    assert "前提を示して回答します" in str(model.calls[0].system_instructions)


def test_unattended_runs_do_not_offer_the_tool(
    monkeypatch: MonkeyPatch, mcp: FakeProductMcp
) -> None:
    model = _script(monkeypatch, [assistant_message("答え")])
    run = _run(metadata={EVALUATION_DRY_RUN_KEY: True})
    assert run.status == RunStatus.COMPLETED
    assert CONFIRMATION_TOOL not in _tool_names(model)
    assert "回答前の確認" not in str(model.calls[0].system_instructions)


def test_the_round_limit_answers_without_asking(
    monkeypatch: MonkeyPatch, mcp: FakeProductMcp
) -> None:
    model = _script(
        monkeypatch,
        _ask("call-1"),
        _ask("call-2", question="もう 1 つ確認？"),
        [assistant_message("前提を示して答えます。")],
    )
    # 前の往復で 1 回確認した（既定の上限 2 回の 2 回目）。
    run = _run(metadata={ROUND_METADATA_KEY: 1})
    assert run.status == RunStatus.WAITING_CONFIRMATION
    assert (run.confirmations[0].round, run.confirmations[0].max_rounds) == (2, 2)
    runtime_repository.decide_confirmation(
        run.id,
        run.confirmations[0].id,
        action="answer",
        answers=[ConfirmationAnswer(item_id="item_1", other_text="先月")],
    )
    resumed = _resume(run.id)
    # 上限（2 回）を超えた呼び出しは中断せず、上限に達したことを返す。
    assert resumed.status == RunStatus.COMPLETED
    assert len(resumed.confirmations) == 1
    assert _tool_output(model, 1, "call-1")["other_text"] == "先月"
    assert _tool_output(model, 2, "call-2")["status"] == "limit_reached"


# ---- ツールの結果の確認（RAG・NL2SQL） -------------------------------------------------------


def test_rag_clarification_becomes_a_card_and_the_answer_is_passed_as_conditions(
    monkeypatch: MonkeyPatch, mcp: FakeProductMcp
) -> None:
    _script(
        monkeypatch,
        [function_call("rag__rag_search", {"query": "取り消し 手順"}, call_id="call-1")],
        [assistant_message("どの画面の操作ですか？（受注入力 / 請求）")],
    )
    first = _run("受注を取り消す手順は？")
    assert first.status == RunStatus.COMPLETED
    [confirmation] = first.confirmations
    assert (confirmation.source, confirmation.can_skip, confirmation.round) == ("rag", True, 1)
    [item] = confirmation.items
    assert (item.id, item.prompt) == ("screen", "どの画面の操作ですか？")
    assert [option.label for option in item.options] == ["受注入力", "請求"]
    assert "画面" in item.reason

    outcome = runtime_repository.decide_confirmation(
        first.id,
        confirmation.id,
        action="answer",
        answers=[ConfirmationAnswer(item_id="screen", option_ids=["option_1"])],
    )
    next_run = outcome.next_run
    assert next_run is not None and next_run.thread_id == first.thread_id
    response = next_run.metadata[RESPONSE_METADATA_KEY]
    assert (response["source"], response["action"], response["outcome"]) == (
        "rag",
        "answer",
        "answered",
    )
    assert response["question_run_id"] == first.id
    assert response["entries"][0]["selected"] == ["受注入力"]
    assert next_run.metadata[ROUND_METADATA_KEY] == 1
    assert next_run.goal == "どの画面の操作ですか？: 受注入力"

    model = _script(
        monkeypatch,
        [
            function_call(
                "rag__rag_search",
                {"query": "取り消し 手順", "conditions": {"screen": "受注入力"}},
                call_id="call-2",
            )
        ],
        [assistant_message("受注入力の画面で取り消します。")],
    )
    second = _execute(next_run.id)
    assert second.status == RunStatus.COMPLETED
    instructions = str(model.calls[0].system_instructions)
    assert "回答前の確認の答え" in instructions
    assert '"screen": "受注入力"' in instructions
    state = run_support_task(second)
    assert state is not None
    assert state["known_conditions"]["screen"]["value"] == "受注入力"
    assert state["known_conditions"]["screen"]["source"] == "user_answer"
    assert second.confirmations == []


def test_validation_premises_are_the_answered_items() -> None:
    """最終の検証に渡す前提は、答えた確認事項の文と答え（選択肢の名前・自由入力）（#1898）。

    この Run の Agent 自身の確認（答えたものだけ）と、前の Run の確認に答えて作った Run の
    `clarification_response`。「確認せずに回答」・答えを待つ確認は前提にしない。
    """
    item = item_from_arguments(ASK_ARGUMENTS)
    answered = ConfirmationRequest(
        source="agent",
        items=[item],
        status="answered",
        answers=[ConfirmationAnswer(item_id="item_1", option_ids=["option_2"])],
    )
    other = ConfirmationRequest(
        source="agent",
        items=[item],
        status="answered",
        answers=[ConfirmationAnswer(item_id="item_1", other_text="  先月 と 今月  ")],
    )
    pending = ConfirmationRequest(source="agent", items=[item])
    response = {
        "source": "rag",
        "action": "answer",
        "entries": [
            {"item_id": "screen", "prompt": "どの画面の操作ですか？", "selected": ["受注入力"]},
            {"item_id": "empty", "prompt": "空", "selected": [], "other_text": ""},
        ],
    }

    premises = validation_premises(
        {RESPONSE_METADATA_KEY: response}, [answered, other, pending, answered]
    )

    assert premises == [
        {"question": "どの画面の操作ですか？", "answer": "受注入力"},
        {"question": "どの期間の売上を見ますか？", "answer": "今年度"},
        {"question": "どの期間の売上を見ますか？", "answer": "先月 と 今月"},
    ]
    skipped = {**response, "action": "skip"}
    assert validation_premises({RESPONSE_METADATA_KEY: skipped}, [pending]) == []
    assert validation_premises({}, []) == []


def test_final_validation_receives_the_agent_confirmation_answer(
    monkeypatch: MonkeyPatch, mcp: FakeProductMcp
) -> None:
    """Agent 自身の確認の答えを、最終の検証（rag_validate_answer）に前提として渡す（#1898）。"""
    monkeypatch.setattr(get_settings(), "agent_final_validation_enabled", True)
    _script(
        monkeypatch,
        _ask("call-ask"),
        [function_call("rag__rag_search", {"query": "売上 今年度"}, call_id="call-1")],
        [assistant_message("今年度の売上は 1,200 です。")],
    )
    run = _run()
    runtime_repository.decide_confirmation(
        run.id,
        run.confirmations[0].id,
        action="answer",
        answers=[ConfirmationAnswer(item_id="item_1", option_ids=["option_2"])],
    )
    resumed = _resume(run.id)

    assert resumed.status == RunStatus.COMPLETED
    [call] = mcp.calls_of("rag_validate_answer")
    assert call["arguments"]["premises"] == [
        {"question": "どの期間の売上を見ますか？", "answer": "今年度"}
    ]


def test_final_validation_receives_the_tool_confirmation_answer(
    monkeypatch: MonkeyPatch, mcp: FakeProductMcp
) -> None:
    """前の Run の RAG の確認への答え（`clarification_response`）も前提として渡す（#1898）。

    答えの無い Run（確認を出した Run）の検証には前提を付けない。
    """
    monkeypatch.setattr(get_settings(), "agent_final_validation_enabled", True)
    _script(
        monkeypatch,
        [function_call("rag__rag_search", {"query": "取り消し 手順"}, call_id="call-1")],
        [assistant_message("どの画面の操作ですか？（受注入力 / 請求）")],
    )
    first = _run("受注を取り消す手順は？")
    outcome = runtime_repository.decide_confirmation(
        first.id,
        first.confirmations[0].id,
        action="answer",
        answers=[ConfirmationAnswer(item_id="screen", option_ids=["option_1"])],
    )
    assert outcome.next_run is not None
    _script(
        monkeypatch,
        [
            function_call(
                "rag__rag_search",
                {"query": "取り消し 手順", "conditions": {"screen": "受注入力"}},
                call_id="call-2",
            )
        ],
        [assistant_message("受注入力の画面で取り消します。")],
    )
    second = _execute(outcome.next_run.id)

    assert second.status == RunStatus.COMPLETED
    calls = mcp.calls_of("rag_validate_answer")
    assert all("premises" not in call["arguments"] for call in calls[:-1])
    assert calls[-1]["arguments"]["premises"] == [
        {"question": "どの画面の操作ですか？", "answer": "受注入力"}
    ]


def test_rag_skip_answers_with_assumptions_without_asking_again(
    monkeypatch: MonkeyPatch, mcp: FakeProductMcp
) -> None:
    _script(
        monkeypatch,
        [function_call("rag__rag_search", {"query": "取り消し 手順"}, call_id="call-1")],
        [assistant_message("どの画面ですか？")],
    )
    first = _run("受注を取り消す手順は？")
    outcome = runtime_repository.decide_confirmation(
        first.id, first.confirmations[0].id, action="skip"
    )
    assert outcome.next_run is not None
    model = _script(
        monkeypatch,
        [function_call("rag__rag_search", {"query": "取り消し 手順"}, call_id="call-2")],
        [assistant_message("画面ごとに分けて示します。")],
    )
    second = _execute(outcome.next_run.id)
    assert second.status == RunStatus.COMPLETED
    assert CONFIRMATION_TOOL not in _tool_names(model)
    seen = _tool_output(model, 1, "call-2")
    assert seen["next_step"]["action"] == "answer_with_assumptions"
    # 聞き直さない（カードを出さない・確かめ中の問いを残さない）。
    assert second.confirmations == []
    state = run_support_task(second)
    assert state is not None and state["pending_clarifications"] == []


def test_assume_policy_answers_rag_clarifications_with_assumptions(
    monkeypatch: MonkeyPatch, mcp: FakeProductMcp
) -> None:
    _set_policy("assume")
    model = _script(
        monkeypatch,
        [function_call("rag__rag_search", {"query": "取り消し 手順"}, call_id="call-1")],
        [assistant_message("画面ごとに示します。")],
    )
    run = _run("受注を取り消す手順は？")
    assert _tool_output(model, 1, "call-1")["next_step"]["action"] == "answer_with_assumptions"
    assert run.confirmations == []


def test_nl2sql_clarification_card_passes_the_answer_to_nl2sql_query(
    monkeypatch: MonkeyPatch, mcp: FakeProductMcp
) -> None:
    query = {"question": GOAL, "profile_id": PROFILE}
    _script(
        monkeypatch,
        [function_call("nl2sql__nl2sql_query", query, call_id="call-1")],
        [assistant_message("どの日付の列で期間を絞りますか？")],
    )
    first = _run()
    [confirmation] = first.confirmations
    # NL2SQL の確認も「確認せずに回答」を出す（skip_clarification で呼び直す。#1839）。
    assert (confirmation.source, confirmation.can_skip) == ("nl2sql", True)
    assert [option.id for option in confirmation.items[0].options] == ["c-order", "c-ship"]
    assert confirmation.context["requirement_session_id"] == "qs-1"
    outcome = runtime_repository.decide_confirmation(
        first.id,
        confirmation.id,
        action="answer",
        answers=[ConfirmationAnswer(item_id="item-period", option_ids=["c-order"])],
    )
    assert outcome.next_run is not None
    answer = {"item_id": "item-period", "selected_candidate_ids": ["c-order"]}
    model = _script(
        monkeypatch,
        [
            function_call(
                "nl2sql__nl2sql_query",
                {**query, "requirement_session_id": "qs-1", "answer": answer},
                call_id="call-2",
            )
        ],
        [assistant_message("営業が 1,200 です。")],
    )
    second = _execute(outcome.next_run.id)
    assert second.status == RunStatus.COMPLETED
    instructions = str(model.calls[0].system_instructions)
    assert '"selected_candidate_ids": ["c-order"]' in instructions
    assert "nl2sql__nl2sql_query を次の引数で" in instructions


def test_nl2sql_skip_calls_nl2sql_query_with_skip_clarification(
    monkeypatch: MonkeyPatch, mcp: FakeProductMcp
) -> None:
    query = {"question": GOAL, "profile_id": PROFILE}
    _script(
        monkeypatch,
        [function_call("nl2sql__nl2sql_query", query, call_id="call-1")],
        [assistant_message("どの日付の列で期間を絞りますか？")],
    )
    first = _run()
    outcome = runtime_repository.decide_confirmation(
        first.id, first.confirmations[0].id, action="skip"
    )
    assert outcome.next_run is not None
    response = outcome.next_run.metadata[RESPONSE_METADATA_KEY]
    assert (response["action"], response["outcome"]) == ("skip", "skipped")
    skip = {**query, "requirement_session_id": "qs-1", "skip_clarification": True}
    model = _script(
        monkeypatch,
        [function_call("nl2sql__nl2sql_query", skip, call_id="call-2")],
        [assistant_message("前提: 受注日で集計しました。営業が 1,200 です。")],
    )
    second = _execute(outcome.next_run.id)
    assert second.status == RunStatus.COMPLETED
    instructions = str(model.calls[0].system_instructions)
    assert '"skip_clarification": true' in instructions
    assert '"requirement_session_id": "qs-1"' in instructions
    sent = mcp.calls_of("nl2sql_query")[-1]["arguments"]
    assert (sent["requirement_session_id"], sent["skip_clarification"]) == ("qs-1", True)
    assert "answer" not in sent
    # 確認せずに作ったので、確かめ中の問いもカードも残らない。
    assert second.confirmations == []
    state = run_support_task(second)
    assert state is not None and state["pending_clarifications"] == []


def test_nl2sql_contract_rejects_skip_with_answer() -> None:
    from mcp_support import Nl2SqlQueryIn

    with pytest.raises(ValidationError):
        Nl2SqlQueryIn(
            question=GOAL,
            profile_id=PROFILE,
            requirement_session_id="qs-1",
            answer={"item_id": "item-period", "selected_candidate_ids": ["c-order"]},
            skip_clarification=True,
        )


def test_nl2sql_cancel_withdraws_and_closes_the_requirement_session(
    monkeypatch: MonkeyPatch, mcp: FakeProductMcp
) -> None:
    _script(
        monkeypatch,
        [
            function_call(
                "nl2sql__nl2sql_query", {"question": GOAL, "profile_id": PROFILE}, call_id="c1"
            )
        ],
        [assistant_message("どの列ですか？")],
    )
    first = _run()
    outcome = runtime_repository.decide_confirmation(
        first.id, first.confirmations[0].id, action="cancel"
    )
    assert outcome.question == GOAL
    assert outcome.closed_sessions == [{"connection": "nl2sql", "requirement_session_id": "qs-1"}]
    anyio.run(builtin_runtime.close_requirement_sessions, first.id, outcome.closed_sessions)
    assert mcp.calls_of("nl2sql_cancel_requirement")[-1]["arguments"] == {
        "requirement_session_id": "qs-1"
    }
    assert runtime_repository.get_run(first.id).metadata[WITHDRAWN_METADATA_KEY] is True


def test_a_new_question_expires_the_previous_card(
    monkeypatch: MonkeyPatch, mcp: FakeProductMcp
) -> None:
    _script(
        monkeypatch,
        [function_call("rag__rag_search", {"query": "取り消し 手順"}, call_id="call-1")],
        [assistant_message("どの画面ですか？")],
    )
    first = _run("受注を取り消す手順は？")
    _script(monkeypatch, [assistant_message("別の答え")])
    _run("別の質問", thread_id=first.thread_id)
    assert runtime_repository.get_run(first.id).confirmations[0].status == "expired"


# ---- API ---------------------------------------------------------------------------------------


def test_confirmation_api_checks_owner_answers_and_state(
    monkeypatch: MonkeyPatch, mcp: FakeProductMcp
) -> None:
    client = TestClient(app)
    monkeypatch.setattr(router, "_schedule_builtin_run", lambda _run: None)
    created = client.post("/api/runs", json={"goal": GOAL, "agent_id": AGENT_ID})
    assert created.status_code == 200, created.text
    run_id = created.json()["data"]["id"]
    request = ConfirmationRequest(
        source="agent",
        call_id="call-ask",
        items=[item_from_arguments(ASK_ARGUMENTS)],
    )
    runtime_repository.request_builtin_approvals(run_id, [], state="{}", confirmations=[request])
    assert runtime_repository.get_run(run_id).status == RunStatus.WAITING_CONFIRMATION
    url = f"/api/runs/{run_id}/confirmations/{request.id}"

    invalid = client.post(url, json={"action": "answer", "answers": []})
    assert invalid.status_code == 422
    missing = client.post(
        f"/api/runs/{run_id}/confirmations/confirmation_x", json={"action": "skip"}
    )
    assert missing.status_code == 404

    answered = client.post(
        url,
        json={
            "action": "answer",
            "answers": [{"item_id": "item_1", "option_ids": ["option_1"], "other_text": ""}],
        },
    )
    assert answered.status_code == 200, answered.text
    assert answered.json()["data"]["run"]["status"] == "queued"
    closed = client.post(url, json={"action": "skip"})
    assert closed.status_code == 409

    # system_admin（local のローカル利用者）は、他の利用者の Run の確認にも答えられる（#1928。
    # system_admin でない利用者の 404 は test_admin_operates_others.py）。
    other = runtime_repository.create_builtin_run(
        RunCreateRequest(goal=GOAL, agent_id=AGENT_ID), created_by_user_uuid=USER_UUID
    )
    other_request = ConfirmationRequest(
        source="agent", call_id="c", items=[item_from_arguments(ASK_ARGUMENTS)]
    )
    runtime_repository.request_builtin_approvals(
        other.id, [], state="{}", confirmations=[other_request]
    )
    by_admin = client.post(
        f"/api/runs/{other.id}/confirmations/{other_request.id}", json={"action": "skip"}
    )
    assert by_admin.status_code == 200, by_admin.text
    [decided] = [
        event.payload
        for event in runtime_repository.get_run(other.id).events
        if event.payload.get("confirmation_id") == other_request.id
        and "decided_by" in event.payload
    ]
    assert decided["decided_by"] == "local"


# ---- 先に資料・業務データのツールで調べる（#1917） -------------------------------------------


def test_policy_instructions_leave_tool_owned_clarifications_to_the_tools() -> None:
    exposed = policy_instructions(ConfirmationBudget(policy="confirm"), tool_exposed=True)
    # 期間など NL2SQL・RAG が確かめる観点は、自分で先に聞かずに呼び先に任せる。
    assert "自分で先に" in exposed
    assert "利用者の質問のまま nl2sql_query" in exposed
    # 選択肢はツールの結果から作り、作れないときは自由入力にする。
    assert "options を空にして、自由入力で聞いてください" in exposed


def test_confirmation_before_any_tool_is_sent_back_to_the_tools_first(
    monkeypatch: MonkeyPatch, mcp: FakeProductMcp
) -> None:
    """ツールを呼ぶ前の確認は中断せず、先にツールで調べさせる（NL2SQL の確認がカードになる）。"""
    monkeypatch.setattr(get_settings(), "agent_tool_grounding_required", True)
    query = {"question": GOAL, "profile_id": PROFILE}
    model = _script(
        monkeypatch,
        _ask("call-early"),
        [function_call("nl2sql__nl2sql_query", query, call_id="call-1")],
        [assistant_message("どの日付の列で期間を絞りますか？")],
    )
    run = _run()
    seen = _tool_output(model, 1, "call-early")
    assert seen["status"] == "consult_tools_first"
    assert "nl2sql_query" in seen["instruction"]
    # Agent 自身の期間の確認は出ず、NL2SQL の確認事項がカードになる（期間を重ねて聞かない）。
    [confirmation] = run.confirmations
    assert confirmation.source == "nl2sql"
    assert run.status == RunStatus.COMPLETED


def test_confirmation_without_tools_twice_asks_with_free_text_only(
    monkeypatch: MonkeyPatch, mcp: FakeProductMcp
) -> None:
    """差し戻しの後もツールを呼ばずに確認したら、モデルが作った選択肢を外して自由入力で聞く。"""
    monkeypatch.setattr(get_settings(), "agent_tool_grounding_required", True)
    _script(monkeypatch, _ask("call-early"), _ask("call-again"))
    run = _run()
    assert run.status == RunStatus.WAITING_CONFIRMATION
    [confirmation] = run.confirmations
    assert confirmation.call_id == "call-again"
    assert confirmation.items[0].prompt == "どの期間の売上を見ますか？"
    assert confirmation.items[0].options == []


def test_confirmation_after_a_tool_keeps_the_options(
    monkeypatch: MonkeyPatch, mcp: FakeProductMcp
) -> None:
    """ツールで調べた後の確認は、そのまま中断して選択肢を出す。"""
    monkeypatch.setattr(get_settings(), "agent_tool_grounding_required", True)
    query = {"question": GOAL, "profile_id": PROFILE}
    _script(
        monkeypatch,
        [function_call("nl2sql__nl2sql_query", query, call_id="call-1")],
        _ask("call-ask"),
    )
    run = _run()
    assert run.status == RunStatus.WAITING_CONFIRMATION
    [confirmation] = [item for item in run.confirmations if item.source == "agent"]
    assert [option.label for option in confirmation.items[0].options] == ["今月", "今年度"]
