"""原本の有界な表示区切りと、開始位置で所属する抽出要素。"""

from __future__ import annotations

import bisect
import hashlib
from collections.abc import Mapping
from typing import Any

from charset_normalizer import from_bytes
from rag_parser_core.json_source_map import raw_string_ranges

from app.services.json_source_rebuild import payload_with_source_map


def decode_inspection_text(data: bytes) -> str:
    """既存の原本閲覧と同じ、UTF-8 優先の文字コード検出を使う。"""
    try:
        return data.decode("utf-8-sig")
    except UnicodeDecodeError:
        decoded = from_bytes(data).best()
        return str(decoded) if decoded is not None else data.decode("utf-8", errors="replace")


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
    limit = max(1, min(8192, limit))
    if expected_revision is None or expected_revision == revision:
        payload = payload_with_source_map(payload, data)
    artifacts = payload.get("parser_artifacts") or {}
    mapping = artifacts.get("json_source_map") if isinstance(artifacts, Mapping) else None
    mapping = mapping if isinstance(mapping, Mapping) else {}
    status = "unrecorded"
    if expected_revision is not None and expected_revision != revision:
        status = "version_mismatch"
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
    boundaries = sorted(set(starts.values()))
    cuts = [0]
    while cuts[-1] + limit < len(text):
        target = cuts[-1] + limit
        i = bisect.bisect_right(boundaries, target) - 1
        cuts.append(boundaries[i] if i >= 0 and boundaries[i] > cuts[-1] else target)
    target = starts.get(element_id, offset) if element_id else offset
    index = max(0, bisect.bisect_right(cuts, min(target, max(0, len(text) - 1))) - 1)
    start = cuts[index]
    end = cuts[index + 1] if index + 1 < len(cuts) else len(text)
    return {
        "text": text[start:end],
        "offset": start,
        "end": end,
        "total": len(text),
        "limit": limit,
        "revision": revision,
        "scope_status": status,
        "element_ids": [eid for eid, position in starts.items() if start <= position < end],
        "element_starts": starts,
        "previous_offset": cuts[index - 1] if index else None,
        "next_offset": cuts[index + 1] if index + 1 < len(cuts) else None,
    }
