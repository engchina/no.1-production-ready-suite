"""原本の有界な表示区切りと、開始位置で所属する抽出要素。"""

from __future__ import annotations

import bisect
import hashlib
from collections.abc import Mapping
from typing import Any

from rag_parser_core.json_source_map import raw_string_ranges
from rag_parser_core.source_map import (
    SOURCE_MAP_KEY,
    SOURCE_WINDOW_CODEPOINTS,
    decode_source_text,
    physical_line_starts,
)

from app.services.json_source_rebuild import payload_with_source_map
from app.services.source_map_resolve import (
    STATUS_EXACT,
    STATUS_UNRESOLVED,
    SourceUnit,
    resolve_source_map,
)

# 最後の単位は要素の末尾までなので、Chunk の位置の上限を区切らない。
CHUNK_OFFSET_MAX = 2**53


def decode_inspection_text(data: bytes) -> str:
    """既存の原本閲覧と同じ、UTF-8 優先の文字コード検出を使う。"""
    return decode_source_text(data)


def _origin_record_starts(
    payload: Mapping[str, Any], text: str, revision: str
) -> tuple[str, dict[str, int]] | None:
    """前処理の変換元の原本で、記録が持つ原本の物理行を位置にする（行から文字へ換算するだけ）。

    変換元の原本でない・行を持つ記録が無いときは None。行の番号が崩れていれば同期しない。
    """
    artifacts = payload.get("parser_artifacts")
    derivation = artifacts.get("source_derivation") if isinstance(artifacts, Mapping) else None
    if (
        not isinstance(derivation, Mapping)
        or not derivation.get("converted")
        or derivation.get("source_sha256") != revision
    ):
        return None
    lines = {
        str(element["element_id"]): element["metadata"]["source_line_start"]
        for element in payload.get("elements") or []
        if element.get("element_id") and "source_line_start" in (element.get("metadata") or {})
    }
    if not lines:
        return None
    line_starts = physical_line_starts(text)
    starts: dict[str, int] = {}
    previous = 0
    for element_id, line in lines.items():
        if type(line) is not int or not previous < line <= len(line_starts):
            return STATUS_UNRESOLVED, {}
        starts[element_id] = line_starts[line - 1]
        previous = line
    return STATUS_EXACT, starts


def _window_units(
    units: tuple[SourceUnit, ...], start: int, end: int
) -> dict[str, list[SourceUnit]]:
    """この表示範囲で始まる単位（要素の本文の順）。単位も開始位置のある範囲にだけ所属する。"""
    grouped: dict[str, list[SourceUnit]] = {}
    for unit in sorted(units, key=lambda item: item.text_start):
        if start <= unit.source_start < end:
            grouped.setdefault(unit.element_id, []).append(unit)
    return grouped


def _offset_ranges(units: tuple[SourceUnit, ...], window: list[SourceUnit]) -> list[list[int]]:
    """Chunk の ``start_offset``（要素の本文の位置）が単位に所属する範囲（間の改行を含む）。"""
    last = max(unit.text_end for unit in units)
    return [
        [unit.text_start, CHUNK_OFFSET_MAX if unit.text_end == last else unit.text_end]
        for unit in window
    ]


def _fragment_view(units: list[SourceUnit]) -> dict[str, Any]:
    rows = [unit for unit in units if unit.row_start is not None and unit.row_end is not None]
    return {
        "ranges": [[unit.text_start, unit.text_end] for unit in units],
        "rows": [min(u.row_start or 0 for u in rows), max(u.row_end or 0 for u in rows)]
        if rows
        else None,
    }


def _chunk_rows(
    fragments: Mapping[str, Mapping[str, Any]],
    starts: Mapping[str, int],
    start: int,
    end: int,
    offset_ranges: list[list[int]],
) -> dict[str, Any] | None:
    """単位に分けた要素の Chunk の所属（分割が記録した行の番号と本文の位置）。

    表は行の番号（表頭の次の行が 1）で、行の単位を持たない要素は ``start_offset`` で所属させる。
    どちらの座標も持たない Chunk は位置が分からないので、要素の開始の範囲にだけ所属する。
    """
    if not fragments:
        return None
    rows = [view["rows"] for view in fragments.values() if view["rows"]]
    return {
        "row_start": min(row[0] for row in rows) - 1 if rows else 0,
        "row_end": max(row[1] for row in rows) - 1 if rows else -1,
        "offset_ranges": [] if rows else offset_ranges,
        "at_start": any(start <= starts.get(eid, -1) < end for eid in fragments),
    }


