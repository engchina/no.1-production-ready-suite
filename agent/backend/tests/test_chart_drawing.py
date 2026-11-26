"""グラフの描画の部品（`chart_drawing`。#5837）。

スライドに依らない入口（`chart_parts`）が返すグラフの XML とブックを、python-pptx と openpyxl で
読み直して確かめる（値は合成）。

- 値: 項目の名前と系列の値が、渡した値と一致する（XML の中の値と、埋め込みのブックの両方）。
- 体裁: 種類ごとの色・凡例・値のラベル・表示形式・横棒の並び順。題と全体の枠は無い。
- 雛形の手当て: 軸の ID が正の数で対応している・`c:varyColors` が 0・字体が英数字と東アジアの両方。
"""

from __future__ import annotations

import io
from decimal import Decimal
from typing import Any

import pytest
from openpyxl import load_workbook  # type: ignore[import-untyped]
from pptx.chart.chart import Chart
from pptx.enum.chart import XL_CHART_TYPE, XL_LEGEND_POSITION
from pptx.oxml import parse_xml
from pptx.oxml.ns import qn
from pptx.util import Pt

from app.features.agent.chart_drawing import (
    CHART_COLORS,
    CHART_KINDS,
    CHART_PT,
    MAX_LABELED_COLUMNS,
    PIE_LABEL_FORMAT,
    chart_parts,
)
from app.features.agent.chart_spec import CHART_TYPES, ChartData, ChartSeries

FONT = "合成の字体"
CATEGORIES = ("品目 A", "品目 B", "品目 C")
QUANTITY = ChartSeries("数量（個）", (Decimal(1200), Decimal("30.5"), None), "integer")
RATE = ChartSeries("比率", (Decimal("0.25"), Decimal("0.5"), Decimal("0.125")), "percent1")
PLAIN = ChartSeries("値", (Decimal(3), Decimal(2), Decimal(1)), None)


def _chart(data: ChartData) -> tuple[Any, bytes]:
    xml, workbook = chart_parts(data, font_name=FONT)
    return Chart(parse_xml(xml), None), workbook


def _values(element: Any, path: str) -> list[str]:
    return [str(value) for value in element.xpath(path)]


def test_every_chart_type_has_an_office_chart_kind() -> None:
    assert set(CHART_KINDS) == set(CHART_TYPES)
    assert CHART_KINDS["column"] == XL_CHART_TYPE.COLUMN_CLUSTERED
    assert CHART_KINDS["bar"] == XL_CHART_TYPE.BAR_CLUSTERED
    assert CHART_KINDS["pie"] == XL_CHART_TYPE.PIE
    # 色は系列・円の区分の上限の数だけあり、同じ色が無い。
    assert len({str(color) for color in CHART_COLORS}) == len(CHART_COLORS) == 8


@pytest.mark.parametrize("chart_type", list(CHART_TYPES), ids=list(CHART_TYPES))
def test_chart_values_match_the_given_values_in_the_xml_and_the_workbook(chart_type: str) -> None:
    series = (PLAIN,) if chart_type == "pie" else (QUANTITY, RATE)
    chart, workbook = _chart(ChartData(chart_type, CATEGORIES, series))

    assert chart.chart_type == CHART_KINDS[chart_type]
    [plot] = chart.plots
    assert list(plot.categories) == list(CATEGORIES)
    assert [item.name for item in plot.series] == [item.name for item in series]
    # 空の値は空のまま（0 にしない）。
    assert [list(item.values) for item in plot.series] == [
        [None if value is None else float(value) for value in item.values] for item in series
    ]
    # 埋め込みのブック（利用者が「データの編集」で開く表）にも、同じ値が入る。
    sheet = load_workbook(io.BytesIO(workbook)).active
    assert sheet is not None
    rows = [list(row) for row in sheet.iter_rows(values_only=True)]
    assert rows[0][1:] == [item.name for item in series]
    assert [row[0] for row in rows[1:]] == list(CATEGORIES)
    for position, item in enumerate(series, 1):
        assert [row[position] for row in rows[1:]] == [
            None if value is None else float(value) for value in item.values
        ]


