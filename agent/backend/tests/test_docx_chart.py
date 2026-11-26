"""Word の文書のグラフ（`docx_document` のグラフのブロックと、埋め込みの `docx_chart`。#5837）。

作ったバイト列を、zip の部品・python-docx・python-pptx・openpyxl で読み直して確かめる（Run・DB・
モデルは使わない。値は合成）。

- ファイルの構造: グラフと値のブックの部品、`[Content_Types].xml`、文書 → グラフ → ブックの関連、
  `c:externalData`、本文の `wp:inline`（子の順は schema の順）、`wp:docPr` の id。
- グラフの中身: 値は表の値と一致する。体裁は PowerPoint と共通の部品で、字の大きさだけ文書用。
- 文書の中の位置: 題・改ページ・表との並び。グラフの値の表は文書に出ない。
- python-docx で開き直して保存しても、グラフの部品が残る。
- 作れない指定（種類・列・系列と項目の数・値）は、切り詰めずに誤りにする。
"""

from __future__ import annotations

import io
import posixpath
import zipfile
from decimal import Decimal
from importlib.metadata import version
from typing import Any

import pytest
from docx import Document
from docx.enum.shape import WD_INLINE_SHAPE
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.opc.constants import CONTENT_TYPE, RELATIONSHIP_TYPE
from docx.shared import Mm
from docx.table import Table
from lxml import etree  # type: ignore[import-untyped]
from openpyxl import load_workbook  # type: ignore[import-untyped]
from pptx.chart.chart import Chart
from pptx.oxml import parse_xml
from pytest import MonkeyPatch

from app.features.agent import docx_document
from app.features.agent.chart_drawing import CHART_KINDS, chart_parts
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
from app.features.agent.docx_document import (
    BLOCK_TYPES,
    CHART_HEIGHT_MM,
    CHART_LABEL_PT,
    CHART_TEXT_PT,
    CHART_WIDTH_MM,
    FONT_NAME,
    MAX_CHARTS,
    MAX_TABLE_COLUMNS,
    Block,
    BuiltDocument,
    ChartBlock,
    Column,
    DocumentContent,
    DocxSpecError,
    HeadingBlock,
    PageBreakBlock,
    ParagraphBlock,
    TableBlock,
    build_document,
)

# `docx_chart` が使う python-docx の内部の API（OPC の部品・関連・run の XML）を確かめた版。
# 版を上げるときは、このファイルのテストに加えて、実機の Word で「修復なしで開ける・編集できる
# グラフとして認識される・データの編集で値が変わる」を確かめ直してから、この値を直す。
VERIFIED_PYTHON_DOCX = "1.2.0"

NS = {
    "a": "http://schemas.openxmlformats.org/drawingml/2006/main",
    "c": "http://schemas.openxmlformats.org/drawingml/2006/chart",
    "ct": "http://schemas.openxmlformats.org/package/2006/content-types",
    "r": "http://schemas.openxmlformats.org/officeDocument/2006/relationships",
    "rel": "http://schemas.openxmlformats.org/package/2006/relationships",
    "w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main",
    "wp": "http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing",
}
R_ID = f"{{{NS['r']}}}id"
# `c:chartSpace` の子の順（schema の `CT_ChartSpace`）。
CHART_SPACE_ORDER = (
    "date1904",
    "lang",
    "roundedCorners",
    "AlternateContent",
    "style",
    "clrMapOvr",
    "pivotSource",
    "protection",
    "chart",
    "spPr",
    "txPr",
    "externalData",
    "printSettings",
    "userShapes",
    "extLst",
)

SALES = [
    Column("月", "text"),
    Column("数量（個）", "number", "integer"),
    Column("比率", "number", "percent1"),
    Column("備考", "text"),
]
SALES_ROWS: list[list[Any]] = [
    ["4 月", 1200, 0.25, "合成の備考"],
    ["5 月", "30.5", 0.5, None],
    ["6 月", None, 0.125, None],
    ["7 月", 80, 0.125, None],
]
PIE_ROWS = [row for row in SALES_ROWS if row[1] is not None]


