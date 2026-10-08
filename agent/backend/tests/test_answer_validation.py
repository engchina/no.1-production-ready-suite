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
from app.features.agent.answer_passages import non_claim_passages
from app.features.agent.answer_validation import (
    connection_check_inputs,
    merge_results,
    publish_answer,
    withhold_paragraphs,
)
from app.features.agent.builtin_runtime import ModelTarget
from app.features.agent.config import runtime_config_store
from app.features.agent.runtime import (
    AgentProfile,
    RunCreateRequest,
    RunState,
    RunStatus,
    RunStep,
    StepStatus,
    run_answer_text,
    run_support_task,
    runtime_repository,
)
from app.features.agent.skills import AgentSkillDefinition, SkillMcpRequirement, skill_registry
from app.features.agent.tools import ToolCall, ToolPolicy, ToolResult
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
        # rag_search の要求ごとの充足（決定的な検査。#1276）。
        "requests": [{"id": "Q1", "text": "契約条項", "status": "addressed"}],
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
    assert content["withheld"] == {"claims": 2, "findings": 0, "all": False}


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
    assert _validation(run)["withheld"] == {"claims": 0, "findings": 0, "all": True}


def test_invalid_answer_shows_deterministic_findings(
    monkeypatch: MonkeyPatch, mcp: FakeProductMcp
) -> None:
    """RAG の決定的な検査の error（#1276）も「確かめられていない点」に出す（warning は除く）。"""
    output = deepcopy(DEFAULT_OUTPUTS["rag_validate_answer"])
    output["valid"] = False
    output["checks"] = ["requests", "guide", "guide_steps", "impact"]
    output["findings"] = [
        {
            "check": "requests",
            "code": "request_missing",
            "severity": "error",
            "message": "要求「更新の手数料」に答えていません。不足も示していません。",
            "request_id": "Q2",
        },
        {
            "check": "guide_steps",
            "code": "step_dependency_missing",
            "severity": "warning",
            "message": "手順「申し出る」の前の手順「契約を開く」が回答にありません。",
            "step_id": "s2",
            "related_step_id": "s1",
        },
    ]
    mcp.outputs["rag_validate_answer"] = output

    run, _ = _searched_run(monkeypatch)

    answer = run_answer_text(run)
    assert answer is not None
    section = answer.split("**確かめられていない点**", 1)[1]
    lines = [line for line in section.strip().splitlines() if line]
    assert lines == ["- 要求「更新の手数料」に答えていません。不足も示していません。"]
    content = _validation(run)
    assert content["result"]["findings"][0]["code"] == "request_missing"


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


GUIDE_STEP_ERROR = {
    "check": "guide_steps",
    "code": "step_order",
    "severity": "error",
    "message": "手順「申し出る」を、先に行う手順「契約を開く」より前に書いています。",
    "step_id": "s2",
    "related_step_id": "s1",
}


def test_search_guide_and_requests_are_checked_and_step_errors_withhold_the_answer(
    monkeypatch: MonkeyPatch, mcp: FakeProductMcp
) -> None:
    """rag_search の要求・不足・業務ガイドを渡し、手順の error は本文を載せない（#1276・#1277）。"""
    search = deepcopy(DEFAULT_OUTPUTS["rag_search"])
    search["requests"] = [{"id": "Q1", "text": "更新の期限", "status": "partial"}]
    search["gaps"] = ["更新の手数料"]
    search["guide"] = {
        "guide_id": "guide-1",
        "revision": 3,
        "title": "契約の更新",
        "decision": "answer",
        "known_conditions": [{"id": "kind", "label": "契約の種類", "value": "年間"}],
        "unknown_conditions": [],
    }
    search["provenance"] = {"search_answer_profile_id": "bv-sales"}
    mcp.outputs["rag_search"] = search
    output = deepcopy(DEFAULT_OUTPUTS["rag_validate_answer"])
    output.update(
        valid=False,
        checks=["requests", "guide", "guide_steps", "impact"],
        findings=[GUIDE_STEP_ERROR],
        guide_revision=3,
    )
    mcp.outputs["rag_validate_answer"] = output

    run, _ = _searched_run(monkeypatch)

    [call] = mcp.calls_of("rag_validate_answer")
    assert call["arguments"]["requests"] == [
        {"id": "Q1", "text": "更新の期限", "status": "partial"}
    ]
    assert call["arguments"]["gaps"] == ["更新の手数料"]
    assert call["arguments"]["guide"] == {
        "search_answer_profile_id": "bv-sales",
        "guide_id": "guide-1",
        "revision": 3,
        "conditions": {"kind": "年間"},
    }
    # どの段落の手順が誤りかを決められないので、確かめていない手順を出さない（handoff §12）。
    assert run_answer_text(run) == (
        "業務ガイドの手順・影響範囲と照らして確かめられない点があるため、回答の本文は載せていません。"
        "\n\n**確かめられていない点**\n\n"
        "- 手順「申し出る」を、先に行う手順「契約を開く」より前に書いています。"
    )
    assert _validation(run)["withheld"] == {"claims": 0, "findings": 1, "all": True}


