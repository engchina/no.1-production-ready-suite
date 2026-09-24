"""DocRAG 解析(Docling + 任意 Vision)を実行し、共通抽出 schema へ変換する。

rag_poc の analyze_pdf から UI・run 保存・再開を除いた最小のオーケストレーション。
LayoutRecord 全体(raw を含む)は parser_artifacts["docrag_layout"] に保持し、
Small-to-Big チャンク化(backend)が rag_poc と同じ入力で分割できるようにする。
"""

from __future__ import annotations

import logging
import mimetypes
import tempfile
from dataclasses import asdict
from pathlib import Path
from typing import Any

from rag_parser_core.extraction import (
    DocumentElement,
    ExtractionAsset,
    ExtractionMetadataValue,
    ExtractionPage,
    ExtractionTable,
    ExtractionTableCell,
    StructuredExtraction,
)

from app.docrag.base import AnalysisContext
from app.docrag.docling_adapter import DoclingAdapter, _table_grid_rows
from app.docrag.layout import LayoutRecord, PageImage
from app.docrag.rendering import (
    SUPPORTED_SOURCE_FILE_TYPES,
    get_source_page_count,
    prepare_source_for_analysis,
    source_frame_warnings,
)
from app.docrag.settings import Settings
from app.docrag.visual_artifacts import persist_semantic_visual_crops

logger = logging.getLogger(__name__)

DOCRAG_LAYOUT_ARTIFACT = "docrag_layout"
DOCRAG_LAYOUT_VERSION = 1

# rag_poc の正規化カテゴリ → 共通抽出 schema の element kind。
CATEGORY_KINDS = {
    "Title": "title",
    "Section-header": "title",
    "Caption": "figure_caption",
    "Footnote": "text",
    "Formula": "equation",
    "List-item": "list",
    "Page-footer": "footer",
    "Page-header": "header",
    "Picture": "figure",
    "Table": "table",
    "Text": "text",
}

# element metadata へ写す raw のスカラー値(プレビューと診断に使う)。
_RAW_SCALAR_KEYS = (
    "vision_status",
    "vision_error",
    "vision_model",
    "vision_skipped",
    "visual_role",
    "visual_role_reason",
    "visual_kind",
    "picture_kind",
    "rag_excluded",
    "table_vision_text",
    "table_image_detection_status",
)


def analyze_source(
    source_bytes: bytes,
    *,
    file_name: str,
    content_type: str,
    vision_enabled: bool,
    settings: Settings | None = None,
) -> StructuredExtraction:
    """PDF / 画像を解析し、DocRAG の LayoutRecord を保持した StructuredExtraction を返す。"""
    settings = settings or Settings()
    suffix = _source_suffix(file_name, content_type)
    settings.output_dir.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=settings.output_dir) as work:
        run_dir = Path(work)
        source_path = run_dir / f"input{suffix}"
        source_path.write_bytes(source_bytes)
        page_count = get_source_page_count(source_path)
        pdf_path, pages = prepare_source_for_analysis(
            source_path, list(range(1, page_count + 1)), run_dir, settings.render_dpi
        )
        records = DoclingAdapter(settings).analyze(
            AnalysisContext(
                pdf_path=Path(pdf_path), run_dir=run_dir, pages=pages, settings=settings
            )
        )
        persist_semantic_visual_crops(records, pages, run_dir=run_dir)
        warnings = list(source_frame_warnings(source_path))
        vision_summary: dict[str, Any] = {"enabled": vision_enabled}
        if vision_enabled:
            vision_summary.update(_describe(records, pages, run_dir, file_name, settings, pdf_path))
            if vision_summary.get("failed"):
                warnings.append("docling_vision_partial_failure")
        return layout_to_extraction(
            records,
            pages,
            source_page_count=page_count,
            warnings=warnings,
            vision_summary=vision_summary,
        )


def _describe(
    records: list[LayoutRecord],
    pages: list[PageImage],
    run_dir: Path,
    file_name: str,
    settings: Settings,
    pdf_path: str,
) -> dict[str, Any]:
    # Vision は任意機能。依存(Pillow 等)と LLM 設定が揃う場合だけ import する。
    from app.docrag.picture_descriptions import describe_docling_pictures

    try:
        stats = describe_docling_pictures(
            records,
            pages,
            run_dir=run_dir,
            pdf_name=file_name,
            settings=settings,
            pdf_path=Path(pdf_path),
        )
    except Exception as exc:  # 個別失敗は record に残る。準備失敗は解析全体を止めない。
        logger.warning("docling vision failed", extra={"error": str(exc)})
        return {"error": str(exc), "failed": 1}
    return {
        "targets": stats.targets,
        "succeeded": stats.succeeded,
        "failed": stats.failed,
        "discovery_failed": stats.discovery_failed,
    }


