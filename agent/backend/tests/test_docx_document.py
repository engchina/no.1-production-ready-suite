"""docx の生成の本体（`docx_document.build_document`。#5837）。

作ったバイト列を python-docx で開き直して確かめる（Run・DB・モデルは使わない。値は合成）。

- 用紙（A4 縦・余白）・字体（東アジアの字体の指定を含む）・フッターの中央のページ番号。
- 題と副題・見出しのレベル・段落・リストの階層と番号の振り直し・改ページ。
- 表の値（列の型と書式を当てた文字）・数値と日付の列の右寄せ・見出しの行の繰り返しと網かけ・幅。
- 作れない内容（上限の超過・型に合わない値・空の文）は、切り捨てずに誤りにすること。
"""

from __future__ import annotations

import io
from typing import Any

import pytest
from docx import Document
from docx.enum.section import WD_ORIENT
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.oxml.ns import qn
from docx.shared import Emu, Twips
from docx.table import Table
from docx.text.paragraph import Paragraph
from pytest import MonkeyPatch

from app.features.agent import docx_document
from app.features.agent.docx_document import (
    BLOCK_TYPES,
    FONT_NAME,
    MAX_BLOCKS,
    MAX_CELL_CHARS,
    MAX_LIST_ITEMS,
    MAX_TABLE_COLUMNS,
    MAX_TABLE_ROWS,
    MAX_TABLES,
    Block,
    BuiltDocument,
    Column,
    DocumentContent,
    DocxSpecError,
    HeadingBlock,
    ListBlock,
    ListItem,
    PageBreakBlock,
    ParagraphBlock,
    TableBlock,
    build_document,
)
from app.features.agent.table_columns import DATE_FORMATS, NUMBER_FORMATS, display_width


def _build(*blocks: Block, title: str = "合成の報告", subtitle: str | None = None) -> BuiltDocument:
    built = build_document(DocumentContent(title=title, subtitle=subtitle, blocks=list(blocks)))
    assert built.data.startswith(b"PK\x03\x04")
    return built


def _open(*blocks: Block, title: str = "合成の報告", subtitle: str | None = None) -> Any:
    return Document(io.BytesIO(_build(*blocks, title=title, subtitle=subtitle).data))


def _issues(*blocks: Block, title: str = "合成の報告") -> list[tuple[str, str]]:
    with pytest.raises(DocxSpecError) as raised:
        build_document(DocumentContent(title=title, blocks=list(blocks)))
    return [(item.argument, item.code) for item in raised.value.issues]


def _body(document: Any) -> list[Any]:
    """本文の段落と表（文書に出る順。表の後の間隔などの空の段落は除く）。"""
    return [
        item
        for item in document.iter_inner_content()
        if isinstance(item, Table) or item.text or _has_page_break(item)
    ]


def _has_page_break(paragraph: Paragraph) -> bool:
    return bool(paragraph.paragraph_format.page_break_before)


def _numbering(paragraph: Paragraph) -> tuple[int, int] | None:
    """段落のリストの階層と番号の実体（`w:ilvl`・`w:numId`）。リストでなければ None。"""
    # 番号の指定を読む API が無いので、XML から読む。
    element: Any = paragraph._p  # noqa: SLF001
    levels = element.xpath("w:pPr/w:numPr/w:ilvl/@w:val")
    numbers = element.xpath("w:pPr/w:numPr/w:numId/@w:val")
    return (int(levels[0]), int(numbers[0])) if levels and numbers else None


def _cell_texts(table: Table) -> list[list[str]]:
    return [[cell.text for cell in row.cells] for row in table.rows]


ITEMS = TableBlock(
    caption="表 1　品目ごとの数量（合成）",
    columns=[
        Column("品目", "text"),
        Column("数量（個）", "number", "integer"),
        Column("単価（円）", "number", "decimal2"),
        Column("比率", "number", "percent1"),
        Column("登録日", "date", "date_slash"),
        Column("更新日時", "date"),
        Column("番号", "text"),
    ],
    rows=[
        ["品目 A", 1200, 1234.5, 0.256, "2026-01-05", "2026-01-05T09:30:00", "0001"],
        ["品目 B", "30", "0.125", 0.5, "2026-02-10T00:00:00", "2026-02-10", "=1+1"],
        ["品目 C", None, None, None, None, None, None],
    ],
)


