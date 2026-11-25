"""docx の生成の本体（#5837）。構造化した内容（題・見出し・段落・リスト・表）から .docx を作る。

Run・DB・ツールの層に依らない純粋な関数（`build_document`）。表の値の出所（Run の表の参照か、
モデルが書いた値か）は呼ぶ側（`docx_tool`）が解決し、ここには行の値として渡す。

- 作るもの: 題と副題、見出し（3 段まで）、段落、箇条書きと番号付きのリスト（入れ子は 1 段）、表
  （列ごとの型と書式・題）、改ページ。体裁は 1 つに決まっている（A4 縦・固定の余白・1 つの字体・
  フッターの中央のページ番号・罫線つきの表）。画像・図・グラフ・目次・ヘッダーとフッターの内容・
  脚注・コメント・変更履歴・段組み・横向き・文字の装飾・テンプレート・既存のファイルの編集は
  作らない。
- 文は装飾の無い文字として入れる（Markdown・HTML を解釈しない）。段落の文の改行は段落の区切りに
  し、題・見出し・項目の改行は空白にする。表の cell の改行は cell の中の改行のまま。
- 表の値は列の型で読み、書式を当てた文字にして入れる（数値・日付の列は右寄せ）。数値・日付として
  読めない値は、文字にしたり丸めたりせずに誤りにする（呼ぶ側が列の型を文字に直す）。列の型と
  書式の名前は Excel の生成（`xlsx_workbook`）と同じ（`table_columns`）。
- 上限（ブロック・表・行・列・cell の合計・文字数）を超える内容は、切り捨てずに誤りにする。Word の
  表は大きい表に向かないので、表の上限は Excel より小さい。
- 誤りは値を含まない場所・種類・直し方（`DocxSpecError.issues`）で返す。場所は `docx_tool` の引数の
  path（`blocks.2.table.columns.1` など。引数の検証の誤りと同じ書き方）。
- python-docx に無い所（東アジアの字体の指定・ページ番号の field・リストの番号の定義・表の見出しの
  行の繰り返しと網かけ）は、python-docx が公開する `OxmlElement` で最小限に書く。
"""

from __future__ import annotations

import io
import math
import re
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from decimal import ROUND_HALF_UP, Context, Decimal
from typing import Any

from docx import Document
from docx.enum.section import WD_ORIENT
from docx.enum.style import WD_STYLE_TYPE
from docx.enum.table import WD_CELL_VERTICAL_ALIGNMENT
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Emu, Mm, Pt, RGBColor, Twips
from docx.table import _Cell
from docx.text.paragraph import Paragraph

from app.features.agent.table_columns import (
    JSON_NUMBER,
    ColumnType,
    SpecIssue,
    ValueProblem,
    display_width,
    format_issue,
    iso_date_value,
)

DOCX_MIME_TYPE = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"

# 件数の上限（超えたら誤り）。行は表ごとのデータの行、cell は文書のすべての表のデータの cell の
# 合計。
MAX_BLOCKS = 300
MAX_HEADING_LEVEL = 3
MAX_LIST_ITEMS = 100
MAX_TABLES = 20
MAX_TABLE_COLUMNS = 10
MAX_TABLE_ROWS = 500
MAX_TABLE_CELLS = 5_000
# 文字数の上限（表の 1 つの cell と、文書の全体）。
MAX_CELL_CHARS = 2_000
MAX_DOCUMENT_CHARS = 200_000
# 数値として表示する桁の上限（指数の大きい値を展開しない）。
_MAX_NUMBER_DIGITS = 40

# 固定の体裁（1 つ）。用紙は A4 縦、字体は日本語の文書で一般的な 1 つ（埋め込まない。無い環境では
# 表示する側の代わりの字体になる）。
PAGE_WIDTH_MM = 210
PAGE_HEIGHT_MM = 297
MARGIN_TOP_BOTTOM_MM = 25
MARGIN_LEFT_RIGHT_MM = 20
FONT_NAME = "游ゴシック"
_BODY_PT = 10.5
_PARAGRAPH_AFTER_PT = 8.0
_SMALL_PT = 9.0
_SUBTITLE_COLOR = RGBColor(0x59, 0x59, 0x59)
_HEADER_FILL = "E7E6E6"
_TABLE_TEXT_STYLE = "Table Text"
_LIST_STYLE = "List Paragraph"
# 表の列の幅を決めるときの、1 列の表示の幅（半角の文字数の目安）の範囲。
_COLUMN_CHARS_MIN = 6
_COLUMN_CHARS_MAX = 40
# 半角の 1 文字の幅と、cell の左右の余白（twip。表の字の大きさでの目安）。
_CHAR_WIDTH = 110
_CELL_PADDING = 300
# リストの字下げ（1 段あたり。twip）。
_LIST_INDENT = 420

