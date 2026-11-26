"""pptx の生成の本体（#5837）。構造化した内容（スライドの列）から .pptx を作る。

Run・DB・ツールの層に依らない純粋な関数（`build_presentation`）。表の値の出所（Run の表の参照か、
モデルが書いた値か）は呼ぶ側（`pptx_tool`）が解決し、ここには行の値として渡す。

- 作るもの: 6 種類のスライド（表紙・章の扉・要点の箇条書き・表・左右の対比・グラフ）と、スライド
  ごとの発表者ノート。体裁は 1 つに決まっている（16:9・白い背景・1 つの字体・題の下の強調の色の
  細い線・表紙を除くスライドの右下のスライド番号）。既存のファイルの編集、画像・図形・SmartArt、
  アニメーションと画面の切り替え、マスターとテンプレートの選択、文字の装飾、動画・音声、4 つ以外の
  グラフは作らない。
- 収まらない内容は、文字を小さくしたりスライドからはみ出させたりせずに、誤りにする。文字の大きさ・
  行の高さ・枠の大きさを固定し、文字数と項目の数の上限を、上限いっぱいでも枠に収まる値にしている
  （行の高さは字体に依らない固定の値。上限と枠の関係は `tests/test_pptx_presentation.py` が
  確かめる）。
  表は 1 つの cell を 2 行までとし、列の幅に収まらない表は誤りにする。
- 表が 1 枚の行の上限を超えるときは、次のスライドへ続ける（題に「（続き）」を付け、見出しの行を
  毎回置く）。続きを含めた行の上限を超える表は誤りにする（Excel のファイルにすることを促す）。
- グラフは PowerPoint のグラフ（利用者が開いて値・体裁を編集できる。画像にしない）。値は表から取り、
  指定（種類・項目の列・系列の列）の検証は `chart_spec`、描画（種類ごとに決まっている色・凡例・
  データラベル・軸の体裁）は `chart_drawing` がする（どちらもスライドに依らない）。
- 文は装飾の無い文字として入れる（Markdown・HTML を解釈しない）。題・項目・cell の改行は空白にし、
  発表者ノートの改行は段落の区切りにする。
- 表の値は列の型で読み、書式を当てた文字にして入れる（数値・日付の列は右寄せ）。読み方と書式の
  名前は Word の生成（`docx_document`）と同じ（`table_text`・`table_columns`）。
- 誤りは値を含まない場所・種類・直し方（`PptxSpecError.issues`）で返す。場所は `pptx_tool` の引数の
  path（`slides.2.table.columns.1` など。引数の検証の誤りと同じ書き方）。
- python-pptx に無い所（東アジアの字体の指定・箇条書きの記号と字下げ・スライド番号の field・表の
  罫線）は、python-pptx が公開する `OxmlElement` で最小限に書く。
"""

from __future__ import annotations

import io
import math
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from pptx import Presentation
from pptx.dml.color import RGBColor
from pptx.enum.lang import MSO_LANGUAGE_ID
from pptx.enum.shapes import MSO_CONNECTOR, PP_PLACEHOLDER
from pptx.enum.text import MSO_ANCHOR, MSO_AUTO_SIZE, PP_ALIGN
from pptx.opc.constants import RELATIONSHIP_TYPE
from pptx.opc.oxml import serialize_part_xml
from pptx.oxml import parse_xml
from pptx.oxml.ns import qn
from pptx.oxml.xmlchemy import OxmlElement
from pptx.util import Pt

from app.features.agent.chart_drawing import (
    CHART_KINDS,
    apply_font,
    chart_values,
    chart_workbook,
    style_chart,
)
from app.features.agent.chart_spec import (
    ChartData,
    ChartSpec,
    chart_data,
    chart_data_issues,
    chart_issues,
)
from app.features.agent.table_columns import SpecIssue, display_width, format_issue
from app.features.agent.table_text import Column, clean_text, column_texts, column_values

PPTX_MIME_TYPE = "application/vnd.openxmlformats-officedocument.presentationml.presentation"

# スライドの種類の名前（`pptx_tool` の引数の `type` と、結果の要約の key）。
SLIDE_TYPES = ("cover", "section", "bullets", "table", "comparison", "chart")

# 件数と文字数の上限（超えたら誤り）。スライドの数は、表の続きのスライドを含めて数える。
MAX_SLIDES = 50
TITLE_MAX_CHARS = 60
SUBTITLE_MAX_CHARS = 100
# 箇条書きの項目は、1 段下の項目を含めて数える。
MAX_BULLET_ITEMS = 8
BULLET_ITEM_MAX_CHARS = 100
MAX_COMPARISON_ITEMS = 6
COMPARISON_ITEM_MAX_CHARS = 40
COMPARISON_HEADING_MAX_CHARS = 24
MAX_TABLE_COLUMNS = 8
MAX_TABLE_ROWS_PER_SLIDE = 12
MAX_TABLE_ROWS = 60
NOTES_MAX_CHARS = 2_000
# 表が次のスライドへ続くときに、題の後ろに付ける文字。
CONTINUED_SUFFIX = "（続き）"