def _chart_block(
    chart_type: str = "column",
    series: tuple[int, ...] = (2, 3),
    *,
    columns: list[Column] | None = None,
    rows: list[list[Any]] | None = None,
    category: int = 1,
    caption: str | None = None,
) -> ChartBlock:
    return ChartBlock(
        columns=SALES if columns is None else columns,
        rows=SALES_ROWS if rows is None else rows,
        chart=ChartSpec(chart_type, category, series),
        caption=caption,
    )


def _build(*blocks: Block) -> BuiltDocument:
    return build_document(DocumentContent(title="合成の報告", blocks=list(blocks)))


def _issues(*blocks: Block) -> list[tuple[str, str]]:
    with pytest.raises(DocxSpecError) as raised:
        _build(*blocks)
    return [(item.argument, item.code) for item in raised.value.issues]


def _relationships(package: zipfile.ZipFile, part: str) -> dict[str, tuple[str, str]]:
    """部品の関連（ID → 種類と、相手の部品の名前）。"""
    folder, name = posixpath.split(part)
    root = etree.fromstring(package.read(f"{folder}/_rels/{name}.rels"))
    return {
        item.get("Id"): (
            item.get("Type"),
            posixpath.normpath(posixpath.join(folder, item.get("Target"))),
        )
        for item in root.findall("rel:Relationship", NS)
    }


def _inlines(package: zipfile.ZipFile) -> list[Any]:
    document = etree.fromstring(package.read("word/document.xml"))
    return list(document.iterfind(".//w:drawing/wp:inline", NS))


def _chart_names(package: zipfile.ZipFile) -> list[str]:
    """本文に出る順の、グラフの部品の名前。"""
    relationships = _relationships(package, "word/document.xml")
    return [
        relationships[inline.find("a:graphic/a:graphicData/c:chart", NS).get(R_ID)][1]
        for inline in _inlines(package)
    ]


def _charts(data: bytes) -> list[tuple[Any, Any]]:
    """本文に出る順の、グラフ（python-pptx で読んだもの）と、値のブックの sheet。"""
    found = []
    with zipfile.ZipFile(io.BytesIO(data)) as package:
        for name in _chart_names(package):
            root = parse_xml(package.read(name))
            workbook_id = root.find("c:externalData", NS).get(R_ID)
            workbook = package.read(_relationships(package, name)[workbook_id][1])
            found.append((Chart(root, None), load_workbook(io.BytesIO(workbook)).active))
    return found


def _open(data: bytes) -> Any:
    return Document(io.BytesIO(data))


def _decimal(value: Any) -> Decimal | None:
    return None if value is None else Decimal(str(value))


def _paragraphs(data: bytes) -> list[tuple[str, str, bool]]:
    """本文の段落（style・文・グラフがあるか。表の後の間隔などの空の段落は除く）。"""
    paragraphs = []
    for item in _open(data).iter_inner_content():
        if isinstance(item, Table):
            paragraphs.append(("Table", "", False))
            continue
        has_chart = bool(item._p.xpath(".//w:drawing"))  # noqa: SLF001 - 図を読む API が無い
        if item.text or has_chart:
            paragraphs.append((item.style.name, item.text, has_chart))
    return paragraphs


def test_python_docx_is_the_version_the_embedding_was_verified_with() -> None:
    assert version("python-docx") == VERIFIED_PYTHON_DOCX


# ---- ファイルの構造（部品・content type・関連・本文の図） ----------------------------------------


