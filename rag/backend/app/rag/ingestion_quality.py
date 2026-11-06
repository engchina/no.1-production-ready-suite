"""取込品質レポートの生成とレビュー判定。"""

from collections.abc import Mapping, Sequence

from app.schemas.document import SourceProfile
from app.schemas.extraction import IngestionQualityReport, StructuredExtraction

LONG_DOCUMENT_PAGE_THRESHOLD = 30
TABLE_ARTIFACT_COUNT_KEYS = ("table_count", "adapter_table_count")
FIGURE_ARTIFACT_COUNT_KEYS = (
    "figure_count",
    "image_count",
    "picture_count",
    "chart_count",
    "asset_count",
)
FORMULA_ARTIFACT_COUNT_KEYS = ("formula_count", "equation_count")
LOW_CONFIDENCE_ARTIFACT_COUNT_KEYS = (
    "low_confidence_count",
    "low_confidence_element_count",
)
FAILED_SEGMENT_ARTIFACT_COUNT_KEYS = ("failed_segment_count", "failed_segments")
PAGE_ARTIFACT_COUNT_KEYS = ("page_count", "page_total")
FIGURE_ASSET_KINDS = {"figure", "image", "picture", "chart", "diagram"}
MEDIUM_RISK_WARNING_CODES = {
    "table_structure_review",
    "figure_ocr_review",
    "formula_review",
    "long_document",
    "large_file",
    "content_type_extension_mismatch",
    "parser_fallback_used",
    "failed_segments",
    "extraction_artifact_cache_failed",
    "segment_extraction_artifact_cache_miss",
    "docling_adapter_package_missing",
    "unstructured_adapter_package_missing",
    "docling_adapter_feature_flag_disabled",
    "unstructured_adapter_feature_flag_disabled",
    "docling_adapter_source_unsupported",
    "unstructured_adapter_source_unsupported",
    "docling_adapter_unavailable",
    "unstructured_adapter_unavailable",
    "docling_adapter_failed",
    "unstructured_adapter_failed",
    "docling_adapter_empty",
    "unstructured_adapter_empty",
    "docling_adapter_unsupported",
    "unstructured_adapter_unsupported",
    "excel_header_low_confidence",
}
# 文書レシピを確認（REVIEW）で止める warning（全体のレビューの設定に関係なく止める。#1229）。
# Excel の表頭の推定の信頼度が低いシートは、誤った表頭で黙って索引を作らない。
REVIEW_REQUIRED_WARNING_CODES = {"excel_header_low_confidence"}
HIGH_RISK_WARNING_CODES = {
    "no_structured_elements",
    "low_extraction_confidence",
    "unknown_modality",
    "content_type_missing",
    "unsupported_outlook_msg",
    "unsupported_tiff_image",
    "unsupported_legacy_office_binary",
    "unsupported_macro_enabled_office",
    "unsupported_office_template",
    "unsupported_odt_rtf",
}


def build_ingestion_quality_report(
    extraction: StructuredExtraction,
    *,
    source_profile: SourceProfile | None = None,
    parser_profile: str = "enterprise_ai_generic",
    parser_backend: str | None = None,
    parser_version: str | None = None,
    fallback_used: bool = False,
    failed_segment_count: int = 0,
) -> IngestionQualityReport:
    """構造化抽出結果から評価に使える品質レポートを作る。"""
    pages = _structured_content_pages(extraction)
    artifact_page_count = _artifact_count(extraction.parser_artifacts, PAGE_ARTIFACT_COUNT_KEYS)
    extraction_page_count = max(len(extraction.pages), artifact_page_count)
    page_count = max(extraction_page_count, len(pages))
    table_count = max(
        len(extraction.tables),
        sum(1 for element in extraction.elements if element.kind == "table"),
        _artifact_count(extraction.parser_artifacts, TABLE_ARTIFACT_COUNT_KEYS),
    )
    figure_count = max(
        sum(1 for element in extraction.elements if element.kind in {"figure", "figure_caption"}),
        sum(1 for asset in extraction.assets if _is_figure_asset_kind(asset.kind)),
        _artifact_count(extraction.parser_artifacts, FIGURE_ARTIFACT_COUNT_KEYS),
    )
    formula_count = max(
        sum(
            1
            for element in extraction.elements
            if element.kind in {"formula", "equation"} or element.content_kind == "equation"
        ),
        _artifact_count(extraction.parser_artifacts, FORMULA_ARTIFACT_COUNT_KEYS),
    )
    low_confidence_count = max(
        _structured_low_confidence_count(extraction),
        _artifact_count(extraction.parser_artifacts, LOW_CONFIDENCE_ARTIFACT_COUNT_KEYS),
    )
    effective_failed_segment_count = max(
        failed_segment_count,
        _artifact_count(extraction.parser_artifacts, FAILED_SEGMENT_ARTIFACT_COUNT_KEYS),
    )
    page_coverage = _page_coverage(pages, extraction_page_count)
    long_document = page_count >= LONG_DOCUMENT_PAGE_THRESHOLD
    warnings = _dedupe_warnings(
        [
            *(source_profile.quality_warnings if source_profile is not None else []),
            *extraction.warnings,
            "table_structure_review" if table_count else "",
            "figure_ocr_review" if figure_count else "",
            "formula_review" if formula_count else "",
            "long_document" if long_document else "",
            "low_extraction_confidence" if extraction.confidence < 0.65 else "",
            "low_confidence_elements" if low_confidence_count else "",
            "no_structured_elements" if not extraction.elements else "",
            "parser_fallback_used" if fallback_used else "",
            "failed_segments" if effective_failed_segment_count else "",
        ]
    )
    return IngestionQualityReport(
        parser_profile=parser_profile,
        parser_backend=(
            parser_backend
            or (source_profile.parser_backend if source_profile is not None else "enterprise_ai")
        ),
        parser_version=(
            parser_version
            or (source_profile.parser_version if source_profile is not None else "v1")
        ),
        fallback_used=fallback_used,
        risk_level=_risk_level(warnings),
        page_count=page_count,
        page_coverage=page_coverage,
        table_count=table_count,
        figure_count=figure_count,
        formula_count=formula_count,
        element_count=len(extraction.elements),
        low_confidence_count=low_confidence_count,
        failed_segment_count=effective_failed_segment_count,
        long_document=long_document,
        quality_warnings=warnings,
    )


