"""Unstructured の要素に原本の位置を付ける（#4100。形式の座標は ``source_map``）。

parser のサービスの中で、Unstructured の公開の関数を位置の分かる区切りごとに当てる（private な関数や
属性の動きに依存しない。テストで確かめる）。版を上げて結果が全体の解析と合わなくなったら位置を記録
しないだけにする（解析は止めない）。
"""

from __future__ import annotations

import csv
import logging
import re
from collections.abc import Mapping, Sequence
from typing import Any

from rag_parser_core.source_map import (
    SCHEME_DELIMITED_ROW,
    SCHEME_HTML_SOURCE,
    SCHEME_JSON_POINTER,
    SCHEME_JSONL_LINE,
    SCHEME_MARKDOWN_SOURCE,
    SCHEME_TEXT_CHAR,
    SCHEME_XML_SOURCE,
    SOURCE_WINDOW_CODEPOINTS,
    LineIndex,
    SourceRange,
    decode_source_text,
    delimited_element_ranges,
    html_segments,
    iter_text_blocks,
    json_element_ranges,
    jsonl_element_ranges,
    nonblank_line_ranges,
    source_map_artifact,
    text_locator,
    xml_leaves,
)

logger = logging.getLogger(__name__)

_TEXT_EXTENSIONS = frozenset({".txt", ".text", ".log", ".rst", ".org"})
_MARKDOWN_EXTENSIONS = frozenset({".md", ".markdown"})
_HTML_EXTENSIONS = frozenset({".html", ".htm", ".xhtml"})
_TEXT_CONTENT_TYPES = frozenset({"text/plain"})
_MARKDOWN_CONTENT_TYPES = frozenset({"text/markdown", "text/x-markdown"})
_HTML_CONTENT_TYPES = frozenset({"text/html", "application/xhtml+xml"})
_XML_CONTENT_TYPES = frozenset({"application/xml", "text/xml"})
_NDJSON_CONTENT_TYPES = frozenset(
    {"application/x-ndjson", "application/jsonl", "application/ndjson"}
)

ElementRanges = tuple[str, dict[str, list[SourceRange]]]


def source_format(extension: str, content_type: str) -> str | None:
    """位置を記録する形式。拡張子を優先する（Unstructured に渡す判定と同じ順）。"""
    normalized = content_type.split(";", 1)[0].strip().casefold()
    if extension in _TEXT_EXTENSIONS:
        return "text"
    if extension in _MARKDOWN_EXTENSIONS:
        return "markdown"
    if extension in _HTML_EXTENSIONS:
        return "html"
    if extension == ".xml":
        return "xml"
    if extension == ".csv":
        return "csv"
    if extension == ".tsv":
        return "tsv"
    if extension in {".jsonl", ".ndjson"}:
        return "jsonl"
    if extension == ".json":
        return "json"
    if normalized in _TEXT_CONTENT_TYPES:
        return "text"
    if normalized in _MARKDOWN_CONTENT_TYPES:
        return "markdown"
    if normalized in _HTML_CONTENT_TYPES:
        return "html"
    if normalized in _XML_CONTENT_TYPES:
        return "xml"
    if normalized in {"text/csv", "application/csv"}:
        return "csv"
    if normalized == "text/tab-separated-values":
        return "tsv"
    if normalized in _NDJSON_CONTENT_TYPES:
        return "jsonl"
    if normalized == "application/json":
        return "json"
    return None


def _element_text(item: object) -> str:
    return str(getattr(item, "text", "") or "")