def test_each_chart_adds_a_chart_part_and_a_workbook_part_with_their_relationships() -> None:
    built = _build(*[_chart_block(kind, (2,), rows=PIE_ROWS) for kind in CHART_TYPES])

    with zipfile.ZipFile(io.BytesIO(built.data)) as package:
        names = package.namelist()
        assert len(names) == len(set(names))
        count = len(CHART_TYPES)
        charts = [f"word/charts/chart{number}.xml" for number in range(1, count + 1)]
        workbooks = [
            f"word/embeddings/Microsoft_Excel_Sheet{number}.xlsx" for number in range(1, count + 1)
        ]
        assert [name for name in names if name.startswith("word/charts/chart")] == charts
        assert sorted(name for name in names if name.startswith("word/embeddings/")) == workbooks
        # 値のブックは拡張子の Default、グラフは部品ごとの Override で content type を持つ。
        types = etree.fromstring(package.read("[Content_Types].xml"))
        defaults = {
            item.get("Extension"): item.get("ContentType")
            for item in types.findall("ct:Default", NS)
        }
        overrides = {
            item.get("PartName"): item.get("ContentType")
            for item in types.findall("ct:Override", NS)
        }
        assert defaults["xlsx"] == CONTENT_TYPE.SML_SHEET
        assert [overrides[f"/{name}"] for name in charts] == [CONTENT_TYPE.DML_CHART] * count
        assert not [name for name in overrides if name.endswith(".xlsx")]
        # 文書 → グラフ（本文に出る順）。
        document_relationships = _relationships(package, "word/document.xml")
        assert _chart_names(package) == charts
        assert [
            name
            for kind, name in document_relationships.values()
            if kind == RELATIONSHIP_TYPE.CHART
        ] == charts
        for chart_name, workbook_name in zip(charts, workbooks, strict=True):
            # グラフ → ブック。グラフの XML の `c:externalData` が、その関連を指す。
            [(relation_id, target)] = _relationships(package, chart_name).items()
            assert target == (RELATIONSHIP_TYPE.PACKAGE, workbook_name)
            root = etree.fromstring(package.read(chart_name))
            [external] = root.findall("c:externalData", NS)
            assert external.get(R_ID) == relation_id
            assert [(etree.QName(child).localname, child.get("val")) for child in external] == [
                ("autoUpdate", "0")
            ]
            # `c:chartSpace` の子は schema の順（`c:externalData` は体裁の後ろ）。
            children = [etree.QName(child).localname for child in root]
            assert children == sorted(children, key=CHART_SPACE_ORDER.index)
            assert children[-1] == "externalData"
            # ブックは開ける .xlsx。
            assert package.read(workbook_name).startswith(b"PK\x03\x04")


def test_charts_are_inline_drawings_with_unique_ids_and_the_fixed_size() -> None:
    built = _build(
        _chart_block("column", caption='図 1　月ごとの数量 <合成> & "比率"'),
        ParagraphBlock("間の段落。"),
        _chart_block("line"),
        _chart_block("bar", (2,)),
    )

    with zipfile.ZipFile(io.BytesIO(built.data)) as package:
        inlines = _inlines(package)
        document = etree.fromstring(package.read("word/document.xml"))
    assert len(inlines) == 3
    for inline in inlines:
        # 子の順は schema の順（`CT_Inline`）。
        assert [etree.QName(child).localname for child in inline] == [
            "extent",
            "effectExtent",
            "docPr",
            "cNvGraphicFramePr",
            "graphic",
        ]
        assert {name: inline.get(name) for name in ("distT", "distB", "distL", "distR")} == {
            "distT": "0",
            "distB": "0",
            "distL": "0",
            "distR": "0",
        }
        extent = inline.find("wp:extent", NS)
        assert (int(extent.get("cx")), int(extent.get("cy"))) == (
            Mm(CHART_WIDTH_MM),
            Mm(CHART_HEIGHT_MM),
        )
        assert inline.find("a:graphic/a:graphicData", NS).get("uri") == NS["c"]
        # 画像ではなく、グラフの部品への参照。
        assert [
            etree.QName(child).localname for child in inline.find("a:graphic/a:graphicData", NS)
        ] == ["chart"]
    properties = [inline.find("wp:docPr", NS) for inline in inlines]
    # 図の id は文書の中で一意（ほかの要素の id とも重ならない）。
    ids = [item.get("id") for item in properties]
    assert len(set(ids)) == 3 and all(value.isdigit() and int(value) > 0 for value in ids)
    assert sorted(document.xpath("//@id")) == sorted(ids)
    assert [item.get("name") for item in properties] == ["グラフ 1", "グラフ 2", "グラフ 3"]
    # 題のあるグラフは、題が代替テキストになる（記号を含む題も、そのまま文字として入る）。
    assert [item.get("descr") for item in properties] == [
        '図 1　月ごとの数量 <合成> & "比率"',
        None,
        None,
    ]


def test_chart_fits_the_body_width() -> None:
    [section] = _open(_build(_chart_block()).data).sections

    body = section.page_width - section.left_margin - section.right_margin
    assert 0 < Mm(CHART_WIDTH_MM) <= body
    assert (
        0 < Mm(CHART_HEIGHT_MM) < section.page_height - section.top_margin - section.bottom_margin
    )


