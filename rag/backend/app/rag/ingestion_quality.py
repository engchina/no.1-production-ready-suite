"""取込品質レポートの生成と評価向け集計。"""

from collections.abc import Mapping, Sequence

from app.schemas.document import SourceProfile
from app.schemas.extraction import IngestionQualityReport, StructuredExtraction

LONG_DOCUMENT_PAGE_THRESHOLD = 30
MEDIUM_RISK_WARNING_CODES = {
    "table_structure_review",
    "figure_ocr_review",
    "long_document",
    "large_file",
    "content_type_extension_mismatch",
}
HIGH_RISK_WARNING_CODES = {
    "no_structured_elements",
    "low_extraction_confidence",
    "unknown_modality",
    "content_type_missing",
}


def build_ingestion_quality_report(
    extraction: StructuredExtraction,
    *,
    source_profile: SourceProfile | None = None,
    parser_profile: str = "enterprise_ai_generic",
) -> IngestionQualityReport:
    """構造化抽出結果から評価に使える品質レポートを作る。"""
    pages = {
        element.page_number for element in extraction.elements if element.page_number is not None
    }
    table_count = sum(1 for element in extraction.elements if element.kind == "table")
    figure_count = sum(
        1 for element in extraction.elements if element.kind in {"figure", "figure_caption"}
    )
    page_count = len(pages)
    long_document = page_count >= LONG_DOCUMENT_PAGE_THRESHOLD
    warnings = _dedupe_warnings(
        [
            *(source_profile.quality_warnings if source_profile is not None else []),
            *extraction.warnings,
            "table_structure_review" if table_count else "",
            "figure_ocr_review" if figure_count else "",
            "long_document" if long_document else "",
            "low_extraction_confidence" if extraction.confidence < 0.65 else "",
            "no_structured_elements" if not extraction.elements else "",
        ]
    )
    return IngestionQualityReport(
        parser_profile=parser_profile,
        risk_level=_risk_level(warnings),
        page_count=page_count,
        table_count=table_count,
        figure_count=figure_count,
        element_count=len(extraction.elements),
        long_document=long_document,
        quality_warnings=warnings,
    )


def summarize_ingestion_quality(
    extractions: Sequence[Mapping[str, object]],
) -> dict[str, object]:
    """保存済み extraction JSON から evaluation response 用の品質集計を作る。"""
    reports: list[IngestionQualityReport] = []
    for extraction in extractions:
        report = _quality_report_from_payload(extraction)
        if report is not None:
            reports.append(report)
    warning_counts: dict[str, int] = {}
    risk_counts = {"low": 0, "medium": 0, "high": 0}
    parser_profile_counts: dict[str, int] = {}
    for report in reports:
        risk_counts[report.risk_level] = risk_counts.get(report.risk_level, 0) + 1
        parser_profile_counts[report.parser_profile] = (
            parser_profile_counts.get(report.parser_profile, 0) + 1
        )
        for warning in report.quality_warnings:
            warning_counts[warning] = warning_counts.get(warning, 0) + 1
    return {
        "document_count": len(reports),
        "table_document_count": sum(1 for report in reports if report.table_count > 0),
        "figure_document_count": sum(1 for report in reports if report.figure_count > 0),
        "long_document_count": sum(1 for report in reports if report.long_document),
        "warning_counts": warning_counts,
        "risk_counts": risk_counts,
        "parser_profile_counts": parser_profile_counts,
    }


def _quality_report_from_payload(
    extraction: Mapping[str, object],
) -> IngestionQualityReport | None:
    payload = extraction.get("quality_report")
    if not isinstance(payload, Mapping):
        return _legacy_quality_report_from_payload(extraction)
    return IngestionQualityReport.model_validate(payload)


def _legacy_quality_report_from_payload(
    extraction: Mapping[str, object],
) -> IngestionQualityReport | None:
    """旧 extraction も評価集計に入れるため、最低限の品質レポートを推定する。"""
    elements = extraction.get("elements")
    if not isinstance(elements, list):
        return None
    table_count = 0
    figure_count = 0
    pages: set[int] = set()
    for element in elements:
        if not isinstance(element, Mapping):
            continue
        kind = str(element.get("kind", ""))
        if kind == "table":
            table_count += 1
        if kind in {"figure", "figure_caption"}:
            figure_count += 1
        page_number = element.get("page_number")
        if isinstance(page_number, int) and page_number >= 1:
            pages.add(page_number)
    warnings = _dedupe_warnings(
        [
            "table_structure_review" if table_count else "",
            "figure_ocr_review" if figure_count else "",
            "long_document" if len(pages) >= LONG_DOCUMENT_PAGE_THRESHOLD else "",
        ]
    )
    return IngestionQualityReport(
        parser_profile="legacy",
        risk_level=_risk_level(warnings),
        page_count=len(pages),
        table_count=table_count,
        figure_count=figure_count,
        element_count=len(elements),
        long_document=len(pages) >= LONG_DOCUMENT_PAGE_THRESHOLD,
        quality_warnings=warnings,
    )


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


def _risk_level(warnings: Sequence[str]) -> str:
    warning_set = set(warnings)
    if warning_set.intersection(HIGH_RISK_WARNING_CODES):
        return "high"
    if warning_set.intersection(MEDIUM_RISK_WARNING_CODES):
        return "medium"
    return "low"
