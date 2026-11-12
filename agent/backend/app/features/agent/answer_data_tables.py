"""保存済みの行列参照だけから、部分表の値と見出しを展開する。"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

_RULE = re.compile(r"\s*:?-+:?\s*")
_PIPE = re.compile(r"(?<!\\)(?:\\\\)*\|")


class ReferenceTableError(ValueError):
    pass


@dataclass(frozen=True)
class BoundCell:
    table: str
    row: int
    column: str
    value: Any
    unit: str | None


def unit_header(column: str, unit: str | None) -> str:
    """同じ単位の括弧接尾辞がある表示名には重ねて付けない。"""
    if unit is None or column.endswith((f"({unit})", f"（{unit}）")):
        return column
    return f"{column} ({unit})"


def _parts(line: str) -> list[str]:
    text = line.strip().removeprefix("|").removesuffix("|")
    return [part.strip() for part in re.split(r"(?<!\\)\|", text)]


def render_reference_tables(
    answer: str,
    *,
    resolve: Callable[[str], BoundCell],
    render_scalar: Callable[[str], str],
    markdown: Callable[[Any], str],
    is_reference_data: Callable[[str], bool],
) -> str:
    lines = answer.splitlines()
    rendered: list[str] = []
    index = 0
    while index < len(lines):
        if index + 1 < len(lines):
            rule = _parts(lines[index + 1])
            table_syntax = len(rule) > 1 or bool(
                _PIPE.search(lines[index]) or _PIPE.search(lines[index + 1])
            )
            if table_syntax and rule and all(_RULE.fullmatch(part) for part in rule):
                end = index + 2
                body: list[list[str]] = []
                while end < len(lines) and "|" in lines[end] and lines[end].strip():
                    body.append(_parts(lines[end]))
                    end += 1
                if any(is_reference_data(part) for row in body for part in row):
                    rendered.append(_matrix(_parts(lines[index]), body, resolve, markdown))
                    index = end
                    continue
        # 展開済みの値をもう一度参照として解釈しない。
        rendered.append(render_scalar(lines[index]))
        index += 1
    return "\n".join(rendered)


def _matrix(
    headers: list[str],
    rows: list[list[str]],
    resolve: Callable[[str], BoundCell],
    markdown: Callable[[Any], str],
) -> str:
    columns: list[tuple[str, str]] | None = None
    names: list[str] = []
    rendered: list[list[str]] = []
    seen_rows: set[tuple[str, int]] = set()
    for row in rows:
        if len(row) != len(headers):
            raise ReferenceTableError("参照の表は各行の列数をそろえてください。")
        bound = [resolve(part) for part in row]
        identities = {(cell.table, cell.row) for cell in bound}
        if len(identities) != 1:
            raise ReferenceTableError("同じ表示行には同じ結果の同じ行の参照を使用してください。")
        identity = next(iter(identities))
        if identity in seen_rows:
            raise ReferenceTableError("同じ元行を表に重ねて指定しないでください。")
        seen_rows.add(identity)
        current = [(cell.table, cell.column) for cell in bound]
        if len(set(current)) != len(current):
            raise ReferenceTableError("同じ元列を表に重ねて指定しないでください。")
        if columns is None:
            columns = current
            names = [unit_header(cell.column, cell.unit) for cell in bound]
        elif columns != current:
            raise ReferenceTableError("同じ表示列には同じ元列の参照を使用してください。")
        rendered.append([markdown(cell.value) for cell in bound])
    return "\n".join(
        [
            "| " + " | ".join(markdown(name) for name in names) + " |",
            "| " + " | ".join("---" for _name in names) + " |",
            *("| " + " | ".join(row) + " |" for row in rendered),
        ]
    )
