"""Verify an archived Agent Runtime release directory offline.

The verifier reads archive-record.json plus the copied files under
<archive-dir>/artifacts, recalculates hashes, and checks the embedded release
bundle manifest. It intentionally resolves archived files from archive_name so
the directory can be moved before offline verification.
"""

from __future__ import annotations

import argparse
import json
from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from agent_runtime_build_release_bundle import _bundle_content_sha256
from agent_runtime_release_gate_check import _sha256_file
from agent_runtime_validate_evidence import _load_json

JsonObject = dict[str, Any]


def main() -> int:
    args = _parse_args()
    archive_record_path, archive_dir, archive_record = _resolve_archive_input(Path(args.archive))
    result = _verify_archive_dir(
        archive_record,
        archive_record_path=archive_record_path,
        archive_dir=archive_dir,
        expected_environment=args.environment,
        generated_by=args.generated_by,
    )
    output = json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True)
    if args.output:
        Path(args.output).write_text(f"{output}\n", encoding="utf-8")
    if args.summary_markdown:
        Path(args.summary_markdown).write_text(_markdown_summary(result), encoding="utf-8")
    print(output)
    return 0 if result["ok"] is True else 3


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "archive",
        help="Archive directory or archive-record.json inside the archived release.",
    )
    parser.add_argument("--environment", default="")
    parser.add_argument("--generated-by", default="agent-runtime-release-archive-verifier")
    parser.add_argument("--output", default="")
    parser.add_argument("--summary-markdown", default="")
    return parser.parse_args()


def _resolve_archive_input(path: Path) -> tuple[Path, Path, JsonObject]:
    if path.is_dir():
        archive_record_path = path / "archive-record.json"
        return archive_record_path, path, _load_json(archive_record_path)

    supplied_record = _load_json(path)
    if path.name == "archive-record.json" and (path.parent / "artifacts").is_dir():
        return path, path.parent, supplied_record

    recorded_archive_dir = Path(_string(supplied_record.get("archive_dir")))
    if recorded_archive_dir.is_dir():
        archived_record_path = recorded_archive_dir / "archive-record.json"
        if archived_record_path.is_file():
            return (
                archived_record_path,
                recorded_archive_dir,
                _load_json(archived_record_path),
            )
        return path, recorded_archive_dir, supplied_record

    return path, path.parent, supplied_record


def _verify_archive_dir(
    archive_record: JsonObject,
    *,
    archive_record_path: Path,
    archive_dir: Path,
    expected_environment: str,
    generated_by: str,
) -> JsonObject:
    artifacts_dir = archive_dir / "artifacts"
    artifact_results, artifact_violations = _verify_artifacts(
        archive_record,
        artifacts_dir=artifacts_dir,
    )
    bundle_result, bundle_violations = _verify_bundle(
        archive_record,
        artifact_results=artifact_results,
    )
    violations = (
        _archive_record_violations(
            archive_record,
            archive_record_path=archive_record_path,
            archive_dir=archive_dir,
            artifacts_dir=artifacts_dir,
            expected_environment=expected_environment,
        )
        + artifact_violations
        + bundle_violations
    )
    warnings = _archive_record_warnings(
        archive_record,
        archive_record_path=archive_record_path,
        archive_dir=archive_dir,
    )
    return {
        "schema_version": "1.0",
        "name": "agent-runtime-release-archive-directory-verification",
        "ok": not violations,
        "created_at": datetime.now(tz=UTC).isoformat(),
        "generated_by": generated_by,
        "environment": (
            archive_record.get("environment") if isinstance(archive_record, dict) else None
        ),
        "archive_id": (
            archive_record.get("archive_id") if isinstance(archive_record, dict) else None
        ),
        "archive_dir": str(archive_dir),
        "archive_record_file": str(archive_record_path),
        "archive_record_sha256": _sha256_file(archive_record_path),
        "bundle": bundle_result,
        "artifacts": artifact_results,
        "warnings": warnings,
        "violations": violations,
    }


