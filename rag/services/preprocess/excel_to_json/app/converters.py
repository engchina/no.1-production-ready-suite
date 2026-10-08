"""Excel(.xls/.xlsx)→行の記録 JSON 前処理マイクロサービスの変換実装（#1221）。

シートごとに「表頭・元の行番号・セル範囲・表示の値」を持つ JSON（`rag_parser_core.sheet_records`）
を作る。parser のサービスはこの JSON の記録の単位（block）を 1 つずつ要素にし、検索の根拠に
シートと行の場所が残る。読み方は engchina/no.1-rag の前処理（`utils/office_preprocess_util.py`）
に合わせる。

- シートの読み方（`mode`）: `table` は 1 行 1 block。`procedure` は手順書（番号の列と題名の列）
  を、番号と題名のある行から次の手順までの複数行で 1 block にし、詳細の無い題名の行は章にする。
  `auto` は番号と題名の列があれば procedure、無ければデータの行の埋まり方（中央値 60% 以上）で
  table / procedure を決める。

- `.xlsx` は openpyxl で読む（結合セルと表示の書式を読むため read_only にしない）。数式は
  キャッシュ値を読み、キャッシュの無い数式のセルは空にせず数式の文字列にして診断を残す。マクロは
  実行しない。
- `.xls` は xlrd で読む（書式・結合を読むため formatting_info を使う）。日付は日付、真偽は
  TRUE/FALSE。
- 表頭は選択肢の `header_row` を優先し、無ければ先頭の行から採点して選ぶ（信頼度と理由を残し、
  低いときは警告）。表頭より上の行（説明など）は preamble として残す。
- 空行を消す前に元の行番号を持つ。結合セルは、表頭の中では結合の範囲を埋め、データでは同じ行の
  横の結合だけを埋める（縦の結合の値を後の行へ漏らさない。手順の境目を増やさない）。普通の空のセルは
  埋めない。既定は表示されているシートだけを読み、選択肢でシートの指定・除外と列の除外ができる。
- 列の役割（#1281）: 設定値の表の列を、表頭の語（既定値・記入例・現在値・推奨値・設定範囲・
  説明 など。英語の語も）で決め、`SheetColumn.role` に残す。parser は値の列の本文に役割の表示を
  付ける。語が完全に一致すれば信頼度 1.0、表頭の末尾が語なら 0.8。0.75 未満・複数の役割に当たる・
  意味が資料ごとに違う語（「設定値」「値」）の列には付けず、診断を残す（推測しない）。表頭の推定の
  信頼度が低いシート・役割の列が 1 つだけのシートでは語の判定を使わない。選択肢の `column_roles`
  （列名か列の記号 → 役割）は判定より優先する。
- 形式判定は magic bytes(ZIP=xlsx / OLE2=xls)優先、失敗時は拡張子フォールバック。
- 未対応・依存欠如・解析失敗・空のときは passthrough(変換せず原本を使う)へ縮退する。
"""

from __future__ import annotations

import datetime as _dt
import io
import re
import unicodedata
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any, cast

from pydantic import ValidationError
from rag_parser_core.preprocess import ConvertOutcome
from rag_parser_core.sheet_records import (
    COLUMN_ROLE_NONE,
    SHEET_RECORDS_CONTENT_TYPE,
    ColumnRole,
    ExcelOptions,
    ExcelRange,
    HeaderDetection,
    SheetBlock,
    SheetColumn,
    SheetDiagnostic,
    SheetPreambleRow,
    SheetRecords,
    SheetRecordsDocument,
    SkippedSheet,
    column_display_name,
)
from rag_parser_core.source import SourceProfile

