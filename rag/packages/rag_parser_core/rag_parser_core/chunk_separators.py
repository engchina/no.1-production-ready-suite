"""分割符を優先度順に試して本文を切る共通の処理(#4733)。"""

from __future__ import annotations

from collections.abc import Sequence

# structure_aware / recursive_character / small_to_big が優先度順に試す区切りの既定。
DEFAULT_CHUNK_SEPARATORS: tuple[str, ...] = (
    "\n\n",
    "\n",
    "。",
    "！",
    "？",
    ". ",
    "! ",
    "? ",
    "；",
    ";",
)
# 文末の記号。断片から除かず前の文に残す。
_SENTENCE_END_SEPARATORS = frozenset(DEFAULT_CHUNK_SEPARATORS[2:])


def split_delimiter_spans(text: str, start: int, end: int, delimiter: str) -> list[tuple[int, int]]:
    """``text[start:end]`` を分割符で切り、前後の空白を除いた空でない範囲を返す。"""
    kept = len(delimiter) if delimiter in _SENTENCE_END_SEPARATORS else 0
    spans: list[tuple[int, int]] = []
    cursor = start
    while (hit := text.find(delimiter, cursor, end)) >= 0:
        spans.append((cursor, hit + kept))
        cursor = hit + len(delimiter)
    spans.append((cursor, end))
    trimmed: list[tuple[int, int]] = []
    for span_start, span_end in spans:
        part = text[span_start:span_end]
        if part.strip():
            leading = len(part) - len(part.lstrip())
            trimmed.append((span_start + leading, span_start + len(part.rstrip())))
    return trimmed


def split_oversized_span(
    text: str, start: int, end: int, delimiters: Sequence[str], limit: int
) -> list[tuple[int, int]]:
    """``limit`` を超える範囲だけ、残りの分割符で順に切り直す。"""
    if end - start <= limit or not delimiters:
        return [(start, end)]
    head, *tail = delimiters
    return [
        leaf
        for part_start, part_end in split_delimiter_spans(text, start, end, head)
        for leaf in split_oversized_span(text, part_start, part_end, tail, limit)
    ]


def separator_unit_spans(text: str, separators: Sequence[str], limit: int) -> list[tuple[int, int]]:
    """本文を ``limit`` に収まる単位へ、``separators`` の優先度順に切った範囲を返す。"""
    return split_oversized_span(text, 0, len(text), separators, limit)
