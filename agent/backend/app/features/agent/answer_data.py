"""保存したSQL結果の行・列参照を回答へ展開する。値はモデルから受け取らない。"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

_REFERENCE = re.compile(r"\[\[(cell|table):([a-f0-9]{24}|T[1-9]\d*(?::R[1-9]\d*:C[1-9]\d*)?)\]\]")
REGISTRY_KEY = "_answer_data_references"
_NUMBER = re.compile(r"(?<![\w])[-+]?\d+(?:[.,]\d+)*(?![\w])")
_RULE = re.compile(r"\s*:?-+:?\s*")

DATA_INSTRUCTIONS = (
    "## Step 1: 取得した表の値を参照する\n"
    "SQL結果の answer_data にある [[table:T1]] 形式の参照を単独の行に置くと、"
    "取得順の全行・全列の表になります。"
    "表の行・列を変える場合は必要な項目と並び順を指定して照会してください。\n"
    "## Step 2: 数値を本文へ入れる\n"
    "本文の値には cells の対応する [[cell:T1:R2:C3]] 形式の参照を使い、"
    "必要な単位を参照の後に付けてください。"
    "取得した数値の表を手で書き直さないでください。"
)


class DataReferenceError(ValueError):
    pass


def _key(step_id: str, value: Any) -> str:
    encoded = json.dumps([step_id, value], ensure_ascii=False, sort_keys=True, default=str)
    return hashlib.sha256(encoded.encode()).hexdigest()[:24]


def _rows(output: Mapping[str, Any]) -> list[dict[str, Any]]:
    if output.get("status") != "done" or output.get("error_code"):
        return []
    rows = output.get("rows")
    return [row for row in rows if isinstance(row, dict)] if isinstance(rows, list) else []


def _is_result(output: Mapping[str, Any]) -> bool:
    return (
        output.get("status") == "done"
        and not output.get("error_code")
        and isinstance(output.get("rows"), list)
    )


def register_data_result(metadata: dict[str, Any], step_id: str, output: Mapping[str, Any]) -> None:
    """実行の保存 lock 内で登録する。圧縮したメッセージから番号を作り直さない。"""
    if not _is_result(output):
        return
    entries = metadata.setdefault(REGISTRY_KEY, [])
    if not isinstance(entries, list):
        raise DataReferenceError("保存したデータ参照の対応表が不正です。")
    if any(entry["step_id"] == step_id for entry in entries):
        return
    rows = _rows(output)
    entries.append(
        {
            "step_id": step_id,
            "key": _key(step_id, [_column_order(output, rows), rows]),
            "number": max((entry["number"] for entry in entries), default=0) + 1,
        }
    )


def model_data_references(
    step_id: str, output: Mapping[str, Any], *, metadata: Mapping[str, Any] | None = None
) -> dict[str, Any] | None:
    if not _is_result(output):
        return None
    rows = _rows(output)
    columns = _column_order(output, rows)
    key = _key(step_id, [columns, rows])
    number = None
    if metadata is not None:
        number = next(
            (
                entry["number"]
                for entry in metadata.get(REGISTRY_KEY, [])
                if entry["step_id"] == step_id and entry["key"] == key
            ),
            None,
        )
        if number is None:
            raise DataReferenceError("保存した結果のデータ参照が登録されていません。")
    return {
        "table": f"[[table:T{number}]]" if number is not None else "[[table:" + key + "]]",
        "cells": [
            {
                column: (
                    f"[[cell:T{number}:R{index + 1}:C{columns.index(column) + 1}]]"
                    if number is not None
                    else "[[cell:" + _key(step_id, [index, column, value]) + "]]"
                )
                for column, value in row.items()
            }
            for index, row in enumerate(rows)
        ],
    }


def _display(value: Any) -> str:
    if value is None:
        return "NULL"
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value)


def _markdown(value: Any) -> str:
    return (
        _display(value)
        .replace("\\", "\\\\")
        .replace("|", "\\|")
        .replace("\r", " ")
        .replace("\n", " ")
    )


def _column_order(output: Mapping[str, Any], rows: list[dict[str, Any]]) -> list[str]:
    columns = output.get("columns", [])
    names = (
        [column.get("name") if isinstance(column, dict) else column for column in columns]
        if isinstance(columns, list)
        else []
    )
    return list(
        dict.fromkeys(
            [name for name in names if isinstance(name, str)]
            + [column for row in rows for column in row]
        )
    )


def _table(columns: list[str], rows: list[dict[str, Any]]) -> str:
    if not columns:
        return "取得した行はありません。"
    return "\n".join(
        [
            "| " + " | ".join(_markdown(column) for column in columns) + " |",
            "| " + " | ".join("---" for _ in columns) + " |",
            *(
                "| " + " | ".join(_markdown(row.get(column)) for column in columns) + " |"
                for row in rows
            ),
        ]
    )


@dataclass(frozen=True)
class DataIndex:
    cells: dict[str, Any]
    tables: dict[str, tuple[list[str], list[dict[str, Any]]]]

    aliases: dict[str, str] = field(default_factory=dict)

    def render(self, answer: str) -> str:
        remainder = _REFERENCE.sub("", answer)
        if "[[cell:" in remainder or "[[table:" in remainder:
            raise DataReferenceError(
                "データ参照の形式が不正です。取得結果の参照を使用してください。"
            )
        for line in answer.splitlines():
            for marker in _REFERENCE.finditer(line):
                if marker[1] == "table" and line.strip() != marker[0]:
                    raise DataReferenceError("table参照は単独の行に置いてください。")
        if not self.tables:
            if _REFERENCE.search(answer):
                raise DataReferenceError("この実行に無いデータ参照です。")
            return answer
        in_table = False
        for line in answer.splitlines():
            cells = line.strip().strip("|").split("|")
            if len(cells) > 1 and all(_RULE.fullmatch(cell) for cell in cells):
                in_table = True
                continue
            if in_table and "|" in line and line.strip():
                if _NUMBER.search(line) or _REFERENCE.search(line):
                    raise DataReferenceError("数値の表には取得結果のtable参照を使用してください。")
            else:
                in_table = False

        def replace(match: re.Match[str]) -> str:
            kind, key = match.groups()
            key = self.aliases.get(key, key)
            if kind == "table" and key in self.tables:
                return _table(*self.tables[key])
            if kind == "cell" and key in self.cells:
                return _markdown(self.cells[key])
            raise DataReferenceError("取得した行・列に存在しないデータ参照です。")

        return _REFERENCE.sub(replace, answer)


def data_index(steps: Sequence[Any], metadata: Mapping[str, Any] | None = None) -> DataIndex:
    cells: dict[str, Any] = {}
    tables: dict[str, tuple[list[str], list[dict[str, Any]]]] = {}
    aliases: dict[str, str] = {}
    for step in steps:
        result = step.tool_result
        if step.status != "completed" or result is None or not result.success:
            continue
        output = result.output
        if not isinstance(output, dict):
            continue
        if (
            output.get("status") != "done"
            or output.get("error_code")
            or not isinstance(output.get("rows"), list)
        ):
            continue
        rows = _rows(output)
        columns = _column_order(output, rows)
        table_key = _key(step.id, [columns, rows])
        tables[table_key] = (columns, rows)
        number = next(
            (
                entry["number"]
                for entry in (metadata or {}).get(REGISTRY_KEY, [])
                if entry["step_id"] == step.id and entry["key"] == table_key
            ),
            None,
        )
        if number is not None:
            aliases[f"T{number}"] = table_key
        for index, row in enumerate(rows):
            for column, value in row.items():
                cell_key = _key(step.id, [index, column, value])
                cells[cell_key] = value
                if number is not None:
                    aliases[f"T{number}:R{index + 1}:C{columns.index(column) + 1}"] = cell_key
    return DataIndex(cells=cells, tables=tables, aliases=aliases)
