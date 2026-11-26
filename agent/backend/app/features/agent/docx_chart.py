"""Word の文書（python-docx）に、Office のグラフを入れる（#5837）。

グラフの XML と埋め込みのブックは `chart_drawing.chart_parts`（PowerPoint と同じ部品）が作る。ここは
それを文書のファイル（OPC の package）にグラフの部品として足し、本文の段落に行内の図として置く。
Run・DB・ツールの層に依らない。

- python-docx にグラフの API が無いので、文書の API の下の層（`docx.opc` の `Part`・`XmlPart`、
  package の `next_partname`、part の `relate_to`・`next_id`、run の `_r`）を使う。使う所はこの
  module だけにする。確かめた python-docx の版は `tests/test_docx_chart.py` が持つ。版を上げる
  ときは、そのテスト（部品・関連・content type・要素の順）に加えて、実機の Word で開いて確かめ直す。
- グラフ 1 つにつき、ファイルに足すもの:
  - `/word/charts/chartN.xml`（`c:chartSpace`。content type は Override）。
  - `/word/embeddings/Microsoft_Excel_SheetN.xlsx`（グラフの値のブック。利用者が「データの編集」で
    開く。content type は拡張子 `xlsx` の Default）。
  - 文書 → グラフ（chart）と、グラフ → ブック（package）の関連。グラフの XML の `c:externalData` が
    ブックの関連を指す。
- 本文には `w:drawing` の `wp:inline`（行内）で置く。段落の中の 1 文字として流れるので、ページを
  またがず、収まらなければ次のページへ送られる。`wp:docPr` の id は文書の中で一意の番号にする。
"""

from __future__ import annotations

from typing import Any

from docx.opc.constants import CONTENT_TYPE, RELATIONSHIP_TYPE
from docx.opc.part import Part, XmlPart
from docx.oxml import OxmlElement, parse_xml
from docx.oxml.ns import nsmap, qn
from docx.shared import Length
from docx.text.paragraph import Paragraph

from app.features.agent.chart_drawing import chart_parts
from app.features.agent.chart_spec import ChartData

# 部品の名前（Word が作る文書と同じ置き場所。`%d` は 1 からの番号）。
CHART_PARTNAME = "/word/charts/chart%d.xml"
WORKBOOK_PARTNAME = "/word/embeddings/Microsoft_Excel_Sheet%d.xlsx"
# `c:chartSpace` の中で、`c:externalData` より後ろに置く要素（schema の順）。
_AFTER_EXTERNAL_DATA = tuple(qn(f"c:{tag}") for tag in ("printSettings", "userShapes", "extLst"))


def _element(tag: str, **attributes: str) -> Any:
    """名前空間つきの tag（`wp:inline` など）と、名前空間なしの属性の要素。"""
    prefix = tag.split(":", maxsplit=1)[0]
    element = OxmlElement(tag, nsdecls={prefix: nsmap[prefix], "r": nsmap["r"]})
    for name, value in attributes.items():
        element.set(name, value)
    return element


def _link_workbook(chart_space: Any, relation_id: str) -> None:
    """グラフの XML に、埋め込みのブックの関連（`c:externalData`）を書く。"""
    external = _element("c:externalData")
    external.set(qn("r:id"), relation_id)
    # ブックの値が変わっても、開いたときに自動では読み直さない（Office が作るグラフと同じ）。
    external.append(_element("c:autoUpdate", val="0"))
    successor = next((child for child in chart_space if child.tag in _AFTER_EXTERNAL_DATA), None)
    if successor is None:
        chart_space.append(external)
    else:
        successor.addprevious(external)


def _inline_drawing(
    relation_id: str, shape_id: int, *, width: Length, height: Length, name: str, description: str
) -> Any:
    """本文に行内で置くグラフ（`w:drawing`。子の順は schema の順）。"""
    chart = _element("c:chart")
    chart.set(qn("r:id"), relation_id)
    data = _element("a:graphicData", uri=nsmap["c"])
    data.append(chart)
    graphic = _element("a:graphic")
    graphic.append(data)
    properties = _element("wp:docPr", id=str(shape_id), name=name)
    if description:
        # 代替テキスト（読み上げ・画像の無い表示で、グラフの代わりに読まれる）。
        properties.set("descr", description)
    inline = _element("wp:inline", distT="0", distB="0", distL="0", distR="0")
    for child in (
        _element("wp:extent", cx=str(int(width)), cy=str(int(height))),
        _element("wp:effectExtent", l="0", t="0", r="0", b="0"),
        properties,
        _element("wp:cNvGraphicFramePr"),
        graphic,
    ):
        inline.append(child)
    drawing = _element("w:drawing")
    drawing.append(inline)
    return drawing


def add_inline_chart(
    paragraph: Paragraph,
    data: ChartData,
    *,
    font_name: str,
    text_pt: float,
    label_pt: float,
    width: Length,
    height: Length,
    name: str,
    description: str = "",
) -> None:
    """段落の末尾に、グラフを行内の図として足す（グラフの部品と関連を文書のファイルに足す）。

    `text_pt`・`label_pt` はグラフの字の大きさ（`chart_drawing.style_chart`）、`width`・`height` は
    グラフの大きさ、`name` は図の名前（Word の「選択」の一覧に出る）、`description` は代替テキスト。
    """
    document_part = paragraph.part
    # python-docx は、part の `package`（部品の名前を決める所）に型を付けていない。
    package: Any = document_part.package
    chart_xml, workbook = chart_parts(data, font_name=font_name, text_pt=text_pt, label_pt=label_pt)
    # `next_partname` は、文書からたどれる部品の名前だけを見る。同じ種類の部品を続けて作る前に、
    # 関連を足して文書からたどれるようにする（グラフごとに、ブック → グラフ → 関連の順）。
    workbook_part = Part(
        package.next_partname(WORKBOOK_PARTNAME), CONTENT_TYPE.SML_SHEET, workbook, package
    )
    chart_part = XmlPart(
        package.next_partname(CHART_PARTNAME), CONTENT_TYPE.DML_CHART, parse_xml(chart_xml), package
    )
    _link_workbook(
        chart_part.element, chart_part.relate_to(workbook_part, RELATIONSHIP_TYPE.PACKAGE)
    )
    drawing = _inline_drawing(
        document_part.relate_to(chart_part, RELATIONSHIP_TYPE.CHART),
        document_part.next_id,
        width=width,
        height=height,
        name=name,
        description=description,
    )
    paragraph.add_run()._r.append(drawing)  # noqa: SLF001 - python-docx にグラフの API が無い


__all__ = ["CHART_PARTNAME", "WORKBOOK_PARTNAME", "add_inline_chart"]