# 書式の名前 → 小数の桁数と、百分率か（名前は `NUMBER_FORMATS` と同じ）。
_NUMBER_DISPLAY: dict[str, tuple[int, bool]] = {
    "integer": (0, False),
    "decimal1": (1, False),
    "decimal2": (2, False),
    "percent": (0, True),
    "percent1": (1, True),
}
# 書式の名前 → 年月日の区切りと、時刻を出すか（名前は `DATE_FORMATS` と同じ）。
_DATE_DISPLAY: dict[str, tuple[str, bool]] = {
    "date": ("-", False),
    "date_slash": ("/", False),
    "datetime": ("-", True),
}
# Excel の表示と同じ四捨五入（桁の多い値も丸めずに扱える精度）。
_DECIMAL = Context(prec=_MAX_NUMBER_DIGITS * 3, rounding=ROUND_HALF_UP)
# XML に入れられない文字（制御文字・対にならない surrogate・非文字）。
_XML_ILLEGAL = re.compile("[\x00-\x08\x0b\x0c\x0e-\x1f\ud800-\udfff\ufffe\uffff]")

_TOO_LARGE_FIX = (
    "大きい表は Word の文書に向きません。行・列を絞るか、Excel のファイルにしてください。"
)


@dataclass(frozen=True, slots=True)
class HeadingBlock:
    # 見出しのレベル（1〜`MAX_HEADING_LEVEL`）。
    level: int
    text: str


@dataclass(frozen=True, slots=True)
class ParagraphBlock:
    text: str


@dataclass(frozen=True, slots=True)
class ListItem:
    text: str
    # 1 段下の項目。
    children: Sequence[str] = ()


@dataclass(frozen=True, slots=True)
class ListBlock:
    # True は番号付き、False は箇条書き。
    ordered: bool
    items: Sequence[ListItem]


@dataclass(frozen=True, slots=True)
class Column:
    header: str
    type: ColumnType
    # 書式の名前（数値は `NUMBER_FORMATS`、日付は `DATE_FORMATS` の key）。None は既定。
    format: str | None = None


@dataclass(frozen=True, slots=True)
class TableBlock:
    columns: Sequence[Column]
    # 行ごとの値（列の順。None は空の cell）。
    rows: Sequence[Sequence[Any]]
    caption: str | None = None


@dataclass(frozen=True, slots=True)
class PageBreakBlock:
    pass


Block = HeadingBlock | ParagraphBlock | ListBlock | TableBlock | PageBreakBlock
# ブロックの種類の名前（`docx_tool` の引数の `type` と、結果の要約の key）。
BLOCK_TYPES = ("heading", "paragraph", "bullets", "numbered", "table", "page_break")


@dataclass(frozen=True, slots=True)
class DocumentContent:
    title: str
    blocks: Sequence[Block]
    subtitle: str | None = None


class DocxSpecError(ValueError):
    """内容が作れる範囲に合わない（`issues` を直して作り直す）。"""

    def __init__(self, issues: Sequence[SpecIssue]) -> None:
        super().__init__("; ".join(f"{item.argument}: {item.message}" for item in issues))
        self.issues = tuple(issues)


@dataclass(frozen=True, slots=True)
class BuiltDocument:
    data: bytes = field(repr=False)
    # ブロックの種類ごとの数（`BLOCK_TYPES` の名前）。
    blocks: dict[str, int]
    # 表ごとの要約（文書に出る順。データの行の数と列の数）。
    tables: tuple[dict[str, int], ...]


def clean_text(value: str) -> str:
    """XML に入れられない文字を除き、改行を `\\n` にそろえる。"""
    return _XML_ILLEGAL.sub("", value.replace("\r\n", "\n").replace("\r", "\n"))


def _line(value: str) -> str:
    """1 行の文（改行は 1 つの空白にする。文の中の空白は変えない）。"""
    return " ".join(_paragraph_lines(value))


def _paragraph_lines(value: str) -> list[str]:
    """段落の文を、改行で段落に分ける（空の行は除く）。"""
    return [line.strip() for line in clean_text(value).split("\n") if line.strip()]


def _block_type(block: Block) -> str:
    if isinstance(block, HeadingBlock):
        return "heading"
    if isinstance(block, ParagraphBlock):
        return "paragraph"
    if isinstance(block, ListBlock):
        return "numbered" if block.ordered else "bullets"
    if isinstance(block, TableBlock):
        return "table"
    return "page_break"


