"""文字列の操作と同時に、視覚説明の出現位置を保持する。"""

from __future__ import annotations

import json
import re
from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Literal, cast

from rag_parser_core.vision_text import (
    VisionTextProjection,
    load_vision_text_projection,
    text_sha256,
)

type VisionTextRole = Literal["image_description", "image_ocr", "image_table_supplement"]


@dataclass(frozen=True)
class VisionTextSource:
    element_id: str
    occurrence: int
    projection: VisionTextProjection
    role: VisionTextRole = "image_description"
    record_id: str = ""
    element_body_sha256: str = ""

    def __post_init__(self) -> None:
        if self.record_id == "":
            object.__setattr__(self, "record_id", self.element_id)
        if self.element_body_sha256 == "":
            if self.role != "image_description":
                raise ValueError("画像本文の元要素hashが未記録です。")
            object.__setattr__(
                self, "element_body_sha256", text_sha256(self.projection.answer_description)
            )
        if (
            not isinstance(self.element_id, str)
            or not self.element_id
            or not isinstance(self.record_id, str)
            or not self.record_id
            or not isinstance(self.element_body_sha256, str)
            or len(self.element_body_sha256) != 64
            or any(char not in "0123456789abcdef" for char in self.element_body_sha256)
            or type(self.occurrence) is not int
            or self.occurrence < 0
            or self.role not in ("image_description", "image_ocr", "image_table_supplement")
        ):
            raise ValueError("画像本文の出現IDが不正です。")


@dataclass(frozen=True)
class VisionTextRange:
    start: int
    end: int
    source: VisionTextSource


@dataclass(frozen=True)
class ProjectedVisionText:
    text: str
    ranges: tuple[VisionTextRange, ...] = ()

    def __post_init__(self) -> None:
        previous = 0
        for item in self.ranges:
            if (
                type(item.start) is not int
                or type(item.end) is not int
                or not 0 <= item.start < item.end <= len(self.text)
            ):
                raise ValueError("画像本文の投影位置が本文の範囲外です。")
            if item.start < previous:
                raise ValueError("画像本文の投影位置が重複しています。")
            previous = item.end

    def slice(self, start: int, end: int) -> ProjectedVisionText:
        if (
            type(start) is not int
            or type(end) is not int
            or not 0 <= start <= end <= len(self.text)
        ):
            raise ValueError("画像本文の切り出し位置が範囲外です。")
        ranges = tuple(
            VisionTextRange(max(start, item.start) - start, min(end, item.end) - start, item.source)
            for item in self.ranges
            if item.start < end and start < item.end
        )
        return ProjectedVisionText(self.text[start:end], ranges)

    def splice(self, start: int, end: int, replacement: ProjectedVisionText) -> ProjectedVisionText:
        if not 0 <= start <= end <= len(self.text):
            raise ValueError("本文の置換位置が範囲外です。")
        return self.join([self.slice(0, start), replacement, self.slice(end, len(self.text))], "")

    def stripped(self) -> ProjectedVisionText:
        start = len(self.text) - len(self.text.lstrip())
        end = max(start, len(self.text.rstrip()))
        return self.slice(start, end)

    def whitespace_normalized(self) -> ProjectedVisionText:
        """既存の空白圧縮と同じ本文を作り、原文を検索せず各出力文字の由来を写す。"""
        if not self.ranges:
            return ProjectedVisionText(re.sub(r"\s+", " ", self.text).strip())
        owners: list[VisionTextSource | None] = [None] * len(self.text)
        for item in self.ranges:
            owners[item.start : item.end] = [item.source] * (item.end - item.start)
        output: list[str] = []
        output_owners: list[VisionTextSource | None] = []
        for match in re.finditer(r"\s+|\S+", self.text):
            if match.group().isspace():
                if match.start() == 0 or match.end() == len(self.text):
                    continue
                output.append(" ")
                source = owners[match.start()]
                output_owners.append(
                    source
                    if all(owner == source for owner in owners[match.start() : match.end()])
                    else None
                )
            else:
                output.extend(match.group())
                output_owners.extend(owners[match.start() : match.end()])
        ranges: list[VisionTextRange] = []
        start = 0
        while start < len(output_owners):
            source = output_owners[start]
            end = start + 1
            while end < len(output_owners) and output_owners[end] == source:
                end += 1
            if source is not None:
                ranges.append(VisionTextRange(start, end, source))
            start = end
        return ProjectedVisionText("".join(output), tuple(ranges))

    def line_endings_normalized(self) -> ProjectedVisionText:
        parts: list[ProjectedVisionText] = []
        cursor = 0
        for match in re.finditer(r"\r\n|\r", self.text):
            parts.append(self.slice(cursor, match.start()))
            original = self.slice(match.start(), match.end())
            source = (
                original.ranges[0].source
                if len(original.ranges) == 1
                and original.ranges[0].start == 0
                and original.ranges[0].end == len(original.text)
                else None
            )
            parts.append(
                ProjectedVisionText("\n", (VisionTextRange(0, 1, source),) if source else ())
            )
            cursor = match.end()
        parts.append(self.slice(cursor, len(self.text)))
        return self.join(parts, "")

    @classmethod
    def join(cls, parts: Sequence[ProjectedVisionText], separator: str) -> ProjectedVisionText:
        text: list[str] = []
        ranges: list[VisionTextRange] = []
        cursor = 0
        for index, part in enumerate(parts):
            if index:
                text.append(separator)
                cursor += len(separator)
            text.append(part.text)
            ranges.extend(
                VisionTextRange(cursor + item.start, cursor + item.end, item.source)
                for item in part.ranges
            )
            cursor += len(part.text)
        return cls("".join(text), tuple(ranges))


