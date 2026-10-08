"""回答の最終の検証（#1246・#1277）の決定論テスト。

モデルは SDK の `ScriptedModel`、RAG は契約どおりの fake の MCP（`mcp_support`）にする。
Control Plane が（モデルではなく）`rag_validate_answer` を、その Run の根拠でツールの境界を
通して呼び、結果を成果物に残し、確かめられない点・検証できなかったことを回答の末尾に示すことを
確かめる。
"""

from __future__ import annotations

import contextlib
from collections.abc import Iterator
from copy import deepcopy
from typing import Any

import anyio
import pytest
from agents import FunctionTool
from agents.testing import ScriptedModel, assistant_message, function_call
from mcp_support import DEFAULT_OUTPUTS, FakeProductMcp, McpToolError, fake_product_mcp
from pr_backend_core.mcp import McpServer
from pytest import MonkeyPatch

from app.features.agent import builtin_runtime
from app.features.agent.answer_validation import merge_results, withhold_paragraphs
from app.features.agent.builtin_runtime import ModelTarget
from app.features.agent.config import runtime_config_store
from app.features.agent.runtime import (
    AgentProfile,
    RunCreateRequest,
    RunState,
    RunStatus,
    run_answer_text,
    run_support_task,
    runtime_repository,
)
from app.features.agent.skills import AgentSkillDefinition, SkillMcpRequirement, skill_registry
from app.features.agent.tools import ToolPolicy
from app.settings import Settings, get_settings

SKILL_ID = "test1246-skill"
AGENT_ID = "test1246-agent"
USER_UUID = "11111111-2222-3333-4444-555555555555"
VALIDATOR = "rag__rag_validate_answer"
ANSWER = "契約の更新は 30 日前までに申し出ます。"


@pytest.fixture
def mcp(monkeypatch: MonkeyPatch) -> Iterator[FakeProductMcp]:
    """RAG の rag_search・rag_retrieve_evidence だけを使う業務 Agent（検証のツールは渡さない）。"""
    fake = fake_product_mcp(monkeypatch)
    monkeypatch.setattr(get_settings(), "agent_final_validation_enabled", True)
    skill_registry.upsert_custom(
        AgentSkillDefinition(
            id=SKILL_ID,
            name="検証の Skill",
            instructions="RAG で調べて答える。",
            mcp_requirements=[
                SkillMcpRequirement(
                    server_id="rag", tool_names=["rag_search", "rag_retrieve_evidence"]
                )
            ],
        )
    )
    runtime_repository.create_agent(
        AgentProfile(id=AGENT_ID, name="検証の業務 Agent", skill_ids=[SKILL_ID])
    )
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


def _searched_run(monkeypatch: MonkeyPatch, answer: str = ANSWER) -> tuple[RunState, ScriptedModel]:
    model = _script(
        monkeypatch,
        [function_call("rag__rag_search", {"query": "契約の更新"}, call_id="call-1")],
        [assistant_message(answer)],
    )
    run = runtime_repository.create_builtin_run(
        RunCreateRequest(goal="契約の更新の期限は？", agent_id=AGENT_ID),
        created_by_user_uuid=USER_UUID,
    )
    anyio.run(builtin_runtime.execute_run, run.id)
    return runtime_repository.get_run(run.id), model


def _validation(run: RunState) -> dict[str, Any]:
    [artifact] = [item for item in run.artifacts if item.kind == "answer_validation"]
    assert artifact.name == "回答の検証"
    return artifact.content