# ---- 表の値（列の型で読み、書式を当てた文字にする） -------------------------------------------


def _text_value(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, bool):
        return "true" if value else "false"
    return clean_text(value if isinstance(value, str) else str(value)).strip() or None


def _number_value(value: Any) -> Decimal | None:
    if value is None:
        return None
    if isinstance(value, str):
        text = value.strip()
        if JSON_NUMBER.fullmatch(text) is None:
            raise ValueProblem("not_number", "数値として読めません。")
        number = Decimal(text)
    elif isinstance(value, bool) or not isinstance(value, int | float):
        raise ValueProblem("not_number", "数値として読めません。")
    elif isinstance(value, float):
        if not math.isfinite(value):
            raise ValueProblem("not_number", "数値として読めません。")
        number = Decimal(repr(value))
    else:
        number = Decimal(value)
    if number.is_zero():
        return Decimal(0)
    if (
        len(number.as_tuple().digits) > _MAX_NUMBER_DIGITS
        or abs(number.adjusted()) > _MAX_NUMBER_DIGITS
    ):
        raise ValueProblem("precision", "桁が多すぎて、数値として表示できません。")
    return number


_CONVERTERS: dict[str, Callable[[Any], Any]] = {
    "text": _text_value,
    "number": _number_value,
    "date": iso_date_value,
}


def _number_text(number: Decimal, format_name: str | None) -> str:
    if format_name is None:
        # 書式なしは、値のまま（桁区切りなし。指数は展開する）。
        return format(number, "f")
    decimals, percent = _NUMBER_DISPLAY[format_name]
    scaled = number.scaleb(2, context=_DECIMAL) if percent else number
    rounded = scaled.quantize(Decimal(1).scaleb(-decimals), context=_DECIMAL)
    if rounded.is_zero():
        rounded = abs(rounded)
    return f"{rounded:,.{decimals}f}" + ("%" if percent else "")


def _date_text(value: date | datetime, format_name: str) -> str:
    separator, with_time = _DATE_DISPLAY[format_name]
    text = f"{value.year:04d}{separator}{value.month:02d}{separator}{value.day:02d}"
    if not with_time:
        return text
    moment = value if isinstance(value, datetime) else datetime(value.year, value.month, value.day)
    return f"{text} {moment.hour:02d}:{moment.minute:02d}"


def _column_texts(column: Column, values: Sequence[Any]) -> list[str]:
    """1 つの列の、型で読んだ値を表示の文字にする（None は空）。"""
    if column.type == "number":
        return ["" if value is None else _number_text(value, column.format) for value in values]
    if column.type == "date":
        # 既定は内容から決める（時刻のある値が 1 つでもあれば日時）。
        has_time = any(isinstance(value, datetime) for value in values)
        name = column.format or ("datetime" if has_time else "date")
        return ["" if value is None else _date_text(value, name) for value in values]
    return ["" if value is None else str(value) for value in values]


def _column_cells(
    at: str, column: Column, cells: Sequence[Any], issues: list[SpecIssue]
) -> list[str]:
    """1 つの列の値を、表示の文字にする（読めない値・長すぎる値は、最初の 1 件を誤りにする）。"""
    convert = _CONVERTERS[column.type]
    values: list[Any] = []
    for row_index, cell in enumerate(cells):
        try:
            values.append(convert(cell))
        except ValueProblem as problem:
            issues.append(
                SpecIssue(
                    f"{at}.type",
                    problem.code,
                    f"この列の {row_index + 1} 行目の値は、{problem.message}{problem.fix}",
                )
            )
            return [""] * len(cells)
    texts = _column_texts(column, values)
    long_row = next((row for row, text in enumerate(texts) if len(text) > MAX_CELL_CHARS), None)
    if long_row is not None:
        issues.append(
            SpecIssue(
                at,
                "too_long",
                f"この列の {long_row + 1} 行目の値は、文字が長すぎます（表の 1 つの cell は "
                f"{MAX_CELL_CHARS} 文字まで）。値を短くするか、この列を外してください。",
            )
        )
    return texts


def _table_texts(index: int, table: TableBlock, issues: list[SpecIssue]) -> list[list[str]]:
    """表の行の値を、表示の文字にする（行ごと・列の順）。"""
    columns = [
        _column_cells(
            f"blocks.{index}.table.columns.{position}",
            column,
            [row[position] for row in table.rows],
            issues,
        )
        for position, column in enumerate(table.columns)
    ]
    return [[column[row] for column in columns] for row in range(len(table.rows))]


