"""位置情報の無い既存の XML ツリー JSON の原本対応を、原本から決定的に作り直す（#4094）。

文字検索で推測しない。解析と同じ処理を原本へ当て、保存済みの要素と ID・本文が完全に一致する要素に
だけ使う。一致しない要素は位置不明のままにする。保存済みの抽出結果は書き換えない。
"""

from __future__ import annotations

import hashlib
import threading
from collections import OrderedDict
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from rag_parser_core.xml_tree_json import XML_TREE_JSON_SOURCE_FORMAT, xml_tree_json_extraction

_CACHE_MAX_SOURCES = 8


@dataclass(frozen=True)
class _RebuiltSource:
    source_map: Mapping[str, Any]
    texts: Mapping[str, str]


_cache: OrderedDict[str, _RebuiltSource | None] = OrderedDict()
_cache_lock = threading.Lock()


def _rebuild(data: bytes) -> _RebuiltSource | None:
    try:
        extraction = xml_tree_json_extraction(data)
    except (ValueError, TypeError, KeyError, IndexError, RecursionError, UnicodeError):
        return None
    source_map = extraction.parser_artifacts.get("json_source_map") if extraction else None
    if extraction is None or not isinstance(source_map, Mapping):
        return None
    return _RebuiltSource(
        source_map=source_map,
        texts={
            element.element_id: str(element.text)
            for element in extraction.elements
            if element.element_id
        },
    )


def _rebuilt_source(data: bytes) -> _RebuiltSource | None:
    """長い原本の再計算を画面の操作ごとに繰り返さない（プロセス内に上限付きで残す）。"""
    key = hashlib.sha256(data).hexdigest()
    with _cache_lock:
        if key in _cache:
            _cache.move_to_end(key)
            return _cache[key]
    rebuilt = _rebuild(data)
    with _cache_lock:
        _cache[key] = rebuilt
        while len(_cache) > _CACHE_MAX_SOURCES:
            _cache.popitem(last=False)
    return rebuilt


def payload_with_source_map(payload: Mapping[str, Any], data: bytes) -> Mapping[str, Any]:
    """原本対応が無い XML ツリー JSON にだけ、一致した要素の対応を足した表示用の値を返す。"""
    artifacts = payload.get("parser_artifacts")
    if (
        not isinstance(artifacts, Mapping)
        or "json_source_map" in artifacts
        or artifacts.get("source_format") != XML_TREE_JSON_SOURCE_FORMAT
    ):
        return payload
    rebuilt = _rebuilt_source(data)
    if rebuilt is None:
        return payload
    spans = rebuilt.source_map.get("elements")
    spans = spans if isinstance(spans, Mapping) else {}
    matched = {
        element["element_id"]: spans[element["element_id"]]
        for element in payload.get("elements") or []
        if isinstance(element, Mapping)
        and element.get("element_id") in spans
        and rebuilt.texts.get(element["element_id"]) == element.get("text")
    }
    if not matched:
        return payload
    return {
        **payload,
        "parser_artifacts": {
            **artifacts,
            "json_source_map": {**rebuilt.source_map, "elements": matched},
        },
    }
