"""会話の添付を読むツールの中核（3 製品共通。#2517）。

名前と結果の形は Issue #2517 で固定した契約（後の「出力 schema・抽出→照合のテンプレート」が使う）。
ツールは渡された会話の添付だけを読む（会話の権限の判定は製品が先に行う）。

- `chat_attachment_list`: 添付の索引（節・シート）。
- `chat_attachment_read_section`: 節の本文を `READ_SECTION_CHARACTERS` 文字ずつ。
- `chat_attachment_read_rows`: シートの行を「列名 → 値」で `READ_ROWS_MAX` 行まで。
- `chat_attachment_find`: 文字の一致で節と行を探す。
"""

from __future__ import annotations

import unicodedata
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Final

from .models import (
    TABULAR_KINDS,
    ChatAttachmentRecord,
    ParsedAttachment,
    ParsedSheet,
    SourceLocation,
)

LIST_TOOL_NAME: Final = "chat_attachment_list"
READ_SECTION_TOOL_NAME: Final = "chat_attachment_read_section"
READ_ROWS_TOOL_NAME: Final = "chat_attachment_read_rows"
FIND_TOOL_NAME: Final = "chat_attachment_find"
CHAT_ATTACHMENT_TOOL_NAMES: Final = (
    LIST_TOOL_NAME,
    READ_SECTION_TOOL_NAME,
    READ_ROWS_TOOL_NAME,
    FIND_TOOL_NAME,
)
READ_SECTION_CHARACTERS: Final = 8_000
READ_ROWS_MAX: Final = 200
FIND_MAX: Final = 50
_SNIPPET_CHARACTERS: Final = 160


@dataclass(frozen=True)
class ChatAttachmentToolSpec:
    name: str
    description: str
    input_schema: dict[str, Any]


_ATTACHMENT_ID = {"type": "string", "description": "chat_attachment_list の attachment_id。"}
CHAT_ATTACHMENT_TOOL_SPECS: Final[tuple[ChatAttachmentToolSpec, ...]] = (
    ChatAttachmentToolSpec(
        name=LIST_TOOL_NAME,
        description=(
            "この会話に利用者が添付したファイルの一覧と、各ファイルの節（section_id・見出し・頁・文字数）"
            "と表のシート（列名・行数）を返す。本文は返さない。"
        ),
        input_schema={
            "type": "object",
            "properties": {},
            "required": [],
            "additionalProperties": False,
        },
    ),
    ChatAttachmentToolSpec(
        name=READ_SECTION_TOOL_NAME,
        description=(
            f"添付ファイルの 1 つの節の本文を {READ_SECTION_CHARACTERS} 文字ずつ返す。"
            "続きがあれば next_offset を返す。"
        ),
        input_schema={
            "type": "object",
            "properties": {
                "attachment_id": _ATTACHMENT_ID,
                "section_id": {
                    "type": "string",
                    "description": "chat_attachment_list の section_id。",
                },
                "offset": {
                    "type": "integer",
                    "minimum": 0,
                    "description": "読み始める文字の位置。最初は 0、続きは前の結果の next_offset。",
                },
            },
            "required": ["attachment_id", "section_id", "offset"],
            "additionalProperties": False,
        },
    ),
    ChatAttachmentToolSpec(
        name=READ_ROWS_TOOL_NAME,
        description=(
            "添付の Excel・CSV のシートの行を「列名 → 値」で返す。"
            "続きがあれば next_start_row を返す。"
        ),
        input_schema={
            "type": "object",
            "properties": {
                "attachment_id": _ATTACHMENT_ID,
                "sheet": {"type": "string", "description": "chat_attachment_list の sheet。"},
                "start_row": {
                    "type": "integer",
                    "minimum": 1,
                    "description": "最初の行の番号（列名の行を除いたデータの行。1 始まり）。",
                },
                "limit": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": READ_ROWS_MAX,
                    "description": f"返す行の数（1〜{READ_ROWS_MAX}）。",
                },
            },
            "required": ["attachment_id", "sheet", "start_row", "limit"],
            "additionalProperties": False,
        },
    ),
    ChatAttachmentToolSpec(
        name=FIND_TOOL_NAME,
        description=(
            "添付ファイルの本文と表の行から、文字が一致する箇所（節・行と前後の文）を探す。"
        ),
        input_schema={
            "type": "object",
            "properties": {
                "text": {
                    "type": "string",
                    "minLength": 1,
                    "description": "探す文字（大文字と小文字・全角と半角は区別しない）。",
                },
                "attachment_id": {
                    "type": ["string", "null"],
                    "description": "探す添付ファイル。null なら会話のすべての添付。",
                },
                "limit": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": FIND_MAX,
                    "description": f"返す箇所の数（1〜{FIND_MAX}）。",
                },
            },
            "required": ["text", "attachment_id", "limit"],
            "additionalProperties": False,
        },
    ),
)


class ChatAttachmentToolError(ValueError):
    """モデルが読んで呼び直せる、引数の誤り。"""


