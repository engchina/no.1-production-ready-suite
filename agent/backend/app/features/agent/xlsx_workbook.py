"""xlsx の生成の本体（#5837）。構造化した内容（シート・列・行・集計）から .xlsx のバイト列を作る。

Run・DB・ツールの層に依らない純粋な関数（`build_workbook`）。表の値の出所（Run の表の参照か、
モデルが書いた値か）は呼ぶ側（`xlsx_tool`）が解決し、ここには行の値として渡す。

- 作るもの: 複数のシート、シートごとの題・説明の文と 1 つの表、列ごとの型（文字・数値・日付）と
  書式、列の幅、表の見出しの固定とフィルター、集計の行、表の右のグラフ（1 つのシートに 1 つ）。
  画像・ピボット・マクロ・条件付き書式・既存のファイルの編集は作らない。
- グラフは Excel のグラフ（縦棒・横棒・折れ線・円。画像にしない）で、そのシートの表の cell を
  参照する（集計の行は含めない。利用者が cell の値を変えると、グラフも変わる）。指定（種類・項目の
  列・系列の列）の検証は `chart_spec`、cell の参照と置き場所は `xlsx_chart`、体裁は PowerPoint・
  Word と同じ `chart_drawing`。
- 字体は 1 つ（`FONT_NAME`。ブックの既定の字体・題と見出し・グラフ）。字体は埋め込まない。
- 文字の cell は必ず文字型で書く（`=` `+` `-` `@` で始まる文字を数式として解釈させない）。数式として
  書くのは、ここで組み立てる集計の行だけ（集計は値を埋めずに数式にする）。
- 数値は数値、日付は日付として cell に入れる。数値・日付として読めない値、Excel の精度（15 桁）を
  超える数値は、丸めたり文字にしたりせずに誤りにする（呼ぶ側が列の型を文字に直す）。
- 上限（シート・列・行・cell の合計）を超える内容は、切り捨てずに誤りにする。
- 誤りは値を含まない場所・種類・直し方（`XlsxSpecError.issues`）で返す。場所は `xlsx_tool` の引数の
  path（`sheets.0.columns.1` など。引数の検証の誤りと同じ書き方）。
"""

from __future__ import annotations

import io
import math
import re
from collections.abc import Sequence
from copy import copy
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal
from typing import Any, Literal

from openpyxl import Workbook  # type: ignore[import-untyped]
from openpyxl.cell.cell import ILLEGAL_CHARACTERS_RE, Cell  # type: ignore[import-untyped]
from openpyxl.styles import (  # type: ignore[import-untyped]
    Alignment,
    Border,
    Font,
    PatternFill,
    Side,
)
from openpyxl.styles.fonts import DEFAULT_FONT  # type: ignore[import-untyped]
from openpyxl.utils import get_column_letter  # type: ignore[import-untyped]
from openpyxl.utils.indexed_list import IndexedList  # type: ignore[import-untyped]
from openpyxl.worksheet.worksheet import Worksheet  # type: ignore[import-untyped]

from app.features.agent.chart_spec import (
    ChartData,
    ChartSeries,
    ChartSpec,
    chart_data_issues,
    chart_issues,
)

# 列の型・書式の名前と、形式に依らない値の読み方は Word の生成（`docx_document`）と共通。
from app.features.agent.table_columns import (
    DATE_FORMATS,
    JSON_NUMBER,
    NUMBER_FORMATS,
    TYPE_LABELS,
    ColumnType,
    SpecIssue,
    ValueProblem,
    display_width,
    format_issue,
    iso_date_value,
)
from app.features.agent.table_text import Column as TextColumn
from app.features.agent.table_text import column_texts
from app.features.agent.xlsx_chart import add_sheet_chart

XLSX_MIME_TYPE = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"

# 件数の上限（超えたら誤り）。行はシートごとのデータの行、cell は全シートのデータの cell の合計。
MAX_SHEETS = 10
MAX_COLUMNS = 50
MAX_ROWS = 20_000
MAX_CELLS = 200_000
MAX_NOTES = 5
MAX_SUMMARIES = 5
# Excel の制約（シート名の文字数、1 つの cell の文字数、数値の精度〔15 桁〕、日付の始まりの年）。
SHEET_NAME_MAX_CHARS = 31
MAX_TEXT_CHARS = 32_767
MAX_EXACT_INTEGER = 10**15 - 1
MIN_DATE_YEAR = 1900
# 列の幅（半角の文字数の目安）。指定できる範囲と、内容から決めるときの範囲。
WIDTH_MIN = 4.0
WIDTH_MAX = 100.0
_AUTO_WIDTH_MIN = 8
_AUTO_WIDTH_MAX = 60