# ---- 固定の体裁（1 つ）。長さは pt（1 pt = 12700 EMU） ------------------------------------------
# 16:9 のスライドと、左右の余白。
SLIDE_WIDTH_PT = 960.0
SLIDE_HEIGHT_PT = 540.0
MARGIN_X_PT = 44.0
CONTENT_WIDTH_PT = SLIDE_WIDTH_PT - 2 * MARGIN_X_PT
# 字体は日本語の資料で一般的な 1 つ（日本語・英数字の両方。埋め込まない。無い環境では表示する側の
# 代わりの字体になる）。箇条書きの記号だけは、記号の形が決まっている字体にする。
FONT_NAME = "游ゴシック"
_BULLET_FONT = "Arial"
_BULLET_CHARS = ("•", "–")
# 色は、文字の濃い色・副題とスライド番号の灰色・強調の色（題の下の線）。
_TEXT = RGBColor(0x26, 0x26, 0x26)
_MUTED = RGBColor(0x59, 0x59, 0x59)
ACCENT = RGBColor(0x2A, 0x78, 0xD6)
_WHITE = RGBColor(0xFF, 0xFF, 0xFF)
_HEADER_FILL = RGBColor(0xF2, 0xF2, 0xF2)
_RULE = "BFBFBF"
# 題（表紙と章の扉を除くスライド）: 2 行ぶんの枠の下に寄せ、その下に強調の色の線を引く。
TITLE_PT = 24.0
TITLE_LINE_PT = 30.0
TITLE_LINES = 2
_TITLE_TOP_PT = 28.0
_ACCENT_GAP_PT = 6.0
_ACCENT_WIDTH_PT = 1.5
# 表紙と章の扉の題: 3 行ぶんの枠。表紙は副題（3 行ぶん）を線の下に置く。
COVER_TITLE_PT = 32.0
COVER_TITLE_LINE_PT = 40.0
COVER_TITLE_LINES = 3
SUBTITLE_PT = 18.0
SUBTITLE_LINE_PT = 24.0
SUBTITLE_LINES = 3
_COVER_TITLE_TOP_PT = 150.0
_SECTION_TITLE_TOP_PT = 190.0
_SUBTITLE_GAP_PT = 14.0
# 本文の枠（題の線の下から、スライド番号の上まで）。
BODY_TOP_PT = 110.0
BODY_HEIGHT_PT = 382.0
# 箇条書き: 字の大きさ（1 段目・2 段目）、行の高さ、項目の後の間隔、1 段の字下げ。
BULLET_PT = (16.0, 14.0)
BULLET_LINE_PT = 20.0
BULLET_AFTER_PT = 4.0
BULLET_INDENT_PT = 22.0
# 対比: 左右の列の間隔と、列の小見出しの後の間隔（小見出しは 1 段目と同じ大きさの太字で 1 行）。
COMPARISON_GAP_PT = 28.0
COMPARISON_HEADING_AFTER_PT = 6.0
# 表: 行の高さ（1 つの cell に 2 行まで）、字の大きさ、行の高さ、cell の余白。
TABLE_ROW_PT = 29.0
TABLE_PT = 10.5
TABLE_LINE_PT = 12.5
TABLE_CELL_LINES = 2
_CELL_MARGIN_X_PT = 6.0
_CELL_MARGIN_Y_PT = 1.5
_RULE_WIDTH_PT = 0.5
# 表の列の幅の見積もり: 半角 1 文字の幅（字の大きさに対する比。全角は 2 文字ぶん）、1 列の表示の
# 幅の下限（半角の文字数）、折り返しの位置のずれに備える余り（半角の文字数）。
_HALF_WIDTH_EM = 0.6
_COLUMN_UNITS_MIN = 4
_WRAP_SLACK_UNITS = 2
# スライド番号（右下）と、発表者ノートの字の大きさ。
_NUMBER_PT = 10.5
_NUMBER_TOP_PT = 504.0
_NUMBER_HEIGHT_PT = 16.0
_NUMBER_WIDTH_PT = 80.0
_NOTES_PT = 12.0
# スライド番号の field の ID（field の種類ごとの値。スライドごとに変えない）。
_SLIDE_NUMBER_FIELD_ID = "{7A6D4F0E-2C55-4B5E-9E0B-5C1F3D2A8B41}"
# 表の体裁を引数ではなく cell ごとに決めるための、何も当てない表の style（PowerPoint の組み込み）。
_PLAIN_TABLE_STYLE_ID = "{2D5ABB26-0587-4C30-8999-92F81FD0307C}"
# python-pptx の既定のテンプレートの layout（位置で選ぶ。題と本文の placeholder の有無が違う）。
_LAYOUT_COVER = 0
_LAYOUT_TITLE_AND_BODY = 1
_LAYOUT_TWO_BODIES = 3
_LAYOUT_TITLE_ONLY = 5
_TEMPLATE_SLIDE_WIDTH_EMU = 9_144_000

_TOO_LARGE_FIX = "行・列を絞るか、Excel のファイルにしてください。"


@dataclass(frozen=True, slots=True)
class BulletItem:
    text: str
    # 1 段下の項目。
    children: Sequence[str] = ()


@dataclass(frozen=True, slots=True)
class CoverSlide:
    title: str
    subtitle: str | None = None
    notes: str | None = None


@dataclass(frozen=True, slots=True)
class SectionSlide:
    title: str
    notes: str | None = None


@dataclass(frozen=True, slots=True)
class BulletsSlide:
    title: str
    items: Sequence[BulletItem]
    notes: str | None = None


@dataclass(frozen=True, slots=True)
class TableSlide:
    title: str
    columns: Sequence[Column]
    # 行ごとの値（列の順。None は空の cell）。
    rows: Sequence[Sequence[Any]]
    notes: str | None = None


@dataclass(frozen=True, slots=True)
class ComparisonColumn:
    items: Sequence[BulletItem]
    # 列の小見出し。
    heading: str | None = None


