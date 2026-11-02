"""保存済みの ``parser_artifacts["source_map"]`` を検証して読む（#4100）。

位置は解析が記録した座標だけを使う。版の違う原本・対応表の不整合では同期の結果を出さない。
"""

from __future__ import annotations

import hashlib
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from rag_parser_core.source_map import FRAGMENT_SCHEMES, SOURCE_MAP_VERSION

STATUS_EXACT = "exact"
STATUS_VERSION_MISMATCH = "version_mismatch"
STATUS_UNRESOLVED = "unresolved"


@dataclass(frozen=True)
class SourceUnit:
    """1 つの要素の中の所属の単位（csv / tsv の行のまとまり、JSON の値のまとまり）。"""

    element_id: str
    source_start: int
    text_start: int
    text_end: int
    row_start: int | None
    row_end: int | None


@dataclass(frozen=True)
class ResolvedSourceMap:
    status: str
    starts: dict[str, int]
    units: tuple[SourceUnit, ...] = ()


def _int(value: object) -> int:
    if type(value) is not int or value < 0:
        raise ValueError("invalid source map coordinate")
    return value


def resolve_source_map(
    source_map: Mapping[str, Any],
    *,
    data: bytes,
    text_length: int,
    element_texts: Mapping[str, str],
) -> ResolvedSourceMap:
    """要素の開始位置と単位を返す。要素の本文が変わった単位は、要素の開始にだけ所属させる。"""
    if source_map.get("sha256") != hashlib.sha256(data).hexdigest():
        return ResolvedSourceMap(STATUS_VERSION_MISMATCH, {})
    try:
        if (
            source_map.get("version") != SOURCE_MAP_VERSION
            or source_map.get("coordinate_unit") != "unicode_codepoint"
            or source_map.get("source_length") != text_length
        ):
            raise ValueError("invalid source map header")
        elements = source_map.get("elements")
        if not isinstance(elements, Mapping):
            raise ValueError("invalid source map elements")
        fragmentable = source_map.get("scheme") in FRAGMENT_SCHEMES
        starts: dict[str, int] = {}
        units: list[SourceUnit] = []
        for element_id, text in element_texts.items():
            spans = elements.get(element_id)
            if not spans:
                continue
            positions = []
            for span in spans:
                start, end = _int(span["source_start"]), _int(span["source_end"])
                if end < start or end > text_length:
                    raise ValueError("source range out of bounds")
                positions.append(start)
            starts[element_id] = min(positions)
            if fragmentable and len(spans) > 1:
                units.extend(_fragment_units(element_id, spans, text))
    except (KeyError, ValueError, TypeError):
        return ResolvedSourceMap(STATUS_UNRESOLVED, {})
    return ResolvedSourceMap(STATUS_EXACT if starts else STATUS_UNRESOLVED, starts, tuple(units))


def _fragment_units(element_id: str, spans: Any, text: str) -> list[SourceUnit]:
    units = [
        SourceUnit(
            element_id=element_id,
            source_start=_int(span["source_start"]),
            text_start=_int(span["start"]),
            text_end=_int(span["end"]),
            row_start=span.get("row_start"),
            row_end=span.get("row_end"),
        )
        for span in spans
    ]
    # 確認の修正で本文が変わった要素は、単位の範囲が本文に合わないので分けない。
    if max(unit.text_end for unit in units) != len(text):
        return []
    return units
