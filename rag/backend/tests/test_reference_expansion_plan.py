"""引用発見・読取・依存選択を、合成文書だけで検証する。"""

from typing import Any

from app.rag.answer_engine import _SearchState
from app.rag.cross_references import ReferenceTarget
from app.schemas.search import RetrievedChunk, SearchRequest
from tests.test_cross_references import ReferenceOracle, _engine


def target(number: int) -> ReferenceTarget:
    return ReferenceTarget(f"第{number}章", "chapter", str(number), None, f"第{number}章")


def chunk(uid: str, section: str, targets: list[ReferenceTarget] | None = None) -> RetrievedChunk:
    import json

    from app.rag.cross_references import (
        REFERENCE_RESOLUTION_VERSION,
        REFERENCE_RESOLUTION_VERSION_KEY,
        REFERENCE_TARGETS_KEY,
    )

    return RetrievedChunk(
        chunk_id=uid,
        document_id="synthetic-doc",
        file_name="sample.pdf",
        text="合成本文",
        score=1,
        metadata={
            "chunk_set_id": "version-a",
            "section_path": section,
            REFERENCE_RESOLUTION_VERSION_KEY: REFERENCE_RESOLUTION_VERSION,
            REFERENCE_TARGETS_KEY: json.dumps([item.to_dict() for item in targets or []]),
        },
    )


async def test_discovers_entire_range_before_selecting_and_keeps_full_receipt() -> None:
    anchors = [chunk(f"a{i}", "起点", [target(i + 2)]) for i in range(12)]
    contents = [chunk(f"r{i}", f"第{i + 2}章") for i in range(12)]
    oracle = ReferenceOracle(anchors, contents)
    state = _SearchState()
    receipt: dict[str, Any] = {}
    result = await _engine(oracle, rag_reference_expansion_max_chunks=2)._reference_expansion(
        SearchRequest(query="合成の質問"),
        state,
        anchors,
        existing={row.chunk_id for row in anchors},
        scores={row.chunk_id: 10.0 if i == 11 else 1.0 for i, row in enumerate(anchors)},
        receipt=receipt,
    )
    assert set(result) == {"a0", "a11"}
    assert len(oracle.reference_calls) == 12
    assert receipt["checked_sources"] == receipt["unique_targets"] == receipt["read_targets"] == 12
    assert receipt["added_count"] == 2
    assert len(receipt["sources"]) == len(receipt["targets"]) == 12
    assert len(state.reference_sources[0]["sources"]) == 10
    assert receipt["stop_reasons"] == ["chunk_budget"]


async def test_target_read_budget_is_independent_and_cache_reuse_is_measured() -> None:
    anchors = [chunk("a", "起点", [target(2)]), chunk("b", "起点", [target(3)])]
    oracle = ReferenceOracle(anchors, [chunk("x", "第2章"), chunk("y", "第3章")])
    state = _SearchState()
    engine = _engine(oracle, rag_reference_expansion_max_targets=1)
    first: dict[str, Any] = {}
    await engine._reference_expansion(
        SearchRequest(query="合成の質問"), state, anchors, existing={"a", "b"}, receipt=first
    )
    assert first["discovered_targets"] == 2
    assert first["read_targets"] == first["unread_targets"] == 1
    assert first["sources"][1]["dependency_reasons"] == ["target_limit"]
    second: dict[str, Any] = {}
    await engine._reference_expansion(
        SearchRequest(query="合成の質問"), state, anchors, existing={"a", "b"}, receipt=second
    )
    assert second["reused_targets"] == second["read_targets"] == 1
    assert second["unread_targets"] == 0
    assert len(oracle.reference_calls) == 2


async def test_shared_reference_preserves_both_dependencies_without_duplicate_chunks() -> None:
    anchors = [chunk("a", "起点", [target(2)]), chunk("b", "起点", [target(2)])]
    oracle = ReferenceOracle(anchors, [chunk("x", "第2章"), chunk("y", "第2章")])
    state = _SearchState()
    receipt: dict[str, Any] = {}
    result = await _engine(oracle, rag_reference_expansion_max_chunks=2)._reference_expansion(
        SearchRequest(query="合成の質問"),
        state,
        anchors,
        existing={"a", "b"},
        receipt=receipt,
    )
    assert len(oracle.reference_calls) == 1
    assert [row.chunk_id for rows in result.values() for row in rows] == ["x", "y"]
    assert receipt["duplicate_targets"] == 1
    assert all(row["dependency_status"] == "satisfied" for row in receipt["sources"])
    assert all(row["selected_dependency_chunk_ids"] == ["x", "y"] for row in receipt["sources"])
    assert state.chunks["x"].metadata["reference_from_chunk_ids"] == ["a", "b"]


async def test_scope_changes_do_not_reuse_target_content_from_another_search_scope() -> None:
    anchor = chunk("a", "起点", [target(2)])
    oracle = ReferenceOracle([anchor], [chunk("x", "第2章")])
    state = _SearchState()
    engine = _engine(oracle)
    for category in ["category-a", "category-b"]:
        await engine._reference_expansion(
            SearchRequest(query="合成の質問", filters={"large_category": category}),
            state,
            [anchor],
            existing={"a"},
        )
    assert [call["filters"]["large_category"] for call in oracle.reference_calls] == [
        "category-a",
        "category-b",
    ]


