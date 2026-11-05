"""本文の連結と同時に記録する画像部位の位置。本文から出所を推測しない。"""

from __future__ import annotations

import hashlib
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from rag_parser_core.image_purpose import recorded_image_purpose

IMAGE_TEXT_SOURCE_ROLES = frozenset({"image_description", "image_ocr", "image_table_supplement"})


@dataclass(frozen=True)
class RecordTextProjection:
    text: str = ""
    ranges: tuple[dict[str, Any], ...] = ()


def source_text(
    text: str,
    owner: dict[str, Any],
    *,
    role: str = "unclassified",
    image_id: str = "",
    container_record_id: str = "",
    purpose_source: dict[str, Any] | None = None,
) -> RecordTextProjection:
    record_id = str(owner.get("id") or owner.get("record_id") or owner.get("image_id") or "")
    if not text or not record_id or role == "unclassified":
        return RecordTextProjection(text)
    row: dict[str, Any] = {
        "source_record_id": record_id,
        "container_record_id": container_record_id,
        "role": role,
        "start": 0,
        "end": len(text),
    }
    if role.startswith("image_"):
        row.update(image_id=image_id, vision_image_purpose=recorded_image_purpose(purpose_source))
    return RecordTextProjection(text, (row,))


def join_projections(parts: Sequence[RecordTextProjection], separator: str, *, strip: bool = False) -> RecordTextProjection:
    fragments: list[str] = []
    cursor = 0
    rows: list[dict[str, Any]] = []
    for part in parts:
        if not part.text:
            continue
        if fragments:
            cursor += len(separator)
        rows.extend({**row, "start": row["start"] + cursor, "end": row["end"] + cursor} for row in part.ranges)
        fragments.append(part.text)
        cursor += len(part.text)
    text = separator.join(fragments)
    if strip:
        left, right = len(text) - len(text.lstrip()), len(text.rstrip())
        text = text.strip()
        rows = [
            {**row, "start": max(row["start"], left) - left, "end": min(row["end"], right) - left}
            for row in rows
            if row["start"] < right and left < row["end"]
        ]
    return RecordTextProjection(text, tuple(rows))


def make_text_source_map(
    projection: RecordTextProjection,
    *,
    source_run_id: str,
    source_file_name: str,
    source_engine_id: str,
    unknown_sources: Sequence[str] = (),
    children: Sequence[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    value = {
        "version": 1,
        "source_run_id": source_run_id,
        "source_file_name": source_file_name,
        "source_engine_id": source_engine_id,
        "text_sha256": hashlib.sha256(projection.text.encode("utf-8")).hexdigest(),
        "ranges": list(projection.ranges),
    }
    if children is not None:
        value["children"] = list(children)
    if unknown_sources:
        value["unknown_sources"] = list(unknown_sources)
    return value


def validate_text_source_map(value: Any) -> dict[str, Any]:
    """保存形式のみを検証する。本文・解析版への結合は読み出し時に別途照合する。"""
    required = {"version", "source_run_id", "source_file_name", "source_engine_id", "text_sha256", "ranges"}
    if not isinstance(value, dict) or not required <= value.keys() or value.keys() - required - {"unknown_sources", "children"}:
        raise ValueError("本文の出所位置の形式が不正です。再チャンキングしてください。")
    if type(value["version"]) is not int or value["version"] != 1:
        raise ValueError("本文の出所位置の版に対応していません。")
    for key in ("source_run_id", "source_file_name", "source_engine_id", "text_sha256"):
        if not isinstance(value[key], str):
            raise ValueError("本文の出所位置の識別子が不正です。")
    digest = value["text_sha256"]
    if len(digest) != 64 or any(char not in "0123456789abcdef" for char in digest):
        raise ValueError("本文の出所位置のハッシュが不正です。")
    if not isinstance(value["ranges"], list):
        raise ValueError("本文の出所位置が配列ではありません。")
    for row in value["ranges"]:
        keys = {"source_record_id", "container_record_id", "role", "start", "end"}
        if (
            not isinstance(row, dict)
            or not keys <= row.keys()
            or row.keys() - keys - {"image_id", "vision_image_purpose", "child_chunk_id"}
        ):
            raise ValueError("本文の出所位置の項目が不正です。")
        if not isinstance(row["source_record_id"], str) or not row["source_record_id"] or not isinstance(row["container_record_id"], str):
            raise ValueError("本文の出所レコードが不正です。")
        if type(row["start"]) is not int or type(row["end"]) is not int or not 0 <= row["start"] < row["end"]:
            raise ValueError("本文の出所位置の範囲が不正です。")
        if "child_chunk_id" in row and (not isinstance(row["child_chunk_id"], str) or not row["child_chunk_id"]):
            raise ValueError("本文の出所の子段識別子が不正です。")
        if not isinstance(row["role"], str):
            raise ValueError("本文の出所位置の役割が不正です。")
        if row["role"] == "unclassified":
            if "vision_image_purpose" in row or "image_id" in row:
                raise ValueError("出所未確定の本文に画像用途は付けられません。")
        elif row["role"] in IMAGE_TEXT_SOURCE_ROLES:
            if (
                not isinstance(row.get("image_id"), str)
                or not row["image_id"]
                or not isinstance(row.get("vision_image_purpose"), str)
                or row["vision_image_purpose"] not in {"record", "example", "unknown"}
            ):
                raise ValueError("画像部位の識別子または用途が不正です。")
        else:
            raise ValueError("本文の出所位置の役割が不正です。")
    if "unknown_sources" in value and (
        not isinstance(value["unknown_sources"], list) or not all(isinstance(item, str) and item for item in value["unknown_sources"])
    ):
        raise ValueError("未確定の子段識別子が不正です。")
    if "children" in value:
        if not isinstance(value["children"], list):
            raise ValueError("本文の子段位置が配列ではありません。")
        for item in value["children"]:
            if not isinstance(item, dict) or set(item) != {"chunk_id", "start", "end", "text_sha256", "ordinal"}:
                raise ValueError("本文の子段位置が不正です。")
            if (
                not isinstance(item["chunk_id"], str)
                or not item["chunk_id"]
                or type(item["start"]) is not int
                or type(item["end"]) is not int
                or not 0 <= item["start"] < item["end"]
                or type(item["ordinal"]) is not int
                or item["ordinal"] < 0
                or not isinstance(item["text_sha256"], str)
                or len(item["text_sha256"]) != 64
                or any(c not in "0123456789abcdef" for c in item["text_sha256"])
            ):
                raise ValueError("本文の子段位置の識別子または範囲が不正です。")
    return value


def bound_text_source_map(
    value: Any, text: str, *, source_run_id: str, source_file_name: str, source_engine_id: str
) -> dict[str, Any] | None:
    """本文と版が一致しない位置は転用しない。旧記録に位置を補完しない。"""
    try:
        checked = validate_text_source_map(value)
    except ValueError:
        return None
    if (
        checked["source_run_id"] != source_run_id
        or checked["source_file_name"] != source_file_name
        or checked["source_engine_id"] != source_engine_id
        or checked["text_sha256"] != hashlib.sha256(text.encode("utf-8")).hexdigest()
        or any(row["end"] > len(text) for row in checked["ranges"])
        or any(row["end"] > len(text) for row in checked.get("children", []))
    ):
        return None
    return checked