def _step(name: str, arguments: dict[str, Any], output: dict[str, Any]) -> RunStep:
    return RunStep(
        run_id="run-1",
        status=StepStatus.COMPLETED,
        tool_call=ToolCall(name=name, arguments=arguments),
        tool_result=ToolResult(name=name, success=True, output=output),
    )


def test_lookup_guide_is_used_only_when_it_gives_steps() -> None:
    """rag_search が業務ガイドを返さなければ、同じ接続の rag_lookup_guides の最上位を使う。"""
    lookup = deepcopy(DEFAULT_OUTPUTS["rag_lookup_guides"])
    lookup["guides"][0]["decision"] = "branch"
    steps = [
        _step(
            "rag__rag_lookup_guides",
            {"query": "q", "search_answer_profile_id": "bv-sales", "conditions": {"kind": "月額"}},
            lookup,
        ),
        # 別の接続の業務ガイドは使わない。
        _step("rag2__rag_lookup_guides", {"search_answer_profile_id": "other"}, lookup),
        _step("rag__rag_search", {"query": "q"}, deepcopy(DEFAULT_OUTPUTS["rag_search"])),
    ]

    inputs = connection_check_inputs(steps, "rag__rag_search")

    assert inputs == {
        "requests": [{"id": "Q1", "text": "契約条項", "status": "addressed"}],
        "guide": {
            "search_answer_profile_id": "bv-sales",
            "guide_id": "guide-1",
            "revision": 1,
            "conditions": {"kind": "月額"},
        },
    }
    # 確かめる質問の判断（手順を書かない回答）では、手順・影響範囲を確かめさせない。
    lookup["guides"][0]["decision"] = "clarify"
    assert "guide" not in connection_check_inputs(steps, "rag__rag_search")
    # プロファイルが分からなければ業務ガイドは渡さない。
    lookup["guides"][0]["decision"] = "answer"
    steps[0].tool_call = ToolCall(name="rag__rag_lookup_guides", arguments={"query": "q"})
    assert "guide" not in connection_check_inputs(steps, "rag__rag_search")


def test_request_errors_keep_the_answer_and_merge_across_connections() -> None:
    claim = {"answer_quote": ANSWER, "status": "supported", "reason": "根拠あり"}
    request_error = {
        "check": "requests",
        "code": "request_missing",
        "severity": "error",
        "message": "要求「更新の手数料」に答えていません。不足も示していません。",
    }
    warning = {**GUIDE_STEP_ERROR, "code": "step_dependency_missing", "severity": "warning"}
    first = {
        "valid": False,
        "status": "completed",
        "claims": [claim],
        "checks": ["requests"],
        "findings": [request_error],
    }
    second = {
        "valid": True,
        "status": "completed",
        "claims": [claim],
        "checks": ["guide_steps", "requests"],
        "findings": [request_error, warning],
    }

    merged = merge_results([first, second])

    # 同じ指摘は 1 つにし、error があれば valid にしない。
    assert merged["findings"] == [request_error, warning]
    assert merged["checks"] == ["guide_steps", "requests"]
    assert merged["valid"] is False
    published, withheld = publish_answer(ANSWER, merged)
    # 要求の error は本文を残し、不足として示す（warning は出さない）。
    assert published == (
        f"{ANSWER}\n\n**確かめられていない点**\n\n"
        "- 要求「更新の手数料」に答えていません。不足も示していません。"
    )
    assert withheld == {"claims": 0, "findings": 1, "all": False}
    # 確かめる主張が無くても error の指摘があれば示す。
    no_claims = {"valid": False, "status": "no_claims", "claims": [], "findings": [request_error]}
    assert publish_answer(ANSWER, no_claims)[0].endswith("不足も示していません。")


