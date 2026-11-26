"""検索経路ごとの候補に、1 つの文書が占める数の上限を掛ける(#5854)。

回答の検索は検索文・検索経路(ベクトル・全文。原生方式は 1 回の ``DBMS_HYBRID_VECTOR.SEARCH``)
ごとに ``RAG_RETRIEVAL_QUERY_LIMIT`` 件を取る。chunk を多く持つ文書がその枠の大半を使うと、
別の文書の chunk が rerank の前に候補から落ちる。``RAG_MAX_ANCHORS_PER_DOCUMENT`` は rerank の
後の起点に掛かるので、候補に入らなかった文書は戻せない。

ここでは融合(RRF)の前に、検索経路ごとの一覧に文書ごとの上限を掛ける。上限は DB の検索の中では
掛けない: 原生方式の ``DBMS_HYBRID_VECTOR.SEARCH`` は関数の中で件数を切るので、SQL で文書ごとの
順位(``ROW_NUMBER() OVER (PARTITION BY ...)``)を付けても切られた後の行にしか掛からない。個別 SQL
方式のベクトル検索も、近似の索引は「距離だけの ``ORDER BY`` と ``FETCH APPROX``」のときにしか
使われず、文書ごとの順位を付けると範囲の全行の距離を計算することになる。そこで、上限があるときは
取得件数の ``CANDIDATE_FETCH_FACTOR`` 倍を取り、この関数で文書ごとに数えて取得件数に切る。

- 文書の単位は ``document_id``(同じ文書の複数のレシピの chunk・派生の chunk は同じ文書に数える。
  別々に取り込んだ同じ内容の文書は別の文書。本文が同じ chunk は検索の段で 1 件にまとめ済み)。
- 文書の種類・形式では分けず、数だけで決める。
- 上限に達した文書の chunk は外し、次の順位の別の文書の chunk で埋める。別の文書の chunk で埋め
  きれないときは、外した chunk を元の順位で戻す(取得件数を保つ。1 つの文書だけの範囲で候補を
  減らさない)。
- 並びは検索の順位のまま(残った chunk の順位を詰めない)。融合は chunk の元の順位で計算する。
"""

from __future__ import annotations

import copy
from collections import Counter
from collections.abc import Sequence
from typing import Any

# 上限があるときに取る件数の倍率。取得件数の上限(200)でも 800 件(``FETCH ... FIRST`` の上限
# 2000 の内)。DB から読む行は増えるが、rerank に渡す件数は取得件数のまま。
CANDIDATE_FETCH_FACTOR = 4


def candidate_fetch_limit(query_limit: int, max_per_document: int) -> int:
    """検索経路ごとに DB から取る件数。上限が無い(0)か取得件数以上なら取得件数のまま。"""
    if 0 < max_per_document < query_limit:
        return query_limit * CANDIDATE_FETCH_FACTOR
    return query_limit


def cap_candidates_per_document[Hits: list[Any]](
    hits: Hits, *, max_per_document: int, limit: int
) -> tuple[Hits, dict[str, Any]]:
    """1 本の検索経路の一覧(検索の順位の順)に文書ごとの上限を掛け、``limit`` 件までにする。

    返す一覧は ``hits`` と同じ型・属性(検索の状態)を持つ写し。記録は、上限を掛けない場合の
    上位 ``limit`` 件と比べて外した chunk・代わりに入った chunk(chunk ID → 文書 ID)と、別の文書で
    埋めきれずに戻した chunk の数。
    """
    counts: Counter[str] = Counter()
    kept: list[int] = []
    over: list[int] = []
    for index, hit in enumerate(hits):
        if len(kept) >= limit:
            break
        if counts[hit.document_id] < max_per_document:
            counts[hit.document_id] += 1
            kept.append(index)
        else:
            over.append(index)
    backfilled = over[: max(0, limit - len(kept))]
    capped = copy.copy(hits)
    capped[:] = [hits[index] for index in sorted([*kept, *backfilled])]
    selected_ids = {hit.chunk_id for hit in capped}
    baseline_ids = {hit.chunk_id for hit in hits[:limit]}
    return capped, {
        "fetched": len(hits),
        "kept": len(capped),
        "excluded": {
            hit.chunk_id: hit.document_id
            for hit in hits[:limit]
            if hit.chunk_id not in selected_ids
        },
        "added": {
            hit.chunk_id: hit.document_id for hit in capped if hit.chunk_id not in baseline_ids
        },
        "backfilled_count": len(backfilled),
    }


def summarize_candidate_caps(
    *,
    max_per_document: int,
    query_limit: int,
    fetch_limit: int,
    lists: Sequence[tuple[int, str, dict[str, Any]]],
    capped_hits: Sequence[Sequence[Any]],
) -> dict[str, Any]:
    """検索経路ごとの記録を、回答の診断(``candidate_fusion``)に残す形にまとめる。

    ``lists`` は (検索文の番号, 検索経路, ``cap_candidates_per_document`` の記録) で、
    ``capped_hits`` は同じ順の上限を掛けた後の一覧。融合の候補から外れた chunk は、どの検索経路にも
    残らなかったものだけを数える(別の経路に残った chunk は融合に入る)。代わりに入った chunk は、
    上限を掛けない場合にどの経路にも無かったものだけを数える。
    """
    kept: set[str] = set()
    baseline: set[str] = set()
    excluded: dict[str, str] = {}
    added: dict[str, str] = {}
    for (_index, _channel, record), hits in zip(lists, capped_hits, strict=True):
        ids = {hit.chunk_id for hit in hits}
        kept |= ids
        # 上限を掛けない場合のこの経路の上位(残った chunk - 代わりに入った chunk + 外した chunk)。
        baseline |= (ids - record["added"].keys()) | record["excluded"].keys()
        excluded.update(record["excluded"])
        added.update(record["added"])
    dropped = {chunk_id: doc for chunk_id, doc in excluded.items() if chunk_id not in kept}
    entered = {chunk_id: doc for chunk_id, doc in added.items() if chunk_id not in baseline}
    return {
        "max_per_document": max_per_document,
        "query_limit": query_limit,
        "fetch_limit": fetch_limit,
        "excluded_count": len(dropped),
        "excluded_by_document": _by_document(dropped),
        "excluded_ids": sorted(dropped),
        "added_count": len(entered),
        "added_by_document": _by_document(entered),
        "lists": [
            {
                "query_index": index,
                "channel": channel,
                "fetched": record["fetched"],
                "kept": record["kept"],
                "excluded_count": len(record["excluded"]),
                "excluded_by_document": _by_document(record["excluded"]),
                "added_count": len(record["added"]),
                "added_by_document": _by_document(record["added"]),
                "backfilled_count": record["backfilled_count"],
            }
            for index, channel, record in lists
        ],
    }


def _by_document(documents: dict[str, str]) -> dict[str, int]:
    """chunk ID → 文書 ID から、文書ごとの数(多い順)。"""
    return dict(Counter(documents.values()).most_common())
