"""Excel のシートのグラフ（`xlsx_workbook` の `Sheet.chart` と `xlsx_chart`。#5837）。

作ったバイト列を openpyxl で開き直し、グラフの部品の XML も読んで確かめる（Run・DB・モデルは
使わない。値は合成）。

- グラフは、そのシートの表の cell を参照する（系列の名前は見出しの cell、項目と値はデータの行。
  集計の行は含めない）。XML に入る値の控えは、cell の値と同じ。
- 種類（縦棒・横棒・折れ線・円）、置き場所（表の右）と大きさ。
- Excel に合わせる所: 数値・日付の項目の参照、等間隔の項目の軸、白い塗りと角、負の値、凡例の位置、
  字体と字の大きさ。
- 上限を超えた指定・種類に合わない指定は、切り詰めずに誤りにする。
"""

from __future__ import annotations

import io
import zipfile
from datetime import datetime
from typing import Any

import pytest
from openpyxl import load_workbook  # type: ignore[import-untyped]
from openpyxl.chart import BarChart, LineChart, PieChart  # type: ignore[import-untyped]
from openpyxl.utils.units import cm_to_EMU  # type: ignore[import-untyped]
from pptx.oxml import parse_xml

from app.features.agent.chart_spec import (
    CHART_TYPES,
    MAX_CHART_CATEGORIES,
    MAX_CHART_LABEL_CHARS,
    MAX_CHART_SERIES,
    MAX_PIE_CATEGORIES,
    ChartData,
    ChartSeries,
    ChartSpec,
)
from app.features.agent.xlsx_chart import (
    CHART_HEIGHT_CM,
    CHART_WIDTH_CM,
    SHEET_CHART_LABEL_PT,
    SHEET_CHART_PT,
    chart_height_cm,
)
from app.features.agent.xlsx_workbook import (
    FONT_NAME,
    Column,
    Sheet,
    Summary,
    XlsxSpecError,
    build_workbook,
)

COLUMNS = [
    Column("品目", "text"),
    Column("数量（個）", "number", "integer"),
    Column("比率", "number", "percent1"),
    Column("登録日", "date", "date_slash"),
    Column("年", "number"),
]
ROWS: list[list[Any]] = [
    ["品目 A", 1200, 0.25, "2026-01-05", 2024],
    ["品目 B", 30.5, 0.5, "2026-02-10", 2025],
    ["品目 C", None, 0.125, "2026-03-15", 2026],
]
OPENPYXL_CHARTS = {"column": BarChart, "bar": BarChart, "line": LineChart, "pie": PieChart}


def _sheet(chart: ChartSpec | None, **changes: Any) -> Sheet:
    base: dict[str, Any] = {
        "name": "明細",
        "heading": "合成の明細",
        "notes": ["合成のデータ。"],
        "columns": COLUMNS,
        "rows": ROWS,
        "summaries": [Summary("sum", (2,), "合計")],
        "chart": chart,
    }
    return Sheet(**{**base, **changes})


def _built(*sheets: Sheet) -> tuple[Any, list[Any], Any]:
    """開き直したブックと、グラフの部品の XML（シートの順）と、結果の要約。"""
    built = build_workbook(list(sheets))
    with zipfile.ZipFile(io.BytesIO(built.data)) as archive:
        names = sorted(name for name in archive.namelist() if name.startswith("xl/charts/"))
        parts = [parse_xml(archive.read(name)) for name in names]
    return load_workbook(io.BytesIO(built.data)), parts, built


def _issues(*sheets: Sheet) -> list[tuple[str, str]]:
    with pytest.raises(XlsxSpecError) as raised:
        build_workbook(list(sheets))
    return [(item.argument, item.code) for item in raised.value.issues]


def _values(element: Any, path: str) -> list[str]:
    return [str(value) for value in element.xpath(path)]


