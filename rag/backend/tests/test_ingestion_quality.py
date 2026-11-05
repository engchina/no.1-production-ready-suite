"""取込品質レポートの構造カウントを検証する。"""

import pytest

from app.rag.ingestion_quality import (
    build_ingestion_quality_report,
    requires_review,
)
from app.schemas.extraction import (
    DocumentElement,
    ExtractionAsset,
    ExtractionPage,
    ExtractionTable,
    ExtractionTableCell,
    StructuredExtraction,
)


def test_quality_report_counts_first_class_structure_and_parser_artifacts() -> None:
    """Docling 系の first-class metadata から品質指標を落とさず集計する。"""
    extraction = StructuredExtraction(
        raw_text="本文\n表\n図",
        confidence=0.92,
        elements=[
            DocumentElement(kind="text", text="本文", page_number=1, confidence=0.6),
        ],
        pages=[
            ExtractionPage(page_number=1, element_ids=["el-0000"]),
            ExtractionPage(page_number=2),
            ExtractionPage(page_number=3, element_ids=["tbl-main"]),
            ExtractionPage(page_number=4, element_ids=["fig-1"]),
        ],
        tables=[
            ExtractionTable(
                table_id="tbl-main",
                element_id="tbl-main",
                page_number=3,
                cells=[ExtractionTableCell(row=0, col=0, text="金額", confidence=0.4)],
            )
        ],
        assets=[ExtractionAsset(asset_id="fig-1", kind="image", page_number=4, alt_text="構成図")],
        parser_artifacts={
            "page_count": 5,
            "table_count": 2,
            "equation_count": 2,
            "asset_count": 3,
            "low_confidence_count": 4,
            "failed_segment_count": 1,
        },
    )

    report = build_ingestion_quality_report(
        extraction,
        parser_profile="external_layout",
        parser_backend="docling",
        parser_version="2.0.0",
    )

    assert report.parser_backend == "docling"
    assert report.parser_version == "2.0.0"
    assert report.page_count == 5
    assert report.page_coverage == 0.6
    assert report.table_count == 2
    assert report.figure_count == 3
    assert report.formula_count == 2
    assert report.low_confidence_count == 4
    assert report.failed_segment_count == 1
    assert report.risk_level == "medium"
    assert report.quality_warnings == [
        "table_structure_review",
        "figure_ocr_review",
        "formula_review",
        "low_confidence_elements",
        "failed_segments",
    ]


@pytest.mark.parametrize("confidence,expected_risk", [(0.1, "high"), (0.95, "low")])
def test_new_quality_report_preserves_measured_risk_without_saved_report(
    confidence: float, expected_risk: str
) -> None:
    """保存報告のない新しい解析結果から、実測の信頼度を品質に反映する。"""
    extraction = StructuredExtraction(
        raw_text="合成の本文",
        confidence=confidence,
        elements=[DocumentElement(kind="text", text="合成の本文", page_number=1)],
        pages=[ExtractionPage(page_number=1, element_ids=["el-synthetic"])],
    )
    assert extraction.quality_report is None

    report = build_ingestion_quality_report(
        extraction,
        parser_profile="synthetic",
        parser_backend="synthetic",
        parser_version="synthetic-v1",
    )

    assert report.risk_level == expected_risk
    assert report.page_coverage == 1.0
    assert report.element_count == 1
    assert report.parser_profile == "synthetic"
    assert report.parser_backend == "synthetic"
    assert report.parser_version == "synthetic-v1"
    assert ("low_extraction_confidence" in report.quality_warnings) == (confidence < 0.65)
    saved = extraction.model_copy(update={"quality_report": report})
    restored = StructuredExtraction.model_validate(saved.model_dump(mode="json"))
    assert restored.quality_report == report


@pytest.mark.parametrize(
    "warning,expected_review", [("excel_header_low_confidence", True), ("", False)]
)
def test_new_quality_report_preserves_review_required_warning(
    warning: str, expected_review: bool
) -> None:
    """新規レポートでも、既存の確認必須warningを失わない。"""
    extraction = StructuredExtraction(
        raw_text="合成の本文",
        elements=[DocumentElement(kind="text", text="合成の本文")],
        warnings=[warning] if warning else [],
    )
    report = build_ingestion_quality_report(extraction)
    assert requires_review(report) is expected_review


def test_risk_level_ignores_exception_type_suffix_on_warning_codes() -> None:
    """``<code>: <例外の型名>`` の warning も code で判定する(#310)。"""
    from app.rag.ingestion_quality import _risk_level

    assert _risk_level(["docling_adapter_failed"]) == "medium"
    assert _risk_level(["docling_adapter_failed: ImportError"]) == "medium"
    assert _risk_level(["unknown_modality: detail"]) == "high"
