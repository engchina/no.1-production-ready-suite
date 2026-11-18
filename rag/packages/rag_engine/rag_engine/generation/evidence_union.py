"""独立の経路（Wiki。#4941）の原文を、通常検索の根拠に集合として足す。経路の間で採点・並べ替え・件数の削減をしない。"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import replace
from typing import Any

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


def _with_identities(record: Any, identities: dict[tuple[str, str], Any]) -> Any:
    """独立の経路が示した文書の版の属性（#5044）を、同じ文書・保存版の根拠にも付ける（#5444）。"""
    found = identities.get(_position(record, "")[:2])
    if not found or record.metadata.get("source_identities"):
        return record
    return replace(record, metadata={**record.metadata, "source_identities": found})


def _identified(context: AnswerContext, additional: Sequence[AnswerRecord]) -> AnswerContext:
    """版の属性は文書の属性。重なりで足さない原文の属性を、通常検索が拾った同じ文書の根拠へ写す。"""
    identities = {
        _position(record, "")[:2]: record.metadata["source_identities"] for record in additional if record.metadata.get("source_identities")
    }
    if not identities:
        return context
    return replace(
        context,
        records=[_with_identities(record, identities) for record in context.records],
        evidence_tree=tuple(
            replace(
                parent,
                record=_with_identities(parent.record, identities),
                children=tuple(replace(child, record=_with_identities(child.record, identities)) for child in parent.children),
            )
            for parent in context.evidence_tree
        ),
    )


def union_sources(context: AnswerContext, additional: Sequence[AnswerRecord]) -> tuple[AnswerContext, int]:
    """通常検索の根拠（親とその子）がまだ含まない原文だけを末尾に足した context と、足した数。"""
    context = _identified(context, additional)
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
