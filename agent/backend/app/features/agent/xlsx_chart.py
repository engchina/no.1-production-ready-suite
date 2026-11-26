"""Excel のシートに置くグラフ（#5837）。そのシートの表の cell を参照する Office のグラフ。

Run・DB・ツールの層に依らない。指定（種類・列・上限）の検証は `chart_spec`、グラフの XML（種類ごとに
決まっている色・凡例・値のラベル・軸の体裁・負の値の表示と、雛形の手当て）は PowerPoint・Word と
同じ `chart_drawing` が作る。ここは Excel に合わせる所だけを持つ。

- 値の参照（`c:f`）を、そのシートの表の cell に書き換える。系列の名前は表の見出しの cell、項目の
  名前と系列の値は表のデータの行（集計の行は含めない）。利用者が cell の値を変えると、グラフも
  変わる。グラフ用の値を別に持たない（埋め込みのブックは無い。XML に入る値の控えは、cell に書いた
  値と同じもの）。
- 項目の列が数値・日付のときは、項目の参照を数値の参照（`c:numRef`）にして、軸の目盛りに cell の
  表示形式を使う（文字の参照 `c:strRef` のままだと、Excel は日付を通し番号で表示する）。
- 項目の軸は、cell の値が日付でも、表の行の順に等間隔で並べる（`c:auto` を 0 にして、Excel が
  日付の軸に切り替えないようにする）。
- グラフの全体を白く塗り、薄い線で囲む（透明のままだと、シートの枠線が透ける）。角は丸めない
  （`c:roundedCorners` が無いと、Excel は枠の角を丸める）。
- 字の大きさは、シートの cell の字に合わせて小さくする（`chart_drawing` の大きさはスライド用）。
  グラフの大きさも同じ割合で小さくし、縦と横の比はスライドのグラフに合わせる（値のラベル・凡例・
  円の配置は、その比で重ならないように決まっている）。
- 置き場所は表の右（1 列空けて、表の見出しの行から）。大きさは決まっている（`CHART_WIDTH_CM`・
  `CHART_HEIGHT_CM`。横棒だけは、棒の数が多いときに、縦棒と同じ棒の太さになる高さまで高くする）。
  cell と一緒に動くが、列の幅・行の高さを変えても大きさは変わらない。
- openpyxl の chart の class（`BarChart` など）では描かない（同じ体裁をもう 1 つ書くことになる）。
  openpyxl に渡すのは、置き場所・大きさと、書き出す XML だけ（`ChartBase._write` を差し替える）。
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from openpyxl.chart import Reference  # type: ignore[import-untyped]
from openpyxl.chart._chart import ChartBase  # type: ignore[import-untyped]
from openpyxl.utils import get_column_letter  # type: ignore[import-untyped]
from openpyxl.utils.datetime import to_excel  # type: ignore[import-untyped]
from openpyxl.worksheet.worksheet import Worksheet  # type: ignore[import-untyped]
from openpyxl.xml.functions import fromstring  # type: ignore[import-untyped]
from pptx.chart.chart import Chart
from pptx.opc.oxml import serialize_part_xml
from pptx.oxml import parse_xml
from pptx.oxml.ns import qn
from pptx.oxml.xmlchemy import OxmlElement
from pptx.util import Pt

from app.features.agent.chart_drawing import (
    CHART_KINDS,
    CHART_LABEL_PT,
    CHART_PT,
    chart_values,
    style_chart,
)
from app.features.agent.chart_spec import PIE, ChartData, ChartSpec

# グラフの大きさ（cm）と、表とグラフの間に空ける列の数。
CHART_WIDTH_CM = 24.0
CHART_HEIGHT_CM = 10.5
CHART_GAP_COLUMNS = 1
# 横棒の高さを決める値（cm）: 棒 1 本の太さ・項目の間・軸と凡例の分。
_BAR_CM = 0.16
_BAR_GAP_CM = 0.14
_BAR_MARGIN_CM = 2.0
# 字の大きさ（軸・凡例と、値のラベル）。
SHEET_CHART_PT = 10.0
SHEET_CHART_LABEL_PT = 9.0
_FRAME_FILL = "FFFFFF"
_FRAME_LINE = "D9D9D9"
_FRAME_LINE_PT = 0.75


class _SheetChart(ChartBase):  # type: ignore[misc]
    """openpyxl がシートに置くグラフ。書き出す XML は、ここに持つもの（`sheet_chart_xml`）。"""

    def __init__(self, xml: bytes) -> None:
        super().__init__()
        self._xml = xml

    def _write(self) -> Any:
        return fromstring(self._xml)


def _solid_fill(color: str) -> Any:
    fill = OxmlElement("a:solidFill")
    value = OxmlElement("a:srgbClr")
    value.set("val", color)
    fill.append(value)
    return fill


def _fill_frame(root: Any) -> None:
    """グラフの全体を白く塗り、薄い線で囲む（角は丸めない）。"""
    for existing in root.findall(qn("c:roundedCorners")):
        root.remove(existing)
    corners = OxmlElement("c:roundedCorners")
    corners.set("val", "0")
    # `c:roundedCorners` は、`c:chart` より前で、`c:date1904`・`c:lang` の後ろに置く。
    leading = [child for child in root if child.tag in (qn("c:date1904"), qn("c:lang"))]
    root.insert(len(leading), corners)
    properties = root.find(qn("c:spPr"))
    for child in list(properties):
        properties.remove(child)
    properties.append(_solid_fill(_FRAME_FILL))
    line = OxmlElement("a:ln")
    line.set("w", str(int(Pt(_FRAME_LINE_PT))))
    line.append(_solid_fill(_FRAME_LINE))
    properties.append(line)


def _resize_text(root: Any) -> None:
    """字の大きさを、スライド用からシート用に変える。"""
    sizes = {
        str(int(CHART_PT * 100)): str(int(SHEET_CHART_PT * 100)),
        str(int(CHART_LABEL_PT * 100)): str(int(SHEET_CHART_LABEL_PT * 100)),
    }
    for properties in root.iter(qn("a:defRPr")):
        size = properties.get("sz")
        if size in sizes:
            properties.set("sz", sizes[size])


def _number_reference(reference: str, cells: Sequence[Any]) -> Any:
    """数値・日付の cell の参照（`c:numRef`）と、値の控え（cell の値と表示形式のまま）。"""
    container = OxmlElement("c:numRef")
    formula = OxmlElement("c:f")
    formula.text = reference
    container.append(formula)
    cache = OxmlElement("c:numCache")
    code = OxmlElement("c:formatCode")
    code.text = cells[0].number_format
    count = OxmlElement("c:ptCount")
    count.set("val", str(len(cells)))
    cache.extend((code, count))
    for index, cell in enumerate(cells):
        point = OxmlElement("c:pt")
        point.set("idx", str(index))
        value = OxmlElement("c:v")
        # 日付の cell の値は、Excel の通し番号。
        value.text = repr(to_excel(cell.value) if cell.is_date else cell.value)
        point.append(value)
        cache.append(point)
    container.append(cache)
    return container


def sheet_chart_xml(
    worksheet: Worksheet,
    spec: ChartSpec,
    data: ChartData,
    *,
    header_row: int,
    first_row: int,
    last_row: int,
    font_name: str,
) -> bytes:
    """そのシートの表の cell を参照するグラフの XML（`c:chartSpace`）。

    `data` は、`spec` でその表から取り出した値（cell に書いた値と同じもの）。表は、見出しの行が
    `header_row`、データの行が `first_row`〜`last_row`、列は `spec` の番号のとおりに A 列から並ぶ。
    """
    root = parse_xml(chart_values(data).xml_bytes(CHART_KINDS[data.type]))
    chart = Chart(root, None)
    style_chart(chart, data, font_name=font_name)

    def cells(column: int, start: int, end: int) -> str:
        return str(Reference(worksheet, min_col=column, min_row=start, max_col=column, max_row=end))

    categories = [
        worksheet.cell(row=row, column=spec.category_column)
        for row in range(first_row, last_row + 1)
    ]
    # 文字の列の cell は文字型（"s"）。数値・日付の列の cell は、どれも数値か日付。
    numeric = all(cell.data_type != "s" for cell in categories)
    for series, column in zip(root.iter(qn("c:ser")), spec.series_columns, strict=True):
        for tag, reference in (
            ("c:tx", cells(column, header_row, header_row)),
            ("c:cat", cells(spec.category_column, first_row, last_row)),
            ("c:val", cells(column, first_row, last_row)),
        ):
            holder = series.find(qn(tag))
            if tag == "c:cat" and numeric:
                for child in list(holder):
                    holder.remove(child)
                holder.append(_number_reference(reference, categories))
                continue
            [formula] = holder.iter(qn("c:f"))
            formula.text = reference
    if data.type != PIE:
        if numeric:
            labels = chart.category_axis.tick_labels
            labels.number_format = categories[0].number_format
            labels.number_format_is_linked = True
        for auto in root.iter(qn("c:auto")):
            auto.set("val", "0")
    _fill_frame(root)
    _resize_text(root)
    return bytes(serialize_part_xml(root))


def chart_height_cm(data: ChartData) -> float:
    """グラフの高さ（cm）。横棒は、棒の数に合わせて高くする。"""
    if data.type != "bar":
        return CHART_HEIGHT_CM
    bars = len(data.categories) * (_BAR_GAP_CM + len(data.series) * _BAR_CM)
    return max(CHART_HEIGHT_CM, round(_BAR_MARGIN_CM + bars, 1))


def add_sheet_chart(
    worksheet: Worksheet,
    spec: ChartSpec,
    data: ChartData,
    *,
    header_row: int,
    first_row: int,
    last_row: int,
    columns: int,
    font_name: str,
) -> None:
    """表の右にグラフを置く（`columns` は表の列の数。ほかの引数は `sheet_chart_xml` と同じ）。"""
    chart = _SheetChart(
        sheet_chart_xml(
            worksheet,
            spec,
            data,
            header_row=header_row,
            first_row=first_row,
            last_row=last_row,
            font_name=font_name,
        )
    )
    chart.width = CHART_WIDTH_CM
    chart.height = chart_height_cm(data)
    worksheet.add_chart(chart, f"{get_column_letter(columns + 1 + CHART_GAP_COLUMNS)}{header_row}")


__all__ = [
    "CHART_GAP_COLUMNS",
    "CHART_HEIGHT_CM",
    "CHART_WIDTH_CM",
    "SHEET_CHART_LABEL_PT",
    "SHEET_CHART_PT",
    "add_sheet_chart",
    "chart_height_cm",
    "sheet_chart_xml",
]