# ---- 用紙・字体・ページ番号（固定の体裁） -----------------------------------------------------


def test_page_is_a4_portrait_with_fixed_margins_and_a_centered_page_number() -> None:
    document = _open(ParagraphBlock("合成の段落。"))

    [section] = document.sections
    assert section.orientation == WD_ORIENT.PORTRAIT
    # 長さは twip に丸めて保存される（mm に直して比べる）。
    sizes = (section.page_width, section.page_height)
    margins = (section.top_margin, section.bottom_margin, section.left_margin, section.right_margin)
    assert [round(size.mm, 1) for size in sizes] == [210.0, 297.0]
    assert [round(margin.mm, 1) for margin in margins] == [25.0, 25.0, 20.0, 20.0]
    # フッターの中央に、ページ番号の field（PAGE）がある。
    [footer] = section.footer.paragraphs
    assert footer.alignment == WD_ALIGN_PARAGRAPH.CENTER
    kinds = [
        element.get(qn("w:fldCharType"))
        for element in footer._p.iter(qn("w:fldChar"))  # noqa: SLF001
    ]
    assert kinds == ["begin", "separate", "end"]
    [instruction] = list(footer._p.iter(qn("w:instrText")))  # noqa: SLF001
    assert instruction.text.strip() == "PAGE"
    # ヘッダーには何も置かない。
    assert [paragraph.text for paragraph in section.header.paragraphs] == [""]


def test_one_font_is_set_for_latin_and_east_asian_text() -> None:
    document = _open(HeadingBlock(1, "見出し"), ParagraphBlock("合成の段落。"))
    styles = document.styles

    [defaults] = styles.element.xpath("w:docDefaults/w:rPrDefault/w:rPr/w:rFonts")
    used = ["Normal", "Title", "Subtitle", "Heading 1", "Heading 2", "Heading 3", "Caption"]
    fonts = [defaults, *(styles[name].element.rPr.find(qn("w:rFonts")) for name in used)]
    for element in fonts:
        # 東アジアの文字の字体（w:eastAsia）も同じ名前にし、テーマの字体の指定を残さない。
        assert {key.rpartition("}")[2]: value for key, value in element.attrib.items()} == {
            "ascii": FONT_NAME,
            "hAnsi": FONT_NAME,
            "eastAsia": FONT_NAME,
            "cs": FONT_NAME,
        }
    [language] = styles.element.xpath("w:docDefaults/w:rPrDefault/w:rPr/w:lang")
    assert language.get(qn("w:eastAsia")) == "ja-JP"
    # 字体は埋め込まない（名前の指定だけ）。
    assert document.settings.element.find(qn("w:embedTrueTypeFonts")) is None


def test_document_properties_carry_the_title_and_no_author() -> None:
    properties = _open(ParagraphBlock("合成の段落。"), title="合成の報告").core_properties

    assert (properties.title, properties.author, properties.comments) == ("合成の報告", "", "")


# ---- 題・見出し・段落・リスト・改ページ -------------------------------------------------------


def test_title_headings_and_paragraphs_keep_their_order_and_levels() -> None:
    document = _open(
        HeadingBlock(1, "1. 概要"),
        ParagraphBlock("1 つ目の段落。"),
        HeadingBlock(2, "1.1 詳細"),
        # 段落の文の改行は段落の区切りになる（空の行は除く）。
        ParagraphBlock("2 つ目の段落。\n\n3 つ目の段落。"),
        HeadingBlock(3, "1.1.1 補足"),
        ParagraphBlock("**装飾の記号**はそのままの文字。"),
        title="合成の報告",
        subtitle="2026-11-26　部門 1",
    )

    assert [(item.style.name, item.text) for item in _body(document)] == [
        ("Title", "合成の報告"),
        ("Subtitle", "2026-11-26　部門 1"),
        ("Heading 1", "1. 概要"),
        ("Normal", "1 つ目の段落。"),
        ("Heading 2", "1.1 詳細"),
        ("Normal", "2 つ目の段落。"),
        ("Normal", "3 つ目の段落。"),
        ("Heading 3", "1.1.1 補足"),
        ("Normal", "**装飾の記号**はそのままの文字。"),
    ]
    # 段落の中に、太字・斜体などの装飾の run は無い。
    for paragraph in document.paragraphs:
        assert all(run.bold is None and run.italic is None for run in paragraph.runs)
    # 見出しは次の段落と離さない。
    for level in (1, 2, 3):
        assert document.styles[f"Heading {level}"].paragraph_format.keep_with_next is True


