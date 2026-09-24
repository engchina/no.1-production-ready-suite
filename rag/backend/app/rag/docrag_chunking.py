"""DocRAG(rag_poc)の Small-to-Big 親子分割を backend の Chunk へ写す。

docling サービスが ``parser_artifacts["docrag_layout"]`` に保持した LayoutRecord から
``docrag.chunking.build_small_to_big_chunks`` で親子チャンクを作り、子だけを索引単位として返す。
親の本文と ID は子の metadata(``chunk_group_id`` / ``docrag_parent_text``)に保持し、既存の
group sibling 展開と回答文脈で使う。Oracle Text と embedding には rag_poc の search_text を使う。
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from typing import TYPE_CHECKING, Any

from rag_parser_core.extraction import StructuredExtraction
from rag_pipeline_core.chunking import Chunk

if TYPE_CHECKING:
    from docrag.chunking.constants import ChunkingConfig, DocumentChunk

DOCRAG_CHUNKING_STRATEGY = "docrag_small_to_big"
DOCRAG_LAYOUT_ARTIFACT = "docrag_layout"
DOCRAG_SOURCE_PARSER = "docling_docrag"
# 検索・embedding 用 text(rag_poc の retrieval_text)を載せる metadata key。
DOCRAG_SEARCH_TEXT_KEY = "docrag_search_text"


class DocragLayoutMissingError(ValueError):
    """DocRAG 分割に必要な docling レイアウトが抽出結果にない。"""


def has_docrag_layout(extraction: StructuredExtraction) -> bool:
    layout = extraction.parser_artifacts.get(DOCRAG_LAYOUT_ARTIFACT)
    return isinstance(layout, Mapping) and bool(layout.get("records"))


def build_docrag_chunks(
    extraction: StructuredExtraction,
    *,
    source_name: str = "",
    config: ChunkingConfig | None = None,
) -> list[Chunk]:
    """docrag_layout から親子分割し、子チャンクを索引用 Chunk として返す。"""
    # oracle client からも search_text 判定で import されるため、分割実装は使用時に読む。
    from docrag.chunking.builder import build_small_to_big_chunks
    from docrag.chunking.constants import CHILD_CHUNK_LEVEL, ChunkingConfig

    layout = extraction.parser_artifacts.get(DOCRAG_LAYOUT_ARTIFACT)
    if not isinstance(layout, Mapping) or not layout.get("records"):
        raise DocragLayoutMissingError(
            "DocRAG 分割には docling(DocRAG)の解析結果が必要です。"
            "文書解析を docling にして再解析してください。"
        )
    records = [dict(record) for record in _items(layout.get("records")) if isinstance(record, dict)]
    pages = [dict(page) for page in _items(layout.get("pages")) if isinstance(page, dict)]
    engines = sorted({str(record.get("engine") or "docling") for record in records})
    payload: dict[str, Any] = {
        "pdf_name": source_name,
        "records": records,
        "pages": pages,
        "engines": [{"engine": engine, "label": "Docling"} for engine in engines],
        "classification": {},
        "document_metadata": {},
    }
    chunks = build_small_to_big_chunks(
        payload,
        source_run_id=_source_run_id(records),
        selected_engine_ids=engines,
        config=(config or ChunkingConfig()).validate(),
        source_page_count=len(pages),
    )
    parents = {chunk.chunk_id: chunk for chunk in chunks if chunk.chunk_level != CHILD_CHUNK_LEVEL}
    children = [chunk for chunk in chunks if chunk.chunk_level == CHILD_CHUNK_LEVEL]
    page_sizes = {
        int(page.get("page") or 0): (page.get("width"), page.get("height")) for page in pages
    }
    return [
        _backend_chunk(child, index, parents.get(child.parent_chunk_id), page_sizes)
        for index, child in enumerate(children)
    ]


def _items(value: object) -> list[object]:
    return list(value) if isinstance(value, list) else []


def docrag_search_text(metadata: Mapping[str, object]) -> str | None:
    """DocRAG chunk の検索用 text(Oracle Text / embedding 入力)を返す。"""
    value = metadata.get(DOCRAG_SEARCH_TEXT_KEY)
    return value if isinstance(value, str) and value.strip() else None


def _backend_chunk(
    child: DocumentChunk,
    index: int,
    parent: DocumentChunk | None,
    page_sizes: Mapping[int, tuple[object, object]],
) -> Chunk:
    metadata = child.metadata
    section_path = [str(part) for part in metadata.get("section_path") or [] if str(part)]
    element_ids = [
        str(ref.get("record_id"))
        for ref in child.source_record_refs
        if isinstance(ref, Mapping) and ref.get("record_id")
    ]
    width, height = page_sizes.get(child.page_start, (None, None))
    bbox = _first_region_bbox(metadata, child.page_start)
    group_id = child.parent_chunk_id or child.chunk_id
    return Chunk(
        text=child.text,
        index=index,
        start_offset=0,
        end_offset=len(child.text),
        metadata={
            "source_parser": DOCRAG_SOURCE_PARSER,
            "chunk_strategy": DOCRAG_CHUNKING_STRATEGY,
            "docrag_chunk_id": child.chunk_id,
            "parent_chunk_id": group_id,
            "chunk_group_id": group_id,
            "chunk_group_kind": "docrag_parent",
            "docrag_parent_text": parent.text if parent is not None else "",
            DOCRAG_SEARCH_TEXT_KEY: child.retrieval_text,
            "content_kind": _content_kind(metadata),
            "section_path": " > ".join(section_path) or None,
            "page_number": child.page_start or None,
            "page_start": child.page_start or None,
            "page_end": child.page_end or None,
            "page_width": width if isinstance(width, int | float) else None,
            "page_height": height if isinstance(height, int | float) else None,
            "bbox": json.dumps(bbox) if bbox else None,
            "bbox_unit": "absolute" if bbox else None,
            "element_ids": ",".join(element_ids) or None,
            "atomic": bool(metadata.get("atomic")),
            "text_sha256": hashlib.sha256(child.text.encode("utf-8")).hexdigest(),
            # metadata v4 全体(display_regions / image_evidence 等)は JSON で保持する。
            "docrag_metadata_json": json.dumps(metadata, ensure_ascii=False, default=str),
            # 回答根拠の record 対応(bbox ハイライト)に使う。
            "docrag_source_record_refs_json": json.dumps(
                child.source_record_refs, ensure_ascii=False, default=str
            ),
            "docrag_source_seq_ranges_json": json.dumps(child.source_seq_ranges, default=str),
            "docrag_chunk_seq": child.chunk_seq,
        },
    )


def _content_kind(metadata: Mapping[str, Any]) -> str:
    categories = {str(value) for value in metadata.get("source_categories") or []}
    if "Table" in categories:
        return "table"
    if "Picture" in categories:
        return "figure"
    return "text"


def _first_region_bbox(metadata: Mapping[str, Any], page: int) -> list[float] | None:
    """先頭ページの表示領域(複数 box)を包含する bbox を返す。"""
    layout = metadata.get("layout")
    regions = layout.get("display_regions") if isinstance(layout, Mapping) else None
    for region in regions or []:
        if not isinstance(region, Mapping) or int(region.get("page") or 0) != page:
            continue
        boxes = [
            box["bbox"]
            for box in region.get("boxes") or []
            if isinstance(box, Mapping)
            and isinstance(box.get("bbox"), list)
            and len(box["bbox"]) == 4
        ]
        if boxes:
            return [
                float(min(box[0] for box in boxes)),
                float(min(box[1] for box in boxes)),
                float(max(box[2] for box in boxes)),
                float(max(box[3] for box in boxes)),
            ]
    return None


def _source_run_id(records: list[dict[str, Any]]) -> str:
    """rag_poc の run_id 形式(16 桁 hex)を record 群の内容から決定論的に作る。"""
    digest = hashlib.sha256(
        json.dumps([record.get("id") for record in records], ensure_ascii=False).encode("utf-8")
    )
    return digest.hexdigest()[:16]
