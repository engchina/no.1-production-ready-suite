"""Excel→行の記録 JSON 変換の検証（#1221。.xlsx は openpyxl、.xls は xlrd）。

テストのデータはすべて合成（顧客の資料は使わない）。
"""

from __future__ import annotations

import datetime as dt
import io
import json
from types import SimpleNamespace
from typing import Any

import openpyxl
import pytest
from rag_parser_core.sheet_records import SHEET_RECORDS_CONTENT_TYPE

from app import converters
from app.converters import convert


def _workbook(sheets: dict[str, list[list[Any]]]) -> openpyxl.Workbook:
    workbook = openpyxl.Workbook()
    workbook.remove(workbook.active)
    for name, rows in sheets.items():
        worksheet = workbook.create_sheet(title=name)
        for row in rows:
            worksheet.append(row)
    return workbook


def _bytes(workbook: openpyxl.Workbook) -> bytes:
    buffer = io.BytesIO()
    workbook.save(buffer)
    return buffer.getvalue()


def _xlsx_bytes(sheets: dict[str, list[list[Any]]]) -> bytes:
    return _bytes(_workbook(sheets))


def _payload(source: bytes, **options: Any) -> dict[str, Any]:
    outcome = convert(source, "", "excel_to_json", None, options=options)
    assert outcome.converted is True, outcome.warnings
    assert outcome.derived_bytes is not None
    assert outcome.derived_content_type == SHEET_RECORDS_CONTENT_TYPE
    assert outcome.converter_version == "v2"
    payload: dict[str, Any] = json.loads(outcome.derived_bytes.decode("utf-8"))
    return payload


def _sheet(payload: dict[str, Any], index: int = 0) -> dict[str, Any]:
    sheet: dict[str, Any] = payload["sheets"][index]
    return sheet


def test_description_row_before_header_is_detected_as_preamble() -> None:
    """1 行目が説明、2 行目が表頭のシート（handoff の再現条件）。"""
    payload = _payload(
        _xlsx_bytes(
            {
                "コード表": [
                    ["このシートは経費の区分コードの一覧です。"],
                    ["コード", "名称", "区分"],
                    ["A01", "交通費", "旅費"],
                    ["A02", "宿泊費", "旅費"],
                ]
            }
        )
    )
    sheet = _sheet(payload)
    assert payload["format"] == "sheet_records"
    assert payload["source_format"] == "xlsx"
    assert sheet["header_row"] == 2
    assert sheet["header_detection"]["method"] == "detected"
    assert sheet["header_detection"]["confidence"] >= converters.HEADER_LOW_CONFIDENCE
    assert [column["name"] for column in sheet["columns"]] == ["コード", "名称", "区分"]
    assert [column["column"] for column in sheet["columns"]] == ["A", "B", "C"]
    assert sheet["preamble"] == [
        {"row_number": 1, "cell_range": "A1:A1", "text": "このシートは経費の区分コードの一覧です。"}
    ]
    assert sheet["mode"] == "table"
    assert sheet["blocks"][0] == {
        "kind": "row",
        "row_start": 3,
        "row_end": 3,
        "cell_range": "A3:C3",
        "section_path": [],
        "values": {"コード": "A01", "名称": "交通費", "区分": "旅費"},
        "lines": [],
    }
    assert sheet["diagnostics"] == []


def test_configured_header_row_wins_over_detection() -> None:
    payload = _payload(
        _xlsx_bytes({"S": [["説明"], ["a", "b"], ["x", "y"], ["1", "2"]]}), header_row=3
    )
    sheet = _sheet(payload)
    assert sheet["header_row"] == 3
    assert sheet["header_detection"]["method"] == "configured"
    assert [column["name"] for column in sheet["columns"]] == ["x", "y"]
    assert [block["row_start"] for block in sheet["blocks"]] == [4]
    assert [row["row_number"] for row in sheet["preamble"]] == [1, 2]


def test_original_row_numbers_survive_blank_rows() -> None:
    payload = _payload(
        _xlsx_bytes({"S": [["名前", "点"], ["甲", 1], [None, None], ["乙", 2], [], ["丙", 3]]})
    )
    blocks = _sheet(payload)["blocks"]
    assert [block["row_start"] for block in blocks] == [2, 4, 6]
    assert [block["cell_range"] for block in blocks] == ["A2:B2", "A4:B4", "A6:B6"]


def test_values_keep_display_formats() -> None:
    workbook = _workbook(
        {"S": [["コード", "文字のコード", "長い番号", "率", "金額", "小数", "日付", "真偽"]]}
    )
    worksheet = workbook["S"]
    worksheet.append(
        [1, "001", 123456789012345, 0.25, 1234567, 0.1 + 0.2, dt.date(2026, 4, 1), True]
    )
    worksheet["A2"].number_format = "000"
    worksheet["D2"].number_format = "0%"
    worksheet["E2"].number_format = "#,##0"
    values = _sheet(_payload(_bytes(workbook)))["blocks"][0]["values"]
    assert values == {
        "コード": "001",
        "文字のコード": "001",
        "長い番号": "123456789012345",
        "率": "25%",
        "金額": "1,234,567",
        "小数": "0.3",
        "日付": "2026-04-01",
        "真偽": "TRUE",
    }