def test_python_docx_reads_the_charts_and_keeps_them_when_saving_again() -> None:
    built = _build(_chart_block("column"), _chart_block("pie", (2,), rows=PIE_ROWS))

    document = _open(built.data)
    assert [shape.type for shape in document.inline_shapes] == [WD_INLINE_SHAPE.CHART] * 2
    assert [(shape.width, shape.height) for shape in document.inline_shapes] == [
        (Mm(CHART_WIDTH_MM), Mm(CHART_HEIGHT_MM))
    ] * 2
    saved = io.BytesIO()
    document.save(saved)

    def parts(data: bytes) -> dict[str, bytes]:
        with zipfile.ZipFile(io.BytesIO(data)) as package:
            return {
                name: package.read(name)
                for name in package.namelist()
                if name.startswith(("word/charts/", "word/embeddings/"))
            }

    # グラフ・値のブック・その関連は、開き直して保存しても同じ中身で残る。
    assert parts(saved.getvalue()) == parts(built.data)
    assert len(parts(built.data)) == 6
    with zipfile.ZipFile(saved) as package:
        assert _chart_names(package) == ["word/charts/chart1.xml", "word/charts/chart2.xml"]
        types = package.read("[Content_Types].xml").decode("utf-8")
    assert types.count(CONTENT_TYPE.DML_CHART) == 2 and 'Extension="xlsx"' in types


# ---- グラフの中身（値は表から。体裁は共通の部品） -----------------------------------------------


@pytest.mark.parametrize(
    ("chart_type", "series"),
    [("column", (2, 3)), ("bar", (3, 2)), ("line", (2, 3)), ("pie", (3,))],
    ids=list(CHART_TYPES),
)
def test_chart_values_match_the_table(chart_type: str, series: tuple[int, ...]) -> None:
    built = _build(_chart_block(chart_type, series))
    [(chart, sheet)] = _charts(built.data)

    # Word のグラフとして入る（値は開いて編集できる）。
    assert chart.chart_type == CHART_KINDS[chart_type]
    [plot] = chart.plots
    assert list(plot.categories) == [row[0] for row in SALES_ROWS]
    assert [item.name for item in plot.series] == [SALES[column - 1].header for column in series]
    # 系列の値は表の数値と一致する（空の値は空のまま）。
    expected = [
        [None if row[column - 1] is None else float(row[column - 1]) for row in SALES_ROWS]
        for column in series
    ]
    assert [list(item.values) for item in plot.series] == expected
    # 値のブック（利用者が「データの編集」で開く表）にも、同じ値が入る。
    rows = [list(row) for row in sheet.iter_rows(values_only=True)]
    assert rows[0][1:] == [SALES[column - 1].header for column in series]
    assert [row[0] for row in rows[1:]] == [row[0] for row in SALES_ROWS]
    assert [
        [row[position] for row in rows[1:]] for position in range(1, len(series) + 1)
    ] == expected
    assert built.charts == (
        {"type": chart_type, "series": len(series), "categories": len(SALES_ROWS)},
    )
    assert built.blocks == {**dict.fromkeys(BLOCK_TYPES, 0), "chart": 1}
    # グラフの値の表は、文書の表にならない。
    assert built.tables == () and _open(built.data).tables == []


