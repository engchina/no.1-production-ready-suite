"""ファイル処理 golden set 向けの軽量評価指標。"""

import hashlib
import json
import math
import mimetypes
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import TypeGuard

from app.rag.chunking import Chunk, chunk_extraction
from app.rag.ingestion_quality import build_ingestion_quality_report
from app.rag.parsers import (
    parse_openxml_office_segment_extractions,
    parse_with_registry,
    template_for_source_profile,
)
from app.rag.source_profile import build_source_profile
from app.schemas.extraction import StructuredExtraction
from app.schemas.search import RetrievedChunk

REQUIRED_FILE_PROCESSING_METRICS = frozenset(
    {
        "retrieval_recall",
        "table_qa_accuracy",
        "page_hit_accuracy",
        "citation_traceability_coverage",
        "bbox_citation_coverage",
        "preview_addressability_coverage",
        "element_lineage_coverage",
        "extraction_page_coverage",
        "low_confidence_document_rate",
        "failed_segment_rate",
        "groundedness",
        "parser_fallback_rate",
        "ingestion_p95_ms",
    }
)
FILE_PROCESSING_THRESHOLD_DIRECTIONS = {
    "retrieval_recall": "min",
    "table_qa_accuracy": "min",
    "page_hit_accuracy": "min",
    "citation_traceability_coverage": "min",
    "bbox_citation_coverage": "min",
    "preview_addressability_coverage": "min",
    "element_lineage_coverage": "min",
    "extraction_page_coverage": "min",
    "low_confidence_document_rate": "max",
    "failed_segment_rate": "max",
    "groundedness": "min",
    "parser_fallback_rate": "max",
    "ingestion_p95_ms": "max",
}

REQUIRED_FILE_PROCESSING_SCENARIOS = frozenset(
    {
        "scanned_pdf_ocr",
        "two_column_pdf_reading_order",
        "long_table_row_groups",
        "japanese_docx_layout",
        "japanese_pptx_slides",
        "japanese_xlsx_sheets",
        "html_semantic_blocks",
        "markdown_code_formula_blocks",
        "email_thread_headers",
        "image_ocr_bbox",
        "duplicate_file_canonical_kb",
        "corrupted_file_partial_failure",
        "legacy_office_unsupported",
        "tiff_image_unsupported",
        "audio_unsupported",
    }
)
REQUIRED_FILE_PROCESSING_SCENARIO_CHECKS: Mapping[str, frozenset[str]] = {
    "scanned_pdf_ocr": frozenset(
        {"ocr_text", "page_coverage", "citation_traceability"}
    ),
    "two_column_pdf_reading_order": frozenset(
        {"reading_order", "page_hit_accuracy", "citation_traceability"}
    ),
    "long_table_row_groups": frozenset(
        {"table_preserve_rows", "table_qa_accuracy", "element_lineage"}
    ),
    "japanese_docx_layout": frozenset(
        {"heading_structure", "paragraph_order", "element_lineage"}
    ),
    "japanese_pptx_slides": frozenset(
        {"slide_segment", "citation_traceability", "element_lineage"}
    ),
    "japanese_xlsx_sheets": frozenset(
        {"sheet_segment", "table_preserve_rows", "element_lineage"}
    ),
    "html_semantic_blocks": frozenset(
        {"heading_structure", "section_path", "citation_traceability"}
    ),
    "markdown_code_formula_blocks": frozenset(
        {"heading_structure", "code_block", "equation_block", "element_lineage"}
    ),
    "email_thread_headers": frozenset(
        {"email_headers", "thread_body", "attachment_metadata"}
    ),
    "image_ocr_bbox": frozenset({"ocr_text", "bbox_citation", "preview_jump"}),
    "tiff_image_unsupported": frozenset({"unsupported_reason", "safe_error"}),
    "audio_unsupported": frozenset({"unsupported_reason", "safe_error"}),
    "duplicate_file_canonical_kb": frozenset(
        {"canonical_alias", "knowledge_base_membership", "searchable_canonical"}
    ),
    "corrupted_file_partial_failure": frozenset(
        {"failed_segment_status", "artifact_reuse", "safe_error"}
    ),
    "legacy_office_unsupported": frozenset({"unsupported_reason", "safe_error"}),
}

_REQUIRED_CASE_FIELDS = frozenset(
    {
        "id",
        "fixture",
        "modality",
        "scenario",
        "expected_parser_profile",
        "expected_chunk_template",
        "required_checks",
    }
)
_CASE_ASSERTION_FIELDS = frozenset(
    {
        "expected_content_kind",
        "expected_warning",
        "expected_answer",
        "expected_pages",
        "expected_unsupported_reason",
    }
)
_SUPPORTED_MODALITIES = frozenset(
    {
        "pdf",
        "image",
        "text",
        "markdown",
        "json",
        "csv",
        "tsv",
        "html",
        "email",
        "office",
        "audio",
    }
)
_OPTIONAL_FIXTURE_FIELDS = ("duplicate_fixture",)
_FIXTURE_EXTENSIONS_BY_MODALITY = {
    "pdf": frozenset({".pdf"}),
    "image": frozenset({".png", ".jpg", ".jpeg", ".tif", ".tiff", ".webp"}),
    "text": frozenset({".txt"}),
    "markdown": frozenset({".md", ".markdown"}),
    "json": frozenset({".json"}),
    "csv": frozenset({".csv"}),
    "tsv": frozenset({".tsv"}),
    "html": frozenset({".html", ".htm", ".xhtml"}),
    "email": frozenset({".eml", ".msg"}),
    "office": frozenset({".docx", ".pptx", ".xlsx", ".doc", ".ppt", ".xls"}),
    "audio": frozenset({".mp3", ".wav", ".m4a", ".flac", ".ogg"}),
}
_STAGING_ONLY_CHECKS = frozenset(
    {
        "artifact_reuse",
        "knowledge_base_membership",
        "page_hit_accuracy",
        "searchable_canonical",
    }
)


@dataclass(frozen=True)
class TableQaResult:
    """表 QA の期待値と実回答。"""

    case_id: str
    expected_answer: str
    actual_answer: str


@dataclass(frozen=True)
class PageHitCase:
    """citation が当てるべき document/page。"""

    case_id: str
    expected_document_id: str
    expected_pages: tuple[int, ...]


@dataclass(frozen=True)
class FileProcessingContractCaseResult:
    """file-processing manifest の 1 case に対する local contract 結果。"""

    case_id: str
    fixture: str
    parser_profile: str = ""
    parser_backend: str = ""
    chunk_template: str = ""
    fallback_used: bool = False
    page_coverage: float | None = None
    low_confidence_count: int = 0
    failed_segment_count: int = 0
    passed_checks: tuple[str, ...] = ()
    pending_checks: tuple[str, ...] = ()
    failures: tuple[str, ...] = ()