@dataclass(frozen=True, slots=True)
class ComparisonSlide:
    title: str
    left: ComparisonColumn
    right: ComparisonColumn
    notes: str | None = None


@dataclass(frozen=True, slots=True)
class ChartSlide:
    title: str
    # グラフの値を取る表（スライドには出ない。グラフのデータとしてファイルに入る）。
    columns: Sequence[Column]
    rows: Sequence[Sequence[Any]]
    chart: ChartSpec
    notes: str | None = None


Slide = CoverSlide | SectionSlide | BulletsSlide | TableSlide | ComparisonSlide | ChartSlide


@dataclass(frozen=True, slots=True)
class PresentationContent:
    # ファイルの題（ファイルのプロパティに入る）。
    title: str
    slides: Sequence[Slide]


class PptxSpecError(ValueError):
    """内容が作れる範囲に合わない（`issues` を直して作り直す）。"""

    def __init__(self, issues: Sequence[SpecIssue]) -> None:
        super().__init__("; ".join(f"{item.argument}: {item.message}" for item in issues))
        self.issues = tuple(issues)


@dataclass(frozen=True, slots=True)
class BuiltPresentation:
    data: bytes = field(repr=False)
    # 内容のスライドの、種類ごとの数（`SLIDE_TYPES` の名前。表の続きのスライドは数えない）。
    slides: dict[str, int]
    # ファイルのスライドの数（表の続きのスライドを含む）と、そのうちの続きのスライドの数。
    slide_count: int
    continued_slides: int
    # 表ごとの要約（ファイルに出る順。データの行の数・列の数・使ったスライドの数）。
    tables: tuple[dict[str, int], ...]
    # グラフごとの要約（ファイルに出る順。種類・系列の数・項目の数）。
    charts: tuple[dict[str, Any], ...]


def _line(value: str | None) -> str:
    """1 行の文（改行は 1 つの空白にする。文の中の空白は変えない）。"""
    lines = (line.strip() for line in clean_text(value or "").split("\n"))
    return " ".join(line for line in lines if line)


def _slide_type(slide: Slide) -> str:
    if isinstance(slide, CoverSlide):
        return "cover"
    if isinstance(slide, SectionSlide):
        return "section"
    if isinstance(slide, BulletsSlide):
        return "bullets"
    if isinstance(slide, TableSlide):
        return "table"
    if isinstance(slide, ComparisonSlide):
        return "comparison"
    return "chart"


def _table_slides(rows: int) -> int:
    """表が使うスライドの数（行が無い表も 1 枚）。"""
    return max(1, math.ceil(rows / MAX_TABLE_ROWS_PER_SLIDE))


# ---- 内容の検査 --------------------------------------------------------------------------------


def _text_issues(
    at: str, text: str | None, limit: int, fix: str, issues: list[SpecIssue], *, required: bool
) -> None:
    line = _line(text)
    if required and not line:
        issues.append(SpecIssue(at, "required", "空でない文を書いてください。"))
    elif len(line) > limit:
        issues.append(SpecIssue(at, "too_long", f"{limit} 文字までです。{fix}"))


def _items_issues(
    at: str, items: Sequence[BulletItem], limit: int, chars: int, fix: str, issues: list[SpecIssue]
) -> None:
    """箇条書きの項目（数は 1 段下の項目を含めて数える。`at` は項目の配列の場所）。"""
    count = len(items) + sum(len(item.children) for item in items)
    if not items:
        issues.append(SpecIssue(at, "required", "項目を 1 つ以上書いてください。"))
    if count > limit:
        issues.append(
            SpecIssue(at, "too_many", f"1 段下の項目を含めて {limit} 項目までです。{fix}")
        )
    shorten = "文を短くするか、項目を分けてください。"
    for item_index, item in enumerate(items):
        item_at = f"{at}.{item_index}"
        _text_issues(f"{item_at}.text", item.text, chars, shorten, issues, required=True)
        for child_index, child in enumerate(item.children):
            child_at = f"{item_at}.children.{child_index}"
            _text_issues(child_at, child, chars, shorten, issues, required=True)


def _columns_issues(
    at: str, columns: Sequence[Column], rows: Sequence[Sequence[Any]], issues: list[SpecIssue]
) -> None:
    """表（表のスライドと、グラフの値を取る表）の列の定義と、行の値の数。"""
    width = len(columns)
    if not 1 <= width <= MAX_TABLE_COLUMNS:
        issues.append(
            SpecIssue(
                f"{at}.columns",
                "count",
                f"1 つの表の列は 1〜{MAX_TABLE_COLUMNS} 列にしてください。{_TOO_LARGE_FIX}",
            )
        )
    for row_index, row in enumerate(rows):
        if len(row) != width:
            issues.append(
                SpecIssue(
                    f"{at}.rows.{row_index}",
                    "length",
                    f"行の値の数を列の数（{width}）にそろえてください。",
                )
            )
            break
    for column_index, column in enumerate(columns):
        column_at = f"{at}.columns.{column_index}"
        if not _line(column.header):
            issues.append(
                SpecIssue(f"{column_at}.header", "required", "空でない文を書いてください。")
            )
        issue = format_issue(column_at, column.type, column.format)
        if issue is not None:
            issues.append(issue)


