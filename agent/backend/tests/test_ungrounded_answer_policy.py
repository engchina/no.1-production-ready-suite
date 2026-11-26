"""資料・データで確かめていない回答の扱い（業務 Agent の `ungrounded_answer_policy`。#5859）。

根拠・データのツールを渡した Run で、モデルがどれも呼ばずに答えたときの 2 つの設定を確かめる。

- 既定（`annotate`）: モデルが返した回答をそのまま出し、回答の注意書きの先頭に注意を足す。差し戻し・
  拒答のイベントは無く、回答の対応は値を変えず basis を `ungrounded_answer` にする（確かめるべき
  実行の「根拠を使わずに答えた」）。
- 拒答（`refuse`）: 1 回差し戻し、それでも呼ばなければ拒答の文に置き換える（細部は
  `test_tool_grounding.py`）。「答えられなかった」に数える。

注意を付けない例外（拒答の設定の例外と同じ集合）・設定の保存と版・保存済みのデータの読み込み・
API の入出力もここで確かめる。モデルは SDK の `ScriptedModel`、RAG / NL2SQL は契約どおりの fake の
MCP（`mcp_support`）。値は合成。
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
from mcp_support import FakeProductMcp, fake_product_mcp, nl2sql_needs_clarification
from pr_system_settings.answer_notice import DEFAULT_ANSWER_NOTICE_TEXT, AnswerNoticeRecord
from pytest import MonkeyPatch
from security_support import client

from app.features.agent import builtin_runtime, tool_grounding
from app.features.agent.builtin_runtime import ModelTarget
from app.features.agent.confirmation import CONFIRMATION_TOOL
from app.features.agent.run_facts import fact_from_run
from app.features.agent.run_signals import SignalKind, fact_signals
from app.features.agent.runtime import (
    AGENT_VERSIONED_FIELDS,
    BUILTIN_AGENT_IDS,
    EVALUATION_DRY_RUN_KEY,
    AgentProfile,
    AgentProfilePatch,
    AgentRuntimeRepository,
    AgentVersion,
    RunCreateRequest,
    RunState,
    RunStatus,
    agent_for_run,
    run_answer_notice,
    run_answer_text,
    runtime_repository,
)
from app.features.agent.skills import AgentSkillDefinition, SkillMcpRequirement, skill_registry
from app.features.agent.tools import ToolPolicy
from app.security.service import get_security_service
from app.settings import get_settings

SKILL_ID = "test5859-skill"
AGENT_ID = "test5859-agent"
BARE_AGENT_ID = "test5859-bare-agent"
API_AGENT_ID = "test5859-api-agent"
USER_UUID = "11111111-2222-3333-4444-555555555859"
SEARCH = "rag__rag_search"
QUERY = "nl2sql__nl2sql_query"
GOAL = "合成の規程の上限はいくつですか"
UNGROUNDED = "上限は 10 です。"
POLICIES = (tool_grounding.POLICY_ANNOTATE, tool_grounding.POLICY_REFUSE)


@pytest.fixture
def mcp(monkeypatch: MonkeyPatch) -> Iterator[FakeProductMcp]:
    """RAG・NL2SQL のツールを持つ業務 Agent（既定の設定）と、ツールを持たない業務 Agent。"""
    fake = fake_product_mcp(monkeypatch)
    monkeypatch.setattr(get_settings(), "agent_tool_grounding_required", True)
    monkeypatch.setattr(builtin_runtime, "_active_policy", lambda: ToolPolicy(allow={QUERY}))
    skill_registry.upsert_custom(
        AgentSkillDefinition(
            id=SKILL_ID,
            name="合成の調べ物",
            instructions="RAG と NL2SQL で調べて答える。",
            mcp_requirements=[
                SkillMcpRequirement(server_id="rag", tool_names=["rag_search"]),
                SkillMcpRequirement(server_id="nl2sql", tool_names=["nl2sql_query"]),
            ],
        )
    )
    # 設定を書かずに作る（新しく作る業務 Agent の既定）。
    runtime_repository.create_agent(
        AgentProfile(
            id=AGENT_ID,
            name="合成の業務 Agent",
            description="合成の規程の質問に答えます。",
            skill_ids=[SKILL_ID],
        )
    )
    runtime_repository.create_agent(AgentProfile(id=BARE_AGENT_ID, name="ツールの無い業務 Agent"))
    try:
        yield fake
    finally:
        repository: Any = runtime_repository
        with repository._lock:  # noqa: SLF001 - テストの後始末
            for run_id in [
                run_id
                for run_id, run in repository._runs.items()
                if run.agent_id in {AGENT_ID, BARE_AGENT_ID}
            ]:
                repository._runs.pop(run_id)
        for agent_id in (AGENT_ID, BARE_AGENT_ID):
            with contextlib.suppress(KeyError, ValueError):
                runtime_repository.delete_agent(agent_id)
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


def _run(goal: str = GOAL, *, agent_id: str = AGENT_ID, **request: Any) -> RunState:
    created = runtime_repository.create_builtin_run(
        RunCreateRequest(goal=goal, agent_id=agent_id, **request),
        created_by_user_uuid=USER_UUID,
    )
    anyio.run(builtin_runtime.execute_run, created.id)
    return runtime_repository.get_run(created.id)


def _set_policy(policy: str) -> None:
    """設定を保存して公開する（利用者の Run は公開中の版の設定で動く）。"""
    runtime_repository.patch_agent(AGENT_ID, AgentProfilePatch(ungrounded_answer_policy=policy))
    runtime_repository.publish_agent(AGENT_ID)


def _answer_content(run: RunState) -> dict[str, Any]:
    [artifact] = [item for item in run.artifacts if item.kind == "answer"]
    return artifact.content


def _grounding_events(run: RunState) -> list[dict[str, Any]]:
    return [
        event.payload["tool_grounding"] for event in run.events if "tool_grounding" in event.payload
    ]


def _noticed(run: RunState) -> bool:
    """回答の注意書きに「資料・業務データで確かめていません」の注意があるか。"""
    return tool_grounding.UNGROUNDED_NOTICE in (run_answer_notice(run) or "")


_RUN_KINDS = [
    pytest.param({}, id="chat"),
    pytest.param({"automation_id": "auto_5859", "automation_trigger": "schedule"}, id="automation"),
    pytest.param({EVALUATION_DRY_RUN_KEY: True}, id="evaluation"),
]

# ---- 既定: そのまま出して注意を付ける ---------------------------------------------------------


@pytest.mark.parametrize("metadata", _RUN_KINDS)
def test_default_keeps_the_model_answer_and_adds_the_notice(
    monkeypatch: MonkeyPatch, mcp: FakeProductMcp, metadata: dict[str, Any]
) -> None:
    """既定の設定は、チャット・自動実行・品質評価のどの Run でも差し戻さずに注意を付ける。"""
    model = _script(monkeypatch, [assistant_message(UNGROUNDED)])

    run = _run(metadata=metadata)

    assert run.status == RunStatus.COMPLETED, run.events[-1].message
    # モデルは 1 回だけ（差し戻さない）。回答はモデルが返したまま。
    assert len(model.calls) == 1
    assert mcp.calls_of("rag_search") == [] and mcp.calls_of("nl2sql_query") == []
    assert run_answer_text(run) == UNGROUNDED
    assert _grounding_events(run) == []
    assert tool_grounding.METADATA_KEY not in run.metadata
    # 注意は本文に混ぜず、回答の注意書きの先頭に足す。
    content = _answer_content(run)
    assert tool_grounding.UNGROUNDED_NOTICE not in content["text"]
    assert content["notice"] == (
        f"{tool_grounding.UNGROUNDED_NOTICE}\n{DEFAULT_ANSWER_NOTICE_TEXT}"
    )
    assert tool_grounding.UNGROUNDED_NOTICE == "この回答は、資料・業務データで確かめていません。"
    # 回答の対応は値を変えず、basis で確かめていない回答であることを残す。
    assert content["outcome"] == {
        "schema_version": 1,
        "value": "answered",
        "basis": "ungrounded_answer",
        "rag_outcome": None,
        "signals": [],
    }
    # 確かめるべき実行は「根拠を使わずに答えた」（「答えられなかった」ではない）。
    fact = fact_from_run(run)
    assert (fact.answer_outcome, fact.answer_basis) == ("answered", "ungrounded_answer")
    assert fact_signals(fact) == [SignalKind.UNGROUNDED_ANSWER]


def test_default_notice_is_added_even_when_the_answer_notice_is_disabled(
    monkeypatch: MonkeyPatch, mcp: FakeProductMcp
) -> None:
    """運用設定の回答の注意書きが無効でも、確かめていない回答の注意は付ける。"""
    del mcp
    get_security_service().answer_notice.save(AnswerNoticeRecord(enabled=False))
    _script(monkeypatch, [assistant_message(UNGROUNDED)])

    run = _run()

    assert run_answer_text(run) == UNGROUNDED
    assert run_answer_notice(run) == tool_grounding.UNGROUNDED_NOTICE


def test_default_notice_is_not_in_the_history_of_the_next_run(
    monkeypatch: MonkeyPatch, mcp: FakeProductMcp
) -> None:
    """注意は会話の履歴（前の回答の本文）に入らず、次の Run のモデルにも渡らない。"""
    del mcp
    model = _script(
        monkeypatch,
        [assistant_message(UNGROUNDED)],
        [assistant_message("下限は 1 です。")],
    )
    first = _run()

    second = _run("下限はいくつですか", thread_id=first.thread_id)

    # 前の Run もツールで答えていない会話なので、続きの回答にも注意を付ける。
    assert _noticed(first) and _noticed(second)
    assert tool_grounding.UNGROUNDED_NOTICE not in json.dumps(
        model.calls[1].input, ensure_ascii=False, default=str
    )
    assert tool_grounding.UNGROUNDED_NOTICE not in str(model.calls[1].system_instructions)


# ---- 拒答の設定: 今と同じ ---------------------------------------------------------------------


@pytest.mark.parametrize("metadata", _RUN_KINDS)
def test_refuse_policy_sends_back_once_and_replaces_the_answer(
    monkeypatch: MonkeyPatch, mcp: FakeProductMcp, metadata: dict[str, Any]
) -> None:
    """設定を拒答にして公開すると、同じ回答を 1 回差し戻し、拒答の文に置き換える（#1835）。"""
    del mcp
    _set_policy(tool_grounding.POLICY_REFUSE)
    model = _script(
        monkeypatch,
        [assistant_message(UNGROUNDED)],
        [assistant_message(UNGROUNDED)],
    )

    run = _run(metadata=metadata)

    assert run.status == RunStatus.COMPLETED, run.events[-1].message
    assert len(model.calls) == 2
    assert [item["action"] for item in _grounding_events(run)] == ["retry", "refused"]
    text = run_answer_text(run) or ""
    assert text.startswith(tool_grounding.REFUSAL_HEAD) and UNGROUNDED not in text
    content = _answer_content(run)
    assert (content["outcome"]["value"], content["outcome"]["basis"]) == (
        "insufficient_evidence",
        "no_tool_grounding",
    )
    # 拒答の文には、確かめていない回答の注意を重ねない（運用設定の注意書きだけ）。
    assert content["notice"] == DEFAULT_ANSWER_NOTICE_TEXT
    fact = fact_from_run(run)
    assert fact_signals(fact) == [SignalKind.UNANSWERED]


