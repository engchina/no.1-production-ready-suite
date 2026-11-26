"""pptx の生成の本体（`pptx_presentation.build_presentation`。#5837）。

作ったバイト列を python-pptx で開き直して確かめる（Run・DB・モデルは使わない。値は合成）。

- 固定の体裁: 16:9・1 つの字体（英数字と東アジアの両方）・題の下の強調の色の線・表紙を除く
  スライドの右下のスライド番号・テンプレートの余分な部品が無いこと。
- 6 種類のスライド: 題・副題・箇条書きの階層・左右の対比・表の値と右寄せ・グラフの値。
- 表が次のスライドへ続くこと（題の「（続き）」・見出しの行・行の分け方・発表者ノート）。
- 上限いっぱいの内容が枠に収まること（上限と、字の大きさ・行の高さ・枠の大きさの関係）。
- 作れない内容（上限の超過・型に合わない値・空の文・幅に収まらない表）は、切り捨てずに誤りに
  すること。誤りの文に値を載せないこと。
"""

from __future__ import annotations

import io
import math
import zipfile
from decimal import Decimal
from typing import Any

import pytest
from openpyxl import load_workbook  # type: ignore[import-untyped]
from pptx import Presentation
from pptx.enum.shapes import PP_PLACEHOLDER
from pptx.enum.text import MSO_ANCHOR, MSO_AUTO_SIZE, PP_ALIGN
from pptx.oxml.ns import qn
from pptx.util import Pt

from app.features.agent import pptx_presentation as module
from app.features.agent.chart_drawing import CHART_COLORS, CHART_KINDS, chart_parts
from app.features.agent.chart_spec import (
    MAX_CHART_CATEGORIES,
    MAX_PIE_CATEGORIES,
    ChartData,
    ChartSeries,
    ChartSpec,
)
from app.features.agent.pptx_presentation import (
    ACCENT,
    BULLET_ITEM_MAX_CHARS,
    COMPARISON_HEADING_MAX_CHARS,
    COMPARISON_ITEM_MAX_CHARS,
    CONTINUED_SUFFIX,
    FONT_NAME,
    MAX_BULLET_ITEMS,
    MAX_COMPARISON_ITEMS,
    MAX_SLIDES,
    MAX_TABLE_COLUMNS,
    MAX_TABLE_ROWS,
    MAX_TABLE_ROWS_PER_SLIDE,
    NOTES_MAX_CHARS,
    SLIDE_TYPES,
    SUBTITLE_MAX_CHARS,
    TITLE_MAX_CHARS,
    BuiltPresentation,
    BulletItem,
    BulletsSlide,
    ChartSlide,
    Column,
    ComparisonColumn,
    ComparisonSlide,
    CoverSlide,
    PptxSpecError,
    PresentationContent,
    SectionSlide,
    Slide,
    TableSlide,
    build_presentation,
)

BULLETS = BulletsSlide(
    "数量は 3 か月続けて増えている",
    [
        BulletItem("品目 A の数量が増えた", ["部門 1 が最も多い", "部門 2 は横ばい"]),
        BulletItem("品目 B は減った"),
    ],
)
ITEMS = [
    Column("品目", "text"),
    Column("数量（個）", "number", "integer"),
    Column("単価（円）", "number", "decimal2"),
    Column("比率", "number", "percent1"),
    Column("登録日", "date", "date_slash"),
    Column("更新日時", "date"),
    Column("番号", "text"),
]
ITEM_ROWS: list[list[Any]] = [
    ["品目 A", 1200, 1234.5, 0.256, "2026-01-05", "2026-01-05T09:30:00", "0001"],
    ["品目 B", "30", "0.125", 0.5, "2026-02-10T00:00:00", "2026-02-10", "=1+1"],
    ["品目 C", None, None, None, None, None, None],
]
SALES = [Column("月", "text"), Column("部門 1", "number", "integer"), Column("部門 2", "number")]
SALES_ROWS: list[list[Any]] = [["1 月", 120, 80.5], ["2 月", 135, None], ["3 月", 150, 95]]


def _build(*slides: Slide, title: str = "合成の資料") -> BuiltPresentation:
    built = build_presentation(PresentationContent(title=title, slides=list(slides)))
    assert built.data.startswith(b"PK\x03\x04")
    return built


def _open(*slides: Slide, title: str = "合成の資料") -> Any:
    return Presentation(io.BytesIO(_build(*slides, title=title).data))


def _issues(*slides: Slide, title: str = "合成の資料") -> list[tuple[str, str]]:
    with pytest.raises(PptxSpecError) as raised:
        build_presentation(PresentationContent(title=title, slides=list(slides)))
    return [(item.argument, item.code) for item in raised.value.issues]


def _title(slide: Any) -> str:
    return str(slide.shapes.title.text_frame.text)


def _paragraphs(shape: Any) -> list[tuple[int, str]]:
    return [(paragraph.level, paragraph.text) for paragraph in shape.text_frame.paragraphs]


def _table(slide: Any) -> Any:
    [frame] = [shape for shape in slide.shapes if shape.has_table]
    return frame.table


def _cell_texts(table: Any) -> list[list[str]]:
    return [[cell.text for cell in row.cells] for row in table.rows]


def _chart(slide: Any) -> Any:
    [frame] = [shape for shape in slide.shapes if shape.has_chart]
    return frame.chart


def _number_fields(slide: Any) -> list[str]:
    """スライド番号の placeholder の中の field の種類。"""
    return [
        str(kind)
        for shape in slide.placeholders
        if shape.placeholder_format.type == PP_PLACEHOLDER.SLIDE_NUMBER
        for kind in shape.element.xpath(".//a:fld/@type")
    ]


