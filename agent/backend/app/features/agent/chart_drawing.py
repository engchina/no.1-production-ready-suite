"""グラフの描画の部品（#5837）。グラフに描く値（`chart_spec.ChartData`）から、Office のグラフ
（DrawingML の `c:chartSpace` と、値を持つ埋め込みのブック）を作る。

スライド・文書・Run に依らない（グラフの XML とブックは、PowerPoint と Word で同じ形）。グラフの
雛形は python-pptx が作り、ここは固定の体裁と、雛形のままでは Office で崩れる所の手当てを当てる。
埋め込みのブックは、ここで作る（`chart_workbook`）。指定（種類・列・上限）の検証は `chart_spec`。

- 入口は 2 つ。`chart_values`・`style_chart`・`chart_workbook` は、python-pptx がスライドに置いた
  グラフに使う（`pptx_presentation`）。`chart_parts` は、同じ体裁のグラフの XML とブックをバイト列で
  返す（スライドを持たない形式が、自分のファイルに部品として入れる）。
- 埋め込みのブック（利用者が「データの編集」で開く表）は、項目と系列の名前を文字型で書く。
  python-pptx が作るブックは、`=` で始まる文字を数式に、URL の形の文字をリンクにするので、使わない
  （表の値を、数式として評価される形でファイルに入れない）。
- 体裁は種類ごとに決まっている（呼ぶ側が決めるのは、字体と字の大きさだけ。字の大きさの既定は
  スライド用の `CHART_PT`・`CHART_LABEL_PT` で、文書に置く側は本文に合う大きさを渡す）。
  - 題と、グラフの全体の枠の線は置かない（題は呼ぶ側が、スライドの題・文書の見出しで示す）。
  - 色は `CHART_COLORS` をこの順に使う（系列ごと。円は区分ごと）。
  - 凡例は、色で見分けるものが 2 つ以上のときだけ（複数の系列は下、円の区分は右）。
  - 値のラベルは、系列が 1 つの横棒と、系列が 1 つで項目が `MAX_LABELED_COLUMNS` 個までの縦棒
    （値）、円（割合）にだけ付ける。ほかは軸の目盛りで読む。数値の表示形式は、列の書式から決める
    （軸の目盛りは 1 つ目の系列の列の書式）。項目の名前は間引かない。
  - 横棒は、表の上の行から順に、上から並べる。
  - 空の値は、棒を置かず、線をつながない。
  - 負の値があっても読めるようにする: 負の値の棒も系列の色で塗り（`c:invertIfNegative` が無いと、
    Office は負の値の棒を白く塗って黒い線で囲む）、項目の名前はグラフの端（縦棒・折れ線は下、
    横棒は左）に置く（既定の「軸のそば」だと、0 の線の所に出て、負の値の棒・線に重なる。負の値が
    無いグラフでは、0 の線が端なので、置き場所は変わらない）。
- 雛形の手当て: 軸の ID を 1 からの番号に振り直す（雛形の負の数は、schema の符号なしの整数に
  合わない。32 bit の符号つきの整数を超える値は、LibreOffice が軸を取り違える）。棒と
  折れ線は `c:varyColors` を 0 にする（無いと、系列が 1 つのときに Office が棒ごとに色を変える）。
  字体は `a:latin` に加えて東アジアの `a:ea` も書く。字の大きさを明示する（既定はスライドの本文用の
  大きさで、グラフには大きすぎる）。凡例の位置は、既定の値（右）でも属性に書く（python-pptx は
  既定の値の属性を省く。schema では省けるが、省くと openpyxl がグラフを読めない）。
"""

from __future__ import annotations

import io
from typing import Any

from openpyxl import Workbook  # type: ignore[import-untyped]
from pptx.chart.chart import Chart
from pptx.chart.data import CategoryChartData
from pptx.dml.color import RGBColor
from pptx.enum.chart import (
    XL_CHART_TYPE,
    XL_LABEL_POSITION,
    XL_LEGEND_POSITION,
    XL_MARKER_STYLE,
    XL_TICK_LABEL_POSITION,
    XL_TICK_MARK,
)
from pptx.opc.oxml import serialize_part_xml
from pptx.oxml import parse_xml
from pptx.oxml.ns import qn
from pptx.oxml.xmlchemy import OxmlElement
from pptx.util import Pt