def prove_complete_assembly(
    raw_text: str, parts: Sequence[ProjectedVisionText], *, separator: str
) -> ProjectedVisionText:
    """宣言された順序の全体連結が一致するときだけ、累積位置を採用する。"""
    assembled = ProjectedVisionText.join(parts, separator)
    if assembled.text != raw_text:
        raise ValueError("画像本文と全文の組み立てが一致しません。再解析してください。")
    return assembled


VISION_TEXT_BINDING_KEY = "vision_text_binding"


def bind_element_vision(
    *,
    element_id: str,
    record_id: str,
    body: str,
    projection: VisionTextProjection,
    role: VisionTextRole,
    ranges: tuple[tuple[int, int], ...],
    supplement: str = "",
) -> str:
    """producerが保持するIDと追加時点の位置を保存する。本文から位置を推定しない。"""
    if not element_id or not record_id:
        raise ValueError("画像本文の結び付けに出所IDが必要です。")
    source = VisionTextSource(element_id, 0, projection, role, record_id, text_sha256(body))
    ProjectedVisionText(body, tuple(VisionTextRange(start, end, source) for start, end in ranges))
    return json.dumps(
        {
            "version": 1,
            "element_id": element_id,
            "record_id": record_id,
            "body_sha256": text_sha256(body),
            "role": role,
            "ranges": [{"start": start, "end": end} for start, end in ranges],
            "supplement_sha256": text_sha256(supplement),
            "projection": projection.dumps(),
        },
        ensure_ascii=False,
        separators=(",", ":"),
    )


def require_element_vision_projection(metadata: Mapping[str, Any]) -> None:
    """既知の旧画像由来を、新しい別画像の投影で認定しない。"""
    if metadata.get("vision_text_projection_status") == "unrecorded" or (
        metadata.get("figure_text_source") == "vision"
        and VISION_TEXT_BINDING_KEY not in metadata
        and load_vision_text_projection(metadata.get("vision_text_projection")) is None
    ):
        raise ValueError("旧画像説明の回答本文が未分離です。再解析してください。")


