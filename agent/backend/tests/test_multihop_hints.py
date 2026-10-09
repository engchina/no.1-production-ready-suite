"""多段の質問の案内（#1345）の決定論テスト。

RAG は部品（根拠・構成・順に読む）を出し、多段の組み立ては Agent が行う。Control Plane は、根拠を
集める・読むツールの本文に同じ文書の別の箇所への参照（「第 4 章を参照」）があれば読む先を、RAG の
予算があればこの Run で残る検索の回数を、モデルへの結果に足す（記録する step の結果は RAG のまま）。
SDK の `ScriptedModel` と契約どおりの fake の RAG の MCP（`mcp_support`）で確かめる。
"""

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
from app.features.agent.builtin_runtime import ModelTarget
from app.features.agent.runtime import AgentProfile, RunCreateRequest, RunState, runtime_repository
from app.features.agent.skills import (
    AgentSkillDefinition,
    SkillMcpRequirement,
    skill_registry,
)
from app.features.agent.support_task import (
    MAX_REFERENCES,
    REFERENCE_READ_HINT,
    text_references,
)
from app.settings import Settings, get_settings

SKILL_ID = "test1345-skill"
AGENT_ID = "test1345-agent"
USER_UUID = "11111111-2222-3333-4444-555555555555"
GOAL = "HRM の変更の承認者の代理は誰ですか？"