from app.features.agent.chart_spec import PIE, ChartData
from app.features.agent.table_columns import NUMBER_FORMATS

# グラフの種類（`chart_spec.CHART_TYPES` の key）→ Office のグラフの種類。
CHART_KINDS: dict[str, XL_CHART_TYPE] = {
    "column": XL_CHART_TYPE.COLUMN_CLUSTERED,
    "bar": XL_CHART_TYPE.BAR_CLUSTERED,
    "line": XL_CHART_TYPE.LINE_MARKERS,
    PIE: XL_CHART_TYPE.PIE,
}
# 系列・円の区分の色（この順に使う。隣り合う色が色覚の違いに依らず見分けられる並び）。
CHART_COLORS = tuple(
    RGBColor.from_string(code)
    for code in ("2A78D6", "EB6834", "1BAF7A", "EDA100", "E87BA4", "008300", "6250D6", "E34948")
)
# 埋め込みのブックのシートの名前（python-pptx のグラフの XML が参照する名前）。
_SHEET_NAME = "Sheet1"
# 字の大きさ（軸・凡例と、値のラベル）の既定。スライドに置くグラフに合う値。
CHART_PT = 12.0
CHART_LABEL_PT = 10.5
_TEXT = RGBColor(0x26, 0x26, 0x26)
_GRID = RGBColor(0xD9, 0xD9, 0xD9)
_WHITE = RGBColor(0xFF, 0xFF, 0xFF)
_LINE_PT = 2.25
_MARKER_SIZE = 7
# 円の割合のラベルの表示形式と、円を描く範囲（グラフの全体に対する左・上・幅・高さの比）。
PIE_LABEL_FORMAT = "0.0%"
_PIE_AREA = (0.1, 0.12, 0.6, 0.76)
# 系列が 1 つの縦棒に値のラベルを付ける、項目の数の上限（多いと隣のラベルと重なる）。
MAX_LABELED_COLUMNS = 12


def apply_font(font: Any, name: str) -> None:
    """DrawingML の文字の字体を 1 つに決める（英数字の `a:latin` と、東アジアの文字の `a:ea`）。"""
    font.name = name
    properties = font._element  # noqa: SLF001 - python-pptx に東アジアの字体の API が無い
    for east_asian in properties.findall(qn("a:ea")):
        properties.remove(east_asian)
    east_asian = OxmlElement("a:ea")
    east_asian.set("typeface", name)
    properties.find(qn("a:latin")).addnext(east_asian)


def chart_number_format(format_name: str | None) -> str:
    """グラフの数値の表示形式（列の書式の名前から。Excel の表示形式と同じ書き方）。"""
    return "General" if format_name is None else NUMBER_FORMATS[format_name]


def chart_values(data: ChartData) -> CategoryChartData:
    """python-pptx に渡すグラフの値（項目の名前と、系列ごとの値・表示形式。空の値は None）。"""
    values = CategoryChartData()
    values.categories = list(data.categories)
    for series in data.series:
        values.add_series(
            series.name,
            [None if value is None else float(value) for value in series.values],
            number_format=chart_number_format(series.format),
        )
    return values


def chart_workbook(data: ChartData) -> bytes:
    """グラフの値を持つ埋め込みのブック（.xlsx）。

    配置は、python-pptx のグラフの XML が参照する形（1 列目の 2 行目から項目の名前、2 列目から
    系列。系列は 1 行目が名前、2 行目から値）。名前は文字型で書き、空の値の cell は置かない。
    """
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = _SHEET_NAME
    for row, label in enumerate(data.categories, 2):
        _write_text(sheet.cell(row=row, column=1), label)
    for column, series in enumerate(data.series, 2):
        _write_text(sheet.cell(row=1, column=column), series.name)
        for row, value in enumerate(series.values, 2):
            if value is not None:
                cell = sheet.cell(row=row, column=column, value=float(value))
                cell.number_format = chart_number_format(series.format)
    buffer = io.BytesIO()
    workbook.save(buffer)
    return buffer.getvalue()