@pytest.mark.parametrize("chart_type", list(CHART_TYPES), ids=list(CHART_TYPES))
def test_chart_refers_to_the_cells_of_the_table(chart_type: str) -> None:
    series_columns = (3,) if chart_type == "pie" else (2, 3)
    workbook, [part], built = _built(_sheet(ChartSpec(chart_type, 1, series_columns)))

    sheet = workbook["明細"]
    # 表: 題・説明（1〜2 行目）・空行・見出し（4 行目）・データ（5〜7 行目）・集計（8 行目）。
    assert [cell.value for cell in sheet[4]][:3] == ["品目", "数量（個）", "比率"]
    assert sheet["B8"].value == "=SUM(B5:B7)"
    [chart] = sheet._charts  # noqa: SLF001 - openpyxl に、読んだグラフの公開の API が無い
    assert type(chart) is OPENPYXL_CHARTS[chart_type]
    if chart_type in ("column", "bar"):
        assert chart.barDir == {"column": "col", "bar": "bar"}[chart_type]
    # 系列の名前は見出しの cell、項目と値はデータの行だけ（集計の行は含めない）。
    assert [
        (series.tx.strRef.f, series.cat.strRef.f, series.val.numRef.f) for series in chart.series
    ] == [
        (f"'明細'!${letter}$4", "'明細'!$A$5:$A$7", f"'明細'!${letter}$5:${letter}$7")
        for letter in ("BC"[column - 2] for column in series_columns)
    ]
    # XML に入る値の控えは、cell の値と同じ（空の cell は控えにも無い）。
    for series, column in zip(chart.series, series_columns, strict=True):
        cells = [sheet.cell(row=row, column=column).value for row in (5, 6, 7)]
        assert series.tx.strRef.strCache.pt[0].v == sheet.cell(row=4, column=column).value
        assert [point.v for point in series.cat.strRef.strCache.pt] == [
            "品目 A",
            "品目 B",
            "品目 C",
        ]
        assert {point.idx: point.v for point in series.val.numRef.numCache.pt} == {
            index: float(value) for index, value in enumerate(cells) if value is not None
        }
    # グラフ用の値を別に持たない（埋め込みのブック・外部の参照が無い）。
    assert part.xpath("//c:externalData") == []
    assert built.charts == (
        {"sheet": "明細", "type": chart_type, "series": len(series_columns), "categories": 3},
    )


def test_chart_is_placed_to_the_right_of_the_table_with_a_fixed_size() -> None:
    workbook, _, _ = _built(
        _sheet(ChartSpec("column", 1, (2,))),
        _sheet(ChartSpec("line", 1, (2,)), name="題なし", heading=None, notes=[], summaries=[]),
    )

    [chart] = workbook["明細"]._charts  # noqa: SLF001
    # 表は A〜E 列。1 列空けて G 列の、表の見出しの行（4 行目）から。
    start = chart.anchor._from  # noqa: SLF001
    assert (start.col, start.row, start.colOff, start.rowOff) == (6, 3, 0, 0)
    assert (chart.anchor.ext.width, chart.anchor.ext.height) == (
        cm_to_EMU(CHART_WIDTH_CM),
        cm_to_EMU(CHART_HEIGHT_CM),
    )
    [plain] = workbook["題なし"]._charts  # noqa: SLF001
    assert (plain.anchor._from.col, plain.anchor._from.row) == (6, 0)  # noqa: SLF001


def test_horizontal_bar_chart_grows_with_the_number_of_bars() -> None:
    def data(chart_type: str, categories: int, series: int) -> ChartData:
        names = tuple(f"項目 {index}" for index in range(categories))
        return ChartData(
            chart_type,
            names,
            tuple(ChartSeries(f"系列 {index}", (), None) for index in range(series)),
        )

    # 横棒だけ、棒の数が多いときに高くする。少ないとき・ほかの種類は決まった高さ。
    assert chart_height_cm(data("bar", 6, 1)) == CHART_HEIGHT_CM
    assert chart_height_cm(data("bar", MAX_CHART_CATEGORIES, 1)) == CHART_HEIGHT_CM
    tall = chart_height_cm(data("bar", MAX_CHART_CATEGORIES, MAX_CHART_SERIES))
    assert CHART_HEIGHT_CM < chart_height_cm(data("bar", MAX_CHART_CATEGORIES, 2)) < tall
    for chart_type in ("column", "line"):
        heights = chart_height_cm(data(chart_type, MAX_CHART_CATEGORIES, MAX_CHART_SERIES))
        assert heights == CHART_HEIGHT_CM
    rows = [[f"項目 {index}", index, index, index, index, index] for index in range(20)]
    columns = [Column("項目", "text"), *[Column(f"系列 {index}", "number") for index in range(5)]]
    workbook, _, _ = _built(
        Sheet(name="横棒", columns=columns, rows=rows, chart=ChartSpec("bar", 1, (2, 3, 4, 5, 6)))
    )
    [chart] = workbook["横棒"]._charts  # noqa: SLF001
    assert chart.anchor.ext.height == cm_to_EMU(tall)


