"""xlsx の生成の本体（`xlsx_workbook.build_workbook`。#5837）。

作ったバイト列を openpyxl で開き直して確かめる（Run・DB・モデルは使わない。値は合成）。

- シート名・題と説明の文・表の見出し・型（文字・数値・日付）・数値と日付の書式・列の幅。
- 集計の行が値ではなく数式であること、表の見出しの固定、フィルターの範囲。
- `=` `+` `-` `@` で始まる文字が数式にならないこと。
- 作れない内容（上限の超過・型に合わない値・精度を超える数値）は、切り捨てずに誤りにすること。
"""

from __future__ import annotations

import io
from datetime import datetime
from typing import Any

import pytest
from openpyxl import load_workbook  # type: ignore[import-untyped]

from app.features.agent import xlsx_workbook
from app.features.agent.xlsx_workbook import (
    MAX_CELLS,
    MAX_COLUMNS,
    MAX_NOTES,
    MAX_ROWS,
    MAX_SHEETS,
    MAX_SUMMARIES,
    Column,
    Sheet,
    Summary,
    XlsxSpecError,
    build_workbook,
    sheet_titles,
)


def _open(sheets: list[Sheet]) -> Any:
    built = build_workbook(sheets)
    assert built.data.startswith(b"PK\x03\x04")
    return load_workbook(io.BytesIO(built.data))


def _issues(sheets: list[Sheet]) -> list[tuple[str, str]]:
    with pytest.raises(XlsxSpecError) as raised:
        build_workbook(sheets)
    return [(item.argument, item.code) for item in raised.value.issues]


ITEMS = Sheet(
    name="明細",
    heading="合成の明細",
    notes=["合成のデータ。", "単位: 個"],
    columns=[
        Column("品目", "text"),
        Column("数量（個）", "number", "integer"),
        Column("単価", "number", "decimal2"),
        Column("比率", "number", "percent1"),
        Column("登録日", "date", "date_slash"),
        Column("更新日時", "date"),
    ],
    rows=[
        ["品目 A", 1200, 10.5, 0.25, "2026-01-05", "2026-01-05T09:30:00"],
        ["品目 B", 30, "20.25", 0.75, "2026-02-10T00:00:00", "2026-02-10 18:00:00+09:00"],
        ["品目 C", None, None, None, None, None],
    ],
    summaries=[
        Summary("sum", (2,), "合計"),
        Summary("average", (2, 3), "平均"),
        Summary("count", (1,)),
        Summary("min", (2, 5), "最小"),
        Summary("max", (6,), "最大"),
    ],
)


def test_sheet_has_heading_notes_table_and_typed_cells() -> None:
    workbook = _open([ITEMS, Sheet(name="空の表", columns=[Column("名前", "text")], rows=[])])

    assert workbook.sheetnames == ["明細", "空の表"]
    sheet = workbook["明細"]
    # 題（太字）→ 説明の文 → 空行 → 表の見出し → データ。
    assert sheet["A1"].value == "合成の明細" and sheet["A1"].font.bold
    assert [sheet["A2"].value, sheet["A3"].value, sheet["A4"].value] == [
        "合成のデータ。",
        "単位: 個",
        None,
    ]
    assert [cell.value for cell in sheet[5]] == [
        "品目",
        "数量（個）",
        "単価",
        "比率",
        "登録日",
        "更新日時",
    ]
    assert all(cell.font.bold and cell.fill.fill_type == "solid" for cell in sheet[5])
    # 数値は数値、日付は日付、文字は文字（数値の形の文字列も数値にする）。
    first, second, third = sheet[6], sheet[7], sheet[8]
    assert [cell.value for cell in first] == [
        "品目 A",
        1200,
        10.5,
        0.25,
        datetime(2026, 1, 5),
        datetime(2026, 1, 5, 9, 30),
    ]
    assert [cell.data_type for cell in first] == ["s", "n", "n", "n", "d", "d"]
    assert second[2].value == 20.25 and second[2].data_type == "n"
    # 時間帯は持たせず、書かれた時刻のまま。
    assert second[5].value == datetime(2026, 2, 10, 18, 0)
    assert [cell.value for cell in third] == ["品目 C", None, None, None, None, None]
    # 書式: 文字は文字の書式、数値・日付は指定の書式。日付の既定は内容から（時刻があれば日時）。
    assert [cell.number_format for cell in first] == [
        "@",
        "#,##0",
        "#,##0.00",
        "0.0%",
        "yyyy/mm/dd",
        "yyyy-mm-dd hh:mm",
    ]