# ---- 内容の検査 --------------------------------------------------------------------------------


def _required(at: str, text: str | None, issues: list[SpecIssue]) -> None:
    if not _line(text or ""):
        issues.append(SpecIssue(at, "required", "空でない文を書いてください。"))


def _list_issues(at: str, block: ListBlock, issues: list[SpecIssue]) -> None:
    count = len(block.items) + sum(len(item.children) for item in block.items)
    if not block.items:
        issues.append(SpecIssue(f"{at}.items", "required", "項目を 1 つ以上書いてください。"))
    if count > MAX_LIST_ITEMS:
        issues.append(
            SpecIssue(
                f"{at}.items",
                "too_many",
                f"1 つのリストの項目は、1 段下の項目を含めて {MAX_LIST_ITEMS} 個までです。"
                "リストを分けてください。",
            )
        )
    for item_index, item in enumerate(block.items):
        _required(f"{at}.items.{item_index}.text", item.text, issues)
        for child_index, child in enumerate(item.children):
            _required(f"{at}.items.{item_index}.children.{child_index}", child, issues)


def _table_issues(at: str, table: TableBlock, issues: list[SpecIssue]) -> None:
    width = len(table.columns)
    if not 1 <= width <= MAX_TABLE_COLUMNS:
        issues.append(
            SpecIssue(
                f"{at}.columns",
                "count",
                f"1 つの表の列は 1〜{MAX_TABLE_COLUMNS} 列にしてください。{_TOO_LARGE_FIX}",
            )
        )
    if len(table.rows) > MAX_TABLE_ROWS:
        issues.append(
            SpecIssue(
                f"{at}.rows",
                "too_many",
                f"1 つの表の行は {MAX_TABLE_ROWS} 行までです。{_TOO_LARGE_FIX}",
            )
        )
    for row_index, row in enumerate(table.rows):
        if len(row) != width:
            issues.append(
                SpecIssue(
                    f"{at}.rows.{row_index}",
                    "length",
                    f"行の値の数を列の数（{width}）にそろえてください。",
                )
            )
            break
    for column_index, column in enumerate(table.columns):
        column_at = f"{at}.columns.{column_index}"
        _required(f"{column_at}.header", column.header, issues)
        issue = format_issue(column_at, column.type, column.format)
        if issue is not None:
            issues.append(issue)


def _structure_issues(content: DocumentContent) -> list[SpecIssue]:
    issues: list[SpecIssue] = []
    _required("title", content.title, issues)
    if not 1 <= len(content.blocks) <= MAX_BLOCKS:
        issues.append(
            SpecIssue("blocks", "count", f"ブロックは 1〜{MAX_BLOCKS} 個にしてください。")
        )
    tables = 0
    cells = 0
    for index, block in enumerate(content.blocks):
        at = f"blocks.{index}"
        if isinstance(block, HeadingBlock):
            if not 1 <= block.level <= MAX_HEADING_LEVEL:
                issues.append(
                    SpecIssue(
                        f"{at}.level",
                        "range",
                        f"見出しのレベルは 1〜{MAX_HEADING_LEVEL} にしてください。",
                    )
                )
            _required(f"{at}.text", block.text, issues)
        elif isinstance(block, ParagraphBlock):
            _required(f"{at}.text", block.text, issues)
        elif isinstance(block, ListBlock):
            _list_issues(at, block, issues)
        elif isinstance(block, TableBlock):
            tables += 1
            cells += len(block.columns) * len(block.rows)
            _table_issues(f"{at}.table", block, issues)
    if tables > MAX_TABLES:
        issues.append(
            SpecIssue("blocks", "too_many_tables", f"1 つの文書の表は {MAX_TABLES} 個までです。")
        )
    if cells > MAX_TABLE_CELLS:
        issues.append(
            SpecIssue(
                "blocks",
                "too_many_cells",
                f"表の cell の合計（行 × 列。すべての表）は {MAX_TABLE_CELLS} までです。"
                f"{_TOO_LARGE_FIX}",
            )
        )
    return issues


def _characters(content: DocumentContent, tables: dict[int, list[list[str]]]) -> int:
    """文書に入れる文字の数（題・本文・表）。"""
    count = len(content.title) + len(content.subtitle or "")
    for index, block in enumerate(content.blocks):
        if isinstance(block, HeadingBlock | ParagraphBlock):
            count += len(block.text)
        elif isinstance(block, ListBlock):
            count += sum(
                len(item.text) + sum(len(child) for child in item.children) for item in block.items
            )
        elif isinstance(block, TableBlock):
            count += len(block.caption or "") + sum(len(column.header) for column in block.columns)
            count += sum(len(text) for row in tables[index] for text in row)
    return count