# ---- 固定の体裁 --------------------------------------------------------------------------------


def test_slides_are_16_9_and_the_template_leftovers_are_removed() -> None:
    built = _build(CoverSlide("合成の資料"), BULLETS, title="合成の資料")
    presentation = Presentation(io.BytesIO(built.data))

    assert (presentation.slide_width, presentation.slide_height) == (Pt(960), Pt(540))
    assert int(Pt(960)) * 9 == int(Pt(540)) * 16
    # テンプレートの 4:3 の印は残さない。
    assert presentation.element.xpath("p:sldSz/@type") == []
    properties = presentation.core_properties
    assert (properties.title, properties.author, properties.last_modified_by) == (
        "合成の資料",
        "",
        "",
    )
    assert (properties.comments, properties.language) == ("", "ja-JP")
    with zipfile.ZipFile(io.BytesIO(built.data)) as package:
        names = package.namelist()
    # テンプレートの縮小の画像・印刷の設定、マクロ・画像・動画・音声は入らない。
    assert [name for name in names if "thumbnail" in name or "printerSettings" in name] == []
    assert [name for name in names if name.startswith(("ppt/media/", "ppt/vbaProject"))] == []
    # アニメーション・画面の切り替えは無い。
    for slide in presentation.slides:
        assert slide.element.xpath(".//p:timing | .//p:transition") == []


def test_one_font_is_set_for_latin_and_east_asian_text_everywhere() -> None:
    presentation = _open(
        CoverSlide("合成の資料 ABC", "2026-11-26", notes="表紙のノート"),
        SectionSlide("1. 推移"),
        BULLETS,
        ComparisonSlide(
            "案 A と案 B",
            ComparisonColumn([BulletItem("早い", ["3 か月"])], "案 A"),
            ComparisonColumn([BulletItem("安い")]),
        ),
        TableSlide("表", ITEMS, ITEM_ROWS),
        ChartSlide("グラフ", SALES, SALES_ROWS, ChartSpec("line", 1, [2, 3])),
    )

    for slide in presentation.slides:
        parts = [slide.element, *([slide.notes_slide.element] if slide.has_notes_slide else [])]
        fonts = [properties for part in parts for properties in part.iter(qn("a:rPr"))]
        assert fonts
        # 書いた文字のすべて（題・本文・表・スライド番号・発表者ノート）に、2 つの字体を直接書く。
        for properties in fonts:
            assert properties.xpath("a:latin/@typeface") == [FONT_NAME]
            assert properties.xpath("a:ea/@typeface") == [FONT_NAME]
            assert properties.get("lang") == "ja-JP"
    # グラフの文字も同じ字体。
    [defaults] = _chart(presentation.slides[5]).element.xpath("c:txPr/a:p/a:pPr/a:defRPr")
    assert defaults.xpath("a:latin/@typeface") == defaults.xpath("a:ea/@typeface") == [FONT_NAME]
    # テーマの字体も同じ（利用者が PowerPoint で足す文字も同じ字体になる）。
    with zipfile.ZipFile(io.BytesIO(_build(BULLETS).data)) as package:
        theme = package.read("ppt/theme/theme1.xml").decode("utf-8")
    for tag in ("majorFont", "minorFont"):
        scheme = theme.split(f"<a:{tag}>")[1].split(f"</a:{tag}>")[0]
        assert f'<a:latin typeface="{FONT_NAME}"/>' in scheme
        assert f'<a:ea typeface="{FONT_NAME}"/>' in scheme


def test_every_slide_but_the_cover_has_a_slide_number_at_the_bottom_right() -> None:
    presentation = _open(
        CoverSlide("合成の資料"), SectionSlide("1. 推移"), BULLETS, TableSlide("表", ITEMS, [])
    )
    cover, *others = presentation.slides

    assert _number_fields(cover) == []
    for position, slide in enumerate(others, 2):
        assert _number_fields(slide) == ["slidenum"]
        [number] = [
            shape
            for shape in slide.placeholders
            if shape.placeholder_format.type == PP_PLACEHOLDER.SLIDE_NUMBER
        ]
        # 右下（右の余白にそろえ、本文の枠より下）。表示の文字は、今のスライドの番号。
        assert number.left + number.width == presentation.slide_width - Pt(module.MARGIN_X_PT)
        assert number.top >= Pt(module.BODY_TOP_PT + module.BODY_HEIGHT_PT)
        assert number.top + number.height < presentation.slide_height
        assert number.text_frame.paragraphs[0].alignment == PP_ALIGN.RIGHT
        assert number.text_frame.text == str(position)
    # フッターの文・日付は置かない。
    kinds = {
        shape.placeholder_format.type
        for slide in presentation.slides
        for shape in slide.placeholders
    }
    assert not kinds & {PP_PLACEHOLDER.FOOTER, PP_PLACEHOLDER.DATE}