def test_chart_is_the_shared_drawing_with_the_document_text_sizes() -> None:
    built = _build(_chart_block("bar", (2,)))
    with zipfile.ZipFile(io.BytesIO(built.data)) as package:
        actual = etree.fromstring(package.read("word/charts/chart1.xml"))

    data = ChartData(
        "bar",
        tuple(str(row[0]) for row in SALES_ROWS),
        (ChartSeries("数量（個）", tuple(_decimal(row[1]) for row in SALES_ROWS), "integer"),),
    )
    xml, _ = chart_parts(data, font_name=FONT_NAME, text_pt=CHART_TEXT_PT, label_pt=CHART_LABEL_PT)
    # 埋め込みのブックへの参照を除けば、PowerPoint と共通の部品が返す XML と同じ。
    for external in actual.findall("c:externalData", NS):
        actual.remove(external)
    assert etree.tostring(actual, method="c14n") == etree.tostring(
        etree.fromstring(xml), method="c14n"
    )
    # 字体は英数字と東アジアの両方に書く。字の大きさは、スライド用ではなく文書の本文に合う値。
    [defaults] = actual.xpath("c:txPr/a:p/a:pPr/a:defRPr", namespaces=NS)
    assert defaults.get("sz") == str(int(CHART_TEXT_PT * 100))
    assert defaults.xpath("a:latin/@typeface | a:ea/@typeface", namespaces=NS) == [FONT_NAME] * 2
    assert actual.xpath("//c:dLbls/c:txPr/a:p/a:pPr/a:defRPr/@sz", namespaces=NS) == [
        str(int(CHART_LABEL_PT * 100))
    ]
    assert CHART_LABEL_PT < CHART_TEXT_PT < 10.5
    # 円の割合のラベルも、文書用の大きさ。
    pie = _build(_chart_block("pie", (2,), rows=PIE_ROWS))
    with zipfile.ZipFile(io.BytesIO(pie.data)) as package:
        pie_root = etree.fromstring(package.read("word/charts/chart1.xml"))
    assert pie_root.xpath("//c:dLbls/c:txPr/a:p/a:pPr/a:defRPr/@sz", namespaces=NS) == [
        str(int(CHART_LABEL_PT * 100))
    ]


def test_names_are_written_as_text_not_formulas_or_links() -> None:
    """表の値を、数式・リンクとして評価される形でファイルに入れない。"""
    labels = ["=1+1", "http://example.invalid/x", "@SUM(1)", '<b>&"']
    columns = [Column("項目", "text"), Column("=2+2", "number")]
    rows = [[label, index] for index, label in enumerate(labels, 1)]
    [(chart, sheet)] = _charts(_build(_chart_block("bar", (2,), columns=columns, rows=rows)).data)

    names = [sheet.cell(row=1, column=2), *(sheet.cell(row=row, column=1) for row in range(2, 6))]
    assert [cell.value for cell in names] == ["=2+2", *labels]
    assert {cell.data_type for cell in names} == {"s"}
    assert all(cell.hyperlink is None for cell in names)
    assert list(chart.plots[0].categories) == labels


# ---- 文書の中の位置（題・改ページ・表との並び） -------------------------------------------------


def test_caption_is_above_the_chart_and_stays_with_it() -> None:
    built = _build(
        HeadingBlock(1, "1. 概要"),
        TableBlock(columns=SALES, rows=SALES_ROWS, caption="表 1　月ごとの数量"),
        _chart_block("column", caption="図 1　月ごとの\n数量"),
        ParagraphBlock("グラフの後の段落。"),
        _chart_block("line"),
    )

    assert _paragraphs(built.data)[1:] == [
        ("Heading 1", "1. 概要", False),
        ("Caption", "表 1　月ごとの数量", False),
        ("Table", "", False),
        # 題は表と同じく上に置く（改行は空白にする）。グラフは、それだけの段落に行内で置く。
        ("Caption", "図 1　月ごとの 数量", False),
        ("Normal", "", True),
        ("Normal", "グラフの後の段落。", False),
        ("Normal", "", True),
    ]
    document = _open(built.data)
    # 題は次の段落（グラフ）と同じページに置く。
    assert document.styles["Caption"].paragraph_format.keep_with_next is True
    charts = [item for item in document.paragraphs if item._p.xpath(".//w:drawing")]  # noqa: SLF001
    assert [item.alignment for item in charts] == [WD_ALIGN_PARAGRAPH.CENTER] * 2
    # 行の高さは中の図に合わせて広がる（固定の高さで図を切らない）。
    assert all(item.paragraph_format.line_spacing_rule is None for item in charts)
    assert document.styles["Normal"].paragraph_format.line_spacing == 1.0
    assert built.tables == ({"rows": 4, "columns": 4},)
    assert [chart["type"] for chart in built.charts] == ["column", "line"]


def test_page_break_before_a_chart_starts_a_new_page() -> None:
    built = _build(
        ParagraphBlock("1 ページ目。"),
        PageBreakBlock(),
        # 題の無いグラフの前の改ページは、グラフの段落に付く。
        _chart_block("column"),
        PageBreakBlock(),
        _chart_block("line", caption="題のあるグラフ"),
    )

    document = _open(built.data)
    marked = [
        (item.style.name, item.text, bool(item._p.xpath(".//w:drawing")))  # noqa: SLF001
        for item in document.paragraphs
        if item.paragraph_format.page_break_before
    ]
    assert marked == [("Normal", "", True), ("Caption", "題のあるグラフ", False)]


