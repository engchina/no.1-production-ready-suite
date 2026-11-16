"""引用補充で足す参照先の chunk を決め、関連度の順に並べる。読取や Rerank の呼び出しとは別の責務。

個数の上限で選ばない(#5243)。読めた参照先の chunk はすべて足し、原質問との rerank の点数の高い順に
並べる。回答の生成は、この順(点数の印)で根拠の文字の予算とモデルの入力の予算に入れ、入らない分は
分割読取へ渡す(捨てない)。参照は 1 段だけで、参照先として足した候補は起点として辿らない。
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from math import isfinite
from typing import Literal

type ReferenceOutcome = Literal["selected", "unavailable", "referenced"]


@dataclass(frozen=True)
class ReferenceGroup:
    source_id: str
    source_rank: int
    # 起点の読めた参照先の chunk(参照先の順。既存の起点にある chunk も含む)。
    chunk_ids: tuple[str, ...]
    # 読めた参照先が 1 つも無く、読めなかった理由がある起点。
    unavailable: bool = False


@dataclass(frozen=True)
class ReferenceSelection:
    # 参照先を辿った起点(検索順位の順)。
    sources: tuple[str, ...]
    # 新しく足す chunk(最初に見つけた順。重複と既存の起点・辿った起点を除く)。
    chunk_ids: tuple[str, ...]
    outcomes: dict[str, ReferenceOutcome]


def select_reference_chunks(
    groups: Sequence[ReferenceGroup], *, existing: set[str]
) -> ReferenceSelection:
    """起点を検索順位の順に辿り、読めた参照先の chunk をすべて足す(個数の上限は無い)。

    既に起点として採用した候補(``existing`` と先に辿った起点)は移さない。先の起点の参照先として
    足した候補は、起点として辿らない(参照は 1 段だけ)。読めた参照先が無い起点は ``unavailable``。
    """
    sources: list[str] = []
    chunks: list[str] = []
    added: set[str] = set()
    protected = set(existing)
    outcomes: dict[str, ReferenceOutcome] = {}
    for group in sorted(groups, key=lambda item: item.source_rank):
        if group.source_id in added:
            outcomes[group.source_id] = "referenced"
            continue
        if group.unavailable:
            outcomes[group.source_id] = "unavailable"
            continue
        sources.append(group.source_id)
        protected.add(group.source_id)
        outcomes[group.source_id] = "selected"
        for chunk_id in group.chunk_ids:
            if chunk_id not in protected and chunk_id not in added:
                added.add(chunk_id)
                chunks.append(chunk_id)
    return ReferenceSelection(tuple(sources), tuple(chunks), outcomes)


def relevance_order(chunk_ids: Sequence[str], scores: Mapping[str, float]) -> list[str]:
    """点数の高い順(点数の無いものは後ろ)。同点・点数の無いものは元の順を保つ。"""

    def key(item: tuple[int, str]) -> tuple[int, float, int]:
        index, chunk_id = item
        score = scores.get(chunk_id)
        if score is None or not isfinite(score):
            return (1, 0.0, index)
        return (0, -score, index)

    return [chunk_id for _index, chunk_id in sorted(enumerate(chunk_ids), key=key)]