def test_sheet_without_a_chart_has_no_drawing() -> None:
    built = build_workbook([_sheet(None)])

    with zipfile.ZipFile(io.BytesIO(built.data)) as archive:
        assert [name for name in archive.namelist() if "chart" in name or "drawing" in name] == []
    assert built.charts == ()


def test_each_sheet_has_its_own_chart_and_quoted_sheet_names() -> None:
    rows = [["区分 A", 1], ["区分 B", 2]]
    columns = [Column("区分", "text"), Column("値", "number")]
    workbook, parts, built = _built(
        Sheet(name="集計 'その' 1", columns=columns, rows=rows, chart=ChartSpec("pie", 1, (2,))),
        Sheet(name="表だけ", columns=columns, rows=rows),
        Sheet(name="1 行", columns=columns, rows=rows[:1], chart=ChartSpec("bar", 1, (2,))),
    )

    assert [len(sheet._charts) for sheet in workbook] == [1, 0, 1]  # noqa: SLF001
    # シート名の `'` は重ねて書く。1 行の表は、1 つの cell の参照になる。
    assert [_values(part, "//c:f/text()") for part in parts] == [
        ["'集計 ''その'' 1'!$B$1", "'集計 ''その'' 1'!$A$2:$A$3", "'集計 ''その'' 1'!$B$2:$B$3"],
        ["'1 行'!$B$1", "'1 行'!$A$2", "'1 行'!$B$2"],
    ]
    assert [(item["sheet"], item["type"]) for item in built.charts] == [
        ("集計 'その' 1", "pie"),
        ("1 行", "bar"),
    ]


@pytest.mark.parametrize(
    ("category_column", "code", "expected"),
    [
        (4, "yyyy/mm/dd", [46027.0, 46063.0, 46096.0]),
        (5, "General", [2024.0, 2025.0, 2026.0]),
    ],
    ids=["date", "number"],
)
def test_number_and_date_categories_are_number_references(
    category_column: int, code: str, expected: list[float]
) -> None:
    """文字の参照のままだと、Excel は日付の項目を通し番号で表示する。"""
    workbook, [part], _ = _built(_sheet(ChartSpec("line", category_column, (2, 3))))

    sheet = workbook["明細"]
    assert sheet.cell(row=5, column=category_column).number_format == code
    if category_column == 4:
        assert sheet["D5"].value == datetime(2026, 1, 5)
    [chart] = sheet._charts  # noqa: SLF001
    letter = "ABCDE"[category_column - 1]
    for series in chart.series:
        assert series.cat.strRef is None
        reference = series.cat.numRef
        assert reference.f == f"'明細'!${letter}$5:${letter}$7"
        # 値の控えは、cell の値（日付は Excel の通し番号）と表示形式のまま。
        assert reference.numCache.formatCode == code
        assert [point.v for point in reference.numCache.pt] == expected
    # 軸の目盛りは、cell の表示形式を使う。
    [number_format] = part.xpath("//c:catAx/c:numFmt")
    assert (number_format.get("formatCode"), number_format.get("sourceLinked")) == (code, "1")