def test_policy_change_takes_effect_only_after_publishing(
    monkeypatch: MonkeyPatch, mcp: FakeProductMcp
) -> None:
    """設定は版の項目。下書きを変えただけでは利用者の Run は公開中の版（既定）のまま動く。"""
    del mcp
    patched = runtime_repository.patch_agent(
        AGENT_ID, AgentProfilePatch(ungrounded_answer_policy=tool_grounding.POLICY_REFUSE)
    )
    assert patched.unpublished_changes is True
    model = _script(
        monkeypatch,
        [assistant_message(UNGROUNDED)],
        # 下書きの Run（Agent 管理が試す）と、公開の後の Run は拒答の設定で動く。
        [assistant_message(UNGROUNDED)],
        [assistant_message(UNGROUNDED)],
        [assistant_message(UNGROUNDED)],
        [assistant_message(UNGROUNDED)],
    )

    published_run = _run()
    assert run_answer_text(published_run) == UNGROUNDED and _noticed(published_run)

    draft_run = _run(draft=True)
    assert (run_answer_text(draft_run) or "").startswith(tool_grounding.REFUSAL_HEAD)

    published = runtime_repository.publish_agent(AGENT_ID)
    assert published.unpublished_changes is False
    version = published.published()
    assert version is not None and version.ungrounded_answer_policy == "refuse"
    after = _run()
    assert (run_answer_text(after) or "").startswith(tool_grounding.REFUSAL_HEAD)
    assert len(model.calls) == 5

    # 前の版（既定）に戻すと、下書きも Run も既定に戻る。
    restored = runtime_repository.restore_agent_version(AGENT_ID, 1)
    assert restored.ungrounded_answer_policy == "annotate"
    restored_run = runtime_repository.create_builtin_run(
        RunCreateRequest(goal=GOAL, agent_id=AGENT_ID), created_by_user_uuid=USER_UUID
    )
    assert agent_for_run(restored, restored_run).ungrounded_answer_policy == "annotate"


