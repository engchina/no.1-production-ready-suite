"""各段の予算と精排後の原文読取の契約。外部モデル/DBは呼ばない。"""

from dataclasses import replace
from types import SimpleNamespace

import pytest

from rag_engine.dependencies import AnswerDependencies, bind_dependencies
from rag_engine.generation.answer_models import AnswerRecord
from rag_engine.generation.answering import _bundle_context
from rag_engine.retrieval.candidate_budget import select_candidates


def child(uid: str, *, artifact: str = "a", text: str = "合成の本文", record: str = "r") -> AnswerRecord:
    return AnswerRecord(
        id=uid,
        engine="test",
        engine_label="test",
        page=1,
        seq_no=1,
        category="Chunk",
        text=text,
        source="source.pdf",
        chunk_uid=uid,
        chunk_id=uid,
        chunk_level="child",
        source_run_id="run",
        chunk_seq=1,
        source_record_refs=({"record_id": record},),
        metadata={"document_id": "document", "recipe_id": "recipe", "chunk_set_id": artifact, "chunk_strategy": "fixed"},
    )


@pytest.mark.parametrize("field", ["document_id", "recipe_id", "chunk_set_id", "artifact_id"])
def test_identical_text_in_another_artifact_is_retained(field):
    first = child("c1")
    other = replace(child("c2"), metadata={**first.metadata, field: "other"})
    selected, _ = select_candidates([first, other])
    assert len(selected) == 2


def test_same_original_is_merged_but_another_source_range_is_kept_without_a_count_limit():
    rows = [child("c1"), child("duplicate"), child("c2", record="other"), child("c3", text="別の本文")]
    selected, trace = select_candidates(rows)
    assert [row.id for row in selected] == ["c1", "c2", "c3"]
    assert trace["excluded"] == [{"id": "duplicate", "reason": "duplicate_original", "retained_id": "c1"}]


def test_fusion_and_attachment_candidates_are_all_reranked_and_expansions_are_not():
    """件数では切らず、検索拡張の経路だけを rerank の入力から外す（#5162・#5443）。"""

    def staged(stage: str, count: int) -> list[AnswerRecord]:
        return [
            replace(
                child(f"{stage}-{index}", record=f"{stage}-{index}"),
                metadata={**child("x").metadata, "retrieval_admission_stage": stage},
            )
            for index in range(count)
        ]

    records = [*staged("fusion", 200), *staged("reference", 3), *staged("attachment", 5)]
    selected, trace = select_candidates(records)

    assert len(selected) == 205
    assert trace["admitted"] == {"fusion": 200, "attachment": 5}
    assert {item["reason"] for item in trace["excluded"]} == {"not_rerank_stage"}
    assert len(trace["excluded"]) == 3


def test_no_source_provenance_is_not_a_duplicate():
    selected, _ = select_candidates([replace(child(uid), source_record_refs=()) for uid in ("c1", "c2")])
    assert len(selected) == 2


def test_context_is_read_after_the_ranked_anchor_choice():
    ranked = [child("last", text="精排の最上位"), child("first", text="融合の最上位")]
    calls, stages = [], []

    def read(**options):
        calls.append([row.id for row in options["anchors"]])
        return []

    dependencies = AnswerDependencies(
        search=lambda **kw: None,
        check_ready=lambda **kw: None,
        parse_text=lambda *args, **kw: None,
        parse_images=lambda *args, **kw: None,
        rerank=lambda *args, **kw: [],
        context_chunks=read,
        record_retrieval=stages.append,
    )
    with bind_dependencies(dependencies):
        result = _bundle_context(
            "合成の質問",
            ranked,
            list(ranked),
            SimpleNamespace(),
            top_k=1,
            neighbor_child_count=0,
            max_records=5,
            max_chars=1000,
        )
    assert calls == [["last"]]
    assert [record.id for record in result.records] == ["last"]
    assert stages[-1]["anchor_limit"] == 1
    assert stages[-1]["full_parent_text"] is True
    assert "character_limit" not in stages[-1]


def test_parent_group_uses_one_anchor_slot_and_fills_the_next_original():
    from rag_engine.retrieval.context_builder import ContextBuildRequest, build_chunk_context_bundle

    first = replace(child("c1"), parent_chunk_uid="p1", metadata={**child("c1").metadata, "chunk_strategy": "small_to_big"})
    same = replace(first, id="c2", chunk_id="c2", chunk_uid="c2", text="同じ親の別の子")
    next_child = replace(first, id="c3", chunk_id="c3", chunk_uid="c3", parent_chunk_uid="p2", text="別の親")
    records = [first, same, next_child]
    result = build_chunk_context_bundle(
        ContextBuildRequest(
            question="合成の質問",
            ranked_children=records,
            active_records=records,
            top_k=2,
            neighbor_child_count=0,
            max_records=3,
            max_chars=1000,
            select_anchors_only=True,
        )
    )
    assert {item.record.id for parent in result.evidence_tree for item in parent.children} == {"c1", "c2", "c3"}


def test_unknown_stage_is_not_reranked():
    row = replace(child("unknown"), metadata={"retrieval_admission_stage": "unknown"})
    selected, trace = select_candidates([row])
    assert not selected
    assert trace["excluded"] == [{"id": "unknown", "reason": "not_rerank_stage", "stage": "unknown"}]


def test_same_local_identifier_in_another_artifact_is_not_a_duplicate():
    selected, _ = select_candidates([child("local", artifact="v1"), child("local", artifact="v2")])
    assert len(selected) == 2