CONVERTER_NAME = "excel_to_json"
CONVERTER_VERSION = "v3"
# xlsx は ZIP(PK\x03\x04)、xls は OLE2 複合ドキュメント(D0CF11E0...)。
_XLSX_MAGIC = b"PK\x03\x04"
_XLS_MAGIC = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"
# 表頭を探す範囲（先頭の空でない行の数）と、表頭の後の行の一致を見る数。
_HEADER_SCAN_ROWS = 30
_HEADER_LOOKAHEAD_ROWS = 5
# これより低い信頼度の表頭は警告にする（黙って推測しない）。
HEADER_LOW_CONFIDENCE = 0.6
# 手順書の表頭に多い語（表頭の行の採点で加点する。no.1-rag と同じ）。
PROCEDURE_HEADER_HINTS = ("作業項目", "作業内容", "コマンド", "確認ポイント", "確認条件", "備考")
# auto で table にする、データの行の埋まり方（中央値）の下限と、見る行の数。
TABLE_DENSITY_THRESHOLD = 0.6
_DENSITY_SAMPLE_ROWS = 50
# 列の役割（#1281）を決める表頭の語（正規化した形。`_normalize_header` を参照）。
# 語の選び方: 設定値の表で意味がほぼ 1 つに決まる語だけを置く。資料ごとに意味の違う語は
# `COLUMN_ROLE_UNCERTAIN_TERMS` に置き、役割を付けずに診断だけを残す。
COLUMN_ROLE_TERMS: dict[str, tuple[str, ...]] = {
    "default": (
        "既定値",
        "既定",
        "デフォルト",
        "デフォルト値",
        "初期値",
        "初期設定",
        "初期設定値",
        "出荷時設定",
        "出荷時の値",
        "出荷時設定値",
        "標準値",
        "default",
        "defaultvalue",
        "defaults",
        "initialvalue",
        "factorydefault",
    ),
    "example": (
        "例",
        "記入例",
        "入力例",
        "設定例",
        "記述例",
        "記載例",
        "値の例",
        "例示",
        "例示値",
        "サンプル",
        "サンプル値",
        "example",
        "examples",
        "examplevalue",
        "sample",
        "samplevalue",
        "eg",
    ),
    "current": (
        "現在値",
        "現在の値",
        "現在の設定値",
        "現在設定値",
        "現行値",
        "現行設定値",
        "実設定値",
        "本番値",
        "本番設定値",
        "実績値",
        "currentvalue",
        "currentsetting",
        "actualvalue",
        "configuredvalue",
    ),
    "recommended": (
        "推奨値",
        "推奨",
        "推奨設定",
        "推奨設定値",
        "recommended",
        "recommendedvalue",
    ),
    "allowed": (
        "設定範囲",
        "設定可能範囲",
        "設定可能値",
        "設定できる値",
        "値の範囲",
        "有効範囲",
        "許容値",
        "許容範囲",
        "取りうる値",
        "取り得る値",
        "有効値",
        "選択肢",
        "allowedvalues",
        "validvalues",
        "validrange",
    ),
    "definition": (
        "説明",
        "定義",
        "内容",
        "概要",
        "意味",
        "用途",
        "解説",
        "項目説明",
        "パラメータ説明",
        "description",
        "definition",
        "meaning",
    ),
}
# 表頭の一部に含まれるときに見る語の最短の長さ（「タイムアウトの既定値」「Windows 既定値」）。
# 短い語（「例」「既定」「内容」「推奨」）は「事例」「既定外」「作業内容」のように別の意味の語にも
# 含まれるので、表頭全体が一致したときだけ使う。
_COLUMN_ROLE_SUFFIX_MIN_CHARS = 3
# 資料ごとに意味の違う語（「設定値」は現場の値のことも、選べる値の一覧のこともある）。
# 役割を付けない。
COLUMN_ROLE_UNCERTAIN_TERMS = ("設定値", "値", "設定", "value", "values", "setting")
COLUMN_ROLE_EXACT_CONFIDENCE = 1.0
COLUMN_ROLE_SUFFIX_CONFIDENCE = 0.8
# これより低い信頼度の判定は役割にしない（黙って推測しない）。
COLUMN_ROLE_MIN_CONFIDENCE = 0.75
# 役割が決まった列（判定と指定の合計）がこれより少ないシートでは、判定を使わない。設定値の表は
# 説明の列と値の列、または複数の値の列を持つ。1 列だけ当たる表（検体の「サンプル」、作業の
# 「内容」）は設定値の表とは限らない。
COLUMN_ROLE_MIN_DETECTED_COLUMNS = 2
_HEADER_BRACKETS = re.compile(r"[（(［\[【〔<＜][^）)］\]】〕>＞]*[）)］\]】〕>＞]")
_HEADER_NOISE = re.compile(r"[\s_\-・:：.。、,/／※*＊]+")
_NUMERIC_TEXT = re.compile(r"^[+-]?[\d,]+(\.\d+)?%?$|^\d{4}-\d{2}-\d{2}([ T][\d:.]+)?$")


def openpyxl_available() -> bool:
    try:
        import openpyxl  # noqa: F401
    except Exception:
        return False
    return True


def xlrd_available() -> bool:
    try:
        import xlrd  # noqa: F401
    except Exception:
        return False
    return True


@dataclass
class _Grid:
    """1 シートのセル（1 始まりの行・列 → 表示の文字列）。"""

    name: str
    hidden: bool
    cells: dict[tuple[int, int], str] = field(default_factory=dict)
    merges: list[tuple[int, int, int, int]] = field(default_factory=list)
    missing_formula_cells: set[tuple[int, int]] = field(default_factory=set)

    @property
    def max_row(self) -> int:
        return max((row for row, _ in self.cells), default=0)

    def row_columns(self, row: int) -> list[int]:
        return sorted(col for (r, col), value in self.cells.items() if r == row and value.strip())


