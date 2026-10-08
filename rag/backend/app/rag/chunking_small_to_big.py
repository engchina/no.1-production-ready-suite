"""親子階層（small-to-big）(rag_poc の Small-to-Big 親子分割)を backend の Chunk へ写す。

docling サービス・MinerU の解析(``app.clients.mineru_layout``。#1334)が
``parser_artifacts["layout_records"]`` に保持した LayoutRecord から
``rag_engine.chunking.build_small_to_big_chunks`` で親子チャンクを作り、子だけを索引単位として返す。
親の本文と ID は子の metadata(``chunk_group_id`` / ``parent_text``)に保持し、既存の
group sibling 展開と回答文脈で使う。Oracle Text と embedding には rag_poc の search_text を使う。
文書の 1 ページ目の本文(回答の「文書の背景」)は先頭の chunk にだけ載せ、保存のときに chunk set
へ 1 つだけ移す(#557。検索用 text には入れない)。

文書解析が Docling・MinerU 以外で layout_records がない文書は、失敗させずに
「構造認識」(structure_aware)で分割する(#300)。縮退したことは chunk の metadata
(``chunk_strategy_requested`` / ``chunk_strategy_fallback_reason``)に残し、分割プレビューと
Chunk 一覧で利用者に示す。
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from dataclasses import replace
from typing import TYPE_CHECKING, Any

from rag_parser_core.extraction import StructuredExtraction
from rag_pipeline_core.chunking import (
    FIGURE_TEXT_SOURCE_KEY,
    FIGURE_TEXT_SOURCE_OCR,
    FIGURE_TEXT_SOURCE_VISION,
    Chunk,
)

if TYPE_CHECKING:
    from rag_engine.chunking.constants import ChunkingConfig, DocumentChunk

    from app.rag.chunking_strategy import SmallToBigParams

SMALL_TO_BIG_STRATEGY = "small_to_big"
LAYOUT_ARTIFACT = "layout_records"
# chunk の source_parser(解析エンジンの名前 + "_layout")。Docling は "docling_layout"、MinerU は
# "mineru_layout"(``layout_source_parser``)。
LAYOUT_SOURCE_PARSER = "docling_layout"
# record の engine → 分割の結果に出す解析エンジンの表示名。
_ENGINE_LABELS = {"docling": "Docling", "mineru": "MinerU"}
# 検索・embedding 用 text(rag_poc の retrieval_text)を載せる metadata key。
ENGINE_SEARCH_TEXT_KEY = "engine_search_text"
# 文書の物理 1 ページ目の本文(回答の「文書の背景」。rag_poc の first_page_context)を JSON で
# 載せる metadata key(#557)。chunk ごとに同じ本文(最大 8000 文字)を持たせないよう、先頭の
# chunk にだけ載せる。保存では chunk の行から外し、chunk set に 1 つだけ保存する。
FIRST_PAGE_CONTEXT_KEY = "first_page_context_json"
# layout_records がない文書で親子階層（small-to-big）の代わりに使う分割方式(#300)。
SMALL_TO_BIG_FALLBACK_STRATEGY = "structure_aware"
# 縮退時に chunk metadata へ残す key と理由。chunk_strategy には実際に使った方式が入る。
CHUNK_STRATEGY_REQUESTED_KEY = "chunk_strategy_requested"
CHUNK_STRATEGY_FALLBACK_REASON_KEY = "chunk_strategy_fallback_reason"
LAYOUT_MISSING_REASON = "layout_missing"


def layout_source_parser(engine: str) -> str:
    """親子階層の chunk の source_parser。record の engine(解析エンジン)が分かる値にする。"""
    name = (engine or "docling").strip().casefold() or "docling"
    return LAYOUT_SOURCE_PARSER if name == "docling" else f"{name}_layout"


class LayoutRecordsMissingError(ValueError):
    """親子階層の分割に必要なレイアウト(Docling・MinerU の LayoutRecord)が抽出結果にない。

    取込と分割プレビューは ``has_layout_records`` で先に判定して構造認識へ縮退するため、
    通常は起きない(``build_parent_child_chunks`` を直接呼んだときの防御)。起きた場合も
    利用者へ表示してよい文言にする(``safe_for_user``)。
    """

    safe_for_user = True


def has_layout_records(extraction: StructuredExtraction) -> bool:
    layout = extraction.parser_artifacts.get(LAYOUT_ARTIFACT)
    return isinstance(layout, Mapping) and bool(layout.get("records"))


def small_to_big_fallback_needed(strategy: str, extraction: StructuredExtraction) -> bool:
    """親子階層（small-to-big）を選んだが layout_records がなく、構造認識で分割するときに真。"""
    return strategy == SMALL_TO_BIG_STRATEGY and not has_layout_records(extraction)


def mark_small_to_big_fallback(chunks: list[Chunk]) -> list[Chunk]:
    """構造認識で縮退した chunk に、選ばれていた方式と縮退の理由を残す。

    ``chunk_strategy`` は分割に実際に使った方式(structure_aware)のまま変えない。
    """
    return [
        replace(
            chunk,
            metadata={
                **chunk.metadata,
                CHUNK_STRATEGY_REQUESTED_KEY: SMALL_TO_BIG_STRATEGY,
                CHUNK_STRATEGY_FALLBACK_REASON_KEY: LAYOUT_MISSING_REASON,
            },
        )
        for chunk in chunks
    ]


def small_to_big_chunking_config(params: SmallToBigParams | None = None) -> ChunkingConfig:
    """設定の 5 項目を rag_engine の ChunkingConfig へ写す。

    検索用テキストの 3 項目(contextual search text)は rag_poc と同じく既定値のまま使う。
    """
    from rag_engine.chunking.constants import ChunkingConfig

    if params is None:
        return ChunkingConfig().validate()
    return ChunkingConfig(
        child_target_chars=params.child_target_chars,
        table_child_target_chars=params.table_child_target_chars,
        parent_target_chars=params.parent_target_chars,
        parent_max_pages=params.parent_max_pages,
        parent_max_children=params.parent_max_children,
    ).validate()


def build_parent_child_chunks(
    extraction: StructuredExtraction,
    *,
    source_name: str = "",
    params: SmallToBigParams | None = None,
) -> list[Chunk]:
    """layout_records から親子分割し、子チャンクを索引用 Chunk として返す。

    ``params`` は文書分割の設定(親子階層（small-to-big）の 5 項目)。None は rag_poc の既定値。
    """
    # oracle client からも search_text 判定で import されるため、分割実装は使用時に読む。
    from rag_engine.chunking.builder import build_small_to_big_chunks
    from rag_engine.chunking.constants import CHILD_CHUNK_LEVEL

    layout = extraction.parser_artifacts.get(LAYOUT_ARTIFACT)
    if not isinstance(layout, Mapping) or not layout.get("records"):
        raise LayoutRecordsMissingError(
            "親子階層（small-to-big）には Docling か MinerU の解析結果が必要です。"
            "文書解析を Docling か MinerU にして再解析するか、別の分割方式を選んでください。"
        )
    records = [dict(record) for record in _items(layout.get("records")) if isinstance(record, dict)]
    pages = [dict(page) for page in _items(layout.get("pages")) if isinstance(page, dict)]
    engines = sorted({str(record.get("engine") or "docling") for record in records})
    payload: dict[str, Any] = {
        "pdf_name": source_name,
        "records": records,
        "pages": pages,
        "engines": [
            {"engine": engine, "label": _ENGINE_LABELS.get(engine, engine)} for engine in engines
        ],
        "classification": {},
        "document_metadata": {},
    }
    chunks = build_small_to_big_chunks(
        payload,
        source_run_id=_source_run_id(records),
        selected_engine_ids=engines,
        config=small_to_big_chunking_config(params),
        source_page_count=len(pages),
    )
    parents = {chunk.chunk_id: chunk for chunk in chunks if chunk.chunk_level != CHILD_CHUNK_LEVEL}
    children = [chunk for chunk in chunks if chunk.chunk_level == CHILD_CHUNK_LEVEL]
    page_sizes = {
        int(page.get("page") or 0): (page.get("width"), page.get("height")) for page in pages
    }
    backend_chunks = [
        _backend_chunk(child, index, parents.get(child.parent_chunk_id), page_sizes)
        for index, child in enumerate(children)
    ]
    # rag_engine は子ごとに同じ 1 ページ目の本文を document に持たせるので、先頭の子から
    # 1 つだけ取る。
    document = children[0].metadata.get("document") if children else None
    first_page = document.get("first_page_context") if isinstance(document, Mapping) else None
    if backend_chunks and isinstance(first_page, Mapping):
        backend_chunks[0].metadata[FIRST_PAGE_CONTEXT_KEY] = json.dumps(
            first_page, ensure_ascii=False
        )
    return backend_chunks


def _items(value: object) -> list[object]:
    return list(value) if isinstance(value, list) else []


def engine_search_text(metadata: Mapping[str, object]) -> str | None:
    """親子階層の chunk の検索用 text(Oracle Text / embedding 入力)を返す。"""
    value = metadata.get(ENGINE_SEARCH_TEXT_KEY)
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
    content_kind = _content_kind(metadata)
    return Chunk(
        text=child.text,
        index=index,
        start_offset=0,
        end_offset=len(child.text),
        metadata={
            # 図の本文の出どころ（根拠の種類。#1282）。図の chunk だけに付ける。
            **(
                {FIGURE_TEXT_SOURCE_KEY: _figure_text_source(child.source_record_refs)}
                if content_kind == "figure"
                else {}
            ),
            "source_parser": layout_source_parser(child.source_engine_id),
            "chunk_strategy": SMALL_TO_BIG_STRATEGY,
            "engine_chunk_id": child.chunk_id,
            "parent_chunk_id": group_id,
            "chunk_group_id": group_id,
            "chunk_group_kind": "small_to_big_parent",
            "parent_text": parent.text if parent is not None else "",
            ENGINE_SEARCH_TEXT_KEY: child.retrieval_text,
            "content_kind": content_kind,
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
            # 1 ページ目の本文は chunk ごとに複製しない(chunk set に 1 つ保存する。#557)。
            "engine_metadata_json": json.dumps(
                _without_first_page_context(metadata), ensure_ascii=False, default=str
            ),
            # 回答根拠の record 対応(bbox ハイライト)に使う。
            "source_record_refs_json": json.dumps(
                child.source_record_refs, ensure_ascii=False, default=str
            ),
            "source_seq_ranges_json": json.dumps(child.source_seq_ranges, default=str),
            "engine_chunk_seq": child.chunk_seq,
        },
    )


def _without_first_page_context(metadata: Mapping[str, Any]) -> Mapping[str, Any]:
    """chunk に保存する metadata から document.first_page_context を外す(入力は変更しない)。"""
    document = metadata.get("document")
    if not isinstance(document, Mapping) or "first_page_context" not in document:
        return metadata
    return {
        **metadata,
        "document": {key: value for key, value in document.items() if key != "first_page_context"},
    }


def _figure_text_source(refs: object) -> str:
    """図の本文を Vision（VLM）が書いたか(vision)、解析の OCR・キャプションのままか(ocr)。

    図の record に Vision のモデルが残り、失敗の印が無ければ VLM の説明とみなす。
    """
    for ref in refs if isinstance(refs, list) else []:
        if not isinstance(ref, Mapping) or str(ref.get("category") or "") != "Picture":
            continue
        if str(ref.get("raw_type") or "") == "picture_ocr_text":
            continue
        if str(ref.get("vision_model") or "").strip() and not ref.get("vision_error"):
            return FIGURE_TEXT_SOURCE_VISION
    return FIGURE_TEXT_SOURCE_OCR


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