def _archive_record_violations(
    archive_record: JsonObject,
    *,
    archive_record_path: Path,
    archive_dir: Path,
    artifacts_dir: Path,
    expected_environment: str,
) -> list[str]:
    violations: list[str] = []
    if not archive_record_path.exists():
        return ["archive_record.missing"]
    if not archive_record_path.is_file():
        return ["archive_record.not_file"]
    if archive_record.get("_load_error"):
        return [f"archive_record.{archive_record.get('_load_error')}"]
    if archive_record.get("ok") is not True:
        violations.append("archive_record.not_ok")
    if archive_record.get("violations") not in (None, []):
        violations.append("archive_record.has_violations")
    if expected_environment and archive_record.get("environment") != expected_environment:
        violations.append("archive_record.environment_mismatch")
    if not archive_dir.exists():
        violations.append("archive_dir.missing")
    elif not archive_dir.is_dir():
        violations.append("archive_dir.not_dir")
    if not artifacts_dir.exists():
        violations.append("artifacts_dir.missing")
    elif not artifacts_dir.is_dir():
        violations.append("artifacts_dir.not_dir")
    if not isinstance(archive_record.get("archive_policy"), Mapping):
        violations.append("archive_record.archive_policy_missing")
    return violations


def _archive_record_warnings(
    archive_record: JsonObject,
    *,
    archive_record_path: Path,
    archive_dir: Path,
) -> list[str]:
    warnings: list[str] = []
    recorded_dir = _string(archive_record.get("archive_dir"))
    if recorded_dir and Path(recorded_dir) != archive_dir:
        warnings.append("archive_dir.recorded_path_differs_from_verified_path")
    recorded_record = _string(archive_record.get("archive_record_path"))
    if recorded_record and Path(recorded_record) != archive_record_path:
        warnings.append("archive_record.recorded_path_differs_from_verified_path")
    return warnings


def _verify_artifacts(
    archive_record: JsonObject,
    *,
    artifacts_dir: Path,
) -> tuple[list[JsonObject], list[str]]:
    violations: list[str] = []
    results: list[JsonObject] = []
    artifacts = _artifact_list(archive_record.get("artifacts"))
    if not artifacts:
        violations.append("artifacts.empty")
        return results, violations
    seen_kinds: set[str] = set()
    for artifact in artifacts:
        kind = _string(artifact.get("kind")) or "unknown"
        archive_name = _string(artifact.get("archive_name"))
        archived_path = artifacts_dir / archive_name if archive_name else artifacts_dir
        expected_sha256 = _string(artifact.get("sha256"))
        expected_size = _int_or_none(artifact.get("size_bytes"))
        actual_sha256 = _sha256_file(archived_path) if archived_path.is_file() else ""
        actual_size = archived_path.stat().st_size if archived_path.is_file() else 0
        status: JsonObject = {
            "kind": kind,
            "archive_name": archive_name,
            "path": str(archived_path),
            "exists": archived_path.exists(),
            "is_file": archived_path.is_file(),
            "expected_sha256": expected_sha256,
            "sha256": actual_sha256,
            "expected_size_bytes": expected_size,
            "size_bytes": actual_size,
            "ok": True,
        }
        item_violations: list[str] = []
        if kind in seen_kinds:
            item_violations.append("duplicate_kind")
        seen_kinds.add(kind)
        if not archive_name:
            item_violations.append("archive_name_missing")
        if status["exists"] is not True:
            item_violations.append("file_missing")
        elif status["is_file"] is not True:
            item_violations.append("not_file")
        elif expected_sha256 != actual_sha256:
            item_violations.append("sha256_mismatch")
        if expected_size is not None and actual_size != expected_size:
            item_violations.append("size_mismatch")
        status["violations"] = item_violations
        status["ok"] = not item_violations
        violations.extend(f"artifact.{kind}.{violation}" for violation in item_violations)
        results.append(status)
    for required_kind in ("release_bundle", "release_bundle_summary"):
        if required_kind not in seen_kinds:
            violations.append(f"artifact.{required_kind}.missing")
    return results, violations


