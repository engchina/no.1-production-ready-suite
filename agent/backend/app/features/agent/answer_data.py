"""保存したSQL結果の行・列参照を回答へ展開する。値はモデルから受け取らない。"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Collection, Mapping, Sequence
from dataclasses import dataclass, field
from numbers import Number
from typing import Any

from .answer_data_projection import TableProjectionError, selected_positions
from .answer_data_tables import (
    BoundCell,
    ReferenceTableError,
    data_detection_text,
    render_reference_tables,
)

_REFERENCE = re.compile(
    r"\[\[(cell|quantity|table):((?:[a-f0-9]{24}|T[1-9]\d*)"
    r"(?::R[1-9]\d*:C[1-9]\d*|:rows=[^:\]\r\n]*:cols=[^:\]\r\n]*"
    r"(?::layout=[^:\]\r\n]*)?)?)\]\]"
)
REGISTRY_KEY = "_answer_data_references"
_JSON_NUMBER = re.compile(r"-?(?:0|[1-9][0-9]*)(?:\.[0-9]+)?(?:[eE][+-]?[0-9]+)?\Z")
_NUMBER = re.compile(r"(?<![\w])[-+]?\d+(?:[.,]\d+)*(?![\w])")

DATA_INSTRUCTIONS = (
    "## Step 1: 取得した表を参照する\n"
    "全行・全列はanswer_data.tableを単独行に置いて表示します。"
    "一部の行・列は [[table:T1:rows=1-8:cols=1,2,4]] の投影参照で選びます。"
    "行・列は1始まりで、*は全件、昇順の範囲やカンマ列挙は指定順に表示します。"
    "列番号はcolumn_refsを使います。"
    "render_charsのwholeは全表のMarkdown文字数、columnsは列番号順のheader文字数と"
    "values合計文字数（罫線・区切りを除く）です。画面の字数やtoken数ではありません。"
    "回答の分量に合わせた行列の選択に使います。"
    "項目を縦に並べる表は [[table:T1:rows=1:cols=2-4:layout=vertical]] と指定します。"
    "値・見出し・項目名を手書きせず表の参照を優先してください。\n"
    "## Step 2: 本文の値を参照する\n"
    "cellsの [[cell:T1:R2:C3]] を使って値を表示します。"
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


def _result_key(step_id: str, output: Mapping[str, Any]) -> str:
    rows = _rows(output)
    value: list[Any] = [_column_order(output, rows), rows]
    if output.get("column_metadata"):
        value.append(output["column_metadata"])
    return _key(step_id, value)


def _cell_key(step_id: str, index: int, column: str, value: Any, revision: str | None) -> str:
    identity = [index, column, value]
    if revision is not None:
        identity.append(revision)
    return _key(step_id, identity)


def _column_units(output: Mapping[str, Any], columns: list[str]) -> dict[str, str]:
    metadata = output.get("column_metadata")
    if not metadata:
        return {}
    if not isinstance(metadata, list) or len(metadata) != len(columns):
        raise DataReferenceError("列の型情報が取得結果の列に対応していません。")
    units: dict[str, str] = {}
    for column, entry in zip(columns, metadata, strict=True):
        if not isinstance(entry, dict) or entry.get("name") != column:
            raise DataReferenceError("列の型情報が取得結果の列に対応していません。")
        if entry.get("unit_status") == "known":
            unit = entry.get("unit")
            if not isinstance(unit, str) or not unit.strip():
                raise DataReferenceError("単位が確定した列に単位の値がありません。")
            units[column] = unit
    return units


def _quantity_value(value: Any) -> bool:
    # NL2SQLは精度を失う巨大整数をJSON数値形式の文字列で返す。floatへ戻さない。
    return (
        value is None
        or isinstance(value, Number)
        and not isinstance(value, bool)
        or isinstance(value, str)
        and _JSON_NUMBER.fullmatch(value) is not None
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
    entries.append(
        {
            "step_id": step_id,
            "key": _result_key(step_id, output),
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
    key = _result_key(step_id, output)
    units = _column_units(output, columns)
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
    references: dict[str, Any] = {
        "table": f"[[table:T{number}]]" if number is not None else "[[table:" + key + "]]",
        "columns": columns,
        "column_refs": {column: index + 1 for index, column in enumerate(columns)},
        "row_count": len(rows),
        "render_chars": _render_chars(columns, rows),
        "cells": [
            {
                column: (
                    f"[[cell:T{number}:R{index + 1}:C{columns.index(column) + 1}]]"
                    if number is not None
                    else "[[cell:"
                    + _cell_key(
                        step_id,
                        index,
                        column,
                        value,
                        key if output.get("column_metadata") else None,
                    )
                    + "]]"
                )
                for column, value in row.items()
            }
            for index, row in enumerate(rows)
        ],
    }
    if units:
        references["quantity_columns"] = {
            column: {"column": columns.index(column) + 1, "unit": unit}
            for column, unit in units.items()
        }
    return references


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


def _table(
    columns: list[str], rows: list[dict[str, Any]], *, headers: list[str] | None = None
) -> str:
    if not columns:
        return "取得した行はありません。"
    return "\n".join(
        [
            "| " + " | ".join(_markdown(column) for column in (headers or columns)) + " |",
            "| " + " | ".join("---" for _ in columns) + " |",
            *(
                "| " + " | ".join(_markdown(row.get(column)) for column in columns) + " |"
                for row in rows
            ),
        ]
    )


def _render_chars(columns: list[str], rows: list[dict[str, Any]]) -> dict[str, Any]:
    """表のMarkdownだけをUnicode符号位置で数え、列ごとの合計を付ける。"""
    return {
        "metric": "markdown_codepoints",
        "whole": len(_table(columns, rows)),
        "columns": [
            {
                "header": len(_markdown(column)),
                "values": sum(len(_markdown(row.get(column))) for row in rows),
            }
            for column in columns
        ],
    }


@dataclass(frozen=True)
class DataIndex:
    cells: dict[str, Any]
    tables: dict[str, tuple[list[str], list[dict[str, Any]]]]

    aliases: dict[str, str] = field(default_factory=dict)

    units: dict[str, str] = field(default_factory=dict)
    positions: dict[str, tuple[str, int, str]] = field(default_factory=dict)
    trusted_sources: frozenset[str] = field(default_factory=frozenset)

    def _cell(self, marker: re.Match[str]) -> tuple[str, Any]:
        kind, alias = marker.groups()
        key = self.aliases.get(alias, alias)
        if kind not in {"cell", "quantity"} or key not in self.cells:
            raise DataReferenceError("取得した行・列に存在しないデータ参照です。")
        value = self.cells[key]
        has_unit = key in self.units and _quantity_value(value)
        if kind == "quantity" and not has_unit:
            raise DataReferenceError("単位が確定していない値です。cellsの参照を使用してください。")
        return key, value

    def _bound_cell(self, text: str) -> BoundCell:
        marker = _REFERENCE.fullmatch(text.strip())
        if marker is None or marker[1] == "table":
            raise DataReferenceError("表の各セルにはcellsの参照だけを使用してください。")
        key, value = self._cell(marker)
        if key not in self.positions:
            raise DataReferenceError("表の参照に対応する元の位置がありません。")
        table, row, column = self.positions[key]
        return BoundCell(table, row, column, value, self.units.get(key))

    def _render_table(self, alias: str) -> str:
        base, projection, selectors = alias.partition(":rows=")
        key = self.aliases.get(base, base)
        if key not in self.tables:
            raise DataReferenceError("この実行に無い表の参照です。")
        columns, rows = self.tables[key]
        headers = columns
        if not projection:
            return _table(columns, rows, headers=headers)
        if selectors.count(":cols=") != 1:
            raise DataReferenceError("表の投影はrowsとcolsを指定してください。")
        row_spec, column_spec = selectors.split(":cols=")
        column_spec, explicit_layout, layout = column_spec.partition(":layout=")
        if not explicit_layout:
            layout = "horizontal"
        layout = layout.strip()
        if layout not in {"horizontal", "vertical"}:
            raise DataReferenceError("表のlayoutはhorizontalかverticalを指定してください。")
        try:
            selected_rows = selected_positions(row_spec, len(rows))
            selected_columns = selected_positions(column_spec, len(columns))
        except TableProjectionError as exc:
            raise DataReferenceError(str(exc)) from exc
        if layout == "vertical":
            if not selected_rows or not selected_columns:
                return "取得した行はありません。"
            value_keys = [f"value{index}" for index in range(len(selected_rows))]
            vertical_rows = [
                {
                    "label": headers[column],
                    **{
                        value_key: rows[row].get(columns[column])
                        for value_key, row in zip(value_keys, selected_rows, strict=True)
                    },
                }
                for column in selected_columns
            ]
            return _table(
                ["label", *value_keys],
                vertical_rows,
                headers=["項目", "値"]
                if len(selected_rows) == 1
                else ["項目", *(f"R{row + 1}" for row in selected_rows)],
            )
        return _table(
            [columns[index] for index in selected_columns],
            [rows[index] for index in selected_rows],
            headers=[headers[index] for index in selected_columns],
        )

    def render(self, answer: str) -> str:
        remainder = _REFERENCE.sub("", answer)
        if any(prefix in remainder for prefix in ("[[cell:", "[[quantity:", "[[table:")):
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

        def replace(marker: re.Match[str]) -> str:
            kind, alias = marker.groups()
            if kind == "table":
                return self._render_table(alias)
            _key, value = self._cell(marker)
            return _markdown(value)

        try:
            return render_reference_tables(
                answer,
                resolve=self._bound_cell,
                render_scalar=lambda text: _REFERENCE.sub(replace, text),
                markdown=_markdown,
                is_reference_data=lambda text: bool(
                    _NUMBER.search(data_detection_text(text, trusted_sources=self.trusted_sources))
                    or _REFERENCE.search(text)
                ),
            )
        except ReferenceTableError as exc:
            raise DataReferenceError(str(exc)) from exc


def has_data_reference(text: str) -> bool:
    """回答の形式契約にあるデータ参照を含むか。実在はDataIndexで別途検証する。"""
    return _REFERENCE.search(text) is not None


def data_index(
    steps: Sequence[Any],
    metadata: Mapping[str, Any] | None = None,
    *,
    trusted_sources: Collection[str] = (),
) -> DataIndex:
    cells: dict[str, Any] = {}
    tables: dict[str, tuple[list[str], list[dict[str, Any]]]] = {}
    aliases: dict[str, str] = {}
    units: dict[str, str] = {}
    positions: dict[str, tuple[str, int, str]] = {}
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
        table_key = _result_key(step.id, output)
        column_units = _column_units(output, columns)
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
                cell_key = _cell_key(
                    step.id,
                    index,
                    column,
                    value,
                    table_key if output.get("column_metadata") else None,
                )
                cells[cell_key] = value
                positions[cell_key] = (table_key, index, column)
                if column in column_units:
                    units[cell_key] = column_units[column]
                if number is not None:
                    aliases[f"T{number}:R{index + 1}:C{columns.index(column) + 1}"] = cell_key
    return DataIndex(
        cells=cells,
        tables=tables,
        aliases=aliases,
        units=units,
        positions=positions,
        trusted_sources=frozenset(trusted_sources),
    )