def test_valid_answer_is_kept_and_the_result_is_saved(
    monkeypatch: MonkeyPatch, mcp: FakeProductMcp
) -> None:
    run, model = _searched_run(monkeypatch)

    assert run.status == RunStatus.COMPLETED, run.events[-1].message
    assert run_answer_text(run) == ANSWER
    # モデルには検証のツールを渡さない（Control Plane が呼ぶ）。
    tools = {tool.name for tool in model.calls[0].tools if isinstance(tool, FunctionTool)}
    assert VALIDATOR not in tools
    # その Run の質問・回答・RAG の根拠で、Run の利用者のサービストークンで呼ぶ。
    [call] = mcp.calls_of("rag_validate_answer")
    assert call["arguments"] == {
        "query": "契約の更新の期限は？",
        "answer": ANSWER,
        "evidence": [{"document_id": "doc-1", "chunk_id": "chunk-1"}],
    }
    assert call["claims"]["sub"] == USER_UUID
    assert call["claims"]["run_id"] == run.id
    # ツールの境界（step・監査）を通す。
    step = run.steps[-1]
    assert step.tool_call is not None and step.tool_call.name == VALIDATOR
    assert step.status == "completed"
    assert step.tool_result is not None
    assert step.tool_result.audit_metadata["tool_name"] == VALIDATOR
    content = _validation(run)
    assert content["status"] == "completed"
    assert content["valid"] is True
    assert content["connection"] == "rag"
    assert content["step_id"] == step.id
    assert content["result"]["counts"]["supported"] == 1
    # Control Plane の検証はモデルの予算に数えない。
    state = run_support_task(run)
    assert state is not None
    assert state["budget"]["run"]["tool_calls"] == 1


def test_validation_is_on_by_default() -> None:
    # 既定で on（#1277）。環境変数 AGENT_FINAL_VALIDATION_ENABLED=false で切れる。
    assert Settings.model_fields["agent_final_validation_enabled"].default is True


STEP_OK = "1. 管理画面で「更新」を押します。"
STEP_FEE = "2. 手数料として 5,000 円を支払い" + "、担当者の確認を受け" * 6 + "ます。"
STEP_WRONG = "3. 解約は 60 日前までに申し出ます。"


def test_failed_verdict_withholds_unsupported_steps_with_a_notice(
    monkeypatch: MonkeyPatch, mcp: FakeProductMcp
) -> None:
    mcp.outputs["rag_validate_answer"] = {
        "valid": False,
        "status": "completed",
        "counts": {"supported": 2, "unsupported": 1, "contradicted": 1},
        "claims": [
            {"answer_quote": ANSWER, "status": "supported", "chunk_id": "chunk-1", "reason": "ok"},
            {"answer_quote": STEP_OK, "status": "supported", "chunk_id": "chunk-1", "reason": "ok"},
            {"answer_quote": STEP_FEE, "status": "unsupported", "reason": "根拠に金額が無い。"},
            {
                "answer_quote": STEP_WRONG,
                "status": "contradicted",
                "chunk_id": "chunk-1",
                "reason": "根拠は 30 日前。",
            },
        ],
        "missing_evidence": [],
        "stale_evidence": [{"document_id": "doc-1", "chunk_id": "chunk-1"}],
        "evidence_truncated": False,
    }

    run, _ = _searched_run(monkeypatch, answer=f"{ANSWER}\n\n{STEP_OK}\n{STEP_FEE}\n{STEP_WRONG}")

    answer = run_answer_text(run)
    assert answer is not None
    # 根拠で確かめた段落だけを載せ、確かめられなかった手順は外す（handoff §12）。
    body, section = answer.split("\n\n**確かめられていない点**\n\n", 1)
    assert body == f"{ANSWER}\n\n{STEP_OK}"
    assert "手数料" not in body and "60 日前" not in body
    lines = section.splitlines()
    assert lines[0] == "根拠で確かめられなかった次の内容は、回答に載せていません。"
    assert lines[1].startswith("- 根拠で確かめられない: 「2. 手数料として 5,000 円を支払い")
    quote = lines[1].split("「", 1)[1].split("」", 1)[0]
    assert len(quote) == 80 and quote.endswith("…")
    assert lines[1].endswith("（根拠に金額が無い。）")
    assert lines[2] == f"- 根拠と矛盾: 「{STEP_WRONG}」（根拠は 30 日前。）"
    assert lines[3].startswith("- 根拠の 1 件は文書の古い版です。")
    assert len(lines) == 4
    content = _validation(run)
    assert (content["status"], content["valid"]) == ("completed", False)
    assert content["withheld"] == {"claims": 2, "all": False}