def test_title_sits_above_one_accent_line_and_is_never_shrunk() -> None:
    presentation = _open(CoverSlide("合成の資料", "副題"), SectionSlide("1. 推移"), BULLETS)

    for slide, size in zip(presentation.slides, (32, 32, 24), strict=True):
        title = slide.shapes.title
        frame = title.text_frame
        [run] = frame.paragraphs[0].runs
        assert (run.font.size, run.font.bold) == (Pt(size), True)
        assert frame.paragraphs[0].alignment == PP_ALIGN.LEFT
        # 文字を自動で小さくしない・枠を広げない（収まらない内容は誤りにする）。
        assert (frame.auto_size, frame.word_wrap) == (MSO_AUTO_SIZE.NONE, True)
        assert frame.vertical_anchor == MSO_ANCHOR.BOTTOM
        # 強調の色は、題のすぐ下の細い線の 1 か所だけ（影などの効果は無い）。
        [line] = [shape for shape in slide.shapes if shape.element.tag == qn("p:cxnSp")]
        assert line.line.color.rgb == ACCENT and line.line.width == Pt(1.5)
        assert line.begin_y == line.end_y and 0 < line.begin_y - (title.top + title.height) <= Pt(8)
        assert (line.begin_x, line.end_x) == (title.left, title.left + title.width)
        assert line.element.find(qn("p:style")) is None
        assert slide.element.xpath(".//a:srgbClr/@val").count(str(ACCENT)) == 1
    # 題の位置は、同じ種類のスライドで変わらない（表紙と章の扉を除く種類は同じ位置）。
    others = _open(
        BULLETS,
        TableSlide("表", ITEMS, []),
        ChartSlide("グラフ", SALES, SALES_ROWS, ChartSpec("bar", 1, [2])),
    )
    assert (
        len({(slide.shapes.title.top, slide.shapes.title.height) for slide in others.slides}) == 1
    )


def test_template_layouts_are_widened_to_the_slide_width() -> None:
    presentation = _open(BULLETS)

    for layout in presentation.slide_layouts:
        for shape in layout.placeholders:
            assert 0 <= shape.left <= shape.left + shape.width <= presentation.slide_width
    # 題と本文の layout の題は、16:9 の幅のほぼ全体を使う（4:3 の幅のままではない）。
    [title] = [
        shape
        for shape in presentation.slide_layouts[1].placeholders
        if shape.placeholder_format.type == PP_PLACEHOLDER.TITLE
    ]
    assert title.width > presentation.slide_width * 0.85


# ---- スライドの種類 ----------------------------------------------------------------------------


def test_cover_and_section_keep_title_and_subtitle() -> None:
    presentation = _open(
        CoverSlide("合成の資料\n2 行目", "2026-11-26　部門 1"),
        CoverSlide("副題の無い表紙"),
        SectionSlide("1. 数量の推移"),
    )
    cover, plain, section = presentation.slides

    # 題の改行は空白にする。
    assert _title(cover) == "合成の資料 2 行目"
    [subtitle] = [
        shape
        for shape in cover.placeholders
        if shape.placeholder_format.type == PP_PLACEHOLDER.SUBTITLE
    ]
    assert subtitle.text_frame.text == "2026-11-26　部門 1"
    assert subtitle.top > cover.shapes.title.top + cover.shapes.title.height
    # 副題が無ければ、空の枠を残さない。
    assert [shape.placeholder_format.type for shape in plain.placeholders] == [
        PP_PLACEHOLDER.CENTER_TITLE
    ]
    assert _title(section) == "1. 数量の推移"
    assert [shape.placeholder_format.type for shape in section.placeholders] == [
        PP_PLACEHOLDER.TITLE,
        PP_PLACEHOLDER.SLIDE_NUMBER,
    ]


def test_bullets_keep_two_levels_with_fixed_sizes_and_spacing() -> None:
    [slide] = _open(BULLETS).slides
    body = slide.placeholders[1]

    assert _title(slide) == "数量は 3 か月続けて増えている"
    assert _paragraphs(body) == [
        (0, "品目 A の数量が増えた"),
        (1, "部門 1 が最も多い"),
        (1, "部門 2 は横ばい"),
        (0, "品目 B は減った"),
    ]
    frame = body.text_frame
    assert (frame.auto_size, frame.word_wrap) == (MSO_AUTO_SIZE.NONE, True)
    assert (body.top, body.height) == (Pt(module.BODY_TOP_PT), Pt(module.BODY_HEIGHT_PT))
    for paragraph in frame.paragraphs:
        level = paragraph.level
        [run] = paragraph.runs
        # 字の大きさ・行の高さ・項目の後の間隔は固定（字体に依らない行の高さ）。
        assert run.font.size == Pt(module.BULLET_PT[level])
        assert paragraph.line_spacing == Pt(module.BULLET_LINE_PT)
        assert paragraph.space_after == Pt(module.BULLET_AFTER_PT)
        # 行頭の記号と字下げは段ごとに決まっている（文に記号を書かない）。
        properties = paragraph._p.pPr  # noqa: SLF001 - 記号と字下げを読む API が無い
        assert properties.xpath("a:buChar/@char") == ["•" if level == 0 else "–"]
        indent = int(Pt(module.BULLET_INDENT_PT))
        assert (properties.get("marL"), properties.get("indent")) == (
            str(indent * (level + 1)),
            str(-indent),
        )


def test_comparison_puts_two_columns_side_by_side_with_optional_headings() -> None:
    [slide] = _open(
        ComparisonSlide(
            "案 A は早く、案 B は安い",
            ComparisonColumn([BulletItem("3 か月で始められる", ["既存の仕組みを使う"])], "案 A"),
            ComparisonColumn([BulletItem("費用は半分"), BulletItem("6 か月かかる")]),
        )
    ).slides
    left, right = slide.placeholders[1], slide.placeholders[2]

    assert _paragraphs(left) == [(0, "案 A"), (0, "3 か月で始められる"), (1, "既存の仕組みを使う")]
    assert _paragraphs(right) == [(0, "費用は半分"), (0, "6 か月かかる")]
    # 小見出しは太字で、行頭の記号が無い。
    heading = left.text_frame.paragraphs[0]
    assert heading.runs[0].font.bold is True
    assert heading._p.pPr.xpath("a:buNone") and not heading._p.pPr.xpath("a:buChar")  # noqa: SLF001
    # 左右は同じ幅・同じ高さで、重ならない。
    assert (left.top, left.width, left.height) == (right.top, right.width, right.height)
    assert left.left + left.width + Pt(module.COMPARISON_GAP_PT) == right.left
    assert right.left + right.width == slide.shapes.title.left + slide.shapes.title.width


