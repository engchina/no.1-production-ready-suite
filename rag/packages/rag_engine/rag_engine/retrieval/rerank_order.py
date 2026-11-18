"""rerank の後の候補の並び（rerank の点数の順。#5165）。

rerank の後の並びは rerank の点数だけで決める（検索の順位は混ぜない。件数は切らない）。MMR は #5443 で削除した。
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from .attachment_evidence import is_attachment_evidence
from .rerank_scores import rerank_score


def keeps_position(record: Any) -> bool:
    """MMR の対象にせず、rerank の工程が決めた位置に残す候補か。

    会話の添付は、rerank の点数で動かさない規則がある候補。rerank の点数が無い候補（画像のベクトルだけで当たった
    候補など）は点数で比べられないので動かさない（検索拡張の根拠は rerank の候補に入らない。#5162）。
    """
    return rerank_score(record) is None or is_attachment_evidence(record)


def order_reranked_candidates[T](records: Sequence[T]) -> list[T]:
    """合格した候補を rerank の点数の順に並べ直す（同点は元の順）。``keeps_position`` の候補は位置を変えない。"""
    slots = [index for index, record in enumerate(records) if not keeps_position(record)]
    movable = sorted((records[index] for index in slots), key=lambda record: -float(rerank_score(record) or 0.0))
    result = list(records)
    for slot, record in zip(slots, movable, strict=True):
        result[slot] = record
    return result


__all__ = [
    "keeps_position",
    "order_reranked_candidates",
]
