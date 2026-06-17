"""file-processing golden set の staging gate CLI。"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from collections.abc import Mapping, Sequence
from dataclasses import asdict
from pathlib import Path
from typing import Any

from app.config import Settings, get_settings
from app.rag.file_processing_staging import (
    FileProcessingStagingReport,
    run_file_processing_staging_checks_with_real_clients,
)
from app.rag.parser_adapter_readiness import (
    ParserAdapterRuntimeSettings,
    parser_adapter_runtime_settings,
)
from app.rag.staging_smoke import SmokePreflightResult, staging_smoke_preflight


def main(argv: Sequence[str] | None = None) -> int:
    """CLI entrypoint。"""
    parser = argparse.ArgumentParser(
        prog="rag-file-processing-staging",
        description=(
            "file-processing golden manifest の pending checks を staging 実環境で検証します。"
        ),
    )
    parser.add_argument("manifest", type=Path, help="file-processing golden manifest JSON")
    parser.add_argument("--output", type=Path, help="結果 JSON の保存先。未指定なら stdout。")
    parser.add_argument(
        "--cleanup",
        action="store_true",
        help=(
            "実行後に作成した staging document/object/KB を best-effort で削除または "
            "archive します。"
        ),
    )
    parser.add_argument(
        "--preflight-only",
        action="store_true",
        help=(
            "外部依存へ接続せず、file-processing staging 実行前の設定チェックだけを "
            "JSON 出力する。"
        ),
    )
    args = parser.parse_args(argv)
    try:
        manifest = _load_manifest(args.manifest)
        settings = get_settings()
        preflight = staging_smoke_preflight(settings=settings)
        preflight_payload = _preflight_payload(preflight, settings)
        if args.preflight_only:
            _write_payload(preflight_payload, args.output)
            return 0 if preflight_payload["passed"] else 1
        if not preflight_payload["passed"]:
            _write_payload(preflight_payload, args.output)
            return 1
        report = asyncio.run(
            run_file_processing_staging_checks_with_real_clients(
                manifest,
                manifest_path=args.manifest,
                cleanup=args.cleanup,
            )
        )
        payload = _report_payload(report, manifest=manifest)
        _write_payload(payload, args.output)
    except FileProcessingStagingCliError as exc:
        print(f"file-processing staging エラー: {exc}", file=sys.stderr)
        return exc.exit_code
    except Exception as exc:
        _write_payload({"passed": False, "error_type": type(exc).__name__}, args.output)
        return 3
    return 0 if report.passed and bool(payload["promotion_ready"]) else 1


class FileProcessingStagingCliError(RuntimeError):
    """CLI 利用者へ返す安全なエラー。"""

    def __init__(self, message: str, exit_code: int = 2) -> None:
        super().__init__(message)
        self.exit_code = exit_code


def _load_manifest(path: Path) -> dict[str, Any]:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise FileProcessingStagingCliError(f"manifest が見つかりません: {path}") from exc
    except json.JSONDecodeError as exc:
        raise FileProcessingStagingCliError(
            f"manifest が JSON として読めません: line={exc.lineno}, column={exc.colno}"
        ) from exc
    if not isinstance(raw, dict):
        raise FileProcessingStagingCliError("manifest root は JSON object にしてください。")
    return raw


def _report_payload(
    report: FileProcessingStagingReport,
    *,
    manifest: Mapping[str, Any],
) -> dict[str, Any]:
    payload = asdict(report)
    promotion_blockers = _promotion_blockers(report, manifest=manifest)
    payload.update(
        {
            "passed": report.passed,
            "promotion_ready": not promotion_blockers,
            "promotion_blockers": promotion_blockers,
            "staging_policy": _staging_policy(manifest),
            "case_count": report.case_count,
            "gate_count": report.gate_count,
            "failure_count": report.failure_count,
        }
    )
    return payload


def _promotion_blockers(
    report: FileProcessingStagingReport,
    *,
    manifest: Mapping[str, Any],
) -> list[dict[str, Any]]:
    """本番昇格を止める staging blocker を機械可読に返す。"""
    blockers: list[dict[str, Any]] = []
    if report.local_manifest_errors:
        blockers.append(
            {
                "code": "local_contract_failed",
                "count": len(report.local_manifest_errors),
            }
        )
    failed_runtime_checks = [check for check in report.runtime_checks if not check.passed]
    blockers.extend(
        {
            "code": "runtime_check_failed",
            "check": check.check,
            "failure_code": check.failure_code,
        }
        for check in failed_runtime_checks
    )
    failed_gate_count = sum(
        1
        for case_result in report.case_results
        for gate_result in case_result.gate_results
        if not gate_result.passed
    )
    if failed_gate_count:
        blockers.append({"code": "staging_gate_failed", "count": failed_gate_count})
    blockers.extend(
        {
            "code": "threshold_failed",
            "metric": threshold.metric,
            "reason": threshold.reason,
        }
        for threshold in report.threshold_results
        if not threshold.passed
    )
    runtime_status_by_check = {check.check: check.status for check in report.runtime_checks}
    for check_name in _required_runtime_checks(manifest):
        status = runtime_status_by_check.get(check_name)
        if status == "ok":
            continue
        blockers.append(
            {
                "code": "required_runtime_check_not_ok",
                "check": check_name,
                "status": status or "missing",
            }
        )
    return blockers


def _preflight_payload(preflight: SmokePreflightResult, settings: Settings) -> dict[str, Any]:
    parser_adapters = parser_adapter_runtime_settings(settings)
    parser_adapter_preflight = _parser_adapter_preflight(parser_adapters)
    passed = preflight.ok and bool(parser_adapter_preflight["ok"])
    return {
        "passed": passed,
        "preflight": asdict(preflight),
        "parser_adapters": asdict(parser_adapters),
        "parser_adapter_preflight": parser_adapter_preflight,
        "case_count": 0,
        "gate_count": 0,
        "failure_count": _preflight_failure_count(
            smoke_preflight_ok=preflight.ok,
            parser_adapter_preflight=parser_adapter_preflight,
        ),
    }


def _staging_policy(manifest: Mapping[str, Any]) -> dict[str, Any]:
    raw_policy = _mapping(manifest.get("staging_policy"))
    return {
        "required_for_promotion": bool(raw_policy.get("required_for_promotion", False)),
        "pending_checks_block_promotion": bool(
            raw_policy.get("pending_checks_block_promotion", False)
        ),
        "required_runtime_checks": _required_runtime_checks(manifest),
    }


def _required_runtime_checks(manifest: Mapping[str, Any]) -> list[str]:
    raw_policy = _mapping(manifest.get("staging_policy"))
    raw_checks = raw_policy.get("required_runtime_checks")
    if not isinstance(raw_checks, list):
        return []
    return [check for check in raw_checks if isinstance(check, str)]


def _mapping(value: object) -> dict[str, object]:
    return value if isinstance(value, dict) else {}


def _parser_adapter_preflight(runtime: ParserAdapterRuntimeSettings) -> dict[str, Any]:
    failures = [
        {
            "backend": adapter.backend,
            "status": adapter.status,
            "warning_code": adapter.warning_code,
        }
        for adapter in runtime.adapters
        if adapter.selected and adapter.status in {"disabled", "missing"}
    ]
    failure_statuses = {failure["status"] for failure in failures}
    return {
        "ok": not failures,
        "message": (
            "parser adapter preflight ok"
            if not failures
            else (
                "selected parser adapter feature flag is disabled"
                if failure_statuses == {"disabled"}
                else "selected parser adapter is not ready"
            )
        ),
        "failures": failures,
    }


def _preflight_failure_count(
    *,
    smoke_preflight_ok: bool,
    parser_adapter_preflight: dict[str, Any],
) -> int:
    count = 0 if smoke_preflight_ok else 1
    failures = parser_adapter_preflight.get("failures")
    if isinstance(failures, list):
        count += len(failures)
    return count


def _write_payload(payload: dict[str, Any], output: Path | None) -> None:
    encoded = json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True)
    if output is None:
        print(encoded)
        return
    output.write_text(encoded + "\n", encoding="utf-8")


if __name__ == "__main__":
    raise SystemExit(main())