def test_table_cells_are_formatted_text_and_numbers_and_dates_are_right_aligned() -> None:
    [slide] = _open(TableSlide("品目ごとの数量", ITEMS, ITEM_ROWS)).slides
    table = _table(slide)

    assert _cell_texts(table) == [
        ["品目", "数量（個）", "単価（円）", "比率", "登録日", "更新日時", "番号"],
        ["品目 A", "1,200", "1,234.50", "25.6%", "2026/01/05", "2026-01-05 09:30", "0001"],
        # 文字の列の値は、そのまま文字として入る（`=` で始まる文字を式にしない）。
        ["品目 B", "30", "0.13", "50.0%", "2026/02/10", "2026-02-10 00:00", "=1+1"],
        ["品目 C", "", "", "", "", "", ""],
    ]
    alignments = [cell.text_frame.paragraphs[0].alignment for cell in table.rows[1].cells]
    right, left = PP_ALIGN.RIGHT, PP_ALIGN.LEFT
    assert alignments == [left, right, right, right, right, right, left]
    for cell in table.rows[0].cells:
        paragraph = cell.text_frame.paragraphs[0]
        assert (paragraph.alignment, paragraph.runs[0].font.bold) == (PP_ALIGN.CENTER, True)
    # 表は本文の幅いっぱいで、行の高さ・字の大きさ・行の高さは固定。
    assert sum(column.width for column in table.columns) == pytest.approx(
        int(Pt(module.CONTENT_WIDTH_PT)), abs=len(ITEMS)
    )
    assert {row.height for row in table.rows} == {Pt(module.TABLE_ROW_PT)}
    for row in table.rows:
        for cell in row.cells:
            paragraph = cell.text_frame.paragraphs[0]
            assert paragraph.line_spacing == Pt(module.TABLE_LINE_PT)
            assert all(run.font.size == Pt(module.TABLE_PT) for run in paragraph.runs)
            # 体裁は cell ごとに決める（4 辺の罫線と塗り。罫線は塗りより前）。
            children = [child.tag for child in cell._tc.tcPr]  # noqa: SLF001
            assert children == [
                qn(f"a:{name}") for name in ("lnL", "lnR", "lnT", "lnB", "solidFill")
            ]
    assert table.horz_banding is False


def test_long_table_continues_on_the_next_slides_with_the_header_row() -> None:
    columns = [Column("品目", "text"), Column("数量", "number", "integer")]
    rows = [[f"品目 {index:02d}", index * 10] for index in range(1, 31)]
    built = _build(
        CoverSlide("合成の資料"),
        TableSlide("品目ごとの数量", columns, rows, notes="値の出所: 合成の表。"),
        BULLETS,
    )
    presentation = Presentation(io.BytesIO(built.data))

    assert (built.slide_count, built.continued_slides) == (5, 2)
    assert built.tables == ({"rows": 30, "columns": 2, "slides": 3},)
    assert built.slides == {**dict.fromkeys(SLIDE_TYPES, 0), "cover": 1, "table": 1, "bullets": 1}
    assert [_title(slide) for slide in presentation.slides] == [
        "合成の資料",
        "品目ごとの数量",
        "品目ごとの数量" + CONTINUED_SUFFIX,
        "品目ごとの数量" + CONTINUED_SUFFIX,
        "数量は 3 か月続けて増えている",
    ]
    tables = [_table(slide) for slide in list(presentation.slides)[1:4]]
    # 見出しの行は毎回付き、行は順に 12 行ずつ分かれる（値を落とさない・重ねない）。
    assert [len(table.rows) - 1 for table in tables] == [MAX_TABLE_ROWS_PER_SLIDE, 12, 6]
    assert all(_cell_texts(table)[0] == ["品目", "数量"] for table in tables)
    assert [row for table in tables for row in _cell_texts(table)[1:]] == [
        [f"品目 {index:02d}", str(index * 10)] for index in range(1, 31)
    ]
    # 列の幅は続きのスライドでも同じ。発表者ノートは最初のスライドにだけ置く。
    assert len({tuple(column.width for column in table.columns) for table in tables}) == 1
    assert [slide.has_notes_slide for slide in presentation.slides] == [
        False,
        True,
        False,
        False,
        False,
    ]
    # スライド番号は、続きのスライドを含めて通しで数える。
    assert [slide.placeholders[12].text_frame.text for slide in list(presentation.slides)[1:]] == [
        "2",
        "3",
        "4",
        "5",
    ]


def test_notes_are_plain_paragraphs_outside_the_slide() -> None:
    [slide] = _open(
        BulletsSlide(
            "題", [BulletItem("項目")], notes="値の出所: 合成の表 T1。\n\n**前提**: 税抜き。"
        )
    ).slides

    frame = slide.notes_slide.notes_text_frame
    # 改行は段落の区切り。Markdown の記号は解釈せず、そのまま文字として入れる。
    assert [paragraph.text for paragraph in frame.paragraphs] == [
        "値の出所: 合成の表 T1。",
        "**前提**: 税抜き。",
    ]
    assert "値の出所" not in "".join(shape.text_frame.text for shape in slide.placeholders)