def _structure_issues(content: PresentationContent) -> list[SpecIssue]:
    issues: list[SpecIssue] = []
    if not _line(content.title):
        issues.append(SpecIssue("title", "required", "空でない文を書いてください。"))
    if not content.slides:
        issues.append(SpecIssue("slides", "count", "スライドを 1 枚以上書いてください。"))
    total = 0
    for index, slide in enumerate(content.slides):
        at = f"slides.{index}"
        total += 1
        _text_issues(
            f"{at}.title",
            slide.title,
            TITLE_MAX_CHARS,
            "題を短くし、入りきらない内容は本文か発表者ノートに書いてください。",
            issues,
            required=True,
        )
        _text_issues(
            f"{at}.notes",
            slide.notes,
            NOTES_MAX_CHARS,
            "発表者ノートを短くしてください。",
            issues,
            required=False,
        )
        if isinstance(slide, CoverSlide):
            _text_issues(
                f"{at}.subtitle",
                slide.subtitle,
                SUBTITLE_MAX_CHARS,
                "副題を短くしてください。",
                issues,
                required=False,
            )
        elif isinstance(slide, BulletsSlide):
            _items_issues(
                f"{at}.items",
                slide.items,
                MAX_BULLET_ITEMS,
                BULLET_ITEM_MAX_CHARS,
                "スライドを分けてください。",
                issues,
            )
        elif isinstance(slide, ComparisonSlide):
            for side, column in (("left", slide.left), ("right", slide.right)):
                _text_issues(
                    f"{at}.{side}.heading",
                    column.heading,
                    COMPARISON_HEADING_MAX_CHARS,
                    "小見出しを短くしてください。",
                    issues,
                    required=False,
                )
                _items_issues(
                    f"{at}.{side}.items",
                    column.items,
                    MAX_COMPARISON_ITEMS,
                    COMPARISON_ITEM_MAX_CHARS,
                    "項目を絞るか、スライドを分けてください。",
                    issues,
                )
        elif isinstance(slide, TableSlide):
            _columns_issues(f"{at}.table", slide.columns, slide.rows, issues)
            if len(slide.rows) > MAX_TABLE_ROWS:
                issues.append(
                    SpecIssue(
                        f"{at}.table.rows",
                        "too_many",
                        f"1 つの表の行は、続きのスライドを含めて {MAX_TABLE_ROWS} 行までです。"
                        "行を絞るか、Excel のファイルにしてください。",
                    )
                )
            else:
                total += _table_slides(len(slide.rows)) - 1
        elif isinstance(slide, ChartSlide):
            _columns_issues(f"{at}.table", slide.columns, slide.rows, issues)
            issues.extend(
                chart_issues(
                    f"{at}.chart",
                    slide.chart,
                    [column.type for column in slide.columns],
                    len(slide.rows),
                    rows_at=f"{at}.table.rows",
                )
            )
    if total > MAX_SLIDES:
        issues.append(
            SpecIssue(
                "slides",
                "too_many",
                f"スライドは、表の続きのスライドを含めて {MAX_SLIDES} 枚までです。内容を絞るか、"
                "ファイルを分けてください。",
            )
        )
    return issues


@dataclass(frozen=True, slots=True)
class _TableValues:
    """表の値（列ごと。型で読んだ値と、書式を当てた表示の文字）。"""

    values: list[list[Any]]
    texts: list[list[str]]


def _table_values(
    at: str, columns: Sequence[Column], rows: Sequence[Sequence[Any]], issues: list[SpecIssue]
) -> _TableValues | None:
    """表の値を列の型で読む（読めない値のある列は誤り。1 つでもあれば None）。"""
    values: list[list[Any]] = []
    for position, column in enumerate(columns):
        read = column_values(
            f"{at}.columns.{position}", column, [row[position] for row in rows], issues
        )
        if read is None:
            return None
        values.append(read)
    texts = [
        [_line(text) for text in column_texts(column, read)]
        for column, read in zip(columns, values, strict=True)
    ]
    return _TableValues(values=values, texts=texts)


def _column_widths(
    headers: Sequence[str], texts: Sequence[Sequence[str]], keep: Sequence[bool]
) -> list[float] | None:
    """表の列の幅（pt。合計は本文の幅）。どの cell も決まった行の数に収まる幅を取る。

    文字の幅は、半角 1 文字の幅の見積もりで数える。見出しと文字の列は cell の中で折り返せるので、
    折り返して収まる幅を下限にする。`keep` の列（数値・日付。途中で折り返すと読みにくい）の値は
    1 行に収める。下限の合計が本文の幅を超える表は、収まらない（None）。

    幅が足りないときは、折り返さずに入る幅の狭い列をそのままにし、広い列を同じ幅まで（下限は
    守って）縮める。余るときは、折り返さずに入る幅の比で本文の幅を分ける。
    """
    unit = TABLE_PT * _HALF_WIDTH_EM
    padding = 2 * _CELL_MARGIN_X_PT

    def wrapped(width: int) -> int:
        return min(width, math.ceil(width / TABLE_CELL_LINES) + _WRAP_SLACK_UNITS)

    minimums: list[float] = []
    naturals: list[float] = []
    for header, column, fixed in zip(headers, texts, keep, strict=True):
        head = display_width(header)
        body = max((display_width(text) for text in column), default=0)
        natural = max(head, body, _COLUMN_UNITS_MIN)
        minimum = max(wrapped(head), body if fixed else wrapped(body), _COLUMN_UNITS_MIN)
        naturals.append(padding + unit * natural)
        minimums.append(padding + unit * minimum)
    if sum(minimums) > CONTENT_WIDTH_PT:
        return None

    def capped(level: float) -> list[float]:
        """広い列を `level` の幅まで縮めた幅（下限より狭くしない）。"""
        return [
            min(natural, max(minimum, level))
            for natural, minimum in zip(naturals, minimums, strict=True)
        ]

    # 合計が本文の幅を超えない、いちばん広い `level` を二分で探す。
    low, high = 0.0, max(naturals)
    for _ in range(40):
        level = (low + high) / 2
        if sum(capped(level)) > CONTENT_WIDTH_PT:
            high = level
        else:
            low = level
    widths = capped(high if sum(capped(high)) <= CONTENT_WIDTH_PT else low)
    return [width * CONTENT_WIDTH_PT / sum(widths) for width in widths]