def convert(
    source_bytes: bytes,
    content_type: str,
    preprocess_profile: str,
    source_profile: SourceProfile | None,
    *,
    options: Mapping[str, Any] | None = None,
) -> ConvertOutcome:
    """選択プリセットで変換する。対象外・依存欠如・失敗は passthrough へ縮退する。"""
    del content_type
    if preprocess_profile != "excel_to_json":
        return ConvertOutcome.passthrough(
            reason=f"preprocess_unsupported_profile:{preprocess_profile}"
        )
    try:
        parsed = ExcelOptions.model_validate(dict(options or {}))
    except ValidationError:
        return ConvertOutcome.passthrough(reason="excel_options_invalid")
    return _excel_to_records(source_bytes, source_profile, parsed)


def _is_xlsx(source_bytes: bytes, source_profile: SourceProfile | None) -> bool:
    if source_bytes[:4] == _XLSX_MAGIC:
        return True
    if source_bytes[:8] == _XLS_MAGIC:
        return False
    extension = (source_profile.extension if source_profile is not None else "") or ""
    return extension.strip().lower().lstrip(".") == "xlsx"


def _excel_to_records(
    source_bytes: bytes, source_profile: SourceProfile | None, options: ExcelOptions
) -> ConvertOutcome:
    if not source_bytes:
        return ConvertOutcome.passthrough(reason="excel_empty")
    is_xlsx = _is_xlsx(source_bytes, source_profile)
    grids, failure = _read_xlsx(source_bytes) if is_xlsx else _read_xls(source_bytes)
    if grids is None:
        return ConvertOutcome.passthrough(reason=failure or "excel_parse_failed")
    warnings: list[str] = []
    sheets: list[SheetRecords] = []
    skipped: list[SkippedSheet] = []
    requested = [name.strip() for name in options.sheets if name.strip()]
    available = {grid.name for grid in grids}
    for name in requested:
        if name not in available:
            warnings.append(f"excel_sheet_not_found:{name}")
    excluded_sheets = {name.strip().casefold() for name in options.exclude_sheets if name.strip()}
    ranges = options.parsed_ranges()
    for name in sorted({item.sheet for item in ranges if item.sheet is not None}):
        if name not in available:
            warnings.append(f"excel_range_sheet_not_found:{name}")
    for grid in grids:
        if requested and grid.name not in requested:
            skipped.append(SkippedSheet(name=grid.name, reason="not_selected"))
            continue
        if grid.name.strip().casefold() in excluded_sheets:
            skipped.append(SkippedSheet(name=grid.name, reason="excluded"))
            continue
        if grid.hidden and not options.include_hidden_sheets and grid.name not in requested:
            skipped.append(SkippedSheet(name=grid.name, reason="hidden"))
            continue
        sheet_ranges = _ranges_for(grid.name, ranges)
        sheet = _sheet_records(_crop(grid, sheet_ranges) if sheet_ranges else grid, options)
        if sheet is None:
            skipped.append(SkippedSheet(name=grid.name, reason="empty"))
            continue
        for item in sheet.diagnostics:
            if item.code == "header_low_confidence":
                warnings.append(f"excel_header_low_confidence:{grid.name}")
        missing = sum(
            1 for item in sheet.diagnostics if item.code == "formula_without_cached_value"
        )
        if missing:
            warnings.append(f"excel_formula_without_cached_value:{grid.name}:{missing}")
        sheets.append(sheet)
    for key in options.column_roles:
        # 指定した列がどのシートにも無ければ警告する（列名の書き違いに気付けるように）。
        if not any(
            key.casefold() in {column.name.casefold(), column.column.casefold()}
            for sheet in sheets
            for column in sheet.columns
        ):
            warnings.append(f"excel_column_role_target_not_found:{key}")
    if not any(sheet.blocks or sheet.preamble for sheet in sheets):
        return ConvertOutcome.passthrough(reason="excel_no_rows")
    document = SheetRecordsDocument(
        source_format="xlsx" if is_xlsx else "xls", sheets=sheets, skipped_sheets=skipped
    )
    return ConvertOutcome(
        converted=True,
        converter_name=CONVERTER_NAME,
        converter_version=CONVERTER_VERSION,
        derived_bytes=document.to_json_bytes(),
        derived_content_type=SHEET_RECORDS_CONTENT_TYPE,
        warnings=tuple(warnings),
    )


# ---- 読み取り ------------------------------------------------------------------------