# ---- 注意を付けない例外（拒答の設定の例外と同じ集合） -----------------------------------------


@pytest.mark.parametrize("policy", POLICIES)
@pytest.mark.parametrize(
    ("question", "answer"),
    [
        ("こんにちは", "こんにちは。ご用件をお知らせください。"),
        ("テスト", "何についてお調べしましょうか？"),
        (GOAL, "確認させてください。\nどの年度の規程ですか？"),
    ],
    ids=["greeting", "asks-for-request", "clarification-only"],
)
def test_greetings_and_clarification_questions_get_no_notice(
    monkeypatch: MonkeyPatch, mcp: FakeProductMcp, policy: str, question: str, answer: str
) -> None:
    """あいさつ・用件を聞くだけの回答・確認の質問だけの回答は、どちらの設定でも拒答にも注意にもしない。"""
    del mcp
    _set_policy(policy)
    steps = [[assistant_message(answer)]]
    if policy == tool_grounding.POLICY_REFUSE and question == GOAL:
        # チャットの Run は確認のツールを渡す。拒答の設定では、本文の確認の質問を 1 回差し戻し、
        # 同じ聞き返しならそのまま出す（#1881）。あいさつ・用件の聞き返しは差し戻さない。
        steps.append([assistant_message(answer)])
    model = _script(monkeypatch, *steps)

    run = _run(question)

    assert run.status == RunStatus.COMPLETED, run.events[-1].message
    assert model.remaining_steps == 0
    assert run_answer_text(run) == answer
    assert not (run_answer_text(run) or "").startswith(tool_grounding.REFUSAL_HEAD)
    assert not _noticed(run)
    assert _answer_content(run)["outcome"]["value"] == "needs_clarification"
    assert fact_signals(fact_from_run(run)) == []


