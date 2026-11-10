"""検索で当たった派生の chunk を、回答の根拠に渡す原文の chunk に置き換える（#2641）。

派生の chunk（章節ナビゲーションの要約・項目抽出の値。判定は
``rag_parser_core.extraction.is_derived_content``）は、取込が原文から作った検索の手がかりで、
引用できる原文ではない。検索の当たりはそのまま根拠にせず、その chunk が指す原文を読んで根拠に渡す。

- 章節の要約: 同じ版（chunk_set）の、要約と同じ章節（``section_path``）の chunk。その章節の直下の
  chunk が無ければ、下の章節の chunk。
- 項目抽出の値: 値を読み取った元の要素（``field_source_element_id``）を含む同じ版の chunk。
- 文書全体の要約（#4793）: 指す原文が文書の全体で、置き換える原文の chunk を決められない。
  置き換えず、要約と分かる印（``content_kind=document_summary``）を付けたまま根拠に渡す
  （``keeps_own_evidence``）。前後の文脈・参照先・章節の拡張の起点にはしない。

原文が読めなければ空を返す（呼び出し側は派生の chunk を根拠から外し、根拠不足として扱う）。
読む範囲は検索と同じ条件（tenant・ナレッジベース・有効な chunk_set・ページ・旧版の扱い）で、
chunk の種類の条件（``content_kind``）だけを外す（派生の chunk の種類で絞った検索でも原文を読む）。
"""

from __future__ import annotations

import logging
from collections.abc import Iterable, Mapping
from typing import Literal, Protocol

from rag_parser_core.extraction import (
    DOCUMENT_SUMMARY_CONTENT_KIND,
    EXTRACTED_FIELD_CONTENT_KIND,
    EXTRACTED_FIELD_KEY,
    FIELD_SOURCE_ELEMENT_KEY,
    NAV_SUMMARY_KEY,
    SECTION_SUMMARY_CONTENT_KIND,
    is_derived_content,
)

from app.rag.element_locator import chunk_element_ids
from app.schemas.search import RetrievedChunk

logger = logging.getLogger(__name__)

DerivedKind = Literal["section_summary", "field", "document_summary"]

# 派生の chunk 1 件から根拠に渡す原文の chunk の数の上限（読み順の先頭から。並びは rerank が
# 決める）。
DERIVED_SOURCE_MAX_CHUNKS = 6
# 原文を読むときに外す、chunk の種類の検索の条件。
_KIND_FILTER_KEYS = ("content_kind", "content_kinds")
# 章節の要約の原文を探すときに読む行の倍率（下の章節の要約・項目の値の行を除いた後に残す分）。
_SECTION_READ_FACTOR = 4
_SECTION_PATH_SEPARATOR = " > "


class DerivedSourceReader(Protocol):
    """原文の chunk を検索と同じ条件で読む SQL（``OracleClient``）。"""

    async def retrieval_reference_chunks(
        self,
        filters: dict[str, str],
        *,
        document_id: str,
        section_path: str,
        chunk_set_id: str | None = None,
        limit: int,
    ) -> list[RetrievedChunk]: ...

    async def retrieval_chunk_set_chunks(
        self,
        filters: dict[str, str],
        *,
        document_id: str,
        chunk_set_id: str,
        element_id: str,
        limit: int,
    ) -> list[RetrievedChunk]: ...


def is_derived_chunk(chunk: RetrievedChunk) -> bool:
    """派生の chunk か（印か ``content_kind`` で判定。#2590 より前の印の無い chunk も）。"""
    return is_derived_content(chunk.metadata.get("content_kind"), chunk.metadata)


def derived_kind(metadata: Mapping[str, object]) -> DerivedKind | None:
    """派生の chunk の種類（派生でなければ None）。"""
    if not is_derived_content(metadata.get("content_kind"), metadata):
        return None
    content_kind = metadata.get("content_kind")
    if content_kind == DOCUMENT_SUMMARY_CONTENT_KIND:
        return "document_summary"
    if content_kind == SECTION_SUMMARY_CONTENT_KIND or metadata.get(NAV_SUMMARY_KEY):
        return "section_summary"
    if content_kind == EXTRACTED_FIELD_CONTENT_KIND or metadata.get(EXTRACTED_FIELD_KEY):
        return "field"
    return None


def keeps_own_evidence(chunk: RetrievedChunk) -> bool:
    """原文の chunk に置き換えず、そのまま根拠に渡す派生の chunk か（文書全体の要約。#4793）。"""
    return derived_kind(chunk.metadata) == "document_summary"