def test_names_are_written_as_text_in_the_workbook_and_the_xml() -> None:
    """表の値を、数式・リンクとして評価される形でファイルに入れない。"""
    labels = ("=1+1", "http://example.invalid/x", "@SUM(1)", "#N/A", '<b>&"', "0012")
    values = tuple(Decimal(index) for index in range(1, len(labels) + 1))
    chart, workbook = _chart(ChartData("bar", labels, (ChartSeries("=2+2", values, None),)))

    sheet = load_workbook(io.BytesIO(workbook)).active
    assert sheet is not None
    names = [sheet.cell(row=1, column=2), *(sheet.cell(row=row, column=1) for row in range(2, 8))]
    assert [cell.value for cell in names] == ["=2+2", *labels]
    # `=` で始まる文字も文字型（数式にしない）。URL の形の文字もリンクにしない。
    assert {cell.data_type for cell in names} == {"s"}
    assert all(cell.hyperlink is None for cell in names)
    # グラフの XML でも、記号を含む名前がそのまま文字として読める。
    assert list(chart.plots[0].categories) == list(labels)
    assert [series.name for series in chart.plots[0].series] == ["=2+2"]


@pytest.mark.parametrize("chart_type", list(CHART_TYPES), ids=list(CHART_TYPES))
def test_template_defects_are_fixed(chart_type: str) -> None:
    series = (PLAIN,) if chart_type == "pie" else (QUANTITY,)
    chart, _ = _chart(ChartData(chart_type, CATEGORIES, series))
    root = chart.element

    # 軸の ID は正の整数（32 bit の符号つきの範囲）で、軸どうしの対応が保たれる。
    plot_ids = _values(root, "//c:plotArea/*[not(self::c:catAx or self::c:valAx)]/c:axId/@val")
    axis_ids = _values(root, "//c:catAx/c:axId/@val | //c:valAx/c:axId/@val")
    cross_ids = _values(root, "//c:catAx/c:crossAx/@val | //c:valAx/c:crossAx/@val")
    assert all(0 < int(value) < 2**31 for value in [*plot_ids, *axis_ids, *cross_ids])
    assert sorted(plot_ids) == sorted(axis_ids) == sorted(cross_ids)
    assert len(set(axis_ids)) == len(axis_ids) == (0 if chart_type == "pie" else 2)
    if axis_ids:
        assert cross_ids == list(reversed(axis_ids))
    # 棒と折れ線は、系列が 1 つでも棒・点ごとに色を変えない。円は区分ごとに色を変える。
    assert _values(root, "//c:varyColors/@val") == ["1" if chart_type == "pie" else "0"]
    # 字体は英数字と東アジアの両方に書き、字の大きさを明示する。
    [defaults] = root.xpath("c:txPr/a:p/a:pPr/a:defRPr")
    assert defaults.get("sz") == str(int(CHART_PT * 100))
    assert _values(defaults, "a:latin/@typeface") == [FONT]
    assert _values(defaults, "a:ea/@typeface") == [FONT]
    # 題は置かず、グラフの全体の枠の線と塗りは無い（`c:spPr` は `c:chart` のすぐ後ろ）。
    assert chart.has_title is False
    assert _values(root, "c:chart/c:autoTitleDeleted/@val") == ["1"]
    frame = root.find(qn("c:chart")).getnext()
    assert frame.tag == qn("c:spPr")
    assert [child.tag for child in frame] == [qn("a:noFill"), qn("a:ln")]
    assert [child.tag for child in frame.find(qn("a:ln"))] == [qn("a:noFill")]


def test_legend_is_shown_only_when_colors_tell_things_apart() -> None:
    single, _ = _chart(ChartData("column", CATEGORIES, (QUANTITY,)))
    many, _ = _chart(ChartData("column", CATEGORIES, (QUANTITY, RATE)))
    pie, _ = _chart(ChartData("pie", CATEGORIES, (PLAIN,)))

    assert single.has_legend is False
    assert many.has_legend and many.legend.position == XL_LEGEND_POSITION.BOTTOM
    assert pie.has_legend and pie.legend.position == XL_LEGEND_POSITION.RIGHT
    assert many.legend.include_in_layout is False


