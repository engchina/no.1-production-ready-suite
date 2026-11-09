"""根拠が検索の候補の一部のときだけ、実際の数を入れた読み方を生成へ渡す（#4668）。"""

from dataclasses import replace

from rag_engine.generation.answer_models import AnswerContext, AnswerRecord
from rag_engine.generation.answering import _evidence_subset_counts
from rag_engine.generation.grounded import evidence_subset_reading_rule
from rag_engine.retrieval.context_builder import ContextChildEvidence, ContextParentEvidence


def _record(uid: str, level: str = "parent", parent: str = "") -> AnswerRecord:
    return AnswerRecord(
        id=uid,
        chunk_id=uid,
        chunk_uid=uid,
        engine="docling",
        engine_label="Docling",
        page=1,
        seq_no=1,
        category="Chunk",
        text=f"{uid} の本文",
        source="資料.pdf",
        chunk_level=level,
        parent_chunk_uid=parent,
    )


def test_no_rule_when_all_candidates_are_shown() -> None:
    assert evidence_subset_reading_rule(5, 5) == ""
    assert evidence_subset_reading_rule(0, 10) == ""


def test_rule_carries_the_counted_numbers_when_only_a_subset_is_shown() -> None:
    rule = evidence_subset_reading_rule(3, 10)
    assert "候補 10 件のうち上位 3 件" in rule
    assert rule.startswith("### 根拠の範囲")


def test_counts_use_the_retrieved_child_units_of_the_parents_passed_to_the_model() -> None:
    parent = _record("p1")
    other = _record("p2")
    anchors = tuple(
        ContextChildEvidence(record=_record(f"c{i}", "child", "p1"), role="retrieved_anchor", reason="retrieved", retrieval_rank=i)
        for i in range(2)
    )
    neighbor = ContextChildEvidence(record=_record("n1", "child", "p1"), role="neighbor", reason="neighbor", retrieval_rank=0)
    tree = (
        ContextParentEvidence(record=parent, role="synthesis_parent", reason="retrieved", children=(*anchors, neighbor)),
        ContextParentEvidence(record=other, role="synthesis_parent", reason="retrieved"),
    )
    context = AnswerContext(records=[parent, other], text="", evidence_tree=tree, candidate_count=9)
    spans = [{"source_id": "p1", "evidence_id": "E1"}, {"source_id": "p1", "evidence_id": "E2"}]
    assert _evidence_subset_counts(context, spans) == (2, 9)
    assert _evidence_subset_counts(replace(context, evidence_tree=()), spans) == (1, 9)