def _text_ranges(
    text: str, items: Sequence[object], ids: Sequence[str]
) -> dict[str, list[SourceRange]]:
    """Unstructured と同じ段落の処理を、位置の分かる区切りごとに当てる。"""
    from unstructured.cleaners.core import (
        auto_paragraph_grouper,
        clean_bullets,
        group_broken_paragraphs,
        new_line_grouper,
    )
    from unstructured.nlp.patterns import (
        DOUBLE_PARAGRAPH_PATTERN_RE,
        PARAGRAPH_PATTERN,
        UNICODE_BULLETS_RE,
    )

    line_mode = auto_paragraph_grouper(text) == new_line_grouper(text)
    separator = re.compile(r"\n") if line_mode else DOUBLE_PARAGRAPH_PATTERN_RE
    group = new_line_grouper if line_mode else group_broken_paragraphs
    lines = LineIndex(text)
    ranges: dict[str, list[SourceRange]] = {}
    ordinal = 0
    for start, end in iter_text_blocks(text, separator):
        # 段落の行に分け、記号だけの行を除く（partition_text の読み方と同じ）。
        outputs = [
            value.strip()
            for value in re.split(PARAGRAPH_PATTERN, group(text[start:end]).strip())
            if value.strip()
            and not (len(value.strip()) == 1 and UNICODE_BULLETS_RE.match(value.strip()))
        ]
        block_lines = nonblank_line_ranges(text, start, end)
        for index, output in enumerate(outputs):
            if ordinal >= len(items):
                return {}
            element_text = _element_text(items[ordinal])
            if element_text != output and element_text != clean_bullets(output):
                return {}
            a, b = block_lines[index] if len(outputs) == len(block_lines) else (start, end)
            ranges[ids[ordinal]] = [SourceRange(a, b, text_locator(lines, a, b))]
            ordinal += 1
    return ranges if ordinal == len(items) else {}


def _markdown_segments(text: str) -> tuple[list[tuple[int, int, list[tuple[int, int]]]], str]:
    """markdown-it の最上位のブロックの行の範囲を、空行で隔たっていないものでまとめる。

    python-markdown は空行で区切って読むので、空行の無いブロックは同じ区切りに入れる。区切りの中の
    リストの項目の行も返す。ブロックの外の行（参照の定義）は各区切りの解析に添える。
    """
    from markdown_it import MarkdownIt

    tokens = MarkdownIt("commonmark").enable("table").parse(text)
    lines = text.split("\n")
    segments: list[tuple[int, int, list[tuple[int, int]]]] = []
    covered: set[int] = set()
    for index, token in enumerate(tokens):
        if token.level != 0 or not token.map:
            continue
        start, end = token.map
        covered.update(range(start, end))
        items: list[tuple[int, int]] = []
        if token.type in {"bullet_list_open", "ordered_list_open"}:
            for child in tokens[index + 1 :]:
                if child.level == 0:
                    break
                if child.type == "list_item_open" and child.level == 1 and child.map:
                    items.append((child.map[0], child.map[1]))
        # 空行が間にあれば別の区切りにする（リストの map の終わりは後ろの空行を含むことがある）。
        if segments and segments[-1][1] == start and lines[start - 1].strip():
            segments[-1] = (segments[-1][0], end, [])
        else:
            segments.append((start, end, items))
    references = "\n".join(
        line for number, line in enumerate(lines) if number not in covered and line.strip()
    )
    return segments, references


def _markdown_ranges(
    text: str, items: Sequence[object], ids: Sequence[str]
) -> dict[str, list[SourceRange]]:
    """区切りごとの解析の連結が全体の解析と同じときだけ、区切りの行の範囲を位置にする。"""
    from unstructured.partition.md import partition_md

    segments, references = _markdown_segments(text)
    index = LineIndex(text)
    lines = text.split("\n")
    ranges: dict[str, list[SourceRange]] = {}
    ordinal = 0
    for first, last, list_items in segments:
        body = "\n".join(lines[first:last])
        parts = partition_md(text=f"{body}\n\n{references}", languages=[""])
        for position, part in enumerate(parts):
            if ordinal >= len(items) or _element_text(part) != _element_text(items[ordinal]):
                return {}
            line_start, line_end = (
                list_items[position] if len(list_items) == len(parts) else (first, last)
            )
            a = index.start_of(line_start)
            b = max(a, index.start_of(line_end) - 1)
            ranges[ids[ordinal]] = [SourceRange(a, b, f"line={line_start},{line_end}")]
            ordinal += 1
    return ranges if ordinal == len(items) else {}


