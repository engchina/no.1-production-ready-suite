"""Wiki の検索の計画に渡す短い ID と、選択したファイルの全原文を覆う章節の候補を作る。"""

from __future__ import annotations

import re
from collections.abc import Iterable, Sequence

from app.rag.wiki_models import WikiPage


def handle_pattern(handles: Iterable[str]) -> re.Pattern[str]:
    """短い ID と完全に一致する所だけ。前後が英数字・下線の所（``C12`` の中の ``C1``）は除く。"""
    return re.compile(
        r"(?<![0-9A-Za-z_])(?:"
        + "|".join(re.escape(handle) for handle in sorted(handles, key=len, reverse=True))
        + r")(?![0-9A-Za-z_])"
    )


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