SummaryFunction = Literal["sum", "average", "count", "min", "max"]

_TEXT_FORMAT = "@"
_GENERAL_FORMAT = "General"
_COUNT_FORMAT = NUMBER_FORMATS["integer"]
# 集計の種類ごとの、集計できる列の型。
SUMMARY_COLUMN_TYPES: dict[str, frozenset[str]] = {
    "sum": frozenset({"number"}),
    "average": frozenset({"number"}),
    "count": frozenset({"text", "number", "date"}),
    "min": frozenset({"number", "date"}),
    "max": frozenset({"number", "date"}),
}

_INTEGER = re.compile(r"-?[0-9]+\Z")
_SHEET_NAME_FORBIDDEN = re.compile(r"[\[\]:*?/\\]")

# 字体（ブックの既定の字体・題と見出し・グラフ。Word・PowerPoint の生成と同じ名前）。
FONT_NAME = "游ゴシック"
# 日本語の字体の文字セット（Shift_JIS。字体が無い環境が、代わりの字体を選ぶ手がかりにする）。
_FONT_CHARSET = 128
_FONT_PT = 11
# 固定の書式（1 つ）: 題は太字の大きい字、表の見出しは太字・薄い塗り・下線、集計の行は太字・上線。
_HEADING_FONT = Font(name=FONT_NAME, charset=_FONT_CHARSET, size=14, bold=True)
_BOLD_FONT = Font(name=FONT_NAME, charset=_FONT_CHARSET, size=_FONT_PT, bold=True)
_HEADER_FILL = PatternFill(fill_type="solid", start_color="E7E6E6", end_color="E7E6E6")
_THIN = Side(style="thin", color="7F7F7F")
_HEADER_BORDER = Border(bottom=_THIN)
_SUMMARY_BORDER = Border(top=_THIN)
_HEADER_ALIGNMENT = Alignment(horizontal="center", vertical="center")


@dataclass(frozen=True, slots=True)
class Column:
    header: str
    type: ColumnType
    # 書式の名前（数値は `NUMBER_FORMATS`、日付は `DATE_FORMATS` の key）。None は既定。
    format: str | None = None
    # 列の幅。None は内容から決める。
    width: float | None = None


@dataclass(frozen=True, slots=True)
class Summary:
    function: SummaryFunction
    # 集計する列（1 始まりの位置）。
    columns: tuple[int, ...]
    # 行の見出し（集計しない最初の列に置く）。
    label: str | None = None


@dataclass(frozen=True, slots=True)
class Sheet:
    name: str
    columns: Sequence[Column]
    # 行ごとの値（列の順。None は空の cell）。
    rows: Sequence[Sequence[Any]]
    heading: str | None = None
    notes: Sequence[str] = ()
    summaries: Sequence[Summary] = ()
    # 表の右に置くグラフ（この表のどの列を項目・系列にするか）。None は置かない。
    chart: ChartSpec | None = None


class XlsxSpecError(ValueError):
    """内容が作れる範囲に合わない（`issues` を直して作り直す）。"""

    def __init__(self, issues: Sequence[SpecIssue]) -> None:
        super().__init__("; ".join(f"{item.argument}: {item.message}" for item in issues))
        self.issues = tuple(issues)


@dataclass(frozen=True, slots=True)
class BuiltWorkbook:
    data: bytes = field(repr=False)
    # シートごとの要約（`name` は Excel の制約に合わせた後の名前）。
    sheets: tuple[dict[str, Any], ...]
    # グラフごとの要約（置いたシートの名前・種類・系列の数・項目の数。シートの順）。
    charts: tuple[dict[str, Any], ...] = ()


def clean_text(value: str) -> str:
    """cell に書けない制御文字を除く（XML に入れられない文字）。"""
    return str(ILLEGAL_CHARACTERS_RE.sub("", value))