# Excel だけの索引の項目（#2544）。値の無い添付（Excel 以外・古い索引）では一覧に出さない。
_EXCEL_SECTION_KEYS: Final = ("sheet",)
_EXCEL_SHEET_KEYS: Final = ("layout", "source_sheet", "region")


def _without_empty(item: dict[str, Any], keys: Sequence[str]) -> dict[str, Any]:
    return {key: value for key, value in item.items() if key not in keys or value is not None}


def _location_fields(location: SourceLocation | None) -> dict[str, Any]:
    """Excel の元の場所（#2544。行番号・セル範囲、帳票はラベルと値のセル）。無ければ空。"""
    if location is None:
        return {}
    return location.model_dump(mode="json", exclude_none=True)


def _row_location(sheet: ParsedSheet, index: int) -> SourceLocation | None:
    """シートの `index` 番目（0 始まり）の行の元の場所。"""
    locations = sheet.locations or []
    return locations[index] if index < len(locations) else None


def _normalized(text: str) -> str:
    return unicodedata.normalize("NFKC", text).casefold()


def _int_argument(
    arguments: Mapping[str, Any], name: str, *, minimum: int, maximum: int | None = None
) -> int:
    value = arguments.get(name)
    if isinstance(value, bool) or not isinstance(value, int):
        raise ChatAttachmentToolError(f"{name} は整数で指定してください。")
    if value < minimum or (maximum is not None and value > maximum):
        upper = f"{maximum} 以下" if maximum is not None else ""
        raise ChatAttachmentToolError(f"{name} は {minimum} 以上{upper}で指定してください。")
    return value


def _str_argument(arguments: Mapping[str, Any], name: str) -> str:
    value = arguments.get(name)
    if not isinstance(value, str) or not value.strip():
        raise ChatAttachmentToolError(f"{name} を指定してください。")
    return value.strip()