@dataclass(frozen=True)
class FileProcessingContractReport:
    """file-processing manifest の local contract 集計結果。"""

    manifest_errors: tuple[str, ...] = ()
    case_results: tuple[FileProcessingContractCaseResult, ...] = ()

    @property
    def passed(self) -> bool:
        """local contract と manifest/asset 契約に失敗がないか。"""
        return not self.manifest_errors and not any(result.failures for result in self.case_results)

    @property
    def case_count(self) -> int:
        return len(self.case_results)

    @property
    def failure_count(self) -> int:
        return len(self.manifest_errors) + sum(len(result.failures) for result in self.case_results)

    @property
    def pending_staging_check_count(self) -> int:
        return sum(len(result.pending_checks) for result in self.case_results)


@dataclass(frozen=True)
class FileProcessingStagingRequirement:
    """staging で閉じるべき file-processing pending check。"""

    case_id: str
    scenario: str
    fixture: str
    check: str
    reason: str
    required_evidence: tuple[str, ...]
    suggested_gate: str


@dataclass(frozen=True)
class FileProcessingMetricThresholdResult:
    """file-processing metric threshold の評価結果。"""

    metric: str
    direction: str
    threshold: float
    actual: float | None
    status: str
    passed: bool
    reason: str | None = None


def validate_file_processing_manifest(
    manifest: Mapping[str, object],
) -> tuple[str, ...]:
    """file-processing golden set manifest の最低契約を検証する。"""
    errors: list[str] = []

    metrics = _string_set(manifest.get("metrics"))
    missing_metrics = REQUIRED_FILE_PROCESSING_METRICS - metrics
    if missing_metrics:
        errors.append("missing_metrics:" + ",".join(sorted(missing_metrics)))
    errors.extend(_validate_metric_threshold_contract(manifest.get("thresholds")))

    raw_cases = manifest.get("cases")
    if not _is_sequence(raw_cases):
        errors.append("cases:not_sequence")
        return tuple(errors)

    scenarios: set[str] = set()
    seen_case_ids: set[str] = set()
    for index, raw_case in enumerate(raw_cases):
        if not isinstance(raw_case, Mapping):
            errors.append(f"case[{index}]:not_mapping")
            continue
        case = _mapping(raw_case)
        case_id = _string_value(case.get("id"))
        case_label = case_id or str(index)
        if case_id in seen_case_ids:
            errors.append(f"case[{case_label}]:duplicate_id")
        if case_id:
            seen_case_ids.add(case_id)

        missing_fields = _missing_required_case_fields(case)
        if missing_fields:
            errors.append(f"case[{case_label}]:missing_fields:{','.join(missing_fields)}")

        scenario = _string_value(case.get("scenario"))
        if scenario:
            scenarios.add(scenario)

        modality = _string_value(case.get("modality"))
        if modality and modality not in _SUPPORTED_MODALITIES:
            errors.append(f"case[{case_label}]:unsupported_modality:{modality}")

        required_checks = _string_set(case.get("required_checks"))
        if not required_checks:
            errors.append(f"case[{case_label}]:required_checks_empty")
        scenario_required_checks = REQUIRED_FILE_PROCESSING_SCENARIO_CHECKS.get(scenario)
        if scenario_required_checks:
            missing_required_checks = scenario_required_checks - required_checks
            if missing_required_checks:
                errors.append(
                    f"case[{case_label}]:missing_required_checks:"
                    + ",".join(sorted(missing_required_checks))
                )

        if not any(_has_case_assertion(case, field) for field in _CASE_ASSERTION_FIELDS):
            errors.append(f"case[{case_label}]:assertion_missing")

    missing_scenarios = REQUIRED_FILE_PROCESSING_SCENARIOS - scenarios
    if missing_scenarios:
        errors.append("missing_scenarios:" + ",".join(sorted(missing_scenarios)))

    return tuple(errors)


def evaluate_file_processing_metric_thresholds(
    metric_summary: Mapping[str, object],
    thresholds: Mapping[str, object] | None,
) -> tuple[FileProcessingMetricThresholdResult, ...]:
    """manifest thresholds を local/staging の metric summary に適用する。"""
    if not thresholds:
        return ()
    results: list[FileProcessingMetricThresholdResult] = []
    for metric, threshold_config in thresholds.items():
        direction = FILE_PROCESSING_THRESHOLD_DIRECTIONS.get(metric)
        if direction is None:
            continue
        threshold = _threshold_value(threshold_config, direction)
        if threshold is None:
            continue
        summary = _mapping(metric_summary.get(metric))
        actual = _optional_float(summary.get("value"))
        if actual is None:
            actual = _optional_float(metric_summary.get(metric))
        if actual is None:
            results.append(
                FileProcessingMetricThresholdResult(
                    metric=metric,
                    direction=direction,
                    threshold=threshold,
                    actual=None,
                    status="pending",
                    passed=True,
                    reason="metric_value_unavailable",
                )
            )
            continue
        passed = actual >= threshold if direction == "min" else actual <= threshold
        status = "passed" if passed else "failed"
        results.append(
            FileProcessingMetricThresholdResult(
                metric=metric,
                direction=direction,
                threshold=threshold,
                actual=actual,
                status=status,
                passed=passed,
            )
        )
    return tuple(results)


def build_file_processing_staging_plan(
    manifest: Mapping[str, object],
    report: FileProcessingContractReport,
) -> tuple[FileProcessingStagingRequirement, ...]:
    """local contract で pending になった項目を staging 実行計画へ変換する。"""
    raw_cases = manifest.get("cases")
    if not _is_sequence(raw_cases):
        return ()
    case_by_id = {
        _string_value(raw_case.get("id")): _mapping(raw_case)
        for raw_case in raw_cases
        if isinstance(raw_case, Mapping)
    }
    requirements: list[FileProcessingStagingRequirement] = []
    for result in report.case_results:
        case = case_by_id.get(result.case_id, {})
        scenario = _string_value(case.get("scenario"))
        for pending in result.pending_checks:
            check, reason = _split_pending_check(pending)
            requirements.append(
                FileProcessingStagingRequirement(
                    case_id=result.case_id,
                    scenario=scenario,
                    fixture=result.fixture,
                    check=check,
                    reason=reason,
                    required_evidence=_staging_required_evidence(check, reason),
                    suggested_gate=_staging_suggested_gate(check, reason),
                )
            )
    return tuple(requirements)


