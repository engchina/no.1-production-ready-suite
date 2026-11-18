"""独立の経路（Wiki。#4941）の原文を通常検索の根拠に集合として足す。合成の記録だけで確かめる。"""

from rag_engine.generation import answering
from rag_engine.generation.answer_models import AnswerContext, AnswerRecord
from rag_engine.generation.evidence_union import union_sources
from rag_engine.retrieval.context_builder import ContextChildEvidence, ContextParentEvidence


def record(chunk_id, *, children=(), version="cs", text=None):
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
        metadata={"document_id": "doc", "chunk_set_id": version},
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
    merged, added = union_sources(
        context, [record("c1"), record("c3"), record("c9"), record("c9"), record("c1", version="cs-new")]
    )
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