def test_summary_rows_are_formulas_over_the_data_rows() -> None:
    sheet = _open([ITEMS])["明細"]

    rows = {row: [cell.value for cell in sheet[row]] for row in range(9, 14)}
    assert rows[9] == ["合計", "=SUM(B6:B8)", None, None, None, None]
    assert rows[10] == [
        "平均",
        '=IF(COUNT(B6:B8)=0,"",AVERAGE(B6:B8))',
        '=IF(COUNT(C6:C8)=0,"",AVERAGE(C6:C8))',
        None,
        None,
        None,
    ]
    # 見出しの無い集計の行は、数式だけ。
    assert rows[11] == ["=COUNTA(A6:A8)", None, None, None, None, None]
    assert rows[12] == [
        "最小",
        '=IF(COUNT(B6:B8)=0,"",MIN(B6:B8))',
        None,
        None,
        '=IF(COUNT(E6:E8)=0,"",MIN(E6:E8))',
        None,
    ]
    assert rows[13][0] == "最大" and rows[13][5] == '=IF(COUNT(F6:F8)=0,"",MAX(F6:F8))'
    # 数式の cell は数式の型で、列の書式（件数は整数）を持つ。
    assert sheet["B9"].data_type == "f" and sheet["B9"].number_format == "#,##0"
    assert sheet["C10"].number_format == "#,##0.00"
    assert sheet["A11"].data_type == "f" and sheet["A11"].number_format == "#,##0"
    assert sheet["E12"].number_format == "yyyy/mm/dd"
    assert sheet["A9"].data_type == "s" and sheet["A9"].font.bold


def test_header_is_frozen_and_filter_covers_header_and_data_only() -> None:
    workbook = _open(
        [
            ITEMS,
            Sheet(
                name="題なし", columns=[Column("a", "text"), Column("b", "number")], rows=[["x", 1]]
            ),
            Sheet(name="空の表", columns=[Column("a", "text")], rows=[]),
        ]
    )

    # 題・説明の文の下の、表の見出しまでを固定する。フィルターに集計の行を入れない。
    assert workbook["明細"].freeze_panes == "A6"
    assert workbook["明細"].auto_filter.ref == "A5:F8"
    assert workbook["題なし"].freeze_panes == "A2"
    assert workbook["題なし"].auto_filter.ref == "A1:B2"
    assert workbook["空の表"].auto_filter.ref == "A1:A1"


def test_column_width_follows_the_content_or_the_given_value() -> None:
    sheet = _open(
        [
            Sheet(
                name="幅",
                columns=[
                    Column("短", "text"),
                    Column("全角の長い見出しの列", "text"),
                    Column("n", "number", "integer"),
                    Column("指定", "text", width=40),
                    Column("長文", "text"),
                ],
                rows=[["a", "b", 1234567, "c", "x" * 500]],
            )
        ]
    )["幅"]

    widths = [sheet.column_dimensions[letter].width for letter in "ABCDE"]
    # 内容から決める幅は下限と上限の間（全角は 2 つ分、数値は書式の後の桁区切りを含む）。
    assert widths[0] == 8
    assert widths[1] == len("全角の長い見出しの列") * 2 + 2 + 2
    assert widths[2] == len("1,234,567") + 2
    assert widths[3] == 40
    assert widths[4] == 60


@pytest.mark.parametrize(
    "text",
    ["=1+1", "+1+1", "-1+1", "@SUM(A1)", '=HYPERLINK("https://example.invalid")', "#N/A"],
    ids=["equals", "plus", "minus", "at", "function", "error-code"],
)
def test_text_is_never_written_as_a_formula(text: str) -> None:
    sheet = _open(
        [
            Sheet(
                name=text,
                heading=text,
                notes=[text],
                columns=[Column(text, "text"), Column("n", "number")],
                rows=[[text, 1]],
                summaries=[Summary("sum", (2,), text)],
            )
        ]
    )[sheet_titles([text])[0]]

    # 題・説明の文・見出し・データ・集計の行の見出しのどれも、文字型のまま。
    for coordinate in ("A1", "A2", "A4", "A5", "A6"):
        assert sheet[coordinate].value == text, coordinate
        assert sheet[coordinate].data_type == "s", coordinate
    # 数式として書くのは集計の行だけ。
    formulas = [
        cell.coordinate for row in sheet.iter_rows() for cell in row if cell.data_type == "f"
    ]
    assert formulas == ["B6"]


