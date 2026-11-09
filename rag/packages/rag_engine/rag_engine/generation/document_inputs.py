"""選択順位・文書の版・原文位置を保つ生成入力の並べ方。"""

import math
from collections.abc import Mapping, Sequence
from typing import Any

from rag_engine.retrieval.article_catalog import ARTICLE_CATALOG_KEY


def document_key(span: Mapping[str, Any]) -> tuple[str, ...]:
    """表示名だけでは同名の別文書をまとめない。版の範囲が無い入力は出典単位で保つ。"""
    if scope := span.get("document_scope"):
        return tuple(str(value) for value in scope)
    return ("unscoped", str(span.get("source_id") or span.get("evidence_id") or id(span)))


def document_groups(
    spans: Sequence[dict[str, Any]], *, priority: Mapping[tuple[str, ...], int] | None = None
) -> list[list[dict[str, Any]]]:
    """最上位の選択根拠が先にある文書から、各文書の原文順へ戻す。本文と引用IDは変えない。"""
    groups: dict[tuple[str, ...], list[dict[str, Any]]] = {}
    for span in spans:
        groups.setdefault(document_key(span), []).append(span)
    keys = list(groups)
    if priority is not None:
        keys.sort(key=lambda key: priority.get(key, len(priority)))
    result = []
    for key in keys:
        members = groups[key]
        # 位置未記録の入力をページ番号や表示名から推測で並べ替えない。
        if all(value.get("source_order") is not None for value in members):
            members = sorted(members, key=lambda value: (value["source_order"], value.get("start", 0)))
        result.append(members)
    return result


def order_document_spans(spans: Sequence[dict[str, Any]], *, priority: Mapping[tuple[str, ...], int] | None = None) -> list[dict[str, Any]]:
    return [value for members in document_groups(spans, priority=priority) for value in members]


def document_priority(spans: Sequence[dict[str, Any]], source_scores: Mapping[str, float | None] | None) -> dict[tuple[str, ...], int]:
    """選択済みChunkの原質問スコアの最高値で並べ、明示した目録文書の既存位置は保つ。"""
    groups = document_groups(spans)
    catalog = {index for index, members in enumerate(groups) if any(value.get(ARTICLE_CATALOG_KEY) for value in members)}

    def rank(members: list[dict[str, Any]]) -> tuple[int, float]:
        scores = [
            score
            for value in members
            if (score := (source_scores or {}).get(str(value.get("source_id") or ""))) is not None
            and not isinstance(score, bool)
            and math.isfinite(score)
        ]
        return (0, -max(scores)) if scores else (1, 0.0)

    ranked = iter(sorted((members for index, members in enumerate(groups) if index not in catalog), key=rank))
    ordered = [members if index in catalog else next(ranked) for index, members in enumerate(groups)]
    return {document_key(members[0]): index for index, members in enumerate(ordered)}