def test_series_colors_follow_the_fixed_order() -> None:
    series = tuple(
        ChartSeries(f"系列 {index}", (Decimal(index), Decimal(1), Decimal(2)), None)
        for index in range(1, 6)
    )
    for chart_type in ("column", "bar"):
        chart, _ = _chart(ChartData(chart_type, CATEGORIES, series))
        assert [item.format.fill.fore_color.rgb for item in chart.plots[0].series] == list(
            CHART_COLORS[:5]
        )
    line, _ = _chart(ChartData("line", CATEGORIES, series))
    assert [item.format.line.color.rgb for item in line.plots[0].series] == list(CHART_COLORS[:5])
    assert [item.marker.format.fill.fore_color.rgb for item in line.plots[0].series] == list(
        CHART_COLORS[:5]
    )
    assert all(item.smooth is False for item in line.plots[0].series)
    # 円は区分ごとに色を変える。
    pie, _ = _chart(ChartData("pie", CATEGORIES, (PLAIN,)))
    [pie_series] = pie.plots[0].series
    assert [
        pie_series.points[index].format.fill.fore_color.rgb for index in range(len(CATEGORIES))
    ] == list(CHART_COLORS[:3])


def test_number_formats_come_from_the_column_formats() -> None:
    chart, _ = _chart(ChartData("column", CATEGORIES, (RATE, QUANTITY, PLAIN)))
    root = chart.element

    # 系列の値の表示形式は列の書式（書式の無い列は値のまま）。
    assert _values(root, "//c:ser/c:val/c:numRef/c:numCache/c:formatCode/text()") == [
        "0.0%",
        "#,##0",
        "General",
    ]
    # 軸の目盛りは、1 つ目の系列の列の書式。
    labels = chart.value_axis.tick_labels
    assert (labels.number_format, labels.number_format_is_linked) == ("0.0%", False)
    # 項目の名前は間引かない。
    assert _values(root, "//c:catAx/c:tickLblSkip/@val") == ["1"]


def test_value_labels_are_shown_only_where_they_stay_readable() -> None:
    def labeled(chart_type: str, categories: int, series: int = 1) -> bool:
        data = ChartData(
            chart_type,
            tuple(f"品目 {index}" for index in range(categories)),
            tuple(
                ChartSeries(f"系列 {index}", tuple(Decimal(1) for _ in range(categories)), None)
                for index in range(series)
            ),
        )
        chart, _ = _chart(data)
        return bool(chart.plots[0].has_data_labels)

    # 系列が 1 つの横棒と、項目の少ない縦棒にだけ、値のラベルを付ける。
    assert labeled("bar", 20) and labeled("column", MAX_LABELED_COLUMNS)
    assert not labeled("column", MAX_LABELED_COLUMNS + 1)
    assert not labeled("column", 3, series=2) and not labeled("bar", 3, series=2)
    assert not labeled("line", 3)


def test_horizontal_bars_list_the_rows_from_the_top() -> None:
    bar, _ = _chart(ChartData("bar", CATEGORIES, (QUANTITY,)))
    column, _ = _chart(ChartData("column", CATEGORIES, (QUANTITY,)))

    # 横棒は項目の並びを逆にして表の順に上から並べ、値の軸は下に置く。
    assert bar.category_axis.reverse_order is True
    assert _values(bar.element, "//c:valAx/c:crosses/@val") == ["max"]
    assert column.category_axis.reverse_order is False
    assert _values(column.element, "//c:valAx/c:crosses/@val") == ["autoZero"]


def test_pie_shows_percentages_inside_a_fixed_plot_area() -> None:
    chart, _ = _chart(ChartData("pie", CATEGORIES, (PLAIN,)))
    labels = chart.plots[0].data_labels

    assert (labels.show_percentage, labels.show_value) == (True, False)
    assert (labels.number_format, labels.number_format_is_linked) == (PIE_LABEL_FORMAT, False)
    assert labels.font.size == Pt(10.5)
    # 円を描く範囲を決めて、外側のラベルがグラフの縁に重ならないようにする。
    plot_area = chart.element.find(qn("c:chart")).find(qn("c:plotArea"))
    assert plot_area[0].tag == qn("c:layout")
    layout = {child.tag.split("}")[1]: child.get("val") for child in plot_area[0][0]}
    assert (layout["layoutTarget"], layout["xMode"], layout["yMode"]) == ("inner", "edge", "edge")
    left, top, width, height = (float(layout[name]) for name in ("x", "y", "w", "h"))
    assert 0 < left < left + width < 1 and 0 < top < top + height < 1