def projected_element_text(
    element_id: str, body: str, metadata: Mapping[str, Any], *, occurrence: int = 0
) -> ProjectedVisionText:
    """実要素のID・本文hashと一致する保存済み範囲だけを復元する。"""
    require_element_vision_projection(metadata)
    if VISION_TEXT_BINDING_KEY not in metadata:
        value = metadata.get("vision_text_projection")
        if value is None:
            return ProjectedVisionText(body)
        projection = load_vision_text_projection(value)
        if projection is None or projection.answer_description != body or not element_id:
            raise ValueError("画像本文の結び付けが未記録です。再解析してください。")
        source = VisionTextSource(element_id, occurrence, projection)
        return ProjectedVisionText(body, (VisionTextRange(0, len(body), source),) if body else ())
    try:
        binding = json.loads(metadata[VISION_TEXT_BINDING_KEY])
        if (
            not isinstance(binding, dict)
            or type(binding.get("version")) is not int
            or binding["version"] != 1
            or binding.get("element_id") != element_id
            or binding.get("body_sha256") != text_sha256(body)
            or not isinstance(binding.get("record_id"), str)
            or not binding["record_id"]
        ):
            raise ValueError
        role = binding.get("role")
        if role not in ("image_description", "image_ocr", "image_table_supplement"):
            raise ValueError
        projection = load_vision_text_projection(binding.get("projection"))
        if projection is None or not isinstance(binding.get("ranges"), list):
            raise ValueError
        source = VisionTextSource(
            element_id,
            occurrence,
            projection,
            cast(VisionTextRole, role),
            binding["record_id"],
            binding["body_sha256"],
        )
        return ProjectedVisionText(
            body,
            tuple(
                VisionTextRange(item["start"], item["end"], source) for item in binding["ranges"]
            ),
        )
    except (TypeError, KeyError, ValueError, json.JSONDecodeError) as exc:
        raise ValueError("画像本文の結び付けが一致しません。再解析してください。") from exc


VISION_CHUNK_MAP_KEY = "vision_text_chunk_map"


def chunk_projection_metadata(value: ProjectedVisionText) -> dict[str, str]:
    """回答本文と検索補助の投影を同じ本文hashへ結び付ける。"""
    if not value.ranges:
        return {}
    sources = list(dict.fromkeys(item.source for item in value.ranges))
    source_indices = {source: index for index, source in enumerate(sources)}
    payload = {
        "version": 1,
        "body_sha256": text_sha256(value.text),
        "sources": [
            {
                "element_id": source.element_id,
                "occurrence": source.occurrence,
                "role": source.role,
                "record_id": source.record_id,
                "element_body_sha256": source.element_body_sha256,
                "projection": source.projection.dumps(),
            }
            for source in sources
        ],
        "ranges": [
            {"start": item.start, "end": item.end, "source_index": source_indices[item.source]}
            for item in value.ranges
        ],
    }
    return {
        VISION_CHUNK_MAP_KEY: json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
        "answer_text_projection_json": json.dumps(
            {"version": 1, "body_sha256": text_sha256(value.text)}, separators=(",", ":")
        ),
        "engine_search_text": "\n\n".join(
            [value.text, *[source.projection.index_text for source in sources]]
        ),
    }


def chunk_projected_text(body: str, metadata: Mapping[str, Any]) -> ProjectedVisionText:
    if VISION_CHUNK_MAP_KEY not in metadata:
        return ProjectedVisionText(body)
    try:
        raw = json.loads(metadata[VISION_CHUNK_MAP_KEY])
        if (
            not isinstance(raw, dict)
            or type(raw.get("version")) is not int
            or raw["version"] != 1
            or raw.get("body_sha256") != text_sha256(body)
            or not isinstance(raw.get("ranges"), list)
        ):
            raise ValueError
        if not isinstance(raw.get("sources"), list):
            raise ValueError
        sources = []
        for row in raw["sources"]:
            projection = load_vision_text_projection(row["projection"])
            if projection is None or row["role"] not in (
                "image_description",
                "image_ocr",
                "image_table_supplement",
            ):
                raise ValueError
            sources.append(
                VisionTextSource(
                    row["element_id"],
                    row["occurrence"],
                    projection,
                    cast(VisionTextRole, row["role"]),
                    row["record_id"],
                    row["element_body_sha256"],
                )
            )
        ranges = []
        for row in raw["ranges"]:
            index = row["source_index"]
            if type(index) is not int or not 0 <= index < len(sources):
                raise ValueError
            ranges.append(VisionTextRange(row["start"], row["end"], sources[index]))
        return ProjectedVisionText(body, tuple(ranges))
    except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise ValueError("画像本文の分割位置が一致しません。再分割してください。") from exc


VISION_ASSEMBLY_KEY = "vision_text_assembly"