def run_file_processing_contract_checks(
    manifest: Mapping[str, object],
    *,
    manifest_path: Path,
) -> FileProcessingContractReport:
    """同梱 fixture を local parser/chunker で検証する。

    OCI Enterprise AI、Object Storage、Oracle 26ai が必要な品質 check は失敗扱いにせず、
    ``pending_checks`` として返す。CI では local contract の退化を検出し、staging/nightly
    では pending check を実データで閉じる。
    """
    manifest_errors = (
        *validate_file_processing_manifest(manifest),
        *validate_file_processing_fixture_assets(manifest, manifest_path=manifest_path),
    )
    if manifest_errors:
        return FileProcessingContractReport(manifest_errors=tuple(manifest_errors))
    raw_cases = manifest.get("cases")
    if not _is_sequence(raw_cases):
        return FileProcessingContractReport(manifest_errors=("cases:not_sequence",))
    fixture_root = _resolve_fixture_root(manifest, manifest_path=manifest_path)
    case_results = [
        _run_file_processing_case_contract(_mapping(raw_case), fixture_root=fixture_root)
        for raw_case in raw_cases
        if isinstance(raw_case, Mapping)
    ]
    return FileProcessingContractReport(case_results=tuple(case_results))


def validate_file_processing_fixture_assets(
    manifest: Mapping[str, object],
    *,
    manifest_path: Path,
) -> tuple[str, ...]:
    """manifest が参照する fixture 実体の存在・基本整合性を検証する。"""
    errors: list[str] = []
    fixture_root = _resolve_fixture_root(manifest, manifest_path=manifest_path)
    if not fixture_root.exists():
        errors.append(f"fixture_root:not_found:{fixture_root}")
    elif not fixture_root.is_dir():
        errors.append(f"fixture_root:not_directory:{fixture_root}")

    raw_cases = manifest.get("cases")
    if not _is_sequence(raw_cases):
        errors.append("cases:not_sequence")
        return tuple(errors)

    for index, raw_case in enumerate(raw_cases):
        if not isinstance(raw_case, Mapping):
            errors.append(f"case[{index}]:not_mapping")
            continue
        case = _mapping(raw_case)
        case_id = _string_value(case.get("id")) or str(index)
        _validate_fixture_reference(
            case,
            field="fixture",
            fixture_root=fixture_root,
            case_id=case_id,
            errors=errors,
        )
        for field in _OPTIONAL_FIXTURE_FIELDS:
            if _string_value(case.get(field)):
                _validate_fixture_reference(
                    case,
                    field=field,
                    fixture_root=fixture_root,
                    case_id=case_id,
                    errors=errors,
                )

    return tuple(errors)


def _validate_metric_threshold_contract(value: object) -> list[str]:
    errors: list[str] = []
    thresholds = _mapping(value)
    if not thresholds:
        errors.append("thresholds:missing")
        return errors
    missing = set(REQUIRED_FILE_PROCESSING_METRICS) - set(thresholds)
    if missing:
        errors.append("thresholds:missing_metrics:" + ",".join(sorted(missing)))
    for metric, threshold_config in thresholds.items():
        direction = FILE_PROCESSING_THRESHOLD_DIRECTIONS.get(metric)
        if direction is None:
            errors.append(f"thresholds:{metric}:unknown_metric")
            continue
        config = _mapping(threshold_config)
        if not config:
            errors.append(f"thresholds:{metric}:not_mapping")
            continue
        unexpected_keys = sorted(set(config) - {"min", "max"})
        if unexpected_keys:
            errors.append(f"thresholds:{metric}:unknown_keys:{','.join(unexpected_keys)}")
        wrong_direction = "max" if direction == "min" else "min"
        if wrong_direction in config:
            errors.append(f"thresholds:{metric}:wrong_direction:{wrong_direction}")
        threshold = _threshold_value(config, direction)
        if threshold is None:
            errors.append(f"thresholds:{metric}:{direction}_missing_or_invalid")
        elif threshold < 0:
            errors.append(f"thresholds:{metric}:{direction}_negative")
    return errors


def _run_file_processing_case_contract(
    case: Mapping[str, object],
    *,
    fixture_root: Path,
) -> FileProcessingContractCaseResult:
    case_id = _string_value(case.get("id"))
    fixture_name = _string_value(case.get("fixture"))
    fixture_path = fixture_root / fixture_name
    data = fixture_path.read_bytes()
    content_type = _fixture_content_type(fixture_path)
    digest = hashlib.sha256(data).hexdigest()
    source_profile = build_source_profile(
        original_file_name=fixture_name,
        sanitized_file_name=fixture_name,
        content_type=content_type,
        file_size_bytes=len(data),
        content_sha256=digest,
        data=data,
    )
    parser_result = parse_with_registry(
        data,
        source_profile=source_profile,
        content_type=content_type,
    )
    extraction = parser_result.extraction
    chunks = chunk_extraction(extraction, chunk_size=200, overlap=0) if extraction else []
    segment_result = parse_openxml_office_segment_extractions(
        data,
        source_profile=source_profile,
    )
    quality_report = (
        build_ingestion_quality_report(
            extraction,
            source_profile=source_profile,
            parser_profile=source_profile.parser_profile,
            parser_backend=parser_result.parser_backend,
            parser_version=parser_result.parser_version,
            fallback_used=parser_result.fallback_used,
            failed_segment_count=len(segment_result.failures),
        )
        if extraction is not None
        else None
    )
    failed_segment_count = max(
        quality_report.failed_segment_count if quality_report is not None else 0,
        len(segment_result.failures),
    )
    effective_template = (
        parser_result.template
        if parser_result.unsupported_reason
        or (extraction is not None and parser_result.template != "enterprise_ai_fallback")
        else template_for_source_profile(source_profile)
    )

    passed: list[str] = []
    pending: list[str] = []
    failures: list[str] = []
    _check_expected_parser_profile(case, source_profile.parser_profile, passed, failures)
    _check_expected_chunk_template(case, effective_template, passed, failures)
    _check_expected_content_kind(case, extraction, chunks, passed, pending, failures)
    _check_expected_answer(case, extraction, chunks, passed, pending, failures)
    _check_expected_warning(case, parser_result.warnings, segment_result.failures, passed, failures)
    _check_expected_unsupported_reason(case, parser_result.unsupported_reason, passed, failures)
    _check_expected_pages(case, extraction, passed, pending, failures)
    _check_duplicate_fixture(case, fixture_root, digest, passed, failures)
    for check in _string_set(case.get("required_checks")):
        status, detail = _evaluate_required_file_processing_check(
            check,
            case=case,
            extraction=extraction,
            chunks=chunks,
            warnings=parser_result.warnings,
            unsupported_reason=parser_result.unsupported_reason,
            segment_failures=segment_result.failures,
            duplicate_sha256=digest,
            fixture_root=fixture_root,
        )
        if status == "passed":
            passed.append(check)
        elif status == "pending":
            pending.append(f"{check}:{detail}")
        else:
            failures.append(f"{check}:{detail}")
    return FileProcessingContractCaseResult(
        case_id=case_id,
        fixture=fixture_name,
        parser_profile=source_profile.parser_profile,
        parser_backend=parser_result.parser_backend,
        chunk_template=effective_template,
        fallback_used=parser_result.fallback_used,
        page_coverage=(
            quality_report.page_coverage
            if quality_report is not None and _is_page_coverage_metric_case(case)
            else None
        ),
        low_confidence_count=(
            quality_report.low_confidence_count if quality_report is not None else 0
        ),
        failed_segment_count=failed_segment_count,
        passed_checks=tuple(_unique_sorted(passed)),
        pending_checks=tuple(_unique_sorted(pending)),
        failures=tuple(_unique_sorted(failures)),
    )


