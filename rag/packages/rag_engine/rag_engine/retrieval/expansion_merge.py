"""検索拡張の根拠（参照先・実体・関係情報・目次から選んだ章節）を、最後の根拠の並びへ合わせる規則（#5097・#5162）。

backend は回答の検索で、上位の候補から拡張（交差参照の参照先 #1280・実体の 1 段 #1362・関係情報 #2055・目次から
回答のモデルが選んだ章節 #5324）で chunk を集め、metadata の ``reference_expansion`` / ``entity_expansion`` /
``graph_expansion`` / ``outline_selection`` に足した理由を付け、
rerank の前の候補とは分けて渡す。拡張の根拠は rerank にかけず、全体の下限・``top_k``・context の枠に数えない。
rerank・下限・起点の選択が終わって最後の根拠の並びが決まった後に、回答・検索だけ・MCP の 3 つの経路がこの規則で
1 回だけ合わせる。

1. 並びに同じ chunk があるか、本文が並びの親・前後の文脈に含まれる: 印を付けるだけで足さない（呼び出し元が判定する）。
2. 並びに同じ文書の根拠がある: 文書の chunk の順で入れる（同じ文書で番号が小さいもののうち最大のものの後。
   無ければ同じ文書の最小のものの前）。
3. 同じ文書が無い: 最後に置く（複数は文書ごとにまとめ、文書の中は chunk の順）。

主の検索の後に足す補いの検索（語数上限の要求別の補完・業務ガイドの手順ごとの検索。#5173）の根拠も、検索ごとに
rerank・下限・``top_k`` の切りを通した後、検索拡張の後に同じ規則で合わせる（``query_overflow`` / ``guide_step`` の印）。
補いの検索は下限の後・切りの前に、主の検索の根拠にすでにある候補（上の 1. に当たるもの・章節で補う起点の章節に入るもの）
を除くので（#5175）、切った根拠は主の検索の根拠に無いものになる（補いの検索どうしの重なりは 1. で印だけになる）。
同じ chunk かは頁の範囲を除いた key（``context_builder.chunk_identity_key``）で判定する（#5185）。主の検索の根拠
（起点と前後・親の文脈）は外さない（前後の文脈は主の検索の原文だけから選ぶ。#5185）。合わせた後に文脈の補完
（``context_fill``）を 1 回だけ行う。
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from typing import Any

# backend が拡張の根拠の metadata に付ける印のキー（``app.rag.answer_engine._stored_child``）。
REFERENCE_EXPANSION_KEY = "reference_expansion"
ENTITY_EXPANSION_KEY = "entity_expansion"
GRAPH_EXPANSION_KEY = "graph_expansion"
# 検索の上位の文書の目次から回答のモデルが選んだ章節の根拠の印（#5324。backend の ``app.rag.reference_sections``）。
OUTLINE_SELECTION_KEY = "outline_selection"
EXPANSION_KEYS = (REFERENCE_EXPANSION_KEY, ENTITY_EXPANSION_KEY, GRAPH_EXPANSION_KEY, OUTLINE_SELECTION_KEY)
# 補いの検索の根拠の印のキー（#5173）。値は検索の記録（要求 ID・手順 ID・種類など）。
QUERY_OVERFLOW_KEY = "query_overflow"
GUIDE_STEP_KEY = "guide_step"
SUPPLEMENT_KEYS = (QUERY_OVERFLOW_KEY, GUIDE_STEP_KEY)
# 合わせた根拠が並びにある・含まれるときに、並びの根拠へ写す印のキー。
MERGE_MARK_KEYS = (*EXPANSION_KEYS, *SUPPLEMENT_KEYS)
# 最後の並びに新しく足した根拠（検索拡張と補いの検索）の、根拠の木の理由（context_reason）。並びの件数に数えない印に使う。
EXPANSION_EVIDENCE_REASON = "expansion_evidence"


def expansion_kinds(record: Any) -> tuple[str, ...]:
    """根拠に付いた拡張の印のキー（足した拡張が無ければ空）。"""
    metadata = getattr(record, "metadata", None)
    if not isinstance(metadata, dict):
        return ()
    return tuple(key for key in EXPANSION_KEYS if isinstance(metadata.get(key), dict))


def supplement_kinds(record: Any) -> tuple[str, ...]:
    """根拠に付いた補いの検索の印のキー（無ければ空。#5173）。"""
    metadata = getattr(record, "metadata", None)
    if not isinstance(metadata, dict):
        return ()
    return tuple(key for key in SUPPLEMENT_KEYS if isinstance(metadata.get(key), dict))


def is_expansion_record(record: Any) -> bool:
    """検索拡張のどれかで足した根拠か。"""
    return bool(expansion_kinds(record))


def merge_expansions[T](
    ordered: Sequence[T],
    expansions: Sequence[T],
    *,
    key: Callable[[T], str],
    document: Callable[[T], str],
    seq: Callable[[T], int],
) -> list[T]:
    """拡張の根拠を最後の根拠の並びへ入れる（同じ ``key`` がすでに並びにあるものは足さない）。

    同じ文書の根拠があれば文書の chunk の順の位置へ、無ければ最後へ置く。同じ文書の拡張の根拠が複数あれば、
    先に入れた拡張の根拠も同じ文書の根拠として位置を決める（最後に置いた文書の中も chunk の順になる）。
    """
    result = list(ordered)
    present = {key(item) for item in result}
    document_order: dict[str, int] = {}
    for item in expansions:
        document_order.setdefault(document(item), len(document_order))
    pending = sorted(
        (item for item in dict((key(item), item) for item in expansions).values() if key(item) not in present),
        key=lambda item: (document_order[document(item)], seq(item)),
    )
    for item in pending:
        target, number = document(item), seq(item)
        same = [index for index, other in enumerate(result) if document(other) == target]
        if not same:
            result.append(item)
            continue
        before = [index for index in same if seq(result[index]) < number]
        if before:
            position = max(before, key=lambda index: (seq(result[index]), index)) + 1
        else:
            position = min(same, key=lambda index: (seq(result[index]), index))
        result.insert(position, item)
    return result


__all__ = [
    "ENTITY_EXPANSION_KEY",
    "EXPANSION_EVIDENCE_REASON",
    "EXPANSION_KEYS",
    "GRAPH_EXPANSION_KEY",
    "GUIDE_STEP_KEY",
    "MERGE_MARK_KEYS",
    "OUTLINE_SELECTION_KEY",
    "QUERY_OVERFLOW_KEY",
    "REFERENCE_EXPANSION_KEY",
    "SUPPLEMENT_KEYS",
    "expansion_kinds",
    "is_expansion_record",
    "merge_expansions",
    "supplement_kinds",
]