# ---- ファイルの組み立て（python-pptx） ---------------------------------------------------------


class _Deck:
    """スライドを順にファイルへ書く。"""

    def __init__(self, title: str) -> None:
        self.presentation: Any = Presentation()
        presentation = self.presentation
        presentation.slide_width = Pt(SLIDE_WIDTH_PT)
        presentation.slide_height = Pt(SLIDE_HEIGHT_PT)
        for size in presentation.element.iter(qn("p:sldSz")):
            # テンプレートの 4:3 の印を外す（大きさは上で 16:9 にした）。
            size.attrib.pop("type", None)
        self._widen_template()
        self._set_theme_font()
        self._drop_template_leftovers()
        properties = presentation.core_properties
        properties.title = title
        properties.author = ""
        properties.last_modified_by = ""
        properties.comments = ""
        properties.language = "ja-JP"
        properties.created = properties.modified = datetime.now(UTC).replace(tzinfo=None)
        self._number = 0

    def _widen_template(self) -> None:
        """テンプレート（4:3）のマスターと layout の枠を、16:9 の幅に広げる。

        生成するスライドは枠の位置を自分で決めるが、利用者が PowerPoint でスライドを足すときの
        layout が、スライドの幅に合うようにする。
        """
        master = self.presentation.slide_master
        ratio = int(self.presentation.slide_width) / _TEMPLATE_SLIDE_WIDTH_EMU
        for part in (master, *master.slide_layouts):
            for node in part.element.iter(qn("a:off"), qn("a:ext")):
                for name in ("x", "cx"):
                    if node.get(name) is not None:
                        node.set(name, str(round(int(node.get(name)) * ratio)))

    def _set_theme_font(self) -> None:
        """テーマの字体（見出し・本文の、英数字と東アジアの文字）を 1 つの字体にする。

        生成する文字には字体を直接指定するが、利用者が PowerPoint で足す文字も同じ字体になる。
        """
        theme = self.presentation.slide_master.part.part_related_by(RELATIONSHIP_TYPE.THEME)
        root = parse_xml(theme.blob)
        for fonts in root.iter(qn("a:majorFont"), qn("a:minorFont")):
            for tag in ("a:latin", "a:ea"):
                fonts.find(qn(tag)).set("typeface", FONT_NAME)
        theme._blob = serialize_part_xml(root)  # noqa: SLF001 - テーマを書き換える API が無い

    def _drop_template_leftovers(self) -> None:
        """テンプレートに付いている、内容と関係の無い部品（縮小の画像・印刷の設定）を外す。"""
        part = self.presentation.part
        package = part.package
        for relationship in list(package.iter_rels()):
            if relationship.reltype == RELATIONSHIP_TYPE.THUMBNAIL:
                package.drop_rel(relationship.rId)
        for relationship_id, relationship in list(part.rels.items()):
            if relationship.reltype == RELATIONSHIP_TYPE.PRINTER_SETTINGS:
                part.drop_rel(relationship_id)

    # ---- 文字 ----

    def _frame(self, shape: Any, top: float, height: float, anchor: MSO_ANCHOR) -> Any:
        """枠の位置（左右は本文の幅）と、文字の枠の設定（余白なし・折り返す・自動で縮めない）。"""
        return self._frame_at(shape, MARGIN_X_PT, top, CONTENT_WIDTH_PT, height, anchor)

    def _frame_at(
        self, shape: Any, left: float, top: float, width: float, height: float, anchor: MSO_ANCHOR
    ) -> Any:
        shape.left, shape.top, shape.width, shape.height = (
            Pt(left),
            Pt(top),
            Pt(width),
            Pt(height),
        )
        frame = shape.text_frame
        frame.word_wrap = True
        frame.auto_size = MSO_AUTO_SIZE.NONE
        frame.margin_left = frame.margin_right = frame.margin_top = frame.margin_bottom = 0
        frame.vertical_anchor = anchor
        return frame

    def _paragraph(
        self,
        paragraph: Any,
        text: str,
        *,
        size: float,
        line: float,
        bold: bool = False,
        color: RGBColor = _TEXT,
        after: float = 0.0,
        alignment: PP_ALIGN = PP_ALIGN.LEFT,
    ) -> Any:
        """段落に文字を入れる（字の大きさ・固定の行の高さ・前後の間隔・字体・色を直接指定する）。"""
        paragraph.alignment = alignment
        paragraph.line_spacing = Pt(line)
        paragraph.space_before = Pt(0)
        paragraph.space_after = Pt(after)
        run = paragraph.add_run()
        run.text = text
        font = run.font
        font.size = Pt(size)
        font.bold = bold
        font.color.rgb = color
        font.language_id = MSO_LANGUAGE_ID.JAPANESE
        apply_font(font, FONT_NAME)
        return run

    def _bullet(self, paragraph: Any, level: int | None) -> None:
        """段落の字下げと行頭の記号（`level` が None の段落は、記号も字下げも無い）。"""
        # python-pptx に、字下げと行頭の記号の API が無い。
        properties = paragraph._p.get_or_add_pPr()  # noqa: SLF001
        if level is None:
            properties.set("marL", "0")
            properties.set("indent", "0")
            properties.append(OxmlElement("a:buNone"))
            return
        paragraph.level = level
        indent = int(Pt(BULLET_INDENT_PT))
        properties.set("marL", str(indent * (level + 1)))
        properties.set("indent", str(-indent))
        bullet_font = OxmlElement("a:buFont")
        bullet_font.set("typeface", _BULLET_FONT)
        bullet_char = OxmlElement("a:buChar")
        bullet_char.set("char", _BULLET_CHARS[level])
        properties.append(bullet_font)
        properties.append(bullet_char)

    def _items(self, frame: Any, items: Sequence[BulletItem], heading: str | None = None) -> None:
        """文字の枠に、小見出し（あれば）と 2 段までの箇条書きを入れる。"""
        paragraphs = iter([frame.paragraphs[0]])

        def next_paragraph() -> Any:
            return next(paragraphs, None) or frame.add_paragraph()

        if heading:
            paragraph = next_paragraph()
            self._paragraph(
                paragraph,
                heading,
                size=BULLET_PT[0],
                line=BULLET_LINE_PT,
                bold=True,
                after=COMPARISON_HEADING_AFTER_PT,
            )
            self._bullet(paragraph, None)
        for item in items:
            for level, text in ((0, item.text), *((1, child) for child in item.children)):
                paragraph = next_paragraph()
                self._paragraph(
                    paragraph,
                    _line(text),
                    size=BULLET_PT[level],
                    line=BULLET_LINE_PT,
                    after=BULLET_AFTER_PT,
                )
                self._bullet(paragraph, level)

    # ---- スライドの共通の部分 ----

    def _slide(self, layout: int, notes: str | None, *, numbered: bool = True) -> Any:
        slide = self.presentation.slides.add_slide(self.presentation.slide_layouts[layout])
        self._number += 1
        if numbered:
            self._slide_number(slide)
        lines = [line.strip() for line in clean_text(notes or "").split("\n") if line.strip()]
        if lines:
            frame = slide.notes_slide.notes_text_frame
            for index, line in enumerate(lines):
                paragraph = frame.paragraphs[0] if index == 0 else frame.add_paragraph()
                run = paragraph.add_run()
                run.text = line
                run.font.size = Pt(_NOTES_PT)
                run.font.language_id = MSO_LANGUAGE_ID.JAPANESE
                apply_font(run.font, FONT_NAME)
        return slide

    def _slide_number(self, slide: Any) -> None:
        """右下のスライド番号（layout の placeholder を写し、番号の field を入れる）。"""
        source = next(
            shape
            for shape in slide.slide_layout.placeholders
            if shape.placeholder_format.type == PP_PLACEHOLDER.SLIDE_NUMBER
        )
        slide.shapes.clone_placeholder(source)
        shape = slide.placeholders[source.placeholder_format.idx]
        frame = self._frame_at(
            shape,
            SLIDE_WIDTH_PT - MARGIN_X_PT - _NUMBER_WIDTH_PT,
            _NUMBER_TOP_PT,
            _NUMBER_WIDTH_PT,
            _NUMBER_HEIGHT_PT,
            MSO_ANCHOR.MIDDLE,
        )
        run = self._paragraph(
            frame.paragraphs[0],
            str(self._number),
            size=_NUMBER_PT,
            line=_NUMBER_HEIGHT_PT,
            color=_MUTED,
            alignment=PP_ALIGN.RIGHT,
        )
        # python-pptx に field の API が無いので、run を番号の field に書き換える（文字は、
        # field を数え直さない表示のための、今の番号）。
        element = run._r  # noqa: SLF001
        element.tag = qn("a:fld")
        element.set("id", _SLIDE_NUMBER_FIELD_ID)
        element.set("type", "slidenum")

    def _accent(self, slide: Any, top: float) -> None:
        """強調の色の細い線（題の下）。"""
        line = slide.shapes.add_connector(
            MSO_CONNECTOR.STRAIGHT,
            Pt(MARGIN_X_PT),
            Pt(top),
            Pt(MARGIN_X_PT + CONTENT_WIDTH_PT),
            Pt(top),
        )
        line.line.color.rgb = ACCENT
        line.line.width = Pt(_ACCENT_WIDTH_PT)
        for style in line.element.findall(qn("p:style")):
            # テーマの線の体裁（影）を当てない。
            line.element.remove(style)

    def _titled(self, layout: int, title: str, notes: str | None) -> Any:
        """題と、その下の線のあるスライド（表紙と章の扉を除く種類）。"""
        slide = self._slide(layout, notes)
        height = TITLE_LINE_PT * TITLE_LINES
        frame = self._frame(slide.shapes.title, _TITLE_TOP_PT, height, MSO_ANCHOR.BOTTOM)
        self._paragraph(frame.paragraphs[0], title, size=TITLE_PT, line=TITLE_LINE_PT, bold=True)
        self._accent(slide, _TITLE_TOP_PT + height + _ACCENT_GAP_PT)
        return slide

    def _large_title(self, slide: Any, title: str, top: float) -> float:
        """表紙と章の扉の大きい題と、その下の線。線の位置（pt）を返す。"""
        height = COVER_TITLE_LINE_PT * COVER_TITLE_LINES
        frame = self._frame(slide.shapes.title, top, height, MSO_ANCHOR.BOTTOM)
        self._paragraph(
            frame.paragraphs[0],
            title,
            size=COVER_TITLE_PT,
            line=COVER_TITLE_LINE_PT,
            bold=True,
        )
        rule = top + height + _ACCENT_GAP_PT
        self._accent(slide, rule)
        return rule

    # ---- スライドの種類 ----

    def cover(self, slide: CoverSlide) -> None:
        page = self._slide(_LAYOUT_COVER, slide.notes, numbered=False)
        rule = self._large_title(page, _line(slide.title), _COVER_TITLE_TOP_PT)
        placeholder = page.placeholders[1]
        subtitle = _line(slide.subtitle)
        if not subtitle:
            # 使わない placeholder は消す（空の枠を残さない）。
            placeholder.element.getparent().remove(placeholder.element)
            return
        frame = self._frame(
            placeholder,
            rule + _SUBTITLE_GAP_PT,
            SUBTITLE_LINE_PT * SUBTITLE_LINES,
            MSO_ANCHOR.TOP,
        )
        self._paragraph(
            frame.paragraphs[0], subtitle, size=SUBTITLE_PT, line=SUBTITLE_LINE_PT, color=_MUTED
        )

    def section(self, slide: SectionSlide) -> None:
        page = self._slide(_LAYOUT_TITLE_ONLY, slide.notes)
        self._large_title(page, _line(slide.title), _SECTION_TITLE_TOP_PT)

    def bullets(self, slide: BulletsSlide) -> None:
        page = self._titled(_LAYOUT_TITLE_AND_BODY, _line(slide.title), slide.notes)
        frame = self._frame(page.placeholders[1], BODY_TOP_PT, BODY_HEIGHT_PT, MSO_ANCHOR.TOP)
        self._items(frame, slide.items)

    def comparison(self, slide: ComparisonSlide) -> None:
        page = self._titled(_LAYOUT_TWO_BODIES, _line(slide.title), slide.notes)
        width = (CONTENT_WIDTH_PT - COMPARISON_GAP_PT) / 2
        for index, column in enumerate((slide.left, slide.right), 1):
            left = MARGIN_X_PT + (index - 1) * (width + COMPARISON_GAP_PT)
            frame = self._frame_at(
                page.placeholders[index], left, BODY_TOP_PT, width, BODY_HEIGHT_PT, MSO_ANCHOR.TOP
            )
            self._items(frame, column.items, _line(column.heading))

    def table(
        self, slide: TableSlide, texts: Sequence[Sequence[str]], widths: Sequence[float]
    ) -> int:
        """表のスライド（1 枚の行の上限を超える分は、次のスライドへ続ける）。使った枚数を返す。"""
        title = _line(slide.title)
        headers = [_line(column.header) for column in slide.columns]
        right = [column.type in ("number", "date") for column in slide.columns]
        rows = [[column[row] for column in texts] for row in range(len(slide.rows))]
        pages = _table_slides(len(rows))
        for page_index in range(pages):
            start = page_index * MAX_TABLE_ROWS_PER_SLIDE
            page = self._titled(
                _LAYOUT_TITLE_ONLY,
                title if page_index == 0 else title + CONTINUED_SUFFIX,
                # 発表者ノートは、表の最初のスライドにだけ置く。
                slide.notes if page_index == 0 else None,
            )
            self._table(
                page, headers, rows[start : start + MAX_TABLE_ROWS_PER_SLIDE], right, widths
            )
        return pages

    def _table(
        self,
        page: Any,
        headers: Sequence[str],
        rows: Sequence[Sequence[str]],
        right: Sequence[bool],
        widths: Sequence[float],
    ) -> None:
        frame = page.shapes.add_table(
            len(rows) + 1,
            len(headers),
            Pt(MARGIN_X_PT),
            Pt(BODY_TOP_PT),
            Pt(CONTENT_WIDTH_PT),
            Pt(TABLE_ROW_PT * (len(rows) + 1)),
        )
        table = frame.table
        # 体裁は cell ごとに決める（表の style の色・縞を使わない）。
        table.horz_banding = False
        for style in frame.element.iter(qn("a:tableStyleId")):
            style.text = _PLAIN_TABLE_STYLE_ID
        for column, width in zip(table.columns, widths, strict=True):
            column.width = Pt(width)
        for row_index, row in enumerate(table.rows):
            row.height = Pt(TABLE_ROW_PT)
            texts = headers if row_index == 0 else rows[row_index - 1]
            for position, cell in enumerate(row.cells):
                header = row_index == 0
                self._cell_borders(cell)
                cell.fill.solid()
                cell.fill.fore_color.rgb = _HEADER_FILL if header else _WHITE
                cell.margin_left = cell.margin_right = Pt(_CELL_MARGIN_X_PT)
                cell.margin_top = cell.margin_bottom = Pt(_CELL_MARGIN_Y_PT)
                cell.vertical_anchor = MSO_ANCHOR.MIDDLE
                cell.text_frame.word_wrap = True
                alignment = PP_ALIGN.LEFT
                if header:
                    alignment = PP_ALIGN.CENTER
                elif right[position]:
                    alignment = PP_ALIGN.RIGHT
                self._paragraph(
                    cell.text_frame.paragraphs[0],
                    texts[position],
                    size=TABLE_PT,
                    line=TABLE_LINE_PT,
                    bold=header,
                    alignment=alignment,
                )

    def _cell_borders(self, cell: Any) -> None:
        """cell の 4 辺の細い罫線（python-pptx に罫線の API が無い。塗りより前に置く）。"""
        properties = cell._tc.get_or_add_tcPr()  # noqa: SLF001
        for tag in ("a:lnL", "a:lnR", "a:lnT", "a:lnB"):
            line = OxmlElement(tag)
            line.set("w", str(int(Pt(_RULE_WIDTH_PT))))
            fill = OxmlElement("a:solidFill")
            color = OxmlElement("a:srgbClr")
            color.set("val", _RULE)
            fill.append(color)
            line.append(fill)
            properties.append(line)

    def chart(self, slide: ChartSlide, data: ChartData) -> None:
        """グラフのスライド（PowerPoint のグラフ。体裁は `chart_drawing` が決める）。"""
        page = self._titled(_LAYOUT_TITLE_ONLY, _line(slide.title), slide.notes)
        frame = page.shapes.add_chart(
            CHART_KINDS[data.type],
            Pt(MARGIN_X_PT),
            Pt(BODY_TOP_PT),
            Pt(CONTENT_WIDTH_PT),
            Pt(BODY_HEIGHT_PT),
            chart_values(data),
        )
        style_chart(frame.chart, data, font_name=FONT_NAME)
        # 埋め込みのブックは、名前を文字型で書いたものに置き換える（`chart_drawing`）。
        frame.chart.part.chart_workbook.update_from_xlsx_blob(chart_workbook(data))

    def finish(self) -> bytes:
        buffer = io.BytesIO()
        self.presentation.save(buffer)
        return buffer.getvalue()


