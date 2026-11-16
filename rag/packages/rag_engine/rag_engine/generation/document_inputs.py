"""選択順位・文書の版・原文位置を保つ生成入力の並べ方。"""

import math
from collections.abc import Collection, Mapping, Sequence
from typing import Any

# 文書の先頭ページの背景（引用の対象外の補助の原文）の span の印。
BACKGROUND_ONLY = "document_background_only"
# 根拠に入った文書の本文の先頭の chunk の span の印（#5240）。生成・分割読取の各組・最終生成の入力に、その文書の根拠と
# 一緒に必ず入れる（``with_first_chunks``）。
FIRST_CHUNK = "document_first_chunk"


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
        # 位置未記録の入力をページ番号や表示名から推測で並べ替えない。先頭ページの背景と本文の先頭の chunk は、
        # 原文の位置にかかわらずその文書の根拠の先頭に置く（#5240）。
        if all(value.get("source_order") is not None for value in members):
            members = sorted(members, key=lambda value: (_lead(value), value["source_order"], value.get("start", 0)))
        else:
            members = sorted(members, key=_lead)
        result.append(members)
    return result


def _lead(span: Mapping[str, Any]) -> int:
    return 0 if span.get(BACKGROUND_ONLY) else 1 if span.get(FIRST_CHUNK) else 2


def order_document_spans(spans: Sequence[dict[str, Any]], *, priority: Mapping[tuple[str, ...], int] | None = None) -> list[dict[str, Any]]:
    return [value for members in document_groups(spans, priority=priority) for value in members]


def document_priority(
    spans: Sequence[dict[str, Any]], source_scores: Mapping[str, float | None] | None, *, first: Collection[str] = ()
) -> dict[tuple[str, ...], int]:
    """選択済みChunkの原質問スコアの最高値で並べる。``first`` の出典を含む文書は、点数より先に置く。"""
    groups = document_groups(spans)

    def rank(members: list[dict[str, Any]]) -> tuple[bool, int, float]:
        scores = [
            score
            for value in members
            if (score := (source_scores or {}).get(str(value.get("source_id") or ""))) is not None
            and not isinstance(score, bool)
            and math.isfinite(score)
        ]
        leading = any(str(value.get("source_id") or "") in first for value in members)
        return (not leading, *((0, -max(scores)) if scores else (1, 0.0)))

    ordered = sorted(groups, key=rank)
    return {document_key(members[0]): index for index, members in enumerate(ordered)}


def with_first_chunks(values: Sequence[dict[str, Any]], firsts: Sequence[dict[str, Any]] | None = None) -> list[dict[str, Any]]:
    """``values`` の本文に、そこに出てくる文書の本文の先頭の chunk（``FIRST_CHUNK``）を足す（#5240）。

    ``firsts`` は先頭の chunk の全文の span（None は ``values`` の中の印の付いた span）。``values`` の中の先頭の chunk と
    その抜粋はいったん外し、同じ文書の本文がその原文を含まなければ全文を足し直す（同じ原文を 2 回渡さない）。
    抜粋に付いた分割読取の回答候補は、足し直した全文へ移す。
    """
    pool = [value for value in (values if firsts is None else firsts) if value.get(FIRST_CHUNK)]
    main = [value for value in values if not value.get(FIRST_CHUNK)]
    result = list(main)
    added: set[tuple[Any, ...]] = set()
    for key in dict.fromkeys(document_key(value) for value in values):
        for first in pool:
            identity = (first.get("source_id"), first.get("start", 0), first.get("end"))
            if document_key(first) != key or identity in added:
                continue
            added.add(identity)
            if any(document_key(value) == key and first["text"] in value["text"] for value in main):
                continue
            answers = [
                str(value["batch_answer"])
                for value in values
                if value.get(FIRST_CHUNK)
                and value.get("batch_answer")
                and value.get("source_id") == first.get("source_id")
                and value.get("start", 0) >= first.get("start", 0)
                and value.get("end", len(value["text"])) <= first.get("end", len(first["text"]))
            ]
            result.append({**first, **({"batch_answer": "\n".join(dict.fromkeys(answers))} if answers else {})})
    return result