def _read_xlsx(source_bytes: bytes) -> tuple[list[_Grid] | None, str | None]:
    try:
        import openpyxl
    except Exception:
        return None, "openpyxl_unavailable"
    try:
        values_book = openpyxl.load_workbook(io.BytesIO(source_bytes), data_only=True)
        formula_book = openpyxl.load_workbook(io.BytesIO(source_bytes), data_only=False)
    except Exception:
        return None, "excel_open_failed"
    grids: list[_Grid] = []
    try:
        for worksheet in values_book.worksheets:
            formulas = formula_book[worksheet.title]
            grid = _Grid(name=worksheet.title, hidden=worksheet.sheet_state != "visible")
            for row in worksheet.iter_rows():
                for cell in row:
                    formula = formulas.cell(row=cell.row, column=cell.column).value
                    is_formula = isinstance(formula, str) and formula.startswith("=")
                    if cell.value is None:
                        if is_formula:
                            # キャッシュの無い数式は空にせず、数式の文字列にして診断を残す。
                            grid.missing_formula_cells.add((cell.row, cell.column))
                            grid.cells[(cell.row, cell.column)] = str(formula)
                        continue
                    text = _format_value(cell.value, cell.number_format, is_date=cell.is_date)
                    if text:
                        grid.cells[(cell.row, cell.column)] = text
            grid.merges = [
                (rng.min_row, rng.min_col, rng.max_row, rng.max_col)
                for rng in worksheet.merged_cells.ranges
            ]
            grids.append(grid)
    except Exception:
        return None, "excel_read_failed"
    finally:
        values_book.close()
        formula_book.close()
    return grids, None


def _read_xls(source_bytes: bytes) -> tuple[list[_Grid] | None, str | None]:
    try:
        import xlrd
    except Exception:
        return None, "xlrd_unavailable"
    try:
        book = xlrd.open_workbook(file_contents=source_bytes, formatting_info=True)
    except Exception:
        try:
            book = xlrd.open_workbook(file_contents=source_bytes)
        except Exception:
            return None, "excel_open_failed"
    grids: list[_Grid] = []
    try:
        for sheet in book.sheets():
            grid = _Grid(name=sheet.name, hidden=getattr(sheet, "visibility", 0) != 0)
            for row in range(sheet.nrows):
                for col in range(sheet.ncols):
                    text = _xls_cell_text(book, sheet, row, col, xlrd)
                    if text:
                        grid.cells[(row + 1, col + 1)] = text
            grid.merges = [
                (rlo + 1, clo + 1, rhi, chi) for rlo, rhi, clo, chi in sheet.merged_cells
            ]
            grids.append(grid)
    except Exception:
        return None, "excel_read_failed"
    return grids, None


def _xls_cell_text(book: Any, sheet: Any, row: int, col: int, xlrd: Any) -> str:
    cell = sheet.cell(row, col)
    if cell.ctype in (xlrd.XL_CELL_EMPTY, xlrd.XL_CELL_BLANK):
        return ""
    if cell.ctype == xlrd.XL_CELL_TEXT:
        return str(cell.value)
    if cell.ctype == xlrd.XL_CELL_BOOLEAN:
        return "TRUE" if cell.value else "FALSE"
    if cell.ctype == xlrd.XL_CELL_ERROR:
        return str(xlrd.error_text_from_code.get(cell.value, "#ERROR"))
    if cell.ctype == xlrd.XL_CELL_DATE:
        try:
            value = xlrd.xldate_as_datetime(cell.value, book.datemode)
        except Exception:
            return _general_number(float(cell.value))
        return _format_datetime(value)
    return _format_number(float(cell.value), _xls_number_format(book, sheet, row, col))


def _xls_number_format(book: Any, sheet: Any, row: int, col: int) -> str:
    try:
        xf = book.xf_list[sheet.cell_xf_index(row, col)]
        return str(book.format_map[xf.format_key].format_str)
    except Exception:
        return "General"


# ---- 値の文字列化 ----------------------------------------------------------------------


def _format_value(value: object, number_format: str | None, *, is_date: bool) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "TRUE" if value else "FALSE"
    if isinstance(value, _dt.datetime | _dt.date | _dt.time):
        return _format_datetime(value)
    if isinstance(value, int | float):
        if is_date:
            return _general_number(float(value))
        return _format_number(value, number_format or "General")
    return str(value).strip()


def _format_datetime(value: _dt.datetime | _dt.date | _dt.time) -> str:
    if isinstance(value, _dt.datetime):
        if value.time() == _dt.time(0, 0):
            return value.date().isoformat()
        return value.isoformat(sep=" ")
    return value.isoformat()


def _general_number(value: int | float) -> str:
    if isinstance(value, int):
        return str(value)
    if value.is_integer():
        return str(int(value))
    # 浮動小数の誤差（0.1+0.2 など）を出さない。指数表記にもしない。
    text = f"{value:.15f}".rstrip("0").rstrip(".")
    return text or "0"