def test_title_and_heading_text_are_single_lines() -> None:
    document = _open(
        HeadingBlock(1, "見出しの\n改行"),
        ParagraphBlock("合成の段落。"),
        title="合成の\r\n報告",
    )

    assert [item.text for item in _body(document)[:2]] == ["合成の 報告", "見出しの 改行"]


def test_lists_keep_two_levels_and_numbered_lists_restart() -> None:
    document = _open(
        ListBlock(False, [ListItem("項目 A", ["項目 A-1", "項目 A-2"]), ListItem("項目 B")]),
        ListBlock(True, [ListItem("手順 1", ["補足 1"]), ListItem("手順 2")]),
        ParagraphBlock("間の段落。"),
        ListBlock(True, [ListItem("別の手順 1"), ListItem("別の手順 2")]),
        ListBlock(False, [ListItem("項目 C")]),
    )

    items = [(item.text, _numbering(item)) for item in _body(document)[1:]]
    numbers = {text: number for text, number in items if number is not None}
    assert [(text, number[0] if number else None) for text, number in items] == [
        ("項目 A", 0),
        ("項目 A-1", 1),
        ("項目 A-2", 1),
        ("項目 B", 0),
        ("手順 1", 0),
        ("補足 1", 1),
        ("手順 2", 0),
        ("間の段落。", None),
        ("別の手順 1", 0),
        ("別の手順 2", 0),
        ("項目 C", 0),
    ]
    bullets, first, second = numbers["項目 A"][1], numbers["手順 1"][1], numbers["別の手順 1"][1]
    # 1 つのリストの項目は同じ番号の実体。番号付きのリストは、リストごとに別の実体で 1 から始まる。
    assert numbers["項目 B"][1] == numbers["項目 C"][1] == bullets
    assert numbers["手順 2"][1] == numbers["補足 1"][1] == first
    assert len({bullets, first, second}) == 3
    numbering = document.part.numbering_part.element

    def definition(num_id: int) -> tuple[list[str], list[str]]:
        [num] = numbering.xpath(f"w:num[@w:numId='{num_id}']")
        abstract_id = num.find(qn("w:abstractNumId")).get(qn("w:val"))
        [abstract] = numbering.xpath(f"w:abstractNum[@w:abstractNumId='{abstract_id}']")
        formats = [item.get(qn("w:val")) for item in abstract.iter(qn("w:numFmt"))]
        starts = [item.get(qn("w:val")) for item in num.iter(qn("w:startOverride"))]
        return formats, starts

    assert definition(bullets) == (["bullet", "bullet"], [])
    assert definition(first) == (["decimal", "decimal"], ["1", "1"])
    assert definition(second) == (["decimal", "decimal"], ["1", "1"])
    # 行頭の記号・番号は文に入れない（番号の定義が付ける）。
    assert all(not text.startswith(("•", "1.")) for text, _ in items)


def test_page_break_starts_the_next_block_on_a_new_page() -> None:
    document = _open(
        ParagraphBlock("1 ページ目。"),
        PageBreakBlock(),
        HeadingBlock(1, "2 ページ目の見出し"),
        PageBreakBlock(),
        # 題の無い表の前の改ページは、表の前の空の段落に付く。
        TableBlock(columns=[Column("名前", "text")], rows=[["a"]]),
        PageBreakBlock(),
        TableBlock(caption="題のある表", columns=[Column("名前", "text")], rows=[["b"]]),
    )

    body = _body(document)[1:]
    assert [
        (type(item).__name__, "" if isinstance(item, Table) else item.text) for item in body
    ] == [
        ("Paragraph", "1 ページ目。"),
        ("Paragraph", "2 ページ目の見出し"),
        ("Paragraph", ""),
        ("Table", ""),
        ("Paragraph", "題のある表"),
        ("Table", ""),
    ]
    breaks = [_has_page_break(item) for item in body if isinstance(item, Paragraph)]
    assert breaks == [False, True, True, True]


# ---- 表 ----------------------------------------------------------------------------------------