def sheet_titles(names: Sequence[str]) -> list[str]:
    """シート名を Excel の制約に合わせる（禁止の文字は `_`、31 文字まで、重複は連番）。"""
    titles: list[str] = []
    used: set[str] = set()
    for index, raw in enumerate(names, start=1):
        text = " ".join(_SHEET_NAME_FORBIDDEN.sub("_", clean_text(raw)).split())
        base = text.strip("'")[:SHEET_NAME_MAX_CHARS].strip().strip("'") or f"Sheet{index}"
        title = base
        number = 2
        while title.casefold() in used:
            suffix = f" ({number})"
            title = base[: SHEET_NAME_MAX_CHARS - len(suffix)].rstrip() + suffix
            number += 1
        used.add(title.casefold())
        titles.append(title)
    return titles


def _text_value(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, bool):
        text = "true" if value else "false"
    else:
        text = clean_text(value if isinstance(value, str) else str(value))
    if len(text) > MAX_TEXT_CHARS:
        raise ValueProblem(
            "too_long",
            f"文字が長すぎます（1 つの cell は {MAX_TEXT_CHARS} 文字まで）。",
            "値を短くしてください。",
        )
    return text or None


def _number_value(value: Any) -> int | float | None:
    if value is None:
        return None
    number: Any = value
    if isinstance(value, str):
        text = value.strip()
        if JSON_NUMBER.fullmatch(text) is None:
            raise ValueProblem("not_number", "数値として読めません。")
        if _INTEGER.fullmatch(text):
            number = int(text)
        else:
            number = float(text)
            if math.isfinite(number) and Decimal(repr(number)) != Decimal(text):
                raise ValueProblem("precision", "桁が多く、Excel の数値では値が変わります。")
    if isinstance(number, bool) or not isinstance(number, int | float):
        raise ValueProblem("not_number", "数値として読めません。")
    if isinstance(number, float) and not math.isfinite(number):
        raise ValueProblem("not_number", "数値として読めません。")
    if isinstance(number, int) and abs(number) > MAX_EXACT_INTEGER:
        raise ValueProblem("precision", "桁が多く、Excel の数値では値が変わります。")
    return number


def _date_value(value: Any) -> date | datetime | None:
    parsed = iso_date_value(value)
    if parsed is not None and parsed.year < MIN_DATE_YEAR:
        raise ValueProblem(
            "not_date", f"{MIN_DATE_YEAR} 年より前の日付は Excel の日付にできません。"
        )
    return parsed


_CONVERTERS = {"text": _text_value, "number": _number_value, "date": _date_value}


def _shown(value: Any, number_format: str) -> str:
    """列の幅を決めるための、cell の表示の文字。"""
    if isinstance(value, str):
        return value
    if isinstance(value, date):
        return number_format
    decimals = {"#,##0": 0, "#,##0.0": 1, "#,##0.00": 2}.get(number_format)
    if decimals is not None:
        return f"{value:,.{decimals}f}"
    if number_format.endswith("%"):
        return f"{value * 100:.{1 if '.' in number_format else 0}f}%"
    return str(value)


def _structure_issues(sheets: Sequence[Sheet]) -> list[SpecIssue]:
    issues: list[SpecIssue] = []
    if not 1 <= len(sheets) <= MAX_SHEETS:
        issues.append(SpecIssue("sheets", "count", f"シートは 1〜{MAX_SHEETS} 枚にしてください。"))
    cells = 0
    for index, sheet in enumerate(sheets):
        at = f"sheets.{index}"
        width = len(sheet.columns)
        if not 1 <= width <= MAX_COLUMNS:
            issues.append(
                SpecIssue(f"{at}.columns", "count", f"列は 1〜{MAX_COLUMNS} 列にしてください。")
            )
        if len(sheet.rows) > MAX_ROWS:
            issues.append(
                SpecIssue(
                    f"{at}.rows",
                    "too_many",
                    f"1 つのシートの行は {MAX_ROWS} 行までです。行を絞ってください。",
                )
            )
        if len(sheet.notes) > MAX_NOTES:
            issues.append(
                SpecIssue(f"{at}.notes", "too_many", f"説明の文は {MAX_NOTES} 行までです。")
            )
        if len(sheet.summaries) > MAX_SUMMARIES:
            issues.append(
                SpecIssue(f"{at}.summaries", "too_many", f"集計の行は {MAX_SUMMARIES} 行までです。")
            )
        cells += width * len(sheet.rows)
        for row_index, row in enumerate(sheet.rows):
            if len(row) != width:
                issues.append(
                    SpecIssue(
                        f"{at}.rows.{row_index}",
                        "length",
                        f"行の値の数を列の数（{width}）にそろえてください。",
                    )
                )
                break
        for column_index, column in enumerate(sheet.columns):
            issues.extend(_column_issues(f"{at}.columns.{column_index}", column))
        for summary_index, summary in enumerate(sheet.summaries):
            issues.extend(_summary_issues(f"{at}.summaries.{summary_index}", sheet, summary))
        if sheet.chart is not None:
            issues.extend(
                chart_issues(
                    f"{at}.chart",
                    sheet.chart,
                    [column.type for column in sheet.columns],
                    len(sheet.rows),
                    rows_at=f"{at}.rows",
                )
            )
    if cells > MAX_CELLS:
        issues.append(
            SpecIssue(
                "sheets",
                "too_many_cells",
                f"cell の合計（行 × 列）は {MAX_CELLS} までです。行・列を絞ってください。",
            )
        )
    return issues