class ChatAttachmentToolset:
    """会話の添付を読むツール（`records` はその会話の期限内の添付）。"""

    def __init__(
        self,
        records: Sequence[ChatAttachmentRecord],
        load_parsed: Callable[[str], ParsedAttachment | None],
    ) -> None:
        self._records = {record.attachment_id: record for record in records}
        self._order = [record.attachment_id for record in records]
        self._load_parsed = load_parsed
        self._parsed: dict[str, ParsedAttachment] = {}

    @property
    def records(self) -> list[ChatAttachmentRecord]:
        return [self._records[attachment_id] for attachment_id in self._order]

    def __bool__(self) -> bool:
        return bool(self._order)

    def parsed(self, attachment_id: str) -> ParsedAttachment:
        if attachment_id not in self._records:
            raise ChatAttachmentToolError(
                "この会話に無い attachment_id です。"
                "chat_attachment_list の attachment_id を指定してください。"
            )
        cached = self._parsed.get(attachment_id)
        if cached is None:
            loaded = self._load_parsed(attachment_id)
            if loaded is None:
                raise ChatAttachmentToolError("添付ファイルの期限が切れたか、削除されています。")
            cached = self._parsed[attachment_id] = loaded
        return cached

    def call(self, name: str, arguments: Mapping[str, Any]) -> dict[str, Any]:
        if name == LIST_TOOL_NAME:
            return self.list_attachments()
        if name == READ_SECTION_TOOL_NAME:
            return self.read_section(
                _str_argument(arguments, "attachment_id"),
                _str_argument(arguments, "section_id"),
                _int_argument(arguments, "offset", minimum=0),
            )
        if name == READ_ROWS_TOOL_NAME:
            return self.read_rows(
                _str_argument(arguments, "attachment_id"),
                _str_argument(arguments, "sheet"),
                _int_argument(arguments, "start_row", minimum=1),
                _int_argument(arguments, "limit", minimum=1, maximum=READ_ROWS_MAX),
            )
        if name == FIND_TOOL_NAME:
            attachment_id = arguments.get("attachment_id")
            if attachment_id is not None and not isinstance(attachment_id, str):
                raise ChatAttachmentToolError("attachment_id は文字列か null で指定してください。")
            return self.find(
                _str_argument(arguments, "text"),
                attachment_id=attachment_id or None,
                limit=_int_argument(arguments, "limit", minimum=1, maximum=FIND_MAX),
            )
        raise ChatAttachmentToolError(f"{name} は添付のツールではありません。")

    def list_attachments(self) -> dict[str, Any]:
        return {
            "attachments": [
                {
                    "attachment_id": record.attachment_id,
                    "file_name": record.file_name,
                    "kind": record.kind,
                    "size_bytes": record.size_bytes,
                    "warnings": list(record.outline.warnings),
                    "sections": [
                        _without_empty(section.model_dump(mode="json"), _EXCEL_SECTION_KEYS)
                        for section in record.outline.sections
                    ],
                    "sheets": [
                        _without_empty(sheet.model_dump(mode="json"), _EXCEL_SHEET_KEYS)
                        for sheet in record.outline.sheets
                    ],
                }
                for record in self.records
            ]
        }

    def read_section(self, attachment_id: str, section_id: str, offset: int) -> dict[str, Any]:
        parsed = self.parsed(attachment_id)
        section = next((item for item in parsed.sections if item.section_id == section_id), None)
        if section is None:
            raise ChatAttachmentToolError(
                "この添付に無い section_id です。"
                "chat_attachment_list の section_id を指定してください。"
            )
        content = section.text[offset : offset + READ_SECTION_CHARACTERS]
        end = offset + len(content)
        return {
            "attachment_id": attachment_id,
            "file_name": self._records[attachment_id].file_name,
            "section_id": section.section_id,
            "title": section.title,
            "page": section.page,
            **({"sheet": section.sheet} if section.sheet else {}),
            **_location_fields(section.location),
            "content": content,
            "next_offset": end if end < len(section.text) else None,
        }

    def _sheet(self, attachment_id: str, sheet_name: str) -> ParsedSheet:
        record = self._records.get(attachment_id)
        if record is not None and record.kind not in TABULAR_KINDS:
            raise ChatAttachmentToolError(
                "この添付は表（Excel・CSV）ではありません。"
                "chat_attachment_read_section で読んでください。"
            )
        parsed = self.parsed(attachment_id)
        sheet = next((item for item in parsed.sheets if item.sheet == sheet_name), None)
        if sheet is None:
            names = "、".join(item.sheet for item in parsed.sheets)
            raise ChatAttachmentToolError(f"この添付に無いシートです。シート: {names}")
        return sheet

    def read_rows(
        self, attachment_id: str, sheet_name: str, start_row: int, limit: int
    ) -> dict[str, Any]:
        sheet = self._sheet(attachment_id, sheet_name)
        start = start_row - 1
        rows = sheet.rows[start : start + limit]
        end = start + len(rows)
        return {
            "attachment_id": attachment_id,
            "file_name": self._records[attachment_id].file_name,
            "sheet": sheet.sheet,
            "columns": list(sheet.columns),
            "rows": [
                {
                    "row_number": start + index + 1,
                    **_location_fields(_row_location(sheet, start + index)),
                    "values": dict(zip(sheet.columns, row, strict=False)),
                }
                for index, row in enumerate(rows)
            ],
            "row_count": len(sheet.rows),
            "next_start_row": end + 1 if end < len(sheet.rows) else None,
        }

    def find(self, text: str, *, attachment_id: str | None, limit: int) -> dict[str, Any]:
        needle = _normalized(text)
        targets = [attachment_id] if attachment_id else list(self._order)
        matches: list[dict[str, Any]] = []
        truncated = False
        for target in targets:
            parsed = self.parsed(target)
            file_name = self._records[target].file_name
            for section in parsed.sections:
                haystack = _normalized(section.text)
                position = haystack.find(needle)
                while position >= 0:
                    if len(matches) >= limit:
                        truncated = True
                        break
                    matches.append(
                        {
                            "attachment_id": target,
                            "file_name": file_name,
                            "section_id": section.section_id,
                            "sheet": section.sheet,
                            "row_number": None,
                            **_location_fields(section.location),
                            "snippet": _snippet(section.text, position, len(text)),
                        }
                    )
                    position = haystack.find(needle, position + max(len(needle), 1))
                if truncated:
                    break
            for sheet in parsed.sheets if not truncated else []:
                for index, row in enumerate(sheet.rows, start=1):
                    line = " / ".join(
                        f"{column}: {value}"
                        for column, value in zip(sheet.columns, row, strict=False)
                        if value is not None
                    )
                    position = _normalized(line).find(needle)
                    if position < 0:
                        continue
                    if len(matches) >= limit:
                        truncated = True
                        break
                    matches.append(
                        {
                            "attachment_id": target,
                            "file_name": file_name,
                            "section_id": None,
                            "sheet": sheet.sheet,
                            "row_number": index,
                            **_location_fields(_row_location(sheet, index - 1)),
                            "snippet": _snippet(line, position, len(text)),
                        }
                    )
                if truncated:
                    break
            if truncated:
                break
        return {"matches": matches, "truncated": truncated}

    def index_lines(self) -> list[str]:
        """モデルへ渡す索引（1 つの添付 = 1 行。本文は入れない）。"""
        lines: list[str] = []
        for record in self.records:
            parts = [f"attachment_id={record.attachment_id}", f"種類={record.kind}"]
            if record.outline.sections:
                parts.append(f"節 {len(record.outline.sections)}")
            for sheet in record.outline.sheets:
                parts.append(f"シート「{sheet.sheet}」{sheet.row_count} 行")
            lines.append(f"- {record.file_name}（{'、'.join(parts)}）")
        return lines


def _snippet(text: str, position: int, length: int) -> str:
    half = max((_SNIPPET_CHARACTERS - length) // 2, 20)
    start = max(position - half, 0)
    end = min(position + length + half, len(text))
    snippet = text[start:end].replace("\n", " ")
    return ("…" if start > 0 else "") + snippet + ("…" if end < len(text) else "")
