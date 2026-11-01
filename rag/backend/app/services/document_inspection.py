"""保存済み抽出の閲覧範囲。原本と完全エクスポートは変更しない。"""

from __future__ import annotations

import hashlib
from collections.abc import Mapping
from typing import Any

TEXT_WINDOW = 8192
ELEMENT_WINDOW = 25


def text_window(text: str, offset: int, limit: int, focus: str | None = None) -> dict[str, Any]:
    if focus and (position := text.find(focus)) >= 0:
        offset = max(0, position - limit // 4)
    offset = min(offset, max(0, len(text) - 1))
    return {
        "text": text[offset : offset + limit],
        "offset": offset,
        "total": len(text),
        "limit": limit,
    }


def extraction_window(
    payload: Mapping[str, Any],
    *,
    mode: str,
    offset: int,
    limit: int,
    page: int | None = None,
    element_id: str | None = None,
    text_offset: int = 0,
    cell_offset: int = 0,
) -> dict[str, Any]:
    """サーバーで絞り、表示対象外の本文・要素・重複metadataを返さない。"""
    elements = list(payload.get("elements") or [])
    pages = list(payload.get("pages") or [])
    if page is not None:
        ids = {
            value
            for row in pages
            if row.get("page_number") == page
            for value in row.get("element_ids", [])
        }
        elements = [
            row
            for row in elements
            if row.get("page_number") == page
            or (row.get("page_number") is None and row.get("element_id") in ids)
        ]
    if element_id:
        index = ((payload.get("parser_artifacts") or {}).get("inspection_index") or {}).get(
            "elements"
        ) or {}
        position = index.get(element_id) if page is None else None
        if isinstance(position, int) and (
            position >= len(elements) or elements[position].get("element_id") != element_id
        ):
            position = None
        position = (
            position
            if isinstance(position, int)
            else next(
                (i for i, row in enumerate(elements) if row.get("element_id") == element_id), None
            )
        )
        if position is not None:
            offset = position // limit * limit
    total = len(elements)
    selected = elements[offset : offset + limit] if mode == "structure" else []
    raw = str(payload.get("display_text") or payload.get("raw_text") or "")
    if page is not None:
        raw = "\n\n".join(str(row.get("text") or "") for row in elements)
    window = text_window(raw, offset if mode == "text" else 0, TEXT_WINDOW)
    view = {
        key: payload[key] for key in ("document_type", "confidence", "warnings") if key in payload
    }
    text_total = max((len(str(row.get("text") or "")) for row in selected), default=0)
    truncated = False
    bounded = []
    for element in selected:
        element = dict(element)
        text = str(element.get("text") or "")
        if len(text) > TEXT_WINDOW:
            element["text"] = text[text_offset : text_offset + TEXT_WINDOW]
            element["metadata"] = {
                **(element.get("metadata") or {}),
                "preview_text_offset": text_offset,
                "preview_text_sha256": hashlib.sha256(text.encode()).hexdigest(),
            }
            truncated = True
        bounded.append(element)
    selected = bounded
    view["preview_truncated"] = truncated
    view.update(
        raw_text=window["text"] if mode == "text" else "",
        display_text=window["text"] if mode == "text" else "",
        elements=selected,
        pages=[row for row in pages if page is None or row.get("page_number") == page][:limit],
    )
    # 原文の重複を含む parser artifact は概要から必要なスカラーだけを返す。
    artifacts = payload.get("parser_artifacts") or {}
    view["parser_artifacts"] = {
        key: value
        for key, value in artifacts.items()
        if isinstance(value, (str, int, float, bool, type(None))) and len(str(value)) < TEXT_WINDOW
    }
    lineage = artifacts.get("source_derivation")
    if isinstance(lineage, Mapping):
        view["parser_artifacts"]["source_derivation"] = dict(lineage)
    tables = list(payload.get("tables") or []) if mode == "structure" else []
    if page is not None:
        tables = [
            row
            for row in tables
            if row.get("page_number") == page
            or row.get("element_id") in {e.get("element_id") for e in elements}
        ]
    cell_total = sum(len(row.get("cells") or []) for row in tables)
    visible_tables = []
    ordinal = 0
    for table in tables:
        cells = list(table.get("cells") or [])
        selected_cells = cells[
            max(0, cell_offset - ordinal) : max(0, cell_offset + limit - ordinal)
        ]
        ordinal += len(cells)
        if selected_cells:
            visible_tables.append({**table, "cells": selected_cells})
    view["tables"] = visible_tables
    for key in ("assets", "fields", "navigation"):
        values = list(payload.get(key) or []) if mode == "structure" else []
        if page is not None:
            values = [row for row in values if row.get("page_number") == page]
        view[key] = values[offset : offset + limit]
    return {
        "payload": view,
        "offset": window["offset"] if mode == "text" else offset,
        "total": window["total"]
        if mode == "text"
        else max(
            total,
            *(len(payload.get(key) or []) for key in ("tables", "assets", "fields", "navigation")),
        ),
        "limit": TEXT_WINDOW if mode == "text" else limit,
        "text_offset": text_offset,
        "text_total": text_total,
        "cell_offset": cell_offset,
        "cell_total": cell_total,
    }


def with_inspection_index(payload: Mapping[str, Any]) -> dict[str, Any]:
    positions = {
        str(row["element_id"]): i
        for i, row in enumerate(payload.get("elements") or [])
        if row.get("element_id")
    }
    if not positions:
        return dict(payload)
    artifacts = dict(payload.get("parser_artifacts") or {})
    artifacts["inspection_index"] = {"version": 1, "elements": positions}
    return {**payload, "parser_artifacts": artifacts}
