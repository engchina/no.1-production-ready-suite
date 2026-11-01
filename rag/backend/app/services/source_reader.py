"""同一解析版の原要素を、引用位置から有界に読む。"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import json
from collections.abc import Mapping
from typing import Any

from fastapi import HTTPException
from pr_backend_core.source_text import read_source_window

from app.clients.oracle import OracleClient
from app.rag.element_locator import chunk_element_ids

MAX_READER_CHARS = 8192
MAX_READER_BLOCKS = 9


def _path(element: Mapping[str, Any]) -> tuple[str, ...]:
    metadata = element.get("metadata") or {}
    value = (
        element.get("section_path")
        or metadata.get("section_path")
        or metadata.get("heading_path")
        or []
    )
    return (
        tuple(str(v) for v in value)
        if isinstance(value, list)
        else tuple(str(value).split(" > "))
        if value
        else ()
    )


def _section_id(path: tuple[str, ...]) -> str:
    return hashlib.sha256(json.dumps(path, ensure_ascii=False).encode()).hexdigest()[:24]


def _cursor(revision: str, index: int, offset: int, section: str | None) -> str:
    return (
        base64.urlsafe_b64encode(
            json.dumps([revision, index, offset, section], separators=(",", ":")).encode()
        )
        .decode()
        .rstrip("=")
    )


def reading_window(
    payload: Mapping[str, Any],
    *,
    revision: str,
    anchor_ids: list[str],
    quote: str,
    cursor: str | None = None,
    direction: str = "after",
    scope: str = "paragraph",
    section_id: str | None = None,
    max_chars: int = MAX_READER_CHARS,
) -> dict[str, Any]:
    """原要素の順序を保ち、文字数・block 数を制限する。cursor はこの版だけで有効。"""
    max_chars = min(MAX_READER_CHARS, max(1, max_chars))
    elements = list(payload.get("elements") or [])
    # 配列は抽出器が保存した原順序。検索順位・chunk の順序を用いない。
    anchor = next(
        (i for i, row in enumerate(elements) if row.get("element_id") in anchor_ids), None
    )
    if anchor is None:
        return {
            "revision": revision,
            "anchor_status": "unresolved",
            "section_path": [],
            "blocks": [
                {
                    "id": "unresolved",
                    "text": quote[:max_chars],
                    "offset": 0,
                    "highlights": [],
                    "cited": True,
                    "kind": "text",
                }
            ],
            "previous_cursor": None,
            "next_cursor": None,
            "outline": [],
            "scope": scope,
        }
    paths = [_path(row) for row in elements]
    anchor_path = paths[anchor]
    sections: dict[tuple[str, ...], int] = {(): 0}
    for index, path in enumerate(paths):
        for size in range(1, len(path) + 1):
            sections.setdefault(path[:size], index)
    selected_path = (
        anchor_path if scope == "section" else anchor_path[:-1] if scope == "parent" else ()
    )
    if section_id:
        selected_path = next((path for path in sections if _section_id(path) == section_id), ())
        if not selected_path and section_id != _section_id(()):
            raise HTTPException(422, "章節の位置が不正です。")
    index, offset = anchor, 0
    source = str(elements[anchor].get("text") or "")
    quote_position = source.find(quote) if quote else -1
    unique_quote = quote_position >= 0 and source.find(quote, quote_position + 1) < 0
    if quote_position >= 0 and quote_position + len(quote) > max_chars:
        offset = max(0, quote_position - max_chars // 4)
    elif anchor > 0 and len(source) < max_chars // 2 and not selected_path:
        index = anchor - 1
        offset = max(0, len(str(elements[index].get("text") or "")) - min(512, max_chars // 4))
    if cursor:
        try:
            if len(cursor) > 2048:
                raise ValueError
            version, index, offset, cursor_section = json.loads(
                base64.urlsafe_b64decode(cursor + "=" * (-len(cursor) % 4))
            )
            if version != revision:
                raise HTTPException(409, "資料の版が変わりました。引用を開き直してください。")
            if (
                type(index) is not int
                or type(offset) is not int
                or not 0 <= index < len(elements)
                or not 0 <= offset <= len(str(elements[index].get("text") or ""))
            ):
                raise ValueError
            if cursor_section:
                selected_path = next(
                    path for path in sections if _section_id(path) == cursor_section
                )
            else:
                selected_path = ()
        except (ValueError, TypeError, KeyError, StopIteration, UnicodeError) as exc:
            raise HTTPException(422, "読み取り位置が不正です。") from exc
    indexes = [
        i
        for i, path in enumerate(paths)
        if not selected_path or path[: len(selected_path)] == selected_path
    ]
    if index not in indexes:
        index = anchor if anchor in indexes else indexes[0]
        offset = 0
    position = indexes.index(index)
    upper: tuple[int, int] | None = None
    if cursor and direction == "before":
        upper = (index, offset)
        remaining = max_chars
        count = 0
        while remaining > 0:
            available = offset
            take = min(available, remaining)
            offset -= take
            remaining -= take
            count += 1
            if remaining == 0 or position == 0 or count >= MAX_READER_BLOCKS:
                break
            position -= 1
            index = indexes[position]
            offset = len(str(elements[index].get("text") or ""))
    first_index, first_offset = index, offset
    blocks: list[dict[str, Any]] = []
    remaining = max_chars
    end_index = index
    end_offset = offset
    for current in indexes[position:]:
        text = str(elements[current].get("text") or "")
        start = offset if current == index else 0
        stop = upper[1] if upper and current == upper[0] else len(text)
        if remaining < 1 or len(blocks) >= MAX_READER_BLOCKS or upper and current > upper[0]:
            break
        if stop <= start:
            continue
        piece = read_source_window(
            text[:stop],
            offset=start,
            max_chars=remaining,
            kind="table" if elements[current].get("kind") == "table" else "text",
        )
        end = piece.excerpt_range.end if piece.excerpt_range else stop
        highlights = []
        # 表の再掲ヘッダーを含む場合、誤った文字位置を強調しない。
        if current == anchor and unique_quote and not piece.header_range:
            a = max(quote_position, start)
            b = min(quote_position + len(quote), end)
            if a < b:
                highlights = [[a - start, b - start]]
        blocks.append(
            {
                "id": str(elements[current].get("element_id") or current),
                "text": piece.text,
                "offset": start,
                "highlights": highlights,
                "cited": elements[current].get("element_id") in anchor_ids,
                "kind": elements[current].get("kind", "text"),
                "heading": [p[:80] for p in paths[current][-1:]],
            }
        )
        remaining -= len(piece.text)
        end_index, end_offset = current, end
        if end < stop or upper and (current, end) >= upper:
            break
    section = _section_id(selected_path) if selected_path else None
    previous = (
        _cursor(revision, first_index, first_offset, section)
        if first_offset > 0 or indexes.index(first_index) > 0
        else None
    )
    end_position = indexes.index(end_index)
    if end_offset >= len(str(elements[end_index].get("text") or "")):
        next_index = indexes[end_position + 1] if end_position + 1 < len(indexes) else None
        next_offset = 0
    else:
        next_index, next_offset = end_index, end_offset
    next_cursor = (
        _cursor(revision, next_index, next_offset, section) if next_index is not None else None
    )
    parents = {path[:-1] for path in sections if path}
    branch = selected_path or anchor_path
    outline = [
        {
            "id": _section_id(path),
            "title": path[-1][:80] if path else "文書全体",
            "parent_id": _section_id(path[:-1]) if path else None,
            "has_children": path in parents,
        }
        for path in sections
        if path == branch
        or path[:-1] == branch
        or path[:-1] == branch[:-1]
        or path == branch[: len(path)]
    ][:25]
    return {
        "revision": revision,
        "anchor_status": "exact" if unique_quote else "element",
        "section_path": [p[:80] for p in anchor_path[-12:]],
        "blocks": blocks,
        "previous_cursor": previous,
        "next_cursor": next_cursor,
        "outline": outline,
        "scope": scope,
    }


async def read_source_context(
    oracle: OracleClient,
    document_id: str,
    chunk_id: str,
    *,
    cursor: str | None = None,
    direction: str = "after",
    scope: str = "paragraph",
    section_id: str | None = None,
    expected_revision: str | None = None,
) -> dict[str, Any]:
    chunk = await oracle.retrievable_chunk(document_id, chunk_id)
    if chunk is None:
        stale = await oracle.accessible_chunk_exists(document_id, chunk_id)
        raise HTTPException(
            409 if stale else 404, "資料の版が変わりました。" if stale else "根拠を閲覧できません。"
        )
    revision = str(chunk.metadata.get("chunk_set_id") or "")
    if expected_revision is not None and revision != expected_revision:
        raise HTTPException(409, "資料の版が変わりました。")
    sources = await oracle.chunk_set_extraction_recipe_ids([revision])
    artifact = (
        await oracle.get_document_extraction_artifact(
            document_id=document_id, extraction_recipe_id=sources[revision]
        )
        if revision in sources
        else None
    )
    payload = artifact.get("extraction_json") if artifact else {}
    ids = chunk_element_ids(chunk.metadata)
    return await asyncio.to_thread(
        reading_window,
        payload if isinstance(payload, Mapping) else {},
        revision=revision,
        anchor_ids=ids,
        quote=chunk.text,
        cursor=cursor,
        direction=direction,
        scope=scope,
        section_id=section_id,
    )