def _write_text(cell: Any, text: str) -> None:
    # 文字型を明示する（openpyxl は `=` で始まる文字を数式、`#N/A` などをエラー値の型にする）。
    cell.value = text
    cell.data_type = "s"


def style_chart(
    chart: Any,
    data: ChartData,
    *,
    font_name: str,
    text_pt: float = CHART_PT,
    label_pt: float = CHART_LABEL_PT,
) -> None:
    """python-pptx のグラフ（`chart_values` の値で作ったもの）に、固定の体裁と手当てを当てる。

    `text_pt` は軸・凡例の字の大きさ、`label_pt` は値のラベルの字の大きさ（pt）。
    """
    axis_ids: dict[str, str] = {}
    for axis_id in chart.element.iter(qn("c:axId"), qn("c:crossAx")):
        # 雛形の ID（負の数を含む）を、同じ対応のまま 1 からの番号に振り直す。
        axis_id.set("val", axis_ids.setdefault(axis_id.get("val"), str(len(axis_ids) + 1)))
    chart.has_title = False
    _clear_frame(chart)
    chart.font.size = Pt(text_pt)
    chart.font.color.rgb = _TEXT
    apply_font(chart.font, font_name)
    plot = chart.plots[0]
    pie = data.type == PIE
    many = len(data.series) > 1
    chart.has_legend = pie or many
    if chart.has_legend:
        chart.legend.position = XL_LEGEND_POSITION.RIGHT if pie else XL_LEGEND_POSITION.BOTTOM
        chart.legend.include_in_layout = False
        # 位置が既定の値（右）のとき、python-pptx は属性を省く。省かずに書く。
        for position in chart.element.iter(qn("c:legendPos")):
            position.set("val", position.get("val", "r"))
    if pie:
        _style_pie(plot, data, label_pt)
        return
    plot.vary_by_categories = False
    _style_axes(chart, data)
    for series, color in zip(plot.series, CHART_COLORS, strict=False):
        if data.type == "line":
            series.smooth = False
            series.format.line.color.rgb = color
            series.format.line.width = Pt(_LINE_PT)
            series.marker.style = XL_MARKER_STYLE.CIRCLE
            series.marker.size = _MARKER_SIZE
            series.marker.format.fill.solid()
            series.marker.format.fill.fore_color.rgb = color
            series.marker.format.line.color.rgb = color
        else:
            series.format.fill.solid()
            series.format.fill.fore_color.rgb = color
            # 負の値の棒も、系列の色で塗る（指定が無いと、Office は白く塗って黒い線で囲む）。
            series.invert_if_negative = False
    if data.type != "line":
        plot.gap_width = 80
        plot.overlap = -10
        labeled = not many and (data.type == "bar" or len(data.categories) <= MAX_LABELED_COLUMNS)
        plot.has_data_labels = labeled
        if labeled:
            labels = plot.data_labels
            labels.show_value = True
            labels.position = XL_LABEL_POSITION.OUTSIDE_END
            labels.font.size = Pt(label_pt)


def _clear_frame(chart: Any) -> None:
    """グラフの全体の枠の線と塗りを無くす（python-pptx に、グラフの全体の体裁の API が無い）。"""
    properties = OxmlElement("c:spPr")
    properties.append(OxmlElement("a:noFill"))
    line = OxmlElement("a:ln")
    line.append(OxmlElement("a:noFill"))
    properties.append(line)
    # `c:spPr` は `c:chart` のすぐ後ろに置く。
    chart.element.find(qn("c:chart")).addnext(properties)


