"""回答の最終の検証（#1246）の決定論テスト。

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
from app.features.agent.builtin_runtime import ModelTarget
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
from app.settings import get_settings

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


def test_invalid_answer_shows_unverified_points(
    monkeypatch: MonkeyPatch, mcp: FakeProductMcp
) -> None:
    long_quote = "手数料は 5,000 円です。" + "詳しい条件は営業担当に確認してください。" * 5
    mcp.outputs["rag_validate_answer"] = {
        "valid": False,
        "status": "completed",
        "counts": {"supported": 1, "unsupported": 1, "contradicted": 1},
        "claims": [
            {"answer_quote": ANSWER, "status": "supported", "chunk_id": "chunk-1", "reason": "ok"},
            {"answer_quote": long_quote, "status": "unsupported", "reason": "根拠に金額が無い。"},
            {
                "answer_quote": "60 日前まで",
                "status": "contradicted",
                "chunk_id": "chunk-1",
                "reason": "根拠は 30 日前。",
            },
        ],
        "missing_evidence": [],
        "stale_evidence": [{"document_id": "doc-1", "chunk_id": "chunk-1"}],
        "evidence_truncated": False,
    }

    run, _ = _searched_run(monkeypatch, answer=f"{ANSWER}\n\n{long_quote}")

    answer = run_answer_text(run)
    assert answer is not None
    # 回答は作り直さず、末尾に確かめられていない点を足す。
    assert answer.startswith(f"{ANSWER}\n\n{long_quote}")
    section = answer.split("**確かめられていない点**", 1)[1]
    lines = [line for line in section.strip().splitlines() if line]
    assert lines[0].startswith("- 根拠で確かめられない: 「手数料は 5,000 円です。")
    quote = lines[0].split("「", 1)[1].split("」", 1)[0]
    assert len(quote) == 80 and quote.endswith("…")
    assert lines[0].endswith("（根拠に金額が無い。）")
    assert lines[1] == "- 根拠と矛盾: 「60 日前まで」（根拠は 30 日前。）"
    assert lines[2].startswith("- 根拠の 1 件は文書の古い版です。")
    assert len(lines) == 3
    content = _validation(run)
    assert (content["status"], content["valid"]) == ("completed", False)


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
    assert run_answer_text(run) == f"{ANSWER}\n\nこの回答は検証できませんでした。"
    content = _validation(run)
    assert content["status"] == "failed"
    assert content["reason"] == "mcp.tool_error"
    assert content["message"] == "一時的に利用できません。"
    assert run.steps[-1].status == "failed"


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


def test_run_without_rag_evidence_is_skipped(monkeypatch: MonkeyPatch, mcp: FakeProductMcp) -> None:
    _script(monkeypatch, [assistant_message("資料を調べずに答えました。")])
    created = runtime_repository.create_builtin_run(
        RunCreateRequest(goal="こんにちは", agent_id=AGENT_ID), created_by_user_uuid=USER_UUID
    )

    anyio.run(builtin_runtime.execute_run, created.id)

    run = runtime_repository.get_run(created.id)
    assert mcp.calls_of("rag_validate_answer") == []
    assert run_answer_text(run) == "資料を調べずに答えました。"
    content = _validation(run)
    assert (content["status"], content["reason"]) == ("skipped", "no_rag_evidence")


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


def test_rag_without_the_validator_is_skipped_with_a_reason(
    monkeypatch: MonkeyPatch, mcp: FakeProductMcp
) -> None:
    rag = mcp.servers["rag"]
    mcp.servers["rag"] = McpServer(
        name=rag.name,
        version="test",
        tools=[tool for tool in rag.tools.values() if tool.name != "rag_validate_answer"],
    )

    run, _ = _searched_run(monkeypatch)

    assert run_answer_text(run) == ANSWER
    content = _validation(run)
    assert (content["status"], content["reason"]) == ("skipped", "validator_unavailable")
