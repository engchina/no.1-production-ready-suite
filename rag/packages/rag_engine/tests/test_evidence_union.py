"""独立の経路（Wiki。#4941）の原文を通常検索の根拠に集合として足す。合成の記録だけで確かめる。"""

from rag_engine.generation import answering
from rag_engine.generation.answer_models import AnswerContext, AnswerRecord
from rag_engine.generation.evidence_union import union_sources
from rag_engine.retrieval.context_builder import ContextChildEvidence, ContextParentEvidence


def record(chunk_id, *, children=(), version="cs", text=None, document="doc", identities=None, relations=None):
    return AnswerRecord(
        id=chunk_id,
        engine="e",
        engine_label="e",
        page=1,
        seq_no=1,
        category="ChildChunk",
        text=text or f"{chunk_id} の合成の本文",
        source="合成.md",
        chunk_id=chunk_id,
        child_chunk_ids=tuple(children),
        metadata={
            "document_id": document,
            "chunk_set_id": version,
            **({"source_identities": identities} if identities else {}),
            **({"wiki_relations": relations} if relations else {}),
        },
    )


def native_context():
    parent = record("p1", children=("c1", "c2"))
    child = record("c3")
    tree = (
        ContextParentEvidence(
            record=parent, role="parent", reason="", children=(ContextChildEvidence(record=child, role="retrieved_anchor", reason=""),)
        ),
    )
    return AnswerContext(records=[parent], text=parent.text, evidence_tree=tree, candidate_count=1)


def test_only_sources_at_new_positions_are_appended_without_reordering():
    context = native_context()
    merged, added = union_sources(context, [record("c1"), record("c3"), record("c9"), record("c9"), record("c1", version="cs-new")])
    assert added == 2
    assert [r.chunk_id for r in merged.records] == ["p1", "c9", "c1"]
    assert merged.records[2].metadata["chunk_set_id"] == "cs-new"  # 別の保存版は同じ原文にしない
    assert merged.evidence_tree[0] == context.evidence_tree[0]
    assert [parent.record.chunk_id for parent in merged.evidence_tree[1:]] == ["c9", "c1"]
    assert merged.status == "ready" and merged.candidate_count == 3


def test_wiki_only_sources_make_an_empty_context_answerable():
    empty = AnswerContext(records=[], text="", status="insufficient", insufficient_reason="根拠なし")
    merged, added = union_sources(empty, [record("c1")])
    assert added == 1 and merged.status == "ready" and merged.insufficient_reason == "" and merged.text


def test_injected_sources_are_unioned_and_recorded(monkeypatch):
    import rag_engine.dependencies as dependencies
    import rag_engine.generation.answer_records as answer_records

    stages = []
    monkeypatch.setattr(dependencies, "injected_additional_sources", lambda: [record("c1"), record("c9")])
    monkeypatch.setattr(answer_records, "_stored_chunk_answer_record", lambda value: value)
    monkeypatch.setattr(answering, "record_retrieval_stage", stages.append)
    merged = answering._with_independent_sources(native_context())
    assert [r.chunk_id for r in merged.records] == ["p1", "c9"]
    assert stages == [{"stage": "independent_source_union", "fusion": "set_union", "source_count": 2, "added": 1}]


def test_no_injected_sources_leaves_the_context_unchanged():
    context = native_context()
    assert answering._with_independent_sources(context) is context


def test_document_identities_reach_overlapping_sources_of_the_same_document():
    # 通常検索がすでに拾った原文と重なっても、文書の版の属性は同じ文書・保存版の根拠に付く（#5444）。
    identity = [{"source_key": "S", "effective_date": "2000-01-01", "source_status": "Current"}]
    context = native_context()
    other = record("o1", document="other")
    context = AnswerContext(records=[*context.records, other], text="", evidence_tree=context.evidence_tree)
    merged, added = union_sources(context, [record("c3", identities=identity), record("c1", version="old", identities=identity)])
    assert added == 1  # 重なる原文は足さない
    assert merged.records[0].metadata["source_identities"] == identity
    assert merged.evidence_tree[0].record.metadata["source_identities"] == identity
    assert merged.evidence_tree[0].children[0].record.metadata["source_identities"] == identity
    assert "source_identities" not in merged.records[1].metadata  # 別の文書には付けない


def test_wiki_relations_reach_the_sources_that_already_hold_the_same_original():
    # 重なりで足さない原文の関係（#5500）は、同じ位置・保存版の原文を含む根拠（親の子 ID・子の根拠）に写す。
    exception = {"relation": "exception", "role_of": "this", "document_name": "合成B.md", "section_path": ["通知"]}
    reference = {"relation": "reference", "role_of": "other", "document_name": "合成C.md", "section_path": ["別表"]}
    context = native_context()
    merged, added = union_sources(
        context,
        [
            record("c1", relations=[exception]),
            record("c2", relations=[exception, reference]),
            record("c3", relations=[reference]),
            record("c1", version="old", relations=[reference]),
        ],
    )
    assert added == 1  # 別の保存版だけを足す
    assert merged.records[0].metadata["wiki_relations"] == [exception, reference]  # 親は子 c1・c2 を含む
    assert merged.evidence_tree[0].record.metadata["wiki_relations"] == [exception, reference]
    assert merged.evidence_tree[0].children[0].record.metadata["wiki_relations"] == [reference]
    assert merged.records[1].metadata["wiki_relations"] == [reference]  # 足した原文は自分の関係のまま