def test_default_does_not_send_back_a_text_clarification(
    monkeypatch: MonkeyPatch, mcp: FakeProductMcp
) -> None:
    """既定の設定は、確認のツールを渡した Run の本文の確認の質問も差し戻さない（そのまま出す）。"""
    del mcp
    answer = "確認させてください。\nどの年度の規程ですか？"
    model = _script(monkeypatch, [assistant_message(answer)])

    run = _run()

    assert len(model.calls) == 1
    assert CONFIRMATION_TOOL in {tool.name for tool in model.calls[0].tools}
    assert run_answer_text(run) == answer
    assert _grounding_events(run) == [] and not _noticed(run)


@pytest.mark.parametrize("policy", POLICIES)
def test_answer_grounded_by_a_tool_gets_no_notice(
    monkeypatch: MonkeyPatch, mcp: FakeProductMcp, policy: str
) -> None:
    """根拠・データのツールを呼んで答えた回答には、どちらの設定でも注意を付けない。"""
    _set_policy(policy)
    model = _script(
        monkeypatch,
        [function_call(SEARCH, {"query": "合成の規程 上限"}, call_id="call-1")],
        [assistant_message("上限は 20 です。")],
    )

    run = _run()

    assert len(model.calls) == 2 and len(mcp.calls_of("rag_search")) == 1
    assert run_answer_text(run) == "上限は 20 です。"
    assert not _noticed(run) and _grounding_events(run) == []
    assert _answer_content(run)["outcome"]["basis"] != "ungrounded_answer"
    assert fact_signals(fact_from_run(run)) == []