def test_table_cells_are_formatted_text_and_numbers_and_dates_are_right_aligned() -> None:
    built = _build(ITEMS)
    document = Document(io.BytesIO(built.data))

    [table] = document.tables
    assert table.style is not None and table.style.name == "Table Grid"
    assert _cell_texts(table) == [
        ["品目", "数量（個）", "単価（円）", "比率", "登録日", "更新日時", "番号"],
        # 数値は書式を当てた文字（四捨五入は Excel の表示と同じ）、日付は書式の文字。
        ["品目 A", "1,200", "1,234.50", "25.6%", "2026/01/05", "2026-01-05 09:30", "0001"],
        ["品目 B", "30", "0.13", "50.0%", "2026/02/10", "2026-02-10 00:00", "=1+1"],
        ["品目 C", "", "", "", "", "", ""],
    ]
    left, center, right = (
        None,
        WD_ALIGN_PARAGRAPH.CENTER,
        WD_ALIGN_PARAGRAPH.RIGHT,
    )
    alignments = [[cell.paragraphs[0].alignment for cell in row.cells] for row in table.rows]
    assert alignments[0] == [center] * 7
    # 数値・日付の列は右寄せ、文字の列は左（指定なし）。
    assert alignments[1] == alignments[2] == [left, right, right, right, right, right, left]
    # 表の題は、表の前の段落。
    body = _body(document)
    assert (body[1].style.name, body[1].text) == ("Caption", "表 1　品目ごとの数量（合成）")
    assert body[2] is not None and isinstance(body[2], Table)
    assert built.tables == ({"rows": 3, "columns": 7},)
    assert built.blocks == {**dict.fromkeys(BLOCK_TYPES, 0), "table": 1}


def test_table_has_borders_a_repeated_shaded_header_and_fixed_widths() -> None:
    document = _open(ITEMS)

    [table] = document.tables
    element = table._tbl  # noqa: SLF001 - 行・cell の指定を読む API が無い
    # 罫線は表の style（格子）。
    borders = document.styles["Table Grid"].element.xpath("w:tblPr/w:tblBorders/*")
    assert {item.get(qn("w:val")) for item in borders} == {"single"} and len(borders) == 6
    header, *rows = element.tr_lst
    # 見出しの行は、ページごとに繰り返す。データの行には付けない。
    assert header.trPr.find(qn("w:tblHeader")) is not None
    assert all(row.trPr.find(qn("w:tblHeader")) is None for row in rows)
    # 見出しの行は薄い網かけ（模様なしの塗り）と太字。
    for cell in header.tc_lst:
        [shading] = cell.tcPr.findall(qn("w:shd"))
        assert (shading.get(qn("w:val")), shading.get(qn("w:fill"))) == ("clear", "E7E6E6")
    assert all(run.bold for cell in table.rows[0].cells for run in cell.paragraphs[0].runs)
    assert all(not run.bold for cell in table.rows[1].cells for run in cell.paragraphs[0].runs)
    # 幅は本文の幅（A4 から左右の余白を引いた幅）に固定し、表・列・cell で同じ値を持つ。
    [section] = document.sections
    content_width = int(Emu(section.page_width - section.left_margin - section.right_margin).twips)
    assert round(Twips(content_width).mm) == 170
    [table_width] = element.tblPr.findall(qn("w:tblW"))
    assert (table_width.get(qn("w:type")), table_width.get(qn("w:w"))) == (
        "dxa",
        str(content_width),
    )
    assert table.autofit is False
    grid = [int(column.w.twips) for column in element.tblGrid.gridCol_lst]
    assert sum(grid) == content_width and all(width > 0 for width in grid)
    for row in element.tr_lst:
        assert [int(cell.width.twips) for cell in row.tc_lst] == grid


