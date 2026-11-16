"""rerank の後の候補の並び（rerank の点数の順に、MMR で内容の似た候補を後ろへ回す。#5165）。

rerank の後の並びは rerank の点数だけで決める（検索の順位は混ぜない）。内容の似た候補が上位を占めないよう、
MMR で似た候補を後ろに回す（件数は切らない）。語の分割は全文検索と同じ部品で、LLM は呼ばない。
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from .attachment_evidence import is_attachment_evidence
from .rerank_scores import rerank_score
from .text_search_tokenizer import TextSearchTokenizerConfig, content_term_set

# MMR の関連度の重み（1 に近いほど rerank の点数を、0 に近いほど内容の違いを重く見る）。
MMR_LAMBDA = 0.7


def keeps_position(record: Any) -> bool:
    """MMR の対象にせず、rerank の工程が決めた位置に残す候補か。

    会話の添付は、rerank の点数で動かさない規則がある候補。rerank の点数が無い候補（画像のベクトルだけで当たった
    候補など）は点数で比べられないので動かさない（検索拡張の根拠は rerank の候補に入らない。#5162）。
    """
    return rerank_score(record) is None or is_attachment_evidence(record)


def _term_masks(terms: Sequence[frozenset[str]]) -> list[int]:
    """語の集合を整数のビット列にする（候補の間で同じ語に同じビットを当て、共通の語の数を速く数える）。"""
    numbers: dict[str, int] = {}
    positions = [[numbers.setdefault(term, len(numbers)) for term in group] for group in terms]
    masks: list[int] = []
    for bits in positions:
        mask = bytearray(len(numbers) // 8 + 1)
        for bit in bits:
            mask[bit >> 3] |= 1 << (bit & 7)
        masks.append(int.from_bytes(mask, "little"))
    return masks


def mmr_order(scores: Sequence[float], terms: Sequence[frozenset[str]]) -> list[int]:
    """位置の列を MMR の順に返す。入力は rerank の順で、同点は先の候補を取る。件数は切らない。"""
    remaining = list(range(len(scores)))
    redundancy = [0.0] * len(scores)
    order: list[int] = []
    masks = _term_masks(terms)
    sizes = [len(group) for group in terms]
    while remaining:
        chosen = max(remaining, key=lambda index: MMR_LAMBDA * scores[index] - (1.0 - MMR_LAMBDA) * redundancy[index])
        remaining.remove(chosen)
        order.append(chosen)
        for index in remaining:
            # 語の重なり（Jaccard）。
            shared = (masks[chosen] & masks[index]).bit_count()
            union = sizes[chosen] + sizes[index] - shared
            redundancy[index] = max(redundancy[index], shared / union if union else 0.0)
    return order


def order_reranked_candidates[T](
    records: Sequence[T],
    *,
    tokenizer_config: TextSearchTokenizerConfig | None = None,
) -> list[T]:
    """合格した候補を rerank の点数の順（MMR）に並べ直す。``keeps_position`` の候補は位置を変えない。"""
    slots = [index for index, record in enumerate(records) if not keeps_position(record)]
    if len(slots) < 2:
        return list(records)
    movable = [records[index] for index in slots]
    scores = [float(rerank_score(record) or 0.0) for record in movable]
    terms = [content_term_set(getattr(record, "body_text", "") or getattr(record, "text", ""), tokenizer_config) for record in movable]
    result = list(records)
    for slot, position in zip(slots, mmr_order(scores, terms), strict=True):
        result[slot] = movable[position]
    return result


__all__ = [
    "MMR_LAMBDA",
    "keeps_position",
    "mmr_order",
    "order_reranked_candidates",
]