def test_verdict_without_readable_evidence_withholds_the_whole_answer(
    monkeypatch: MonkeyPatch, mcp: FakeProductMcp
) -> None:
    mcp.outputs["rag_validate_answer"] = {
        "valid": False,
        "status": "no_evidence",
        "counts": {},
        "claims": [],
        "missing_evidence": [{"document_id": "doc-1", "chunk_id": "chunk-1"}],
        "stale_evidence": [],
        "evidence_truncated": False,
    }

    run, _ = _searched_run(monkeypatch)

    assert run_answer_text(run) == (
        "根拠で確かめられた内容はありませんでした。\n\n**確かめられていない点**\n\n"
        "根拠で確かめられなかった次の内容は、回答に載せていません。\n"
        "- 根拠の 1 件は見つかりません（削除された・参照できない）。"
    )
    assert _validation(run)["withheld"] == {"claims": 0, "all": True}


def test_validator_failure_keeps_the_answer_with_a_notice(
    monkeypatch: MonkeyPatch, mcp: FakeProductMcp
) -> None:
    mcp.outputs["rag_validate_answer"] = McpToolError("RAG_UNAVAILABLE", "一時的に利用できません。")

    run, _ = _searched_run(monkeypatch)

    assert run.status == RunStatus.COMPLETED
    # 基盤の障害では内容を落とさない（確かめていないことを示す）。
    assert run_answer_text(run) == f"{ANSWER}\n\nこの回答は検証できませんでした。"
    content = _validation(run)
    assert content["status"] == "unvalidated"
    assert content["reason"] == "mcp.tool_error"
    assert content["message"] == "一時的に利用できません。"
    assert run.steps[-1].status == "failed"


def test_validator_timeout_keeps_the_answer_with_a_notice(
    monkeypatch: MonkeyPatch, mcp: FakeProductMcp
) -> None:
    def search(_argument: Any) -> dict[str, Any]:
        # 検索の後の呼び出し（検証）だけ、呼び先に届いた後の読み取りの timeout にする（再試行も）。
        mcp.tool_call_statuses.extend(["timeout"] * 5)
        return deepcopy(DEFAULT_OUTPUTS["rag_search"])

    mcp.outputs["rag_search"] = search

    run, _ = _searched_run(monkeypatch)

    assert run_answer_text(run) == f"{ANSWER}\n\nこの回答は検証できませんでした。"
    assert _validation(run)["status"] == "unvalidated"


def test_unexpected_validation_error_keeps_the_answer_and_records_it(
    monkeypatch: MonkeyPatch, mcp: FakeProductMcp
) -> None:
    def broken(_steps: Any) -> Any:
        raise RuntimeError("boom")

    monkeypatch.setattr(builtin_runtime, "run_evidence_groups", broken)

    run, _ = _searched_run(monkeypatch)

    assert run.status == RunStatus.COMPLETED
    assert run_answer_text(run) == f"{ANSWER}\n\nこの回答は検証できませんでした。"
    content = _validation(run)
    assert content["status"] == "unvalidated"
    assert content["reason"] == "validation_error"


def test_unusable_validator_result_keeps_the_answer_with_a_notice(
    monkeypatch: MonkeyPatch, mcp: FakeProductMcp
) -> None:
    mcp.outputs["rag_validate_answer"] = {
        "valid": False,
        "status": "input_too_large",
        "counts": {},
        "claims": [],
        "missing_evidence": [],
        "stale_evidence": [],
        "evidence_truncated": False,
    }

    run, _ = _searched_run(monkeypatch)

    assert run_answer_text(run) == f"{ANSWER}\n\nこの回答は検証できませんでした。"
    content = _validation(run)
    assert (content["status"], content["reason"]) == ("unvalidated", "unusable_result")