def _fixture_content_type(path: Path) -> str:
    content_type, _ = mimetypes.guess_type(path.name)
    return content_type or "application/octet-stream"


def _is_page_coverage_metric_case(case: Mapping[str, object]) -> bool:
    return bool(_int_set(case.get("expected_pages"))) or "page_coverage" in _string_set(
        case.get("required_checks")
    )


def _check_expected_parser_profile(
    case: Mapping[str, object],
    parser_profile: str,
    passed: list[str],
    failures: list[str],
) -> None:
    expected = _string_value(case.get("expected_parser_profile"))
    if not expected:
        return
    if parser_profile == expected:
        passed.append("expected_parser_profile")
        return
    failures.append(f"expected_parser_profile:{parser_profile}!={expected}")


def _check_expected_chunk_template(
    case: Mapping[str, object],
    chunk_template: str,
    passed: list[str],
    failures: list[str],
) -> None:
    expected = _string_value(case.get("expected_chunk_template"))
    if not expected:
        return
    if chunk_template == expected:
        passed.append("expected_chunk_template")
        return
    failures.append(f"expected_chunk_template:{chunk_template}!={expected}")


def _check_expected_content_kind(
    case: Mapping[str, object],
    extraction: StructuredExtraction | None,
    chunks: Sequence[Chunk],
    passed: list[str],
    pending: list[str],
    failures: list[str],
) -> None:
    expected = _string_value(case.get("expected_content_kind"))
    if not expected:
        return
    if extraction is None:
        pending.append("expected_content_kind:requires_enterprise_ai_extraction")
        return
    content_kinds = _content_kinds(extraction, chunks)
    if expected in content_kinds:
        passed.append("expected_content_kind")
        return
    failures.append(f"expected_content_kind:{expected}:not_found")


def _check_expected_answer(
    case: Mapping[str, object],
    extraction: StructuredExtraction | None,
    chunks: Sequence[Chunk],
    passed: list[str],
    pending: list[str],
    failures: list[str],
) -> None:
    expected = _string_value(case.get("expected_answer"))
    if not expected:
        return
    if extraction is None:
        pending.append("expected_answer:requires_extraction")
        return
    haystack = _normalized_extraction_and_chunk_text(extraction, chunks)
    if _normalize_answer(expected) in haystack:
        passed.append("expected_answer")
        return
    failures.append("expected_answer:not_found")


def _check_expected_warning(
    case: Mapping[str, object],
    warnings: Sequence[str],
    segment_failures: Sequence[object],
    passed: list[str],
    failures: list[str],
) -> None:
    expected = _string_value(case.get("expected_warning"))
    if not expected:
        return
    failure_codes = {
        _string_value(getattr(failure, "error_code", "")) for failure in segment_failures
    }
    if expected in set(warnings) or expected in failure_codes:
        passed.append("expected_warning")
        return
    failures.append(f"expected_warning:{expected}:not_found")


def _check_expected_unsupported_reason(
    case: Mapping[str, object],
    unsupported_reason: str | None,
    passed: list[str],
    failures: list[str],
) -> None:
    expected = _string_value(case.get("expected_unsupported_reason"))
    if not expected:
        return
    if unsupported_reason == expected:
        passed.append("expected_unsupported_reason")
        return
    failures.append(f"expected_unsupported_reason:{unsupported_reason or '<none>'}!={expected}")


def _check_expected_pages(
    case: Mapping[str, object],
    extraction: StructuredExtraction | None,
    passed: list[str],
    pending: list[str],
    failures: list[str],
) -> None:
    expected_pages = _int_set(case.get("expected_pages"))
    if not expected_pages:
        return
    if extraction is None:
        pending.append("expected_pages:requires_enterprise_ai_extraction")
        return
    actual_pages = {page.page_number for page in extraction.pages} | {
        element.page_number for element in extraction.elements if element.page_number is not None
    }
    if expected_pages <= actual_pages:
        passed.append("expected_pages")
        return
    missing_pages = ",".join(str(page) for page in sorted(expected_pages - actual_pages))
    failures.append(f"expected_pages:missing:{missing_pages}")


def _check_duplicate_fixture(
    case: Mapping[str, object],
    fixture_root: Path,
    digest: str,
    passed: list[str],
    failures: list[str],
) -> None:
    duplicate_fixture = _string_value(case.get("duplicate_fixture"))
    if not duplicate_fixture:
        return
    duplicate_digest = hashlib.sha256((fixture_root / duplicate_fixture).read_bytes()).hexdigest()
    if duplicate_digest == digest:
        passed.append("duplicate_fixture_sha256")
        return
    failures.append("duplicate_fixture_sha256:mismatch")


def _evaluate_required_file_processing_check(
    check: str,
    *,
    case: Mapping[str, object],
    extraction: StructuredExtraction | None,
    chunks: Sequence[Chunk],
    warnings: Sequence[str],
    unsupported_reason: str | None,
    segment_failures: Sequence[object],
    duplicate_sha256: str,
    fixture_root: Path,
) -> tuple[str, str]:
    if check in _STAGING_ONLY_CHECKS:
        return "pending", "requires_staging_pipeline"
    if check in {"ocr_text", "reading_order"}:
        return _check_non_empty_extraction(extraction, requires="enterprise_ai")
    if check == "page_coverage":
        expected_pages = _int_set(case.get("expected_pages"))
        if extraction is None:
            return "pending", "requires_enterprise_ai_extraction"
        actual_pages = {page.page_number for page in extraction.pages}
        return ("passed", "ok") if expected_pages <= actual_pages else ("failure", "missing_pages")
    if check == "citation_traceability":
        return _check_chunk_traceability(extraction, chunks)
    if check == "bbox_citation":
        return _check_bbox_citation(chunks)
    if check == "preview_jump":
        return _check_bbox_citation(chunks, pending_reason="requires_preview_bbox")
    if check == "table_preserve_rows":
        return _check_table_preserve_rows(chunks)
    if check == "table_qa_accuracy":
        return _check_expected_answer_as_required(case, extraction, chunks)
    if check == "code_block":
        return _check_content_kind_present(
            extraction,
            chunks,
            expected_kind="code",
            required_metadata_key="code_language",
        )
    if check == "equation_block":
        return _check_content_kind_present(
            extraction,
            chunks,
            expected_kind="equation",
            required_metadata_key="equation_delimiter",
        )
    if check == "element_lineage":
        return _check_element_lineage(extraction, chunks)
    if check == "heading_structure":
        return _check_heading_structure(extraction, chunks)
    if check == "paragraph_order":
        return _check_paragraph_order(extraction)
    if check == "slide_segment":
        return _check_segment_success(segment_failures, extraction, expected_kind="slide")
    if check == "sheet_segment":
        return _check_segment_success(segment_failures, extraction, expected_kind="sheet")
    if check == "section_path":
        return _check_section_path(extraction, chunks)
    if check == "email_headers":
        return _check_email_headers(extraction)
    if check == "thread_body":
        return _check_thread_body(extraction)
    if check == "attachment_metadata":
        return _check_attachment_metadata(extraction)
    if check == "canonical_alias":
        duplicate_fixture = _string_value(case.get("duplicate_fixture"))
        if not duplicate_fixture:
            return "failure", "duplicate_fixture_missing"
        duplicate_digest = hashlib.sha256(
            (fixture_root / duplicate_fixture).read_bytes()
        ).hexdigest()
        if duplicate_digest == duplicate_sha256:
            return "passed", "ok"
        return "failure", "sha_mismatch"
    if check == "failed_segment_status":
        return ("passed", "ok") if segment_failures else ("failure", "segment_failure_missing")
    if check == "unsupported_reason":
        expected = _string_value(case.get("expected_unsupported_reason"))
        if not expected:
            return "failure", "expected_unsupported_reason_missing"
        if unsupported_reason == expected:
            return "passed", "ok"
        return "failure", "reason_mismatch"
    if check == "safe_error":
        if warnings or segment_failures:
            return "passed", "ok"
        return "failure", "safe_warning_missing"
    return "failure", "unknown_required_check"