# ---- グラフ ------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("chart_type", "series"),
    [("column", [2, 3]), ("bar", [3, 2]), ("line", [2, 3]), ("pie", [2])],
    ids=["column", "bar", "line", "pie"],
)
def test_chart_values_match_the_table(chart_type: str, series: list[int]) -> None:
    built = _build(ChartSlide("月ごとの数量", SALES, SALES_ROWS, ChartSpec(chart_type, 1, series)))
    [slide] = Presentation(io.BytesIO(built.data)).slides
    chart = _chart(slide)

    assert _title(slide) == "月ごとの数量"
    # PowerPoint のグラフとして入る（画像ではない。値は開いて編集できる）。
    assert chart.chart_type == CHART_KINDS[chart_type]
    assert not [shape for shape in slide.shapes if shape.shape_type == 13]
    [plot] = chart.plots
    assert list(plot.categories) == [row[0] for row in SALES_ROWS]
    assert [item.name for item in plot.series] == [SALES[column - 1].header for column in series]
    # 系列の値は表の数値と一致する（空の値は空のまま）。
    assert [list(item.values) for item in plot.series] == [
        [row[column - 1] for row in SALES_ROWS] for column in series
    ]
    assert built.charts == (
        {"type": chart_type, "series": len(series), "categories": len(SALES_ROWS)},
    )
    assert built.slides["chart"] == 1 and built.tables == ()
    # グラフは本文の枠に置く。
    [frame] = [shape for shape in slide.shapes if shape.has_chart]
    assert (frame.top, frame.height) == (Pt(module.BODY_TOP_PT), Pt(module.BODY_HEIGHT_PT))
    # グラフの XML は、スライドに依らない入口（`chart_parts`）が返すものと同じ。
    data = ChartData(
        chart_type,
        tuple(str(row[0]) for row in SALES_ROWS),
        tuple(
            ChartSeries(
                SALES[column - 1].header,
                tuple(_decimal(row[column - 1]) for row in SALES_ROWS),
                SALES[column - 1].format,
            )
            for column in series
        ),
    )
    expected, _ = chart_parts(data, font_name=FONT_NAME)
    with zipfile.ZipFile(io.BytesIO(built.data)) as package:
        actual = package.read("ppt/charts/chart1.xml")
    assert _without_workbook_reference(actual) == _without_workbook_reference(expected)


@pytest.mark.parametrize("chart_type", ["column", "bar", "line"])
def test_chart_with_negative_values_stays_readable(chart_type: str) -> None:
    """負の値の棒の色を反転せず、項目の名前をグラフの端に置く（#5858。体裁は共通の部品）。"""
    rows: list[list[Any]] = [["1 月", 120, -30.5], ["2 月", -80, None], ["3 月", 45, -90]]
    [slide] = _open(
        ChartSlide("月ごとの増減", SALES, rows, ChartSpec(chart_type, 1, [2, 3]))
    ).slides
    chart = _chart(slide)

    assert [list(item.values) for item in chart.plots[0].series] == [
        [120.0, -80.0, 45.0],
        [-30.5, None, -90.0],
    ]
    assert chart.element.xpath("//c:catAx/c:tickLblPos/@val") == ["low"]
    assert chart.element.xpath("//c:ser/c:invertIfNegative/@val") == (
        [] if chart_type == "line" else ["0", "0"]
    )


def test_pie_chart_writes_the_legend_position() -> None:
    [slide] = _open(ChartSlide("月ごとの割合", SALES, SALES_ROWS, ChartSpec("pie", 1, [2]))).slides

    assert _chart(slide).element.xpath("//c:legendPos/@val") == ["r"]


def _decimal(value: Any) -> Any:
    return None if value is None else Decimal(str(value))


def _without_workbook_reference(xml: bytes) -> str:
    """グラフの XML から、埋め込みのブックへの参照（スライドに入れたときだけ付く）を除く。"""
    text = xml.decode("utf-8")
    start = text.find("<c:externalData")
    return text if start < 0 else text[:start] + text[text.index("</c:externalData>") + 17 :]


def test_embedded_workbook_keeps_names_as_text() -> None:
    """グラフのデータのブックに、`=` で始まる項目・系列の名前を数式として入れない。"""
    columns = [Column("品目", "text"), Column("=2+2", "number")]
    built = _build(
        ChartSlide("題", columns, [["=1+1", 1], ["品目 B", 2]], ChartSpec("bar", 1, [2]))
    )

    with zipfile.ZipFile(io.BytesIO(built.data)) as package:
        [name] = [item for item in package.namelist() if item.startswith("ppt/embeddings/")]
        sheet = load_workbook(io.BytesIO(package.read(name))).active
    assert sheet is not None
    cells = [sheet["A2"], sheet["A3"], sheet["B1"]]
    assert [(cell.value, cell.data_type) for cell in cells] == [
        ("=1+1", "s"),
        ("品目 B", "s"),
        ("=2+2", "s"),
    ]
    assert [sheet["B2"].value, sheet["B3"].value] == [1, 2]
    # グラフの XML は、このブックの同じ場所を参照する。
    [slide] = Presentation(io.BytesIO(built.data)).slides
    references = _chart(slide).element.xpath("//c:ser//c:f/text()")
    assert references == ["Sheet1!$B$1", "Sheet1!$A$2:$A$3", "Sheet1!$B$2:$B$3"]
    assert sheet.title == "Sheet1"


def test_category_labels_are_the_formatted_column_text() -> None:
    columns = [Column("日付", "date", "date_slash"), Column("数量", "number", "decimal1")]
    rows = [["2026-11-01", 10.5], ["2026-11-02T00:00:00", 12]]
    [slide] = _open(ChartSlide("日付ごとの数量", columns, rows, ChartSpec("line", 1, [2]))).slides
    chart = _chart(slide)

    assert list(chart.plots[0].categories) == ["2026/11/01", "2026/11/02"]
    assert list(chart.plots[0].series[0].values) == [10.5, 12.0]
    assert chart.element.xpath("//c:ser/c:val/c:numRef/c:numCache/c:formatCode/text()") == [
        "#,##0.0"
    ]
    assert chart.plots[0].series[0].format.line.color.rgb == CHART_COLORS[0]