def test_number_and_date_columns_keep_their_width_when_text_is_long() -> None:
    """入りきらない表は、数値・日付の列の幅を保ち、文字の列（cell の中で折り返せる）を縮める。"""
    widths = docx_document._column_widths  # noqa: SLF001
    headers = ["名前", "日付", "数量"]
    rows = [["x" * 100, "2026/01/05", "1,234,567"]]
    padding, char = docx_document._CELL_PADDING, docx_document._CHAR_WIDTH  # noqa: SLF001
    date_width, number_width = padding + char * 10, padding + char * 9

    narrow = widths(headers, rows, [False, True, True], 4000)
    wide = widths(headers, rows, [False, True, True], 40000)
    no_text = widths(headers[1:], [rows[0][1:]], [True, True], 1000)

    assert narrow == [4000 - date_width - number_width, date_width, number_width]
    # 文字の列は、広い列から縮める（狭い列の幅は保つ）。
    short_width = padding + char * display_width("区分 1 の")
    assert widths(["名前", "区分"], [["x" * 100, "区分 1 の"]], [False, False], 3000) == [
        3000 - short_width,
        short_width,
    ]
    # 幅が足りるとき・縮められる列が無いときは、内容の幅の比で分ける。
    assert sum(wide) == 40000 and wide[0] > wide[1] > wide[2] > number_width
    assert sum(no_text) == 1000 and no_text[0] > no_text[1]


def test_tables_are_separated_and_the_document_does_not_end_with_a_table() -> None:
    table = TableBlock(columns=[Column("名前", "text")], rows=[["a"]])
    document = _open(table, table)

    kinds = [type(item).__name__ for item in document.iter_inner_content()]
    assert kinds == ["Paragraph", "Table", "Paragraph", "Table", "Paragraph"]


def test_cell_values_follow_the_column_type_and_format() -> None:
    document = _open(
        TableBlock(
            columns=[
                Column("既定の数値", "number"),
                Column("整数", "number", "integer"),
                Column("割合", "number", "percent"),
                Column("桁の多い数値", "number", "decimal1"),
                Column("既定の日付", "date"),
                Column("日時", "date", "datetime"),
                Column("文字", "text"),
            ],
            rows=[
                [1200, 0.5, 0.125, "12345678901234567890.25", "2026-01-05", "2026-01-05", True],
                [1e-7, 1.5, "-0.004", -1234.56, "2026-12-31T00:00:00+09:00", "2026-03-04T05:06", 7],
                ["1.50", -0.4, 1, 0, None, None, "1 行目\n2 行目\x00"],
            ],
        )
    )

    [table] = document.tables
    assert _cell_texts(table)[1:] == [
        # 書式なしの数値は値のまま。時刻の無い列の既定の日付は日付だけ。
        [
            "1200",
            "1",
            "13%",
            "12,345,678,901,234,567,890.3",
            "2026-01-05",
            "2026-01-05 00:00",
            "true",
        ],
        ["0.0000001", "2", "0%", "-1,234.6", "2026-12-31", "2026-03-04 05:06", "7"],
        # cell の中の改行は残し、XML に入れられない文字は除く。
        ["1.50", "0", "100%", "0.0", "", "", "1 行目\n2 行目"],
    ]


def test_format_names_are_the_same_as_the_spreadsheet_columns() -> None:
    assert set(docx_document._NUMBER_DISPLAY) == set(NUMBER_FORMATS)  # noqa: SLF001
    assert set(docx_document._DATE_DISPLAY) == set(DATE_FORMATS)  # noqa: SLF001


# ---- 作れない内容（切り捨てずに誤りにする） ---------------------------------------------------


def _table(*columns: Column, rows: list[list[Any]] | None = None) -> TableBlock:
    return TableBlock(columns=list(columns), rows=rows or [])


