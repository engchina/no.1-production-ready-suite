"""表計算ファイルの行の記録（前処理 `excel_to_json` の出力）と、その要素化（#1221）。

前処理のサービスは Excel（.xlsx / .xls）を、シートごとの「表頭・元の行番号・セル範囲・表示の値」を
持つ JSON（`SHEET_RECORDS_CONTENT_TYPE`）にする。parser のサービス（どの adapter でも）は、この JSON
を外部の parser に渡さず、`sheet_records_extraction` で記録の単位（block）を 1 つずつ要素にする。

- block は 2 種類（engchina/no.1-rag の前処理に合わせる）。
  - `row`: 表の 1 行（「列名: 値」）。
  - `procedure_step`: 手順書の 1 手順（番号と題名の行から次の手順までの複数行）。
- 節はシート名と、手順なら章・手順の題名（`section_path=[シート名, …]`）。表頭より上の行
  （説明など）は本文の要素にする。シート名だけの見出しの要素は作らない。
- 要素の metadata に `sheet_name`・`row_start` / `row_end`・`cell_range`・`cell_column_start` /
  `cell_column_end`・`header_row` を持ち、chunking が chunk の場所にまとめる。
- 選択肢（`ExcelOptions`）は Document Recipe の `excel_options` から前処理へ渡る。
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from rag_parser_core.extraction import DocumentElement, StructuredExtraction

SHEET_RECORDS_CONTENT_TYPE = "application/vnd.production-ready.sheet-records+json"
SHEET_RECORDS_FORMAT = "sheet_records"
SHEET_RECORDS_FORMAT_VERSION = 1
SHEET_RECORD_CONTENT_KIND = "record"
SHEET_PREAMBLE_CONTENT_KIND = "text"
# 1 つの要素の本文の上限（極端に長い行・手順で chunk を壊さない）。
_BLOCK_TEXT_MAX_CHARS = 8000
# 表頭の推定の信頼度が低いシートの warning（文書レシピを確認（REVIEW）で止める。#1229）。
EXCEL_HEADER_LOW_CONFIDENCE_WARNING = "excel_header_low_confidence"
# 範囲の指定（`A3:F200`、`手順!A1:D80`、`'売上 2026'!B2:H90`）。
_RANGE = re.compile(
    r"^(?:(?:'(?P<quoted>(?:[^']|'')+)'|(?P<sheet>[^!']+))!)?"
    r"(?P<c1>[A-Za-z]{1,3})(?P<r1>\d{1,7}):(?P<c2>[A-Za-z]{1,3})(?P<r2>\d{1,7})$"
)


@dataclass(frozen=True)
class ExcelRange:
    """読む範囲（1 始まりの行・列。シート名が無ければ選んだすべてのシート）。"""

    sheet: str | None
    min_row: int
    min_col: int
    max_row: int
    max_col: int

    def contains(self, row: int, col: int) -> bool:
        return self.min_row <= row <= self.max_row and self.min_col <= col <= self.max_col


def _column_index(letters: str) -> int:
    index = 0
    for char in letters.upper():
        index = index * 26 + (ord(char) - ord("A") + 1)
    return index


def parse_excel_range(text: str) -> ExcelRange:
    """`[シート名!]A1:F20` を読む（不正なら ValueError）。"""
    match = _RANGE.match(text.strip())
    if match is None:
        raise ValueError(f"範囲は「A3:F200」か「シート名!A3:F200」の形で指定してください: {text}")
    sheet = match.group("quoted")
    sheet = sheet.replace("''", "'") if sheet is not None else match.group("sheet")
    rows = sorted((int(match.group("r1")), int(match.group("r2"))))
    cols = sorted((_column_index(match.group("c1")), _column_index(match.group("c2"))))
    if rows[0] < 1 or cols[1] > 16384 or rows[1] > 1048576:
        raise ValueError(f"範囲が Excel のシートの外です: {text}")
    return ExcelRange(
        sheet=sheet.strip() if sheet and sheet.strip() else None,
        min_row=rows[0],
        min_col=cols[0],
        max_row=rows[1],
        max_col=cols[1],
    )


class ExcelOptions(BaseModel):
    """Excel の前処理の選択肢（Document Recipe の `excel_options`）。"""

    model_config = ConfigDict(extra="forbid")

    mode: Literal["auto", "table", "procedure"] = Field(
        default="auto",
        description=(
            "シートの読み方。auto は手順書（番号と題名の列）なら procedure、"
            "密な表なら table に決める。"
        ),
    )
    header_row: int | None = Field(
        default=None,
        ge=1,
        le=1000,
        description="表頭の行（1 始まり）。未指定は先頭の行から推定する。",
    )
    header_row_count: int = Field(default=1, ge=1, le=5, description="表頭の行数。")
    sheets: list[str] = Field(
        default_factory=list,
        max_length=100,
        description="読むシート名（未指定は表示されているすべてのシート）。",
    )
    exclude_sheets: list[str] = Field(
        default_factory=list, max_length=100, description="読まないシート名。"
    )
    include_hidden_sheets: bool = Field(default=False, description="非表示のシートも読むか。")
    exclude_columns: list[str] = Field(
        default_factory=list,
        max_length=200,
        description="読まない列（列名か列の記号。例: 備考、F）。",
    )
    ranges: list[str] = Field(
        default_factory=list,
        max_length=50,
        description=(
            "読む範囲（例: A3:F200＝選んだすべてのシート、手順!A1:D80＝そのシートだけ）。"
            "範囲の外のセルは表頭の推定にも記録にも使わない。未指定はシート全体。"
        ),
    )

    @field_validator("ranges")
    @classmethod
    def _valid_ranges(cls, value: list[str]) -> list[str]:
        cleaned = [item.strip() for item in value if item.strip()]
        for item in cleaned:
            parse_excel_range(item)
        return cleaned

    def parsed_ranges(self) -> list[ExcelRange]:
        return [parse_excel_range(item) for item in self.ranges]


class HeaderDetection(BaseModel):
    method: Literal["configured", "detected"]
    confidence: float = Field(ge=0.0, le=1.0)
    reason: str


class SheetColumn(BaseModel):
    name: str
    column: str = Field(description="列の記号（A, B, …）。")


class SheetPreambleRow(BaseModel):
    row_number: int = Field(ge=1)
    cell_range: str
    text: str


class SheetBlock(BaseModel):
    """記録の単位（表の 1 行か、手順書の 1 手順）。"""

    kind: Literal["row", "procedure_step"]
    row_start: int = Field(ge=1, description="シートの元の行番号（1 始まり）。")
    row_end: int = Field(ge=1)
    cell_range: str = Field(description="セルの範囲（例: A5:F5、A5:F9）。")
    section_path: list[str] = Field(
        default_factory=list, description="手順の章・題名（表の行は空）。"
    )
    values: dict[str, str] = Field(default_factory=dict, description="表の行の列名と値。")
    lines: list[str] = Field(default_factory=list, description="手順の本文の行。")


class SheetDiagnostic(BaseModel):
    code: str
    cell: str | None = None
    detail: str | None = None


class SheetRecords(BaseModel):
    name: str
    header_row: int | None = Field(default=None, ge=1)
    header_row_count: int = 1
    header_detection: HeaderDetection | None = None
    mode: Literal["table", "procedure"] = "table"
    columns: list[SheetColumn] = Field(default_factory=list)
    preamble: list[SheetPreambleRow] = Field(default_factory=list)
    blocks: list[SheetBlock] = Field(default_factory=list)
    diagnostics: list[SheetDiagnostic] = Field(default_factory=list)


class SkippedSheet(BaseModel):
    name: str
    reason: str


class SheetRecordsDocument(BaseModel):
    format: Literal["sheet_records"] = SHEET_RECORDS_FORMAT
    format_version: int = SHEET_RECORDS_FORMAT_VERSION
    source_format: Literal["xlsx", "xls"]
    sheets: list[SheetRecords] = Field(default_factory=list)
    skipped_sheets: list[SkippedSheet] = Field(default_factory=list)

    def to_json_bytes(self) -> bytes:
        return self.model_dump_json(indent=2).encode("utf-8")


def is_sheet_records_content_type(content_type: str | None) -> bool:
    """前処理の行の記録の JSON か（parameter の charset などは無視する）。"""
    return (content_type or "").split(";", 1)[0].strip().casefold() == SHEET_RECORDS_CONTENT_TYPE


def parse_sheet_records(source_bytes: bytes) -> SheetRecordsDocument | None:
    """行の記録の JSON を読む（形式・版が違えば None）。"""
    try:
        payload = json.loads(source_bytes.decode("utf-8"))
        document = SheetRecordsDocument.model_validate(payload)
    except (UnicodeDecodeError, ValueError, ValidationError):
        return None
    if document.format_version != SHEET_RECORDS_FORMAT_VERSION:
        return None
    return document


def _block_text(block: SheetBlock) -> str:
    if block.kind == "procedure_step":
        text = "\n".join(line for line in block.lines if line.strip())
    else:
        text = " / ".join(
            f"{name}: {value}" for name, value in block.values.items() if value.strip()
        )
    return text[:_BLOCK_TEXT_MAX_CHARS]


def _column_bounds(cell_range: str) -> tuple[str | None, str | None]:
    start, _, end = cell_range.partition(":")
    letters_start = "".join(char for char in start if char.isalpha()) or None
    letters_end = "".join(char for char in (end or start) if char.isalpha()) or None
    return letters_start, letters_end


def sheet_records_extraction(
    document: SheetRecordsDocument,
    *,
    source_parser: str,
    parser_backend: str,
    parser_version: str,
) -> StructuredExtraction:
    """シートを節、データの行を 1 行 1 要素にした抽出を作る。"""
    elements: list[DocumentElement] = []
    raw_lines: list[str] = []
    order = 0
    for sheet_index, sheet in enumerate(document.sheets):
        section_path = [sheet.name]
        # シート名は節（section_path）と chunk の文脈の見出しに入るので、見出しだけの要素は作らない
        # （シート名だけの場所の無い小さな chunk を作らない）。
        heading_id = f"sheet-{sheet_index:03d}"
        raw_lines.append(f"# {sheet.name}")
        for preamble in sheet.preamble:
            column_start, column_end = _column_bounds(preamble.cell_range)
            elements.append(
                DocumentElement(
                    kind="text",
                    text=preamble.text,
                    order=order,
                    element_id=f"{heading_id}-r{preamble.row_number}",
                    content_kind=SHEET_PREAMBLE_CONTENT_KIND,
                    source_parser=source_parser,
                    section_path=section_path,
                    metadata={
                        "sheet_name": sheet.name,
                        "row_start": preamble.row_number,
                        "row_end": preamble.row_number,
                        "cell_range": preamble.cell_range,
                        "cell_column_start": column_start,
                        "cell_column_end": column_end,
                        "sheet_block_kind": "preamble",
                    },
                )
            )
            raw_lines.append(preamble.text)
            order += 1
        for block in sheet.blocks:
            text = _block_text(block)
            if not text:
                continue
            column_start, column_end = _column_bounds(block.cell_range)
            elements.append(
                DocumentElement(
                    kind="text",
                    text=text,
                    order=order,
                    element_id=f"{heading_id}-r{block.row_start}",
                    content_kind=SHEET_RECORD_CONTENT_KIND,
                    source_parser=source_parser,
                    section_path=[sheet.name, *block.section_path],
                    confidence=1.0,
                    metadata={
                        "sheet_name": sheet.name,
                        "row_start": block.row_start,
                        "row_end": block.row_end,
                        "cell_range": block.cell_range,
                        "cell_column_start": column_start,
                        "cell_column_end": column_end,
                        "header_row": sheet.header_row,
                        "sheet_block_kind": block.kind,
                    },
                )
            )
            raw_lines.append(text)
            order += 1
    diagnostics = [
        f"{sheet.name}:{item.code}" + (f"@{item.cell}" if item.cell else "")
        for sheet in document.sheets
        for item in sheet.diagnostics
    ]
    # 表頭の推定の信頼度が低いシートは取込の品質の warning にし、文書レシピを確認で止める（#1229）。
    warnings = [
        f"{EXCEL_HEADER_LOW_CONFIDENCE_WARNING}:{sheet.name}"
        for sheet in document.sheets
        if any(item.code == "header_low_confidence" for item in sheet.diagnostics)
    ]
    return StructuredExtraction(
        raw_text="\n".join(raw_lines),
        document_type="SPREADSHEET",
        confidence=1.0,
        warnings=warnings,
        elements=elements,
        parser_artifacts={
            "source_parser": source_parser,
            "parser_backend": parser_backend,
            "parser_version": parser_version,
            "sheet_records_format_version": document.format_version,
            "source_format": document.source_format,
            "sheet_count": len(document.sheets),
            "block_count": sum(len(sheet.blocks) for sheet in document.sheets),
            "skipped_sheet_count": len(document.skipped_sheets),
            "sheet_diagnostics": "\n".join(diagnostics)[:4000],
        },
    )


__all__ = [
    "EXCEL_HEADER_LOW_CONFIDENCE_WARNING",
    "SHEET_RECORDS_CONTENT_TYPE",
    "SHEET_RECORDS_FORMAT",
    "SHEET_RECORDS_FORMAT_VERSION",
    "SHEET_RECORD_CONTENT_KIND",
    "ExcelOptions",
    "ExcelRange",
    "HeaderDetection",
    "SheetColumn",
    "SheetDiagnostic",
    "SheetPreambleRow",
    "SheetRecords",
    "SheetRecordsDocument",
    "SheetBlock",
    "SkippedSheet",
    "is_sheet_records_content_type",
    "parse_excel_range",
    "parse_sheet_records",
    "sheet_records_extraction",
]
