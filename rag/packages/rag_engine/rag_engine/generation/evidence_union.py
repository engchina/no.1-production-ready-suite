"""独立の経路（Wiki。#4941）の原文を、通常検索の根拠に集合として足す。

経路の間で採点のし直し・件数の削減をしない（通常検索の根拠は外さない）。rerank の順位だけは、通常検索の候補と
独立の経路の起点を点数で 1 つの順にする（#5831）。
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
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


def _with_relations(record: Any, relations: dict[tuple[str, str, str], list[Any]]) -> Any:
    """独立の経路が示した原文の間の関係（#5500）を、同じ原文（位置・保存版）を含む根拠にも付ける。"""
    found = list(record.metadata.get("wiki_relations") or ())
    for chunk_id in (record.chunk_id, *record.child_chunk_ids):
        for relation in relations.get(_position(record, chunk_id), ()) if chunk_id else ():
            if relation not in found:
                found.append(relation)
    if found == list(record.metadata.get("wiki_relations") or ()):
        return record
    return replace(record, metadata={**record.metadata, "wiki_relations": found})


def _identified(context: AnswerContext, additional: Sequence[AnswerRecord]) -> AnswerContext:
    """重なりで足さない原文の属性を、通常検索が拾った根拠へ写す。

    版の属性は文書の属性なので同じ文書・保存版の根拠へ、原文の間の関係は同じ原文を含む根拠へ写す。
    """
    identities = {
        _position(record, "")[:2]: record.metadata["source_identities"] for record in additional if record.metadata.get("source_identities")
    }
    relations = {
        _position(record, record.chunk_id): record.metadata["wiki_relations"]
        for record in additional
        if record.chunk_id and record.metadata.get("wiki_relations")
    }
    if not identities and not relations:
        return context
    return _mapped(context, lambda record: _with_relations(_with_identities(record, identities), relations))


def _mapped(context: AnswerContext, change: Callable[[Any], Any]) -> AnswerContext:
    """根拠の record（並びと木の親・子）を同じ規則で置き換えた context。"""
    return replace(
        context,
        records=[change(record) for record in context.records],
        evidence_tree=tuple(
            replace(
                parent,
                record=change(parent.record),
                children=tuple(replace(child, record=change(child.record)) for child in parent.children),
            )
            for parent in context.evidence_tree
        ),
    )


def _rerank_score(record: Any) -> float | None:
    rerank = record.metadata.get("rerank")
    score = rerank.get("relevance_score") if isinstance(rerank, dict) else None
    return float(score) if isinstance(score, (int, float)) and not isinstance(score, bool) else None


def _ranked_together(context: AnswerContext, added: list[AnswerRecord]) -> tuple[AnswerContext, list[AnswerRecord]]:
    """通常検索の候補と独立の経路の起点を、rerank の点数で 1 つの順位にする（#5831）。

    独立の経路の起点（原文で測った点数を持つ原文）の順位は、点数がそれ以上の通常検索の候補と、点数が上の起点の
    数の次。通常検索の根拠の順位は、点数が上の起点の数だけ下げる（同じ点数は通常検索が先）。点数の無い原文
    （辿った単位）は順位を持たない。点数・並び・件数は変えない。
    """
    roots = sorted(
        ((score, index) for index, record in enumerate(added) if (score := _rerank_score(record)) is not None),
        key=lambda item: (-item[0], item[1]),
    )
    if not roots:
        return context, added
    ordinary = [score for record in context.ranked_candidates if (score := _rerank_score(record)) is not None]
    ranked = list(added)
    for position, (score, index) in enumerate(roots):
        rerank = dict(added[index].metadata["rerank"])
        rerank["rank"] = 1 + sum(value >= score for value in ordinary) + position
        ranked[index] = replace(added[index], metadata={**added[index].metadata, "rerank": rerank})

    def lowered(record: Any) -> Any:
        score = _rerank_score(record)
        rank = record.metadata["rerank"].get("rank") if score is not None else None
        if score is None or not isinstance(rank, int):
            return record
        ahead = sum(value > score for value, _ in roots)
        if not ahead:
            return record
        return replace(record, metadata={**record.metadata, "rerank": {**record.metadata["rerank"], "rank": rank + ahead}})

    return _mapped(context, lowered), ranked


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
    context, added = _ranked_together(context, added)
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
