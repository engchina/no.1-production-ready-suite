"""JSON の文字列値に由来する範囲だけを、抽出の整形後も保持する。"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class SourceSpan:
    start: int
    end: int
    pointer: str
    source_start: int
    source_end: int

    def shifted(self, offset: int) -> SourceSpan:
        return SourceSpan(
            self.start + offset, self.end + offset, self.pointer, self.source_start, self.source_end
        )

    def to_dict(self) -> dict[str, int | str]:
        return {
            "start": self.start,
            "end": self.end,
            "pointer": self.pointer,
            "source_start": self.source_start,
            "source_end": self.source_end,
        }


class MappedText(str):
    spans: tuple[SourceSpan, ...]

    def __new__(cls, value: str, spans: Iterable[SourceSpan] = ()) -> MappedText:
        instance = super().__new__(cls, value)
        instance.spans = tuple(spans)
        return instance


def pointer_child(pointer: str, key: str | int) -> str:
    return pointer + "/" + str(key).replace("~", "~0").replace("/", "~1")


def mapped_payload(value: Any, pointer: str = "") -> Any:
    """値を複写してノード由来を付ける。キーや元の payload は変更しない。"""
    if isinstance(value, str):
        return MappedText(
            value, [SourceSpan(0, len(value), pointer, 0, len(value))] if value else []
        )
    if isinstance(value, Mapping):
        return {
            key: mapped_payload(child, pointer_child(pointer, key)) for key, child in value.items()
        }
    if isinstance(value, list):
        return [mapped_payload(child, pointer_child(pointer, i)) for i, child in enumerate(value)]
    return value


def mapped_join(separator: str, values: Iterable[str]) -> MappedText:
    parts = list(values)
    spans: list[SourceSpan] = []
    offset = 0
    for i, part in enumerate(parts):
        if i:
            offset += len(separator)
        if isinstance(part, MappedText):
            spans.extend(span.shifted(offset) for span in part.spans)
        offset += len(part)
    return MappedText(separator.join(parts), spans)


def mapped_slice(value: str, start: int = 0, end: int | None = None) -> MappedText:
    start, end, _ = slice(start, end).indices(len(value))
    spans: list[SourceSpan] = []
    if isinstance(value, MappedText):
        for span in value.spans:
            a, b = max(start, span.start), min(end, span.end)
            if a < b:
                spans.append(
                    SourceSpan(
                        a - start,
                        b - start,
                        span.pointer,
                        span.source_start + a - span.start,
                        span.source_start + b - span.start,
                    )
                )
    return MappedText(value[start:end], spans)


def mapped_strip(value: str) -> MappedText:
    return mapped_slice(value, len(value) - len(value.lstrip()), len(value.rstrip()))


def mapped_clean(value: str) -> MappedText:
    """正規化で置換した空白は、原文と同じ範囲として偽装しない。"""
    parts: list[str] = []
    last = 0
    for match in re.finditer(r"[ \t\r\n]+", value):
        parts.extend((mapped_slice(value, last, match.start()), " "))
        last = match.end()
    parts.append(mapped_slice(value, last))
    return mapped_strip(mapped_join("", parts))


def mapped_replace(value: str, old: str, new: str) -> MappedText:
    """書式の置換部分は対応外とし、前後の原文だけを残す。"""
    parts: list[str] = []
    last = 0
    for match in re.finditer(re.escape(old), value):
        parts.extend((mapped_slice(value, last, match.start()), new))
        last = match.end()
    parts.append(mapped_slice(value, last))
    return mapped_join("", parts)


def _next_raw_character(text: str, position: int) -> int:
    if text[position] != "\\":
        return position + 1
    if text[position + 1] != "u":
        return position + 2
    code = int(text[position + 2 : position + 6], 16)
    position += 6
    if (
        0xD800 <= code <= 0xDBFF
        and text[position : position + 2] == "\\u"
        and 0xDC00 <= int(text[position + 2 : position + 6], 16) <= 0xDFFF
    ):
        position += 6
    return position


@dataclass(frozen=True)
class JsonStringRange:
    value: str
    source: str
    # 全文字の Python int 配列を作らず、1024 codepoint ごとの疎な境界だけを保持する。
    checkpoints: tuple[int, ...]
    end: int

    def _boundary(self, index: int) -> int:
        position = self.checkpoints[index // 1024]
        for _ in range(index % 1024):
            position = _next_raw_character(self.source, position)
        return position

    def raw_range(self, start: int, end: int) -> tuple[int, int]:
        if (
            type(start) is not int
            or type(end) is not int
            or not 0 <= start <= end <= len(self.value)
        ):
            raise ValueError("invalid source range")
        return self._boundary(start), self._boundary(end)


def _string_range(text: str, value: str, start: int, end: int) -> JsonStringRange:
    checkpoints = [start]
    position, count = start, 0
    while position < end:
        position = _next_raw_character(text, position)
        count += 1
        if count % 1024 == 0:
            checkpoints.append(position)
    if count != len(value) or position != end:
        raise ValueError("invalid decoded coordinates")
    return JsonStringRange(value, text, tuple(checkpoints), end)


def raw_string_ranges(data: bytes, pointers: set[str]) -> tuple[str, dict[str, JsonStringRange]]:
    """JSON を構造順に読み、要求された文字列ノードの位置だけを返す。

    戻り値の位置は UTF-8 bytes ではなく、BOM を除いた原 JSON の codepoint。
    同名キーを含む object は位置が一意でないため、原本対応を成功にしない。
    """
    import json

    if len(data) > 128 * 1024 * 1024:
        raise ValueError("source too large")
    text = data.decode("utf-8-sig")
    decoder = json.JSONDecoder()
    found: dict[str, JsonStringRange] = {}

    def skip_space(position: int) -> int:
        match = re.compile(r"[ \t\r\n]*").match(text, position)
        assert match is not None
        return match.end()

    def read(position: int, pointer: str) -> int:
        position = skip_space(position)
        if position >= len(text):
            raise ValueError("incomplete JSON")
        token = text[position]
        if token == '"':
            value, end = decoder.raw_decode(text, position)
            if not isinstance(value, str):
                raise ValueError("invalid string")
            if pointer in pointers:
                found[pointer] = _string_range(text, value, position + 1, end - 1)
            return end
        if token in "[{":
            object_node = token == "{"
            closing = "}" if object_node else "]"
            position = skip_space(position + 1)
            keys: set[str] = set()
            index = 0
            if position < len(text) and text[position] == closing:
                return position + 1
            while True:
                key: str | int = index
                if object_node:
                    if position >= len(text) or text[position] != '"':
                        raise ValueError("invalid object key")
                    decoded_key, position = decoder.raw_decode(text, position)
                    if not isinstance(decoded_key, str):
                        raise ValueError("invalid object key")
                    key = decoded_key
                    if decoded_key in keys:
                        raise ValueError("duplicate object key")
                    keys.add(decoded_key)
                    position = skip_space(position)
                    if position >= len(text) or text[position] != ":":
                        raise ValueError("invalid object separator")
                    position += 1
                position = skip_space(read(position, pointer_child(pointer, key)))
                if position >= len(text):
                    raise ValueError("incomplete JSON")
                if text[position] == closing:
                    return position + 1
                if text[position] != ",":
                    raise ValueError("invalid JSON separator")
                position = skip_space(position + 1)
                index += 1
        _, end = decoder.raw_decode(text, position)
        return end

    if skip_space(read(0, "")) != len(text):
        raise ValueError("trailing JSON content")
    return text, found