def complete_vision_assembly(
    raw_text: str, elements: Sequence[tuple[str, str, str]]
) -> dict[str, Any]:
    """抽出の宣言順の全体連結だけを採用し、同文の各出現を別ID/位置に保つ。"""
    identities = [
        {
            "element_id": element_id,
            "occurrence": index,
            "body_sha256": text_sha256(body),
            "kind": kind,
        }
        for index, (element_id, body, kind) in enumerate(elements)
    ]
    for omit_tables in (False, True):
        included = [
            index
            for index, (_element_id, body, kind) in enumerate(elements)
            if body.strip() and not (omit_tables and kind == "table")
        ]
        for separator in ("\n\n", "\n"):
            try:
                prove_complete_assembly(
                    raw_text,
                    [ProjectedVisionText(elements[index][1]) for index in included],
                    separator=separator,
                )
            except ValueError:
                continue
            ranges = []
            cursor = 0
            for position, index in enumerate(included):
                if position:
                    cursor += len(separator)
                body = elements[index][1]
                ranges.append({"occurrence": index, "start": cursor, "end": cursor + len(body)})
                cursor += len(body)
            return {
                "version": 1,
                "status": "mapped",
                "raw_sha256": text_sha256(raw_text),
                "elements": identities,
                "separator": separator,
                "ranges": ranges,
            }
    return {
        "version": 1,
        "status": "unmapped",
        "raw_sha256": text_sha256(raw_text),
        "elements": identities,
    }


def positioned_vision_assembly(
    raw_text: str, elements: Sequence[tuple[str, str, str]], metadata: Sequence[Mapping[str, Any]]
) -> dict[str, Any]:
    complete = complete_vision_assembly(raw_text, elements)
    if complete["status"] == "mapped":
        return complete
    ranges = []
    for index, ((_element_id, body, _kind), values) in enumerate(
        zip(elements, metadata, strict=True)
    ):
        if "vision_text_binding" not in values and "vision_text_projection" not in values:
            continue
        projected = projected_element_text(_element_id, body, values, occurrence=index)
        if not projected.ranges:
            continue
        start, end = values.get("raw_start"), values.get("raw_end")
        if (
            type(start) is not int
            or type(end) is not int
            or not 0 <= start <= end <= len(raw_text)
            or raw_text[start:end] != body
        ):
            return complete
        if "raw_source_sha256" in values and values["raw_source_sha256"] != text_sha256(raw_text):
            return complete
        ranges.append({"occurrence": index, "start": start, "end": end})
    ordered = sorted(ranges, key=lambda item: item["start"])
    if any(left["end"] > right["start"] for left, right in zip(ordered, ordered[1:], strict=False)):
        return complete
    return {**complete, "status": "mapped", "ranges": ranges, "position_basis": "producer_offsets"}


VISION_RAW_PROJECTION_KEY = "vision_text_raw_projection"