@pytest.mark.parametrize("chart_type", list(CHART_TYPES), ids=list(CHART_TYPES))
def test_chart_xml_is_adjusted_for_a_sheet(chart_type: str) -> None:
    series_columns = (3,) if chart_type == "pie" else (2,)
    _, [part], _ = _built(_sheet(ChartSpec(chart_type, 1, series_columns)))

    # グラフの全体は白い塗りと薄い線で、角を丸めない（`c:roundedCorners` は `c:chart` より前）。
    assert _values(part, "c:roundedCorners/@val") == ["0"]
    assert part.xpath("c:roundedCorners/following-sibling::c:chart") != []
    assert _values(part, "c:spPr/a:solidFill/a:srgbClr/@val") == ["FFFFFF"]
    assert part.xpath("c:spPr/a:ln/a:solidFill") != [] and part.xpath("c:spPr//a:noFill") == []
    # 字体は英数字と東アジアの両方（ブックの字体と同じ）。字は cell に合わせた大きさ。
    [defaults] = part.xpath("c:txPr/a:p/a:pPr/a:defRPr")
    assert defaults.get("sz") == str(int(SHEET_CHART_PT * 100))
    assert _values(defaults, "a:latin/@typeface") == [FONT_NAME]
    assert _values(defaults, "a:ea/@typeface") == [FONT_NAME]
    label_sizes = set(_values(part, "//c:dLbls/c:txPr//a:defRPr/@sz"))
    assert label_sizes <= {str(int(SHEET_CHART_LABEL_PT * 100))}
    # 凡例の位置は、既定の値でも属性に書く（省くと、openpyxl がブックを読めない）。
    assert _values(part, "//c:legendPos/@val") == (["r"] if chart_type == "pie" else [])
    # 題は置かない。
    assert part.xpath("//c:title") == []
    if chart_type == "pie":
        assert _values(part, "//c:varyColors/@val") == ["1"]
        return
    # 系列が 1 つでも、棒・点ごとに色を変えない。
    assert _values(part, "//c:varyColors/@val") == ["0"]
    # 項目の軸は、表の行の順に等間隔（日付の軸に切り替えない）。軸の ID は正の数で対応する。
    assert _values(part, "//c:catAx/c:auto/@val") == ["0"]
    axis_ids = _values(part, "//c:catAx/c:axId/@val | //c:valAx/c:axId/@val")
    assert sorted(axis_ids) == sorted(_values(part, "//c:plotArea/*/c:crossAx/@val"))
    assert len(set(axis_ids)) == 2 and all(0 < int(value) < 2**31 for value in axis_ids)
    # 負の値があっても読める: 項目の名前はグラフの端、負の値の棒も系列の色。
    assert _values(part, "//c:catAx/c:tickLblPos/@val") == ["low"]
    inverted = _values(part, "//c:ser/c:invertIfNegative/@val")
    assert inverted == ([] if chart_type == "line" else ["0"])
    # 横棒は、表の上の行から順に上から並べる。
    orientation = _values(part, "//c:catAx/c:scaling/c:orientation/@val")
    assert orientation == ["maxMin" if chart_type == "bar" else "minMax"]


def test_names_starting_with_a_formula_sign_stay_text() -> None:
    labels = ["=1+1", "+区分", "@区分", "http://example.invalid/x"]
    workbook, [part], _ = _built(
        Sheet(
            name="文字",
            columns=[Column("=区分", "text"), Column("=値", "number")],
            rows=[[label, index] for index, label in enumerate(labels, 1)],
            chart=ChartSpec("column", 1, (2,)),
        )
    )

    sheet = workbook["文字"]
    names = [sheet["B1"], *(sheet.cell(row=row, column=1) for row in range(2, 6))]
    assert [cell.value for cell in names] == ["=値", *labels]
    assert {cell.data_type for cell in names} == {"s"}
    assert all(cell.hyperlink is None for cell in names)
    # 参照（`c:f`）に入るのは cell の番地だけで、表の文字は控えにだけ入る。
    assert _values(part, "//c:f/text()") == ["'文字'!$B$1", "'文字'!$A$2:$A$5", "'文字'!$B$2:$B$5"]
    assert _values(part, "//c:cat//c:v/text()") == labels


def _numbers(count: int) -> Sheet:
    return Sheet(
        name="表",
        columns=[
            Column("項目", "text"),
            Column("値 1", "number"),
            Column("値 2", "number"),
            Column("名前", "text"),
        ],
        rows=[[f"項目 {index}", index + 1, index, "x"] for index in range(count)],
    )


def _charted(sheet: Sheet, chart: ChartSpec, **changes: Any) -> Sheet:
    values: dict[str, Any] = {
        "name": sheet.name,
        "columns": sheet.columns,
        "rows": sheet.rows,
        "chart": chart,
    }
    return Sheet(**{**values, **changes})