def _format_number(value: int | float, number_format: str) -> str:
    """表示の書式（ゼロ埋め・桁区切り・小数桁・百分率）で数値を文字列にする。"""
    pattern = (number_format or "General").split(";", 1)[0].strip()
    if pattern in {"", "General", "@"} or not re.fullmatch(r"[0#,.%]+", pattern):
        return _general_number(value)
    percent = pattern.endswith("%")
    core = pattern.rstrip("%")
    integer_part, _, decimal_part = core.partition(".")
    decimals = len(decimal_part.replace(",", ""))
    thousands = "," in integer_part
    min_digits = integer_part.replace(",", "").replace("#", "").count("0")
    number = float(value) * (100 if percent else 1)
    sign = "-" if number < 0 else ""
    if thousands:
        body = f"{abs(number):,.{decimals}f}"
    else:
        body = f"{abs(number):.{decimals}f}"
        whole, dot, fraction = body.partition(".")
        body = whole.zfill(min_digits) + dot + fraction
    return f"{sign}{body}{'%' if percent else ''}"


# ---- 表頭・行 --------------------------------------------------------------------------


def _column_letter(index: int) -> str:
    letters = ""
    while index > 0:
        index, remainder = divmod(index - 1, 26)
        letters = chr(65 + remainder) + letters
    return letters


def _ranges_for(sheet_name: str, ranges: list[ExcelRange]) -> list[ExcelRange]:
    """シートに当てる範囲（シート名を付けた範囲があればそれ、無ければシート名の無い範囲）。"""
    named = [item for item in ranges if item.sheet == sheet_name]
    return named or [item for item in ranges if item.sheet is None]


def _crop(grid: _Grid, ranges: list[ExcelRange]) -> _Grid:
    """範囲の外のセルを除いたシート（#1229）。

    結合セルは範囲と重なる部分に切り詰め、左上が範囲の外でも値は切り詰めた左上へ移す。
    """

    def inside(row: int, col: int) -> bool:
        return any(item.contains(row, col) for item in ranges)

    cropped = _Grid(
        name=grid.name,
        hidden=grid.hidden,
        cells={key: value for key, value in grid.cells.items() if inside(*key)},
        missing_formula_cells={key for key in grid.missing_formula_cells if inside(*key)},
    )
    for min_row, min_col, max_row, max_col in grid.merges:
        value = grid.cells.get((min_row, min_col), "")
        for item in ranges:
            top, left = max(min_row, item.min_row), max(min_col, item.min_col)
            bottom, right = min(max_row, item.max_row), min(max_col, item.max_col)
            if top > bottom or left > right:
                continue
            cropped.merges.append((top, left, bottom, right))
            if value:
                cropped.cells.setdefault((top, left), value)
    return cropped


def _apply_merges(grid: _Grid, header_rows: range) -> None:
    """結合セルの値を埋める。

    表頭の行の中では結合の範囲（横・縦）を左上の値で埋める。データでは同じ行の横の結合だけを埋め、
    縦の結合の値は後の行へ漏らさない（no.1-rag と同じ。手順の境目を増やさない）。
    """
    for min_row, min_col, max_row, max_col in grid.merges:
        value = grid.cells.get((min_row, min_col), "")
        if not value:
            continue
        for row in range(min_row, max_row + 1):
            in_header = row in header_rows and min_row in header_rows
            if row != min_row and not in_header:
                continue
            for col in range(min_col, max_col + 1):
                grid.cells.setdefault((row, col), value)


def _is_textual(value: str) -> bool:
    return not _NUMERIC_TEXT.match(value.strip())


def _detect_header(grid: _Grid, nonempty_rows: list[int]) -> tuple[int, float, str]:
    widest = max((len(grid.row_columns(row)) for row in nonempty_rows), default=1)
    best: tuple[float, int, str] | None = None
    for position, row in enumerate(nonempty_rows[:_HEADER_SCAN_ROWS]):
        columns = grid.row_columns(row)
        values = [grid.cells[(row, col)] for col in columns]
        if not values:
            continue
        textish = sum(1 for value in values if _is_textual(value)) / len(values)
        unique = len(set(values)) / len(values)
        coverage = len(columns) / widest
        following = nonempty_rows[position + 1 : position + 1 + _HEADER_LOOKAHEAD_ROWS]
        if following:
            consistency = sum(
                len(set(grid.row_columns(other)) & set(columns)) / len(columns)
                for other in following
            ) / len(following)
        else:
            consistency = 0.0
        # 1 つのセルだけの行（説明・表題）は、表がそれ 1 列でない限り表頭にしない。
        if len(columns) == 1 and widest > 1:
            coverage = 0.0
        hints = sum(1 for hint in PROCEDURE_HEADER_HINTS if hint in " ".join(values))
        score = 0.3 * textish + 0.15 * unique + 0.3 * consistency + 0.25 * coverage
        score += min(0.2, 0.1 * hints)
        # 数値・日付だけの行はデータの行らしい（表頭の無い表）。文字の割合で下げる。
        score *= 0.5 + 0.5 * textish
        reason = (
            f"{len(columns)} 列・文字 {textish:.0%}・重複なし {unique:.0%}・"
            f"後続の行の一致 {consistency:.0%}"
        )
        if best is None or score > best[0] + 1e-9:
            best = (score, row, reason)
    if best is None:
        return nonempty_rows[0], 0.0, "表頭の候補がありません"
    score, row, reason = best
    return row, round(min(max(score, 0.0), 1.0), 3), reason


