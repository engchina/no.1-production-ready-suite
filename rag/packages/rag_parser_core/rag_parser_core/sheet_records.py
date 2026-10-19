"""表計算ファイルの行の記録（前処理 `excel_to_jsonl` の中間の形）と、その要素化（#1221）。

前処理のサービスは Excel（.xlsx / .xls）を、シートごとの「表頭・元の行番号・セル範囲・表示の値」を
持つ行の記録（`SheetRecordsDocument`）にし、構造化レコードの JSONL
（`rag_parser_core.structured_records`。#2457）で parser に渡す。parser のサービス（どの adapter
でも）は、この JSONL を外部の parser に渡さず、`sheet_records_extraction` で記録の単位（block）を
1 つずつ要素にする。

- block は 3 種類（`row` / `procedure_step` は engchina/no.1-rag の前処理に合わせる）。
  - `row`: 表の 1 行（「列名: 値」）。
  - `procedure_step`: 手順書の 1 手順（番号と題名の行から次の手順までの複数行）。
  - `form_field`: 帳票（方眼紙）の 1 項目（「ラベル: 値」と、ラベル・値のセル。#2516）。
- 節はシート名と、表の題名（1 シートの複数の表を分けたとき）、手順なら章・手順の題名、帳票なら
  見出し（`section_path=[シート名, …]`）。表頭より上の行（説明など）は本文の要素にする。
  シート名だけの見出しの要素は作らない。
- 1 シートの複数の表（#2516）: 前処理が空の行・列で分けた表は、それぞれ別の `SheetRecords`
  （同じシート名・`table_index`・`region`）になる。親子階層の親は表をまたがない
  （metadata の `sheet_table_index`）。
- 要素の metadata に `sheet_name`・`row_start` / `row_end`・`cell_range`・`cell_column_start` /
  `cell_column_end`・`header_row` を持ち、chunking が chunk の場所にする。
- 分割（#1349。engchina/no.1-rag の「1 行 = 1 chunk」に合わせる）: 記録（`content_kind=record`）は
  1 つずつ単独の chunk にし、他の記録・前書きと結合せず、途中で切らない
  （`rag_pipeline_core.chunking`）。
  各 chunk の本文は表頭の列名つきの値（「列名: 値 / 列名: 値」。複数行の表頭は前処理が「 / 」で
  つないだ列名）で、シート名は節（`section_path`）として検索の文脈の見出しに入る。親子階層では、
  同じシート（手順書は同じ章）の続く記録をまとめた表の一部を親にする
  （backend の `app.rag.chunking_small_to_big`）。
- 選択肢（`ExcelOptions`）は Document Recipe の `excel_options` から前処理へ渡る。
- 図形の文字・選択の記号（#2526）: テキストボックス・図形の文字は、前処理がアンカーの範囲を 1 つの
  セルの単位にして読み、そのセル範囲を記録の `shape_cells` に残す。帳票の値の選択の記号
  （「■A □B」）は前処理が選ばれた選択肢（「A」）を値にし、原文を `value_original` に残す。
- 列の役割（`SheetColumn.role`。#1281）: 設定値の表の「説明・既定値・例示・現在値・推奨値・
  設定できる範囲」の列を、前処理が表頭の語（または選択肢の `column_roles`）で決める。値の列は
  本文で列名の後に役割の表示（`既定値［資料の既定値］: 30`）を付け、回答の生成が資料の既定値・
  例を利用者の環境の現在の値と取り違えないようにする。決められない列には役割を付けない（推測しない）。
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from rag_parser_core.extraction import DocumentElement, StructuredExtraction

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


# 列の役割（#1281）。definition=説明・定義、default=既定値（出荷時・初期値）、
# example=例示（記入例・設定例・サンプル）、current=記載時点の設定値（現在値・本番値）、
# recommended=推奨値、allowed=設定できる範囲・選択肢。
ColumnRole = Literal["definition", "default", "example", "current", "recommended", "allowed"]
COLUMN_ROLES: tuple[str, ...] = (
    "definition",
    "default",
    "example",
    "current",
    "recommended",
    "allowed",
)
# 選択肢の `column_roles` で「役割を付けない」を指定する値（表頭の語で決まる役割を打ち消す）。
COLUMN_ROLE_NONE = "none"
ColumnRoleSetting = Literal[
    "definition", "default", "example", "current", "recommended", "allowed", "none"
]
# 本文で列名の後に付ける役割の表示。値の列だけに付ける（説明の列は値ではないので付けない）。
# 「資料の」「記載時点の」は、資料に書かれた値で、利用者の環境の今の値ではないことを示す。
COLUMN_ROLE_TEXT_LABELS: dict[str, str] = {
    "default": "資料の既定値",
    "example": "例示の値",
    "current": "記載時点の設定値",
    "recommended": "資料の推奨値",
    "allowed": "設定できる範囲",
}


def column_display_name(name: str, role: str | None) -> str:
    """本文に出す列名（値の列は役割の表示を付ける。例: ``既定値［資料の既定値］``）。"""
    label = COLUMN_ROLE_TEXT_LABELS.get(role or "")
    if not label or name.strip() == label:
        return name
    return f"{name}［{label}］"


class ExcelOptions(BaseModel):
    """Excel の前処理の選択肢（Document Recipe の `excel_options`）。"""

    model_config = ConfigDict(extra="forbid")

    mode: Literal["auto", "table", "procedure", "form"] = Field(
        default="auto",
        description=(
            "シートの読み方。auto は帳票（狭い列の格子と結合セルにラベルと値を散らした版式）なら"
            " form、手順書（番号と題名の列）なら procedure、密な表なら table に決める。"
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
    include_hidden_cells: bool = Field(
        default=False,
        description=(
            "非表示の行・列のセルも読むか（#2526）。既定は読まない（オートフィルターで隠れた行は読む）。"
        ),
    )
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

    split_tables: bool = Field(
        default=True,
        description=(
            "1 つのシートの、空の行・空の列で分かれた複数の表を別々に読むか（#2516）。"
            "表ごとに表頭を推定する。"
        ),
    )
    table_gap_rows: int = Field(
        default=1, ge=1, le=20, description="表を分ける空の行の数（これ以上続いたら別の表）。"
    )
    table_gap_columns: int = Field(
        default=1, ge=1, le=20, description="表を分ける空の列の数（これ以上続いたら別の表）。"
    )
    form_detection_threshold: float = Field(
        default=0.6,
        ge=0.0,
        le=1.0,
        description=(
            "auto で帳票（form）と判定する点数の下限（列幅の狭さ・結合セルの割合・行の列の"
            "不揃い・ラベルと値の隣接から計算する。#2516）。"
        ),
    )

    column_role_detection: Literal["auto", "off"] = Field(
        default="auto",
        description=(
            "列の役割（説明・既定値・例示・現在値・推奨値・設定できる範囲）を表頭の語から"
            "決めるか（#1281）。auto は語が一致した列だけに付け、あいまいな列には付けない。"
            "off は column_roles の指定だけを使う。"
        ),
    )
    column_roles: dict[str, ColumnRoleSetting] = Field(
        default_factory=dict,
        max_length=200,
        description=(
            "列の役割の指定（列名か列の記号 → 役割。none は役割を付けない）。"
            "表頭の語の判定より優先する。"
        ),
    )

    @field_validator("column_roles")
    @classmethod
    def _clean_column_roles(cls, value: dict[str, str]) -> dict[str, str]:
        return {key.strip(): role for key, role in value.items() if key.strip()}

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


SheetLayout = Literal["table", "procedure", "form"]


class LayoutDetection(BaseModel):
    """シートの版式の判定（#2516）。

    ``confidence`` は ``layout`` の確からしさ（帳票は帳票の点数、表・手順書は 1 − 帳票の点数）。
    ``features`` は判定に使った構造の特徴（0〜1）。
    """

    layout: SheetLayout
    method: Literal["configured", "detected"]
    confidence: float = Field(ge=0.0, le=1.0)
    reason: str
    features: dict[str, float] = Field(default_factory=dict)


class SheetColumn(BaseModel):
    name: str
    column: str = Field(description="列の記号（A, B, …）。")
    role: ColumnRole | None = Field(
        default=None, description="列の役割（決められない列は None。#1281）。"
    )
    role_method: Literal["configured", "detected"] | None = Field(
        default=None, description="役割の決め方（選択肢の指定か、表頭の語の判定か）。"
    )
    role_term: str | None = Field(
        default=None, description="役割を決めた表頭の語（判定のとき）か、指定の key。"
    )


class SheetPreambleRow(BaseModel):
    row_number: int = Field(ge=1)
    cell_range: str
    text: str
    shape_cells: list[str] = Field(
        default_factory=list, description="図形・テキストボックスの文字を置いたセル範囲（#2526）。"
    )


class SheetBlock(BaseModel):
    """記録の単位（表の 1 行・手順書の 1 手順・帳票の 1 項目）。"""

    kind: Literal["row", "procedure_step", "form_field"]
    row_start: int = Field(ge=1, description="シートの元の行番号（1 始まり）。")
    row_end: int = Field(ge=1)
    cell_range: str = Field(description="セルの範囲（例: A5:F5、A5:F9）。")
    section_path: list[str] = Field(
        default_factory=list, description="手順の章・題名（表の行は空）。"
    )
    values: dict[str, str] = Field(default_factory=dict, description="表の行の列名と値。")
    lines: list[str] = Field(default_factory=list, description="手順の本文の行。")
    label: str | None = Field(
        default=None, description="帳票の項目のラベル（ラベルの無い文は None）。"
    )
    value: str = Field(default="", description="帳票の項目の値。")
    label_cell: str | None = Field(default=None, description="ラベルのセル（例: B3:E3）。")
    value_cell: str | None = Field(default=None, description="値のセル（例: F3:N3）。")
    value_original: str | None = Field(
        default=None,
        description=("選択の記号を読んで値を選ばれた選択肢にしたときの原文（例: ■A □B。#2526）。"),
    )
    shape_cells: list[str] = Field(
        default_factory=list,
        description="記録のうち、図形・テキストボックスの文字を置いたセル範囲（#2526）。",
    )


class SheetDiagnostic(BaseModel):
    code: str
    cell: str | None = None
    detail: str | None = None


class SheetRecords(BaseModel):
    name: str
    header_row: int | None = Field(default=None, ge=1)
    header_row_count: int = 1
    header_detection: HeaderDetection | None = None
    mode: SheetLayout = "table"
    layout_detection: LayoutDetection | None = None
    table_index: int | None = Field(
        default=None, ge=1, description="1 シートを複数の表に分けたときの表の番号（1 始まり）。"
    )
    region: str | None = Field(default=None, description="分けた表のセル範囲（例: A12:F30）。")
    title: str | None = Field(default=None, description="分けた表の題名（表頭より上の最初の行）。")
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


def _block_text(
    block: SheetBlock,
    roles: Mapping[str, str] | None = None,
    *,
    max_chars: int | None = _BLOCK_TEXT_MAX_CHARS,
) -> str:
    """記録の本文（``max_chars`` が None なら切らない。大きさの判定に使う）。"""
    if block.kind == "form_field":
        text = f"{block.label}: {block.value}" if block.label else block.value
    elif block.kind == "procedure_step":
        # 手順の行は前処理が役割の表示を付けた列名で作る。
        text = "\n".join(line for line in block.lines if line.strip())
    else:
        roles = roles or {}
        text = " / ".join(
            f"{column_display_name(name, roles.get(name))}: {value}"
            for name, value in block.values.items()
            if value.strip()
        )
    return text if max_chars is None else text[:max_chars]


def _column_roles_metadata(roles: Mapping[str, str]) -> str | None:
    """要素の metadata に残す列の役割（``列名=役割`` を ``; `` でつなぐ）。

    要素の metadata は JSON の scalar だけを持てるので、1 つの文字列にする。
    """
    if not roles:
        return None
    return "; ".join(f"{name}={role}" for name, role in roles.items())


def _column_bounds(cell_range: str) -> tuple[str | None, str | None]:
    start, _, end = cell_range.partition(":")
    letters_start = "".join(char for char in start if char.isalpha()) or None
    letters_end = "".join(char for char in (end or start) if char.isalpha()) or None
    return letters_start, letters_end


def sheet_block_record_id(sheet_id: str, block: SheetBlock, mode: str) -> str:
    """記録の ID（要素の ID と JSONL の record_id）。

    表・手順書は行ごとに 1 つの記録なので ``<シート>-r<行>``。帳票は 1 行に複数の項目があるので
    先頭の列も付ける（``<シート>-r<行>-<列>``）。
    """
    if mode != "form" and block.kind != "form_field":
        return f"{sheet_id}-r{block.row_start}"
    column, _ = _column_bounds(block.cell_range)
    return f"{sheet_id}-r{block.row_start}-{column or 'A'}"


def sheet_section_path(sheet: SheetRecords) -> list[str]:
    """シートの記録の節の先頭（シート名と、分けた表の題名）。"""
    return [sheet.name, *([sheet.title] if sheet.title else [])]


def _sheet_metadata(sheet: SheetRecords) -> dict[str, str | int]:
    metadata: dict[str, str | int] = {"sheet_layout": sheet.mode}
    if sheet.table_index is not None:
        metadata["sheet_table_index"] = sheet.table_index
    if sheet.region:
        metadata["sheet_region"] = sheet.region
    return metadata


def _shape_metadata(shape_cells: list[str]) -> dict[str, str]:
    """図形の文字を置いたセル範囲（要素の metadata は scalar だけなので ``; `` でつなぐ）。"""
    return {"sheet_shape_cells": "; ".join(shape_cells)} if shape_cells else {}


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
        section_path = sheet_section_path(sheet)
        sheet_metadata = _sheet_metadata(sheet)
        roles = {column.name: column.role for column in sheet.columns if column.role}
        roles_metadata = _column_roles_metadata(roles)
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
                        **sheet_metadata,
                        **_shape_metadata(preamble.shape_cells),
                    },
                )
            )
            raw_lines.append(preamble.text)
            order += 1
        for block in sheet.blocks:
            text = _block_text(block, roles)
            if not text:
                continue
            column_start, column_end = _column_bounds(block.cell_range)
            elements.append(
                DocumentElement(
                    kind="text",
                    text=text,
                    order=order,
                    element_id=sheet_block_record_id(heading_id, block, sheet.mode),
                    content_kind=SHEET_RECORD_CONTENT_KIND,
                    source_parser=source_parser,
                    section_path=[*section_path, *block.section_path],
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
                        **sheet_metadata,
                        **({"sheet_column_roles": roles_metadata} if roles_metadata else {}),
                        **({"form_label_cell": block.label_cell} if block.label_cell else {}),
                        **({"form_value_cell": block.value_cell} if block.value_cell else {}),
                        **(
                            {"form_value_original": block.value_original}
                            if block.value_original
                            else {}
                        ),
                        **_shape_metadata(block.shape_cells),
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
            "form_field_count": sum(
                1
                for sheet in document.sheets
                for block in sheet.blocks
                if block.kind == "form_field"
            ),
            "skipped_sheet_count": len(document.skipped_sheets),
            "sheet_diagnostics": "\n".join(diagnostics)[:4000],
        },
    )


__all__ = [
    "COLUMN_ROLES",
    "COLUMN_ROLE_NONE",
    "COLUMN_ROLE_TEXT_LABELS",
    "EXCEL_HEADER_LOW_CONFIDENCE_WARNING",
    "SHEET_RECORDS_FORMAT",
    "SHEET_RECORDS_FORMAT_VERSION",
    "SHEET_RECORD_CONTENT_KIND",
    "ColumnRole",
    "ColumnRoleSetting",
    "ExcelOptions",
    "ExcelRange",
    "HeaderDetection",
    "LayoutDetection",
    "SheetColumn",
    "SheetDiagnostic",
    "SheetPreambleRow",
    "SheetRecords",
    "SheetRecordsDocument",
    "SheetBlock",
    "SheetLayout",
    "SkippedSheet",
    "column_display_name",
    "parse_excel_range",
    "sheet_block_record_id",
    "sheet_records_extraction",
    "sheet_section_path",
]
