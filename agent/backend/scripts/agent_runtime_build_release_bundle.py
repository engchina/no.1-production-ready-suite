"""Build a tamper-evident Agent Runtime release bundle manifest.

The bundle manifest is the final archive index for production validation. It
re-validates evidence and review approval, then records hashes for every release
artifact that must be stored with the release record.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from agent_runtime_release_gate_check import _sha256_file, _validate_review
from agent_runtime_validate_evidence import (
    DEFAULT_MANIFEST_PATH,
    _load_json,
    _secret_scan_violations,
    _validate_evidence,
)

JsonObject = dict[str, Any]


def main() -> int:
    args = _parse_args()
    evidence_path = Path(args.evidence_file)
    review_path = Path(args.review_file)
    runner_readiness_path = Path(args.runner_readiness)
    preflight_path = Path(args.preflight)
    summary_path = Path(args.summary_markdown)
    manifest_path = Path(args.manifest)

    runner_readiness = _load_json(runner_readiness_path)
    preflight = _load_json(preflight_path)
    evidence = _load_json(evidence_path)
    review = _load_json(review_path)
    manifest = _load_json(manifest_path)
    artifacts = [
        _artifact("runner_readiness", runner_readiness_path),
        _artifact("preflight", preflight_path),
        _artifact("evidence", evidence_path),
        _artifact("evidence_summary", summary_path),
        _artifact("review", review_path),
        _artifact("validation_manifest", manifest_path),
    ]
    violations = _artifact_violations(artifacts)
    violations.extend(_artifact_secret_violations(artifacts))
    evidence_sha256 = _artifact_sha256(artifacts, "evidence")
    manifest_sha256 = _artifact_sha256(artifacts, "validation_manifest")
    violations.extend(
        f"runner_readiness.{violation}"
        for violation in _validate_runner_readiness(
            runner_readiness,
            expected_environment=args.environment,
            allow_dry_run=args.allow_dry_run,
        )
    )
    violations.extend(
        f"preflight.{violation}"
        for violation in _validate_preflight(
            preflight,
            expected_environment=args.environment,
            allow_dry_run=args.allow_dry_run,
        )
    )
    violations.extend(
        f"evidence.{violation}"
        for violation in _validate_evidence(
            evidence,
            allow_dry_run=args.allow_dry_run,
            manifest=manifest,
        )
    )
    violations.extend(
        _validate_review(
            review,
            evidence=evidence,
            evidence_sha256=evidence_sha256,
            manifest_sha256=manifest_sha256,
            expected_environment=args.environment,
        )
    )

    bundle: JsonObject = {
        "schema_version": "1.0",
        "name": "agent-runtime-release-bundle",
        "ok": not violations,
        "created_at": datetime.now(UTC).isoformat(),
        "generated_by": args.generated_by,
        "allow_dry_run": args.allow_dry_run,
        "environment": evidence.get("environment") if isinstance(evidence, dict) else None,
        "reviewer": review.get("reviewer") if isinstance(review, dict) else None,
        "decision": review.get("decision") if isinstance(review, dict) else None,
        "artifacts": artifacts,
        "archive_policy": _archive_policy(
            artifacts,
            retention_days=args.retention_days,
            archive_location=args.archive_location,
            archive_owner=args.archive_owner or args.generated_by,
        ),
        "bundle_content_sha256": _bundle_content_sha256(artifacts),
        "violations": violations,
    }
    output = json.dumps(bundle, ensure_ascii=False, indent=2, sort_keys=True)
    if args.output:
        Path(args.output).write_text(f"{output}\n", encoding="utf-8")
    if args.bundle_summary_markdown:
        Path(args.bundle_summary_markdown).write_text(
            _markdown_summary(bundle),
            encoding="utf-8",
        )
    print(output)
    return 0 if not violations else 3


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("evidence_file")
    parser.add_argument("review_file")
    parser.add_argument("--runner-readiness", required=True)
    parser.add_argument("--preflight", required=True)
    parser.add_argument("--summary-markdown", required=True)
    parser.add_argument(
        "--manifest",
        default=str(DEFAULT_MANIFEST_PATH),
        help="Production validation manifest used to validate the evidence.",
    )
    parser.add_argument(
        "--environment",
        default="",
        help="Optional environment label that must match both evidence and review.",
    )
    parser.add_argument("--output", default="")
    parser.add_argument("--bundle-summary-markdown", default="")
    parser.add_argument("--generated-by", default="agent-runtime-release-bundle")
    parser.add_argument(
        "--retention-days",
        type=int,
        default=365,
        help="Long-term release record retention period recorded in the bundle.",
    )
    parser.add_argument(
        "--archive-location",
        default="release-record/evidence-bundle",
        help="Long-term archive target for the release bundle artifacts.",
    )
    parser.add_argument(
        "--archive-owner",
        default="",
        help="Owner accountable for copying the bundle into the long-term archive.",
    )
    parser.add_argument(
        "--allow-dry-run",
        action="store_true",
        help="Allow dry-run evidence for release-chain rehearsal only.",
    )
    args = parser.parse_args()
    if args.retention_days < 1:
        parser.error("--retention-days must be greater than 0")
    return args


def _validate_runner_readiness(
    runner_readiness: JsonObject,
    *,
    expected_environment: str,
    allow_dry_run: bool,
) -> list[str]:
    violations: list[str] = []
    load_error = runner_readiness.get("_load_error")
    if isinstance(load_error, str):
        return [load_error]
    if runner_readiness.get("ok") is not True:
        violations.append("not_ok")
    environment = runner_readiness.get("environment")
    if (
        expected_environment
        and isinstance(environment, str)
        and environment != expected_environment
    ):
        violations.append("environment_mismatch")
    mode = runner_readiness.get("mode")
    if mode == "dry-run":
        if not allow_dry_run:
            violations.append("dry_run_not_allowed")
        return violations
    if runner_readiness.get("violations") not in (None, []):
        violations.append("has_violations")
    return violations


def _artifact(kind: str, path: Path) -> JsonObject:
    exists = path.exists()
    is_file = path.is_file()
    return {
        "kind": kind,
        "path": str(path),
        "required": True,
        "exists": exists,
        "is_file": is_file,
        "size_bytes": path.stat().st_size if exists and is_file else 0,
        "sha256": _sha256_file(path) if exists and is_file else "",
    }


def _artifact_violations(artifacts: list[JsonObject]) -> list[str]:
    violations: list[str] = []
    for artifact in artifacts:
        kind = str(artifact.get("kind", "unknown"))
        if artifact.get("exists") is not True:
            violations.append(f"artifact.{kind}.missing")
        elif artifact.get("is_file") is not True:
            violations.append(f"artifact.{kind}.not_file")
    return violations


def _artifact_secret_violations(artifacts: list[JsonObject]) -> list[str]:
    violations: list[str] = []
    for artifact in artifacts:
        if artifact.get("exists") is not True or artifact.get("is_file") is not True:
            continue
        kind = str(artifact.get("kind", "unknown"))
        path = Path(str(artifact.get("path", "")))
        try:
            content = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            violations.append(f"artifact.{kind}.secret_scan_decode_failed")
            continue
        except OSError:
            violations.append(f"artifact.{kind}.secret_scan_read_failed")
            continue
        violations.extend(
            f"artifact.{kind}.{violation}" for violation in _secret_scan_violations(content)
        )
    return violations


def _artifact_sha256(artifacts: list[JsonObject], kind: str) -> str:
    for artifact in artifacts:
        if artifact.get("kind") == kind:
            sha256 = artifact.get("sha256")
            return sha256 if isinstance(sha256, str) else ""
    return ""


def _validate_preflight(
    preflight: JsonObject,
    *,
    expected_environment: str,
    allow_dry_run: bool,
) -> list[str]:
    violations: list[str] = []
    load_error = preflight.get("_load_error")
    if isinstance(load_error, str):
        return [load_error]
    if preflight.get("ok") is not True:
        violations.append("not_ok")
    environment = preflight.get("environment")
    if (
        expected_environment
        and isinstance(environment, str)
        and environment != expected_environment
    ):
        violations.append("environment_mismatch")
    mode = preflight.get("mode")
    if mode == "dry-run":
        if not allow_dry_run:
            violations.append("dry_run_not_allowed")
        return violations
    release_chain = _mapping(preflight.get("release_chain"))
    if release_chain.get("ready") is not True:
        violations.append("release_chain_not_ready")
    return violations


def _bundle_content_sha256(artifacts: list[JsonObject]) -> str:
    material = "\n".join(
        f"{artifact.get('kind')}:{artifact.get('size_bytes')}:{artifact.get('sha256')}"
        for artifact in sorted(artifacts, key=lambda item: str(item.get("kind", "")))
    )
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


def _archive_policy(
    artifacts: list[JsonObject],
    *,
    retention_days: int,
    archive_location: str,
    archive_owner: str,
) -> JsonObject:
    return {
        "retention_days": retention_days,
        "archive_location": archive_location,
        "archive_owner": archive_owner,
        "immutable_storage_required": True,
        "required_artifact_kinds": [
            str(artifact.get("kind", "")) for artifact in artifacts if str(artifact.get("kind", ""))
        ],
        "note": (
            "GitHub Actions artifacts are short-lived; copy this release bundle "
            "and every required artifact to the long-term archive location."
        ),
    }


def _markdown_summary(bundle: JsonObject) -> str:
    artifacts = bundle.get("artifacts", [])
    archive_policy = _mapping(bundle.get("archive_policy"))
    lines = [
        "# Agent Runtime Release Bundle",
        "",
        f"- Environment: `{bundle.get('environment', '')}`",
        f"- OK: `{bundle.get('ok')}`",
        f"- Decision: `{bundle.get('decision', '')}`",
        f"- Reviewer: `{bundle.get('reviewer', '')}`",
        f"- Generated by: `{bundle.get('generated_by', '')}`",
        f"- Allow dry-run: `{bundle.get('allow_dry_run')}`",
        f"- Bundle content SHA256: `{bundle.get('bundle_content_sha256', '')}`",
        "",
        "## Archive Policy",
        "",
        f"- Retention days: `{archive_policy.get('retention_days', '')}`",
        f"- Archive location: `{archive_policy.get('archive_location', '')}`",
        f"- Archive owner: `{archive_policy.get('archive_owner', '')}`",
        (
            "- Immutable storage required: "
            f"`{archive_policy.get('immutable_storage_required', '')}`"
        ),
        (
            "- Required artifact kinds: "
            f"`{', '.join(_string_list(archive_policy.get('required_artifact_kinds')))}`"
        ),
        "",
        "## Artifacts",
        "",
        "| Kind | Exists | Size Bytes | SHA256 | Path |",
        "|---|---:|---:|---|---|",
    ]
    if isinstance(artifacts, list):
        for artifact in artifacts:
            status = _mapping(artifact)
            lines.append(
                "| "
                + " | ".join(
                    [
                        str(status.get("kind", "")),
                        str(status.get("exists")),
                        str(status.get("size_bytes", 0)),
                        f"`{status.get('sha256', '')}`",
                        f"`{status.get('path', '')}`",
                    ]
                )
                + " |"
            )
    violations = bundle.get("violations")
    if isinstance(violations, list) and violations:
        lines.extend(["", "## Violations", ""])
        lines.extend(f"- `{violation}`" for violation in violations)
    lines.append("")
    return "\n".join(lines)


def _mapping(value: object) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _string_list(value: object) -> list[str]:
    return [str(item) for item in value] if isinstance(value, list) else []


if __name__ == "__main__":
    raise SystemExit(main())
