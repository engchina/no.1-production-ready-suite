"""検索拡張（参照先・実体・関係情報）の根拠を、rerank の後に最後の根拠の並びへ合わせる（#5097・#5162）。

拡張の根拠は rerank の候補に入れず、rerank・関連度の下限・``top_k``・context の枠で消さず、最後の根拠の
並びへ同じ規則で 1 回だけ合わせる（同じ chunk・親にあれば印だけ、同じ文書があれば文書の chunk の順、無ければ最後）。
"""

from dataclasses import replace
from types import SimpleNamespace

from record_fixtures import child, parent

from rag_engine.config import get_settings
from rag_engine.generation import answering as a
from rag_engine.models.llm import RerankTextRank
from rag_engine.retrieval.context_builder import ContextBuildRequest, build_chunk_context_bundle
from rag_engine.retrieval.expansion_merge import (
    ENTITY_EXPANSION_KEY,
    EXPANSION_EVIDENCE_REASON,
    GRAPH_EXPANSION_KEY,
    REFERENCE_EXPANSION_KEY,
    expansion_kinds,
    is_expansion_record,
    merge_expansions,
)


def _item(name, document, seq):
    return SimpleNamespace(name=name, document=document, seq=seq)


def _merge(ordered, expansions):
    merged = merge_expansions(
        ordered, expansions, key=lambda item: item.name, document=lambda item: item.document, seq=lambda item: item.seq
    )
    return [item.name for item in merged]


def test_same_chunk_is_not_added_again():
    ordered = [_item("a2", "A", 2), _item("b1", "B", 1)]
    assert _merge(ordered, [_item("a2", "A", 2)]) == ["a2", "b1"]


def test_same_document_goes_after_the_largest_smaller_chunk():
    ordered = [_item("a2", "A", 2), _item("b1", "B", 1), _item("a5", "A", 5)]
    assert _merge(ordered, [_item("a3", "A", 3)]) == ["a2", "a3", "b1", "a5"]
    assert _merge(ordered, [_item("a9", "A", 9)]) == ["a2", "b1", "a5", "a9"]


def test_same_document_without_smaller_chunk_goes_before_the_smallest():
    ordered = [_item("b1", "B", 1), _item("a5", "A", 5), _item("a2", "A", 2)]
    assert _merge(ordered, [_item("a1", "A", 1)]) == ["b1", "a5", "a1", "a2"]


def test_other_documents_go_last_grouped_by_document_in_chunk_order():
    ordered = [_item("a2", "A", 2)]
    expansions = [_item("c7", "C", 7), _item("d1", "D", 1), _item("c3", "C", 3)]
    assert _merge(ordered, expansions) == ["a2", "c3", "c7", "d1"]


def test_expansion_kinds_cover_reference_entity_and_graph():
    record = child("x", 1, "本文", "X.txt", "px")
    assert not is_expansion_record(record)
    record.metadata[REFERENCE_EXPANSION_KEY] = {"from_chunk_uid": "run:a"}
    record.metadata[ENTITY_EXPANSION_KEY] = {"hop": 0}
    record.metadata[GRAPH_EXPANSION_KEY] = {"kind": "section"}
    assert expansion_kinds(record) == (REFERENCE_EXPANSION_KEY, ENTITY_EXPANSION_KEY, GRAPH_EXPANSION_KEY)


def _marked(record, key=ENTITY_EXPANSION_KEY):
    record.metadata[key] = {"hop": 1}
    return record


def _bundle(ranked, active, expansions, *, top_k=2, max_records=2, neighbors=0):
    return build_chunk_context_bundle(
        ContextBuildRequest(
            question="合成の質問",
            ranked_children=ranked,
            active_records=active,
            top_k=top_k,
            neighbor_child_count=neighbors,
            max_records=max_records,
            max_chars=100_000,
            expansion_children=expansions,
        )
    )


def _parents(bundle):
    return [item.record.id for item in bundle.evidence_tree]


def test_bundle_places_expansions_by_document_order_without_counting_them():
    a2, b1 = child("a2", 2, "A の 2", "A.txt", "pa2"), child("b1", 1, "B の 1", "B.txt", "pb1")
    a1 = _marked(child("a1", 1, "A の 1", "A.txt", "pa1"))
    a3 = _marked(child("a3", 3, "A の 3", "A.txt", "pa3"), REFERENCE_EXPANSION_KEY)
    c1 = _marked(child("c1", 1, "C の 1", "C.txt", "pc1"), GRAPH_EXPANSION_KEY)
    parents = [
        parent(name, 1, f"{name} の親", source)
        for name, source in (("pa1", "A.txt"), ("pa2", "A.txt"), ("pa3", "A.txt"), ("pb1", "B.txt"), ("pc1", "C.txt"))
    ]
    bundle = _bundle([a2, b1], [a2, b1, a1, a3, c1, *parents], [a1, a3, c1], top_k=2, max_records=2)
    # top_k=2・max_records=2 でも拡張の根拠は消えず、数えない。
    assert _parents(bundle) == ["pa1", "pa2", "pa3", "pb1", "pc1"]
    added = [child.reason for item in bundle.evidence_tree for child in item.children if child.record.id in {"a1", "a3", "c1"}]
    assert added == [EXPANSION_EVIDENCE_REASON] * 3