def test_policy_denied_validator_is_not_called(
    monkeypatch: MonkeyPatch, mcp: FakeProductMcp
) -> None:
    monkeypatch.setattr(builtin_runtime, "_active_policy", lambda: ToolPolicy(deny={VALIDATOR}))

    run, _ = _searched_run(monkeypatch)

    assert mcp.calls_of("rag_validate_answer") == []
    assert run_answer_text(run) == f"{ANSWER}\n\nこの回答は検証できませんでした。"
    assert _validation(run)["reason"] == "policy_denied"


def test_disabled_validation_is_not_called(monkeypatch: MonkeyPatch, mcp: FakeProductMcp) -> None:
    monkeypatch.setattr(get_settings(), "agent_final_validation_enabled", False)

    run, _ = _searched_run(monkeypatch)

    assert mcp.calls_of("rag_validate_answer") == []
    assert run_answer_text(run) == ANSWER
    assert not [item for item in run.artifacts if item.kind == "answer_validation"]


def test_rag_agent_answer_without_evidence_is_unvalidated(
    monkeypatch: MonkeyPatch, mcp: FakeProductMcp
) -> None:
    _script(monkeypatch, [assistant_message("資料を調べずに答えました。")])
    created = runtime_repository.create_builtin_run(
        RunCreateRequest(goal="こんにちは", agent_id=AGENT_ID), created_by_user_uuid=USER_UUID
    )

    anyio.run(builtin_runtime.execute_run, created.id)

    run = runtime_repository.get_run(created.id)
    assert mcp.calls_of("rag_validate_answer") == []
    assert run_answer_text(run) == (
        "資料を調べずに答えました。\n\n"
        "この回答は資料の根拠を使っておらず、資料と照らし合わせて確かめていません。"
    )
    content = _validation(run)
    assert (content["status"], content["reason"]) == ("unvalidated", "no_rag_evidence")


def test_agent_without_rag_tools_is_skipped(monkeypatch: MonkeyPatch, mcp: FakeProductMcp) -> None:
    del mcp
    skill_registry.upsert_custom(
        AgentSkillDefinition(
            id=f"{SKILL_ID}-sql",
            name="SQL の Skill",
            instructions="NL2SQL で調べる。",
            mcp_requirements=[SkillMcpRequirement(server_id="nl2sql", tool_names=["nl2sql_query"])],
        )
    )
    runtime_repository.create_agent(
        AgentProfile(id=f"{AGENT_ID}-sql", name="SQL の業務 Agent", skill_ids=[f"{SKILL_ID}-sql"])
    )
    try:
        _script(monkeypatch, [assistant_message("売上は 100 件です。")])
        created = runtime_repository.create_builtin_run(
            RunCreateRequest(goal="売上は？", agent_id=f"{AGENT_ID}-sql"),
            created_by_user_uuid=USER_UUID,
        )

        anyio.run(builtin_runtime.execute_run, created.id)

        run = runtime_repository.get_run(created.id)
        # RAG のツールを持たない Agent は今までどおり（注記を足さない）。
        assert run_answer_text(run) == "売上は 100 件です。"
        content = _validation(run)
        assert (content["status"], content["reason"]) == ("skipped", "no_rag_evidence")
    finally:
        repository: Any = runtime_repository
        with repository._lock:  # noqa: SLF001 - テストの後始末
            for run_id in [
                run_id
                for run_id, run in repository._runs.items()
                if run.agent_id == f"{AGENT_ID}-sql"
            ]:
                repository._runs.pop(run_id)
        runtime_repository.delete_agent(f"{AGENT_ID}-sql")
        skill_registry.remove(f"{SKILL_ID}-sql")