def _style_axes(chart: Any, data: ChartData) -> None:
    """軸の体裁（薄い目盛り線・目盛りの印なし）。横棒は、表の上の行から順に上から並べる。"""
    category_axis, value_axis = chart.category_axis, chart.value_axis
    category_axis.has_major_gridlines = False
    category_axis.major_tick_mark = XL_TICK_MARK.NONE
    # 項目の名前は、グラフの端に置く（軸のそばだと、負の値があるときに棒・線と重なる）。
    category_axis.tick_label_position = XL_TICK_LABEL_POSITION.LOW
    category_axis.format.line.color.rgb = _GRID
    value_axis.major_tick_mark = XL_TICK_MARK.NONE
    value_axis.format.line.fill.background()
    value_axis.has_major_gridlines = True
    value_axis.major_gridlines.format.line.color.rgb = _GRID
    value_axis.major_gridlines.format.line.width = Pt(0.75)
    # 軸の目盛りの表示形式は、1 つ目の系列の列の書式にする。
    value_axis.tick_labels.number_format = chart_number_format(data.series[0].format)
    value_axis.tick_labels.number_format_is_linked = False
    # 項目の名前を間引かない（python-pptx に、目盛りの名前の間隔の API が無い）。
    skip = OxmlElement("c:tickLblSkip")
    skip.set("val", "1")
    category_axis._element.find(qn("c:lblOffset")).addnext(skip)  # noqa: SLF001
    if data.type == "bar":
        category_axis.reverse_order = True
        # 項目の並びを逆にしても、値の軸を下に置く（python-pptx に、値の軸が項目の軸と交わる位置の
        # API が無い）。
        for crosses in value_axis._element.iter(qn("c:crosses")):  # noqa: SLF001
            crosses.set("val", "max")


def _style_pie(plot: Any, data: ChartData, label_pt: float) -> None:
    """円: 区分ごとの色と白い境の線、割合のラベル（字の大きさは `label_pt`）。"""
    [series] = plot.series
    for index, color in zip(range(len(data.categories)), CHART_COLORS, strict=False):
        point = series.points[index]
        point.format.fill.solid()
        point.format.fill.fore_color.rgb = color
        point.format.line.color.rgb = _WHITE
        point.format.line.width = Pt(1.5)
    plot.has_data_labels = True
    labels = plot.data_labels
    labels.show_value = False
    labels.show_percentage = True
    labels.number_format = PIE_LABEL_FORMAT
    labels.number_format_is_linked = False
    labels.position = XL_LABEL_POSITION.OUTSIDE_END
    labels.font.size = Pt(label_pt)
    # 円を描く範囲を決めて、外側のラベルと凡例の場所を空ける（自動の配置では、ラベルがグラフの縁に
    # 重なることがある。python-pptx に、描く範囲の API が無い）。
    layout = OxmlElement("c:manualLayout")
    for tag, value in (
        ("c:layoutTarget", "inner"),
        ("c:xMode", "edge"),
        ("c:yMode", "edge"),
        *zip(("c:x", "c:y", "c:w", "c:h"), map(str, _PIE_AREA), strict=True),
    ):
        child = OxmlElement(tag)
        child.set("val", value)
        layout.append(child)
    plot_area = series._element.getparent().getparent()  # noqa: SLF001
    for existing in plot_area.findall(qn("c:layout")):
        plot_area.remove(existing)
    container = OxmlElement("c:layout")
    container.append(layout)
    plot_area.insert(0, container)


def chart_parts(
    data: ChartData,
    *,
    font_name: str,
    text_pt: float = CHART_PT,
    label_pt: float = CHART_LABEL_PT,
) -> tuple[bytes, bytes]:
    """グラフの XML（`c:chartSpace`。体裁と手当てを当てたもの）と、埋め込みのブック（.xlsx）。

    スライドを持たない形式が、自分のファイルにグラフの部品として入れる。XML はブックを参照しない
    （ブックとの関連付けは、入れる側が自分のファイルの関連として足す）。字の大きさ（`text_pt`・
    `label_pt`。`style_chart` と同じ）は、置く先の本文に合わせて渡す。
    """
    root = parse_xml(chart_values(data).xml_bytes(CHART_KINDS[data.type]))
    style_chart(Chart(root, None), data, font_name=font_name, text_pt=text_pt, label_pt=label_pt)
    return serialize_part_xml(root), chart_workbook(data)


__all__ = [
    "CHART_COLORS",
    "CHART_KINDS",
    "CHART_LABEL_PT",
    "CHART_PT",
    "MAX_LABELED_COLUMNS",
    "PIE_LABEL_FORMAT",
    "apply_font",
    "chart_number_format",
    "chart_parts",
    "chart_values",
    "chart_workbook",
    "style_chart",
]