def _check_non_empty_extraction(
    extraction: StructuredExtraction | None,
    *,
    requires: str,
) -> tuple[str, str]:
    if extraction is None:
        return "pending", f"requires_{requires}"
    return ("passed", "ok") if extraction.raw_text.strip() else ("failure", "empty_extraction")


def _check_chunk_traceability(
    extraction: StructuredExtraction | None,
    chunks: Sequence[Chunk],
) -> tuple[str, str]:
    if not chunks:
        return "pending", "requires_extraction"
    if any(
        _chunk_has_resolvable_element_lineage(chunk, extraction)
        and chunk.metadata.get("page_start") is not None
        and chunk.metadata.get("chunk_group_id")
        for chunk in chunks
    ):
        return "passed", "ok"
    return "failure", "lineage_metadata_missing"


def _check_bbox_citation(
    chunks: Sequence[Chunk],
    *,
    pending_reason: str = "requires_enterprise_ai_bbox",
) -> tuple[str, str]:
    if not chunks:
        return "pending", pending_reason
    if any(chunk.metadata.get("bbox") for chunk in chunks):
        return "passed", "ok"
    return "pending", pending_reason


def _check_table_preserve_rows(chunks: Sequence[Chunk]) -> tuple[str, str]:
    table_chunks = [
        chunk
        for chunk in chunks
        if chunk.metadata.get("content_kind") == "table"
        and chunk.metadata.get("chunk_template") == "table_preserve_rows"
    ]
    if not table_chunks:
        return "failure", "table_preserve_rows_chunk_missing"
    if not all(_string_value(chunk.metadata.get("table_id")) for chunk in table_chunks):
        return "failure", "table_lineage_metadata_missing"
    if not all(
        _positive_int(chunk.metadata.get("table_row_count")) > 0
        and _positive_int(chunk.metadata.get("table_column_count")) > 0
        for chunk in table_chunks
    ):
        return "failure", "table_shape_metadata_missing"
    split_chunks = [
        chunk
        for chunk in table_chunks
        if _optional_int(chunk.metadata.get("chunk_part_count")) is not None
        and (_optional_int(chunk.metadata.get("chunk_part_count")) or 0) > 1
    ]
    if not split_chunks:
        return "passed", "ok"
    if not all(
        _optional_int(chunk.metadata.get("table_data_row_start")) is not None
        and _optional_int(chunk.metadata.get("table_data_row_end")) is not None
        for chunk in split_chunks
    ):
        return "failure", "table_row_group_metadata_missing"
    ordered = sorted(
        split_chunks,
        key=lambda chunk: (
            _optional_int(chunk.metadata.get("chunk_part_index")) or 0,
            chunk.index,
        ),
    )
    if any(
        _optional_int(chunk.metadata.get("chunk_part_index")) is None
        for chunk in ordered
    ):
        return "failure", "table_part_index_missing"
    first_header = _table_header_signature(ordered[0].text)
    if not first_header:
        return "failure", "table_header_missing"
    for chunk in ordered[1:]:
        if chunk.metadata.get("table_header_repeated") is not True:
            return "failure", "table_header_repeat_metadata_missing"
        if _table_header_signature(chunk.text) != first_header:
            return "failure", "table_header_not_repeated"
    if any(
        (_optional_int(chunk.metadata.get("table_data_row_start")) or 0)
        > (_optional_int(chunk.metadata.get("table_data_row_end")) or 0)
        for chunk in split_chunks
    ):
        return "failure", "table_row_group_range_invalid"
    return "passed", "ok"


def _table_header_signature(text: str) -> tuple[str, ...]:
    """表 chunk 先頭の header 行を比較用に正規化する。"""
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    if not lines or not _looks_like_table_row(lines[0]):
        return ()
    if len(lines) >= 2 and _looks_like_markdown_separator(lines[1]):
        return (lines[0], lines[1])
    return (lines[0],)


def _looks_like_table_row(line: str) -> bool:
    return line.startswith("|") and line.endswith("|") and line.count("|") >= 2


def _looks_like_markdown_separator(line: str) -> bool:
    if not _looks_like_table_row(line):
        return False
    cells = [cell.strip() for cell in line.strip("|").split("|")]
    return bool(cells) and all(re.fullmatch(r":?-{3,}:?", cell or "") for cell in cells)


def _check_expected_answer_as_required(
    case: Mapping[str, object],
    extraction: StructuredExtraction | None,
    chunks: Sequence[Chunk],
) -> tuple[str, str]:
    expected = _string_value(case.get("expected_answer"))
    if not expected:
        return "failure", "expected_answer_missing"
    if extraction is None:
        return "pending", "requires_extraction"
    haystack = _normalized_extraction_and_chunk_text(extraction, chunks)
    if _normalize_answer(expected) in haystack:
        return "passed", "ok"
    return "failure", "answer_missing"


def _check_content_kind_present(
    extraction: StructuredExtraction | None,
    chunks: Sequence[Chunk],
    *,
    expected_kind: str,
    required_metadata_key: str | None = None,
) -> tuple[str, str]:
    if extraction is None:
        return "pending", "requires_extraction"
    content_kinds = _content_kinds(extraction, chunks)
    if expected_kind not in content_kinds:
        return "failure", f"{expected_kind}_missing"
    if required_metadata_key is None:
        return "passed", "ok"
    if any(
        chunk.metadata.get("content_kind") == expected_kind
        and _string_value(chunk.metadata.get(required_metadata_key))
        for chunk in chunks
    ):
        return "passed", "ok"
    return "failure", f"{expected_kind}_{required_metadata_key}_missing"