def test_text_column_keeps_numbers_as_text_and_drops_control_characters() -> None:
    sheet = _open(
        [
            Sheet(
                name="文字",
                columns=[Column("コード", "text")],
                rows=[["00123"], [2024], [1.5], [True], ["a\x00b\x1fc"], [""]],
            )
        ]
    )["文字"]

    values = [
        (sheet.cell(row=row, column=1).value, sheet.cell(row=row, column=1).data_type)
        for row in range(2, 8)
    ]
    assert values[:5] == [("00123", "s"), ("2024", "s"), ("1.5", "s"), ("true", "s"), ("abc", "s")]
    assert values[5][0] is None


def test_sheet_names_follow_the_excel_rules() -> None:
    assert sheet_titles(["a/b\\c?d*e[f]g:h", " 'x' ", "", "x", "X", "y" * 40, "y" * 40]) == [
        "a_b_c_d_e_f_g_h",
        "x",
        "Sheet3",
        "x (2)",
        "X (3)",
        "y" * 31,
        "y" * 27 + " (2)",
    ]
    workbook = _open(
        [
            Sheet(name="集計/1 月", columns=[Column("a", "text")], rows=[]),
            Sheet(name="集計:1 月", columns=[Column("a", "text")], rows=[]),
        ]
    )
    built = build_workbook(
        [
            Sheet(name="集計/1 月", columns=[Column("a", "text")], rows=[]),
            Sheet(name="集計:1 月", columns=[Column("a", "text")], rows=[]),
        ]
    )
    # 直した名前は要約にも返す（呼ぶ側が利用者に伝えられる）。
    assert workbook.sheetnames == ["集計_1 月", "集計_1 月 (2)"]
    assert [sheet["name"] for sheet in built.sheets] == workbook.sheetnames
    assert built.sheets[0] == {"name": "集計_1 月", "rows": 0, "columns": 1, "summary_rows": 0}


def _wide(columns: int, rows: int) -> Sheet:
    return Sheet(
        name="大きい表",
        columns=[Column(f"c{index}", "number") for index in range(columns)],
        rows=[[1] * columns] * rows,
    )


def test_limits_are_errors_not_truncation(monkeypatch: pytest.MonkeyPatch) -> None:
    one = Sheet(name="s", columns=[Column("a", "text")], rows=[])
    assert _issues([]) == [("sheets", "count")]
    assert _issues([one] * (MAX_SHEETS + 1)) == [("sheets", "count")]
    assert _issues([Sheet(name="s", columns=[], rows=[])]) == [("sheets.0.columns", "count")]
    assert _issues([_wide(MAX_COLUMNS + 1, 0)]) == [("sheets.0.columns", "count")]
    assert _issues([_wide(1, MAX_ROWS + 1)]) == [("sheets.0.rows", "too_many")]
    assert _issues(
        [Sheet(name="s", columns=[Column("a", "text")], rows=[], notes=["x"] * (MAX_NOTES + 1))]
    ) == [("sheets.0.notes", "too_many")]
    too_many_summaries = Sheet(
        name="s",
        columns=[Column("a", "text"), Column("b", "number")],
        rows=[["x", 1]],
        summaries=[Summary("sum", (2,))] * (MAX_SUMMARIES + 1),
    )
    assert _issues([too_many_summaries]) == [("sheets.0.summaries", "too_many")]
    # cell の合計は全シートの合計（どのシートも行・列の上限の中）。
    per_sheet = MAX_CELLS // 20 // 2 + 1
    assert _issues([_wide(20, per_sheet), _wide(20, per_sheet)]) == [("sheets", "too_many_cells")]
    # 上限ちょうどは作れる（上限を小さくして確かめる）。
    monkeypatch.setattr(xlsx_workbook, "MAX_CELLS", 6)
    assert build_workbook([_wide(3, 2)]).sheets[0]["rows"] == 2
    assert _issues([_wide(3, 3)]) == [("sheets", "too_many_cells")]