@pytest.mark.parametrize(
    ("slide", "expected"),
    [
        (
            ChartSlide("題", SALES, SALES_ROWS, ChartSpec("pie", 1, [3])),
            ("slides.0.table.columns.2", "pie_values"),
        ),
        (
            ChartSlide("題", SALES, SALES_ROWS, ChartSpec("column", 2, [1])),
            ("slides.0.chart.series_columns.0", "not_number"),
        ),
        (
            ChartSlide("題", SALES, SALES_ROWS, ChartSpec("line", 1, [4])),
            ("slides.0.chart.series_columns.0", "range"),
        ),
        (
            # 数値として読めない値は、空として描かずに誤りにする。
            ChartSlide("題", SALES, [["1 月", "多い", 1]], ChartSpec("column", 1, [2])),
            ("slides.0.table.columns.1.type", "not_number"),
        ),
        (
            ChartSlide("題", SALES, [[None, 1, 2]], ChartSpec("column", 1, [2])),
            ("slides.0.table.columns.0", "required"),
        ),
        (
            ChartSlide("題", SALES, [["1 月", None, 2]], ChartSpec("column", 1, [2])),
            ("slides.0.table.columns.1", "empty"),
        ),
        (
            ChartSlide(
                "題",
                SALES,
                [[f"{index} 月", index, index] for index in range(MAX_CHART_CATEGORIES + 1)],
                ChartSpec("bar", 1, [2]),
            ),
            ("slides.0.table.rows", "too_many"),
        ),
        (
            ChartSlide(
                "題",
                SALES,
                [[f"{index} 月", index, index] for index in range(MAX_PIE_CATEGORIES + 1)],
                ChartSpec("pie", 1, [2]),
            ),
            ("slides.0.table.rows", "too_many"),
        ),
        (
            ChartSlide("題", SALES, [], ChartSpec("bar", 1, [2])),
            ("slides.0.table.rows", "required"),
        ),
    ],
    ids=[
        "pie-with-empty-value",
        "text-series",
        "series-out-of-range",
        "not-a-number",
        "empty-category",
        "empty-series",
        "too-many-categories",
        "too-many-pie-categories",
        "no-rows",
    ],
)
def test_charts_that_cannot_be_drawn_are_errors(slide: Slide, expected: tuple[str, str]) -> None:
    assert _issues(slide) == [expected]


# ---- 上限いっぱいの内容が枠に収まること --------------------------------------------------------


def _lines(chars: int, width: float, size: float) -> int:
    """全角の文字だけの文が、幅 `width`（pt）の枠で使う行の数（字の大きさ `size` pt）。"""
    return math.ceil(chars / math.floor(width / size))


def test_limits_are_the_decided_values() -> None:
    assert (MAX_BULLET_ITEMS, BULLET_ITEM_MAX_CHARS, MAX_COMPARISON_ITEMS) == (8, 100, 6)
    assert (MAX_TABLE_ROWS_PER_SLIDE, MAX_TABLE_COLUMNS, MAX_TABLE_ROWS, MAX_SLIDES) == (
        12,
        8,
        60,
        50,
    )
    assert SLIDE_TYPES == ("cover", "section", "bullets", "table", "comparison", "chart")


def test_text_at_the_limits_fits_its_frame() -> None:
    width = module.CONTENT_WIDTH_PT
    # 題: 続きの印を付けても 2 行、表紙と章の扉の題・副題は 3 行に収まる。
    assert (
        _lines(TITLE_MAX_CHARS + len(CONTINUED_SUFFIX), width, module.TITLE_PT)
        <= module.TITLE_LINES
    )
    assert _lines(TITLE_MAX_CHARS, width, module.COVER_TITLE_PT) <= module.COVER_TITLE_LINES
    assert _lines(SUBTITLE_MAX_CHARS, width, module.SUBTITLE_PT) <= module.SUBTITLE_LINES
    # 箇条書き: 上限の数の項目が、どの段でも上限の文字数で、本文の枠に収まる（1 行ぶんの余りが
    # ある）。
    indent, line, after = module.BULLET_INDENT_PT, module.BULLET_LINE_PT, module.BULLET_AFTER_PT
    per_item = max(
        _lines(BULLET_ITEM_MAX_CHARS, width - indent * (level + 1), module.BULLET_PT[level])
        for level in (0, 1)
    )
    assert MAX_BULLET_ITEMS * (per_item * line + after) + line <= module.BODY_HEIGHT_PT
    # 対比: 小見出しは 1 行、上限の数の項目が上限の文字数で、列の枠に収まる。
    column = (width - module.COMPARISON_GAP_PT) / 2
    assert _lines(COMPARISON_HEADING_MAX_CHARS, column, module.BULLET_PT[0]) == 1
    per_item = max(
        _lines(COMPARISON_ITEM_MAX_CHARS, column - indent * (level + 1), module.BULLET_PT[level])
        for level in (0, 1)
    )
    heading = line + module.COMPARISON_HEADING_AFTER_PT
    assert (
        heading + MAX_COMPARISON_ITEMS * (per_item * line + after) + line <= module.BODY_HEIGHT_PT
    )
    # 表: 見出しの行と上限の数の行が本文の枠に収まり、1 つの cell の 2 行が行の高さに収まる。
    assert (MAX_TABLE_ROWS_PER_SLIDE + 1) * module.TABLE_ROW_PT <= module.BODY_HEIGHT_PT
    assert module.TABLE_CELL_LINES * module.TABLE_LINE_PT < module.TABLE_ROW_PT
    # 本文の枠は、題の線より下・スライドの下の余白より上。
    assert module.BODY_TOP_PT + module.BODY_HEIGHT_PT < module.SLIDE_HEIGHT_PT
    assert module.BODY_TOP_PT > 28 + module.TITLE_LINE_PT * module.TITLE_LINES