def _verify_bundle(
    archive_record: JsonObject,
    *,
    artifact_results: list[JsonObject],
) -> tuple[JsonObject, list[str]]:
    violations: list[str] = []
    release_bundle = _artifact_by_kind(artifact_results, "release_bundle")
    if release_bundle is None:
        return {"present": False, "ok": False}, ["bundle.missing"]
    bundle_path = Path(_string(release_bundle.get("path")))
    bundle = _load_json(bundle_path)
    if bundle.get("_load_error"):
        return {
            "present": bundle_path.exists(),
            "path": str(bundle_path),
            "ok": False,
        }, [f"bundle.{bundle.get('_load_error')}"]
    if bundle.get("ok") is not True:
        violations.append("bundle.not_ok")
    if bundle.get("violations") not in (None, []):
        violations.append("bundle.has_violations")
    if bundle.get("environment") != archive_record.get("environment"):
        violations.append("bundle.environment_mismatch")
    if bundle.get("bundle_content_sha256") != archive_record.get("bundle_content_sha256"):
        violations.append("bundle.content_sha256_archive_record_mismatch")
    if _sha256_file(bundle_path) != archive_record.get("bundle_sha256"):
        violations.append("bundle.sha256_archive_record_mismatch")
    if dict(_mapping(bundle.get("archive_policy"))) != dict(
        _mapping(archive_record.get("archive_policy"))
    ):
        violations.append("bundle.archive_policy_mismatch")
    bundle_artifacts = _artifact_map(bundle.get("artifacts"))
    archive_artifacts = _artifact_map(artifact_results)
    for kind, bundle_artifact in bundle_artifacts.items():
        archive_artifact = archive_artifacts.get(kind)
        if archive_artifact is None:
            violations.append(f"bundle.artifact.{kind}.missing_from_archive")
            continue
        if archive_artifact.get("sha256") != bundle_artifact.get("sha256"):
            violations.append(f"bundle.artifact.{kind}.sha256_mismatch")
    if bundle.get("bundle_content_sha256") != _bundle_content_sha256(
        _artifact_list(bundle.get("artifacts"))
    ):
        violations.append("bundle.content_sha256_mismatch")
    return {
        "present": bundle_path.exists(),
        "path": str(bundle_path),
        "ok": not violations,
        "sha256": _sha256_file(bundle_path),
        "bundle_content_sha256": bundle.get("bundle_content_sha256"),
        "artifact_count": len(bundle_artifacts),
    }, violations


def _markdown_summary(result: JsonObject) -> str:
    artifacts = result.get("artifacts")
    lines = [
        "# Agent Runtime Release Archive Directory Verification",
        "",
        f"- Environment: `{result.get('environment', '')}`",
        f"- OK: `{result.get('ok')}`",
        f"- Archive id: `{result.get('archive_id', '')}`",
        f"- Archive dir: `{result.get('archive_dir', '')}`",
        f"- Archive record SHA256: `{result.get('archive_record_sha256', '')}`",
        f"- Generated by: `{result.get('generated_by', '')}`",
        "",
        "## Artifacts",
        "",
        "| Kind | OK | Size Bytes | SHA256 | Path |",
        "|---|---:|---:|---|---|",
    ]
    if isinstance(artifacts, list):
        for artifact in artifacts:
            status = _mapping(artifact)
            lines.append(
                "| "
                + " | ".join(
                    [
                        _string(status.get("kind")),
                        str(status.get("ok")),
                        str(status.get("size_bytes", 0)),
                        f"`{status.get('sha256', '')}`",
                        f"`{status.get('path', '')}`",
                    ]
                )
                + " |"
            )
    warnings = result.get("warnings")
    if isinstance(warnings, list) and warnings:
        lines.extend(["", "## Warnings", ""])
        lines.extend(f"- `{warning}`" for warning in warnings)
    violations = result.get("violations")
    if isinstance(violations, list) and violations:
        lines.extend(["", "## Violations", ""])
        lines.extend(f"- `{violation}`" for violation in violations)
    lines.append("")
    return "\n".join(lines)


def _artifact_by_kind(
    artifacts: list[JsonObject],
    kind: str,
) -> JsonObject | None:
    for artifact in artifacts:
        if artifact.get("kind") == kind:
            return artifact
    return None


def _artifact_map(value: object) -> dict[str, JsonObject]:
    return {
        _string(item.get("kind")): item
        for item in _artifact_list(value)
        if _string(item.get("kind"))
    }


def _artifact_list(value: object) -> list[JsonObject]:
    if not isinstance(value, list):
        return []
    return [dict(item) for item in value if isinstance(item, Mapping)]


def _mapping(value: object) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _string(value: object) -> str:
    return value if isinstance(value, str) else ""


def _int_or_none(value: object) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) else None


if __name__ == "__main__":
    raise SystemExit(main())
