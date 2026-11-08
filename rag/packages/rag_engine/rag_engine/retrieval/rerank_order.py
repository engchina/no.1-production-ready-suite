"""rerank の後の候補の並び（rerank の順の列と検索の順の列を枠で混ぜる。#4664）。

rerank の点数の順だけで並べると、内容の似た候補が上位を占め、検索の順位が高い候補は rerank の点数が伸びなかった
だけで起点（上位 ``top_k``）から外れる。合格点を通った候補で 2 本の列を作り、1 本に混ぜる。

1. 列 A は rerank の点数の順。列 A の中だけ MMR で、内容の似た候補を後ろに回す（件数は切らない）。
2. 列 B は rerank の前の検索の順位（融合の順）のまま。
3. 「A から 7 件、B からまだ入っていない 3 件」を繰り返す（比率は全体設定 ``RAG_RERANK_SLOT_RATIO``）。

点数は足さない（合成点を作らない）。合格点の比較は rerank の素点のまま呼び出し元で行う。語の分割は全文検索と
同じ部品で、LLM は呼ばない。
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from .attachment_evidence import is_attachment_evidence
from .entity_expansion import is_reserved_entity_expansion
from .reference_follow import reference_source_uids
from .rerank_scores import keeps_original_order, rerank_score
from .text_search_tokenizer import TextSearchTokenizerConfig, content_term_set

# MMR の関連度の重み（1 に近いほど rerank の点数を、0 に近いほど内容の違いを重く見る）。
MMR_LAMBDA = 0.7
DEFAULT_SLOT_RATIO = (7, 3)
COLUMN_RERANK = "rerank"
COLUMN_SEARCH = "search"


def validate_slot_ratio(value: Any) -> tuple[int, int]:
    """枠の比率（列 A の件数・列 B の件数）を検証する。件数なので整数で、少なくとも一方は 1 以上。"""
    if not isinstance(value, (list, tuple)) or len(value) != 2:
        raise ValueError("rerank の後の並びの比率は 2 要素で指定してください。")
    if any(isinstance(count, bool) or not isinstance(count, int) or count < 0 for count in value) or sum(value) <= 0:
        raise ValueError("rerank の後の並びの比率は 0 以上の整数で、少なくとも一方を 1 以上にしてください。")
    return value[0], value[1]


def keeps_position(record: Any) -> bool:
    """枠と MMR の対象にせず、rerank の工程が決めた位置に残す候補か。

    会話の添付・条の目録で選んだ条・実体の拡張で確保した枠・交差参照の参照先は、rerank の点数や検索の順位で
    動かさない規則がある候補。rerank の点数が無い候補は合格点を通っていないので、検索の順位で前へ出さない。
    """
    return (
        rerank_score(record) is None
        or is_attachment_evidence(record)
        or keeps_original_order(record)
        or is_reserved_entity_expansion(record)
        or bool(reference_source_uids(record))
    )


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


def interleave_columns(first: Sequence[int], second: Sequence[int], ratio: tuple[int, int]) -> list[tuple[int, str]]:
    """2 本の列を比率の件数ずつ交互に取り、まだ入っていない位置を 1 本に並べる。列は同じ位置の集合を持つ。"""
    placed: list[tuple[int, str]] = []
    seen: set[int] = set()
    cursors = [0, 0]
    columns = ((first, COLUMN_RERANK, ratio[0]), (second, COLUMN_SEARCH, ratio[1]))
    while len(placed) < len(first):
        for number, (column, label, count) in enumerate(columns):
            taken = 0
            while taken < count and cursors[number] < len(column):
                position = column[cursors[number]]
                cursors[number] += 1
                if position not in seen:
                    seen.add(position)
                    placed.append((position, label))
                    taken += 1
    return placed


def order_reranked_candidates[T](
    records: Sequence[T],
    ratio: tuple[int, int],
    *,
    tokenizer_config: TextSearchTokenizerConfig | None = None,
) -> tuple[list[T], list[str | None]]:
    """合格した候補を枠と MMR で並べ直す。``keeps_position`` の候補は位置を変えない。

    戻り値は並べ直した候補と、位置ごとの出どころの列（``keeps_position`` の候補は None）。
    """
    slots = [index for index, record in enumerate(records) if not keeps_position(record)]
    columns: list[str | None] = [None] * len(records)
    if len(slots) < 2:
        return list(records), columns
    movable = [records[index] for index in slots]
    scores = [float(rerank_score(record) or 0.0) for record in movable]
    # 列 A が使われないときは語の分割を省く。
    terms = (
        [content_term_set(getattr(record, "body_text", "") or getattr(record, "text", ""), tokenizer_config) for record in movable]
        if ratio[0] > 0
        else []
    )
    column_a = mmr_order(scores, terms) if ratio[0] > 0 else list(range(len(movable)))
    column_b = sorted(range(len(movable)), key=lambda position: _search_position(movable[position]))
    result = list(records)
    for slot, (position, label) in zip(slots, interleave_columns(column_a, column_b, ratio), strict=True):
        result[slot] = movable[position]
        columns[slot] = label
    return result, columns


def _search_position(record: Any) -> int:
    """rerank の前の順位（rerank の工程が ``metadata["rerank"]["candidate_index"]`` に残した候補の位置）。"""
    rerank = record.metadata["rerank"]
    return int(rerank["candidate_index"])


__all__ = [
    "COLUMN_RERANK",
    "COLUMN_SEARCH",
    "DEFAULT_SLOT_RATIO",
    "MMR_LAMBDA",
    "interleave_columns",
    "keeps_position",
    "mmr_order",
    "order_reranked_candidates",
    "validate_slot_ratio",
]