def _column_issues(at: str, column: Column) -> list[SpecIssue]:
    issues: list[SpecIssue] = []
    issue = format_issue(at, column.type, column.format)
    if issue is not None:
        issues.append(issue)
    if column.width is not None and not WIDTH_MIN <= column.width <= WIDTH_MAX:
        issues.append(
            SpecIssue(f"{at}.width", "range", f"幅は {WIDTH_MIN:g}〜{WIDTH_MAX:g} にしてください。")
        )
    return issues


def _summary_issues(at: str, sheet: Sheet, summary: Summary) -> list[SpecIssue]:
    width = len(sheet.columns)
    positions = summary.columns
    if (
        not positions
        or len(set(positions)) != len(positions)
        or any(not 1 <= position <= width for position in positions)
    ):
        return [
            SpecIssue(
                f"{at}.columns",
                "range",
                f"集計する列は、1〜{width} の列の番号を重複なく 1 つ以上書いてください。",
            )
        ]
    issues: list[SpecIssue] = []
    if not sheet.rows:
        issues.append(
            SpecIssue(
                at, "no_rows", "行の無い表には集計の行を付けられません。集計を外してください。"
            )
        )
    allowed = SUMMARY_COLUMN_TYPES[summary.function]
    for position in positions:
        column_type = sheet.columns[position - 1].type
        if column_type not in allowed:
            issues.append(
                SpecIssue(
                    f"{at}.columns",
                    "type",
                    f"{summary.function} は{TYPE_LABELS[column_type]}の列（{position} 列目）に"
                    "使えません。",
                )
            )
    if summary.label and len(positions) == width:
        issues.append(
            SpecIssue(
                f"{at}.label",
                "no_room",
                "すべての列を集計する行には見出しを置けません。label を null にしてください。",
            )
        )
    return issues


def _converted_rows(
    index: int, sheet: Sheet, issues: list[SpecIssue]
) -> list[list[str | int | float | date | datetime | None]]:
    """行の値を列の型の値にする（読めない値は、列ごとに最初の 1 件を誤りにする）。"""
    converters = [_CONVERTERS[column.type] for column in sheet.columns]
    failed: set[int] = set()
    converted: list[list[str | int | float | date | datetime | None]] = []
    for row_index, row in enumerate(sheet.rows):
        values: list[str | int | float | date | datetime | None] = []
        for column_index, (convert, value) in enumerate(zip(converters, row, strict=True)):
            if column_index in failed:
                values.append(None)
                continue
            try:
                values.append(convert(value))
            except ValueProblem as problem:
                failed.add(column_index)
                values.append(None)
                issues.append(
                    SpecIssue(
                        f"sheets.{index}.columns.{column_index}.type",
                        problem.code,
                        f"この列の {row_index + 1} 行目の値は、{problem.message}{problem.fix}",
                    )
                )
        converted.append(values)
    return converted


def _chart_data(
    spec: ChartSpec,
    sheet: Sheet,
    rows: Sequence[Sequence[str | int | float | date | datetime | None]],
) -> ChartData:
    """グラフに描く値（cell に書く値と同じもの）。`chart_issues` が通った指定で呼ぶ。"""

    def cells(position: int) -> list[Any]:
        return [row[position - 1] for row in rows]

    category = sheet.columns[spec.category_column - 1]
    names = cells(spec.category_column)
    # 項目の名前は、cell の表示の文字（数値・日付の列は、書式を当てた文字）。
    if category.type == "date":
        labels = column_texts(TextColumn(category.header, "date", category.format), names)
    else:
        code = _number_format(category, names)
        labels = ["" if name is None else _shown(name, code) for name in names]
    return ChartData(
        type=spec.type,
        categories=tuple(labels),
        series=tuple(
            ChartSeries(
                name=clean_text(sheet.columns[position - 1].header),
                # float は、書いた桁のままの 10 進の値にする（2 進の誤差を広げない）。
                values=tuple(
                    None if cell is None else Decimal(repr(cell)) for cell in cells(position)
                ),
                format=sheet.columns[position - 1].format,
            )
            for position in spec.series_columns
        ),
    )