def test_evidence_of_all_rag_calls_is_passed_newest_first(
    monkeypatch: MonkeyPatch, mcp: FakeProductMcp
) -> None:
    retrieved = deepcopy(DEFAULT_OUTPUTS["rag_retrieve_evidence"])
    first = retrieved["evidence"][0]
    retrieved["evidence"] = [
        {**first, "evidence_id": "chunk-2", "chunk_id": "chunk-2"},
        first,
    ]
    mcp.outputs["rag_retrieve_evidence"] = retrieved
    _script(
        monkeypatch,
        [function_call("rag__rag_retrieve_evidence", {"query": "契約"}, call_id="call-1")],
        [function_call("rag__rag_search", {"query": "契約の更新"}, call_id="call-2")],
        [assistant_message(ANSWER)],
    )
    created = runtime_repository.create_builtin_run(
        RunCreateRequest(goal="契約の更新の期限は？", agent_id=AGENT_ID),
        created_by_user_uuid=USER_UUID,
    )

    anyio.run(builtin_runtime.execute_run, created.id)

    [call] = mcp.calls_of("rag_validate_answer")
    # 新しい呼び出し（rag_search）の根拠から、重複を除いて渡す。
    assert call["arguments"]["evidence"] == [
        {"document_id": "doc-1", "chunk_id": "chunk-1"},
        {"document_id": "doc-1", "chunk_id": "chunk-2"},
    ]


PARAGRAPH_A = "契約の更新は 30 日前までに申し出ます。"
PARAGRAPH_B = "更新の手数料は無料です。"


def test_evidence_of_each_rag_connection_is_validated_and_merged(
    monkeypatch: MonkeyPatch, mcp: FakeProductMcp
) -> None:
    # 2 つ目の RAG の接続（同じ fake の RAG。サービストークンの aud は rag）。
    connections: Any = runtime_config_store._mcp_servers  # noqa: SLF001 - fake の接続を足す
    connections["rag2"] = connections["rag"].model_copy(
        update={"server_id": "rag2", "label": "RAG 2", "service_audience": "rag"}
    )
    skill_registry.upsert_custom(
        AgentSkillDefinition(
            id=SKILL_ID,
            name="検証の Skill",
            instructions="2 つの RAG で調べて答える。",
            mcp_requirements=[
                SkillMcpRequirement(server_id="rag", tool_names=["rag_search"]),
                SkillMcpRequirement(server_id="rag2", tool_names=["rag_search"]),
            ],
        )
    )

    def search(argument: Any) -> dict[str, Any]:
        output: dict[str, Any] = deepcopy(DEFAULT_OUTPUTS["rag_search"])
        chunk = "chunk-a" if argument.query == "更新の期限" else "chunk-b"
        output["evidence"] = [{**output["evidence"][0], "evidence_id": chunk, "chunk_id": chunk}]
        return output

    def validate(argument: Any) -> dict[str, Any]:
        # 接続ごとに、自分の根拠で確かめられる段落だけを裏付ける。
        chunks = {item.chunk_id for item in argument.evidence}
        supported = PARAGRAPH_A if "chunk-a" in chunks else PARAGRAPH_B
        claims = [
            {
                "answer_quote": quote,
                "status": "supported" if quote == supported else "unsupported",
                "chunk_id": next(iter(chunks)) if quote == supported else None,
                "reason": "根拠の範囲",
            }
            for quote in (PARAGRAPH_A, PARAGRAPH_B)
        ]
        return {
            "valid": False,
            "status": "completed",
            "counts": {"supported": 1, "unsupported": 1},
            "claims": claims,
            "missing_evidence": [],
            "stale_evidence": [],
            "evidence_truncated": False,
        }

    mcp.outputs["rag_search"] = search
    mcp.outputs["rag_validate_answer"] = validate
    answer = f"{PARAGRAPH_A}\n{PARAGRAPH_B}"
    _script(
        monkeypatch,
        [function_call("rag__rag_search", {"query": "更新の期限"}, call_id="call-1")],
        [function_call("rag2__rag_search", {"query": "更新の手数料"}, call_id="call-2")],
        [assistant_message(answer)],
    )
    created = runtime_repository.create_builtin_run(
        RunCreateRequest(goal="契約の更新の期限と手数料は？", agent_id=AGENT_ID),
        created_by_user_uuid=USER_UUID,
    )

    anyio.run(builtin_runtime.execute_run, created.id)

    run = runtime_repository.get_run(created.id)
    # 接続ごとに、その接続の根拠だけで 1 回ずつ呼ぶ（最も新しく根拠を返した接続から）。
    calls = mcp.calls_of("rag_validate_answer")
    assert [call["arguments"]["evidence"] for call in calls] == [
        [{"document_id": "doc-1", "chunk_id": "chunk-b"}],
        [{"document_id": "doc-1", "chunk_id": "chunk-a"}],
    ]
    names = [step.tool_call.name for step in run.steps if step.tool_call is not None]
    assert names[-2:] == ["rag2__rag_validate_answer", "rag__rag_validate_answer"]
    # 段落ごとにまとめると、どちらの段落もどれかの接続の根拠で裏付けられる。
    assert run_answer_text(run) == answer
    content = _validation(run)
    assert (content["status"], content["valid"]) == ("completed", True)
    assert content["connection"] is None
    assert [item["connection"] for item in content["connections"]] == ["rag2", "rag"]
    assert [item["status"] for item in content["connections"]] == ["completed", "completed"]
    assert content["result"]["counts"] == {"supported": 2}