def original_chunks[C: RetrievedChunk](chunks: Iterable[C]) -> list[C]:
    """派生の chunk を除いた chunk（原文として根拠に渡せるもの）。"""
    return [chunk for chunk in chunks if not is_derived_chunk(chunk)]


def source_filters(filters: Mapping[str, str]) -> dict[str, str]:
    """原文を読む条件（検索の条件から chunk の種類の条件だけを外す）。"""
    return {key: value for key, value in filters.items() if key not in _KIND_FILTER_KEYS}


async def derived_source_chunks(
    reader: DerivedSourceReader,
    chunk: RetrievedChunk,
    *,
    filters: Mapping[str, str],
    limit: int = DERIVED_SOURCE_MAX_CHUNKS,
) -> list[RetrievedChunk]:
    """派生の chunk が指す原文の chunk（読み順。読めない・見つからなければ空）。"""
    kind = derived_kind(chunk.metadata)
    if kind is None or kind == "document_summary" or limit <= 0:
        return []
    scope = source_filters(filters)
    try:
        if kind == "section_summary":
            rows = await _section_sources(reader, chunk, scope, limit)
        else:
            rows = await _field_sources(reader, chunk, scope, limit)
    except Exception:  # noqa: BLE001 - 原文が読めなければ根拠に渡さない(根拠不足として続ける)。
        logger.warning("derived source chunks load failed", extra={"kind": kind}, exc_info=True)
        return []
    found: dict[str, RetrievedChunk] = {}
    for row in rows:
        if row.document_id != chunk.document_id or row.chunk_id == chunk.chunk_id:
            continue
        if is_derived_chunk(row):
            continue
        found.setdefault(row.chunk_id, row)
    return list(found.values())[:limit]


def _chunk_set_id(chunk: RetrievedChunk) -> str:
    return str(chunk.metadata.get("chunk_set_id") or "").strip()


async def _section_sources(
    reader: DerivedSourceReader,
    chunk: RetrievedChunk,
    filters: dict[str, str],
    limit: int,
) -> list[RetrievedChunk]:
    source_ids = chunk_element_ids({"element_ids": chunk.metadata.get("nav_source_element_ids")})
    if source_ids:
        chunk_set_id = _chunk_set_id(chunk)
        if not chunk_set_id:
            return []
        found: dict[str, RetrievedChunk] = {}
        covered: set[str] = set()
        for element_id in source_ids[: limit * _SECTION_READ_FACTOR]:
            if element_id in covered:
                continue
            rows = await reader.retrieval_chunk_set_chunks(
                filters,
                document_id=chunk.document_id,
                chunk_set_id=chunk_set_id,
                element_id=element_id,
                limit=limit,
            )
            for row in original_chunks(rows):
                covered.update(chunk_element_ids(row.metadata))
                found.setdefault(row.chunk_id, row)
            if len(found) >= limit:
                break
        return list(found.values())[:limit]
    section_path = _section_path(chunk.metadata)
    if not section_path:
        return []
    rows = await reader.retrieval_reference_chunks(
        filters,
        document_id=chunk.document_id,
        section_path=section_path,
        chunk_set_id=_chunk_set_id(chunk) or None,
        limit=limit * _SECTION_READ_FACTOR,
    )
    originals = original_chunks(rows)
    # 要約は章節の直下の本文から作る(navigation.summarize_navigation_nodes)。直下が無ければ
    # 下の章節。
    direct = [row for row in originals if _section_path(row.metadata) == section_path]
    return direct or originals


async def _field_sources(
    reader: DerivedSourceReader,
    chunk: RetrievedChunk,
    filters: dict[str, str],
    limit: int,
) -> list[RetrievedChunk]:
    element_id = str(chunk.metadata.get(FIELD_SOURCE_ELEMENT_KEY) or "").strip()
    chunk_set_id = _chunk_set_id(chunk)
    if not element_id or not chunk_set_id:
        return []
    rows = await reader.retrieval_chunk_set_chunks(
        filters,
        document_id=chunk.document_id,
        chunk_set_id=chunk_set_id,
        element_id=element_id,
        limit=limit,
    )
    # SQL は要素の ID の部分一致で絞るので、完全一致で確かめる(p1-1 と p1-10 を取り違えない)。
    return [row for row in rows if element_id in chunk_element_ids(row.metadata)]


def _section_path(metadata: Mapping[str, object]) -> str:
    value = metadata.get("section_path")
    if isinstance(value, list | tuple):
        return _SECTION_PATH_SEPARATOR.join(str(part) for part in value if str(part).strip())
    return str(value or "").strip()