def test_withhold_removes_only_the_listed_paragraphs() -> None:
    answer = "会費は無料です。\n無料です。\n手順: 開く。押す。閉じる。"

    # 同じ文を含む別の段落（「会費は無料です。」）は削らない。行の中の段落だけを外す。
    assert (
        withhold_paragraphs(answer, {"無料です。", "押す。"})
        == "会費は無料です。\n手順: 開く。閉じる。"
    )
    # 位置を決められない段落があれば本文を載せない。
    assert withhold_paragraphs(answer, {"どこにも無い。"}) is None


# ---- 主張ではない段落を外さない（#1306） ------------------------------------------------
# #1289 の業務支援の評価（D）で、見出し・出典の行・利用者への質問・「資料に記載が無い」の文まで
# 「確かめられていない点」に移り、回答が読みにくく、拒答が拒答に見えなくなった。

TRIAL_HEADING = "## 試用アカウントの有効期限"
TRIAL_FACT = "試用アカウントの有効期限は、発行日から最長 30 日です。"
TRIAL_STEPS = "**延長の手順**"
TRIAL_STEP_OK = "1. ポータルの「アカウント管理」を開きます。"
# 根拠に無い手順（外す）。
TRIAL_STEP_UNSUPPORTED = "2. 「延長」を押して、有効期限を 90 日まで延長します。"
TRIAL_CITATION = "【portal-operations-manual.pdf, section 2. アカウント管理, page 1】"
TRIAL_SOURCE = "出典: portal-operations-manual.pdf p.2"
TRIAL_ABSENCE = "延長の上限日数については、資料に記載がありません。"
TRIAL_QUESTION = "所属部署の部門長の承認は得ていますか？（はい／いいえ）"
TRIAL_ANSWER = "\n".join(
    [
        TRIAL_HEADING,
        "",
        TRIAL_FACT,
        "",
        TRIAL_STEPS,
        TRIAL_STEP_OK,
        TRIAL_STEP_UNSUPPORTED,
        TRIAL_CITATION,
        TRIAL_SOURCE,
        "",
        TRIAL_ABSENCE,
        TRIAL_QUESTION,
    ]
)


def _claim(quote: str, status: str, reason: str = "") -> dict[str, Any]:
    claim: dict[str, Any] = {"answer_quote": quote, "status": status, "reason": reason}
    if status in {"supported", "contradicted"}:
        claim["chunk_id"] = "chunk-1"
    return claim


def _verdict(*claims: dict[str, Any]) -> dict[str, Any]:
    counts: dict[str, int] = {}
    for claim in claims:
        counts[claim["status"]] = counts.get(claim["status"], 0) + 1
    output: dict[str, Any] = deepcopy(DEFAULT_OUTPUTS["rag_validate_answer"])
    output.update({"valid": False, "status": "completed", "counts": counts, "claims": list(claims)})
    return output


