"""Wiki の検索の計画に渡す短い ID と、選択したファイルの全原文を覆う章節の候補を作る。"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from app.rag.wiki_models import WikiPage, fingerprint


class Handles:
    """要求内だけの短い ID（D1・U1・S1）。

    原文の ID をモデルへ渡さず、返った ID を候補に照合して戻す。
    """

    def __init__(self) -> None:
        self._short: dict[tuple[str, str], str] = {}
        self._real: dict[str, str] = {}
        self._counts: dict[str, int] = {}

    def short(self, prefix: str, value: str) -> str:
        key = (prefix, value)
        if key not in self._short:
            self._counts[prefix] = self._counts.get(prefix, 0) + 1
            handle = f"{prefix}{self._counts[prefix]}"
            self._short[key] = handle
            self._real[handle] = value
        return self._short[key]

    def real(self, handles: Sequence[str]) -> list[str]:
        return [self._real[handle] for handle in handles]


def interleave(groups: Sequence[Sequence[WikiPage]], limit: int) -> list[WikiPage]:
    """各経路の順位を保って交互に並べる。経路の点数を足さず、同じ単位は最初の 1 回だけ。"""
    seen: set[str] = set()
    result: list[WikiPage] = []
    for index in range(max((len(group) for group in groups), default=0)):
        for group in groups:
            if index < len(group) and group[index].unit.unit_id not in seen:
                seen.add(group[index].unit.unit_id)
                result.append(group[index])
                if len(result) == limit:
                    return result
    return result


def _common_path(group: Sequence[WikiPage]) -> tuple[str, ...]:
    shared: list[str] = []
    for column in zip(*(page.unit.section_path for page in group), strict=False):
        if len(set(column)) != 1:
            break
        shared.append(column[0])
    return tuple(shared)


def section_frontier(
    pages: Sequence[WikiPage], *, limit: int, ranked_ids: Mapping[str, int]
) -> list[tuple[str, list[str], dict[str, Any]]]:
    """選択したファイルの全原文単位を覆う章節の候補を、上限まで細かくする（#5036）。

    文書・保存版ごとの 1 候補から始め、共通の章節の 1 つ下の階層で分ける。
    親に直接属する単位は子の章節と別の候補になる。上位の階層を先に、同じ階層では順位の高い候補を先に分ける。末尾の候補を捨てない。
    返す値は (章節 ID, 単位 ID の一覧, 候補の情報)。
    """
    grouped: dict[tuple[str, str], list[WikiPage]] = {}
    for page in pages:
        grouped.setdefault((page.unit.document_id, page.unit.chunk_set_id), []).append(page)
    frontier = list(grouped.values())
    if len(frontier) > limit:
        raise ValueError("章節の候補の上限は、選択した文書の保存版の数以上にしてください。")

    def rank(group: Sequence[WikiPage]) -> int:
        return min((ranked_ids.get(p.unit.unit_id, len(ranked_ids)) for p in group), default=0)

    while True:
        options = []
        for index, group in enumerate(frontier):
            prefix = _common_path(group)
            children: dict[str | None, list[WikiPage]] = {}
            for page in group:
                path = page.unit.section_path
                children.setdefault(
                    path[len(prefix)] if len(path) > len(prefix) else None, []
                ).append(page)
            if len(children) > 1 and len(frontier) - 1 + len(children) <= limit:
                options.append(((len(prefix), rank(group), index), index, list(children.values())))
        if not options:
            break
        _, index, replacements = min(options, key=lambda option: option[0])
        frontier[index : index + 1] = replacements

    result = []
    for group in frontier:
        first = group[0].unit
        path = list(_common_path(group))
        section_id = fingerprint([first.document_id, first.chunk_set_id, path, first.unit_id])
        result.append(
            (
                section_id,
                [page.unit.unit_id for page in group],
                {
                    "document_id": first.document_id,
                    "path": path,
                    "original_chars": sum(len(page.unit.text) for page in group),
                    "original_units": len(group),
                },
            )
        )
    return result
