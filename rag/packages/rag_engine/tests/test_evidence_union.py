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