def test_values_that_do_not_fit_the_column_type_are_errors() -> None:
    sheet = Sheet(
        name="型",
        columns=[
            Column("数値", "number"),
            Column("日付", "date"),
            Column("大きい番号", "number"),
            Column("細かい小数", "number"),
            Column("文字", "text"),
        ],
        rows=[
            [1, "2026-01-05", 999_999_999_999_999, "0.10", "ok"],
            ["十", "1 月 5 日", 1_000_000_000_000_000, "1234567890.123456789", "x" * 32_768],
            ["二十", "明日", "12345678901234567890", 0.5, "ok"],
        ],
    )

    with pytest.raises(XlsxSpecError) as raised:
        build_workbook([sheet])

    # 列ごとに最初の 1 件。場所は列の type、文に値を含めない。
    issues = raised.value.issues
    assert [(item.argument, item.code) for item in issues] == [
        ("sheets.0.columns.0.type", "not_number"),
        ("sheets.0.columns.1.type", "not_date"),
        ("sheets.0.columns.2.type", "precision"),
        ("sheets.0.columns.3.type", "precision"),
        ("sheets.0.columns.4.type", "too_long"),
    ]
    assert all("2 行目" in item.message for item in issues)
    for leaked in ("十", "1 月 5 日", "1000000000000000", "1234567890.123456789"):
        assert all(leaked not in item.message for item in issues), leaked
    assert "type を text" in issues[0].message and "値を短く" in issues[4].message


@pytest.mark.parametrize(
    ("value", "column_type"),
    [
        (True, "number"),
        (float("nan"), "number"),
        (float("inf"), "number"),
        ("1e999", "number"),
        ("", "number"),
        ("1,200", "number"),
        (20260105, "date"),
        ("1899-12-31", "date"),
        ("2026-13-01", "date"),
    ],
    ids=[
        "bool",
        "nan",
        "inf",
        "overflow",
        "empty",
        "separator",
        "int-date",
        "before-1900",
        "month",
    ],
)
def test_unreadable_values_are_rejected(value: Any, column_type: Any) -> None:
    sheet = Sheet(name="s", columns=[Column("a", column_type)], rows=[[value]])
    [(argument, _code)] = _issues([sheet])
    assert argument == "sheets.0.columns.0.type"


def test_format_width_row_length_and_summary_rules() -> None:
    def sheet(**changes: Any) -> Sheet:
        base: dict[str, Any] = {
            "name": "s",
            "columns": [Column("名前", "text"), Column("数", "number"), Column("日", "date")],
            "rows": [["a", 1, "2026-01-05"]],
        }
        return Sheet(**{**base, **changes})

    # 書式は列の型のものだけ（文字の列には付けない）。
    for column in (
        Column("a", "text", "integer"),
        Column("a", "number", "date"),
        Column("a", "date", "percent"),
        Column("a", "number", "unknown"),
    ):
        assert _issues([sheet(columns=[column], rows=[])]) == [
            ("sheets.0.columns.0.format", "format")
        ]
    assert _issues([sheet(columns=[Column("a", "text", width=1)], rows=[])]) == [
        ("sheets.0.columns.0.width", "range")
    ]
    # 行の値の数は列の数と同じ（最初の 1 行だけ報告する）。
    assert _issues([sheet(rows=[["a", 1, "2026-01-05"], ["b", 2], ["c"]])]) == [
        ("sheets.0.rows.1", "length")
    ]
    # 集計: 列の番号の範囲・重複、列の型、行の無い表、見出しを置く列。
    for columns in ((), (0,), (4,), (2, 2)):
        assert _issues([sheet(summaries=[Summary("sum", columns)])]) == [
            ("sheets.0.summaries.0.columns", "range")
        ]
    for function, columns in (("sum", (1,)), ("average", (3,)), ("min", (1,)), ("max", (1,))):
        assert _issues([sheet(summaries=[Summary(function, columns)])]) == [
            ("sheets.0.summaries.0.columns", "type")
        ]
    assert _issues([sheet(rows=[], summaries=[Summary("count", (1,))])]) == [
        ("sheets.0.summaries.0", "no_rows")
    ]
    assert _issues([sheet(summaries=[Summary("count", (1, 2, 3), "件数")])]) == [
        ("sheets.0.summaries.0.label", "no_room")
    ]
    # 見出しが無ければ、すべての列を集計できる。見出しは、集計しない最初の列に置く。
    built = _open([sheet(summaries=[Summary("count", (1, 2, 3)), Summary("count", (1,), "件数")])])[
        "s"
    ]
    assert [cell.value for cell in built[3]] == [
        "=COUNTA(A2:A2)",
        "=COUNTA(B2:B2)",
        "=COUNTA(C2:C2)",
    ]
    assert [cell.value for cell in built[4]] == ["=COUNTA(A2:A2)", "件数", None]