@pytest.fixture
def mcp(monkeypatch: MonkeyPatch) -> Iterator[FakeProductMcp]:
    fake = fake_product_mcp(monkeypatch)
    # 最終の検証（#1246）はこのテストの対象外。
    monkeypatch.setattr(get_settings(), "agent_final_validation_enabled", False)
    skill_registry.upsert_custom(
        AgentSkillDefinition(
            id=SKILL_ID,
            name=SKILL_ID,
            instructions="段ごとに rag_retrieve_evidence で調べる。",
            mcp_requirements=[
                SkillMcpRequirement(
                    server_id="rag",
                    tool_names=["rag_retrieve_evidence", "rag_outline", "rag_read_document"],
                )
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


def _run() -> RunState:
    run = runtime_repository.create_builtin_run(
        RunCreateRequest(goal=GOAL, agent_id=AGENT_ID), created_by_user_uuid=USER_UUID
    )
    anyio.run(builtin_runtime.execute_run, run.id)
    return runtime_repository.get_run(run.id)


def _tool_output(model: ScriptedModel, call_index: int, call_id: str) -> dict[str, Any]:
    """モデルの `call_index` 回目の入力にある、ツールの結果（モデルが見たもの）。"""
    for item in model.calls[call_index].input:
        if isinstance(item, dict) and item.get("call_id") == call_id and "output" in item:
            loaded: dict[str, Any] = json.loads(str(item["output"]))
            return loaded
    raise AssertionError(f"{call_id} の結果がモデルの入力にありません")


def _evidence_with(excerpt: str) -> dict[str, Any]:
    output: dict[str, Any] = deepcopy(DEFAULT_OUTPUTS["rag_retrieve_evidence"])
    output["evidence"][0].update(document_id="doc-org", file_name="組織規程.pdf", excerpt=excerpt)
    return output


# ---- 参照の検出（決定的） ---------------------------------------------------------------------


def test_references_are_found_in_evidence_and_read_text() -> None:
    evidence = {
        "evidence": [
            {
                "document_id": "doc-org",
                "file_name": "組織規程.pdf",
                # 全角の数字は NFKC で同じに扱い、空白だけが違う同じ参照（第 4 章も）は 1 つにする。
                "excerpt": "代理は第４章を参照してください。期限は第 2 章 を参照。第 4 章も参照。",
            },
            {"document_id": "doc-ledger", "excerpt": "詳しくは別表 2 参照のこと。"},
            {"document_id": "", "excerpt": "第 9 章を参照"},
        ]
    }
    assert text_references("rag_retrieve_evidence", evidence) == [
        {
            "document_id": "doc-org",
            "file_name": "組織規程.pdf",
            "reference": "第4章",
            "how_to_read": REFERENCE_READ_HINT,
        },
        {
            "document_id": "doc-org",
            "file_name": "組織規程.pdf",
            "reference": "第 2 章",
            "how_to_read": REFERENCE_READ_HINT,
        },
        {
            "document_id": "doc-ledger",
            "file_name": None,
            "reference": "別表 2",
            "how_to_read": REFERENCE_READ_HINT,
        },
    ]

    # 読み取りのツールは本文（rag_read_source は親の本文も）から探す。
    read_source = {"document_id": "doc-1", "text": "本文", "parent_text": "付録 A を参照"}
    assert [item["reference"] for item in text_references("rag_read_source", read_source)] == [
        "付録 A"
    ]
    read_document = {"document_id": "doc-1", "text": "--- p.2 ---\n第3条を参照する。"}
    assert [item["reference"] for item in text_references("rag_read_document", read_document)] == [
        "第3条"
    ]


def test_references_ignore_mentions_without_a_reference_and_other_tools() -> None:
    # 「参照」を伴わない章の言及・ほかのツールの出力は案内しない。
    assert (
        text_references(
            "rag_retrieve_evidence",
            {"evidence": [{"document_id": "d", "excerpt": "第 4 章では代理を定める。"}]},
        )
        == []
    )
    assert (
        text_references(
            "rag_search", {"evidence": [{"document_id": "d", "excerpt": "第 4 章を参照"}]}
        )
        == []
    )
    assert text_references("rag_read_document", {"text": "第 4 章を参照"}) == []

    many = "".join(f"第 {index} 章を参照。" for index in range(1, 10))
    found = text_references("rag_read_document", {"document_id": "d", "text": many})
    assert len(found) == MAX_REFERENCES


# ---- Run ----------------------------------------------------------------------------------------


def test_evidence_with_a_reference_gets_where_to_read_and_the_remaining_budget(
    monkeypatch: MonkeyPatch, mcp: FakeProductMcp
) -> None:
    mcp.outputs["rag_retrieve_evidence"] = _evidence_with("代理は第 4 章を参照してください。")
    model = _script(
        monkeypatch,
        [
            function_call(
                "rag__rag_retrieve_evidence", {"query": "情報システム部 承認者"}, call_id="call-1"
            )
        ],
        [function_call("rag__rag_read_document", {"document_id": "doc-org"}, call_id="call-2")],
        [assistant_message("代理は管理本部長です。")],
    )
    run = _run()

    limit = int(get_settings().agent_max_rag_calls_per_run)
    seen = _tool_output(model, 1, "call-1")
    assert seen["references"] == [
        {
            "document_id": "doc-org",
            "file_name": "組織規程.pdf",
            "reference": "第 4 章",
            "how_to_read": REFERENCE_READ_HINT,
        }
    ]
    assert seen["rag_calls_remaining"] == limit - 1
    # 読み取りは RAG の上限に数えないので、残りは変わらない。
    # 参照の無い本文には references を足さない。
    read = _tool_output(model, 2, "call-2")
    assert read["rag_calls_remaining"] == limit - 1
    assert "references" not in read

    # 記録する step の結果は RAG の結果のまま。
    recorded = [step.tool_result.output for step in run.steps if step.tool_result is not None]
    assert all("references" not in (output or {}) for output in recorded)
    assert all("rag_calls_remaining" not in (output or {}) for output in recorded)


def test_no_budget_means_no_remaining_count(monkeypatch: MonkeyPatch, mcp: FakeProductMcp) -> None:
    monkeypatch.setattr(get_settings(), "agent_max_rag_calls_per_run", 0)
    model = _script(
        monkeypatch,
        [function_call("rag__rag_retrieve_evidence", {"query": "承認者"}, call_id="call-1")],
        [assistant_message("承認者は部長です。")],
    )
    _run()
    seen = _tool_output(model, 1, "call-1")
    assert "rag_calls_remaining" not in seen
    assert "references" not in seen


def test_default_rag_budget_covers_five_hops_and_a_rephrase() -> None:
    # 5 段の橋渡し（段ごとに 1 回の rag_retrieve_evidence）と 1 回の言い換え（#1345）。
    assert Settings.model_fields["agent_max_rag_calls_per_run"].default == 6