def test_non_claim_passages_are_kept_and_unsupported_steps_are_still_withheld(
    monkeypatch: MonkeyPatch, mcp: FakeProductMcp
) -> None:
    mcp.outputs["rag_validate_answer"] = _verdict(
        _claim(TRIAL_HEADING, "unassessed", "見出しのため監査しない。"),
        _claim(TRIAL_FACT, "supported", "根拠に記載。"),
        _claim(TRIAL_STEPS, "unassessed", "見出し。"),
        _claim(TRIAL_STEP_OK, "supported", "根拠に記載。"),
        _claim(TRIAL_STEP_UNSUPPORTED, "unsupported", "根拠に延長の操作が無い。"),
        _claim(
            TRIAL_CITATION,
            "unassessed",
            "本文を見出しとして監査から除外できません。参照情報のみで、具体的な主張を含まない",
        ),
        _claim(TRIAL_SOURCE, "unassessed", "出典の行。"),
        _claim(TRIAL_ABSENCE, "unsupported", "根拠で確かめられない。"),
        _claim(TRIAL_QUESTION, "unassessed", "This passage is a question to the user."),
    )

    run, _ = _searched_run(monkeypatch, answer=TRIAL_ANSWER)

    answer = run_answer_text(run)
    assert answer is not None
    body, section = answer.split("\n\n**確かめられていない点**\n\n", 1)
    # 見出し・出典・不足の文・質問は残し、根拠に無い手順だけを外す。
    assert body == "\n".join(
        [
            TRIAL_HEADING,
            "",
            TRIAL_FACT,
            "",
            TRIAL_STEPS,
            TRIAL_STEP_OK,
            TRIAL_CITATION,
            TRIAL_SOURCE,
            "",
            TRIAL_ABSENCE,
            TRIAL_QUESTION,
        ]
    )
    assert section.splitlines() == [
        "根拠で確かめられなかった次の内容は、回答に載せていません。",
        f"- 根拠で確かめられない: 「{TRIAL_STEP_UNSUPPORTED}」（根拠に延長の操作が無い。）",
    ]
    content = _validation(run)
    assert content["withheld"] == {"claims": 1, "findings": 0, "all": False}
    # RAG の判定は変えず、主張ではない段落に non_claim を付けて残す（画面が同じ判断をする）。
    marked = {item["answer_quote"]: item.get("non_claim") for item in content["result"]["claims"]}
    assert marked == {
        TRIAL_HEADING: "heading",
        TRIAL_FACT: None,
        TRIAL_STEPS: "heading",
        TRIAL_STEP_OK: None,
        TRIAL_STEP_UNSUPPORTED: None,
        TRIAL_CITATION: "citation",
        TRIAL_SOURCE: "citation",
        TRIAL_ABSENCE: "absence",
        TRIAL_QUESTION: "question",
    }
    assert [item["status"] for item in content["result"]["claims"]].count("unassessed") == 5


def test_refusal_that_the_documents_lack_the_answer_is_kept_as_a_refusal(
    monkeypatch: MonkeyPatch, mcp: FakeProductMcp
) -> None:
    # km-license-fee: 資料に答えが無い質問。拒答の文を外すと
    # 「確かめられた内容はありません」だけになる。
    refusal = "\n".join(
        [
            "**ライセンス費用について**",
            "取得した資料には、ライセンス費用の記載は見つかりませんでした。",
            "資料からは、費用の金額や支払い方法は確かめられません。",
            "契約の担当部署に問い合わせてください。",
        ]
    )
    mcp.outputs["rag_validate_answer"] = _verdict(
        _claim("**ライセンス費用について**", "unassessed", "見出し。"),
        _claim(
            "取得した資料には、ライセンス費用の記載は見つかりませんでした。",
            "unsupported",
            "根拠で確かめられない。",
        ),
        _claim(
            "資料からは、費用の金額や支払い方法は確かめられません。",
            "unsupported",
            "根拠で確かめられない。",
        ),
        _claim("契約の担当部署に問い合わせてください。", "data_confirmation", "問い合わせ先。"),
    )

    run, _ = _searched_run(monkeypatch, answer=refusal)

    assert run_answer_text(run) == refusal
    assert _validation(run)["withheld"] == {"claims": 0, "findings": 0, "all": False}


def test_refusal_mixed_with_an_unsupported_fee_still_withholds_the_fee() -> None:
    answer = (
        "ライセンス費用は、資料に記載がありません。\n"
        "ライセンス費用は月額 1,000 円で、資料には記載がありません。"
    )
    result = _verdict(
        _claim("ライセンス費用は、資料に記載がありません。", "unsupported", "確かめられない。"),
        _claim(
            "ライセンス費用は月額 1,000 円で、資料には記載がありません。",
            "unsupported",
            "金額が根拠に無い。",
        ),
    )

    published, withheld = publish_answer(answer, result)

    # 金額の主張を抱き合わせた文は「記載が無い」の文として扱わず、外す。
    body, section = published.split("\n\n**確かめられていない点**\n\n", 1)
    assert body == "ライセンス費用は、資料に記載がありません。"
    assert "月額 1,000 円" in section
    assert withheld == {"claims": 1, "findings": 0, "all": False}