@pytest.mark.parametrize("policy", POLICIES)
def test_run_stopped_at_the_nl2sql_clarification_gets_no_notice(
    monkeypatch: MonkeyPatch, policy: str
) -> None:
    """NL2SQL の SQL の生成前の確認で止まった Run は、確認のカードを出し、注意を付けない。"""
    fake = fake_product_mcp(
        monkeypatch, outputs={"nl2sql_query": lambda _argument: nl2sql_needs_clarification()}
    )
    monkeypatch.setattr(get_settings(), "agent_tool_grounding_required", True)
    monkeypatch.setattr(builtin_runtime, "_active_policy", lambda: ToolPolicy(allow={QUERY}))
    skill_registry.upsert_custom(
        AgentSkillDefinition(
            id=SKILL_ID,
            name="合成の調べ物",
            instructions="NL2SQL で調べて答える。",
            mcp_requirements=[SkillMcpRequirement(server_id="nl2sql", tool_names=["nl2sql_query"])],
        )
    )
    runtime_repository.create_agent(
        AgentProfile(
            id=AGENT_ID,
            name="合成の業務 Agent",
            skill_ids=[SKILL_ID],
            ungrounded_answer_policy=policy,
        )
    )
    try:
        _script(
            monkeypatch,
            [function_call(QUERY, {"question": "合成の値", "profile_id": "p1"}, call_id="c1")],
            [assistant_message("どの日付の列で期間を絞りますか？")],
        )

        run = _run("合成の値の合計は？")

        assert run.status == RunStatus.COMPLETED, run.events[-1].message
        assert len(fake.calls_of("nl2sql_query")) == 1
        assert [item.source for item in run.confirmations] == ["nl2sql"]
        assert _answer_content(run)["outcome"]["basis"] == "nl2sql_clarification"
        assert not _noticed(run) and "notice" not in _answer_content(run)
    finally:
        repository: Any = runtime_repository
        with repository._lock:  # noqa: SLF001 - テストの後始末
            for run_id in [
                run_id for run_id, item in repository._runs.items() if item.agent_id == AGENT_ID
            ]:
                repository._runs.pop(run_id)
        with contextlib.suppress(KeyError, ValueError):
            runtime_repository.delete_agent(AGENT_ID)
        with contextlib.suppress(KeyError, ValueError):
            skill_registry.remove(SKILL_ID)


@pytest.mark.parametrize("policy", POLICIES)
def test_follow_up_of_a_grounded_answer_in_the_thread_gets_no_notice(
    monkeypatch: MonkeyPatch, mcp: FakeProductMcp, policy: str
) -> None:
    """同じ会話の前の Run が根拠・データのツールで答えていれば、続きの言い換えに注意を付けない。"""
    del mcp
    _set_policy(policy)
    table = "| 項目 | 値 |\n| --- | --- |\n| 上限 | 20 |"
    model = _script(
        monkeypatch,
        [function_call(SEARCH, {"query": "合成の規程 上限"}, call_id="call-1")],
        [assistant_message("上限は 20 です。")],
        [assistant_message(table)],
    )
    first = _run()

    second = _run("前の回答を表にして", thread_id=first.thread_id)

    assert len(model.calls) == 3
    assert run_answer_text(second) == table
    assert not _noticed(second) and _grounding_events(second) == []
    assert fact_signals(fact_from_run(second)) == []


@pytest.mark.parametrize("policy", POLICIES)
def test_run_where_the_user_cancelled_the_lookup_gets_no_notice(
    monkeypatch: MonkeyPatch, mcp: FakeProductMcp, policy: str
) -> None:
    """利用者が確認を取り消した Run は、残ったツールを呼ばなくても注意を付けない。"""
    del mcp
    _set_policy(policy)
    answer = "一般的には、上限は申請の種類で変わります。"
    model = _script(monkeypatch, [assistant_message(answer)])

    run = _run("資料の検索は不要", clarification_cancel="rag")

    assert run.status == RunStatus.COMPLETED, run.events[-1].message
    # 資料のツールは外れ、業務データのツールは残っている（根拠・データのツールを渡した Run）。
    tools = {tool.name for tool in model.calls[0].tools}
    assert QUERY in tools and SEARCH not in tools
    assert len(model.calls) == 1
    assert run_answer_text(run) == answer
    assert not _noticed(run) and _grounding_events(run) == []