# ---- 作れない指定は、切り詰めずに誤りにする -----------------------------------------------------


def _numbers(count: int) -> list[Column]:
    return [Column("項目", "text"), *(Column(f"系列 {index}", "number") for index in range(count))]


def _rows(count: int, width: int = 1) -> list[list[Any]]:
    return [[f"項目 {index}", *([index + 1] * width)] for index in range(count)]


@pytest.mark.parametrize(
    ("block", "expected"),
    [
        (_chart_block("area"), [("blocks.0.chart.type", "enum")]),
        (_chart_block(category=5), [("blocks.0.chart.category_column", "range")]),
        (_chart_block(series=(2, 9)), [("blocks.0.chart.series_columns.1", "range")]),
        (_chart_block(series=(1,)), [("blocks.0.chart.series_columns.0", "conflict")]),
        (_chart_block(series=(2, 2)), [("blocks.0.chart.series_columns.1", "duplicate")]),
        # 系列にできるのは数値の列だけ。
        (_chart_block(series=(4,)), [("blocks.0.chart.series_columns.0", "not_number")]),
        (_chart_block(series=()), [("blocks.0.chart.series_columns", "count")]),
        (
            _chart_block(
                series=tuple(range(2, MAX_CHART_SERIES + 3)),
                columns=_numbers(MAX_CHART_SERIES + 1),
                rows=_rows(3, MAX_CHART_SERIES + 1),
            ),
            [("blocks.0.chart.series_columns", "count")],
        ),
        (
            _chart_block("pie", (2, 3), rows=PIE_ROWS),
            [("blocks.0.chart.series_columns", "count")],
        ),
        (_chart_block(rows=[]), [("blocks.0.table.rows", "required")]),
        (
            _chart_block(series=(2,), columns=_numbers(1), rows=_rows(MAX_CHART_CATEGORIES + 1)),
            [("blocks.0.table.rows", "too_many")],
        ),
        (
            _chart_block("pie", (2,), columns=_numbers(1), rows=_rows(MAX_PIE_CATEGORIES + 1)),
            [("blocks.0.table.rows", "too_many")],
        ),
        # 値を取る表の列の定義と行の形は、表のブロックと同じ規則。
        (
            _chart_block(
                series=(2,),
                columns=_numbers(MAX_TABLE_COLUMNS),
                rows=_rows(2, MAX_TABLE_COLUMNS),
            ),
            [("blocks.0.table.columns", "count")],
        ),
        (
            _chart_block(rows=[*SALES_ROWS, ["8 月", 1]]),
            [("blocks.0.table.rows.4", "length")],
        ),
        (
            _chart_block(
                series=(2,),
                columns=[Column("月", "text"), Column(" ", "number")],
                rows=[["4 月", 1]],
            ),
            [("blocks.0.table.columns.1.header", "required")],
        ),
        (
            _chart_block(
                series=(2,),
                columns=[Column("月", "text"), Column("数量", "number", "date")],
                rows=[["4 月", 1]],
            ),
            [("blocks.0.table.columns.1.format", "format")],
        ),
        # 数値として読めない値は、0 にしたり飛ばしたりしない（使わない列の値も読む）。
        (
            _chart_block(series=(2,), rows=[["4 月", "多い", 0.5, None]]),
            [("blocks.0.table.columns.1.type", "not_number")],
        ),
        (
            _chart_block(series=(2,), rows=[["4 月", 1, "x", None], ["5 月", "y", 1, None]]),
            [
                ("blocks.0.table.columns.1.type", "not_number"),
                ("blocks.0.table.columns.2.type", "not_number"),
            ],
        ),
        (
            _chart_block(series=(2,), rows=[["4 月", 1, 0.5, None], [" ", 2, 0.5, None]]),
            [("blocks.0.table.columns.0", "required")],
        ),
        (
            _chart_block(series=(2,), rows=[["x" * (MAX_CHART_LABEL_CHARS + 1), 1, 0.5, None]]),
            [("blocks.0.table.columns.0", "too_long")],
        ),
        (
            _chart_block(
                series=(2,),
                columns=[Column("月", "text"), Column("x" * (MAX_CHART_LABEL_CHARS + 1), "number")],
                rows=[["4 月", 1]],
            ),
            [("blocks.0.table.columns.1.header", "too_long")],
        ),
        (
            _chart_block(series=(2,), rows=[["4 月", None, 0.5, None]]),
            [("blocks.0.table.columns.1", "empty")],
        ),
        # 円は、空の値・負の値・合計が 0 の表を描けない。
        (_chart_block("pie", (2,)), [("blocks.0.table.columns.1", "pie_values")]),
        (
            _chart_block("pie", (2,), rows=[["4 月", 3, 0.5, None], ["5 月", -1, 0.5, None]]),
            [("blocks.0.table.columns.1", "pie_values")],
        ),
        (
            _chart_block("pie", (2,), rows=[["4 月", 0, 0.5, None]]),
            [("blocks.0.table.columns.1", "pie_values")],
        ),
    ],
    ids=[
        "unknown-type",
        "category-out-of-range",
        "series-out-of-range",
        "category-as-series",
        "duplicate-series",
        "text-series",
        "no-series",
        "too-many-series",
        "pie-with-two-series",
        "no-rows",
        "too-many-categories",
        "too-many-pie-categories",
        "too-many-columns",
        "row-length",
        "blank-header",
        "format-for-type",
        "unreadable-value",
        "unreadable-values-in-two-columns",
        "blank-category",
        "long-category",
        "long-series-name",
        "empty-series",
        "pie-with-empty-value",
        "pie-with-negative-value",
        "pie-with-zero-total",
    ],
)
def test_chart_outside_the_limits_is_an_error(
    block: ChartBlock, expected: list[tuple[str, str]]
) -> None:
    assert _issues(block) == expected


