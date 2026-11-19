"""選択順位・文書の版・原文位置を保つ生成入力の並べ方。"""

import math
from collections.abc import Collection, Iterable, Mapping, Sequence
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


def _components(count: int, pairs: Iterable[tuple[int, int]]) -> list[list[int]]:
    """``0..count-1`` を ``pairs`` で結んだ連結成分。成分は最小の番号の順、成分の中は番号の順。"""
    parent = list(range(count))

    def root(index: int) -> int:
        while parent[index] != index:
            parent[index] = parent[parent[index]]
            index = parent[index]
        return index

    for left, right in pairs:
        a, b = root(left), root(right)
        if a != b:
            parent[max(a, b)] = min(a, b)
    groups: dict[int, list[int]] = {}
    for index in range(count):
        groups.setdefault(root(index), []).append(index)
    return list(groups.values())


def _contexts(span: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    context = span.get("source_context")
    if not isinstance(context, Mapping):
        return []
    return [context, *(child for child in context.get("children") or () if isinstance(child, Mapping))]


def _place(value: Mapping[str, Any]) -> tuple[str, tuple[str, ...]] | None:
    path = value.get("section_path")
    if not isinstance(path, list | tuple):
        return None
    return str(value.get("document_name") or ""), tuple(str(part) for part in path)


def related_spans(spans: Sequence[Mapping[str, Any]]) -> list[list[int]]:
    """Wiki の辿りで見た原文の間の関係（出典情報の ``wiki_relations``。#5500）で互いに結ばれた根拠の位置の組。

    関係の相手は、関係の文書名と章節が、出典情報（子を含む）の文書名と章節に完全に一致する根拠。一致する根拠が
    複数の文書（``document_key``）にまたがるときは、同名の別文書・別の版と誤って結ばないよう結ばない。関係の無い根拠は
    1 件の組。組は最も前の根拠の順、組の中は与えた順（``wiki_relations`` が無ければ各根拠が与えた順の 1 件の組）。
    """
    places: dict[tuple[str, tuple[str, ...]], set[int]] = {}
    for index, span in enumerate(spans):
        for context in _contexts(span):
            if (place := _place(context)) is not None:
                places.setdefault(place, set()).add(index)
    pairs = []
    for index, span in enumerate(spans):
        for context in _contexts(span):
            for relation in context.get("wiki_relations") or ():
                place = _place(relation) if isinstance(relation, Mapping) else None
                targets = places.get(place, set()) if place is not None else set()
                if len({document_key(spans[target]) for target in targets}) == 1:
                    pairs.extend((index, target) for target in targets)
    return _components(len(spans), pairs)


def related_documents(documents: Sequence[Sequence[dict[str, Any]]]) -> list[list[Sequence[dict[str, Any]]]]:
    """``related_spans`` で結ばれた根拠を含む文書（``document_groups`` の各組）を 1 つの単位にまとめる。

    単位は最も前の文書の位置に置き、単位の中の文書は与えた順。関係が無ければ各文書が与えた順の 1 件の単位。
    """
    owners = [position for position, members in enumerate(documents) for _ in members]
    groups = related_spans([value for members in documents for value in members])
    pairs = [(owners[group[0]], owners[index]) for group in groups for index in group[1:]]
    return [[documents[position] for position in unit] for unit in _components(len(documents), pairs)]


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