def _check_element_lineage(
    extraction: StructuredExtraction | None,
    chunks: Sequence[Chunk],
) -> tuple[str, str]:
    if not chunks:
        return "pending", "requires_extraction"
    if any(_chunk_has_resolvable_element_lineage(chunk, extraction) for chunk in chunks):
        return "passed", "ok"
    if any(_chunk_element_ids(chunk) for chunk in chunks):
        return "failure", "element_ids_unresolved"
    return "failure", "element_ids_missing"


def _chunk_has_resolvable_element_lineage(
    chunk: Chunk,
    extraction: StructuredExtraction | None,
) -> bool:
    chunk_element_ids = _chunk_element_ids(chunk)
    if not chunk_element_ids:
        return False
    extraction_element_ids = _structured_extraction_element_ids(extraction)
    return bool(extraction_element_ids and (set(chunk_element_ids) & extraction_element_ids))


def _chunk_element_ids(chunk: Chunk) -> tuple[str, ...]:
    return _id_tuple(chunk.metadata.get("element_ids"))


def _structured_extraction_element_ids(
    extraction: StructuredExtraction | None,
) -> set[str]:
    if extraction is None:
        return set()
    element_ids = {
        element.element_id
        for element in extraction.elements
        if element.element_id is not None and element.element_id.strip()
    }
    for page in extraction.pages:
        element_ids.update(element_id for element_id in page.element_ids if element_id.strip())
    return element_ids


def _check_heading_structure(
    extraction: StructuredExtraction | None,
    chunks: Sequence[Chunk],
) -> tuple[str, str]:
    if extraction is None:
        return "pending", "requires_extraction"
    if any(element.kind == "title" for element in extraction.elements):
        return "passed", "ok"
    if any(chunk.metadata.get("section_title") for chunk in chunks):
        return "passed", "ok"
    return "failure", "heading_missing"


def _check_paragraph_order(extraction: StructuredExtraction | None) -> tuple[str, str]:
    if extraction is None:
        return "pending", "requires_extraction"
    orders = [element.order for element in extraction.elements]
    if orders == sorted(orders):
        return "passed", "ok"
    return "failure", "element_order_not_monotonic"


def _check_segment_success(
    segment_failures: Sequence[object],
    extraction: StructuredExtraction | None,
    *,
    expected_kind: str,
) -> tuple[str, str]:
    if extraction is None:
        return "pending", "requires_extraction"
    if segment_failures:
        return "failure", "unexpected_segment_failure"
    content_kinds = _content_kinds(extraction, [])
    if expected_kind in content_kinds:
        return "passed", "ok"
    return "failure", f"{expected_kind}_missing"


def _check_section_path(
    extraction: StructuredExtraction | None,
    chunks: Sequence[Chunk],
) -> tuple[str, str]:
    if extraction is None:
        return "pending", "requires_extraction"
    if any(element.section_path for element in extraction.elements):
        return "passed", "ok"
    if any(chunk.metadata.get("section_path") for chunk in chunks):
        return "passed", "ok"
    return "failure", "section_path_missing"


def _check_email_headers(extraction: StructuredExtraction | None) -> tuple[str, str]:
    if extraction is None:
        return "pending", "requires_extraction"
    raw_text = extraction.raw_text
    if all(label in raw_text for label in ("Subject:", "From:", "To:")):
        return "passed", "ok"
    return "failure", "email_headers_missing"


def _check_thread_body(extraction: StructuredExtraction | None) -> tuple[str, str]:
    if extraction is None:
        return "pending", "requires_extraction"
    return ("passed", "ok") if "承認" in extraction.raw_text else ("failure", "thread_body_missing")


def _check_attachment_metadata(
    extraction: StructuredExtraction | None,
) -> tuple[str, str]:
    if extraction is None:
        return "pending", "requires_extraction"
    if "attachment_count" in extraction.parser_artifacts:
        return "passed", "ok"
    return "failure", "attachment_count_missing"


def _content_kinds(extraction: StructuredExtraction, chunks: Sequence[Chunk]) -> set[str]:
    kinds: set[str] = set()
    chunk_template = extraction.parser_artifacts.get("chunk_template")
    if chunk_template == "office_sheet":
        kinds.add("sheet")
    elif chunk_template == "office_slide":
        kinds.add("slide")
    for element in extraction.elements:
        if element.content_kind:
            kinds.add(element.content_kind)
        if element.kind == "table":
            kinds.add("table")
        elif element.kind in {"list", "equation", "code"}:
            kinds.add(element.kind)
        elif element.kind in {"text", "title"}:
            kinds.add("text")
    for chunk in chunks:
        content_kind = chunk.metadata.get("content_kind")
        if isinstance(content_kind, str) and content_kind:
            kinds.add(content_kind)
    return kinds


def _normalized_extraction_and_chunk_text(
    extraction: StructuredExtraction,
    chunks: Sequence[Chunk],
) -> str:
    return _normalize_answer(extraction.raw_text + "\n" + "\n".join(chunk.text for chunk in chunks))


def _int_set(value: object) -> set[int]:
    if not _is_sequence(value):
        return set()
    values: set[int] = set()
    for item in value:
        number = _optional_int(item)
        if number is not None:
            values.add(number)
    return values


def _unique_sorted(values: Sequence[str]) -> list[str]:
    return sorted(set(values))


def _split_pending_check(value: str) -> tuple[str, str]:
    check, separator, reason = value.partition(":")
    return check, reason if separator else "pending"


def _staging_required_evidence(check: str, reason: str) -> tuple[str, ...]:
    if check in {"ocr_text", "reading_order", "page_coverage"} or reason.startswith(
        "requires_enterprise_ai"
    ):
        return (
            "OCI Enterprise AI extraction completed for fixture",
            "document status is INDEXED",
            "extraction pages/elements are stored without raw text leakage in artifacts",
            "chunk citation metadata includes document_id/chunk_id/page/element lineage",
        )
    if check in {"bbox_citation", "preview_jump"} or reason == "requires_preview_bbox":
        return (
            "GET /api/documents/{document_id}/chunks returns bbox metadata",
            "GET /api/documents/{document_id} preview payload keeps page numbers",
            "DocumentPreviewWorkspace can jump from citation/chunk to page bbox",
        )
    if check == "page_hit_accuracy":
        return (
            "evaluation case retrieves the expected document",
            "top citation overlaps expected page range",
            "page_hit_accuracy threshold passes in staging report",
        )
    if check in {"knowledge_base_membership", "searchable_canonical"}:
        return (
            "canonical document is linked to target knowledge base",
            "duplicate upload records duplicate/alias metadata",
            "search scoped to target knowledge base returns canonical chunks",
        )
    if check == "artifact_reuse":
        return (
            "successful segment extraction artifact is stored in Object Storage",
            "retry only reprocesses failed segment ids",
            "ingestion-segments API shows successful segment artifact path retained",
        )
    return ("staging evidence is required for this pending check",)


