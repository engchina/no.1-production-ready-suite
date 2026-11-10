"""回答の表（列名と行）を Markdown の表にする（#4796）。

生成のモデルは表を列名と行の配列で返し（``answer_parts_wire`` の table 型）、区切りの行・``|`` の位置・改行は
コードが決める。モデルに表の記法を書かせると、区切りの行の無い行や 1 段落に詰めた行になり、表として表示されない。
"""

from __future__ import annotations

from collections.abc import Sequence

from rag_engine.models.llm import AnswerTable


def _cell(value: str) -> str:
    """セルの値。セルの中の改行は空白にし、``|`` は表の区切りにならないよう ``\\|`` にする。"""
    return " ".join(value.split()).replace("|", "\\|")


def _row(cells: Sequence[str]) -> str:
    return "| " + " | ".join(_cell(cell) for cell in cells) + " |"


def table_markdown(table: AnswerTable) -> str:
    """GFM の表（表頭の行・区切りの行・本文の行）。足りないセルは空にする。"""
    width = len(table.columns)
    lines = [_row(table.columns), "| " + " | ".join("---" for _ in range(width)) + " |"]
    lines.extend(_row([*row.cells, *[""] * (width - len(row.cells))][:width]) for row in table.rows)
    return "\n".join(lines)


def table_cells(table: AnswerTable | None) -> list[str]:
    """表の列名とセルの文字（内部の番号の検査・表示 ID の置き換えが読む）。"""
    if table is None:
        return []
    return [*table.columns, *(cell for row in table.rows for cell in row.cells)]