# ---- 文書の組み立て（python-docx） -------------------------------------------------------------


def _w(tag: str, **attributes: str) -> Any:
    """WordprocessingML の要素（`w:` の名前空間の tag と属性）。"""
    element = OxmlElement(f"w:{tag}")
    for name, value in attributes.items():
        element.set(qn(f"w:{name}"), value)
    return element


def _apply_font(run_properties: Any) -> None:
    """字体を 1 つに決める（東アジアの文字の字体 `w:eastAsia` を含む。テーマの字体は使わない）。"""
    for fonts in run_properties.findall(qn("w:rFonts")):
        run_properties.remove(fonts)
    run_properties.insert(
        0, _w("rFonts", ascii=FONT_NAME, hAnsi=FONT_NAME, eastAsia=FONT_NAME, cs=FONT_NAME)
    )


def _restyle(
    style: Any,
    *,
    size: float,
    bold: bool = False,
    before: float = 0,
    after: float = 0,
    keep_with_next: bool = False,
    alignment: WD_ALIGN_PARAGRAPH | None = None,
    color: RGBColor | None = None,
) -> None:
    """テンプレートの段落の style を、固定の体裁に書き直す（テーマの色・字体・罫線を外す）。"""
    element = style.element
    for run_properties in element.findall(qn("w:rPr")):
        element.remove(run_properties)
    for paragraph_properties in element.findall(qn("w:pPr")):
        for tag in ("w:pBdr", "w:numPr", "w:ind", "w:contextualSpacing"):
            for child in paragraph_properties.findall(qn(tag)):
                paragraph_properties.remove(child)
    style.font.size = Pt(size)
    style.font.bold = bold
    style.font.color.rgb = color or RGBColor(0, 0, 0)
    _apply_font(element.find(qn("w:rPr")))
    paragraph_format = style.paragraph_format
    paragraph_format.space_before = Pt(before)
    paragraph_format.space_after = Pt(after)
    paragraph_format.line_spacing = 1.0
    paragraph_format.keep_with_next = keep_with_next
    paragraph_format.alignment = alignment


def _setup_styles(document: Any) -> None:
    # 文書の既定（テーマの字体の指定を外し、東アジアの言語を日本語にする）。
    [defaults] = document.styles.element.xpath("w:docDefaults/w:rPrDefault/w:rPr")
    _apply_font(defaults)
    for language in defaults.findall(qn("w:lang")):
        language.set(qn("w:eastAsia"), "ja-JP")
    styles = document.styles
    _restyle(styles["Normal"], size=_BODY_PT, after=_PARAGRAPH_AFTER_PT)
    _restyle(styles["Title"], size=20, bold=True, after=6, alignment=WD_ALIGN_PARAGRAPH.CENTER)
    _restyle(
        styles["Subtitle"],
        size=_BODY_PT,
        after=18,
        alignment=WD_ALIGN_PARAGRAPH.CENTER,
        color=_SUBTITLE_COLOR,
    )
    # 見出しは、レベルごとに字の大きさ・前後の間隔・次の段落と離さない指定を持つ。
    for level, (size, before, after) in enumerate(((16, 18, 8), (13, 14, 6), (11, 10, 4)), 1):
        _restyle(
            styles[f"Heading {level}"],
            size=size,
            bold=True,
            before=before,
            after=after,
            keep_with_next=True,
        )
    _restyle(styles["Caption"], size=_SMALL_PT, bold=True, before=6, after=3, keep_with_next=True)
    _restyle(styles[_LIST_STYLE], size=_BODY_PT, after=2)
    table_text = styles.add_style(_TABLE_TEXT_STYLE, WD_STYLE_TYPE.PARAGRAPH)
    table_text.base_style = styles["Normal"]
    _restyle(table_text, size=_SMALL_PT, before=2, after=2)