def build_presentation(content: PresentationContent) -> BuiltPresentation:
    """内容から .pptx を作る。作れない内容は `PptxSpecError`（何も作らない）。"""
    issues = _structure_issues(content)
    if issues:
        raise PptxSpecError(issues)
    tables: dict[int, _TableValues] = {}
    widths: dict[int, list[float]] = {}
    charts: dict[int, ChartData] = {}
    for index, slide in enumerate(content.slides):
        if not isinstance(slide, TableSlide | ChartSlide):
            continue
        at = f"slides.{index}.table"
        read = _table_values(at, slide.columns, slide.rows, issues)
        if read is None:
            continue
        headers = [_line(column.header) for column in slide.columns]
        if isinstance(slide, ChartSlide):
            charts[index] = chart_data(
                slide.chart,
                headers,
                [column.format for column in slide.columns],
                read.texts,
                read.values,
            )
            issues.extend(chart_data_issues(slide.chart, charts[index], columns_at=f"{at}.columns"))
            continue
        tables[index] = read
        fitted = _column_widths(
            headers, read.texts, [column.type in ("number", "date") for column in slide.columns]
        )
        if fitted is None:
            issues.append(
                SpecIssue(
                    f"{at}.columns",
                    "too_wide",
                    "この表は 1 枚のスライドの幅に収まりません（1 つの cell は "
                    f"{TABLE_CELL_LINES} 行まで）。列を絞るか、長い値を短くするか、Excel・Word の"
                    "ファイルにしてください。",
                )
            )
        else:
            widths[index] = fitted
    if issues:
        raise PptxSpecError(issues)
    deck = _Deck(_line(content.title))
    counts = dict.fromkeys(SLIDE_TYPES, 0)
    table_summaries: list[dict[str, int]] = []
    chart_summaries: list[dict[str, Any]] = []
    continued = 0
    for index, slide in enumerate(content.slides):
        counts[_slide_type(slide)] += 1
        if isinstance(slide, CoverSlide):
            deck.cover(slide)
        elif isinstance(slide, SectionSlide):
            deck.section(slide)
        elif isinstance(slide, BulletsSlide):
            deck.bullets(slide)
        elif isinstance(slide, ComparisonSlide):
            deck.comparison(slide)
        elif isinstance(slide, TableSlide):
            pages = deck.table(slide, tables[index].texts, widths[index])
            continued += pages - 1
            table_summaries.append(
                {"rows": len(slide.rows), "columns": len(slide.columns), "slides": pages}
            )
        else:
            data = charts[index]
            deck.chart(slide, data)
            chart_summaries.append(
                {
                    "type": data.type,
                    "series": len(data.series),
                    "categories": len(data.categories),
                }
            )
    return BuiltPresentation(
        data=deck.finish(),
        slides=counts,
        slide_count=len(content.slides) + continued,
        continued_slides=continued,
        tables=tuple(table_summaries),
        charts=tuple(chart_summaries),
    )


__all__ = [
    "ACCENT",
    "BULLET_ITEM_MAX_CHARS",
    "COMPARISON_HEADING_MAX_CHARS",
    "COMPARISON_ITEM_MAX_CHARS",
    "CONTINUED_SUFFIX",
    "FONT_NAME",
    "MAX_BULLET_ITEMS",
    "MAX_COMPARISON_ITEMS",
    "MAX_SLIDES",
    "MAX_TABLE_COLUMNS",
    "MAX_TABLE_ROWS",
    "MAX_TABLE_ROWS_PER_SLIDE",
    "NOTES_MAX_CHARS",
    "PPTX_MIME_TYPE",
    "SLIDE_TYPES",
    "SUBTITLE_MAX_CHARS",
    "TITLE_MAX_CHARS",
    "BuiltPresentation",
    "BulletItem",
    "BulletsSlide",
    "ChartSlide",
    "Column",
    "ComparisonColumn",
    "ComparisonSlide",
    "CoverSlide",
    "PptxSpecError",
    "PresentationContent",
    "SectionSlide",
    "Slide",
    "SpecIssue",
    "TableSlide",
    "build_presentation",
]
