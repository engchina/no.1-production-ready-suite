"""Excel の読み方（pr_backend_core.spreadsheet。#2525）の検証。

テストのデータはすべて合成（ラベル・値は匿名の「項目A」「値A」）。RAG の前処理の変換
（JSONL）は rag/services/preprocess/excel_to_jsonl のテストが確かめる。
"""

from __future__ import annotations

import io
import subprocess
import sys
from typing import Any

import openpyxl
import pytest
from openpyxl.styles import PatternFill
from openpyxl.utils import get_column_letter
from openpyxl.worksheet.worksheet import Worksheet
from pydantic import ValidationError

from pr_backend_core.spreadsheet import (
    ExcelOptions,
    SheetRecords,
    column_display_name,
    openpyxl_available,
    read_excel,
)

_FILL = PatternFill("solid", fgColor="DDDDDD")


def _bytes(workbook: openpyxl.Workbook) -> bytes:
    buffer = io.BytesIO()
    workbook.save(buffer)
    return buffer.getvalue()


def _put(worksheet: Worksheet, ref: str, value: Any, *, fill: bool = False) -> None:
    first = ref.split(":", 1)[0]
    worksheet[first] = value
    if ":" in ref:
        worksheet.merge_cells(ref)
    if fill:
        worksheet[first].fill = _FILL


def _sheets(source: bytes, **options: Any) -> list[SheetRecords]:
    result = read_excel(source, options=options)
    assert result.failure is None, result.failure
    assert result.document is not None
    return result.document.sheets


def test_importing_the_package_does_not_import_openpyxl() -> None:
    """openpyxl / xlrd は使う側が入れる（関数の中で読む）。"""
    code = (
        "import sys, pr_backend_core.spreadsheet as s; "
        "print('openpyxl' in sys.modules, 'xlrd' in sys.modules)"
    )
    output = subprocess.run(
        [sys.executable, "-c", code], check=True, capture_output=True, text=True
    ).stdout
    assert output.strip() == "False False"
    assert openpyxl_available() is True


def test_table_sheet_keeps_header_rows_and_cell_ranges() -> None:
    workbook = openpyxl.Workbook()
    worksheet = workbook.active
    assert worksheet is not None
    worksheet.title = "表"
    worksheet.append(["列A", "列B", "列C"])
    for index in range(3):
        worksheet.append([f"a{index}", index, index * 10])
    (sheet,) = _sheets(_bytes(workbook))
    assert sheet.mode == "table"
    assert sheet.header_row == 1
    assert [column.name for column in sheet.columns] == ["列A", "列B", "列C"]
    assert [(block.cell_range, block.values) for block in sheet.blocks[:1]] == [
        ("A2:C2", {"列A": "a0", "列B": "0", "列C": "0"})
    ]
    assert sheet.layout_detection is not None
    assert sheet.layout_detection.layout == "table"


def test_procedure_sheet_groups_steps() -> None:
    workbook = openpyxl.Workbook()
    worksheet = workbook.active
    assert worksheet is not None
    worksheet.title = "手順"
    worksheet.append(["", "作業項目", "作業内容"])
    worksheet.append([1, "準備", None])
    worksheet.append([1.1, "項目A", "内容A"])
    worksheet.append([None, None, "内容A の続き"])
    worksheet.append([1.2, "項目B", "内容B"])
    (sheet,) = _sheets(_bytes(workbook))
    assert sheet.mode == "procedure"
    assert [(block.section_path, block.row_start, block.row_end) for block in sheet.blocks] == [
        (["準備", "項目A"], 3, 4),
        (["準備", "項目B"], 5, 5),
    ]