@dataclass
class VisionRawWriter:
    """元のrawをopaqueに保ち、確認済み位置の変更・新しい追記だけを記録する。"""

    value: ProjectedVisionText
    slots: dict[str, tuple[int, int]]
    before_sha256: str

    @classmethod
    def create(
        cls,
        raw: str,
        elements: Sequence[tuple[str, str, str]],
        metadata: Sequence[Mapping[str, Any]],
        saved: object | None = None,
    ) -> VisionRawWriter:
        if any(
            values.get("vision_text_projection_status") == "unrecorded"
            or (
                values.get("figure_text_source") == "vision"
                and load_vision_text_projection(values.get("vision_text_projection")) is None
            )
            for values in metadata
        ):
            raise ValueError("旧画像説明の本文が未分離です。再解析してください。")
        for (element_id, body, _kind), values in zip(elements, metadata, strict=True):
            if VISION_TEXT_BINDING_KEY in values or "vision_text_projection" in values:
                projected_element_text(element_id, body, values)
        assembly = complete_vision_assembly(raw, elements)
        slots = {}
        counts = Counter(element_id for element_id, _body, _kind in elements)
        if assembly["status"] == "mapped":
            for row in assembly["ranges"]:
                element_id = elements[row["occurrence"]][0]
                if element_id and counts[element_id] == 1:
                    slots[element_id] = (row["start"], row["end"])
        for (element_id, body, _kind), values in zip(elements, metadata, strict=True):
            start, end = values.get("raw_start"), values.get("raw_end")
            if (
                element_id
                and counts[element_id] == 1
                and type(start) is int
                and type(end) is int
                and 0 <= start <= end <= len(raw)
                and raw[start:end] == body
                and (
                    "raw_source_sha256" not in values
                    or values["raw_source_sha256"] == text_sha256(raw)
                )
            ):
                slots[element_id] = (start, end)
        ordered = sorted(slots.values())
        if any(left[1] > right[0] for left, right in zip(ordered, ordered[1:], strict=False)):
            slots = {}
        value = (
            chunk_projected_text(raw, {VISION_CHUNK_MAP_KEY: saved})
            if saved is not None
            else ProjectedVisionText(raw)
        )
        return cls(value, slots, text_sha256(raw))

    def write(
        self,
        element_id: str,
        old: str,
        replacement: ProjectedVisionText,
        *,
        after_id: str | None = None,
    ) -> tuple[int, int]:
        slot = self.slots.get(element_id)
        if slot is not None and self.value.text[slot[0] : slot[1]] == old:
            start, end = slot
            delta = len(replacement.text) - (end - start)
            self.value = self.value.splice(start, end, replacement)
            updated = {}
            for key, (left, right) in self.slots.items():
                if key == element_id:
                    continue
                if right <= start:
                    updated[key] = (left, right)
                elif left >= end:
                    updated[key] = (left + delta, right + delta)
            self.slots = updated
        else:
            if not replacement.ranges:
                return (-1, -1)
            # 位置不明の元本文は複写せず、producerが作った画像補足だけを追加する。
            replacement = ProjectedVisionText.join(
                [replacement.slice(item.start, item.end) for item in replacement.ranges], "\n"
            ).stripped()
            anchor = self.slots.get(after_id) if after_id is not None else None
            position = anchor[1] if anchor is not None else len(self.value.text)
            separator = "\n\n" if self.value.text else ""
            addition = ProjectedVisionText.join([ProjectedVisionText(separator), replacement], "")
            self.value = self.value.splice(position, position, addition)
            delta = len(addition.text)
            self.slots = {
                key: ((left + delta, right + delta) if left >= position else (left, right))
                for key, (left, right) in self.slots.items()
                if right <= position or left >= position
            }
            start = position + len(separator)
        self.slots[element_id] = (start, start + len(replacement.text))
        return self.slots[element_id]

    def tag(self, element_id: str, old: str, source: VisionTextSource) -> bool:
        slot = self.slots.get(element_id)
        if slot is None or self.value.text[slot[0] : slot[1]] != old:
            return False
        occupied = [
            item for item in self.value.ranges if item.start < slot[1] and slot[0] < item.end
        ]
        if occupied:
            return (
                len(occupied) == 1
                and occupied[0].start == slot[0]
                and occupied[0].end == slot[1]
                and occupied[0].source.element_id == source.element_id
                and occupied[0].source.role == source.role
                and occupied[0].source.projection == source.projection
            )
        ranges = tuple(
            sorted(
                [*self.value.ranges, VisionTextRange(slot[0], slot[1], source)],
                key=lambda item: item.start,
            )
        )
        self.value = ProjectedVisionText(self.value.text, ranges)
        return True

    def saved_map(self) -> str:
        payload = json.loads(chunk_projection_metadata(self.value).get(VISION_CHUNK_MAP_KEY, "{}"))
        if not payload:
            payload = {
                "version": 1,
                "body_sha256": text_sha256(self.value.text),
                "sources": [],
                "ranges": [],
            }
        payload["before_sha256"] = self.before_sha256
        return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))


def updated_chunk_projection_metadata(
    metadata: Mapping[str, Any], value: ProjectedVisionText
) -> dict[str, Any]:
    result = dict(metadata)
    if VISION_CHUNK_MAP_KEY in result:
        for key in (VISION_CHUNK_MAP_KEY, "answer_text_projection_json", "engine_search_text"):
            result.pop(key, None)
    result.update(chunk_projection_metadata(value))
    return result
