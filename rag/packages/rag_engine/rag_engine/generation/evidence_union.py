"""独立の経路（Wiki。#4941）の原文を、通常検索の根拠に集合として足す。経路の間で採点・並べ替え・件数の削減をしない。"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import replace

from rag_engine.generation.answer_models import AnswerContext, AnswerRecord
from rag_engine.retrieval.context_builder import ContextChildEvidence, ContextParentEvidence


def _position(record: AnswerRecord, chunk_id: str) -> tuple[str, str, str]:
    """原文の位置（文書・保存版・chunk）。同じ位置・同じ保存版だけを同じ原文とする。"""
    metadata = record.metadata
    return (
        str(metadata.get("document_id") or record.source),
        str(metadata.get("chunk_set_id") or record.source_run_id),
        chunk_id,
    )


def union_sources(context: AnswerContext, additional: Sequence[AnswerRecord]) -> tuple[AnswerContext, int]:
    """通常検索の根拠（親とその子）がまだ含まない原文だけを末尾に足した context と、足した数。"""
    present = [*context.records, *(child.record for parent in context.evidence_tree for child in parent.children)]
    covered = {_position(record, chunk_id) for record in present for chunk_id in (record.chunk_id, *record.child_chunk_ids) if chunk_id}
    added: list[AnswerRecord] = []
    for record in additional:
        key = _position(record, record.chunk_id)
        if key not in covered:
            covered.add(key)
            added.append(record)
    if not added:
        return context, 0
    records = [*context.records, *added]
    tree = context.evidence_tree + tuple(
        ContextParentEvidence(
            record=record,
            role="child_fallback",
            reason="独立の経路が参照した現在の原文",
            children=(ContextChildEvidence(record=record, role="retrieved_anchor", reason="独立の経路が参照した原文"),),
        )
        for record in added
    )
    merged = replace(
        context,
        records=records,
        text="\n\n".join(record.body_text or record.text for record in records),
        evidence_tree=tree,
        status="ready",
        insufficient_reason="",
        candidate_count=max(context.candidate_count, len(records)),
    )
    return merged, len(added)