def _number_format(column: Column, values: Sequence[Any]) -> str:
    if column.type == "text":
        return _TEXT_FORMAT
    if column.type == "number":
        return NUMBER_FORMATS[column.format] if column.format else _GENERAL_FORMAT
    if column.format:
        return DATE_FORMATS[column.format]
    # 既定は内容から決める（時刻のある値が 1 つでもあれば日時）。
    has_time = any(isinstance(value, datetime) for value in values)
    return DATE_FORMATS["datetime" if has_time else "date"]


def _write_text(cell: Cell, text: str) -> None:
    # 文字型を明示する（openpyxl は `=` で始まる文字を数式、`#N/A` などをエラー値の型にする）。
    cell.value = text
    cell.data_type = "s"


def _summary_formula(function: str, cell_range: str) -> str:
    if function == "sum":
        return f"=SUM({cell_range})"
    if function == "count":
        return f"=COUNTA({cell_range})"
    # 数値・日付の無い範囲では、エラー・0 を出さずに空にする。
    name = {"average": "AVERAGE", "min": "MIN", "max": "MAX"}[function]
    return f'=IF(COUNT({cell_range})=0,"",{name}({cell_range}))'


def _write_sheet(
    worksheet: Worksheet,
    sheet: Sheet,
    rows: Sequence[Sequence[str | int | float | date | datetime | None]],
    chart: ChartData | None,
) -> None:
    row_number = 1
    if sheet.heading:
        cell = worksheet.cell(row=row_number, column=1)
        _write_text(cell, clean_text(sheet.heading))
        cell.font = _HEADING_FONT
        row_number += 1
    for note in sheet.notes:
        _write_text(worksheet.cell(row=row_number, column=1), clean_text(note))
        row_number += 1
    if row_number > 1:
        row_number += 1
    header_row = row_number
    first_data_row = header_row + 1
    last_data_row = header_row + len(rows)
    formats = [
        _number_format(column, [row[position] for row in rows])
        for position, column in enumerate(sheet.columns)
    ]
    widths = [0] * len(sheet.columns)
    for position, column in enumerate(sheet.columns):
        header = clean_text(column.header)
        cell = worksheet.cell(row=header_row, column=position + 1)
        _write_text(cell, header)
        cell.font = _BOLD_FONT
        cell.fill = _HEADER_FILL
        cell.border = _HEADER_BORDER
        cell.alignment = _HEADER_ALIGNMENT
        # フィルターのボタンの分を足す。
        widths[position] = display_width(header) + 2
    for offset, row in enumerate(rows):
        for position, value in enumerate(row):
            if value is None:
                continue
            cell = worksheet.cell(row=first_data_row + offset, column=position + 1)
            if isinstance(value, str):
                _write_text(cell, value)
            else:
                cell.value = value
            cell.number_format = formats[position]
            widths[position] = max(
                widths[position], display_width(_shown(value, formats[position]))
            )
    for offset, summary in enumerate(sheet.summaries):
        summary_row = last_data_row + 1 + offset
        for position in range(len(sheet.columns)):
            cell = worksheet.cell(row=summary_row, column=position + 1)
            cell.font = _BOLD_FONT
            cell.border = _SUMMARY_BORDER
            if position + 1 in summary.columns:
                letter = get_column_letter(position + 1)
                cell.value = _summary_formula(
                    summary.function, f"{letter}{first_data_row}:{letter}{last_data_row}"
                )
                cell.number_format = (
                    _COUNT_FORMAT if summary.function == "count" else formats[position]
                )
        if summary.label:
            label_position = next(
                position
                for position in range(len(sheet.columns))
                if position + 1 not in summary.columns
            )
            label = clean_text(summary.label)
            _write_text(worksheet.cell(row=summary_row, column=label_position + 1), label)
            widths[label_position] = max(widths[label_position], display_width(label))
    for position, column in enumerate(sheet.columns):
        worksheet.column_dimensions[get_column_letter(position + 1)].width = (
            column.width
            if column.width is not None
            else min(max(widths[position] + 2, _AUTO_WIDTH_MIN), _AUTO_WIDTH_MAX)
        )
    # 表の見出しまでを固定し、見出しとデータの行（集計の行は含めない）にフィルターを付ける。
    worksheet.freeze_panes = worksheet.cell(row=first_data_row, column=1)
    last_column = get_column_letter(len(sheet.columns))
    worksheet.auto_filter.ref = f"A{header_row}:{last_column}{max(last_data_row, header_row)}"
    if sheet.chart is not None and chart is not None:
        # グラフの範囲はデータの行だけ（集計の行は含めない）。
        add_sheet_chart(
            worksheet,
            sheet.chart,
            chart,
            header_row=header_row,
            first_row=first_data_row,
            last_row=last_data_row,
            columns=len(sheet.columns),
            font_name=FONT_NAME,
        )


