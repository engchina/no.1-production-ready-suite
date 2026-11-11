"""保存済みの原本対応を検証し、JSON 文字列ノードの有界な窓を返す。"""

from __future__ import annotations

import hashlib
from collections.abc import Awaitable, Callable, Mapping
from typing import Any, Literal

from rag_parser_core.json_source_map import raw_string_ranges
from rag_parser_core.source_map import decode_source_text

from app.services.source_map_resolve import resolve_source_map

MAX_SOURCE_CHARS = 8192


def _mapping(payload: Mapping[str, Any]) -> Mapping[str, Any] | None:
    artifacts = payload.get("parser_artifacts")
    if not isinstance(artifacts, Mapping):
        return None
    mapping = artifacts.get("json_source_map")
    if isinstance(mapping, Mapping):
        return mapping
    generic = artifacts.get("source_map")
    if isinstance(generic, Mapping) and generic.get("scheme") in {"json_pointer", "html_source"}:
        return generic
    return None


def _generic_window(
    payload: Mapping[str, Any],
    mapping: Mapping[str, Any],
    data: bytes,
    *,
    element_id: str,
    source_index: int,
    offset: int | None,
    max_chars: int,
) -> dict[str, Any]:
    empty = {"text": "", "highlights": [], "coordinate_unit": "unicode_codepoint"}
    source = decode_source_text(data)
    elements = {
        row["element_id"]: row["text"]
        for row in payload.get("elements", [])
        if isinstance(row, Mapping)
        and isinstance(row.get("element_id"), str)
        and isinstance(row.get("text"), str)
    }
    if element_id not in elements:
        return {**empty, "status": "unresolved"}
    resolved = resolve_source_map(
        mapping, data=data, text_length=len(source), element_texts=elements
    )
    if resolved.status != "exact":
        return {**empty, "status": resolved.status}
    try:
        spans = mapping["elements"].get(element_id) or []
        if type(source_index) is not int or not 0 <= source_index < len(spans):
            raise ValueError
        span = spans[source_index]
        a, b = span["source_start"], span["source_end"]
        if type(a) is not int or type(b) is not int or not 0 <= a < b <= len(source):
            raise ValueError
        start = a if offset is None else offset
        if type(start) is not int or not a <= start < b:
            raise ValueError
        end = min(b, start + min(MAX_SOURCE_CHARS, max(1, max_chars)))
        return {
            **empty,
            "status": "exact",
            "anchor_status": "element",
            "pointer": span.get("locator", ""),
            "source_index": source_index,
            "source_count": len(spans),
            "text": source[start:end],
            "offset": start,
            "highlights": [[0, end - start]],
            "previous_offset": max(a, start - min(MAX_SOURCE_CHARS, max(1, max_chars)))
            if start > a
            else None,
            "next_offset": end if end < b else None,
            "sha256": mapping["sha256"],
        }
    except (KeyError, ValueError, TypeError, IndexError):
        return {**empty, "status": "unresolved"}