def test_content_at_the_limits_is_built() -> None:
    def text(length: int) -> str:
        return "字" * length

    item = BulletItem(text(COMPARISON_ITEM_MAX_CHARS))
    wide = [Column(f"列 {index}", "text") for index in range(MAX_TABLE_COLUMNS)]
    built = _build(
        CoverSlide(text(TITLE_MAX_CHARS), text(SUBTITLE_MAX_CHARS), notes=text(NOTES_MAX_CHARS)),
        BulletsSlide(
            text(TITLE_MAX_CHARS),
            [BulletItem(text(BULLET_ITEM_MAX_CHARS), [text(BULLET_ITEM_MAX_CHARS)])] * 4,
        ),
        ComparisonSlide(
            text(TITLE_MAX_CHARS),
            ComparisonColumn([item] * MAX_COMPARISON_ITEMS, text(COMPARISON_HEADING_MAX_CHARS)),
            ComparisonColumn([BulletItem(item.text, [item.text] * (MAX_COMPARISON_ITEMS - 1))]),
        ),
        TableSlide(
            text(TITLE_MAX_CHARS),
            wide,
            [
                [f"値 {row}-{column}" for column in range(MAX_TABLE_COLUMNS)]
                for row in range(MAX_TABLE_ROWS)
            ],
        ),
        ChartSlide(
            "題",
            [Column("項目", "text"), *(Column(f"系列 {index}", "number") for index in range(5))],
            [[f"項目 {row}", *range(5)] for row in range(MAX_CHART_CATEGORIES)],
            ChartSpec("column", 1, [2, 3, 4, 5, 6]),
        ),
    )

    assert built.slide_count == 4 + MAX_TABLE_ROWS // MAX_TABLE_ROWS_PER_SLIDE
    assert built.continued_slides == MAX_TABLE_ROWS // MAX_TABLE_ROWS_PER_SLIDE - 1
    presentation = Presentation(io.BytesIO(built.data))
    # 上限いっぱいでも、字の大きさは変わらない。
    body = presentation.slides[1].placeholders[1]
    assert {
        run.font.size for paragraph in body.text_frame.paragraphs for run in paragraph.runs
    } == {Pt(16), Pt(14)}


# ---- 作れない内容（切り捨てずに誤りにする） ----------------------------------------------------


def _bullets(count: int, children: int = 0) -> BulletsSlide:
    return BulletsSlide("題", [BulletItem("項目", ["補足"] * children)] * count)


@pytest.mark.parametrize(
    ("slide", "expected"),
    [
        (BulletsSlide(" ", [BulletItem("項目")]), ("slides.0.title", "required")),
        (SectionSlide("題" * (TITLE_MAX_CHARS + 1)), ("slides.0.title", "too_long")),
        (CoverSlide("題", "副" * (SUBTITLE_MAX_CHARS + 1)), ("slides.0.subtitle", "too_long")),
        (SectionSlide("題", notes="文" * (NOTES_MAX_CHARS + 1)), ("slides.0.notes", "too_long")),
        (BulletsSlide("題", []), ("slides.0.items", "required")),
        (_bullets(MAX_BULLET_ITEMS + 1), ("slides.0.items", "too_many")),
        # 1 段下の項目も数える。
        (_bullets(3, children=2), ("slides.0.items", "too_many")),
        (
            BulletsSlide("題", [BulletItem("文" * (BULLET_ITEM_MAX_CHARS + 1))]),
            ("slides.0.items.0.text", "too_long"),
        ),
        (
            BulletsSlide("題", [BulletItem("項目", ["文" * (BULLET_ITEM_MAX_CHARS + 1)])]),
            ("slides.0.items.0.children.0", "too_long"),
        ),
        (
            BulletsSlide("題", [BulletItem("項目", [" "])]),
            ("slides.0.items.0.children.0", "required"),
        ),
        (
            ComparisonSlide(
                "題",
                ComparisonColumn([BulletItem("項目")] * (MAX_COMPARISON_ITEMS + 1)),
                ComparisonColumn([BulletItem("項目")]),
            ),
            ("slides.0.left.items", "too_many"),
        ),
        (
            ComparisonSlide(
                "題",
                ComparisonColumn([BulletItem("項目")]),
                ComparisonColumn([BulletItem("文" * (COMPARISON_ITEM_MAX_CHARS + 1))]),
            ),
            ("slides.0.right.items.0.text", "too_long"),
        ),
        (
            ComparisonSlide(
                "題",
                ComparisonColumn([BulletItem("項目")], "見" * (COMPARISON_HEADING_MAX_CHARS + 1)),
                ComparisonColumn([]),
            ),
            ("slides.0.left.heading", "too_long"),
        ),
        (
            TableSlide("題", [Column(f"列 {index}", "text") for index in range(9)], []),
            ("slides.0.table.columns", "count"),
        ),
        (TableSlide("題", [], []), ("slides.0.table.columns", "count")),
        (
            TableSlide("題", [Column("品目", "text")], [["x"]] * (MAX_TABLE_ROWS + 1)),
            ("slides.0.table.rows", "too_many"),
        ),
        (
            TableSlide("題", [Column("品目", "text"), Column("数量", "number")], [["x"]]),
            ("slides.0.table.rows.0", "length"),
        ),
        (
            TableSlide("題", [Column(" ", "text")], []),
            ("slides.0.table.columns.0.header", "required"),
        ),
        (
            TableSlide("題", [Column("品目", "text", "integer")], []),
            ("slides.0.table.columns.0.format", "format"),
        ),
        (
            TableSlide("題", [Column("数量", "number")], [["多い"]]),
            ("slides.0.table.columns.0.type", "not_number"),
        ),
        (
            TableSlide("題", [Column("日付", "date")], [["2026-13-01"]]),
            ("slides.0.table.columns.0.type", "not_date"),
        ),
    ],
    ids=[
        "blank-title",
        "long-title",
        "long-subtitle",
        "long-notes",
        "no-items",
        "too-many-items",
        "too-many-items-with-children",
        "long-item",
        "long-child-item",
        "blank-child-item",
        "too-many-comparison-items",
        "long-comparison-item",
        "long-comparison-heading",
        "too-many-columns",
        "no-columns",
        "too-many-rows",
        "row-length",
        "blank-header",
        "format-for-type",
        "not-a-number",
        "not-a-date",
    ],
)
def test_content_outside_the_limits_is_an_error(slide: Slide, expected: tuple[str, str]) -> None:
    issues = _issues(slide)
    assert expected in issues
    # 対比の右の列が空の例だけ、もう 1 つの誤り（項目が無い）を持つ。
    assert len(issues) == (2 if expected[1] == "too_long" and "heading" in expected[0] else 1)