def test_rag_without_the_validator_is_unvalidated(
    monkeypatch: MonkeyPatch, mcp: FakeProductMcp
) -> None:
    rag = mcp.servers["rag"]
    mcp.servers["rag"] = McpServer(
        name=rag.name,
        version="test",
        tools=[tool for tool in rag.tools.values() if tool.name != "rag_validate_answer"],
    )

    run, _ = _searched_run(monkeypatch)

    assert run_answer_text(run) == f"{ANSWER}\n\nこの回答は検証できませんでした。"
    content = _validation(run)
    assert (content["status"], content["reason"]) == ("unvalidated", "validator_unavailable")


def test_merge_keeps_contradictions_and_strictest_claim_of_a_connection() -> None:
    first = {
        "valid": False,
        "status": "completed",
        "claims": [
            {"answer_quote": "A。", "status": "supported"},
            {"answer_quote": "A。", "status": "unsupported", "reason": "一部だけ"},
            {"answer_quote": "B。", "status": "contradicted", "reason": "矛盾"},
        ],
    }
    second = {
        "valid": True,
        "status": "completed",
        "claims": [
            {"answer_quote": "A。", "status": "supported"},
            {"answer_quote": "B。", "status": "supported"},
        ],
    }

    merged = merge_results([first, second])

    # 接続の中では最も厳しい判定、接続をまたいでは矛盾を残し、裏付けがあれば通す。
    assert [(item["answer_quote"], item["status"]) for item in merged["claims"]] == [
        ("A。", "supported"),
        ("B。", "contradicted"),
    ]
    assert merged["valid"] is False


def test_withhold_removes_only_the_listed_paragraphs() -> None:
    answer = "会費は無料です。\n無料です。\n手順: 開く。押す。閉じる。"

    # 同じ文を含む別の段落（「会費は無料です。」）は削らない。行の中の段落だけを外す。
    assert (
        withhold_paragraphs(answer, {"無料です。", "押す。"})
        == "会費は無料です。\n手順: 開く。閉じる。"
    )
    # 位置を決められない段落があれば本文を載せない。
    assert withhold_paragraphs(answer, {"どこにも無い。"}) is None