def test_form_sheet_is_detected_and_read_as_label_value_fields() -> None:
    workbook = openpyxl.Workbook()
    worksheet = workbook.active
    assert worksheet is not None
    worksheet.title = "様式"
    for col in range(1, 31):
        worksheet.column_dimensions[get_column_letter(col)].width = 2.5
    _put(worksheet, "B1:AC1", "様式A")
    _put(worksheet, "B3:E3", "項目A", fill=True)
    _put(worksheet, "F3:N3", "値A")
    _put(worksheet, "P3:S3", "項目B", fill=True)
    _put(worksheet, "T3:AC3", "値B")
    _put(worksheet, "B4:E4", "項目C", fill=True)
    _put(worksheet, "F4:AC4", "値C")
    (sheet,) = _sheets(_bytes(workbook))
    assert sheet.mode == "form"
    assert sheet.layout_detection is not None
    assert sheet.layout_detection.method == "detected"
    assert sheet.layout_detection.confidence >= 0.6
    assert [
        (block.label, block.value, block.label_cell, block.value_cell, block.section_path)
        for block in sheet.blocks
    ] == [
        ("項目A", "値A", "B3:E3", "F3:N3", ["様式A"]),
        ("項目B", "値B", "P3:S3", "T3:AC3", ["様式A"]),
        ("項目C", "値C", "B4:E4", "F4:AC4", ["様式A"]),
    ]


def test_sheet_with_two_tables_is_split() -> None:
    workbook = openpyxl.Workbook()
    worksheet = workbook.active
    assert worksheet is not None
    worksheet.title = "表"
    worksheet.append(["表の題名1"])
    worksheet.append(["列A", "列B"])
    worksheet.append(["a1", 1])
    worksheet.append([])
    worksheet.append([])
    worksheet.append(["項目X", "項目Y", "項目Z"])
    worksheet.append(["x1", "y1", "z1"])
    sheets = _sheets(_bytes(workbook))
    assert [(sheet.table_index, sheet.region, sheet.title) for sheet in sheets] == [
        (1, "A1:B3", "表の題名1"),
        (2, "A6:C7", None),
    ]
    assert [column.name for column in sheets[1].columns] == ["項目X", "項目Y", "項目Z"]
    assert len(_sheets(_bytes(workbook), split_tables=False)) == 1


def test_options_are_validated_and_bad_input_degrades() -> None:
    with pytest.raises(ValidationError):
        read_excel(b"x", options={"mode": "unknown"})
    assert ExcelOptions().mode == "auto"
    assert read_excel(b"").failure == "excel_empty"
    assert read_excel(b"not an excel file", extension="xlsx").failure == "excel_open_failed"


def test_xls_is_read_with_widths_and_label_hints() -> None:
    import xlwt  # type: ignore[import-untyped]  # .xls の見本を作る（dev の依存）

    book = xlwt.Workbook()
    sheet = book.add_sheet("旧様式")
    for col in range(30):
        sheet.col(col).width = 256 * 2
    label = xlwt.easyxf("pattern: pattern solid, fore_colour ice_blue; font: bold on")
    sheet.write_merge(2, 2, 1, 5, "項目A", label)
    sheet.write_merge(2, 2, 6, 28, "値A", xlwt.easyxf(""))
    sheet.write_merge(3, 3, 1, 5, "項目B", label)
    sheet.write_merge(3, 3, 6, 10, 25, xlwt.easyxf(""))
    sheet.write_merge(3, 3, 11, 15, "項目C", label)
    sheet.write_merge(3, 3, 16, 28, "値C", xlwt.easyxf(""))
    buffer = io.BytesIO()
    book.save(buffer)
    (records,) = _sheets(buffer.getvalue())
    assert records.mode == "form"
    assert [(block.label, block.value) for block in records.blocks] == [
        ("項目A", "値A"),
        ("項目B", "25"),
        ("項目C", "値C"),
    ]


def test_column_display_name_marks_value_columns_only() -> None:
    assert column_display_name("既定値", "default") == "既定値［資料の既定値］"
    assert column_display_name("説明", "definition") == "説明"
    assert column_display_name("列A", None) == "列A"