def _setup_page(document: Any) -> int:
    """用紙（A4 縦・固定の余白）と、フッターの中央のページ番号。本文の幅（twip）を返す。"""
    section = document.sections[0]
    section.orientation = WD_ORIENT.PORTRAIT
    section.page_width, section.page_height = Mm(PAGE_WIDTH_MM), Mm(PAGE_HEIGHT_MM)
    section.top_margin = section.bottom_margin = Mm(MARGIN_TOP_BOTTOM_MM)
    section.left_margin = section.right_margin = Mm(MARGIN_LEFT_RIGHT_MM)
    section.header_distance = section.footer_distance = Mm(12)
    footer = section.footer
    footer.is_linked_to_previous = False
    paragraph = footer.paragraphs[0]
    paragraph.alignment = WD_ALIGN_PARAGRAPH.CENTER
    # ページ番号は field（開く側がページごとに数える）。
    instruction = _w("instrText")
    instruction.set(qn("xml:space"), "preserve")
    instruction.text = " PAGE "
    placeholder = _w("t")
    placeholder.text = "1"
    for child in (
        _w("fldChar", fldCharType="begin"),
        instruction,
        _w("fldChar", fldCharType="separate"),
        placeholder,
        _w("fldChar", fldCharType="end"),
    ):
        run = paragraph.add_run()
        run.font.size = Pt(_SMALL_PT)
        run._r.append(child)  # noqa: SLF001 - python-docx に field の API が無い
    return int(Emu(section.page_width - section.left_margin - section.right_margin).twips)


def _list_definition(numbering: Any, levels: Sequence[tuple[str, str]]) -> int:
    """リストの番号の定義（`w:abstractNum`。2 段）を足して、その ID を返す。"""
    used = [int(value) for value in numbering.xpath("w:abstractNum/@w:abstractNumId")]
    abstract_id = max(used, default=-1) + 1
    abstract = _w("abstractNum", abstractNumId=str(abstract_id))
    abstract.append(_w("multiLevelType", val="hybridMultilevel"))
    for level, (number_format, text) in enumerate(levels):
        definition = _w("lvl", ilvl=str(level))
        definition.append(_w("start", val="1"))
        definition.append(_w("numFmt", val=number_format))
        definition.append(_w("lvlText", val=text))
        definition.append(_w("lvlJc", val="left"))
        properties = _w("pPr")
        properties.append(
            _w("ind", left=str(_LIST_INDENT * (level + 1)), hanging=str(_LIST_INDENT))
        )
        definition.append(properties)
        abstract.append(definition)
    # `w:abstractNum` は `w:num` より前に置く。
    first_num = numbering.find(qn("w:num"))
    if first_num is None:
        numbering.append(abstract)
    else:
        first_num.addprevious(abstract)
    return abstract_id


def _fitted(natural: Sequence[int], room: int) -> list[int]:
    """幅の合計を `room` に収める（狭い列はそのまま、広い列を同じ幅まで縮める）。"""
    widths = list(natural)
    order = sorted(range(len(natural)), key=lambda position: natural[position])
    for rank, position in enumerate(order):
        share = room // (len(order) - rank)
        if natural[position] > share:
            for rest in order[rank:]:
                widths[rest] = share
            break
        room -= natural[position]
    return widths


