"""引用補充の依存群を、固有 Chunk の予算内で選ぶ。読取や Rerank の Top-N とは別の責務。"""

from __future__ import annotations

from dataclasses import dataclass
from math import isfinite
from typing import Literal


@dataclass(frozen=True)
class ReferenceGroup:
    source_id: str
    source_rank: int
    score: float | None
    chunk_ids: tuple[str, ...]
    # 読取失敗や対象未確認の群を、必要引用の揃った群として採用しない。
    unavailable: bool = False


@dataclass(frozen=True)
class ReferenceSelection:
    sources: tuple[str, ...]
    chunk_ids: frozenset[str]
    outcomes: dict[str, Literal["selected", "budget", "unavailable", "referenced"]]


def select_reference_groups(
    groups: list[ReferenceGroup], *, existing: set[str], budget: int
) -> ReferenceSelection:
    """限界追加量あたりの起点の関連度で選び、同点は検索順位に従う。

    関連度は同じ検索の融合スコアだけを使い、未記録なら順位の逆数を使う。共有引用は
    固有 ID で一度だけ予算を消費し、群を分割しない。選べない群の起点は呼出元に残る。
    既に起点として採用した候補は移さず、参照先として移した候補は再び起点にしない。
    """
    remaining = list(groups)
    sources: list[str] = []
    protected = set(existing)
    chunks: set[str] = set()
    outcomes: dict[str, Literal["selected", "budget", "unavailable", "referenced"]] = {}
    while remaining:
        eligible: list[tuple[float, int, ReferenceGroup, set[str]]] = []
        for group in remaining:
            if group.unavailable or group.source_id in chunks:
                continue
            fresh = set(group.chunk_ids) - chunks - protected
            if len(chunks) + len(fresh) > budget:
                continue
            score = group.score
            utility = (
                score
                if score is not None and isfinite(score) and score > 0
                else 1.0 / max(1, group.source_rank)
            )
            eligible.append((utility / max(1, len(fresh)), -group.source_rank, group, fresh))
        if not eligible:
            break
        _, _, chosen, fresh = max(eligible, key=lambda item: (item[0], item[1]))
        remaining.remove(chosen)
        sources.append(chosen.source_id)
        protected.add(chosen.source_id)
        chunks.update(fresh)
        outcomes[chosen.source_id] = "selected"
    for group in remaining:
        outcomes[group.source_id] = (
            "referenced"
            if group.source_id in chunks
            else "unavailable"
            if group.unavailable
            else "budget"
        )
    return ReferenceSelection(tuple(sources), frozenset(chunks), outcomes)