def test_blank_file_title_and_no_slides_are_errors() -> None:
    assert _issues(BULLETS, title=" ") == [("title", "required")]
    assert _issues() == [("slides", "count")]


def test_slides_are_limited_including_continued_table_slides() -> None:
    table = TableSlide("題", [Column("品目", "text")], [["x"]] * (MAX_TABLE_ROWS_PER_SLIDE + 1))
    # 表の 1 枚が 2 枚になるので、合わせて上限を 1 枚超える。
    assert _issues(*[SectionSlide("題")] * (MAX_SLIDES - 1), table) == [("slides", "too_many")]
    assert _build(*[SectionSlide("題")] * (MAX_SLIDES - 2), table).slide_count == MAX_SLIDES


def test_table_that_does_not_fit_the_slide_width_is_an_error() -> None:
    # どの cell も 2 行までに収まる幅を取れない表は、文字を小さくせずに誤りにする。
    long_text = "長い値の文字列" * 30
    assert _issues(
        TableSlide("題", [Column("品目", "text"), Column("説明", "text")], [["品目 A", long_text]])
    ) == [("slides.0.table.columns", "too_wide")]
    wide = [Column(f"列 {index}", "number", "integer") for index in range(MAX_TABLE_COLUMNS)]
    assert _issues(TableSlide("題", wide, [[10**30] * MAX_TABLE_COLUMNS])) == [
        ("slides.0.table.columns", "too_wide")
    ]
    # 2 行に折り返せば入る長さの文字は、列を広く取って入れる（数値の列は 1 行のまま）。
    fits = [Column("品目", "text"), Column("説明", "text"), Column("数量", "number", "integer")]
    [slide] = _open(TableSlide("題", fits, [["品目 A", "説明の文" * 20, 1234567]])).slides
    widths = [column.width for column in _table(slide).columns]
    assert widths[1] > widths[0] and widths[1] > widths[2]
    assert sum(widths) == pytest.approx(int(Pt(module.CONTENT_WIDTH_PT)), abs=3)


def test_column_widths_keep_every_cell_within_two_lines() -> None:
    widths = module._column_widths  # noqa: SLF001
    unit = module.TABLE_PT * module._HALF_WIDTH_EM  # noqa: SLF001
    padding = 2 * module._CELL_MARGIN_X_PT  # noqa: SLF001
    headers = ["品目", "説明", "数量"]
    texts = [["品目 A"], ["説明" * 50], ["1,234,567"]]

    fitted = widths(headers, texts, [False, False, True])

    assert fitted is not None and sum(fitted) == pytest.approx(module.CONTENT_WIDTH_PT)
    capacity = [(width - padding) / unit for width in fitted]
    # 文字の列は 2 行に、数値の列は 1 行に収まる幅を持つ。
    assert capacity[0] >= 6 and capacity[2] >= 9
    assert capacity[1] * module.TABLE_CELL_LINES >= 200
    # 短い内容の表は、内容の幅の比で本文の幅を分ける。
    even = widths(["a", "b"], [["x"], ["y"]], [False, False])
    assert even == pytest.approx([module.CONTENT_WIDTH_PT / 2] * 2)


def test_errors_point_to_a_spreadsheet_for_large_tables_and_carry_no_values() -> None:
    secret = "合成の機微な値"
    with pytest.raises(PptxSpecError) as raised:
        build_presentation(
            PresentationContent(
                title="題",
                slides=[
                    TableSlide("題", [Column("品目", "text")], [[secret]] * (MAX_TABLE_ROWS + 1)),
                    TableSlide("題", [Column("数量", "number")], [[secret]]),
                    ChartSlide("題", SALES, [[secret * 5, 1, 2]], ChartSpec("bar", 1, [2])),
                    BulletsSlide("題", [BulletItem(secret * 20)]),
                ],
            )
        )
    messages = [item.message for item in raised.value.issues]
    assert any("Excel のファイル" in message for message in messages)
    assert all(secret not in message for message in messages)
    assert secret not in str(raised.value)
    # 直し方を書く（スライドを分ける・文を短くする）。
    [too_many] = [
        item for item in _issue_objects(_bullets(MAX_BULLET_ITEMS + 1)) if item.code == "too_many"
    ]
    assert "スライドを分けてください" in too_many.message


def _issue_objects(*slides: Slide) -> list[Any]:
    with pytest.raises(PptxSpecError) as raised:
        build_presentation(PresentationContent(title="題", slides=list(slides)))
    return list(raised.value.issues)