def test_headings_and_citations_left_alone_are_not_published() -> None:
    # 手順をすべて外した後に、見出しと出典の行だけを本文として出さない。
    step = "1. 管理画面で「削除」を押します。"
    answer = f"## アカウントの削除の手順\n{step}\n【admin-guide.pdf p.12】"
    result = _verdict(
        _claim("## アカウントの削除の手順", "unassessed", "見出し。"),
        _claim(step, "unsupported", "根拠に無い。"),
        _claim("【admin-guide.pdf p.12】", "unassessed", "出典。"),
    )

    published, withheld = publish_answer(answer, result)

    body, _section = published.split("\n\n**確かめられていない点**\n\n", 1)
    assert body == "根拠で確かめられた内容はありませんでした。"
    assert withheld == {"claims": 1, "findings": 0, "all": True}
    # 段落を外した行に残った出典、中身をすべて外した節の見出しも消す。
    kept = withhold_paragraphs(
        f"{TRIAL_FACT}\n\n## 削除の手順\n{step}【admin-guide.pdf p.12】"
        "\n\n## 注意\n- 元に戻せません。",
        {step},
    )
    assert kept == f"{TRIAL_FACT}\n\n## 注意\n- 元に戻せません。"


def test_clarification_only_answer_without_evidence_has_no_notice(
    monkeypatch: MonkeyPatch, mcp: FakeProductMcp
) -> None:
    # cr-delete-account: 根拠を使わない確認の質問だけの回答に「確かめていません」を足さない。
    question = "\n".join(
        [
            "アカウントの削除の前に、次を確認させてください。",
            "所属部署の部門長の承認は得ていますか？（はい／いいえ）",
            "削除するのはどちらのアカウントですか？",
            "- 自分のアカウント",
            "- 部下のアカウント",
        ]
    )
    _script(monkeypatch, [assistant_message(question)])
    created = runtime_repository.create_builtin_run(
        RunCreateRequest(goal="アカウントを削除したい", agent_id=AGENT_ID),
        created_by_user_uuid=USER_UUID,
    )

    anyio.run(builtin_runtime.execute_run, created.id)

    run = runtime_repository.get_run(created.id)
    assert mcp.calls_of("rag_validate_answer") == []
    assert run_answer_text(run) == question
    content = _validation(run)
    assert (content["status"], content["reason"]) == ("skipped", "clarification_only")


@pytest.mark.parametrize(
    ("text", "kind"),
    [
        ("## 確認手順", "heading"),
        ("**手順**", "heading"),
        ("確認が必要な点：", "heading"),
        ("【portal-operations-manual.pdf, section 2. アカウント管理, page 1】", "citation"),
        ("- 出典：admin-guide.pdf 第3章", "citation"),
        ("（参照: security-policy.docx p.4）", "citation"),
        ("[運用マニュアル](https://example.com/manual.pdf)", "citation"),
        ("所属部署の部門長の承認は得ていますか？（はい／いいえ）", "question"),
        ("付与先の部署名を教えてください。", "question"),
        ("ライセンス費用については、資料に記載がありません。", "absence"),
        ("取得した資料からは、今の設定値は確かめられません。", "absence"),
        # 主張・操作の手順（外す対象のまま）。
        ("**30 日以内に申請してください**", None),
        ("## 管理画面で「削除」を押します", None),
        ("根拠: 契約書の第 5 条により、30 日前までに申し出る必要があります", None),
        ("削除できますが、よろしいですか？", None),
        ("ライセンス費用は月額 1,000 円で、資料には記載がありません。", None),
        ("資料には記載がありませんが、設定画面で削除できます。", None),
        ("管理画面で「削除」を押してください。", None),
        ("パスワードは 90 日ごとに変更します。", None),
    ],
    ids=[f"case{index}" for index in range(19)],
)
def test_passage_kind_is_conservative(text: str, kind: str | None) -> None:
    assert non_claim_passages(text).get(text) == kind


def test_unverified_section_keeps_points_but_not_operation_steps() -> None:
    answer = "\n".join(
        [
            "**確かめられていない点**",
            "- 承認の要否は、資料に記載がありません。",
            "- 復元できる期限は確かめられていません",
            "- 管理画面で「削除」を押してください。",
        ]
    )

    kinds = non_claim_passages(answer)

    assert kinds == {
        "**確かめられていない点**": "unverified_section",
        "- 承認の要否は、資料に記載がありません。": "absence",
        "- 復元できる期限は確かめられていません": "unverified_section",
    }
    # 同じ文が別の場所で主張なら、主張として扱う。
    assert "パスワードは 90 日です。" not in non_claim_passages(
        "**確かめられていない点**\nパスワードは 90 日です。\n\nパスワードは 90 日です。"
    )


