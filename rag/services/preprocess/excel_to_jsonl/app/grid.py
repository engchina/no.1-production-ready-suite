"""Excel のシートのセルの格子（読み取りの結果。表・手順書・帳票の読み方が共通に使う）。"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

# 列幅を指定していないシートの既定の列幅（Excel の標準。文字数の単位）。
DEFAULT_COLUMN_WIDTH = 8.43
_NUMERIC_TEXT = re.compile(r"^[+-]?[\d,]+(\.\d+)?%?$|^\d{4}-\d{2}-\d{2}([ T][\d:.]+)?$")


@dataclass
class Grid:
    """1 シートのセル（1 始まりの行・列 → 表示の文字列）。

    ``column_widths`` は列幅を指定した列（文字数の単位）、``label_hints`` は塗りつぶしか太字の
    セル（帳票のラベルの手がかり。#2516）。
    """

    name: str
    hidden: bool
    cells: dict[tuple[int, int], str] = field(default_factory=dict)
    merges: list[tuple[int, int, int, int]] = field(default_factory=list)
    missing_formula_cells: set[tuple[int, int]] = field(default_factory=set)
    column_widths: dict[int, float] = field(default_factory=dict)
    default_column_width: float = DEFAULT_COLUMN_WIDTH
    label_hints: set[tuple[int, int]] = field(default_factory=set)

    @property
    def max_row(self) -> int:
        return max((row for row, _ in self.cells), default=0)

    def row_columns(self, row: int) -> list[int]:
        return sorted(col for (r, col), value in self.cells.items() if r == row and value.strip())

    def column_width(self, col: int) -> float:
        return self.column_widths.get(col, self.default_column_width)


def column_letter(index: int) -> str:
    letters = ""
    while index > 0:
        index, remainder = divmod(index - 1, 26)
        letters = chr(65 + remainder) + letters
    return letters


def cell_ref(top: int, left: int, bottom: int, right: int) -> str:
    """セルの範囲の表記（1 つのセルは ``B3``、範囲は ``B3:E3``）。"""
    start = f"{column_letter(left)}{top}"
    if top == bottom and left == right:
        return start
    return f"{start}:{column_letter(right)}{bottom}"


def is_textual(value: str) -> bool:
    """数値・日付でない文字か。"""
    return not _NUMERIC_TEXT.match(value.strip())