def test_formula_without_cached_value_is_diagnosed_not_blank() -> None:
    """openpyxl が書いたファイルは数式のキャッシュ値を持たない（計算されていない）。

    空にせず数式の文字列にし（no.1-rag と同じ）、セルの位置付きの診断を残す。
    """
    workbook = _workbook({"S": [["単価", "数量", "金額"], [100, 3, "=A2*B2"]]})
    outcome = convert(_bytes(workbook), "", "excel_to_json", None)
    assert "excel_formula_without_cached_value:S:1" in outcome.warnings
    assert outcome.derived_bytes is not None
    sheet = json.loads(outcome.derived_bytes)["sheets"][0]
    assert sheet["blocks"][0]["values"]["金額"] == "=A2*B2"
    assert sheet["diagnostics"] == [
        {"code": "formula_without_cached_value", "cell": "C2", "detail": None}
    ]


def test_merged_header_and_merged_data_cells() -> None:
    workbook = _workbook(
        {
            "S": [
                ["部門", "期間", None, "区分"],
                [None, "開始", "終了", None],
                ["営業", "4月", "6月", "通期"],
                [None, "7月", "9月", None],
            ]
        }
    )
    worksheet = workbook["S"]
    worksheet.merge_cells("B1:C1")  # 表頭の横の結合
    worksheet.merge_cells("A1:A2")  # 表頭の縦の結合
    worksheet.merge_cells("A3:A4")  # データの縦の結合
    worksheet.merge_cells("D3:E3")  # データの横の結合（同じ行）
    sheet = _sheet(_payload(_bytes(workbook), header_row=1, header_row_count=2, mode="table"))
    assert [column["name"] for column in sheet["columns"]] == [
        "部門",
        "期間 / 開始",
        "期間 / 終了",
        "区分",
        "column_E",
    ]
    blocks = sheet["blocks"]
    assert blocks[0]["values"] == {
        "部門": "営業",
        "期間 / 開始": "4月",
        "期間 / 終了": "6月",
        "区分": "通期",
        "column_E": "通期",
    }
    # 縦の結合の値は後の行へ漏らさない（no.1-rag と同じ）。
    assert blocks[1]["values"] == {"期間 / 開始": "7月", "期間 / 終了": "9月"}


def test_procedure_workbook_groups_steps_with_sections() -> None:
    """手順書（表頭の無い番号の列と、表頭のある最初の列＝題名の列）は、手順ごとに複数行を
    1 block にする（no.1-rag と同じ判定）。"""
    payload = _payload(
        _xlsx_bytes(
            {
                "手順": [
                    [None, "作業項目", "作業内容", "確認ポイント"],
                    ["1", "事前準備", None, None],
                    ["1.1", "アカウントの登録", "管理画面で利用者を追加する", "一覧に表示される"],
                    [None, None, "権限を付ける", None],
                    ["1.2", "通知の設定", "通知先を入れる", "テスト通知が届く"],
                    ["2", "確認", None, None],
                    ["2.1", "動作確認", "ログインする", "トップ画面が出る"],
                ]
            }
        )
    )
    sheet = _sheet(payload)
    assert sheet["mode"] == "procedure"
    blocks = sheet["blocks"]
    assert [block["kind"] for block in blocks] == ["procedure_step"] * 3
    first = blocks[0]
    assert (first["row_start"], first["row_end"], first["cell_range"]) == (3, 4, "A3:D4")
    assert first["section_path"] == ["事前準備", "アカウントの登録"]
    assert first["lines"] == [
        "セクション: 事前準備",
        "column_A: 1.1 | 作業項目: アカウントの登録 | 作業内容: 管理画面で利用者を追加する"
        " | 確認ポイント: 一覧に表示される",
        "作業内容: 権限を付ける",
    ]
    assert blocks[2]["section_path"] == ["確認", "動作確認"]
    # mode="table" を選べば 1 行ずつ読む。
    table = _sheet(
        _payload(
            _xlsx_bytes({"手順": [[None, "作業項目", "内容"], ["1", "a", "x"], ["2", "b", "y"]]}),
            mode="table",
        )
    )
    assert [block["kind"] for block in table["blocks"]] == ["row", "row"]


def test_hidden_sheets_selection_and_excluded_columns() -> None:
    workbook = _workbook(
        {
            "表示": [["名前", "備考", "点"], ["甲", "内部用", 1]],
            "非表示": [["x"], ["1"]],
            "別": [["y"], ["2"]],
        }
    )
    workbook["非表示"].sheet_state = "hidden"
    source = _bytes(workbook)

    default = _payload(source)
    assert [sheet["name"] for sheet in default["sheets"]] == ["表示", "別"]
    assert default["skipped_sheets"] == [{"name": "非表示", "reason": "hidden"}]

    assert [sheet["name"] for sheet in _payload(source, include_hidden_sheets=True)["sheets"]] == [
        "表示",
        "非表示",
        "別",
    ]

    selected = _payload(source, sheets=["表示"], exclude_columns=["備考", "c"])
    assert [sheet["name"] for sheet in selected["sheets"]] == ["表示"]
    sheet = _sheet(selected)
    # 除外した列は列にも値にも出さない（列名でも列の記号でも指定できる）。
    assert [column["name"] for column in sheet["columns"]] == ["名前"]
    assert sheet["blocks"][0]["values"] == {"名前": "甲"}

    excluded = _payload(source, exclude_sheets=["別"])
    assert [sheet["name"] for sheet in excluded["sheets"]] == ["表示"]
    assert {"name": "別", "reason": "excluded"} in excluded["skipped_sheets"]

    outcome = convert(source, "", "excel_to_json", None, options={"sheets": ["無い"]})
    assert outcome.converted is False
    assert "excel_no_rows" in outcome.warnings