def _structured_content_pages(extraction: StructuredExtraction) -> set[int]:
    """first-class 構造も含め、抽出済み content が存在するページを集める。"""
    pages = {
        element.page_number for element in extraction.elements if element.page_number is not None
    }
    pages.update(table.page_number for table in extraction.tables if table.page_number is not None)
    pages.update(asset.page_number for asset in extraction.assets if asset.page_number is not None)
    pages.update(page.page_number for page in extraction.pages if page.element_ids)
    return pages


def _structured_low_confidence_count(extraction: StructuredExtraction) -> int:
    count = sum(
        1
        for element in extraction.elements
        if element.confidence is not None and element.confidence < 0.65
    )
    for table in extraction.tables:
        count += sum(
            1 for cell in table.cells if cell.confidence is not None and cell.confidence < 0.65
        )
    return count


def _artifact_count(artifacts: Mapping[str, object], keys: Sequence[str]) -> int:
    return max((_positive_int(artifacts.get(key)) for key in keys), default=0)


def _positive_int(value: object) -> int:
    if isinstance(value, bool):
        return 0
    if isinstance(value, int):
        return value if value > 0 else 0
    if isinstance(value, float):
        return int(value) if value.is_integer() and value > 0 else 0
    if isinstance(value, str) and value.strip().isdigit():
        return int(value.strip())
    return 0


def _is_figure_asset_kind(kind: str) -> bool:
    return kind.strip().casefold() in FIGURE_ASSET_KINDS


def _dedupe_warnings(values: Sequence[str]) -> list[str]:
    seen: set[str] = set()
    warnings: list[str] = []
    for value in values:
        warning = value.strip()
        if not warning or warning in seen:
            continue
        seen.add(warning)
        warnings.append(warning)
    return warnings


def _page_coverage(element_pages: set[int], extraction_page_count: int) -> float:
    """page metadata に対して element が載っているページ比率を返す。"""
    if extraction_page_count <= 0:
        return 1.0 if element_pages else 0.0
    covered = len({page for page in element_pages if 1 <= page <= extraction_page_count})
    return round(covered / extraction_page_count, 4)


def _risk_level(warnings: Sequence[str]) -> str:
    # parser の warning は ``<code>: <例外の型名>`` の形で届くことがある(#310)。
    # code(``:`` の前)だけで判定する。
    warning_set = {warning.partition(":")[0].strip() for warning in warnings}
    if warning_set.intersection(HIGH_RISK_WARNING_CODES):
        return "high"
    if warning_set.intersection(MEDIUM_RISK_WARNING_CODES):
        return "medium"
    return "low"


def requires_review(report: IngestionQualityReport | None) -> bool:
    """取込の品質に、確認（REVIEW）で止める warning があるか。"""
    if report is None:
        return False
    codes = {warning.partition(":")[0].strip() for warning in report.quality_warnings}
    return bool(codes & REVIEW_REQUIRED_WARNING_CODES)
