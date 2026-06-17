"""file-processing golden set の local contract gate CLI。"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Mapping, Sequence
from dataclasses import asdict
from pathlib import Path
from typing import Any

from app.rag.file_processing_evaluation import (
    FileProcessingContractReport,
    FileProcessingMetricThresholdResult,
    build_file_processing_staging_plan,
    evaluate_file_processing_metric_thresholds,
    run_file_processing_contract_checks,
)


def main(argv: Sequence[str] | None = None) -> int:
    """CLI entrypoint。"""
    parser = argparse.ArgumentParser(
        prog="rag-file-processing-golden",
        description=(
            "file-processing golden manifest の fixture / parser / chunk contract を"
            "ローカル実行します。"
        ),
    )
    parser.add_argument("manifest", type=Path, help="file-processing golden manifest JSON")
    parser.add_argument("--output", type=Path, help="結果 JSON の保存先。未指定なら stdout。")
    parser.add_argument(
        "--fail-on-pending",
        action="store_true",
        help="OCI staging が必要な pending check が残っている場合も失敗にします。",
    )
    parser.add_argument(
        "--github-annotations",
        action="store_true",
        help="GitHub Actions log annotation 用の非機密サマリを stdout に出力します。",
    )
    args = parser.parse_args(argv)

    try:
        manifest = _load_manifest(args.manifest)
        report = run_file_processing_contract_checks(manifest, manifest_path=args.manifest)
        payload = _report_payload(report, manifest=manifest)
        _write_payload(payload, args.output)
        if args.github_annotations:
            _emit_github_annotations(payload)
    except FileProcessingGoldenCliError as exc:
        print(f"file-processing golden エラー: {exc}", file=sys.stderr)
        return exc.exit_code

    if not report.passed:
        return 1
    if any(not result["passed"] for result in payload["threshold_results"]):
        return 1
    if args.fail_on_pending and report.pending_staging_check_count > 0:
        return 1
    return 0


class FileProcessingGoldenCliError(RuntimeError):
    """CLI 利用者へ返す安全なエラー。"""

    def __init__(self, message: str, exit_code: int = 2) -> None:
        super().__init__(message)
        self.exit_code = exit_code


def _load_manifest(path: Path) -> dict[str, Any]:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise FileProcessingGoldenCliError(f"manifest が見つかりません: {path}") from exc
    except json.JSONDecodeError as exc:
        raise FileProcessingGoldenCliError(
            f"manifest が JSON として読めません: line={exc.lineno}, column={exc.colno}"
        ) from exc
    if not isinstance(raw, dict):
        raise FileProcessingGoldenCliError("manifest root は JSON object にしてください。")
    return raw


def _report_payload(
    report: FileProcessingContractReport,
    *,
    manifest: dict[str, Any],
) -> dict[str, Any]:
    payload = asdict(report)
    metric_summary = _metric_summary(report)
    threshold_results = evaluate_file_processing_metric_thresholds(
        metric_summary,
        _mapping(manifest.get("thresholds")),
    )
    staging_requirements = build_file_processing_staging_plan(manifest, report)
    promotion_blockers = _promotion_blockers(report, threshold_results, manifest=manifest)
    payload.update(
        {
            "passed": report.passed,
            "promotion_ready": not promotion_blockers,
            "promotion_blockers": promotion_blockers,
            "case_count": report.case_count,
            "failure_count": report.failure_count,
            "pending_staging_check_count": report.pending_staging_check_count,
            "staging_policy": _staging_policy(manifest),
            "metric_summary": metric_summary,
            "threshold_results": [asdict(result) for result in threshold_results],
            "staging_requirements": [asdict(requirement) for requirement in staging_requirements],
        }
    )
    return payload


def _promotion_blockers(
    report: FileProcessingContractReport,
    threshold_results: Sequence[FileProcessingMetricThresholdResult],
    *,
    manifest: Mapping[str, Any],
) -> list[dict[str, Any]]:
    """本番昇格前に閉じるべき blocker を機械可読に返す。"""
    blockers: list[dict[str, Any]] = []
    block_pending_staging = _block_pending_staging_for_promotion(manifest)
    if not report.passed:
        blockers.append(
            {
                "code": "local_contract_failed",
                "count": report.failure_count,
            }
        )
    if block_pending_staging and report.pending_staging_check_count:
        blockers.append(
            {
                "code": "pending_staging_checks",
                "count": report.pending_staging_check_count,
            }
        )
    for threshold in threshold_results:
        status = getattr(threshold, "status", None)
        if status == "pending" and not block_pending_staging:
            continue
        if status not in {"failed", "pending"}:
            continue
        blockers.append(
            {
                "code": f"threshold_{status}",
                "metric": getattr(threshold, "metric", ""),
                "reason": getattr(threshold, "reason", None),
            }
        )
    return blockers


def _block_pending_staging_for_promotion(manifest: Mapping[str, Any]) -> bool:
    """manifest の staging policy から pending staging を promotion blocker にするか決める。"""
    policy = _staging_policy(manifest)
    return bool(policy["required_for_promotion"] or policy["pending_checks_block_promotion"])


def _staging_policy(manifest: Mapping[str, Any]) -> dict[str, Any]:
    raw_policy = _mapping(manifest.get("staging_policy"))
    return {
        "required_for_promotion": bool(raw_policy.get("required_for_promotion", False)),
        "pending_checks_block_promotion": bool(
            raw_policy.get("pending_checks_block_promotion", False)
        ),
        "required_runtime_checks": _string_list(raw_policy.get("required_runtime_checks")),
    }


def _metric_summary(report: FileProcessingContractReport) -> dict[str, Any]:
    return {
        "retrieval_recall": _requires_staging_metric(
            sample_count=report.pending_staging_check_count
        ),
        "parser_fallback_rate": {
            "status": "measured",
            "value": _safe_ratio(
                sum(1 for result in report.case_results if result.fallback_used),
                report.case_count,
            ),
            "sample_count": report.case_count,
        },
        "extraction_page_coverage": _page_coverage_metric_summary(report),
        "low_confidence_document_rate": _case_rate_metric_summary(
            report,
            numerator=sum(1 for result in report.case_results if result.low_confidence_count > 0),
        ),
        "failed_segment_rate": _case_rate_metric_summary(
            report,
            numerator=sum(1 for result in report.case_results if result.failed_segment_count > 0),
        ),
        "table_qa_accuracy": _check_metric_summary(report, "table_qa_accuracy"),
        "page_hit_accuracy": _check_metric_summary(report, "page_hit_accuracy"),
        "citation_traceability_coverage": _check_metric_summary(
            report,
            "citation_traceability",
        ),
        "bbox_citation_coverage": _check_metric_summary(report, "bbox_citation"),
        "preview_addressability_coverage": _check_metric_summary(report, "preview_jump"),
        "element_lineage_coverage": _check_metric_summary(report, "element_lineage"),
        "groundedness": _requires_staging_metric(sample_count=report.pending_staging_check_count),
        "ingestion_p95_ms": _requires_staging_metric(sample_count=report.case_count),
    }


def _page_coverage_metric_summary(report: FileProcessingContractReport) -> dict[str, Any]:
    coverages = [
        result.page_coverage
        for result in report.case_results
        if result.page_coverage is not None
    ]
    measured_count = len(coverages)
    status = "measured"
    if measured_count and measured_count < report.case_count:
        status = "partial"
    elif not measured_count:
        status = "requires_staging"
    return {
        "status": status,
        "value": round(sum(coverages) / measured_count, 4) if measured_count else None,
        "sample_count": report.case_count,
        "measured_count": measured_count,
        "pending_count": max(0, report.case_count - measured_count),
    }


def _case_rate_metric_summary(
    report: FileProcessingContractReport,
    *,
    numerator: int,
) -> dict[str, Any]:
    return {
        "status": "measured",
        "value": _safe_ratio(numerator, report.case_count),
        "sample_count": report.case_count,
        "measured_count": report.case_count,
        "failed_count": numerator,
    }


def _check_metric_summary(
    report: FileProcessingContractReport,
    check: str,
) -> dict[str, Any]:
    passed_count = 0
    failed_count = 0
    pending_count = 0
    for result in report.case_results:
        if check in result.passed_checks:
            passed_count += 1
        if any(failure.startswith(f"{check}:") for failure in result.failures):
            failed_count += 1
        if any(pending.startswith(f"{check}:") for pending in result.pending_checks):
            pending_count += 1
    measured_count = passed_count + failed_count
    sample_count = measured_count + pending_count
    status = "measured"
    if pending_count and measured_count:
        status = "partial"
    elif pending_count:
        status = "requires_staging"
    return {
        "status": status,
        "value": _safe_ratio(passed_count, measured_count) if measured_count else None,
        "sample_count": sample_count,
        "measured_count": measured_count,
        "pending_count": pending_count,
        "failed_count": failed_count,
    }


def _requires_staging_metric(*, sample_count: int) -> dict[str, Any]:
    return {
        "status": "requires_staging",
        "value": None,
        "sample_count": sample_count,
        "measured_count": 0,
        "pending_count": sample_count,
        "failed_count": 0,
    }


def _safe_ratio(numerator: int, denominator: int) -> float:
    if denominator <= 0:
        return 0.0
    return numerator / denominator


def _mapping(value: object) -> dict[str, object]:
    return value if isinstance(value, dict) else {}


def _string_list(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, str)]


def _emit_github_annotations(payload: Mapping[str, Any]) -> None:
    """CI log に promotion status だけを非機密に出す。"""
    promotion_ready = bool(payload.get("promotion_ready"))
    passed = bool(payload.get("passed"))
    pending_count = _int_value(payload.get("pending_staging_check_count"))
    blocker_count = len(payload.get("promotion_blockers", ()))
    if not passed:
        level = "error"
        status = "local_contract_failed"
    elif not promotion_ready:
        level = "warning"
        status = "promotion_not_ready"
    else:
        level = "notice"
        status = "promotion_ready"
    print(
        f"::{level}::file-processing golden {status}; "
        f"promotion_ready={str(promotion_ready).lower()} "
        f"pending_staging_check_count={pending_count} "
        f"promotion_blocker_count={blocker_count}"
    )


def _int_value(value: object) -> int:
    if isinstance(value, int):
        return value
    return 0


def _write_payload(payload: dict[str, Any], output: Path | None) -> None:
    encoded = json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True)
    if output is None:
        print(encoded)
        return
    output.write_text(encoded + "\n", encoding="utf-8")


if __name__ == "__main__":
    raise SystemExit(main())