EVIDENCE_REF = (
    "【証拠 ID: 03ac895f1d744a2b9eab4dd569eb68fc:"
    "b245283c90ea80e5d8aadd3283e6027f148113c0aa42c59bbf12923cc59ae01c:1】"
)


@pytest.mark.parametrize(
    ("text", "kind"),
    [
        # 出典の位置のラベル（#1317。da-trial-account-expiry）。
        (
            "- セクション: 「サンプル業務ポータル 運用手順書 第3版」 → "
            "**2. 検証用アカウントの登録**",
            "citation",
        ),
        ("- ページ: 1  ", "citation"),
        ("- **根拠**：regional-report-guide-v2.pdf、2. 出力の手順（ページ1）", "citation"),
        # 表のセルに分かれた出典（da-trial-account-setup）。
        (f"| 「1. 前提」 （ページ1）{EVIDENCE_REF} |", "citation"),
        (
            "（*portal-operations-manual.pdf*、セクション「3. アクセス権限の付与」）【証拠1】",
            "citation",
        ),
        ("- 「サンプル業務ポータル 運用手順書 第3版」 6. アカウントの削除", "citation"),
        # 表の区切りの行・区切り線。
        ("|------|----------|------|", "table"),
        ("| :--- | ---: |", "table"),
        ("---", "table"),
        # 拒答の文（km-license-fee・km-password-policy）と、検索結果に該当が無かった文。
        ("推測で金額を示すことはできません。", "absence"),
        (
            "そのため、パスワードは何文字以上にすべきかについて根拠のある回答はできません。",
            "absence",
        ),
        (
            "- rag__rag_lookup_guides の検索結果でも、今回の質問に該当する業務ガイドは"
            "返ってきませんでした。",
            "absence",
        ),
        # 主張のまま（外す対象）。
        ("- ページ: 管理画面で「削除」を押してください", None),
        ("- 抜粋: 「…有効期限は最長 30 日です。」", None),
        ("- 「有効期限は最長 30 日です。」", None),
        ("- 「検証用」", None),
        ("| 締め日 | 毎月 10 日 |", None),
        ("この機能では、アカウントを削除することはできません。", None),
        ("利用者を検索してもヒットしません。", None),
        ("年間ライセンス費用は 120,000 円のため、それ以上の金額はお示しできません。", None),
        (f"| 2. 登録 | 1. 「利用者」を開く 2. 「追加」を押す | {EVIDENCE_REF} |", None),
    ],
    ids=[f"case{index}" for index in range(21)],
)
def test_citations_tables_and_refusals_are_not_claims(text: str, kind: str | None) -> None:
    # #1317: #1306 の判定から漏れて unassessed で外れ、回答の対応を誤らせていた段落。
    assert non_claim_passages(text).get(text.strip()) == kind


TABLE_HEADER = "| 手順 | 操作内容 | 根拠 |"
TABLE_SEPARATOR = "|------|----------|------|"
TABLE_ROW = "| 1. 前提確認 | 「ポータル管理者」ロールが要ります | 「1. 前提」（ページ1） |"


def test_table_header_is_structure_only_before_a_separator() -> None:
    answer = f"検証用アカウントの登録の手順です。\n\n{TABLE_HEADER}\n{TABLE_SEPARATOR}\n{TABLE_ROW}"
    kinds = non_claim_passages(answer)
    assert kinds[TABLE_HEADER] == "table"
    assert kinds[TABLE_SEPARATOR] == "table"
    # 表の本文の行は主張のまま。区切りの行が後ろに無い行は見出しにしない。
    assert TABLE_ROW not in kinds
    assert TABLE_HEADER not in non_claim_passages(f"{TABLE_HEADER}\n{TABLE_ROW}")
    # 本文の行をすべて外したら、表の見出しと区切りだけを本文として出さない。
    lead = "検証用アカウントの登録の手順です。"
    assert withhold_paragraphs(answer, {lead, TABLE_ROW}) == ""
    assert (
        withhold_paragraphs(answer, {TABLE_ROW}) == f"{lead}\n\n{TABLE_HEADER}\n{TABLE_SEPARATOR}"
    )