def _column_names(raw: list[tuple[int, str, str]]) -> list[str]:
    """空の表頭は ``column_<列>``、重複は ``<名前>__<列>``（no.1-rag と同じ）。"""
    names: list[str] = []
    seen: set[str] = set()
    for _, letter, name in raw:
        candidate = name or f"column_{letter}"
        if candidate.casefold() in seen:
            candidate = f"{candidate}__{letter}"
        seen.add(candidate.casefold())
        names.append(candidate)
    return names


def _excluded(name: str, letter: str, excludes: set[str]) -> bool:
    return name.casefold() in excludes or letter.casefold() in excludes


def _procedure_columns(
    grid: _Grid, header_row: int, data_rows: list[int], columns: list[int]
) -> tuple[int, int] | None:
    """手順書の番号の列と題名の列を探す（表頭のある最初の列が題名、その左で一緒に埋まる列が番号）。"""
    labeled = [col for col in columns if grid.cells.get((header_row, col), "").strip()]
    if not labeled:
        return None
    title_column = labeled[0]
    candidates: list[tuple[int, int]] = []
    for col in range(1, title_column):
        together = sum(
            1
            for row in data_rows
            if grid.cells.get((row, col), "").strip()
            and grid.cells.get((row, title_column), "").strip()
        )
        if together >= 2:
            candidates.append((together, col))
    if not candidates:
        return None
    return max(candidates)[1], title_column


def _density(grid: _Grid, data_rows: list[int], columns: list[int]) -> float:
    sample = data_rows[:_DENSITY_SAMPLE_ROWS]
    if not sample or not columns:
        return 0.0
    ratios = sorted(
        sum(1 for col in columns if grid.cells.get((row, col), "").strip()) / len(columns)
        for row in sample
    )
    middle = len(ratios) // 2
    if len(ratios) % 2:
        return ratios[middle]
    return (ratios[middle - 1] + ratios[middle]) / 2


def _row_range(cols: list[int], row_start: int, row_end: int) -> str:
    return f"{_column_letter(cols[0])}{row_start}:{_column_letter(cols[-1])}{row_end}"


def _table_blocks(
    grid: _Grid, data_rows: list[int], columns: list[int], names: dict[int, str]
) -> list[SheetBlock]:
    blocks: list[SheetBlock] = []
    for row in data_rows:
        values = {
            names[col]: value
            for col in columns
            if (value := grid.cells.get((row, col), "").strip())
        }
        if values:
            blocks.append(
                SheetBlock(
                    kind="row",
                    row_start=row,
                    row_end=row,
                    cell_range=_row_range(columns, row, row),
                    values=values,
                )
            )
    return blocks


def _procedure_blocks(
    grid: _Grid,
    data_rows: list[int],
    columns: list[int],
    names: dict[int, str],
    index_column: int,
    title_column: int,
) -> list[SheetBlock]:
    """番号と題名のある行を手順の始まりにし、次の手順の前までの行を 1 block にする。"""
    all_columns = sorted({*columns, index_column, title_column})
    detail_columns = [col for col in all_columns if col not in {index_column, title_column}]
    boundaries = [
        row
        for row in data_rows
        if grid.cells.get((row, index_column), "").strip()
        and grid.cells.get((row, title_column), "").strip()
    ]
    blocks: list[SheetBlock] = []
    section_title: str | None = None
    for position, row_start in enumerate(boundaries):
        title = grid.cells[(row_start, title_column)].strip()
        has_detail = any(grid.cells.get((row_start, col), "").strip() for col in detail_columns)
        if not has_detail:
            # 詳細の無い題名の行は章の見出し（以降の手順の section_path に入れる）。
            section_title = title
            continue
        next_start = boundaries[position + 1] if position + 1 < len(boundaries) else None
        rows = [
            row
            for row in data_rows
            if row >= row_start and (next_start is None or row < next_start)
        ]
        lines = [f"セクション: {section_title}"] if section_title else []
        last_row = row_start
        for row in rows:
            parts = [
                f"{names.get(col, f'column_{_column_letter(col)}')}: {value}"
                for col in all_columns
                if (value := grid.cells.get((row, col), "").strip())
            ]
            if parts:
                lines.append(" | ".join(parts))
                last_row = row
        blocks.append(
            SheetBlock(
                kind="procedure_step",
                row_start=row_start,
                row_end=last_row,
                cell_range=_row_range(all_columns, row_start, last_row),
                section_path=[value for value in (section_title, title) if value],
                lines=lines,
            )
        )
    return blocks


