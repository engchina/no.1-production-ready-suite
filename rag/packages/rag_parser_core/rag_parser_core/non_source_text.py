"""解析器が本文に付けた、原文に無い範囲（#5305）。

本文を組み立てた解析器が、自分で付けた文字列（見出しの行など）の範囲を宣言する。範囲は語や正規表現では
決めず、宣言した側の座標だけを使う。分割・根拠の切り出し・結合は、この関数で範囲を自分の本文の座標に移す。
回答の引用はこの範囲を引用単位から外す（原文に無い文字列を原文の引用にしない）。

要素・chunk の metadata には JSON の文字列（``[[start, end], ...]``）で、根拠（span）には
list で持つ。
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Sequence

NON_SOURCE_RANGES_KEY = "non_source_text_ranges"
# small-to-big の親の本文（``parent_text``）の座標の範囲。
PARENT_NON_SOURCE_RANGES_KEY = "parent_non_source_text_ranges"

type TextRanges = tuple[tuple[int, int], ...]


def normalize_ranges(ranges: Iterable[tuple[int, int]], length: int) -> TextRanges:
    """本文の長さの中に切り詰め、並べ、重なり・接する範囲をまとめる。空の範囲は捨てる。"""
    clipped = sorted(
        (max(0, start), min(length, end))
        for start, end in ranges
        if min(length, end) > max(0, start)
    )
    merged: list[tuple[int, int]] = []
    for start, end in clipped:
        if merged and start <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], end))
        else:
            merged.append((start, end))
    return tuple(merged)


def load_ranges(value: object, length: int) -> TextRanges:
    """metadata の値（JSON の文字列か list）を読む。

    形の壊れた値は範囲なし（原文として扱う今の挙動）にする。
    """
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except ValueError:
            return ()
    if not isinstance(value, list | tuple):
        return ()
    ranges = []
    for item in value:
        if (
            not isinstance(item, list | tuple)
            or len(item) != 2
            or any(type(edge) is not int for edge in item)
        ):
            return ()
        ranges.append((item[0], item[1]))
    return normalize_ranges(ranges, length)


def dump_ranges(ranges: Sequence[tuple[int, int]]) -> str:
    return json.dumps([[start, end] for start, end in ranges], separators=(",", ":"))


def slice_ranges(ranges: Sequence[tuple[int, int]], start: int, end: int) -> TextRanges:
    """本文の ``[start, end)`` を切り出した本文の座標の範囲。"""
    return normalize_ranges(((a - start, b - start) for a, b in ranges), end - start)


def shift_ranges(ranges: Sequence[tuple[int, int]], offset: int) -> TextRanges:
    return tuple((start + offset, end + offset) for start, end in ranges)


def join_ranges(
    parts: Sequence[tuple[str, Sequence[tuple[int, int]]]], separator: str
) -> TextRanges:
    """``separator.join(本文)`` の座標の範囲（本文ごとの範囲を、つないだ位置へずらす）。"""
    ranges: list[tuple[int, int]] = []
    offset = 0
    for index, (text, own) in enumerate(parts):
        if index:
            offset += len(separator)
        ranges.extend(shift_ranges(own, offset))
        offset += len(text)
    return normalize_ranges(ranges, offset)


def source_pieces(
    start: int, end: int, ranges: Sequence[tuple[int, int]], text: str
) -> list[tuple[int, int]]:
    """``[start, end)`` から範囲を除いた残り（前後の空白を除き、空白だけの部分は捨てる）。"""
    pieces: list[tuple[int, int]] = []
    cursor = start
    for a, b in ranges:
        if b <= cursor or a >= end:
            continue
        if a > cursor:
            pieces.append((cursor, a))
        cursor = max(cursor, b)
    if cursor < end:
        pieces.append((cursor, end))
    trimmed = []
    for a, b in pieces:
        while a < b and text[a].isspace():
            a += 1
        while b > a and text[b - 1].isspace():
            b -= 1
        if a < b:
            trimmed.append((a, b))
    return trimmed