def _html_ranges(
    text: str, items: Sequence[object], ids: Sequence[str]
) -> dict[str, list[SourceRange]]:
    """記録したタグの区切りごとに解析し、連結が全体の解析と同じときだけ区切りの位置を使う。"""
    from unstructured.partition.html import partition_html

    ranges: dict[str, list[SourceRange]] = {}
    ordinal = 0
    for segment in html_segments(text):
        fragment = text[segment.start : segment.end]
        if not fragment.strip():
            continue
        try:
            parts = partition_html(text=fragment, languages=[""], skip_headers_and_footers=False)
        except ValueError:
            # 文字の無い区切り（スクリプトだけなど）は要素を作らない。
            parts = []
        locator = f"line={segment.line};col={segment.column};tag={segment.tag}"
        for part in parts:
            if ordinal >= len(items) or _element_text(part) != _element_text(items[ordinal]):
                return {}
            ranges[ids[ordinal]] = [SourceRange(segment.start, segment.end, locator)]
            ordinal += 1
    return ranges if ordinal == len(items) else {}


def _xml_ranges(
    text: str, items: Sequence[object], ids: Sequence[str]
) -> dict[str, list[SourceRange]]:
    """閉じる順の文字のある要素と、Unstructured の要素を順番どおりに対応させる。"""
    from unstructured.cleaners.core import clean_bullets

    leaves = xml_leaves(text)
    if len(leaves) != len(items):
        return {}
    ranges: dict[str, list[SourceRange]] = {}
    for leaf, item, element_id in zip(leaves, items, ids, strict=True):
        output = leaf.text.strip()
        element_text = _element_text(item).strip()
        if element_text != output and element_text != clean_bullets(output):
            return {}
        ranges[element_id] = [leaf.source_range]
    return ranges


def unstructured_source_map(
    data: bytes,
    *,
    extension: str,
    content_type: str,
    items: Sequence[object],
    ids: Sequence[str],
    element_texts: Mapping[str, str],
) -> dict[str, Any] | None:
    """位置の対応を作る。検証に通らないとき・形式の対象外のときは None（解析は止めない）。

    ``items`` / ``ids`` は Unstructured の出力の順の要素と要素 ID、``element_texts`` は adapter が
    作った最終の本文（csv / tsv の Markdown の表など）。
    """
    kind = source_format(extension, content_type)
    if kind is None:
        return None
    text = decode_source_text(data)
    if len(text) <= SOURCE_WINDOW_CODEPOINTS:
        return None
    try:
        scheme, ranges = _element_ranges(kind, text, items, ids, element_texts)
    except Exception:  # noqa: BLE001 - 位置の記録の失敗で解析を止めない。
        logger.warning("source_map_failed", extra={"format": kind}, exc_info=True)
        return None
    if not ranges:
        logger.info("source_map_unverified", extra={"format": kind})
    return source_map_artifact(data, text, scheme, ranges)


def _sniffed_csv_delimiter(text: str) -> str:
    """Unstructured の partition_csv と同じ判定（1 列なら区切りは位置に関係しない）。"""
    try:
        return csv.Sniffer().sniff(text[:65536], delimiters=",;|").delimiter
    except csv.Error:
        return ","


def _element_ranges(
    kind: str,
    text: str,
    items: Sequence[object],
    ids: Sequence[str],
    element_texts: Mapping[str, str],
) -> ElementRanges:
    if kind == "text":
        return SCHEME_TEXT_CHAR, _text_ranges(text, items, ids)
    if kind == "markdown":
        return SCHEME_MARKDOWN_SOURCE, _markdown_ranges(text, items, ids)
    if kind == "html":
        return SCHEME_HTML_SOURCE, _html_ranges(text, items, ids)
    if kind == "xml":
        return SCHEME_XML_SOURCE, _xml_ranges(text, items, ids)
    if kind == "jsonl":
        ranges = jsonl_element_ranges(text, [_element_text(item) for item in items])
        return SCHEME_JSONL_LINE, (
            {element_id: [item] for element_id, item in zip(ids, ranges, strict=True)}
            if ranges
            else {}
        )
    if len(ids) != 1 or ids[0] not in element_texts:
        return (SCHEME_DELIMITED_ROW if kind in {"csv", "tsv"} else SCHEME_JSON_POINTER), {}
    element_id = ids[0]
    if kind in {"csv", "tsv"}:
        delimiter = "\t" if kind == "tsv" else _sniffed_csv_delimiter(text)
        rows = delimited_element_ranges(text, element_texts[element_id], delimiter=delimiter)
        return SCHEME_DELIMITED_ROW, ({element_id: rows} if rows else {})
    units = json_element_ranges(text, element_texts[element_id])
    return SCHEME_JSON_POINTER, ({element_id: units} if units else {})