def _normalize_header(text: str) -> str:
    """表頭の語の比較用の形（全角半角・大小をそろえ、括弧の単位・注記と区切りを除く）。"""
    folded = unicodedata.normalize("NFKC", text).casefold()
    folded = _HEADER_BRACKETS.sub("", folded)
    return _HEADER_NOISE.sub("", folded)


def detect_column_role(header: str) -> tuple[str | None, float, str | None, str | None]:
    """表頭の語から列の役割を決める（役割・信頼度・当たった語・役割を付けない理由）。

    複数行の表頭（「設定 / 既定値」）は下の行（具体的な語）から順に見て、最初に当たった行で決める。
    """
    for part in reversed([item for item in header.split(" / ") if item.strip()]):
        normalized = _normalize_header(part)
        if not normalized:
            continue
        exact = [role for role, terms in COLUMN_ROLE_TERMS.items() if normalized in terms]
        if len(exact) == 1:
            return exact[0], COLUMN_ROLE_EXACT_CONFIDENCE, part.strip(), None
        if normalized in COLUMN_ROLE_UNCERTAIN_TERMS:
            return None, 0.0, part.strip(), "uncertain"
        # 長い語（3 文字以上）が表頭のどこかに含まれる役割を集める。2 つ以上なら「既定値の説明」
        # 「既定値／推奨値」のように 1 つに決められない。1 つでも、表頭の末尾がその語でなければ
        # （「既定値の変更履歴」）値の列とは言えないので付けない。
        # 短い語（2 文字。「説明」「推奨」）は役割を決めるのには使わないが、末尾にあれば
        # 1 つに決められない理由にはする（「既定値の説明」）。
        contained: dict[str, bool] = {}
        short_suffix: set[str] = set()
        for role, terms in COLUMN_ROLE_TERMS.items():
            for term in terms:
                if len(term) >= _COLUMN_ROLE_SUFFIX_MIN_CHARS and term in normalized:
                    contained[role] = contained.get(role, False) or normalized.endswith(term)
                elif len(term) >= 2 and normalized.endswith(term):
                    short_suffix.add(role)
        if contained and len(set(contained) | short_suffix) > 1:
            return None, 0.0, part.strip(), "ambiguous"
        if len(contained) == 1:
            role, at_end = next(iter(contained.items()))
            if at_end:
                return role, COLUMN_ROLE_SUFFIX_CONFIDENCE, part.strip(), None
            return None, 0.0, part.strip(), "uncertain"
    return None, 0.0, None, None


def _column_roles(
    columns: list[tuple[int, str, str, str]],
    configured: Mapping[str, str],
    *,
    detect: bool,
) -> tuple[list[SheetColumn], list[SheetDiagnostic]]:
    """列（列番号・記号・列名・表頭の文字）の役割を、指定 → 表頭の語の順に決める。"""
    by_key = {key.casefold(): (key, role) for key, role in configured.items()}
    result: list[SheetColumn] = []
    diagnostics: list[SheetDiagnostic] = []
    detected: list[int] = []
    for _, letter, name, header in columns:
        match = by_key.get(name.casefold()) or by_key.get(letter.casefold())
        if match is not None:
            key, role = match
            if role == COLUMN_ROLE_NONE:
                result.append(SheetColumn(name=name, column=letter))
            else:
                result.append(
                    SheetColumn(
                        name=name,
                        column=letter,
                        role=cast(ColumnRole, role),
                        role_method="configured",
                        role_term=key,
                    )
                )
            continue
        result.append(SheetColumn(name=name, column=letter))
        if not detect:
            continue
        role, confidence, term, reason = detect_column_role(header)
        if role is not None and confidence >= COLUMN_ROLE_MIN_CONFIDENCE:
            result[-1] = SheetColumn(
                name=name,
                column=letter,
                role=cast(ColumnRole, role),
                role_method="detected",
                role_term=term,
            )
            detected.append(len(result) - 1)
        elif reason is not None:
            diagnostics.append(
                SheetDiagnostic(code=f"column_role_{reason}", detail=f"{letter}:{term or name}")
            )
    configured_count = sum(1 for item in result if item.role_method == "configured")
    if len(detected) > 0 and len(detected) + configured_count < COLUMN_ROLE_MIN_DETECTED_COLUMNS:
        # 1 列だけ当たったシートは設定値の表と言い切れない。役割を外して診断だけを残す
        # （指定した役割の列があれば、設定値の表として判定を使う）。
        for index in detected:
            column = result[index]
            diagnostics.append(
                SheetDiagnostic(
                    code="column_role_not_corroborated",
                    detail=f"{column.column}:{column.role_term or column.name}",
                )
            )
            result[index] = SheetColumn(name=column.name, column=column.column)
    return result, diagnostics


