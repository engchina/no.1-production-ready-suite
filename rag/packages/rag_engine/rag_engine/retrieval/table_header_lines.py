"""表の chunk の分割が記録した見出しの行を、根拠の本文の行として示す（#4592）。

表の chunk の分割（``rag_pipeline_core.chunking`` の ``_table_row_tree_metadata``）は、chunk の表の最初の行を見出しの行
（列名）とし、その列名を ``table_row_tree_column_keys`` に記録する。ここでは、chunk の本文の表の最初の行のセルが記録の
列名と一致するときだけ、その行を見出しの行として返す。一致しない（列名の重複・空欄の補い・切り詰めなど）・記録が無い
ときは返さない（推測しない）。行の語の意味は読まない。
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterable, Mapping, Sequence
from typing import Any

# 根拠（span）の本文に含まれる、表の見出しの行の原文（行の前後の空白を除いたもの）。
TABLE_HEADER_LINES_KEY = "table_header_lines"
# 表の chunk の分割が記録した列名（JSON の文字列の配列）。
_COLUMN_KEYS = "table_row_tree_column_keys"
# 分割が列名を切り詰める長さ（``_clean_table_column_key``）。超える列名は一致を確かめられないので扱わない。
_COLUMN_KEY_MAX_CHARS = 80
_TABLE_LINE = re.compile(r"^\s*\|.+\|\s*$")
_SEPARATOR_CELL = re.compile(r":?-{3,}:?")


def _cells(line: str) -> list[str]:
    body = line.strip().strip("|")
    return [re.sub(r"\s+", " ", cell.replace("\\|", "|")).strip() for cell in re.split(r"(?<!\\)\|", body)]


def recorded_header_line(chunk: Any) -> str | None:
    """chunk の分割が見出しと記録した、chunk の本文の表の最初の行（記録と一致しなければ None）。"""
    metadata = getattr(chunk, "metadata", None)
    raw = metadata.get(_COLUMN_KEYS) if isinstance(metadata, Mapping) else None
    if not isinstance(raw, str):
        return None
    try:
        keys = json.loads(raw)
    except ValueError:
        return None
    if not isinstance(keys, list) or not keys or not all(isinstance(key, str) for key in keys):
        return None
    for line in str(getattr(chunk, "text", "") or "").splitlines():
        if not _TABLE_LINE.match(line):
            continue
        cells = _cells(line)
        if cells and all(_SEPARATOR_CELL.fullmatch(cell) for cell in cells):
            continue
        # 最初の表の行だけを見る。列名が空欄・切り詰め・重複の補いで記録と変わる行は扱わない。
        if any(not cell or len(cell) > _COLUMN_KEY_MAX_CHARS for cell in cells) or cells != keys:
            return None
        return line.strip()
    return None


def header_lines(chunks: Iterable[Any]) -> list[str]:
    """chunk の並びの、記録と一致した見出しの行（重複を除き、出現の順）。"""
    return list(dict.fromkeys(line for chunk in chunks if (line := recorded_header_line(chunk)) is not None))


def header_lines_in(text: str, lines: Sequence[str]) -> list[str]:
    """``lines`` のうち、``text`` に 1 つの物理行として現れるもの。"""
    present = {line.strip() for line in text.splitlines()}
    return [line for line in lines if line in present]


def span_header_lines(span: Mapping[str, Any]) -> frozenset[str]:
    """根拠に記録した見出しの行。"""
    value = span.get(TABLE_HEADER_LINES_KEY)
    return frozenset(str(line) for line in value) if isinstance(value, list) else frozenset()