def test_table_and_location_lines_are_not_withheld() -> None:
    # da-trial-account-setup: 表の見出し・区切り・出典のセルが unassessed で外れ、
    # 本文から消えていた。
    citation = f"| 「1. 前提」 （ページ1）{EVIDENCE_REF} |"
    row = "| 1. 前提確認 | 本操作は「ポータル管理者」ロールを持つ利用者のみが実施可能です。"
    answer = "\n".join([TABLE_HEADER, TABLE_SEPARATOR, f"{row} {citation}"])
    result = _verdict(
        _claim(TABLE_HEADER, "unassessed", "テーブルのヘッダー。"),
        _claim(TABLE_SEPARATOR, "unassessed", "テーブルの区切り線。"),
        _claim(row, "supported", "根拠に記載。"),
        _claim(citation, "unassessed", "引用情報の記載。"),
    )

    published, withheld = publish_answer(answer, result)

    assert published == answer
    assert withheld == {"claims": 0, "findings": 0, "all": False}


def _answer_outcome(run: RunState) -> dict[str, Any]:
    [artifact] = [item for item in run.artifacts if item.kind == "answer"]
    outcome = artifact.content["outcome"]
    assert isinstance(outcome, dict)
    return outcome


def test_answer_artifact_records_the_outcome_after_the_validation(
    monkeypatch: MonkeyPatch, mcp: FakeProductMcp
) -> None:
    # 回答の対応（#1305）は、最終の検証の後に Control Plane が決めて成果物 answer に残す。
    run, _ = _searched_run(monkeypatch)

    assert _answer_outcome(run) == {
        "schema_version": 1,
        "value": "answered",
        "basis": "rag_search",
        "rag_outcome": "answered",
        "signals": [],
    }


def test_answer_artifact_records_a_clarification_and_a_refusal(
    monkeypatch: MonkeyPatch, mcp: FakeProductMcp
) -> None:
    del mcp
    _script(
        monkeypatch,
        [assistant_message("確認させてください。\n付与先は個別の利用者ですか、グループですか？")],
    )
    clarified = runtime_repository.create_builtin_run(
        RunCreateRequest(goal="権限を付けたい", agent_id=AGENT_ID), created_by_user_uuid=USER_UUID
    )
    anyio.run(builtin_runtime.execute_run, clarified.id)
    outcome = _answer_outcome(runtime_repository.get_run(clarified.id))
    assert (outcome["value"], outcome["basis"]) == ("needs_clarification", "clarification_question")

    # 根拠を使わない回答は、注記を足す前のモデルの回答の段落で決める。
    _script(monkeypatch, [assistant_message("ライセンス費用は資料に記載がありません。")])
    refused = runtime_repository.create_builtin_run(
        RunCreateRequest(goal="ライセンス費用は？", agent_id=AGENT_ID),
        created_by_user_uuid=USER_UUID,
    )
    anyio.run(builtin_runtime.execute_run, refused.id)
    outcome = _answer_outcome(runtime_repository.get_run(refused.id))
    assert (outcome["value"], outcome["basis"]) == ("insufficient_evidence", "answer_passages")


def test_answer_is_saved_without_an_outcome_when_it_cannot_be_decided(
    monkeypatch: MonkeyPatch, mcp: FakeProductMcp
) -> None:
    def broken(*_args: Any, **_kwargs: Any) -> Any:
        raise RuntimeError("boom")

    monkeypatch.setattr(builtin_runtime, "answer_outcome", broken)
    run, _ = _searched_run(monkeypatch)

    assert run.status == RunStatus.COMPLETED
    [artifact] = [item for item in run.artifacts if item.kind == "answer"]
    assert artifact.content == {"text": ANSWER}


def test_outcome_is_recorded_when_the_validation_is_disabled(
    monkeypatch: MonkeyPatch, mcp: FakeProductMcp
) -> None:
    monkeypatch.setattr(get_settings(), "agent_final_validation_enabled", False)
    mcp.outputs["rag_search"] = {
        **deepcopy(DEFAULT_OUTPUTS["rag_search"]),
        "outcome": "needs_environment_data",
    }
    run, _ = _searched_run(monkeypatch)

    assert mcp.calls_of("rag_validate_answer") == []
    outcome = _answer_outcome(run)
    assert (outcome["value"], outcome["rag_outcome"]) == (
        "needs_environment_data",
        "needs_environment_data",
    )