def layout_to_extraction(
    records: list[LayoutRecord],
    pages: list[PageImage],
    *,
    source_page_count: int,
    warnings: list[str] | None = None,
    vision_summary: dict[str, Any] | None = None,
) -> StructuredExtraction:
    """LayoutRecord 群を共通抽出 schema(要素・ページ・表・図)へ写す。"""
    elements: list[DocumentElement] = []
    tables: list[ExtractionTable] = []
    assets: list[ExtractionAsset] = []
    section_path: list[str] = []
    for order, record in enumerate(records):
        kind = CATEGORY_KINDS.get(record.category, "other")
        text = str(record.text or "")
        if record.category == "Title" and text.strip():
            section_path = [text.strip()]
        elif record.category == "Section-header" and text.strip():
            section_path = [*section_path[:1], text.strip()]
        elements.append(
            DocumentElement(
                kind=kind,
                text=text,
                order=order,
                element_id=record.id,
                source_parser="docling_docrag",
                page_number=record.page,
                bbox=[float(value) for value in record.bbox],
                section_path=list(section_path),
                metadata=_element_metadata(record),
            )
        )
        if record.category == "Table" and record.raw_type != "table_unassigned_text":
            tables.append(_table(record))
        if record.category == "Picture" and record.raw_type != "picture_ocr_text":
            assets.append(_asset(record))
    raw_text = "\n\n".join(e.text for e in elements if e.text.strip() and e.kind != "table")
    return StructuredExtraction(
        raw_text=raw_text,
        document_type="ドキュメント",
        confidence=0.9 if records else 0.0,
        warnings=list(warnings or []),
        elements=elements,
        pages=[
            ExtractionPage(
                page_number=page.page,
                width=float(page.width),
                height=float(page.height),
                element_ids=[r.id for r in records if r.page == page.page],
                metadata={"pdf_width": page.pdf_width, "pdf_height": page.pdf_height},
            )
            for page in pages
        ],
        tables=tables,
        assets=assets,
        parser_artifacts={
            "external_adapter": "docling",
            "adapter_export": "docrag_layout_records",
            "source_page_count": source_page_count,
            "table_count": len(tables),
            "picture_count": len(assets),
            "vision": _json_value(vision_summary or {"enabled": False}),
            DOCRAG_LAYOUT_ARTIFACT: {
                "version": DOCRAG_LAYOUT_VERSION,
                "pages": [_page_payload(page) for page in pages],
                "records": [_json_value(record.to_dict()) for record in records],
            },
        },
    )


def _element_metadata(record: LayoutRecord) -> dict[str, ExtractionMetadataValue]:
    metadata: dict[str, ExtractionMetadataValue] = {
        "category": record.category,
        "raw_type": record.raw_type,
        "page_width": float(record.page_width),
        "page_height": float(record.page_height),
        "bbox_unit": "absolute",
        "bbox_mode": "xyxy",
        "seq_no": record.seq_no,
    }
    for key in _RAW_SCALAR_KEYS:
        value = record.raw.get(key)
        if isinstance(value, str | int | float | bool) and value != "":
            metadata[key] = value
    description = record.raw.get("vision_description")
    if isinstance(description, dict) and description.get("retrieval_text"):
        metadata["vision_retrieval_text"] = str(description["retrieval_text"])
    return metadata


def _table(record: LayoutRecord) -> ExtractionTable:
    cells = [
        ExtractionTableCell(row=row_index, col=col_index, text=text, metadata={"header": is_header})
        for row_index, row in enumerate(_table_grid_rows(record.raw))
        for col_index, (text, is_header) in enumerate(row)
    ]
    return ExtractionTable(
        table_id=f"table-{record.id}",
        element_id=record.id,
        page_number=record.page,
        cells=cells,
        metadata={"html": record.text or ""},
    )


def _asset(record: LayoutRecord) -> ExtractionAsset:
    description = record.raw.get("vision_description")
    summary = ""
    if isinstance(description, dict):
        summary = str(description.get("retrieval_text") or "")
    return ExtractionAsset(
        asset_id=f"asset-{record.id}",
        kind="figure",
        page_number=record.page,
        bbox=[float(value) for value in record.bbox],
        alt_text=(record.text or "")[:2000] or None,
        summary=summary or None,
        metadata={
            "element_id": record.id,
            "vision_status": str(record.raw.get("vision_status") or ""),
            "visual_role": str(record.raw.get("visual_role") or ""),
        },
    )


def _page_payload(page: PageImage) -> dict[str, Any]:
    payload = asdict(page)
    # 画像ファイルはサービス内の一時領域。backend は PDF から再描画する。
    payload.pop("image_path", None)
    payload.pop("image_url", None)
    return payload


def _source_suffix(file_name: str, content_type: str) -> str:
    suffix = Path(file_name or "").suffix.lower()
    if suffix in SUPPORTED_SOURCE_FILE_TYPES:
        return suffix
    guessed = mimetypes.guess_extension((content_type or "").split(";")[0].strip()) or ""
    if guessed in SUPPORTED_SOURCE_FILE_TYPES:
        return guessed
    if guessed == ".jpe":
        return ".jpg"
    raise ValueError("docling は PDF または画像のみ解析できます。")


def _json_value(value: Any) -> Any:
    """Path など JSON 化できない値を文字列へ寄せた純 JSON 値にする。"""
    if value is None or isinstance(value, str | int | float | bool):
        return value
    if isinstance(value, dict):
        return {str(key): _json_value(item) for key, item in value.items()}
    if isinstance(value, list | tuple | set):
        return [_json_value(item) for item in value]
    return str(value)
