"""Validate Agent Runtime live evidence plus human release review.

This gate is intended for the final release step after live evidence has been
collected and validated. It verifies the evidence again, then checks that the
review JSON approves the exact evidence and manifest artifacts by sha256.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from collections.abc import Mapping
from datetime import datetime
from pathlib import Path
from typing import Any

from agent_runtime_validate_evidence import (
    DEFAULT_MANIFEST_PATH,
    _load_json,
    _secret_scan_violations,
    _validate_evidence,
)

JsonObject = dict[str, Any]
REQUIRED_CHECKLIST = (
    "validator_passed",
    "live_mode_confirmed",
    "runner_readiness_accepted",
    "secrets_absent",
    "oracle_sla_accepted",
    "rbac_jwks_accepted",
    "mcp_oauth_accepted",
    "container_sandbox_accepted",
    "rollback_plan_confirmed",
)


def main() -> int:
    args = _parse_args()
    evidence_path = Path(args.evidence_file)
    review_path = Path(args.review_file)
    manifest_path = Path(args.manifest)
    evidence = _load_json(evidence_path)
    manifest = _load_json(manifest_path)
    review = _load_json(review_path)
    evidence_sha256 = _sha256_file(evidence_path)
    manifest_sha256 = _sha256_file(manifest_path)

    violations = [
        f"evidence.{violation}"
        for violation in _validate_evidence(
            evidence,
            allow_dry_run=args.allow_dry_run,
            manifest=manifest,
        )
    ]
    violations.extend(
        _validate_review(
            review,
            evidence=evidence,
            evidence_sha256=evidence_sha256,
            manifest_sha256=manifest_sha256,
            expected_environment=args.environment,
        )
    )
    summary = {
        "ok": not violations,
        "evidence_file": args.evidence_file,
        "review_file": args.review_file,
        "manifest_file": args.manifest,
        "environment": evidence.get("environment") if isinstance(evidence, dict) else None,
        "reviewer": review.get("reviewer") if isinstance(review, dict) else None,
        "decision": review.get("decision") if isinstance(review, dict) else None,
        "allow_dry_run": args.allow_dry_run,
        "evidence_sha256": evidence_sha256,
        "manifest_sha256": manifest_sha256,
        "violations": violations,
    }
    print(json.dumps(summary, ensure_ascii=False, sort_keys=True))
    return 0 if not violations else 3


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("evidence_file")
    parser.add_argument("review_file")
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
    parser.add_argument(
        "--allow-dry-run",
        action="store_true",
        help="Allow dry-run evidence for release-chain rehearsal only.",
    )
    return parser.parse_args()


def _validate_review(
    review: JsonObject,
    *,
    evidence: JsonObject,
    evidence_sha256: str,
    manifest_sha256: str,
    expected_environment: str,
) -> list[str]:
    violations: list[str] = []
    if review.get("_load_error"):
        return [f"review.{review['_load_error']}"]
    violations.extend(f"review.{violation}" for violation in _secret_scan_violations(review))

    if review.get("schema_version") != "1.0":
        violations.append("review.schema_version_invalid")
    if review.get("decision") != "approved":
        violations.append("review.decision_not_approved")
    if not isinstance(review.get("reviewer"), str) or not review.get("reviewer"):
        violations.append("review.reviewer_missing")
    reviewed_at = review.get("reviewed_at")
    if not isinstance(reviewed_at, str) or not reviewed_at:
        violations.append("review.reviewed_at_missing")
    elif not _is_iso_datetime(reviewed_at):
        violations.append("review.reviewed_at_invalid")

    environment = evidence.get("environment")
    review_environment = review.get("environment")
    if not isinstance(review_environment, str) or not review_environment:
        violations.append("review.environment_missing")
    elif review_environment != environment:
        violations.append("review.environment_mismatch")
    if expected_environment and environment != expected_environment:
        violations.append("environment_mismatch")

    if review.get("evidence_sha256") != evidence_sha256:
        violations.append("review.evidence_sha256_mismatch")
    if review.get("manifest_sha256") != manifest_sha256:
        violations.append("review.manifest_sha256_mismatch")

    checklist = review.get("checklist")
    if not isinstance(checklist, Mapping):
        violations.append("review.checklist_missing")
    else:
        for key in REQUIRED_CHECKLIST:
            if checklist.get(key) is not True:
                violations.append(f"review.checklist.{key}_not_true")

    notes = review.get("notes")
    if notes is not None and (
        not isinstance(notes, list) or not all(isinstance(note, str) for note in notes)
    ):
        violations.append("review.notes_invalid")
    return violations


def _sha256_file(path: Path) -> str:
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except FileNotFoundError:
        return ""


def _is_iso_datetime(value: str) -> bool:
    try:
        datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return False
    return True


if __name__ == "__main__":
    raise SystemExit(main())