@pytest.mark.parametrize("policy", POLICIES)
def test_agent_without_grounding_tools_gets_no_notice(
    monkeypatch: MonkeyPatch, mcp: FakeProductMcp, policy: str
) -> None:
    """根拠・データのツールを渡していない Run は、どちらの設定でも対象外。"""
    del mcp
    runtime_repository.patch_agent(
        BARE_AGENT_ID,
        AgentProfilePatch(ungrounded_answer_policy=policy),
    )
    runtime_repository.publish_agent(BARE_AGENT_ID)
    model = _script(monkeypatch, [assistant_message(UNGROUNDED)])

    run = _run(agent_id=BARE_AGENT_ID)

    assert len(model.calls) == 1
    assert run_answer_text(run) == UNGROUNDED
    assert not _noticed(run) and _grounding_events(run) == []
    assert _answer_content(run)["outcome"]["basis"] == "answer_passages"


@pytest.mark.parametrize("policy", POLICIES)
def test_turned_off_aid_does_nothing_under_either_policy(
    monkeypatch: MonkeyPatch, mcp: FakeProductMcp, policy: str
) -> None:
    """実行補助を外す（`AGENT_TOOL_GROUNDING_REQUIRED=false`）と、差し戻し・拒答・注意のどれもしない。"""
    del mcp
    _set_policy(policy)
    monkeypatch.setattr(get_settings(), "agent_tool_grounding_required", False)
    model = _script(monkeypatch, [assistant_message(UNGROUNDED)])

    run = _run()

    assert len(model.calls) == 1
    assert run_answer_text(run) == UNGROUNDED
    assert not _noticed(run) and _grounding_events(run) == []


# ---- 判定と文（決定的な部品） ------------------------------------------------------------------


def test_ungrounded_marks_change_only_the_basis_and_the_notice() -> None:
    outcome = {
        "schema_version": 1,
        "value": "answered",
        "basis": "answer_passages",
        "rag_outcome": None,
        "signals": [],
    }

    assert tool_grounding.ungrounded_outcome(outcome) == {**outcome, "basis": "ungrounded_answer"}
    assert outcome["basis"] == "answer_passages"
    assert tool_grounding.ungrounded_notice(None) == tool_grounding.UNGROUNDED_NOTICE
    assert tool_grounding.ungrounded_notice("運用の注意") == (
        f"{tool_grounding.UNGROUNDED_NOTICE}\n運用の注意"
    )
    # 拒答の段階は拒答（insufficient_evidence）にだけ付く。そのまま出した回答は拒答に数えない。
    assert tool_grounding.refusal_stage("answered", []) is None


# ---- 設定の既定・保存済みのデータ・版・API ----------------------------------------------------


def test_new_builtin_and_saved_agents_default_to_annotate(tmp_path: Path) -> None:
    """新しく作る業務 Agent・組み込みの業務 Agent・この項目の無い保存は、すべて既定になる。"""
    assert AgentProfile(name="新しい業務 Agent").ungrounded_answer_policy == "annotate"
    assert "ungrounded_answer_policy" in AGENT_VERSIONED_FIELDS
    builtin = [item for item in runtime_repository.list_agents() if item.id in BUILTIN_AGENT_IDS]
    assert len(builtin) == len(BUILTIN_AGENT_IDS) == 8
    for agent in builtin:
        published = agent.published()
        assert agent.ungrounded_answer_policy == "annotate", agent.id
        assert published is not None and published.ungrounded_answer_policy == "annotate"

    # 保存済みの snapshot（下書きと版）からこの項目を消して読み直す（#5859 より前の保存）。
    path = tmp_path / "runtime.json"
    repository = AgentRuntimeRepository(path)
    repository.create_agent(
        AgentProfile(id="saved-5859", name="保存済みの業務 Agent", clarification_policy="assume")
    )
    repository.publish_agent("saved-5859")
    saved = json.loads(path.read_text(encoding="utf-8"))
    removed = _drop_key(saved, "ungrounded_answer_policy")
    assert removed >= 3  # 下書きと 2 つの版
    path.write_text(json.dumps(saved, ensure_ascii=False), encoding="utf-8")

    reloaded = next(
        item for item in AgentRuntimeRepository(path).list_agents() if item.id == "saved-5859"
    )
    assert reloaded.ungrounded_answer_policy == "annotate"
    assert reloaded.clarification_policy == "assume"
    assert [item.ungrounded_answer_policy for item in reloaded.versions] == ["annotate"] * 2
    # 版の 1 件を素の dict から読んでも同じ。
    assert AgentVersion.model_validate({"version": 1, "name": "v1"}).ungrounded_answer_policy == (
        "annotate"
    )