async def test_empty_result_and_read_error_keep_real_reasons(monkeypatch: Any) -> None:
    anchors = [chunk("a", "起点", [target(2)]), chunk("b", "起点", [target(3)])]
    oracle = ReferenceOracle(anchors, [])
    engine = _engine(oracle)

    async def read(
        scope: Any, state: Any, anchor: Any, reference: Any, **kwargs: Any
    ) -> list[RetrievedChunk]:
        if reference.key == "3":
            raise RuntimeError("synthetic failure")
        return []

    monkeypatch.setattr(engine, "_reference_target_chunks", read)
    receipt: dict[str, Any] = {}
    result = await engine._reference_expansion(
        SearchRequest(query="合成の質問"),
        _SearchState(),
        anchors,
        existing={"a", "b"},
        receipt=receipt,
    )
    assert result == {}
    assert receipt["sources"][0]["dependency_reasons"] == ["no_available_content"]
    assert receipt["sources"][1]["dependency_reasons"] == ["read_error"]
    assert receipt["unsatisfied_sources"] == 2
    assert receipt["checked_targets"] == receipt["failed_targets"] == 1


async def test_receipt_remains_complete_after_diagnostic_search_limit() -> None:
    anchor = chunk("a", "起点", [target(2)])
    oracle = ReferenceOracle([anchor], [chunk("x", "第2章")])
    state = _SearchState()
    engine = _engine(oracle)
    for _ in range(5):
        receipt: dict[str, Any] = {}
        await engine._reference_expansion(
            SearchRequest(query="合成の質問"), state, [anchor], existing={"a"}, receipt=receipt
        )
        assert len(receipt["sources"]) == len(receipt["targets"]) == 1
        assert receipt["checked_targets"] == 1
        assert receipt["added_count"] == 1
    assert len(state.reference_sources) == 4
    assert len(oracle.reference_calls) == 1


async def test_resolution_failure_is_not_reported_as_complete(monkeypatch: Any) -> None:
    anchor = chunk("a", "起点", [target(2)])
    engine = _engine(ReferenceOracle([anchor], []))

    async def resolve(*args: Any, **kwargs: Any) -> Any:
        raise RuntimeError("synthetic")

    monkeypatch.setattr(engine, "_anchor_reference_targets", resolve)
    receipt: dict[str, Any] = {}
    state = _SearchState()
    assert (
        await engine._reference_expansion(
            SearchRequest(query="合成の質問"), state, [anchor], existing={"a"}, receipt=receipt
        )
        == {}
    )
    assert receipt["resolution_errors"] == receipt["unsatisfied_sources"] == 1
    assert receipt["stop_reasons"] == ["resolution_error"]
    assert state.reference_sources[0]["sources"][0]["outcome"] == "resolve_error"


async def test_document_reference_without_search_input_is_not_counted_as_read() -> None:
    reference = ReferenceTarget("sample", "document", "sample", "sample.pdf", None)
    anchor = chunk("a", "起点", [reference])
    receipt: dict[str, Any] = {}
    await _engine(ReferenceOracle([anchor], []))._reference_expansion(
        SearchRequest(query="合成の質問"), _SearchState(), [anchor], existing={"a"}, receipt=receipt
    )
    assert receipt["read_targets"] == receipt["read_chunk_count"] == 0
    assert receipt["unread_targets"] == 1
    assert receipt["targets"][0]["status"] == "missing_search_input"
    assert receipt["sources"][0]["dependency_status"] == "unsatisfied"


async def test_existing_anchor_dependency_is_retained_without_spending_supplement_budget() -> None:
    from rag_engine.retrieval.rerank_selection import select_reranked_candidates

    a, b = chunk("a", "起点", [target(2)]), chunk("b", "第2章")
    state = _SearchState()
    result = await _engine(ReferenceOracle([a, b], [b]))._search(
        SearchRequest(query="合成の質問"), state, retrieval_queries=["合成の質問"]
    )
    assert [row.chunk_uid for row in result.child_chunks] == ["a", "b"]
    assert state.reference_sources[0]["added_count"] == 0
    assert result.child_chunks[1].metadata["reference_expansion"] == {
        "from_chunk_uids": ["a"],
        "keep_source_position": True,
    }
    assert select_reranked_candidates(result.child_chunks, 1)[0] == []


def test_other_document_target_cache_is_bound_to_source_version() -> None:
    from app.rag.answer_engine import _reference_target_cache_key

    reference = ReferenceTarget("sample", "document", "sample", "sample.pdf", None)
    a = chunk("a", "起点")
    b = a.model_copy(update={"metadata": {**a.metadata, "chunk_set_id": "version-b"}})
    assert _reference_target_cache_key({}, a, reference, None, None) != _reference_target_cache_key(
        {}, b, reference, None, None
    )
