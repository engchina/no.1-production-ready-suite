"""構造化レコードの JSON Lines（前処理の成果物の共通の契約。#2457）。

CSV・Excel の前処理は、原本を 1 行 1 オブジェクトの JSON Lines にする。parser はこの JSONL を
外部の parser に渡さず、記録を 1 つずつ要素にする（場所・列名・表頭を chunk まで残す）。

形式（`STRUCTURED_RECORDS_CONTENT_TYPE`。製品独自の vendor type で、JSONL の MIME type は標準化
されていない）:

- UTF-8（BOM なし）、1 物理行に 1 つの JSON オブジェクト、行の区切りは LF、末尾は LF、空行なし。
  文字列の中の改行は JSON のエスケープ（``\\n``）のまま 1 行に収める（pretty-print しない）。
- すべての行が ``schema``・``schema_version``・``record_id``（ファイルの中で一意）・
  ``source_type``・``record_type`` を持つ。
- ``record_type``:
  - ``document``: 先頭の 1 行だけ。変換器・版・読まなかったシート・表頭の行（CSV）・診断。
  - ``sheet``: Excel のシートの文脈（表頭の行・推定の信頼度・読み方・列と役割・診断）。
  - ``row``: 表の 1 行。``fields`` は列名・列の位置・役割・値の順序付きの配列。
  - ``procedure_step``: 手順書の 1 手順（``lines``）。
  - ``preamble``: 表頭より上の行（``text``）。
  記録は ``parent_id`` で文脈の行（Excel は ``sheet``）を指し、``location``（元の行番号・セル範囲）
  と ``section_path`` を持つ。CSV の行番号は原本の物理行（引用符の中の改行を含む記録は複数行）。
- 読み取り（`parse_structured_records`）は厳格にし、不正な JSON・オブジェクトでない行・空行・BOM・
  未知の schema・未対応の版・record_id の重複・document の行の欠落・存在しない parent_id を、行番号
  つきの診断で拒否する（任意の JSONL を構造化レコードとみなさない）。

Excel の記録は `rag_parser_core.sheet_records` の行の記録（`SheetRecordsDocument`）と相互に変換し、
要素化は `sheet_records_extraction` を使う（要素の ID・metadata・分割の挙動を変えない）。
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, ValidationError

from rag_parser_core.extraction import DocumentElement, StructuredExtraction
from rag_parser_core.sheet_records import (
    SHEET_RECORD_CONTENT_KIND,
    HeaderDetection,
    SheetBlock,
    SheetColumn,
    SheetDiagnostic,
    SheetPreambleRow,
    SheetRecords,
    SheetRecordsDocument,
    SkippedSheet,
    sheet_records_extraction,
)

STRUCTURED_RECORDS_CONTENT_TYPE = "application/vnd.production-ready.structured-records+jsonl"
STRUCTURED_RECORDS_SCHEMA = "production-ready.structured-records"
STRUCTURED_RECORDS_SCHEMA_VERSION = 1
# 抽出の parser_artifacts に刻む schema の版の key。
STRUCTURED_RECORDS_ARTIFACT_KEY = "structured_records_schema_version"
# 要素の metadata に残す記録の ID（JSONL の行と要素・chunk を対応させる）。
STRUCTURED_RECORD_ID_KEY = "structured_record_id"
STRUCTURED_RECORDS_FILE_EXTENSION = ".jsonl"
# 1 つの要素の本文の上限（`sheet_records` と同じ。極端に長い行で chunk を壊さない）。
_RECORD_TEXT_MAX_CHARS = 8000

SourceType = Literal["csv", "xlsx", "xls"]
_SPREADSHEET_SOURCE_TYPES = frozenset({"xlsx", "xls"})


class _Line(BaseModel):
    """すべての行に共通の項目。"""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    schema_: Literal["production-ready.structured-records"] = Field(
        default=STRUCTURED_RECORDS_SCHEMA, alias="schema"
    )
    schema_version: Literal[1] = STRUCTURED_RECORDS_SCHEMA_VERSION
    record_id: str = Field(min_length=1, max_length=200)
    source_type: SourceType


class RecordDiagnostic(BaseModel):
    model_config = ConfigDict(extra="forbid")

    code: str = Field(min_length=1)
    cell: str | None = None
    detail: str | None = None


class SkippedPart(BaseModel):
    """読まなかった部分（Excel のシート）。"""

    model_config = ConfigDict(extra="forbid")

    name: str
    reason: str


class DocumentLine(_Line):
    """先頭の 1 行。文書全体の文脈。"""

    record_type: Literal["document"] = "document"
    converter: str = Field(min_length=1)
    converter_version: str = Field(min_length=1)
    header_row: int | None = Field(
        default=None, ge=1, description="CSV の表頭の物理行（1 始まり）。"
    )
    skipped: list[SkippedPart] = Field(default_factory=list)
    diagnostics: list[RecordDiagnostic] = Field(default_factory=list)


class SheetLine(_Line):
    """Excel のシートの文脈。"""

    record_type: Literal["sheet"] = "sheet"
    sheet_index: int = Field(ge=0)
    name: str
    header_row: int | None = Field(default=None, ge=1)
    header_row_count: int = Field(default=1, ge=1)
    header_detection: HeaderDetection | None = None
    mode: Literal["table", "procedure"] = "table"
    columns: list[SheetColumn] = Field(default_factory=list)
    diagnostics: list[RecordDiagnostic] = Field(default_factory=list)


class RecordLocation(BaseModel):
    """原本の場所（行番号は 1 始まりの元の番号。空行を除いても振り直さない）。"""

    model_config = ConfigDict(extra="forbid")

    row_start: int = Field(ge=1)
    row_end: int = Field(ge=1)
    cell_range: str | None = Field(default=None, description="Excel のセル範囲（例: A5:F5）。")


class RecordField(BaseModel):
    """表の 1 つのセル（列名・列の位置・役割・値）。"""

    model_config = ConfigDict(extra="forbid")

    name: str
    position: int = Field(ge=1, description="原本の列の位置（1 始まり）。")
    column: str | None = Field(default=None, description="Excel の列の記号（A, B, …）。")
    role: str | None = Field(default=None, description="列の役割（Excel。#1281）。")
    value: str


class _RecordLine(_Line):
    parent_id: str | None = None
    location: RecordLocation
    section_path: list[str] = Field(default_factory=list)


class RowLine(_RecordLine):
    record_type: Literal["row"] = "row"
    fields: list[RecordField] = Field(default_factory=list)


class ProcedureStepLine(_RecordLine):
    record_type: Literal["procedure_step"] = "procedure_step"
    lines: list[str] = Field(default_factory=list)


class PreambleLine(_RecordLine):
    record_type: Literal["preamble"] = "preamble"
    text: str


StructuredRecordLine = Annotated[
    DocumentLine | SheetLine | RowLine | ProcedureStepLine | PreambleLine,
    Field(discriminator="record_type"),
]
_LINE_ADAPTER: TypeAdapter[
    DocumentLine | SheetLine | RowLine | ProcedureStepLine | PreambleLine
] = TypeAdapter(StructuredRecordLine)
ContentLine = RowLine | ProcedureStepLine | PreambleLine


@dataclass(frozen=True)
class StructuredRecordsDocument:
    """読み取った JSONL（先頭の document の行と、続く行を順に持つ）。"""

    document: DocumentLine
    lines: tuple[SheetLine | ContentLine, ...]

    @property
    def source_type(self) -> str:
        return self.document.source_type


@dataclass(frozen=True)
class StructuredRecordsError:
    """読めなかった理由（`code`）と行番号（1 始まり。ファイル全体なら None）。"""

    code: str
    line: int | None = None

    def warning(self) -> str:
        suffix = f"@line{self.line}" if self.line is not None else ""
        return f"structured_records_invalid:{self.code}{suffix}"


def is_structured_records_content_type(content_type: str | None) -> bool:
    """前処理の構造化レコードの JSONL か（parameter の charset などは無視する）。"""
    normalized = (content_type or "").split(";", 1)[0].strip().casefold()
    return normalized == STRUCTURED_RECORDS_CONTENT_TYPE


def dump_structured_records(
    lines: Iterable[DocumentLine | SheetLine | ContentLine],
) -> bytes:
    """行を JSON Lines（UTF-8・LF・末尾 LF・1 行 1 オブジェクト）にする。"""
    encoded = [line.model_dump_json(by_alias=True, exclude_none=True) + "\n" for line in lines]
    return "".join(encoded).encode("utf-8")


def parse_structured_records(
    data: bytes,
) -> StructuredRecordsDocument | StructuredRecordsError:
    """JSONL を厳格に読む。契約に合わなければ `StructuredRecordsError` を返す。"""
    if data.startswith(b"\xef\xbb\xbf"):
        return StructuredRecordsError("bom")
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        return StructuredRecordsError("not_utf8")
    physical = text.split("\n")
    if physical and physical[-1] == "":
        physical.pop()
    if not physical:
        return StructuredRecordsError("empty")
    document: DocumentLine | None = None
    lines: list[SheetLine | ContentLine] = []
    seen_ids: set[str] = set()
    sheet_ids: set[str] = set()
    for number, raw in enumerate(physical, start=1):
        if not raw.strip():
            return StructuredRecordsError("blank_line", number)
        try:
            payload = json.loads(raw)
        except ValueError:
            return StructuredRecordsError("invalid_json", number)
        if not isinstance(payload, dict):
            return StructuredRecordsError("not_object", number)
        error = _schema_error(payload)
        if error is not None:
            return StructuredRecordsError(error, number)
        try:
            line = _LINE_ADAPTER.validate_python(payload)
        except ValidationError:
            return StructuredRecordsError("invalid_record", number)
        if line.record_id in seen_ids:
            return StructuredRecordsError("duplicate_record_id", number)
        seen_ids.add(line.record_id)
        if isinstance(line, DocumentLine):
            if number != 1:
                return StructuredRecordsError("document_not_first", number)
            document = line
            continue
        if document is None:
            return StructuredRecordsError("document_missing", number)
        if line.source_type != document.source_type:
            return StructuredRecordsError("source_type_mismatch", number)
        if isinstance(line, SheetLine):
            if document.source_type not in _SPREADSHEET_SOURCE_TYPES:
                return StructuredRecordsError("unsupported_record_type", number)
            sheet_ids.add(line.record_id)
        else:
            if isinstance(line, ProcedureStepLine) and (
                document.source_type not in _SPREADSHEET_SOURCE_TYPES
            ):
                return StructuredRecordsError("unsupported_record_type", number)
            if line.parent_id is not None and line.parent_id not in sheet_ids:
                return StructuredRecordsError("parent_not_found", number)
            if document.source_type in _SPREADSHEET_SOURCE_TYPES and line.parent_id is None:
                return StructuredRecordsError("parent_missing", number)
            if line.location.row_end < line.location.row_start:
                return StructuredRecordsError("invalid_location", number)
        lines.append(line)
    if document is None:
        return StructuredRecordsError("document_missing", 1)
    return StructuredRecordsDocument(document=document, lines=tuple(lines))


def _schema_error(payload: Mapping[str, Any]) -> str | None:
    """schema・版が本方式のものか（任意の JSONL を構造化レコードとみなさない）。"""
    if payload.get("schema") != STRUCTURED_RECORDS_SCHEMA:
        return "unknown_schema"
    version = payload.get("schema_version")
    if isinstance(version, bool) or version != STRUCTURED_RECORDS_SCHEMA_VERSION:
        return "unsupported_schema_version"
    return None


# ---- Excel（行の記録）との相互変換 ------------------------------------------------------


def _sheet_record_id(sheet_index: int) -> str:
    return f"sheet-{sheet_index:03d}"


def _column_position(letters: str) -> int:
    index = 0
    for char in letters.upper():
        index = index * 26 + (ord(char) - ord("A") + 1)
    return index


def _diagnostics(items: Iterable[SheetDiagnostic]) -> list[RecordDiagnostic]:
    return [RecordDiagnostic(code=item.code, cell=item.cell, detail=item.detail) for item in items]


def sheet_records_to_lines(
    document: SheetRecordsDocument,
    *,
    converter: str,
    converter_version: str,
) -> list[DocumentLine | SheetLine | ContentLine]:
    """Excel の行の記録を JSONL の行にする（シートの文脈の行・前書き・記録の順）。

    記録の ID は要素の ID（`sheet_records_extraction`）と同じにし、JSONL の行と要素を対応させる。
    """
    source_type = document.source_format
    lines: list[DocumentLine | SheetLine | ContentLine] = [
        DocumentLine(
            record_id="document",
            source_type=source_type,
            converter=converter,
            converter_version=converter_version,
            skipped=[
                SkippedPart(name=item.name, reason=item.reason) for item in document.skipped_sheets
            ],
        )
    ]
    for sheet_index, sheet in enumerate(document.sheets):
        sheet_id = _sheet_record_id(sheet_index)
        lines.append(
            SheetLine(
                record_id=sheet_id,
                source_type=source_type,
                sheet_index=sheet_index,
                name=sheet.name,
                header_row=sheet.header_row,
                header_row_count=sheet.header_row_count,
                header_detection=sheet.header_detection,
                mode=sheet.mode,
                columns=sheet.columns,
                diagnostics=_diagnostics(sheet.diagnostics),
            )
        )
        for preamble in sheet.preamble:
            lines.append(
                PreambleLine(
                    record_id=f"{sheet_id}-r{preamble.row_number}",
                    source_type=source_type,
                    parent_id=sheet_id,
                    location=RecordLocation(
                        row_start=preamble.row_number,
                        row_end=preamble.row_number,
                        cell_range=preamble.cell_range,
                    ),
                    text=preamble.text,
                )
            )
        columns = {column.name: column for column in sheet.columns}
        for block in sheet.blocks:
            location = RecordLocation(
                row_start=block.row_start, row_end=block.row_end, cell_range=block.cell_range
            )
            record_id = f"{sheet_id}-r{block.row_start}"
            if block.kind == "procedure_step":
                lines.append(
                    ProcedureStepLine(
                        record_id=record_id,
                        source_type=source_type,
                        parent_id=sheet_id,
                        location=location,
                        section_path=block.section_path,
                        lines=block.lines,
                    )
                )
                continue
            fields: list[RecordField] = []
            for name, value in block.values.items():
                column = columns.get(name)
                letter = column.column if column is not None else None
                fields.append(
                    RecordField(
                        name=name,
                        position=_column_position(letter) if letter else len(fields) + 1,
                        column=letter,
                        role=column.role if column is not None else None,
                        value=value,
                    )
                )
            lines.append(
                RowLine(
                    record_id=record_id,
                    source_type=source_type,
                    parent_id=sheet_id,
                    location=location,
                    section_path=block.section_path,
                    fields=fields,
                )
            )
    return lines


def structured_records_to_sheet_records(
    document: StructuredRecordsDocument,
) -> SheetRecordsDocument:
    """Excel の JSONL を行の記録に戻す（要素化を `sheet_records_extraction` に任せる）。"""
    sheets: list[SheetRecords] = []
    by_id: dict[str, SheetRecords] = {}
    for line in document.lines:
        if isinstance(line, SheetLine):
            sheet = SheetRecords(
                name=line.name,
                header_row=line.header_row,
                header_row_count=line.header_row_count,
                header_detection=line.header_detection,
                mode=line.mode,
                columns=line.columns,
                diagnostics=[
                    SheetDiagnostic(code=item.code, cell=item.cell, detail=item.detail)
                    for item in line.diagnostics
                ],
            )
            sheets.append(sheet)
            by_id[line.record_id] = sheet
            continue
        sheet = by_id[str(line.parent_id)]
        if isinstance(line, PreambleLine):
            sheet.preamble.append(
                SheetPreambleRow(
                    row_number=line.location.row_start,
                    cell_range=line.location.cell_range or "",
                    text=line.text,
                )
            )
        elif isinstance(line, ProcedureStepLine):
            sheet.blocks.append(
                SheetBlock(
                    kind="procedure_step",
                    row_start=line.location.row_start,
                    row_end=line.location.row_end,
                    cell_range=line.location.cell_range or "",
                    section_path=line.section_path,
                    lines=line.lines,
                )
            )
        else:
            sheet.blocks.append(
                SheetBlock(
                    kind="row",
                    row_start=line.location.row_start,
                    row_end=line.location.row_end,
                    cell_range=line.location.cell_range or "",
                    section_path=line.section_path,
                    values={item.name: item.value for item in line.fields},
                )
            )
    source_format: Literal["xlsx", "xls"] = "xls" if document.source_type == "xls" else "xlsx"
    return SheetRecordsDocument(
        source_format=source_format,
        sheets=sheets,
        skipped_sheets=[
            SkippedSheet(name=item.name, reason=item.reason) for item in document.document.skipped
        ],
    )


# ---- 要素化 -----------------------------------------------------------------------------


def _csv_row_text(line: RowLine) -> str:
    text = " / ".join(f"{item.name}: {item.value}" for item in line.fields if item.value.strip())
    return text[:_RECORD_TEXT_MAX_CHARS]


def _csv_extraction(
    document: StructuredRecordsDocument,
    *,
    source_parser: str,
    parser_backend: str,
    parser_version: str,
) -> StructuredExtraction:
    """CSV の 1 データ行を 1 要素（列名つきの値。原本の物理行の位置）にする。"""
    elements: list[DocumentElement] = []
    for line in document.lines:
        if not isinstance(line, RowLine):
            continue
        text = _csv_row_text(line)
        if not text:
            continue
        elements.append(
            DocumentElement(
                kind="text",
                text=text,
                order=len(elements),
                element_id=line.record_id,
                content_kind=SHEET_RECORD_CONTENT_KIND,
                source_parser=source_parser,
                section_path=line.section_path,
                confidence=1.0,
                metadata={
                    STRUCTURED_RECORD_ID_KEY: line.record_id,
                    "row_start": line.location.row_start,
                    "row_end": line.location.row_end,
                    "record_kind": "row",
                    **(
                        {"csv_header_row": document.document.header_row}
                        if document.document.header_row is not None
                        else {}
                    ),
                },
            )
        )
    return StructuredExtraction(
        raw_text="\n".join(element.text for element in elements),
        document_type="SPREADSHEET",
        confidence=1.0,
        warnings=[],
        elements=elements,
        parser_artifacts={
            "source_parser": source_parser,
            "parser_backend": parser_backend,
            "parser_version": parser_version,
            "source_format": "csv",
            "record_count": len(elements),
        },
    )


def structured_records_extraction(
    document: StructuredRecordsDocument,
    *,
    source_parser: str,
    parser_backend: str,
    parser_version: str,
) -> StructuredExtraction:
    """構造化レコードの JSONL を、記録を 1 つずつ要素にした抽出にする。"""
    if document.source_type in _SPREADSHEET_SOURCE_TYPES:
        extraction = sheet_records_extraction(
            structured_records_to_sheet_records(document),
            source_parser=source_parser,
            parser_backend=parser_backend,
            parser_version=parser_version,
        )
        # 要素の ID は記録の ID と同じ（`sheet_records_to_lines`）。JSONL の行と要素を対応させる。
        record_ids = {line.record_id for line in document.lines}
        extraction = extraction.model_copy(
            update={
                "elements": [
                    element.model_copy(
                        update={
                            "metadata": {
                                **element.metadata,
                                STRUCTURED_RECORD_ID_KEY: element.element_id,
                            }
                        }
                    )
                    if element.element_id in record_ids
                    else element
                    for element in extraction.elements
                ]
            }
        )
    else:
        extraction = _csv_extraction(
            document,
            source_parser=source_parser,
            parser_backend=parser_backend,
            parser_version=parser_version,
        )
    return extraction.model_copy(
        update={
            "parser_artifacts": {
                **extraction.parser_artifacts,
                STRUCTURED_RECORDS_ARTIFACT_KEY: STRUCTURED_RECORDS_SCHEMA_VERSION,
                "structured_records_converter": document.document.converter,
                "structured_records_converter_version": document.document.converter_version,
            }
        }
    )


__all__ = [
    "STRUCTURED_RECORDS_ARTIFACT_KEY",
    "STRUCTURED_RECORDS_CONTENT_TYPE",
    "STRUCTURED_RECORDS_FILE_EXTENSION",
    "STRUCTURED_RECORDS_SCHEMA",
    "STRUCTURED_RECORDS_SCHEMA_VERSION",
    "STRUCTURED_RECORD_ID_KEY",
    "ContentLine",
    "DocumentLine",
    "PreambleLine",
    "ProcedureStepLine",
    "RecordDiagnostic",
    "RecordField",
    "RecordLocation",
    "RowLine",
    "SheetLine",
    "SkippedPart",
    "StructuredRecordLine",
    "StructuredRecordsDocument",
    "StructuredRecordsError",
    "dump_structured_records",
    "is_structured_records_content_type",
    "parse_structured_records",
    "sheet_records_to_lines",
    "structured_records_extraction",
    "structured_records_to_sheet_records",
]