def test_bundle_only_marks_an_expansion_already_in_the_result():
    a2 = child("a2", 2, "A の 2", "A.txt", "pa2", 0.9)
    expansion = _marked(child("a2", 2, "A の 2", "A.txt", "pa2"))
    bundle = _bundle([a2], [a2, parent("pa2", 1, "親", "A.txt")], [expansion])
    assert _parents(bundle) == ["pa2"]
    record = bundle.evidence_tree[0].children[0].record
    assert ENTITY_EXPANSION_KEY in record.metadata
    # 並びの値（rerank の関連度）は残す。
    assert record.metadata["rerank"]["relevance_score"] == 0.9


def test_bundle_only_marks_an_expansion_inside_a_parent_in_the_result():
    a2 = child("a2", 2, "A の 2", "A.txt", "pa")
    a3 = _marked(child("a3", 3, "A の 3", "A.txt", "pa"))
    bundle = _bundle([a2], [a2, a3, parent("pa", 1, "A の親", "A.txt")], [a3])
    assert _parents(bundle) == ["pa"]
    roles = {child.record.id: child.role for child in bundle.evidence_tree[0].children}
    assert roles == {"a2": "retrieved_anchor", "a3": "parent_context"}


def test_bundle_keeps_expansion_when_its_parent_fell_outside_the_record_limit():
    ranked = [child(f"a{i}", i, f"A の {i}", "A.txt", f"pa{i}") for i in range(1, 4)]
    expansion = _marked(child("a3", 3, "A の 3", "A.txt", "pa3"))
    parents = [parent(f"pa{i}", i, f"親 {i}", "A.txt") for i in range(1, 4)]
    bundle = _bundle(ranked, [*ranked, *parents], [expansion], top_k=3, max_records=2)
    assert _parents(bundle) == ["pa1", "pa2", "pa3"]
    anchors = [child for item in bundle.evidence_tree for child in item.children if child.record.id == "a3"]
    assert [child.reason for child in anchors] == [EXPANSION_EVIDENCE_REASON]


def _flat(name, seq, source):
    record = child(name, seq, f"{source} の {seq}", source, name)
    record.metadata["chunk_strategy"] = "fixed"
    return record


def test_bundle_reads_neighbors_of_a_flat_expansion_and_marks_covered_neighbors():
    a1, a2, a3 = (_flat(f"a{i}", i, "A.txt") for i in range(1, 4))
    b1, b2, b3 = (_flat(f"b{i}", i, "B.txt") for i in range(1, 4))
    covered = _marked(_flat("a2", 2, "A.txt"))
    added = _marked(_flat("b2", 2, "B.txt"))
    bundle = _bundle([a1], [a1, a2, a3, b1, b2, b3], [covered, added], top_k=1, max_records=2, neighbors=1)
    # a2 は a1 の前後の文脈にあるので印だけ。b2 は前後の文脈（b1・b3）と一緒に最後へ。
    assert _parents(bundle) == ["a1", "a2", "b1", "b2", "b3"]
    a2_child = next(child for item in bundle.evidence_tree for child in item.children if child.record.id == "a2")
    assert a2_child.role == "neighbor_context"
    assert ENTITY_EXPANSION_KEY in a2_child.record.metadata


def test_answer_context_does_not_rerank_expansions_and_keeps_them_after_threshold_and_top_k(monkeypatch):
    records = [child(f"c{i}", i, f"合成本文 {i}", "C.txt", f"p{i}") for i in range(3)]
    expansion = _marked(child("x1", 1, "拡張の本文", "X.txt", "px1"))
    parents = [*(parent(f"p{i}", i, f"合成本文 {i}", "C.txt") for i in range(3)), parent("px1", 1, "拡張の本文", "X.txt")]
    reranked = []

    def provider(question, documents, settings, top_n):
        reranked.append(list(documents))
        return [RerankTextRank(i, score) for i, score in enumerate([0.9, 0.1, 0.8])]

    monkeypatch.setattr(
        a,
        "search_adb_hybrid_chunks",
        lambda **_: SimpleNamespace(child_chunks=records, all_chunks=[*records, expansion, *parents], expansion_chunks=[expansion]),
    )
    monkeypatch.setattr(a, "_stored_chunk_answer_record", lambda record: record)
    monkeypatch.setattr(a, "_rerank_configured", lambda _: True)
    monkeypatch.setattr(a, "rerank_text_with_scores", provider)
    context = a.build_adb_hybrid_answer_context(
        "合成の質問",
        "synthetic-run",
        ["docling"],
        replace(get_settings(environ={}, dotenv_path=None), rerank_min_relevance_score=0.4),
        top_k=1,
        neighbor_child_count=0,
        retrieval_scope=a.RETRIEVAL_SCOPE_KNOWLEDGE_BASE,
    )
    # rerank には検索の候補 3 件だけを渡す（拡張の根拠は rerank にかけない）。
    assert reranked and all(len(documents) == 3 for documents in reranked)
    assert all("拡張の本文" not in document for documents in reranked for document in documents)
    # 下限 0.4・top_k 1 の後でも拡張の根拠は残り、別の文書なので最後に置く。
    assert [record.id for record in context.records] == ["p0", "px1"]
    assert context.expansion_candidates == (expansion,)