def source_inspection(
    payload: Mapping[str, Any],
    data: bytes,
    *,
    offset: int,
    limit: int,
    element_id: str | None = None,
    expected_revision: str | None = None,
) -> dict[str, Any]:
    """Unicode codepoint の半開区間。原本対応は記録か決定的な再計算だけを使い、推測しない。"""
    revision = hashlib.sha256(data).hexdigest()
    text = decode_inspection_text(data)
    starts: dict[str, int] = {}
    units: tuple[SourceUnit, ...] = ()
    limit = max(1, min(SOURCE_WINDOW_CODEPOINTS, limit))
    if expected_revision is None or expected_revision == revision:
        payload = payload_with_source_map(payload, data)
    artifacts = payload.get("parser_artifacts") or {}
    mapping = artifacts.get("json_source_map") if isinstance(artifacts, Mapping) else None
    mapping = mapping if isinstance(mapping, Mapping) else {}
    source_map = artifacts.get(SOURCE_MAP_KEY) if isinstance(artifacts, Mapping) else None
    status = "unrecorded"
    if expected_revision is not None and expected_revision != revision:
        status = "version_mismatch"
    elif origin := _origin_record_starts(payload, text, revision):
        status, starts = origin
    elif isinstance(source_map, Mapping):
        resolved = resolve_source_map(
            source_map,
            data=data,
            text_length=len(text),
            element_texts={
                str(element["element_id"]): str(element.get("text") or "")
                for element in payload.get("elements") or []
                if element.get("element_id")
            },
        )
        status, starts, units = resolved.status, resolved.starts, resolved.units
    elif mapping.get("version") == 1 and mapping.get("coordinate_unit") == "unicode_codepoint":
        if mapping.get("sha256") != revision:
            status = "version_mismatch"
        else:
            try:
                spans_by_id = mapping.get("elements") or {}
                if not isinstance(spans_by_id, Mapping):
                    raise ValueError("invalid source map elements")
                pointers = {span["pointer"] for spans in spans_by_id.values() for span in spans}
                text, nodes = raw_string_ranges(data, pointers)
                for element in payload.get("elements") or []:
                    eid = element.get("element_id")
                    spans = spans_by_id.get(eid) or []
                    positions = []
                    for span in spans:
                        node = nodes[span["pointer"]]
                        a, b = span["source_start"], span["source_end"]
                        if element["text"][span["start"] : span["end"]] != node.value[a:b]:
                            raise ValueError("source map mismatch")
                        positions.append(node.raw_range(a, b)[0])
                    if positions:
                        starts[eid] = min(positions)
                status = "exact" if starts else "unrecorded"
            except (KeyError, ValueError, TypeError, IndexError, RecursionError, UnicodeError):
                status, starts = "unresolved", {}
    if status == "unrecorded" and len(text) <= limit:
        # 原本全体が 1 つの表示範囲なら、位置が無くても全要素がこの範囲に所属する（#4094）。
        starts = {
            str(element["element_id"]): 0
            for element in payload.get("elements") or []
            if element.get("element_id")
        }
        status = "exact" if starts else status
    # 短い構造ノードの途中を避ける。長大ノードだけを有限の文字窓に分ける。
    boundaries = sorted({*starts.values(), *(unit.source_start for unit in units)})
    cuts = [0]
    while cuts[-1] + limit < len(text):
        target = cuts[-1] + limit
        i = bisect.bisect_right(boundaries, target) - 1
        cuts.append(boundaries[i] if i >= 0 and boundaries[i] > cuts[-1] else target)
    target = starts.get(element_id, offset) if element_id else offset
    index = max(0, bisect.bisect_right(cuts, min(target, max(0, len(text) - 1))) - 1)
    start = cuts[index]
    end = cuts[index + 1] if index + 1 < len(cuts) else len(text)
    window_units = _window_units(units, start, end)
    fragments = {element: _fragment_view(items) for element, items in window_units.items()}
    offset_ranges = [
        value
        for element, items in window_units.items()
        for value in _offset_ranges(
            tuple(unit for unit in units if unit.element_id == element), items
        )
    ]
    split_elements = {unit.element_id for unit in units}
    return {
        "text": text[start:end],
        "offset": start,
        "end": end,
        "total": len(text),
        "limit": limit,
        "revision": revision,
        "scope_status": status,
        "element_ids": [
            eid
            for eid, position in starts.items()
            if eid in fragments or (eid not in split_elements and start <= position < end)
        ],
        "element_fragments": fragments,
        "chunk_rows": _chunk_rows(fragments, starts, start, end, offset_ranges),
        "element_starts": starts,
        "previous_offset": cuts[index - 1] if index else None,
        "next_offset": cuts[index + 1] if index + 1 < len(cuts) else None,
    }
