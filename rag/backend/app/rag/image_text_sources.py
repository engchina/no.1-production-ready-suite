"""画像部位の位置を、保存済みの本文と正式な成果物scopeへ結ぶ。"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from typing import Any

from rag_engine.chunking.constants import SOURCE_TABLE_CROP_RELATIONSHIP
from rag_engine.chunking.text_projection import (
    RecordTextProjection,
    bound_text_source_map,
    make_text_source_map,
)
from rag_parser_core.image_purpose import recorded_image_purpose

SCOPE_KEY = "image_text_source_scope_json"
PLACEMENT_KEY = "parent_text_placement_json"
READY_KEY = "image_text_source_ready"


def _digest(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def _decode(value: Any) -> dict[str, Any] | None:
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except ValueError:
            return None
    return value if isinstance(value, dict) else None


def checked_map(text: str, metadata: Mapping[str, Any]) -> dict[str, Any] | None:
    engine = _decode(metadata.get("engine_metadata_json"))
    if engine is None:
        return None
    layout = engine.get("layout")
    value = layout.get("text_source_map") if isinstance(layout, dict) else None
    if not isinstance(value, dict):
        return None
    bound = bound_text_source_map(
        value,
        text,
        source_run_id=str(value.get("source_run_id") or ""),
        source_file_name=str(value.get("source_file_name") or ""),
        source_engine_id=str(value.get("source_engine_id") or ""),
    )
    if bound is None:
        return None
    ranges = sorted(bound["ranges"], key=lambda row: (row["start"], row["end"]))
    if any(left["end"] > right["start"] for left, right in zip(ranges, ranges[1:], strict=False)):
        return None
    if metadata.get("source_parser") != f"{bound['source_engine_id']}_layout":
        return None
    refs = _decode_list(metadata.get("source_record_refs_json"))
    pairs = set()
    for ref in refs:
        rid = str(ref.get("record_id") or "")
        if ref.get("category") == "Picture" or ref.get("picture_record_id"):
            pairs.add((rid, str(ref.get("picture_record_id") or rid)))
        for visual in _decode_list(ref.get("table_visual_evidence")):
            if not isinstance(visual, dict):
                continue
            image_id = str(visual.get("image_id") or "")
            pairs.add((str(visual.get("record_id") or image_id), image_id))
            if visual.get("relationship") == SOURCE_TABLE_CROP_RELATIONSHIP:
                pairs.add((rid, image_id))
    images: dict[str, set[str]] = {}
    for image in _decode_list(engine.get("image_evidence")):
        if isinstance(image, dict):
            if (
                image.get("source_run_id") != bound["source_run_id"]
                or image.get("source_file_name") != bound["source_file_name"]
            ):
                return None
            images.setdefault(str(image.get("image_id") or ""), set()).add(
                recorded_image_purpose(image)
            )
    for row in bound["ranges"]:
        if not row["role"].startswith("image_"):
            continue
        if (row["source_record_id"], row["image_id"]) not in pairs or row["image_id"] not in images:
            return None
        if row["vision_image_purpose"] != "unknown" and images[row["image_id"]] != {
            row["vision_image_purpose"]
        }:
            return None
    return bound


def _decode_list(value: Any) -> list[dict[str, Any]]:
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except ValueError:
            return []
    return [item for item in value if isinstance(item, dict)] if isinstance(value, list) else []


def parent_placement(child: Any, parent: Any) -> dict[str, Any] | None:
    if (child.source_run_id, child.source_file_name, child.source_engine_id) != (
        parent.source_run_id,
        parent.source_file_name,
        parent.source_engine_id,
    ):
        return None
    value = bound_text_source_map(
        (parent.metadata.get("layout") or {}).get("text_source_map"),
        parent.text,
        source_run_id=parent.source_run_id,
        source_file_name=parent.source_file_name,
        source_engine_id=parent.source_engine_id,
    )
    if value is None or child.chunk_id in value.get("unknown_sources", []):
        return None
    positions = [item for item in value.get("children", []) if item["chunk_id"] == child.chunk_id]
    if len(positions) != 1:
        return None
    item = positions[0]
    if (
        parent.text[item["start"] : item["end"]] != child.text
        or item["text_sha256"] != hashlib.sha256(child.text.encode()).hexdigest()
    ):
        return None
    return {
        "start": item["start"],
        "end": item["end"],
        "ordinal": item["ordinal"],
        "child_count": len(value["children"]),
        "parent_text_sha256": value["text_sha256"],
    }


def _scope_payload(
    text: str,
    metadata: Mapping[str, Any],
    *,
    document_id: str,
    recipe_id: str,
    chunk_set_id: str,
    chunk_id: str,
) -> dict[str, Any] | None:
    value = checked_map(text, metadata)
    if value is None or not all((document_id, recipe_id, chunk_set_id, chunk_id)):
        return None
    placement = _decode(metadata.get(PLACEMENT_KEY))
    return {
        "version": 1,
        "document_id": document_id,
        "recipe_id": recipe_id,
        "chunk_set_id": chunk_set_id,
        "chunk_id": chunk_id,
        "parent_chunk_id": str(metadata.get("parent_chunk_id") or ""),
        "map_sha256": _digest(value),
        "placement_sha256": _digest(placement),
    }


def scoped_metadata(
    text: str,
    metadata: Mapping[str, Any],
    *,
    document_id: str,
    recipe_id: str,
    chunk_set_id: str,
    chunk_id: str,
    fresh: bool,
    source_file_name: str | None = None,
) -> dict[str, Any]:
    """新producerだけを初回bindし、既存scopeの誤りを現在の版へ修復しない。"""
    result = dict(metadata)
    ready = result.pop(READY_KEY, None) is True
    current = _decode(result.get(SCOPE_KEY))
    expected = _scope_payload(
        text,
        result,
        document_id=document_id,
        recipe_id=recipe_id,
        chunk_set_id=chunk_set_id,
        chunk_id=chunk_id,
    )
    value = checked_map(text, result) if source_file_name is not None else None
    if source_file_name is not None and (
        value is None or value["source_file_name"] != source_file_name
    ):
        expected = None
    if expected is not None and (
        (fresh and ready and current is None)
        or (current == expected and type(current.get("version")) is int)
    ):
        result[SCOPE_KEY] = json.dumps(expected, ensure_ascii=False, separators=(",", ":"))
    else:
        result.pop(SCOPE_KEY, None)
    return result


def verified_source(hit: Any) -> dict[str, Any] | None:
    scope = _decode(hit.metadata.get(SCOPE_KEY))
    if scope is None or type(scope.get("version")) is not int or scope.get("version") != 1:
        return None
    expected = _scope_payload(
        hit.text,
        hit.metadata,
        document_id=hit.document_id,
        recipe_id=str(hit.metadata.get("recipe_id") or ""),
        chunk_set_id=str(hit.metadata.get("chunk_set_id") or ""),
        chunk_id=hit.chunk_id,
    )
    if scope != expected:
        return None
    return checked_map(hit.text, hit.metadata)


def restore_parent_map(
    parent_text: str, metadata: dict[str, Any], members: Sequence[Any], sources: Mapping[str, Any]
) -> None:
    rows: list[dict[str, Any]] = []
    unknown: list[str] = []
    identities = set()
    positions = set()
    counts = set()
    intervals: list[tuple[int, int]] = []
    verified_members = 0
    digest = hashlib.sha256(parent_text.encode()).hexdigest()
    for child in members:
        source = sources.get(child.chunk_uid)
        value = verified_source(source) if source is not None else None
        place = _decode(source.metadata.get(PLACEMENT_KEY)) if source is not None else None
        if value is None or place is None or source is None or source.text != child.text:
            unknown.append(child.chunk_uid)
            continue
        okay = (
            source.document_id == metadata.get("document_id")
            and all(
                source.metadata.get(k, "") == metadata.get(k, "") == child.metadata.get(k, "")
                for k in ("recipe_id", "chunk_set_id")
            )
            and place.get("parent_text_sha256") == digest
            and type(place.get("start")) is int
            and type(place.get("end")) is int
            and 0 <= place["start"] < place["end"] <= len(parent_text)
            and parent_text[place["start"] : place["end"]] == child.text
            and type(place.get("ordinal")) is int
            and type(place.get("child_count")) is int
            and 0 <= place["ordinal"] < place["child_count"]
        )
        if not okay:
            unknown.append(child.chunk_uid)
            continue
        verified_members += 1
        if value.get("unknown_sources"):
            unknown.append(child.chunk_uid)
        scope = _decode(source.metadata.get(SCOPE_KEY)) or {}
        identity = (
            value["source_run_id"],
            value["source_file_name"],
            value["source_engine_id"],
            scope.get("parent_chunk_id"),
        )
        identities.add(identity)
        positions.add(place["ordinal"])
        counts.add(place["child_count"])
        intervals.append((place["start"], place["end"]))
        rows.extend(
            {
                **row,
                "start": row["start"] + place["start"],
                "end": row["end"] + place["start"],
                "child_chunk_id": str(source.metadata.get("engine_chunk_id") or ""),
            }
            for row in value["ranges"]
        )
    from rag_engine.generation.image_text_sources import ANSWER_IMAGE_SOURCE_KEY, has_image_ranges

    metadata.pop(ANSWER_IMAGE_SOURCE_KEY, None)
    metadata.pop(SCOPE_KEY, None)
    metadata.pop(PLACEMENT_KEY, None)
    layout = dict(metadata.get("layout") or {})
    layout.pop("text_source_map", None)
    if layout or "layout" in metadata:
        metadata["layout"] = layout
    # 同じ位置の重複や異なる解析系統を一つの完全mapとして扱わない。
    ordered = sorted(intervals)
    nonoverlapping = all(
        left[1] <= right[0] for left, right in zip(ordered, ordered[1:], strict=False)
    )
    if (
        len(identities) == 1
        and len(positions) == verified_members
        and len(counts) == 1
        and nonoverlapping
    ):
        run, file, engine, _parent = next(iter(identities))
        layout["text_source_map"] = make_text_source_map(
            RecordTextProjection(parent_text, tuple(rows)),
            source_run_id=run,
            source_file_name=file,
            source_engine_id=engine,
            unknown_sources=unknown,
        )
        metadata["layout"] = layout
        metadata["image_text_source_map_status"] = (
            "complete" if not unknown and len(positions) == next(iter(counts)) else "partial"
        )
        if has_image_ranges(layout["text_source_map"]):
            metadata[ANSWER_IMAGE_SOURCE_KEY] = {
                "version": 1,
                "scope": {
                    key: str(metadata.get(key) or "")
                    for key in ("document_id", "recipe_id", "chunk_set_id")
                },
                "completeness": metadata["image_text_source_map_status"],
                "source_map": layout["text_source_map"],
            }
    else:
        metadata["image_text_source_map_status"] = "unknown"
