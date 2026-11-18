"""頁の無い原本の chunk に、原本の行の範囲を付ける（#5450）。

解析の原本の位置（``parser_artifacts.source_map``。#4100）は要素の範囲ごとに原本の行（1 始まり。
``line_start`` / ``line_end``）を持つ。chunk の ``element_ids`` の行の最小・最大を
``source_line_start`` / ``source_line_end`` に、small-to-big の子には親（同じ ``parent_chunk_id`` の
子の全体）の行の範囲と、子の見出しがそろうときの見出しを ``parent_*`` に書く。回答の根拠の場所を
「見出し＋行の範囲」で示すのに使う。

``element_ids`` を持たない分割（文字数・区切り文字・見出しでの分割）の chunk と、1 つの要素が
文書全体になる形式（CSV・JSON の単位の範囲）は行を付けない。既に行を持つ chunk（CSV の記録）は
変えない。
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from rag_parser_core.sheet_records import SOURCE_LINE_END_KEY, SOURCE_LINE_START_KEY
from rag_parser_core.source_map import FRAGMENT_SCHEMES, SOURCE_MAP_KEY

from app.rag.element_locator import chunk_element_ids

PARENT_SOURCE_LINE_START_KEY = "parent_source_line_start"
PARENT_SOURCE_LINE_END_KEY = "parent_source_line_end"
PARENT_SECTION_TITLE_KEY = "parent_section_title"


def _element_lines(parser_artifacts: Mapping[str, Any]) -> dict[str, tuple[int, int]]:
    source_map = parser_artifacts.get(SOURCE_MAP_KEY)
    if not isinstance(source_map, Mapping) or source_map.get("scheme") in FRAGMENT_SCHEMES:
        return {}
    elements = source_map.get("elements")
    lines: dict[str, tuple[int, int]] = {}
    for element_id, ranges in elements.items() if isinstance(elements, Mapping) else ():
        pairs = [
            (item["line_start"], item["line_end"])
            for item in ranges or ()
            if isinstance(item, Mapping)
            and type(item.get("line_start")) is int
            and type(item.get("line_end")) is int
        ]
        if pairs:
            lines[str(element_id)] = (min(a for a, _ in pairs), max(b for _, b in pairs))
    return lines


def apply_source_lines(chunks: Sequence[Any], parser_artifacts: Mapping[str, Any]) -> None:
    lines = _element_lines(parser_artifacts)
    if not lines:
        return
    parents: dict[str, list[Any]] = {}
    for chunk in chunks:
        metadata = chunk.metadata
        found = [lines[item] for item in chunk_element_ids(metadata) if item in lines]
        if found and metadata.get(SOURCE_LINE_START_KEY) is None:
            metadata[SOURCE_LINE_START_KEY] = min(start for start, _ in found)
            metadata[SOURCE_LINE_END_KEY] = max(end for _, end in found)
        if (parent := metadata.get("parent_chunk_id")) and metadata.get("parent_text"):
            parents.setdefault(str(parent), []).append(chunk)
    for members in parents.values():
        ranges = [
            (member.metadata[SOURCE_LINE_START_KEY], member.metadata[SOURCE_LINE_END_KEY])
            for member in members
            if type(member.metadata.get(SOURCE_LINE_START_KEY)) is int
            and type(member.metadata.get(SOURCE_LINE_END_KEY)) is int
        ]
        titles = {str(member.metadata.get("section_title") or "") for member in members}
        for member in members:
            if ranges:
                member.metadata[PARENT_SOURCE_LINE_START_KEY] = min(a for a, _ in ranges)
                member.metadata[PARENT_SOURCE_LINE_END_KEY] = max(b for _, b in ranges)
            if len(titles) == 1 and (title := next(iter(titles))):
                # 親が複数の節にまたがるときは見出しを付けない（どの節か一つに決まらない）。
                member.metadata[PARENT_SECTION_TITLE_KEY] = title


__all__ = [
    "PARENT_SECTION_TITLE_KEY",
    "PARENT_SOURCE_LINE_END_KEY",
    "PARENT_SOURCE_LINE_START_KEY",
    "apply_source_lines",
]