def test_low_confidence_header_is_warned() -> None:
    outcome = convert(_xlsx_bytes({"数値": [[1, 2], [3, 4], [5]]}), "", "excel_to_json", None)
    assert "excel_header_low_confidence:数値" in outcome.warnings
    assert outcome.derived_bytes is not None
    sheet = json.loads(outcome.derived_bytes)["sheets"][0]
    assert sheet["diagnostics"][0]["code"] == "header_low_confidence"


def test_duplicate_and_blank_header_names() -> None:
    sheet = _sheet(_payload(_xlsx_bytes({"S": [["id", "id", None, "名前"], [1, 2, 3, 4]]})))
    # 空の表頭は column_<列>、重複は <名前>__<列>（no.1-rag と同じ）。
    assert [column["name"] for column in sheet["columns"]] == ["id", "id__B", "column_C", "名前"]


def test_is_deterministic() -> None:
    source = _xlsx_bytes({"S": [["a", "b"], [1, 2]]})
    first = convert(source, "", "excel_to_json", None).derived_bytes
    second = convert(source, "", "excel_to_json", None).derived_bytes
    assert first == second


def test_passthrough_cases() -> None:
    assert convert(_xlsx_bytes({"S": [["a"], ["1"]]}), "", "csv_to_json", None).converted is False
    empty = convert(b"", "", "excel_to_json", None)
    assert (empty.converted, empty.warnings) == (False, ("excel_empty",))
    invalid = convert(
        _xlsx_bytes({"S": [["a"], ["1"]]}), "", "excel_to_json", None, options={"header_row": 0}
    )
    assert (invalid.converted, invalid.warnings) == (False, ("excel_options_invalid",))


# ---- .xls（xlrd）の値 -----------------------------------------------------------------

_XLRD = SimpleNamespace(
    XL_CELL_EMPTY=0,
    XL_CELL_TEXT=1,
    XL_CELL_NUMBER=2,
    XL_CELL_DATE=3,
    XL_CELL_BOOLEAN=4,
    XL_CELL_ERROR=5,
    XL_CELL_BLANK=6,
    error_text_from_code={0x07: "#DIV/0!"},
    xldate_as_datetime=lambda value, datemode: dt.datetime(1899, 12, 30) + dt.timedelta(days=value),
)


def _xls_text(ctype: int, value: Any, number_format: str = "General") -> str:
    sheet = SimpleNamespace(
        cell=lambda row, col: SimpleNamespace(ctype=ctype, value=value),
        cell_xf_index=lambda row, col: 0,
    )
    book = SimpleNamespace(
        datemode=0,
        xf_list=[SimpleNamespace(format_key=1)],
        format_map={1: SimpleNamespace(format_str=number_format)},
    )
    return converters._xls_cell_text(book, sheet, 0, 0, _XLRD)


def test_xls_dates_booleans_numbers_and_errors() -> None:
    assert _xls_text(_XLRD.XL_CELL_DATE, 46113.0) == "2026-04-01"
    assert _xls_text(_XLRD.XL_CELL_BOOLEAN, 1) == "TRUE"
    assert _xls_text(_XLRD.XL_CELL_BOOLEAN, 0) == "FALSE"
    assert _xls_text(_XLRD.XL_CELL_NUMBER, 7.0, "0000") == "0007"
    assert _xls_text(_XLRD.XL_CELL_NUMBER, 2.5) == "2.5"
    assert _xls_text(_XLRD.XL_CELL_TEXT, "001") == "001"
    assert _xls_text(_XLRD.XL_CELL_ERROR, 0x07) == "#DIV/0!"
    assert _xls_text(_XLRD.XL_CELL_BLANK, "") == ""


def test_xls_round_trip_when_xlwt_is_available() -> None:
    xlwt = pytest.importorskip("xlwt")  # .xls 書き込みで往復検証(無ければ skip)
    workbook = xlwt.Workbook()
    worksheet = workbook.add_sheet("Sheet1")
    for r, row in enumerate([["name", "score"], ["Carol", 99]]):
        for c, value in enumerate(row):
            worksheet.write(r, c, value)
    buffer = io.BytesIO()
    workbook.save(buffer)
    payload = _payload(buffer.getvalue())
    assert payload["source_format"] == "xls"
    assert _sheet(payload)["blocks"][0]["values"] == {"name": "Carol", "score": "99"}
