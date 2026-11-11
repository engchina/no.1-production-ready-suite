"""保存済みの構造化JSONLを同じ版の読取窓へ投影する。位置は変換器の記録だけを使う。"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from typing import Any

from rag_parser_core.structured_records import (
    STRUCTURED_RECORDS_ARTIFACT_KEY,
    FormFieldLine,
    PassageLine,
    RowLine,
    SheetLine,
    StructuredRecordsError,
    WordTable,
    parse_structured_records,
    structured_records_extraction,
    word_passage_text,
)

from app.api.errors import SourceVersionStaleError
from app.services.source_reader import MAX_READER_CHARS


def has_structured_source_mapping(payload: Mapping[str, Any]) -> bool:
    artifacts = payload.get("parser_artifacts") or {}
    return (
        isinstance(artifacts, Mapping)
        and STRUCTURED_RECORDS_ARTIFACT_KEY in artifacts
        and isinstance(artifacts.get("source_derivation"), Mapping)
        and bool(artifacts["source_derivation"].get("derived_sha256"))
    )


def enrich_structured_window(
    window: dict[str, Any], payload: Mapping[str, Any], data: bytes
) -> dict[str, Any]:
    """hashとrecord ID/本文が一致した記録だけを表示する。長い部分窓は既存の文字表示を保つ。"""
    if not has_structured_source_mapping(payload):
        return window
    derivation = (payload.get("parser_artifacts") or {}).get("source_derivation") or {}
    expected = derivation.get("derived_sha256")
    if not expected or hashlib.sha256(data).hexdigest() != expected:
        raise SourceVersionStaleError("資料の版が変わりました。開き直してください。")
    document = parse_structured_records(data)
    if isinstance(document, StructuredRecordsError):
        raise SourceVersionStaleError("保存された構造化資料の版を確認できません。")
    rebuilt = structured_records_extraction(
        document,
        source_parser="structured_records_jsonl",
        parser_backend="structured_records_jsonl",
        parser_version="reader",
    )
    native = {element.element_id: element for element in rebuilt.elements}
    texts = {key: element.text for key, element in native.items()}
    saved = {element.get("element_id"): element for element in payload.get("elements", [])}
    records = {line.record_id: line for line in document.lines}
    sheets = {line.record_id: line for line in document.lines if isinstance(line, SheetLine)}
    blocks = []
    for block in window["blocks"]:
        element = saved.get(block["id"])
        record = records.get(block["id"])
        if not element or record is None or texts.get(block["id"]) != element.get("text"):
            blocks.append(block)
            continue
        metadata = native[block["id"]].metadata
        location = {
            key: metadata[key]
            for key in (
                "sheet_name",
                "row_start",
                "row_end",
                "cell_range",
                "source_line_start",
                "source_line_end",
                "table_row_start",
                "table_row_end",
                "word_paragraph_start",
                "word_paragraph_end",
                "word_body_start",
                "word_body_end",
                "word_table_indices",
                "word_location_basis",
            )
            if metadata.get(key) is not None
        }
        parts: list[dict[str, Any]] = []
        full = block["offset"] == 0 and block["text"] == element.get("text")
        if full and isinstance(record, RowLine):
            fields = sorted(record.fields, key=lambda field: field.position)
            parts.append(
                {
                    "kind": "table",
                    "columns": [field.name for field in fields],
                    "rows": [
                        {
                            "label": str(record.location.record or record.location.row_start),
                            "cells": [field.value for field in fields],
                        }
                    ],
                }
            )
            parts.extend({"kind": "text", "text": note} for note in record.notes or [])
        elif full and isinstance(record, FormFieldLine):
            parts.append(
                {
                    "kind": "table",
                    "columns": [record.label or ""],
                    "rows": [
                        {
                            "label": record.value_cell or str(record.location.row_start),
                            "cells": [record.value_original or record.value],
                        }
                    ],
                }
            )
        elif full and isinstance(record, PassageLine):
            for part in record.blocks:
                if isinstance(part, WordTable):
                    width = max(map(len, part.rows))
                    columns = [
                        " / ".join(
                            row[index] for row in part.rows[: part.header_rows] if index < len(row)
                        )
                        if part.header_rows
                        else str(index + 1)
                        for index in range(width)
                    ]
                    body = part.rows[part.header_rows :] if part.header_rows else part.rows
                    parts.append(
                        {
                            "kind": "table",
                            "columns": columns,
                            "rows": [
                                {
                                    "label": str(part.row_start + part.header_rows + i),
                                    "cells": cells,
                                }
                                for i, cells in enumerate(body)
                            ],
                        }
                    )
                else:
                    parts.append(
                        {
                            "kind": "text",
                            "text": word_passage_text(
                                record.model_copy(update={"blocks": [part], "section_path": []})
                            ),
                        }
                    )
        sheet = sheets.get(getattr(record, "parent_id", "") or "")
        if sheet and "sheet_name" not in location:
            location["sheet_name"] = sheet.name
        if len(json.dumps(parts, ensure_ascii=False)) > MAX_READER_CHARS:
            parts = []
        blocks.append(
            {
                **block,
                "record": {
                    "source_type": document.source_type,
                    "location": location,
                    "parts": parts,
                    "precision": "record",
                },
            }
        )
    return {**window, "blocks": blocks}