def _sheet_records(grid: _Grid, options: ExcelOptions) -> SheetRecords | None:
    nonempty_rows = sorted({row for (row, _), value in grid.cells.items() if value.strip()})
    if not nonempty_rows:
        return None
    diagnostics: list[SheetDiagnostic] = [
        SheetDiagnostic(code="formula_without_cached_value", cell=f"{_column_letter(col)}{row}")
        for row, col in sorted(grid.missing_formula_cells)
    ]
    header_count = options.header_row_count
    if options.header_row is not None:
        header_row = options.header_row
        detection = HeaderDetection(
            method="configured", confidence=1.0, reason=f"{header_row} 行目を表頭に指定"
        )
        if not grid.row_columns(header_row):
            diagnostics.append(SheetDiagnostic(code="header_row_empty", detail=str(header_row)))
    else:
        header_row, confidence, reason = _detect_header(grid, nonempty_rows)
        detection = HeaderDetection(method="detected", confidence=confidence, reason=reason)
        if confidence < HEADER_LOW_CONFIDENCE:
            diagnostics.append(
                SheetDiagnostic(code="header_low_confidence", detail=f"{confidence:.2f}")
            )
    header_rows = range(header_row, header_row + header_count)
    data_start = header_row + header_count
    _apply_merges(grid, header_rows)

    used_columns = sorted(
        {col for (row, col), value in grid.cells.items() if row >= header_row and value.strip()}
    )
    excludes = {item.strip().casefold() for item in options.exclude_columns if item.strip()}
    raw_names: list[tuple[int, str, str]] = []
    for col in used_columns:
        letter = _column_letter(col)
        parts: list[str] = []
        for row in header_rows:
            value = grid.cells.get((row, col), "").strip()
            if value and (not parts or parts[-1] != value):
                parts.append(value)
        name = " / ".join(parts)
        if not _excluded(name, letter, excludes):
            raw_names.append((col, letter, name))
    names = dict(zip([col for col, _, _ in raw_names], _column_names(raw_names), strict=True))
    columns = [col for col, _, _ in raw_names]
    detect_roles = options.column_role_detection == "auto"
    if (
        detect_roles
        and detection.method == "detected"
        and detection.confidence < HEADER_LOW_CONFIDENCE
    ):
        # 表頭が確かでないシートでは、表頭の語で列の役割を決めない（指定の役割だけを使う）。
        detect_roles = False
        diagnostics.append(SheetDiagnostic(code="column_roles_skipped_low_header_confidence"))
    sheet_columns, role_diagnostics = _column_roles(
        [(col, _column_letter(col), names[col], header) for col, _, header in raw_names],
        options.column_roles,
        detect=detect_roles,
    )
    diagnostics.extend(role_diagnostics)
    roles = {item.name: item.role for item in sheet_columns if item.role}

    preamble: list[SheetPreambleRow] = []
    for row in nonempty_rows:
        if row >= header_row:
            break
        cols = grid.row_columns(row)
        # 横の結合で埋めた同じ値は 1 つにする（結合した見出しの文を繰り返さない）。
        texts: list[str] = []
        for col in cols:
            value = grid.cells[(row, col)].strip()
            if not texts or texts[-1] != value:
                texts.append(value)
        text = " ".join(texts)
        preamble.append(
            SheetPreambleRow(row_number=row, cell_range=_row_range(cols, row, row), text=text)
        )

    data_rows = [
        row
        for row in nonempty_rows
        if row >= data_start and any(grid.cells.get((row, col), "").strip() for col in columns)
    ]
    procedure = _procedure_columns(grid, header_row, data_rows, columns) if columns else None
    mode = options.mode
    if mode == "auto":
        if procedure is not None:
            mode = "procedure"
        else:
            mode = (
                "table"
                if _density(grid, data_rows, columns) >= TABLE_DENSITY_THRESHOLD
                else "procedure"
            )
    if mode == "procedure" and procedure is not None:
        display = {col: column_display_name(name, roles.get(name)) for col, name in names.items()}
        blocks = _procedure_blocks(grid, data_rows, columns, display, *procedure)
    else:
        # 番号と題名の列が無い手順書は、no.1-rag と同じく表として 1 行ずつ読む。
        if mode == "procedure":
            diagnostics.append(SheetDiagnostic(code="procedure_columns_not_found"))
        mode = "table"
        blocks = _table_blocks(grid, data_rows, columns, names)
    return SheetRecords(
        name=grid.name,
        header_row=header_row,
        header_row_count=header_count,
        header_detection=detection,
        mode=mode,
        columns=sheet_columns,
        preamble=preamble,
        blocks=blocks,
        diagnostics=diagnostics,
    )