def _drop_key(value: Any, key: str) -> int:
    """入れ子の JSON から key を消し、消した数を返す。"""
    removed = 0
    if isinstance(value, dict):
        if key in value:
            del value[key]
            removed += 1
        removed += sum(_drop_key(item, key) for item in value.values())
    elif isinstance(value, list):
        removed += sum(_drop_key(item, key) for item in value)
    return removed


def test_saved_refuse_policy_survives_a_reload_with_its_versions(tmp_path: Path) -> None:
    """拒答の設定は、下書き・公開した版と一緒に保存され、読み直しても残る。"""
    path = tmp_path / "runtime.json"
    repository = AgentRuntimeRepository(path)
    repository.create_agent(AgentProfile(id="saved-5859", name="保存済みの業務 Agent"))
    repository.patch_agent("saved-5859", AgentProfilePatch(ungrounded_answer_policy="refuse"))
    repository.publish_agent("saved-5859", note="拒答にする")

    reloaded = next(
        item for item in AgentRuntimeRepository(path).list_agents() if item.id == "saved-5859"
    )

    assert reloaded.ungrounded_answer_policy == "refuse"
    assert [(item.version, item.ungrounded_answer_policy) for item in reloaded.versions] == [
        (1, "annotate"),
        (2, "refuse"),
    ]
    assert reloaded.published_version == 2 and reloaded.unpublished_changes is False


@pytest.fixture
def api_agent() -> Iterator[None]:
    try:
        yield
    finally:
        with contextlib.suppress(KeyError, ValueError):
            runtime_repository.delete_agent(API_AGENT_ID)


def test_api_reads_and_writes_the_policy_with_the_versions(api_agent: None) -> None:
    """作成・変更・公開・前の版に戻すの API が、設定を入出力し、版と一緒に運ぶ。"""
    del api_agent
    created = client.post("/api/agents", json={"id": API_AGENT_ID, "name": "API の業務 Agent"})
    assert created.status_code == 200, created.text
    assert created.json()["data"]["ungrounded_answer_policy"] == "annotate"
    v1 = client.post(f"/api/agents/{API_AGENT_ID}/publish", json={}).json()["data"]
    assert v1["versions"][0]["ungrounded_answer_policy"] == "annotate"

    patched = client.patch(
        f"/api/agents/{API_AGENT_ID}", json={"ungrounded_answer_policy": "refuse"}
    )
    assert patched.status_code == 200, patched.text
    data = patched.json()["data"]
    assert (data["ungrounded_answer_policy"], data["unpublished_changes"]) == ("refuse", True)
    listed = next(
        item
        for item in client.get("/api/agents").json()["data"]["agents"]
        if item["id"] == API_AGENT_ID
    )
    assert listed["ungrounded_answer_policy"] == "refuse"

    v2 = client.post(f"/api/agents/{API_AGENT_ID}/publish", json={}).json()["data"]
    assert [item["ungrounded_answer_policy"] for item in v2["versions"]] == ["annotate", "refuse"]
    assert v2["unpublished_changes"] is False

    restored = client.post(f"/api/agents/{API_AGENT_ID}/versions/1/restore").json()["data"]
    assert (restored["published_version"], restored["ungrounded_answer_policy"]) == (
        1,
        "annotate",
    )

    # 選択肢に無い値は保存しない。
    invalid = client.patch(
        f"/api/agents/{API_AGENT_ID}", json={"ungrounded_answer_policy": "ignore"}
    )
    assert invalid.status_code == 422, invalid.text
    invalid_created = client.post(
        "/api/agents",
        json={
            "id": f"{API_AGENT_ID}-2",
            "name": "拒答の業務 Agent",
            "ungrounded_answer_policy": "x",
        },
    )
    assert invalid_created.status_code == 422
