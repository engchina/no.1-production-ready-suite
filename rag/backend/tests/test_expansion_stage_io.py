"""拡張の実入力と返却候補を、終了後の保存形式で検証する。"""

import json
from typing import Any, cast

import pytest
from pr_backend_core.config.tracing import TracingSettings
from pr_backend_core.observability.call_policy import call_content_policy
from pr_backend_core.observability.tracing import (
    configure_tracing,
    operation_trace,
    shutdown_tracing,
)
from rag_engine.resources.tracing import bind_trace_observer
from rag_engine.retrieval.search_stages import (
    DERIVED_SOURCE_STAGE,
    ENTITY_EXPANSION_STAGE,
    GRAPH_EXPANSION_STAGE,
    REFERENCE_EXPANSION_STAGE,
)

from app.config import Settings
from app.rag import answer_engine as module
from app.rag.answer_engine import AnswerEngine, _SearchState
from app.rag.pipeline import _observe_engine_call
from app.schemas.search import RetrievedChunk, SearchRequest
from tests.test_answer_engine import FakeGenAi, FakeOracle


def row(uid: str) -> RetrievedChunk:
    return RetrievedChunk(
        document_id="doc-a",
        chunk_id=uid,
        text=f"合成本文 {uid}",
        score=0.9,
        metadata={"chunk_set_id": "cs-a", "recipe_id": "recipe-a", "page_start": 1},
    )


def engine(oracle: FakeOracle) -> AnswerEngine:
    return AnswerEngine(
        Settings(
            rag_entity_expansion_enabled=True,
            rag_graph_expansion_enabled=True,
            rag_reference_expansion_enabled=True,
        ),
        oracle=cast(Any, oracle),
        genai=cast(Any, FakeGenAi()),
        entity_store=cast(Any, object()),
        graph_store=cast(Any, object()),
    )


@pytest.fixture(autouse=True)
def tracing() -> Any:
    configure_tracing(TracingSettings(), service_name="synthetic")
    try:
        yield
    finally:
        shutdown_tracing()


def calls(summary: Any) -> dict[str, Any]:
    # 保存後も本文と実際の工程名の対応を保つ。
    from pr_backend_core.observability.tracing import TraceSummary

    restored = TraceSummary.model_validate_json(summary.model_dump_json())
    return {
        str(item.attributes["pr.stage"]): item.display.details.call
        for item in restored.spans
        if item.operation_name == "rag.engine.stage" and item.display and item.display.details
    }


@pytest.mark.parametrize("enabled", [True, False])
@pytest.mark.parametrize("outcome", ["empty", "failed"])
async def test_expansion_empty_and_failure_keep_actual_scope(
    monkeypatch: pytest.MonkeyPatch,
    enabled: bool,
    outcome: str,
) -> None:
    seen: list[dict[str, Any]] = []
    if not enabled:

        def snapshot_forbidden(*args: Any) -> Any:
            raise AssertionError("記録 OFF では本文の snapshot を構築しない")

        monkeypatch.setattr(module, "_trace_chunks", snapshot_forbidden)

    async def plan(*args: Any, **kwargs: Any) -> list[Any]:
        seen.append(kwargs)
        if outcome == "failed":
            raise RuntimeError("private-error-body")
        return []

    monkeypatch.setattr(module, "plan_entity_expansion", plan)
    monkeypatch.setattr(module, "plan_graph_expansion", plan)
    oracle = FakeOracle()
    oracle.chunks = [row("seed-a")]
    state = _SearchState()
    request = SearchRequest(query="合成の質問", filters={"knowledge_base_id": "kb-a"})
    with (
        call_content_policy(lambda: enabled),
        operation_trace("rag.search", operation_id="synthetic") as summary,
        bind_trace_observer(_observe_engine_call),
    ):
        result = await engine(oracle)._search(
            request, state, retrieval_queries=[request.query], defer_context=True
        )
    assert seen and result.child_chunks
    captured = calls(summary)
    for name in [ENTITY_EXPANSION_STAGE, GRAPH_EXPANSION_STAGE, REFERENCE_EXPANSION_STAGE]:
        call = captured[name]
        assert call.capture_enabled is enabled
        assert "private-error-body" not in call.model_dump_json()
        if enabled:
            data = json.loads(call.input.text)
            assert data["範囲"]["knowledge_base_id"] == "kb-a"
            assert json.loads(call.output.text)["返した固有 Chunk 数"] == 0
            assert call.visibility == "system_admin"
        else:
            assert call.input.text == call.output.text == ""
    if enabled:
        output = json.loads(captured[GRAPH_EXPANSION_STAGE].output.text)
        assert bool(output["回答内の縮退記録（累計）"]) is (outcome == "failed")
        assert (
            json.loads(captured[ENTITY_EXPANSION_STAGE].input.text)["起点"][0]["text"]
            == "合成本文 seed-a"
        )