def _column_widths(
    headers: Sequence[str], rows: Sequence[Sequence[str]], keep: Sequence[bool], total: int
) -> list[int]:
    """列の幅（twip。合計は本文の幅 `total`）。

    内容の表示の幅で入るときは、その比で `total` を分ける。入りきらないときは、`keep` の列
    （数値・日付。途中で折り返すと読みにくい）の幅を保ち、ほかの列（文字。cell の中で折り返せる）を
    広い列から縮める。
    """
    natural = [
        _CELL_PADDING
        + _CHAR_WIDTH
        * min(
            max(
                display_width(header),
                *(display_width(row[position]) for row in rows),
                _COLUMN_CHARS_MIN,
            ),
            _COLUMN_CHARS_MAX,
        )
        for position, header in enumerate(headers)
    ]
    kept = sum(width for width, fixed in zip(natural, keep, strict=True) if fixed)
    shrinkable = [width for width, fixed in zip(natural, keep, strict=True) if not fixed]
    narrowest = _CELL_PADDING + _CHAR_WIDTH * _COLUMN_CHARS_MIN
    if sum(natural) > total and total - kept >= narrowest * len(shrinkable) > 0:
        fitted = iter(_fitted(shrinkable, total - kept))
        widths = [
            width if fixed else next(fitted) for width, fixed in zip(natural, keep, strict=True)
        ]
    else:
        widths = [total * width // sum(natural) for width in natural]
    widths[-1] += total - sum(widths)
    return widths


class _Writer:
    """ブロックを上から順に文書へ書く。"""

    def __init__(self, title: str) -> None:
        self.document: Any = Document()
        self.content_width = _setup_page(self.document)
        _setup_styles(self.document)
        for setting in self.document.settings.element.xpath(
            "w:compat/w:compatSetting[@w:name='compatibilityMode']"
        ):
            # 今の Word の文書として開く（古い版との互換の表示にしない）。
            setting.set(qn("w:val"), "15")
        properties = self.document.core_properties
        properties.title = title
        properties.author = ""
        properties.comments = ""
        properties.language = "ja-JP"
        properties.created = properties.modified = datetime.now(UTC).replace(tzinfo=None)
        self._numbering: Any = self.document.part.numbering_part.element
        self._bullets = _list_definition(self._numbering, (("bullet", "•"), ("bullet", "–")))
        self._numbers = _list_definition(self._numbering, (("decimal", "%1."), ("decimal", "(%2)")))
        self._next_num_id = (
            max((int(v) for v in self._numbering.xpath("w:num/@w:numId")), default=0) + 1
        )
        self._bullet_num_id: int | None = None
        # 次に書く段落の前で改ページするか。
        self._break_pending = False
        self._table_text_style_id = self.document.styles[_TABLE_TEXT_STYLE].style_id

    def _paragraph(self, text: str, style: str) -> Any:
        paragraph = self.document.add_paragraph(text, style=style)
        if self._break_pending:
            paragraph.paragraph_format.page_break_before = True
            self._break_pending = False
        return paragraph

    def _spacer(self) -> None:
        """高さの小さい空の段落（表の後の間隔、段落の無い所での改ページ）。"""
        paragraph_format = self._paragraph("", "Normal").paragraph_format
        paragraph_format.space_after = Pt(0)
        paragraph_format.line_spacing = Pt(6)

    def title(self, title: str, subtitle: str | None) -> None:
        paragraph = self._paragraph(_line(title), "Title")
        if subtitle and _line(subtitle):
            self._paragraph(_line(subtitle), "Subtitle")
        else:
            paragraph.paragraph_format.space_after = Pt(18)

    def heading(self, block: HeadingBlock) -> None:
        self._paragraph(_line(block.text), f"Heading {block.level}")

    def paragraph(self, block: ParagraphBlock) -> None:
        for line in _paragraph_lines(block.text):
            self._paragraph(line, "Normal").alignment = WD_ALIGN_PARAGRAPH.JUSTIFY

    def _num_id(self, abstract_id: int, *, restart: bool) -> int:
        """リストの番号の実体（`w:num`）を足す。番号付きは、リストごとに 1 から始める。"""
        num_id = self._next_num_id
        self._next_num_id += 1
        num = _w("num", numId=str(num_id))
        num.append(_w("abstractNumId", val=str(abstract_id)))
        if restart:
            for level in ("0", "1"):
                override = _w("lvlOverride", ilvl=level)
                override.append(_w("startOverride", val="1"))
                num.append(override)
        nums = self._numbering.findall(qn("w:num"))
        if nums:
            nums[-1].addnext(num)
        else:
            self._numbering.append(num)
        return num_id

    def items(self, block: ListBlock) -> None:
        if block.ordered:
            num_id = self._num_id(self._numbers, restart=True)
        else:
            if self._bullet_num_id is None:
                self._bullet_num_id = self._num_id(self._bullets, restart=False)
            num_id = self._bullet_num_id
        paragraph: Any = None
        for item in block.items:
            for level, text in ((0, item.text), *((1, child) for child in item.children)):
                paragraph = self._paragraph(_line(text), _LIST_STYLE)
                # python-docx に、段落へ番号の実体を当てる API が無い。
                number = paragraph._p.get_or_add_pPr().get_or_add_numPr()  # noqa: SLF001
                number.append(_w("ilvl", val=str(level)))
                number.append(_w("numId", val=str(num_id)))
        if paragraph is not None:
            # 項目の間は詰め、リストの後は段落と同じ間隔を置く。
            paragraph.paragraph_format.space_after = Pt(_PARAGRAPH_AFTER_PT)

    def table(self, block: TableBlock, rows: Sequence[Sequence[str]]) -> None:
        if block.caption and _line(block.caption):
            self._paragraph(_line(block.caption), "Caption")
        elif self._break_pending:
            self._spacer()
        headers = [_line(column.header) for column in block.columns]
        right = [column.type in ("number", "date") for column in block.columns]
        widths = _column_widths(headers, rows, right, self.content_width)
        table = self.document.add_table(rows=len(rows) + 1, cols=len(headers), style="Table Grid")
        # 表の幅と列の幅を固定する（表・列の grid・cell のすべてに同じ幅を書く）。
        table.autofit = False
        element = table._tbl  # noqa: SLF001 - 行・cell を XML の順に書く（大きい表で遅くしない）
        for table_width in element.tblPr.findall(qn("w:tblW")):
            table_width.set(qn("w:type"), "dxa")
            table_width.set(qn("w:w"), str(self.content_width))
        for grid_column, width in zip(element.tblGrid.gridCol_lst, widths, strict=True):
            grid_column.w = Twips(width)
        for row_index, row in enumerate(element.tr_lst):
            row_properties = row.get_or_add_trPr()
            # 行をページの途中で分けない。見出しの行は、ページごとに繰り返す。
            row_properties.append(_w("cantSplit"))
            if row_index == 0:
                row_properties.append(_w("tblHeader"))
            texts = headers if row_index == 0 else rows[row_index - 1]
            for position, cell_element in enumerate(row.tc_lst):
                cell_element.width = Twips(widths[position])
                paragraph_element = cell_element.p_lst[0]
                # style は ID で当てる（名前からの検索を cell ごとに繰り返さない）。
                paragraph_element.style = self._table_text_style_id
                paragraph = Paragraph(paragraph_element, table)
                run = paragraph.add_run(texts[position])
                if row_index == 0:
                    run.bold = True
                    paragraph.alignment = WD_ALIGN_PARAGRAPH.CENTER
                    # 薄い網かけ（塗りは `fill`。`val` は模様なしの clear）。
                    cell_element.get_or_add_tcPr().append(
                        _w("shd", val="clear", color="auto", fill=_HEADER_FILL)
                    )
                    _Cell(
                        cell_element, table
                    ).vertical_alignment = WD_CELL_VERTICAL_ALIGNMENT.CENTER
                elif right[position]:
                    paragraph.alignment = WD_ALIGN_PARAGRAPH.RIGHT
        # 表の後に間隔を置く（続く表とつながらない。文書を表で終えない）。
        self._spacer()

    def page_break(self) -> None:
        if self._break_pending:
            self._spacer()
        self._break_pending = True

    def finish(self) -> bytes:
        if self._break_pending:
            self._spacer()
        buffer = io.BytesIO()
        self.document.save(buffer)
        return buffer.getvalue()


def build_document(content: DocumentContent) -> BuiltDocument:
    """内容から .docx を作る。作れない内容は `DocxSpecError`（何も作らない）。"""
    issues = _structure_issues(content)
    if issues:
        raise DocxSpecError(issues)
    tables = {
        index: _table_texts(index, block, issues)
        for index, block in enumerate(content.blocks)
        if isinstance(block, TableBlock)
    }
    if not issues and _characters(content, tables) > MAX_DOCUMENT_CHARS:
        issues.append(
            SpecIssue(
                "blocks",
                "too_long",
                f"文書の文字の合計は {MAX_DOCUMENT_CHARS} 文字までです。内容を絞ってください。",
            )
        )
    if issues:
        raise DocxSpecError(issues)
    writer = _Writer(_line(content.title))
    writer.title(content.title, content.subtitle)
    counts = dict.fromkeys(BLOCK_TYPES, 0)
    for index, block in enumerate(content.blocks):
        counts[_block_type(block)] += 1
        if isinstance(block, HeadingBlock):
            writer.heading(block)
        elif isinstance(block, ParagraphBlock):
            writer.paragraph(block)
        elif isinstance(block, ListBlock):
            writer.items(block)
        elif isinstance(block, TableBlock):
            writer.table(block, tables[index])
        else:
            writer.page_break()
    return BuiltDocument(
        data=writer.finish(),
        blocks=counts,
        tables=tuple(
            {"rows": len(block.rows), "columns": len(block.columns)}
            for block in content.blocks
            if isinstance(block, TableBlock)
        ),
    )


__all__ = [
    "BLOCK_TYPES",
    "DOCX_MIME_TYPE",
    "FONT_NAME",
    "MAX_BLOCKS",
    "MAX_CELL_CHARS",
    "MAX_DOCUMENT_CHARS",
    "MAX_HEADING_LEVEL",
    "MAX_LIST_ITEMS",
    "MAX_TABLES",
    "MAX_TABLE_CELLS",
    "MAX_TABLE_COLUMNS",
    "MAX_TABLE_ROWS",
    "Block",
    "BuiltDocument",
    "Column",
    "DocumentContent",
    "DocxSpecError",
    "HeadingBlock",
    "ListBlock",
    "ListItem",
    "PageBreakBlock",
    "ParagraphBlock",
    "SpecIssue",
    "TableBlock",
    "build_document",
]
