"""認可済み本文mapから、生成用本文と根拠局所の画像位置を投影する。"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from typing import TYPE_CHECKING, Any

from rag_engine.chunking.text_projection import IMAGE_TEXT_SOURCE_ROLES, bound_text_source_map

if TYPE_CHECKING:
    from rag_engine.generation.citation_unit_ids import EvidenceUnitSnapshot

IMAGE_CITATION_UNIT_CONTRACT = "explicit_image_units_v1"
IMAGE_READING_UNIT_CONTRACT = "batch_image_read_units_v1"

IMAGE_PURPOSE_POLICY = (
    "- image_purposes は指定の引用単位だけの画像用途。record は資料の記録値として使い、今回の対象・時点との一致は別に確かめる。"
    "example は操作説明に使えるが、表示値・選択状態を今回の実値・既定値・必須値・許容範囲としない。\n"
    "- unknown・用途未記録の画像は実値と断定しない。例示値は「画面例では」と示す。"
    "本文の指定値や質問の明示値と区別し、用途を他の原文へ広げない。"
)


def with_image_purpose_policy(system: str) -> str:
    """用途のデータ契約を既存の判断Stepに一度だけ含める。"""
    if IMAGE_PURPOSE_POLICY in system:
        return system
    before, marker, after = system.partition("\n## Step 3:")
    return before.rstrip() + "\n" + IMAGE_PURPOSE_POLICY + ("\n" + marker + after if marker else "\n")


def image_purpose_system(system: str, spans: Sequence[Mapping[str, Any]]) -> str:
    """用途データのある呼び出しだけに解釈を添え、原生だけの入力は維持する。"""
    if any(
        (context := span_source_context(span)) is not None and context["ranges"] for span in spans if "image_text_source_context" in span
    ):
        return with_image_purpose_policy(system)
    return system


ANSWER_IMAGE_SOURCE_KEY = "answer_image_text_source_map"
SPAN_IMAGE_SOURCE_KEY = "image_text_source_context"


def _hash(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def admitted_record_map(record: Any) -> dict[str, Any] | None:
    """backendで正式scopeを検証した一時投影だけを本文へ結び直す。"""
    metadata = getattr(record, "metadata", {}) or {}
    projection = metadata.get(ANSWER_IMAGE_SOURCE_KEY)
    if (
        not isinstance(projection, dict)
        or type(projection.get("version")) is not int
        or projection.get("version") != 1
        or not isinstance(projection.get("completeness"), str)
        or projection["completeness"] not in {"complete", "partial"}
        or not isinstance(projection.get("scope"), dict)
    ):
        return None
    scope = {key: metadata.get(key) for key in ("document_id", "recipe_id", "chunk_set_id")}
    if not all(isinstance(v, str) and v for v in scope.values()) or projection["scope"] != scope:
        return None
    value = projection.get("source_map")
    if not isinstance(value, dict):
        return None
    body = str(getattr(record, "body_text", "") or getattr(record, "text", "") or "")
    checked = bound_text_source_map(
        value,
        body,
        source_run_id=str(value.get("source_run_id") or ""),
        source_file_name=str(value.get("source_file_name") or ""),
        source_engine_id=str(value.get("source_engine_id") or ""),
    )
    if checked is None or not has_image_ranges(checked):
        return None
    ordered = sorted(checked["ranges"], key=lambda row: (row["start"], row["end"]))
    if any(left["end"] > right["start"] for left, right in zip(ordered, ordered[1:], strict=False)):
        return None
    if checked.get("unknown_sources") and projection["completeness"] == "complete":
        return None
    return checked


def has_image_ranges(value: Mapping[str, Any]) -> bool:
    rows = value.get("ranges")
    return isinstance(rows, list) and any(
        isinstance(row, dict) and isinstance(row.get("role"), str) and row["role"] in IMAGE_TEXT_SOURCE_ROLES for row in rows
    )


def _has_image_source(record: Any, metadata: Mapping[str, Any]) -> bool:
    if metadata.get("image_evidence"):
        return True
    if projection := metadata.get(ANSWER_IMAGE_SOURCE_KEY):
        value = projection.get("source_map") if isinstance(projection, dict) else None
        if not isinstance(value, dict) or has_image_ranges(value):
            return True
    origin = metadata.get("origin")
    if isinstance(origin, str) and origin in {"image_extraction", "image_generated"} or getattr(record, "category", "") == "Picture":
        return True
    raw = metadata.get("engine_metadata_json")
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except ValueError:
            raw = None
    if isinstance(raw, dict) and raw.get("image_evidence"):
        return True
    refs = getattr(record, "source_record_refs", ()) or metadata.get("source_record_refs", ())
    if not refs and isinstance(metadata.get("source_record_refs_json"), str):
        try:
            refs = json.loads(metadata["source_record_refs_json"])
        except ValueError:
            refs = ()
    return isinstance(refs, (list, tuple)) and any(
        isinstance(ref, dict) and (ref.get("category") == "Picture" or ref.get("picture_record_id") or ref.get("table_visual_evidence"))
        for ref in refs
    )


def span_has_mapped_image_source(record: Any, start: int, end: int) -> bool:
    """同じ本文座標の非空白文字がすべて検証済み画像部位にあるかを返す。"""
    text = str(getattr(record, "text", "") or "")
    if type(start) is not int or type(end) is not int or not 0 <= start < end <= len(text) or not text[start:end].strip():
        return False
    value = admitted_record_map(record)
    if value is None or value["text_sha256"] != _hash(text):
        return False
    cursor = start
    for row in sorted(value["ranges"], key=lambda row: (row["start"], row["end"])):
        if row["role"] not in IMAGE_TEXT_SOURCE_ROLES or row["end"] <= cursor or row["start"] >= end:
            continue
        if text[cursor : max(cursor, row["start"])].strip():
            return False
        cursor = max(cursor, min(end, row["end"]))
    return cursor > start and not text[cursor:end].strip()


def image_source_signature(record: Any) -> str | None:
    """通常本文は従来どおり、未証明の画像はrecord単位に分離する。"""
    metadata = getattr(record, "metadata", {}) or {}
    value = admitted_record_map(record)
    if value is None:
        if not _has_image_source(record, metadata):
            return None
        return "unverified:" + str(getattr(record, "chunk_uid", "") or getattr(record, "id", "") or id(record))
    projection = metadata[ANSWER_IMAGE_SOURCE_KEY]
    data = {
        "scope": projection["scope"],
        "record_id": str(getattr(record, "chunk_uid", "") or getattr(record, "chunk_id", "") or getattr(record, "id", "") or id(record)),
        "completeness": projection["completeness"],
        "source_map": value,
    }
    return hashlib.sha256(json.dumps(data, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def span_image_source_context(record: Any, start: int, end: int, text: str) -> dict[str, Any]:
    """確かな本文sliceに限り画像と既存の原生位置を局所座標へ写す。"""
    value = admitted_record_map(record)
    body = str(getattr(record, "body_text", "") or getattr(record, "text", "") or "")
    if value is None or not 0 <= start < end <= len(body) or body[start:end] != text:
        return {}
    metadata = getattr(record, "metadata", {}) or {}
    rows = [
        {**row, "start": max(start, row["start"]) - start, "end": min(end, row["end"]) - start}
        for row in value["ranges"]
        if row["role"] in IMAGE_TEXT_SOURCE_ROLES and row["start"] < end and start < row["end"]
    ]
    # 欠rangeを原生とみなさず、既存の原生位置だけを独立に保持する。
    native = []
    layout = metadata.get("layout")
    native_rows = layout.get("native_text_ranges") if isinstance(layout, Mapping) else None
    for row in native_rows if isinstance(native_rows, list) else []:
        if (
            not isinstance(row, Mapping)
            or type(row.get("start")) is not int
            or type(row.get("end")) is not int
            or not 0 <= row["start"] < row["end"] <= len(body)
        ):
            continue
        left, right = max(start, row["start"]), min(end, row["end"])
        if left < right:
            native.append({"start": left - start, "end": right - start})
    projection = metadata[ANSWER_IMAGE_SOURCE_KEY]
    return {
        SPAN_IMAGE_SOURCE_KEY: {
            "version": 1,
            "text_sha256": _hash(text),
            "scope": projection["scope"],
            "completeness": projection["completeness"],
            "source_signature": image_source_signature(record),
            "ranges": rows,
            "native_text_ranges": native,
        }
    }


def span_source_context(span: Mapping[str, Any]) -> dict[str, Any] | None:
    """抜粋後の本文hashと位置を検証し、壊れた投影を使わない。"""
    value = span.get(SPAN_IMAGE_SOURCE_KEY)
    text = str(span.get("text") or "")
    if (
        not isinstance(value, dict)
        or type(value.get("version")) is not int
        or value.get("version") != 1
        or value.get("text_sha256") != _hash(text)
        or not isinstance(value.get("completeness"), str)
        or value["completeness"] not in {"complete", "partial"}
        or not isinstance(value.get("source_signature"), str)
        or not value["source_signature"]
        or not isinstance(value.get("scope"), dict)
        or set(value["scope"]) != {"document_id", "recipe_id", "chunk_set_id"}
        or not all(isinstance(v, str) and v for v in value["scope"].values())
    ):
        return None
    for key in ("ranges", "native_text_ranges"):
        if not isinstance(value.get(key), list):
            return None
        for row in value[key]:
            if (
                not isinstance(row, dict)
                or type(row.get("start")) is not int
                or type(row.get("end")) is not int
                or not 0 <= row["start"] < row["end"] <= len(text)
            ):
                return None
            if key == "ranges" and (
                not isinstance(row.get("role"), str)
                or row["role"] not in IMAGE_TEXT_SOURCE_ROLES
                or not isinstance(row.get("image_id"), str)
                or not row["image_id"]
                or not isinstance(row.get("source_record_id"), str)
                or not row["source_record_id"]
                or not isinstance(row.get("vision_image_purpose"), str)
                or row["vision_image_purpose"] not in {"record", "example", "unknown"}
            ):
                return None
        if key == "ranges":
            ordered = sorted(value[key], key=lambda row: (row["start"], row["end"]))
            if any(a["end"] > b["start"] for a, b in zip(ordered, ordered[1:], strict=False)):
                return None
    return value


def merge_span_image_context(left: Mapping[str, Any], right: Mapping[str, Any]) -> dict[str, Any] | None:
    """同じ出典の連続sliceを、確認済み位置だけで結び直す。"""
    a, b = span_source_context(left), span_source_context(right)
    if (
        a is None
        or b is None
        or left.get("source_id") != right.get("source_id")
        or left.get("end") != right.get("start")
        or any(a[key] != b[key] for key in ("scope", "source_signature", "completeness"))
    ):
        return None
    before, after = str(left.get("text") or ""), str(right.get("text") or "")
    if any(
        type(span.get("start")) is not int
        or type(span.get("end")) is not int
        or not 0 <= span["start"] < span["end"]
        or span["end"] - span["start"] != len(text)
        for span, text in ((left, before), (right, after))
    ):
        return None
    result = {**a, "text_sha256": _hash(before + after)}
    for key in ("ranges", "native_text_ranges"):
        rows = [*a[key], *[{**row, "start": row["start"] + len(before), "end": row["end"] + len(before)} for row in b[key]]]
        merged: list[dict[str, Any]] = []
        for row in sorted(rows, key=lambda item: (item["start"], item["end"])):
            attributes = {k: v for k, v in row.items() if k not in {"start", "end"}}
            previous = merged[-1] if merged else None
            if (
                previous is not None
                and previous["end"] == row["start"]
                and attributes == {k: v for k, v in previous.items() if k not in {"start", "end"}}
            ):
                previous["end"] = row["end"]
            else:
                merged.append(dict(row))
        result[key] = merged
    return {SPAN_IMAGE_SOURCE_KEY: result}


def slice_span_image_context(span: Mapping[str, Any], start: int, end: int) -> dict[str, Any]:
    """読取済みの連続原文範囲へ用途位置を再基底化する。"""
    value = span_source_context(span)
    text = str(span.get("text") or "")
    if value is None or not 0 <= start < end <= len(text):
        return {}
    result = {**value, "text_sha256": _hash(text[start:end])}
    for key in ("ranges", "native_text_ranges"):
        result[key] = [
            {**row, "start": max(start, row["start"]) - start, "end": min(end, row["end"]) - start}
            for row in value[key]
            if row["start"] < end and start < row["end"]
        ]
    return {SPAN_IMAGE_SOURCE_KEY: result}


def image_purpose_sidecar(snapshot: EvidenceUnitSnapshot) -> list[dict[str, Any]]:
    """実提示snapshotのUへ用途だけを結び、内部scopeや画像IDは送らない。"""
    from rag_engine.generation.citation_unit_ids import EvidenceUnitSnapshot

    span = snapshot.span()
    context = span_source_context(span)
    if context is None:
        return []
    if snapshot.contract not in {IMAGE_CITATION_UNIT_CONTRACT, IMAGE_READING_UNIT_CONTRACT}:
        raise ValueError("画像用途の引用単位の版が一致しません。")
    expected = EvidenceUnitSnapshot.create(span, snapshot.units, snapshot.contract)
    if expected.snapshot_id != snapshot.snapshot_id or expected.unit_ids != snapshot.unit_ids:
        raise ValueError("画像用途の引用単位が送信記録と一致しません。")
    result: list[dict[str, Any]] = []
    previous_key: tuple[Any, ...] | None = None
    previous_index = -2
    for index, ((start, end), unit_id) in enumerate(zip(snapshot.units, snapshot.unit_ids, strict=True)):
        rows = [row for row in context["ranges"] if row["start"] < end and start < row["end"]]
        if not rows:
            previous_key = None
            continue
        if len(rows) != 1 or not rows[0]["start"] <= start < end <= rows[0]["end"]:
            raise ValueError("画像用途の境界をまたぐ引用単位は扱えません。")
        row = rows[0]
        key = tuple(row.get(field) for field in ("image_id", "source_record_id", "container_record_id", "role", "vision_image_purpose"))
        if result and previous_key == key and previous_index + 1 == index:
            result[-1]["unit_ids"].append(unit_id)
        else:
            result.append({"unit_ids": [unit_id], "purpose": row["vision_image_purpose"]})
        previous_key, previous_index = key, index
    return result
