"""引用発見・読取・関連度の順の選択を、合成文書だけで検証する(#5243)。"""

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
    result = await _engine(oracle)._reference_expansion(
        SearchRequest(query="合成の質問"),
        state,
        anchors,
        existing={row.chunk_id for row in anchors},
        scores={row.chunk_id: 10.0 if i == 11 else 1.0 for i, row in enumerate(anchors)},
        receipt=receipt,
    )
    # 個数の上限で起点を選ばない(#5243): 12 の起点の参照先をすべて足す。
    assert set(result) == {f"a{i}" for i in range(12)}
    assert len(oracle.reference_calls) == 12
    assert receipt["checked_sources"] == receipt["unique_targets"] == receipt["read_targets"] == 12
    assert receipt["added_count"] == 12
    assert len(receipt["sources"]) == len(receipt["targets"]) == len(receipt["added_chunks"]) == 12
    diagnostic_sources = state.reference_sources[0]["sources"]
    assert isinstance(diagnostic_sources, list)
    assert len(diagnostic_sources) == 10
    added_chunks = state.reference_sources[0]["added_chunks"]
    assert isinstance(added_chunks, list)
    assert len(added_chunks) == 10
    assert receipt["stop_reasons"] == ["declared_source_range_complete"]


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
    result = await _engine(oracle)._reference_expansion(
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
    diagnostic_sources = state.reference_sources[0]["sources"]
    assert isinstance(diagnostic_sources, list)
    assert diagnostic_sources[0]["outcome"] == "resolve_error"


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
    a, b = chunk("a", "起点", [target(2)]), chunk("b", "第2章")
    state = _SearchState()
    result = await _engine(ReferenceOracle([a, b], [b]))._search(
        SearchRequest(query="合成の質問"), state, retrieval_queries=["合成の質問"]
    )
    assert [row.chunk_uid for row in result.child_chunks] == ["a", "b"]
    assert state.reference_sources[0]["added_count"] == 0
    # 上位の起点にある参照先は拡張の chunk にせず、rerank の候補の印も付けない(#5162)。
    assert result.expansion_chunks == []
    assert state.chunks["b"].metadata["reference_from_chunk_ids"] == ["a"]
    assert "reference_expansion" not in result.child_chunks[1].metadata


def test_other_document_target_cache_is_bound_to_source_version() -> None:
    from app.rag.answer_engine import _reference_target_cache_key

    reference = ReferenceTarget("sample", "document", "sample", "sample.pdf", None)
    a = chunk("a", "起点")
    b = a.model_copy(update={"metadata": {**a.metadata, "chunk_set_id": "version-b"}})
    assert _reference_target_cache_key({}, a, reference, None, None) != _reference_target_cache_key(
        {}, b, reference, None, None
    )


# ---- 個数の上限を外した選び方(#5243) ----


async def test_many_section_references_are_all_added_in_relevance_order() -> None:
    """節の参照の多い chunk(6 つの章)から、読めた章の chunk をすべて足し、点数の順に並べる。

    旧来は依存群の新しい chunk(12 件)が上限(既定 4 件)を超えて、何も足さなかった。
    """
    from tests.test_cross_references import FakeGenAi

    anchor = chunk("a", "起点", [target(n) for n in range(2, 8)])
    contents = [
        chunk(f"r{n}-{index}", f"第{n}章").model_copy(update={"text": f"第{n}章の本文{index}"})
        for n in range(2, 8)
        for index in range(2)
    ]
    genai = FakeGenAi({"第5章": 0.8, "第3章": 0.4})
    state = _SearchState()
    receipt: dict[str, Any] = {}

    result = await _engine(ReferenceOracle([anchor], contents), genai)._reference_expansion(
        SearchRequest(query="合成の質問"), state, [anchor], existing={"a"}, receipt=receipt
    )

    added = [row.chunk_id for row in result["a"]]
    assert sorted(added) == sorted(row.chunk_id for row in contents)
    assert added[:4] == ["r5-0", "r5-1", "r3-0", "r3-1"]
    assert receipt["added_count"] == 12
    assert receipt["sources"][0]["dependency_status"] == "satisfied"
    assert receipt["stop_reasons"] == ["declared_source_range_complete"]
    assert len(genai.rerank_calls) == 1


async def test_many_document_name_references_are_all_added() -> None:
    """文書名の参照の多い chunk(6 つの文書)から、各文書の質問に関連の高い chunk をすべて足す。"""
    from tests.test_cross_references import FakeGenAi

    names = [f"合成資料{letter}" for letter in "ABCDEF"]
    references = [ReferenceTarget(name, "document", "", name, None) for name in names]
    anchor = chunk("a", "起点", references)
    contents = [
        RetrievedChunk(
            chunk_id=f"{name}-{index}",
            document_id=f"doc-{name}",
            file_name=f"{name}.pdf",
            text=f"{name}の本文{index}",
            score=1,
            metadata={"chunk_set_id": f"set-{name}", "section_path": f"{name} > 節{index}"},
        )
        for name in names
        for index in range(3)
    ]
    genai = FakeGenAi({"合成資料Dの本文2": 0.9})
    receipt: dict[str, Any] = {}

    result = await _engine(ReferenceOracle([anchor], contents), genai)._reference_expansion(
        SearchRequest(query="合成の質問"),
        _SearchState(),
        [anchor],
        existing={"a"},
        question=("合成の質問", [0.1] * 4),
        receipt=receipt,
    )

    added = [row.chunk_id for row in result["a"]]
    assert sorted(added) == sorted(row.chunk_id for row in contents)
    assert added[0] == "合成資料D-2"
    assert {item["status"] for item in receipt["targets"]} == {"available"}
    assert receipt["added_count"] == 18


async def test_unreadable_target_does_not_drop_readable_targets(monkeypatch: Any) -> None:
    """一部の参照先が読めなくても、読めた参照先は足す(すべてか無しかにしない)。"""
    anchor = chunk("a", "起点", [target(2), target(3)])
    engine = _engine(ReferenceOracle([anchor], [chunk("x", "第2章")]))
    original = engine._reference_target_chunks

    async def read(
        scope: Any, state: Any, source: Any, reference: Any, **kwargs: Any
    ) -> list[RetrievedChunk]:
        if reference.key == "3":
            raise RuntimeError("synthetic failure")
        return await original(scope, state, source, reference, **kwargs)

    monkeypatch.setattr(engine, "_reference_target_chunks", read)
    receipt: dict[str, Any] = {}
    result = await engine._reference_expansion(
        SearchRequest(query="合成の質問"), _SearchState(), [anchor], existing={"a"}, receipt=receipt
    )

    assert [row.chunk_id for row in result["a"]] == ["x"]
    assert receipt["sources"][0]["outcome"] == "added"
    assert receipt["sources"][0]["dependency_status"] == "partial"
    assert receipt["sources"][0]["dependency_reasons"] == ["read_error"]
    assert receipt["partial_sources"] == 1
    assert receipt["stop_reasons"] == ["read_error"]


async def test_deadline_stops_new_target_reads_and_keeps_the_reason() -> None:
    """回答の期限の残りが生成の予約(章節の拡張と同じ)より少なければ、新しい参照先を読まない。"""
    from rag_engine.resources.deadline import Deadline

    anchor = chunk("a", "起点", [target(2), target(3)])
    oracle = ReferenceOracle([anchor], [chunk("x", "第2章"), chunk("y", "第3章")])
    engine = _engine(oracle, rag_answer_generation_round_estimate_seconds=60)
    state = _SearchState(deadline=Deadline(30))
    receipt: dict[str, Any] = {}

    result = await engine._reference_expansion(
        SearchRequest(query="合成の質問"), state, [anchor], existing={"a"}, receipt=receipt
    )

    assert result == {}
    assert oracle.reference_calls == []
    assert {item["status"] for item in receipt["targets"]} == {"deadline"}
    assert receipt["deadline_reserve_seconds"] == 90.0
    assert receipt["unread_targets"] == 2
    assert receipt["sources"][0]["dependency_status"] == "unsatisfied"
    assert receipt["stop_reasons"] == ["deadline"]

    # 残りが予約より多ければ読む。
    state = _SearchState(deadline=Deadline(600))
    result = await engine._reference_expansion(
        SearchRequest(query="合成の質問"), state, [anchor], existing={"a"}
    )
    assert sorted(row.chunk_id for row in result["a"]) == ["x", "y"]


async def test_rerank_failure_keeps_every_chunk_in_read_order() -> None:
    """rerank に失敗しても足す chunk は減らさず、読んだ順のまま足す(点数の印は付けない)。"""
    from app.rag.cross_references import REFERENCE_SCORE_KEY
    from tests.test_cross_references import FakeGenAi

    anchor = chunk("a", "起点", [target(2), target(3)])
    state = _SearchState()
    receipt: dict[str, Any] = {}
    result = await _engine(
        ReferenceOracle([anchor], [chunk("x", "第2章"), chunk("y", "第3章")]),
        FakeGenAi(fail=True),
    )._reference_expansion(
        SearchRequest(query="合成の質問"), state, [anchor], existing={"a"}, receipt=receipt
    )

    assert [row.chunk_id for row in result["a"]] == ["x", "y"]
    assert receipt["ranking"] == "rerank_failed"
    assert REFERENCE_SCORE_KEY not in state.chunks["x"].metadata
    assert state.degraded.failed("reference_rerank")


def test_reference_score_is_passed_as_question_relevance_only() -> None:
    """参照先の点数は原質問との点数として rag_engine へ渡し、並べ替えの点数にはしない。

    回答の生成は原文をこの点数の順で根拠の文字の予算に入れ、入らない分を分割読取へ渡す。
    """
    from rag_engine.retrieval.rerank_scores import question_rerank_score, rerank_score

    from app.rag.answer_engine import _stored_child
    from app.rag.cross_references import (
        REFERENCE_FROM_KEY,
        REFERENCE_LABEL_KEY,
        REFERENCE_SCORE_KEY,
    )

    base = chunk("x", "第2章")
    marked = base.model_copy(
        update={
            "metadata": {
                **base.metadata,
                REFERENCE_FROM_KEY: "a",
                REFERENCE_LABEL_KEY: "第2章",
                REFERENCE_SCORE_KEY: 0.7,
            }
        }
    )
    stored = _stored_child(marked, rrf_score=0.0)
    assert question_rerank_score(stored) == 0.7
    assert rerank_score(stored) is None
    assert stored.metadata["reference_expansion"]["from_chunk_uid"] == "a"
    plain = _stored_child(base, rrf_score=0.0)
    assert question_rerank_score(plain) is None