async def test_empty_anchors_still_record_an_executed_question_expansion(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from types import SimpleNamespace

    async def entity(*args: Any, **kwargs: Any) -> list[Any]:
        assert kwargs["seed_chunks"] == []
        return [SimpleNamespace(chunk=row("entity-a"), info={})]

    async def graph(*args: Any, **kwargs: Any) -> list[Any]:
        return []

    monkeypatch.setattr(module, "plan_entity_expansion", entity)
    monkeypatch.setattr(module, "plan_graph_expansion", graph)
    oracle = FakeOracle()
    oracle.chunks = []
    with (
        call_content_policy(lambda: True),
        operation_trace("rag.search", operation_id="synthetic") as summary,
        bind_trace_observer(_observe_engine_call),
    ):
        result = await engine(oracle)._search(
            SearchRequest(query="q"), _SearchState(), retrieval_queries=["q"], defer_context=True
        )
    captured = calls(summary)
    output = json.loads(captured[ENTITY_EXPANSION_STAGE].output.text)
    assert output["返した固有 Chunk 数"] == 1
    assert output["返した候補"][0]["chunk_id"] == "entity-a"
    assert [item.chunk_uid for item in result.child_chunks] == ["entity-a"]


async def test_derived_source_preserves_mapping_and_transferred_score(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def sources(*args: Any, **kwargs: Any) -> list[RetrievedChunk]:
        return [row("original-a")]

    monkeypatch.setattr(module, "derived_source_chunks", sources)
    monkeypatch.setattr(module, "is_derived_chunk", lambda chunk: chunk.chunk_id == "derived-a")
    monkeypatch.setattr(module, "derived_kind", lambda metadata: "section_summary")
    scores = {"derived-a": 0.4}
    with (
        call_content_policy(lambda: True),
        operation_trace("rag.search", operation_id="synthetic") as summary,
        bind_trace_observer(_observe_engine_call),
    ):
        result = await engine(FakeOracle())._original_anchors(
            _SearchState(), [row("derived-a")], scores, filters={"knowledge_base_id": "kb-a"}
        )
    call = calls(summary)[DERIVED_SOURCE_STAGE]
    assert json.loads(call.input.text)["融合スコア"] == {"derived-a": 0.4}
    output = json.loads(call.output.text)
    assert output["派生候補と原文の対応"][0]["source_chunk_ids"] == ["original-a"]
    assert output["置換後の融合スコア"] == scores == {"original-a": 0.4}
    assert [item.chunk_id for item in result] == ["original-a"]


async def test_returned_expansions_keep_their_actual_origin_and_exclusions(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from types import SimpleNamespace

    async def reference(self: Any, request: Any, state: Any, anchors: Any, **kwargs: Any) -> Any:
        assert kwargs["existing"] == {"seed-a"}
        kwargs["receipt"].update({"sources": [{"source_chunk_id": "seed-a", "status": "followed"}]})
        return {"seed-a": [row("reference-a")]}

    async def entity(*args: Any, **kwargs: Any) -> list[Any]:
        return [SimpleNamespace(chunk=row("entity-a"), info={})]

    async def graph(*args: Any, **kwargs: Any) -> list[Any]:
        assert {"seed-a", "reference-a", "entity-a"} <= kwargs["exclude_chunk_ids"]
        kwargs["diagnostics"]["status"] = "read"
        return [SimpleNamespace(chunk=row("graph-a"), info={})]

    monkeypatch.setattr(AnswerEngine, "_reference_expansion", reference)
    monkeypatch.setattr(module, "plan_entity_expansion", entity)
    monkeypatch.setattr(module, "plan_graph_expansion", graph)
    oracle = FakeOracle()
    oracle.chunks = [row("seed-a")]
    with (
        call_content_policy(lambda: True),
        operation_trace("rag.search", operation_id="synthetic") as summary,
        bind_trace_observer(_observe_engine_call),
    ):
        result = await engine(oracle)._search(
            SearchRequest(query="q"), _SearchState(), retrieval_queries=["q"], defer_context=True
        )
    captured = calls(summary)
    ref = json.loads(captured[REFERENCE_EXPANSION_STAGE].output.text)
    assert ref["起点と返した Chunk"]["seed-a"][0]["chunk_id"] == "reference-a"
    assert ref["今回の探索判断"]["sources"][0]["source_chunk_id"] == "seed-a"
    graph_input = json.loads(captured[GRAPH_EXPANSION_STAGE].input.text)
    assert set(graph_input["除外 ID"]) == {"seed-a", "reference-a", "entity-a"}
    graph_output = json.loads(captured[GRAPH_EXPANSION_STAGE].output.text)
    assert graph_output["返した固有 Chunk 数"] == 1
    assert graph_output["返した候補"][0]["chunk_id"] == "graph-a"
    assert graph_output["回答内の関係情報の探索記録（累計）"] == [{"status": "read"}]
    assert {item.chunk_uid for item in result.child_chunks} == {
        "seed-a",
        "reference-a",
        "entity-a",
        "graph-a",
    }