def _new_workbook() -> Workbook:
    """シートの無いブック。既定の字体（字体を指定しない cell と、標準のスタイル）を決める。"""
    workbook = Workbook()
    workbook.remove(workbook.active)
    # テーマの字体に従う印（scheme）を外す（付いていると、Excel は名前よりテーマの字体を使う）。
    font = copy(DEFAULT_FONT)
    font.name = FONT_NAME
    font.charset = _FONT_CHARSET
    font.size = _FONT_PT
    font.scheme = None
    # openpyxl に、ブックの既定の字体を変える公開の API が無い。字体の一覧の先頭が既定の字体で、
    # 標準のスタイルも同じ字体にする。
    workbook._fonts = IndexedList([font])  # noqa: SLF001
    workbook._named_styles["Normal"].font = font  # noqa: SLF001
    return workbook


def build_workbook(sheets: Sequence[Sheet]) -> BuiltWorkbook:
    """内容から .xlsx を作る。作れない内容は `XlsxSpecError`（何も作らない）。"""
    issues = _structure_issues(sheets)
    if issues:
        raise XlsxSpecError(issues)
    converted = [_converted_rows(index, sheet, issues) for index, sheet in enumerate(sheets)]
    if issues:
        raise XlsxSpecError(issues)
    charts = [
        None if sheet.chart is None else _chart_data(sheet.chart, sheet, rows)
        for sheet, rows in zip(sheets, converted, strict=True)
    ]
    for index, (sheet, chart) in enumerate(zip(sheets, charts, strict=True)):
        if sheet.chart is not None and chart is not None:
            issues.extend(
                chart_data_issues(sheet.chart, chart, columns_at=f"sheets.{index}.columns")
            )
    if issues:
        raise XlsxSpecError(issues)
    titles = sheet_titles([sheet.name for sheet in sheets])
    workbook = _new_workbook()
    for title, sheet, rows, chart in zip(titles, sheets, converted, charts, strict=True):
        _write_sheet(workbook.create_sheet(title=title), sheet, rows, chart)
    buffer = io.BytesIO()
    workbook.save(buffer)
    return BuiltWorkbook(
        data=buffer.getvalue(),
        sheets=tuple(
            {
                "name": title,
                "rows": len(sheet.rows),
                "columns": len(sheet.columns),
                "summary_rows": len(sheet.summaries),
            }
            for title, sheet in zip(titles, sheets, strict=True)
        ),
        charts=tuple(
            {
                "sheet": title,
                "type": chart.type,
                "series": len(chart.series),
                "categories": len(chart.categories),
            }
            for title, chart in zip(titles, charts, strict=True)
            if chart is not None
        ),
    )


__all__ = [
    "DATE_FORMATS",
    "FONT_NAME",
    "MAX_CELLS",
    "MAX_COLUMNS",
    "MAX_NOTES",
    "MAX_ROWS",
    "MAX_SHEETS",
    "MAX_SUMMARIES",
    "NUMBER_FORMATS",
    "SHEET_NAME_MAX_CHARS",
    "SUMMARY_COLUMN_TYPES",
    "WIDTH_MAX",
    "WIDTH_MIN",
    "XLSX_MIME_TYPE",
    "BuiltWorkbook",
    "Column",
    "Sheet",
    "SpecIssue",
    "Summary",
    "XlsxSpecError",
    "build_workbook",
    "sheet_titles",
]
