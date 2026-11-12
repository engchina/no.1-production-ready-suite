"""独立した原文の集合和。経路間の採点・再ランキング・件数削減はしない。"""

import json
from collections.abc import Sequence
from dataclasses import replace

from rag_engine.generation.answer_models import AnswerContext, AnswerRecord
from rag_engine.retrieval.context_builder import ContextChildEvidence, ContextParentEvidence


def source_identity(record: AnswerRecord) -> str:
    """原文の保存版と位置が一致したものだけを重複とする。"""
    return json.dumps(
        [
            record.metadata.get("document_id") or record.source_run_id or record.source,
            record.metadata.get("chunk_set_id") or record.source_run_id,
            record.chunk_uid or record.chunk_id or record.id,
            record.source_seq_ranges,
            record.source_record_refs,
        ],
        sort_keys=True,
        ensure_ascii=False,
    )


def union_sources(context: AnswerContext, additional: Sequence[AnswerRecord]) -> AnswerContext:
    if not additional:
        return context
    records = list(context.records)
    indexes = {source_identity(record): i for i, record in enumerate(records)}
    additions = []
    for record in additional:
        key = source_identity(record)
        if key in indexes:
            i = indexes[key]
            records[i] = replace(records[i], metadata={**records[i].metadata, "wiki_source": True})
        else:
            indexes[key] = len(records)
            records.append(record)
            additions.append(record)
    by_identity = {source_identity(record): record for record in records}
    tree = tuple(
        replace(item, record=by_identity.get(source_identity(item.record), item.record)) for item in context.evidence_tree
    ) + tuple(
        ContextParentEvidence(
            record=record,
            role="child_fallback",
            reason="Wiki が参照した現在の原文",
            children=(ContextChildEvidence(record=record, role="retrieved_anchor", reason="Wiki が参照した原文"),),
        )
        for record in additions
    )
    return replace(
        context,
        records=records,
        text="\n\n".join(record.body_text or record.text for record in records),
        evidence_tree=tree,
        status="ready",
        insufficient_reason="",
        candidate_count=max(context.candidate_count, len(records)),
    )
