"""回答の表（列名と行）を Markdown の表にする（#4796）。

生成のモデルは表を列名と行の配列で返し（``answer_parts_wire`` の table 型）、区切りの行・``|`` の位置・改行は
コードが決める。モデルに表の記法を書かせると、区切りの行の無い行や 1 段落に詰めた行になり、表として表示されない。

冒頭の回答の表は右端に出典の列を足し、行ごとに表の下の出典行の番号（``[1][2]``）を出す（#4812）。番号はコードが
振り、モデルに書かせない。
"""

from __future__ import annotations

from collections.abc import Sequence

from rag_engine.models.llm import AnswerTable

# 出典の列の番号の印（表の下の出典行の順。1 始まり）。画面はこの形の印を出典行へのリンクにする。
SOURCE_MARK = "[{number}]"


def _cell(value: str) -> str:
    """セルの値。セルの中の改行は空白にし、``|`` は表の区切りにならないよう ``\\|`` にする。"""
    return " ".join(value.split()).replace("|", "\\|")


def _row(cells: Sequence[str]) -> str:
    return "| " + " | ".join(_cell(cell) for cell in cells) + " |"


def source_marks(numbers: Sequence[int]) -> str:
    """行の出典の列の値（出典行の番号を小さい順に重ねずに並べた印）。"""
    return "".join(SOURCE_MARK.format(number=number) for number in sorted(set(numbers)))


def table_markdown(table: AnswerTable, *, source_column: str = "", sources: Sequence[str] = ()) -> str:
    """GFM の表（表頭の行・区切りの行・本文の行）。足りないセルは空にする。

    ``source_column`` を渡すと右端にその名前の列を足し、行ごとに ``sources`` の値（``source_marks``）を入れる。
    """
    width = len(table.columns)
    columns = [*table.columns, source_column] if source_column else list(table.columns)
    lines = [_row(columns), "| " + " | ".join("---" for _ in columns) + " |"]
    for position, row in enumerate(table.rows):
        cells = [*row.cells, *[""] * (width - len(row.cells))][:width]
        if source_column:
            cells.append(sources[position] if position < len(sources) else "")
        lines.append(_row(cells))
    return "\n".join(lines)


def table_cells(table: AnswerTable | None) -> list[str]:
    """表の列名とセルの文字（内部の番号の検査・表示 ID の置き換えが読む）。"""
    if table is None:
        return []
    return [*table.columns, *(cell for row in table.rows for cell in row.cells)]
