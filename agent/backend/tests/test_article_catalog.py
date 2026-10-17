"""条の目録で根拠の条を確かめる案内（#2254）。

スキル article-catalog-lookup を選んだ業務 Agent では、`rag_search` / `rag_retrieve_evidence` の
結果に、条で構成された文書の目録を読む案内（`article_catalog`）を足す。決定論のテスト（モデルは
`ScriptedModel`、RAG は契約どおりの fake の MCP）。
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
from app.features.agent.runtime import (
    AgentProfile,
    RunCreateRequest,
    RunState,
    RunStep,
    runtime_repository,
)
from app.features.agent.skills import ARTICLE_CATALOG_LOOKUP_SKILL_ID
from app.features.agent.support_task import (
    ARTICLE_CATALOG_HINT,
    ARTICLE_CATALOG_MAX_DOCUMENTS,
    article_catalog_note,
)
from app.features.agent.tools import ToolCall
from app.settings import get_settings

AGENT_ID = "test2254-agent"
PLAIN_AGENT_ID = "test2254-plain"
USER_UUID = "11111111-2222-3333-4444-777777777777"
GOAL = "事例の要件を条文で確かめてください。"


def _evidence(document_id: str, title: str) -> dict[str, Any]:
    item: dict[str, Any] = deepcopy(DEFAULT_OUTPUTS["rag_retrieve_evidence"]["evidence"][0])
    item.update(document_id=document_id, file_name=f"{document_id}.json")
    item["section"] = {**item["section"], "title": title}
    return item


def _outline_step(document_id: str) -> RunStep:
    return RunStep(
        run_id="run-x",
        status="completed",
        tool_call=ToolCall(
            name="rag__rag_outline", arguments={"document_id": document_id, "format": "compact"}
        ),
    )


def test_note_lists_article_documents_in_evidence_order() -> None:
    output = {
        "evidence": [
            _evidence("doc-a", "第九条（資格喪失の時期）"),
            _evidence("doc-b", "第2章 申請"),
            _evidence("doc-a", "第十条"),
            _evidence("doc-c", "第十一条の二"),
            _evidence("doc-d", "第3条"),
            _evidence("doc-e", "第4条"),
        ]
    }
    note = article_catalog_note("rag_retrieve_evidence", output, [])
    assert note is not None
    assert [item["document_id"] for item in note["documents"]] == ["doc-a", "doc-c", "doc-d"]
    assert len(note["documents"]) == ARTICLE_CATALOG_MAX_DOCUMENTS
    assert note["next_step"] == ARTICLE_CATALOG_HINT
    # 目録を読んだ文書は外す（次の文書を出す）。
    after = article_catalog_note("rag_search", output, [_outline_step("doc-a")])
    assert after is not None
    assert [item["document_id"] for item in after["documents"]] == ["doc-c", "doc-d", "doc-e"]
    # 条の文書が無い・根拠を集めるツールでないときは案内しない。
    assert (
        article_catalog_note("rag_search", {"evidence": [_evidence("doc-b", "2.1 申請")]}, [])
        is None
    )
    assert article_catalog_note("rag_read_section", output, []) is None


@pytest.fixture
def mcp(monkeypatch: MonkeyPatch) -> Iterator[FakeProductMcp]:
    fake = fake_product_mcp(monkeypatch)
    monkeypatch.setattr(get_settings(), "agent_final_validation_enabled", False)
    runtime_repository.create_agent(
        AgentProfile(id=AGENT_ID, name=AGENT_ID, skill_ids=[ARTICLE_CATALOG_LOOKUP_SKILL_ID])
    )
    runtime_repository.create_agent(
        AgentProfile(id=PLAIN_AGENT_ID, name=PLAIN_AGENT_ID, skill_ids=["business-rag-research"])
    )
    try:
        yield fake
    finally:
        repository: Any = runtime_repository
        with repository._lock:  # noqa: SLF001 - テストの後始末
            for run_id in [
                run_id
                for run_id, run in repository._runs.items()
                if run.agent_id in {AGENT_ID, PLAIN_AGENT_ID}
            ]:
                repository._runs.pop(run_id)
        for agent_id in (AGENT_ID, PLAIN_AGENT_ID):
            with contextlib.suppress(KeyError, ValueError):
                runtime_repository.delete_agent(agent_id)


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


def _run(agent_id: str) -> RunState:
    run = runtime_repository.create_builtin_run(
        RunCreateRequest(goal=GOAL, agent_id=agent_id), created_by_user_uuid=USER_UUID
    )
    anyio.run(builtin_runtime.execute_run, run.id)
    return runtime_repository.get_run(run.id)


def _tool_output(model: ScriptedModel, call_index: int, call_id: str) -> dict[str, Any]:
    for item in model.calls[call_index].input:
        if isinstance(item, dict) and item.get("call_id") == call_id and "output" in item:
            loaded: dict[str, Any] = json.loads(str(item["output"]))
            return loaded
    raise AssertionError(f"{call_id} の結果がモデルの入力にありません")


@pytest.mark.parametrize(
    ("agent_id", "expected"), [(AGENT_ID, True), (PLAIN_AGENT_ID, False)], ids=["skill", "plain"]
)
def test_evidence_result_points_to_the_article_catalog_only_with_the_skill(
    monkeypatch: MonkeyPatch, mcp: FakeProductMcp, agent_id: str, expected: bool
) -> None:
    """スキルを選んだ Agent だけ、根拠の結果に条の目録の案内を足す（step の記録は RAG のまま）。"""
    del mcp
    model = _script(
        monkeypatch,
        [function_call("rag__rag_retrieve_evidence", {"query": "要件"}, call_id="call-1")],
        [assistant_message("第5条によります。")],
    )
    run = _run(agent_id)
    seen = _tool_output(model, 1, "call-1")
    if expected:
        assert seen["article_catalog"]["documents"] == [
            {"document_id": "doc-1", "file_name": "契約書.pdf"}
        ]
        assert seen["article_catalog"]["next_step"] == ARTICLE_CATALOG_HINT
    else:
        assert "article_catalog" not in seen
    outputs = [step.tool_result.output for step in run.steps if step.tool_result is not None]
    assert all("article_catalog" not in (output or {}) for output in outputs)