def _staging_suggested_gate(check: str, reason: str) -> str:
    if check in {"knowledge_base_membership", "searchable_canonical"}:
        return "duplicate_kb_membership_gate"
    if check in {"bbox_citation", "preview_jump"} or reason == "requires_preview_bbox":
        return "preview_bbox_citation_gate"
    if check == "artifact_reuse":
        return "segment_artifact_reuse_gate"
    if check == "page_hit_accuracy":
        return "file_processing_page_hit_gate"
    if reason.startswith("requires_enterprise_ai"):
        return "enterprise_ai_file_extraction_gate"
    return "file_processing_staging_gate"


def parser_fallback_rate(extractions: Sequence[Mapping[str, object]]) -> float:
    """extraction payload 群から parser fallback 率を返す。"""
    if not extractions:
        return 0.0
    fallback_count = 0
    for extraction in extractions:
        quality = _mapping(extraction.get("quality_report"))
        artifacts = _mapping(extraction.get("parser_artifacts"))
        if bool(quality.get("fallback_used")) or bool(artifacts.get("fallback_used")):
            fallback_count += 1
    return fallback_count / len(extractions)


def extraction_page_coverage(extractions: Sequence[Mapping[str, object]]) -> float:
    """quality_report の page coverage 平均を返す。"""
    coverages: list[float] = []
    for extraction in extractions:
        quality = _mapping(extraction.get("quality_report"))
        value = _optional_float(quality.get("page_coverage"))
        if value is not None:
            coverages.append(value)
    if not coverages:
        return 0.0
    return sum(coverages) / len(coverages)


def low_confidence_document_rate(extractions: Sequence[Mapping[str, object]]) -> float:
    """低信頼 element を含む extraction の割合を返す。"""
    if not extractions:
        return 0.0
    low_confidence_count = 0
    for extraction in extractions:
        quality = _mapping(extraction.get("quality_report"))
        warnings = quality.get("quality_warnings")
        warning_items = warnings if isinstance(warnings, list) else []
        if _positive_int(quality.get("low_confidence_count")) > 0 or any(
            str(warning) in {"low_confidence_elements", "low_extraction_confidence"}
            for warning in warning_items
        ):
            low_confidence_count += 1
    return low_confidence_count / len(extractions)


def failed_segment_rate(extractions: Sequence[Mapping[str, object]]) -> float:
    """失敗 segment を持つ extraction の割合を返す。"""
    if not extractions:
        return 0.0
    failed_count = 0
    for extraction in extractions:
        quality = _mapping(extraction.get("quality_report"))
        artifacts = _mapping(extraction.get("parser_artifacts"))
        if (
            _positive_int(quality.get("failed_segment_count")) > 0
            or _positive_int(artifacts.get("failed_segment_count")) > 0
            or _positive_int(artifacts.get("failed_segments")) > 0
        ):
            failed_count += 1
    return failed_count / len(extractions)


def table_qa_accuracy(results: Sequence[TableQaResult]) -> float:
    """表 QA の実回答が期待値を含む割合を返す。"""
    if not results:
        return 0.0
    hits = 0
    for result in results:
        expected = _normalize_answer(result.expected_answer)
        actual = _normalize_answer(result.actual_answer)
        if expected and expected in actual:
            hits += 1
    return hits / len(results)


def page_hit_accuracy(
    cases: Sequence[PageHitCase],
    retrieved_by_case: Mapping[str, Sequence[RetrievedChunk | Mapping[str, object]]],
) -> float:
    """期待 document/page range に citation が命中した割合を返す。"""
    if not cases:
        return 0.0
    hits = 0
    for case in cases:
        retrieved = retrieved_by_case.get(case.case_id, [])
        if any(_retrieved_hits_expected_page(item, case) for item in retrieved):
            hits += 1
    return hits / len(cases)


def citation_traceability_coverage(
    citations: Sequence[RetrievedChunk | Mapping[str, object]],
) -> float:
    """citation が document/chunk/page/element lineage を持つ割合を返す。"""
    if not citations:
        return 0.0
    traceable = sum(1 for citation in citations if _has_traceable_citation_metadata(citation))
    return traceable / len(citations)


def bbox_citation_coverage(
    citations: Sequence[RetrievedChunk | Mapping[str, object]],
) -> float:
    """citation が preview へ位置決め可能な bbox を持つ割合を返す。"""
    if not citations:
        return 0.0
    with_bbox = sum(1 for citation in citations if _citation_bbox(citation) is not None)
    return with_bbox / len(citations)


def element_lineage_coverage(
    citations: Sequence[RetrievedChunk | Mapping[str, object]],
) -> float:
    """citation が element_ids を保持している割合を返す。"""
    if not citations:
        return 0.0
    with_lineage = sum(1 for citation in citations if _citation_element_ids(citation))
    return with_lineage / len(citations)


def _retrieved_hits_expected_page(
    item: RetrievedChunk | Mapping[str, object],
    case: PageHitCase,
) -> bool:
    document_id = (
        item.document_id if isinstance(item, RetrievedChunk) else str(item.get("document_id"))
    )
    if document_id != case.expected_document_id:
        return False
    metadata = _citation_metadata(item)
    page_start = _optional_int(_citation_value(item, metadata, "page_start"))
    page_end = _optional_int(_citation_value(item, metadata, "page_end")) or page_start
    if page_start is None or page_end is None:
        return False
    retrieved_pages = set(range(page_start, page_end + 1))
    return bool(retrieved_pages & set(case.expected_pages))


def _has_traceable_citation_metadata(
    item: RetrievedChunk | Mapping[str, object],
) -> bool:
    document_id = _citation_document_id(item)
    chunk_id = _citation_chunk_id(item)
    metadata = _citation_metadata(item)
    page_start = _optional_int(_citation_value(item, metadata, "page_start"))
    page_end = _optional_int(_citation_value(item, metadata, "page_end")) or page_start
    if not document_id or not chunk_id or page_start is None or page_end is None:
        return False
    return bool(
        _citation_element_ids(item)
        or _citation_bbox(item) is not None
        or _string_value(_citation_value(item, metadata, "section_path"))
    )


def _citation_document_id(item: RetrievedChunk | Mapping[str, object]) -> str:
    if isinstance(item, RetrievedChunk):
        return item.document_id
    value = item.get("document_id")
    return str(value).strip() if value is not None else ""


def _citation_chunk_id(item: RetrievedChunk | Mapping[str, object]) -> str:
    if isinstance(item, RetrievedChunk):
        return item.chunk_id
    value = item.get("chunk_id")
    return str(value).strip() if value is not None else ""


