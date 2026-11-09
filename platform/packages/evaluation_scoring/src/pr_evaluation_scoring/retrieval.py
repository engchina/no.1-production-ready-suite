"""検索の指標（正解の文書の順位・取れた文書）。"""

from __future__ import annotations

import math
from collections.abc import Collection, Iterable


def unique_in_order(values: Iterable[str]) -> list[str]:
    """重複を除き、初めて現れた順を保つ。"""
    return list(dict.fromkeys(values))


def reciprocal_rank(retrieved_ids: Iterable[str], relevant_ids: Collection[str]) -> float:
    """最初に現れた正解の文書の順位の逆数（無ければ 0）。"""
    for index, document_id in enumerate(retrieved_ids, start=1):
        if document_id in relevant_ids:
            return 1.0 / index
    return 0.0


def document_hits(retrieved_ids: Iterable[str], relevant_ids: Collection[str]) -> list[str]:
    """取れた正解の文書（取れた順・重複なし）。"""
    return unique_in_order(item for item in retrieved_ids if item in relevant_ids)


def precision_at_k(retrieved_ids: Iterable[str], relevant_ids: Collection[str], k: int) -> float:
    """上位 k 件（重複なし）のうち正解の割合。外れの結果も分母に数える。"""
    ranked = unique_in_order(retrieved_ids)[:k]
    if not ranked:
        return 0.0
    return sum(item in relevant_ids for item in ranked) / len(ranked)


def ndcg_at_k(retrieved_ids: Iterable[str], relevant_ids: Collection[str], k: int) -> float:
    """上位 k 件の NDCG。理想の順位には取れなかった正解も数える。"""
    ranked = unique_in_order(retrieved_ids)[:k]
    gain = sum(1 / math.log2(rank + 2) for rank, item in enumerate(ranked) if item in relevant_ids)
    ideal = sum(1 / math.log2(rank + 2) for rank in range(min(len(set(relevant_ids)), k)))
    return gain / ideal if ideal else 0.0


def average_precision(retrieved_ids: Iterable[str], relevant_ids: Collection[str]) -> float:
    """正解が現れた順位ごとの適合率の和を、正解の数で割った値（1 ケースの MAP）。"""
    relevant_count = len(set(relevant_ids))
    if not relevant_count:
        return 0.0
    hits = 0
    total = 0.0
    for rank, item in enumerate(unique_in_order(retrieved_ids), start=1):
        if item in relevant_ids:
            hits += 1
            total += hits / rank
    return total / relevant_count