@pytest.mark.parametrize(
    ("sheet", "expected"),
    [
        (_charted(_numbers(3), ChartSpec("area", 1, (2,))), [("sheets.0.chart.type", "enum")]),
        (
            _charted(_numbers(3), ChartSpec("column", 5, (2,))),
            [("sheets.0.chart.category_column", "range")],
        ),
        (
            _charted(_numbers(3), ChartSpec("column", 1, (4,))),
            [("sheets.0.chart.series_columns.0", "not_number")],
        ),
        (
            _charted(_numbers(3), ChartSpec("column", 1, (2, 2))),
            [("sheets.0.chart.series_columns.1", "duplicate")],
        ),
        (
            _charted(_numbers(3), ChartSpec("line", 2, (2,))),
            [("sheets.0.chart.series_columns.0", "conflict")],
        ),
        (
            _charted(_numbers(3), ChartSpec("bar", 1, ())),
            [("sheets.0.chart.series_columns", "count")],
        ),
        (
            # 円は系列が 1 つ。
            _charted(_numbers(3), ChartSpec("pie", 1, (2, 3))),
            [("sheets.0.chart.series_columns", "count")],
        ),
        (
            _charted(_numbers(MAX_CHART_CATEGORIES + 1), ChartSpec("column", 1, (2,))),
            [("sheets.0.rows", "too_many")],
        ),
        (
            _charted(_numbers(MAX_PIE_CATEGORIES + 1), ChartSpec("pie", 1, (2,))),
            [("sheets.0.rows", "too_many")],
        ),
        (_charted(_numbers(0), ChartSpec("column", 1, (2,))), [("sheets.0.rows", "required")]),
        (
            # 円は、負の値を含められない。
            _charted(
                _numbers(3), ChartSpec("pie", 1, (2,)), rows=[["a", 1, 0, "x"], ["b", -1, 0, "x"]]
            ),
            [("sheets.0.columns.1", "pie_values")],
        ),
        (
            _charted(_numbers(3), ChartSpec("line", 1, (2,)), rows=[["a", None, 0, "x"]] * 2),
            [("sheets.0.columns.1", "empty")],
        ),
        (
            _charted(
                _numbers(3), ChartSpec("line", 1, (2,)), rows=[["a", 1, 0, "x"], [None, 2, 0, "x"]]
            ),
            [("sheets.0.columns.0", "required")],
        ),
        (
            _charted(
                _numbers(3),
                ChartSpec("line", 1, (2,)),
                rows=[["長" * (MAX_CHART_LABEL_CHARS + 1), 1, 0, "x"]],
            ),
            [("sheets.0.columns.0", "too_long")],
        ),
    ],
    ids=[
        "unknown-type",
        "category-out-of-range",
        "text-series",
        "duplicate-series",
        "category-as-series",
        "no-series",
        "pie-two-series",
        "too-many-categories",
        "too-many-pie-categories",
        "no-rows",
        "pie-negative",
        "empty-series",
        "empty-category",
        "long-category",
    ],
)
def test_chart_that_does_not_fit_is_an_error_not_a_truncation(
    sheet: Sheet, expected: list[tuple[str, str]]
) -> None:
    assert _issues(sheet) == expected


def test_large_numbers_are_drawn_or_rejected_without_failing() -> None:
    """Excel の数値に入る大きい値（指数の大きい小数）でも、生成が落ちない。"""
    columns = [Column("項目", "text"), Column("値", "number", "integer"), Column("番号", "number")]
    rows = [["項目 A", 1e200, 1e200], ["項目 B", 2.5e-200, 2]]
    workbook, _, _ = _built(
        Sheet(name="大きい値", columns=columns, rows=rows, chart=ChartSpec("column", 1, (2, 3)))
    )

    [chart] = workbook["大きい値"]._charts  # noqa: SLF001
    assert [[point.v for point in series.val.numRef.numCache.pt] for series in chart.series] == [
        [1e200, 2.5e-200],
        [1e200, 2.0],
    ]
    # 桁区切りの書式の大きい値を項目の名前にすると、表示の文字が上限より長い。
    assert _issues(
        Sheet(name="大きい値", columns=columns, rows=rows, chart=ChartSpec("column", 2, (3,)))
    ) == [("sheets.0.columns.1", "too_long")]


def test_limits_are_accepted_as_they_are() -> None:
    rows = [[f"項目 {index}", *([index + 1] * MAX_CHART_SERIES)] for index in range(20)]
    columns = [
        Column("項目", "text"),
        *[Column(f"系列 {index}", "number") for index in range(MAX_CHART_SERIES)],
    ]
    series = tuple(range(2, MAX_CHART_SERIES + 2))
    workbook, _, built = _built(
        Sheet(name="上限", columns=columns, rows=rows, chart=ChartSpec("column", 1, series)),
        Sheet(
            name="円の上限",
            columns=columns,
            rows=rows[:MAX_PIE_CATEGORIES],
            chart=ChartSpec("pie", 1, (2,)),
        ),
    )

    assert [(item["series"], item["categories"]) for item in built.charts] == [
        (MAX_CHART_SERIES, MAX_CHART_CATEGORIES),
        (1, MAX_PIE_CATEGORIES),
    ]
    [chart] = workbook["上限"]._charts  # noqa: SLF001
    assert [series.val.numRef.f for series in chart.series] == [
        f"'上限'!${letter}$2:${letter}$21" for letter in "BCDEF"
    ]