def source_map_window(
    payload: Mapping[str, Any],
    data: bytes,
    *,
    element_id: str,
    source_index: int = 0,
    offset: int | None = None,
    max_chars: int = MAX_SOURCE_CHARS,
    quote: str = "",
) -> dict[str, Any]:
    """古い記録・版違い・壊れた対応は文字検索で補わない。座標は codepoint。"""
    empty = {"text": "", "highlights": [], "coordinate_unit": "unicode_codepoint"}
    mapping = _mapping(payload)
    if isinstance(mapping, Mapping) and mapping.get("scheme") in {"json_pointer", "html_source"}:
        return _generic_window(
            payload,
            mapping,
            data,
            element_id=element_id,
            source_index=source_index,
            offset=offset,
            max_chars=max_chars,
        )
    if (
        not isinstance(mapping, Mapping)
        or mapping.get("version") != 1
        or mapping.get("coordinate_unit") != "unicode_codepoint"
        or not mapping.get("sha256")
    ):
        return {**empty, "status": "unrecorded"}
    if hashlib.sha256(data).hexdigest() != mapping["sha256"]:
        return {**empty, "status": "version_mismatch"}
    element = next(
        (row for row in payload.get("elements", []) if row.get("element_id") == element_id), None
    )
    source_elements = mapping.get("elements")
    spans = source_elements.get(element_id) if isinstance(source_elements, Mapping) else None
    if element is None or not isinstance(spans, list) or not spans:
        return {**empty, "status": "unresolved"}
    position = element["text"].find(quote) if quote else -1
    exact_quote = position >= 0 and element["text"].find(quote, position + 1) < 0
    if exact_quote:
        selected = []
        for span in spans:
            if not isinstance(span, Mapping):
                return {**empty, "status": "unresolved"}
            start, end = span.get("start"), span.get("end")
            if type(start) is not int or type(end) is not int:
                return {**empty, "status": "unresolved"}
            a, b = max(start, position), min(end, position + len(quote))
            if a < b:
                source_start = span.get("source_start")
                if type(source_start) is not int:
                    return {**empty, "status": "unresolved"}
                selected.append(
                    {
                        **span,
                        "start": a,
                        "end": b,
                        "source_start": source_start + a - start,
                        "source_end": source_start + b - start,
                    }
                )
        spans = selected
        if not spans:
            return {**empty, "status": "unresolved"}
    try:
        if len(spans) > 60000:
            raise ValueError
        pointers = list(dict.fromkeys(span["pointer"] for span in spans))
        if type(source_index) is not int or not 0 <= source_index < len(pointers):
            raise ValueError
        pointer = pointers[source_index]
        if not isinstance(pointer, str) or len(pointer) > 4096:
            raise ValueError
        source, nodes = raw_string_ranges(data, {pointer})
        node = nodes[pointer]
        source_start, source_end = node.raw_range(0, len(node.value))
        ranges: list[tuple[int, int]] = []
        for span in spans:
            if span["pointer"] != pointer:
                continue
            start, end = span["start"], span["end"]
            if (
                type(start) is not int
                or type(end) is not int
                or not 0 <= start < end <= len(element["text"])
            ):
                raise ValueError
            a, b = span["source_start"], span["source_end"]
            raw_start, raw_end = node.raw_range(a, b)
            if element["text"][start:end] != node.value[a:b]:
                raise ValueError
            ranges.append((raw_start, raw_end))
        # 結合セルなどの一対多対応は原本上で重なる。返す強調範囲を重複させない。
        merged: list[tuple[int, int]] = []
        for a, b in sorted(set(ranges)):
            if merged and a <= merged[-1][1]:
                merged[-1] = (merged[-1][0], max(merged[-1][1], b))
            else:
                merged.append((a, b))
        ranges = merged
        limit = min(MAX_SOURCE_CHARS, max(1, max_chars))
        if offset is None:
            start = max(source_start, ranges[0][0] - min(512, limit // 4))
        elif type(offset) is int and source_start <= offset <= source_end:
            start = offset
        else:
            raise ValueError
        end = min(source_end, start + limit)
        highlights = [
            [max(a, start) - start, min(b, end) - start]
            for a, b in ranges
            if max(a, start) < min(b, end)
        ]
        return {
            **empty,
            "status": "exact",
            "anchor_status": "exact" if exact_quote else "element",
            "pointer": pointer,
            "source_index": source_index,
            "source_count": len(pointers),
            "text": source[start:end],
            "offset": start,
            "highlights": highlights,
            "previous_offset": max(source_start, start - limit) if start > source_start else None,
            "next_offset": end if end < source_end else None,
            "sha256": mapping["sha256"],
        }
    except (ValueError, TypeError, KeyError, IndexError, RecursionError, UnicodeError):
        return {**empty, "status": "unresolved"}


async def load_source_map_window(
    payload: Mapping[str, Any],
    *,
    element_id: str,
    revision: str,
    load: Callable[[Literal["original", "prepared"]], Awaitable[bytes]],
    source_index: int = 0,
    offset: int | None = None,
    quote: str = "",
) -> dict[str, Any]:
    """呼出し元で認可した解析版の原本/処理後ファイルだけを照合する。"""
    import asyncio

    from fastapi import HTTPException

    mapping = _mapping(payload)
    base = {"revision": revision, "element_id": element_id}
    if not isinstance(mapping, Mapping) or not mapping.get("sha256"):
        return {**base, **source_map_window(payload, b"", element_id=element_id)}
    variants: tuple[Literal["original", "prepared"], ...] = ("original", "prepared")
    loaded = False
    for variant in variants:
        try:
            data = await load(variant)
        except HTTPException as exc:
            if exc.status_code == 404:
                continue
            raise
        loaded = True
        if hashlib.sha256(data).hexdigest() == mapping["sha256"]:
            result = await asyncio.to_thread(
                source_map_window,
                payload,
                data,
                element_id=element_id,
                source_index=source_index,
                offset=offset,
                quote=quote,
            )
            return {**base, "variant": variant, **result}
    if not loaded:
        raise HTTPException(404, "原本または処理後のファイルが見つかりません。")
    return {
        **base,
        "status": "version_mismatch",
        "text": "",
        "highlights": [],
        "coordinate_unit": "unicode_codepoint",
    }