def test_sources_without_wiki_relations_are_left_unchanged():
    context = native_context()
    merged, _ = union_sources(context, [record("c1")])
    assert all("wiki_relations" not in r.metadata for r in merged.records)
    assert merged.evidence_tree[0] == context.evidence_tree[0]


def ranked(chunk_id, score, rank=None):
    """rerank の点数（と順位）を持つ合成の record。順位が無いのは独立の経路の起点（順位は合わせるときに決まる）。"""
    base = record(chunk_id)
    rerank = {"relevance_score": score, **({"rank": rank} if rank is not None else {})}
    return AnswerRecord(**{**base.__dict__, "metadata": {**base.metadata, "rerank": rerank}})


def test_rerank_ranks_are_one_order_across_normal_candidates_and_independent_roots():
    """通常検索の候補と独立の経路の起点を、rerank の点数で 1 つの順位にする（#5831）。並びと件数は変えない。"""
    first, second, cut = ranked("c1", 0.9, 1), ranked("c2", 0.6, 2), ranked("c5", 0.5, 3)
    tree = tuple(
        ContextParentEvidence(
            record=r, role="child_fallback", reason="", children=(ContextChildEvidence(record=r, role="retrieved_anchor", reason=""),)
        )
        for r in (first, second)
    )
    # 起点数で切られた候補（c5）は根拠に無いが、順位には数える。
    context = AnswerContext(records=[first, second], text="", evidence_tree=tree, ranked_candidates=(first, second, cut), candidate_count=3)
    roots = [ranked("w1", 0.95), ranked("w2", 0.6), ranked("w3", 0.55)]
    merged, added = union_sources(context, [*roots, record("f1")])

    assert added == 4
    assert [r.chunk_id for r in merged.records] == ["c1", "c2", "w1", "w2", "w3", "f1"]
    ranks = {r.chunk_id: (r.metadata.get("rerank") or {}).get("rank") for r in merged.records}
    # w1(0.95) > c1(0.9) > c2(0.6) = w2(0.6。同じ点数は通常検索が先) > w3(0.55) > c5(0.5)。辿った単位は順位なし。
    assert ranks == {"c1": 2, "c2": 3, "w1": 1, "w2": 4, "w3": 5, "f1": None}
    # 木の親・子の record も同じ順位にする。
    assert [parent.children[0].record.metadata["rerank"]["rank"] for parent in merged.evidence_tree[:2]] == [2, 3]
    # 点数は変えない。
    assert [r.metadata["rerank"]["relevance_score"] for r in merged.records[:5]] == [0.9, 0.6, 0.95, 0.6, 0.55]


def test_ranks_are_left_alone_when_no_independent_root_has_a_score():
    first = ranked("c1", 0.9, 1)
    context = AnswerContext(records=[first], text="", ranked_candidates=(first,), candidate_count=1)
    merged, added = union_sources(context, [record("f1")])
    assert added == 1 and merged.records[0] is first and "rerank" not in merged.records[1].metadata


def test_independent_sources_join_after_the_supplement_merge_and_before_the_evidence_expansion(monkeypatch, tmp_path):
    """独立の経路の原文は、補いの検索の合わせ込みの後・根拠の拡張（章節・親と前後・参照）の前に合わせる（#5831）。

    補いの合わせ込みは主の検索の候補から文脈を作り直すので、その前に足した原文は外れる。後の拡張は、足した原文を
    通常検索の根拠と同じ文脈として受け取る。
    """
    from dataclasses import replace
    from pathlib import Path

    from rag_engine.config import get_settings
    from rag_engine.generation.answer_models import QuestionTextSearchInfo
    from rag_engine.knowledge.runtime_knowledge import RuntimeKnowledgeContext

    order: list[tuple[str, list[str]]] = []

    def step(name, result=lambda context: context):
        def call(*args, **kwargs):
            context = next(value for value in args if isinstance(value, AnswerContext))
            order.append((name, [r.chunk_id for r in context.records]))
            return result(context)

        return call

    joined = replace(native_context(), records=[*native_context().records, record("w1")])
    monkeypatch.setattr(answering, "_with_independent_sources", step("union", lambda context: joined))
    monkeypatch.setattr(answering, "_expand_context_by_sections", step("sections", lambda context: (context, None)))
    monkeypatch.setattr(answering, "_fill_unread_section_anchors", step("unread"))
    monkeypatch.setattr(answering, "_add_context_references", step("references"))
    settings = replace(get_settings(environ={}, dotenv_path=None), output_dir=tmp_path)
    expanded = answering._expand_evidence(
        native_context(),
        settings,
        normalized_question="合成の質問",
        request_text="合成の質問",
        run_id=None,
        preferred_engine_ids=[],
        chunk_top_k=5,
        chunk_neighbor_count=1,
        effective_rerank_enabled=True,
        selected_retrieval_scope="knowledge_base",
        selected_classification_filter=None,
        retrieval_queries=("合成の質問",),
        text_queries={},
        text_search_info=QuestionTextSearchInfo(),
        runtime_knowledge=RuntimeKnowledgeContext(original_question="合成の質問", expanded_question="合成の質問", path=Path(tmp_path)),
        guide_plan=None,
        answer_started_at=0.0,
    )
    assert order == [
        ("union", ["p1"]),
        ("sections", ["p1", "w1"]),
        ("unread", ["p1", "w1"]),
        ("references", ["p1", "w1"]),
    ]
    assert [r.chunk_id for r in expanded.context.records] == ["p1", "w1"]