def test_limits_are_the_shared_chart_limits_and_full_charts_are_built() -> None:
    assert (MAX_CHART_SERIES, MAX_CHART_CATEGORIES, MAX_PIE_CATEGORIES) == (5, 20, 8)
    full = _chart_block(
        "column",
        tuple(range(2, MAX_CHART_SERIES + 2)),
        columns=_numbers(MAX_CHART_SERIES),
        rows=_rows(MAX_CHART_CATEGORIES, MAX_CHART_SERIES),
    )
    pie = _chart_block("pie", (2,), columns=_numbers(1), rows=_rows(MAX_PIE_CATEGORIES))

    built = _build(full, pie)

    assert built.charts == (
        {"type": "column", "series": MAX_CHART_SERIES, "categories": MAX_CHART_CATEGORIES},
        {"type": "pie", "series": 1, "categories": MAX_PIE_CATEGORIES},
    )


def test_charts_per_document_are_limited_and_errors_carry_no_values() -> None:
    assert MAX_CHARTS == 20
    chart = _chart_block("bar", (2,))
    assert len(_build(*[chart] * MAX_CHARTS).charts) == MAX_CHARTS
    assert _issues(*[chart] * (MAX_CHARTS + 1)) == [("blocks", "too_many_charts")]
    # 誤りの文に、表の値を写さない。
    with pytest.raises(DocxSpecError) as raised:
        _build(_chart_block(series=(2,), rows=[["secret-label", "secret-value", 0.5, None]]))
    assert "secret" not in str(raised.value)
    assert "1 行目" in raised.value.issues[0].message


def test_chart_text_counts_toward_the_document_characters(monkeypatch: MonkeyPatch) -> None:
    chart = _chart_block("bar", (2,), columns=_numbers(1), rows=_rows(3), caption="題")
    # 題・項目の名前・系列の名前（文書に入る文字）を数える。値を取る表の、使わない列は数えない。
    used = (
        len("合成の報告")
        + len("題")
        + sum(len(f"項目 {index}") for index in range(3))
        + len("系列 0")
    )
    monkeypatch.setattr(docx_document, "MAX_DOCUMENT_CHARS", used)
    assert len(_build(chart).charts) == 1
    monkeypatch.setattr(docx_document, "MAX_DOCUMENT_CHARS", used - 1)
    assert _issues(chart) == [("blocks", "too_long")]
