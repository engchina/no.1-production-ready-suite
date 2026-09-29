"""DocRAG 解析(Docling)を実行し、共通抽出 schema へ変換する。

rag_poc の analyze_pdf から UI・run 保存・再開・Vision を除いた最小のオーケストレーション。
LayoutRecord 全体(raw を含む)は parser_artifacts["docrag_layout"] に保持し、
Small-to-Big チャンク化(backend)が rag_poc と同じ入力で分割できるようにする。
図・画像の Vision は解析エンジンに依存しない backend の共通の段(``app.rag.vision``)が
docrag_layout の record を読み取って反映する(#497)。このサービスは LLM を呼ばない。
"""

from __future__ import annotations

import logging
import mimetypes
import tempfile
from dataclasses import asdict
from pathlib import Path
from typing import Any

from docrag.adapters.parsers.base import AnalysisContext
from docrag.adapters.parsers.docling_adapter import DoclingAdapter, _table_grid_rows
from docrag.config import Settings, get_settings
from docrag.models.layout import LayoutRecord, PageImage
from docrag.parsing.layout_metadata import (
    layout_record_element_metadata,
    layout_record_vision_summary,
)
from docrag.parsing.rendering import (
    SUPPORTED_SOURCE_FILE_TYPES,
    get_source_page_count,
    prepare_source_for_analysis,
    source_frame_warnings,
)
from docrag.parsing.visual_artifacts import persist_semantic_visual_crops
from rag_parser_core.extraction import (
    DocumentElement,
    ExtractionAsset,
    ExtractionMetadataValue,
    ExtractionPage,
    ExtractionTable,
    ExtractionTableCell,
    StructuredExtraction,
)

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


def analyze_source(
    source_bytes: bytes,
    *,
    file_name: str,
    content_type: str,
    settings: Settings | None = None,
) -> StructuredExtraction:
    """PDF / 画像を解析し、DocRAG の LayoutRecord を保持した StructuredExtraction を返す。"""
    # env(DOCRAG_* / DOCLING_*)から解決する。.env は読まない。
    settings = settings or get_settings(dotenv_path=None)
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
        return layout_to_extraction(
            records,
            pages,
            source_page_count=page_count,
            warnings=warnings,
        )


def layout_to_extraction(
    records: list[LayoutRecord],
    pages: list[PageImage],
    *,
    source_page_count: int,
    warnings: list[str] | None = None,
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
            DOCRAG_LAYOUT_ARTIFACT: {
                "version": DOCRAG_LAYOUT_VERSION,
                "pages": [_page_payload(page) for page in pages],
                "records": [_json_value(record.to_dict()) for record in records],
            },
        },
    )


def _element_metadata(record: LayoutRecord) -> dict[str, ExtractionMetadataValue]:
    return dict(layout_record_element_metadata(record))


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
    summary = layout_record_vision_summary(record)
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