def _citation_metadata(
    item: RetrievedChunk | Mapping[str, object],
) -> Mapping[str, object]:
    if isinstance(item, RetrievedChunk):
        return item.metadata
    return _mapping(item.get("metadata"))


def _citation_value(
    item: RetrievedChunk | Mapping[str, object],
    metadata: Mapping[str, object],
    key: str,
) -> object:
    if not isinstance(item, RetrievedChunk) and key in item:
        return item[key]
    return metadata.get(key)


def _citation_element_ids(
    item: RetrievedChunk | Mapping[str, object],
) -> tuple[str, ...]:
    if not isinstance(item, RetrievedChunk) and "element_ids" in item:
        return _id_tuple(item.get("element_ids"))
    return _id_tuple(_citation_metadata(item).get("element_ids"))


def _citation_bbox(
    item: RetrievedChunk | Mapping[str, object],
) -> tuple[float, ...] | None:
    if not isinstance(item, RetrievedChunk) and "bbox" in item:
        return _bbox_tuple(item.get("bbox"))
    return _bbox_tuple(_citation_metadata(item).get("bbox"))


def _mapping(value: object) -> Mapping[str, object]:
    return value if isinstance(value, Mapping) else {}


def _is_sequence(value: object) -> TypeGuard[Sequence[object]]:
    return isinstance(value, Sequence) and not isinstance(value, str | bytes | bytearray)


def _string_set(value: object) -> set[str]:
    if not _is_sequence(value):
        return set()
    return {item.strip() for item in value if isinstance(item, str) and item.strip()}


def _missing_required_case_fields(case: Mapping[str, object]) -> tuple[str, ...]:
    return tuple(
        sorted(
            field
            for field in _REQUIRED_CASE_FIELDS
            if not _has_non_empty_manifest_field(case, field)
        )
    )


def _has_non_empty_manifest_field(case: Mapping[str, object], field: str) -> bool:
    value = case.get(field)
    if field == "required_checks":
        return bool(_string_set(value))
    if isinstance(value, str):
        return bool(value.strip())
    return value is not None


def _resolve_fixture_root(manifest: Mapping[str, object], *, manifest_path: Path) -> Path:
    root = _string_value(manifest.get("fixture_root")) or "."
    return (manifest_path.parent / root).resolve()


def _validate_fixture_extension(
    case: Mapping[str, object],
    *,
    case_id: str,
    fixture_relative: Path,
    errors: list[str],
) -> None:
    modality = _string_value(case.get("modality"))
    expected_extensions = _FIXTURE_EXTENSIONS_BY_MODALITY.get(modality)
    if expected_extensions is None:
        return
    extension = fixture_relative.suffix.casefold()
    if extension not in expected_extensions:
        errors.append(
            f"case[{case_id}]:fixture_extension_mismatch:" f"{modality}:{extension or '<none>'}"
        )


def _validate_fixture_reference(
    case: Mapping[str, object],
    *,
    field: str,
    fixture_root: Path,
    case_id: str,
    errors: list[str],
) -> None:
    fixture_name = _string_value(case.get(field))
    if not fixture_name:
        errors.append(f"case[{case_id}]:{field}_missing_name")
        return
    fixture_relative = Path(fixture_name)
    if fixture_relative.is_absolute() or ".." in fixture_relative.parts:
        errors.append(f"case[{case_id}]:{field}_unsafe_path:{fixture_name}")
        return
    _validate_fixture_extension(
        case,
        case_id=case_id,
        fixture_relative=fixture_relative,
        errors=errors,
    )
    fixture_path = fixture_root / fixture_relative
    try:
        stat = fixture_path.stat()
    except FileNotFoundError:
        errors.append(f"case[{case_id}]:{field}_not_found:{fixture_name}")
        return
    if not fixture_path.is_file():
        errors.append(f"case[{case_id}]:{field}_not_file:{fixture_name}")
        return
    if stat.st_size <= 0:
        errors.append(f"case[{case_id}]:{field}_empty:{fixture_name}")


def _has_case_assertion(case: Mapping[str, object], field: str) -> bool:
    value = case.get(field)
    if isinstance(value, str):
        return bool(value.strip())
    if _is_sequence(value):
        return bool(value)
    return value is not None


def _id_tuple(value: object) -> tuple[str, ...]:
    if value is None:
        return ()
    if isinstance(value, str):
        cleaned = value.strip()
        if cleaned.startswith("["):
            try:
                decoded = json.loads(cleaned)
            except json.JSONDecodeError:
                decoded = None
            if isinstance(decoded, list):
                return tuple(
                    str(item).strip()
                    for item in decoded
                    if isinstance(item, str | int) and str(item).strip()
                )
        cleaned = cleaned.replace("[", "").replace("]", "").replace("'", "").replace('"', "")
        return tuple(item.strip() for item in cleaned.split(",") if item.strip())
    if isinstance(value, Sequence) and not isinstance(value, bytes | bytearray):
        return tuple(
            str(item).strip()
            for item in value
            if isinstance(item, str | int) and str(item).strip()
        )
    return ()


def _bbox_tuple(value: object) -> tuple[float, ...] | None:
    if value is None:
        return None
    raw_value = value
    if isinstance(value, str):
        try:
            raw_value = json.loads(value)
        except json.JSONDecodeError:
            return None
    if not isinstance(raw_value, Sequence) or isinstance(raw_value, bytes | bytearray | str):
        return None
    values: list[float] = []
    for item in raw_value:
        number = _optional_float(item)
        if number is None:
            return None
        values.append(number)
    if len(values) < 4:
        return None
    bbox = tuple(values[:4])
    return bbox if _bbox_has_positive_area(bbox) else None


def _bbox_has_positive_area(bbox: Sequence[float]) -> bool:
    x, y, right_or_width, bottom_or_height = bbox[:4]
    if not all(math.isfinite(value) for value in (x, y, right_or_width, bottom_or_height)):
        return False
    if x < 0 or y < 0:
        return False
    return not (right_or_width <= 0 or bottom_or_height <= 0)


def _optional_int(value: object) -> int | None:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float) and value.is_integer():
        return int(value)
    if isinstance(value, str) and value.strip().isdigit():
        return int(value.strip())
    return None


def _positive_int(value: object) -> int:
    parsed = _optional_int(value)
    return parsed if parsed is not None and parsed > 0 else 0


def _optional_float(value: object) -> float | None:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, int | float):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value.strip())
        except ValueError:
            return None
    return None


def _threshold_value(value: object, direction: str) -> float | None:
    config = _mapping(value)
    return _optional_float(config.get(direction))


def _string_value(value: object) -> str:
    return value.strip() if isinstance(value, str) else ""


def _normalize_answer(value: str) -> str:
    return "".join(value.casefold().split())