@pytest.mark.parametrize(
    ("blocks", "expected"),
    [
        ([], [("blocks", "count")]),
        ([ParagraphBlock("x")] * (MAX_BLOCKS + 1), [("blocks", "count")]),
        ([HeadingBlock(4, "見出し")], [("blocks.0.level", "range")]),
        ([HeadingBlock(1, " \n ")], [("blocks.0.text", "required")]),
        ([ParagraphBlock("")], [("blocks.0.text", "required")]),
        ([ListBlock(False, [])], [("blocks.0.items", "required")]),
        (
            [ListBlock(True, [ListItem("a", ["b", " "])])],
            [("blocks.0.items.0.children.1", "required")],
        ),
        (
            [ListBlock(False, [ListItem("a", ["b"] * MAX_LIST_ITEMS)])],
            [("blocks.0.items", "too_many")],
        ),
        ([_table()], [("blocks.0.table.columns", "count")]),
        (
            [_table(*[Column(f"列 {index}", "text") for index in range(MAX_TABLE_COLUMNS + 1)])],
            [("blocks.0.table.columns", "count")],
        ),
        (
            [_table(Column("名前", "text"), rows=[["x"]] * (MAX_TABLE_ROWS + 1))],
            [("blocks.0.table.rows", "too_many")],
        ),
        (
            [ParagraphBlock("x"), _table(Column("名前", "text"), rows=[["x"], ["y", "z"]])],
            [("blocks.1.table.rows.1", "length")],
        ),
        ([_table(Column(" ", "text"))], [("blocks.0.table.columns.0.header", "required")]),
        (
            [_table(Column("名前", "text", "integer"), Column("日付", "date", "percent"))],
            [
                ("blocks.0.table.columns.0.format", "format"),
                ("blocks.0.table.columns.1.format", "format"),
            ],
        ),
        (
            [_table(Column("名前", "text"))] * (MAX_TABLES + 1),
            [("blocks", "too_many_tables")],
        ),
        (
            [
                _table(
                    Column("数量", "number"),
                    Column("日付", "date"),
                    rows=[[1, "2026-01-05"], ["x", 5]],
                )
            ],
            [
                ("blocks.0.table.columns.0.type", "not_number"),
                ("blocks.0.table.columns.1.type", "not_date"),
            ],
        ),
        (
            [_table(Column("数量", "number"), rows=[[True]])],
            [("blocks.0.table.columns.0.type", "not_number")],
        ),
        (
            [_table(Column("数量", "number"), rows=[["1e400"]])],
            [("blocks.0.table.columns.0.type", "precision")],
        ),
        (
            [_table(Column("数量", "number"), rows=[["9" * 41]])],
            [("blocks.0.table.columns.0.type", "precision")],
        ),
        (
            [_table(Column("名前", "text"), rows=[["a"], ["x" * (MAX_CELL_CHARS + 1)]])],
            [("blocks.0.table.columns.0", "too_long")],
        ),
    ],
    ids=[
        "no-blocks",
        "too-many-blocks",
        "heading-level",
        "blank-heading",
        "empty-paragraph",
        "empty-list",
        "blank-child-item",
        "too-many-items",
        "no-columns",
        "too-many-columns",
        "too-many-rows",
        "row-length",
        "blank-header",
        "format-for-type",
        "too-many-tables",
        "unreadable-values",
        "boolean-as-number",
        "huge-exponent",
        "too-many-digits",
        "long-cell",
    ],
)
def test_content_outside_the_limits_is_an_error(
    blocks: list[Block], expected: list[tuple[str, str]]
) -> None:
    assert _issues(*blocks) == expected


def test_blank_title_is_an_error() -> None:
    assert _issues(ParagraphBlock("x"), title=" ") == [("title", "required")]


def test_total_cells_and_characters_are_limited(monkeypatch: MonkeyPatch) -> None:
    table = _table(Column("名前", "text"), Column("数量", "number"), rows=[["a", 1], ["b", 2]])
    monkeypatch.setattr(docx_document, "MAX_TABLE_CELLS", 7)
    assert _build(table).tables == ({"rows": 2, "columns": 2},)
    assert _issues(table, table) == [("blocks", "too_many_cells")]

    monkeypatch.setattr(docx_document, "MAX_DOCUMENT_CHARS", 20)
    assert _build(ParagraphBlock("x" * 10), title="t").blocks["paragraph"] == 1
    assert _issues(ParagraphBlock("x" * 10), ParagraphBlock("y" * 10), title="t") == [
        ("blocks", "too_long")
    ]


def test_large_tables_are_pointed_to_a_spreadsheet_and_errors_carry_no_values() -> None:
    with pytest.raises(DocxSpecError) as raised:
        build_document(
            DocumentContent(
                title="合成の報告",
                blocks=[
                    _table(
                        Column("名前", "number"),
                        rows=[["secret-value"]] * (MAX_TABLE_ROWS + 1),
                    )
                ],
            )
        )

    [issue] = raised.value.issues
    # 大きい表は、Excel のファイルにすることを促す。誤りの文に値を写さない。
    assert "Excel のファイル" in issue.message and str(MAX_TABLE_ROWS) in issue.message
    assert "secret-value" not in str(raised.value)
    with pytest.raises(DocxSpecError) as unreadable:
        build_document(
            DocumentContent(
                title="合成の報告",
                blocks=[_table(Column("名前", "number"), rows=[["secret-value"]])],
            )
        )
    assert "secret-value" not in str(unreadable.value)
    assert "1 行目" in unreadable.value.issues[0].message
