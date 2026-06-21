"""Scaffold an Agent Runtime release review JSON.

The scaffold fills environment, reviewer, timestamps, evidence sha256, manifest
sha256, and the required checklist keys. It defaults to a non-approving review
so release approval remains an explicit human action.
"""

from __future__ import annotations

import argparse
import getpass
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from agent_runtime_release_gate_check import REQUIRED_CHECKLIST, _sha256_file
from agent_runtime_validate_evidence import DEFAULT_MANIFEST_PATH, _load_json

JsonObject = dict[str, Any]


def main() -> int:
    args = _parse_args()
    evidence_path = Path(args.evidence_file)
    manifest_path = Path(args.manifest)
    evidence = _load_json(evidence_path)
    manifest = _load_json(manifest_path)
    violations = _input_violations(
        evidence,
        manifest,
        expected_environment=args.environment,
        allow_dry_run=args.allow_dry_run,
    )
    if violations:
        summary = {
            "ok": False,
            "evidence_file": args.evidence_file,
            "manifest_file": args.manifest,
            "violations": violations,
        }
        print(json.dumps(summary, ensure_ascii=False, sort_keys=True))
        return 3

    environment = str(evidence["environment"])
    checklist_value = bool(args.mark_checklist_complete)
    review: JsonObject = {
        "schema_version": "1.0",
        "environment": environment,
        "decision": args.decision,
        "reviewed_at": args.reviewed_at or _utc_now_iso(),
        "reviewer": args.reviewer or _default_reviewer(),
        "evidence_sha256": _sha256_file(evidence_path),
        "manifest_sha256": _sha256_file(manifest_path),
        "checklist": {key: checklist_value for key in REQUIRED_CHECKLIST},
        "notes": args.note
        or [
            "Verify every checklist item before changing decision to approved.",
        ],
    }
    output = json.dumps(review, ensure_ascii=False, indent=2, sort_keys=True)
    if args.output:
        Path(args.output).write_text(f"{output}\n", encoding="utf-8")
    print(output)
    return 0


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("evidence_file")
    parser.add_argument(
        "--manifest",
        default=str(DEFAULT_MANIFEST_PATH),
        help="Production validation manifest used with this evidence.",
    )
    parser.add_argument("--environment", default="")
    parser.add_argument("--reviewer", default="")
    parser.add_argument("--reviewed-at", default="")
    parser.add_argument(
        "--decision",
        choices=["pending_review", "approved", "rejected"],
        default="pending_review",
    )
    parser.add_argument(
        "--mark-checklist-complete",
        action="store_true",
        help="Set every release checklist item to true.",
    )
    parser.add_argument(
        "--allow-dry-run",
        action="store_true",
        help="Allow scaffolding from dry-run evidence for rehearsal only.",
    )
    parser.add_argument("--note", action="append", default=[])
    parser.add_argument("--output", default="")
    return parser.parse_args()


def _input_violations(
    evidence: JsonObject,
    manifest: JsonObject,
    *,
    expected_environment: str,
    allow_dry_run: bool,
) -> list[str]:
    violations: list[str] = []
    if evidence.get("_load_error"):
        return [f"evidence.{evidence['_load_error']}"]
    if manifest.get("_load_error"):
        violations.append(f"manifest.{manifest['_load_error']}")
    environment = evidence.get("environment")
    if not isinstance(environment, str) or not environment:
        violations.append("evidence.environment_missing")
    elif expected_environment and environment != expected_environment:
        violations.append("environment_mismatch")
    if evidence.get("mode") != "live" and not allow_dry_run:
        violations.append("evidence.mode_not_live")
    return violations


def _default_reviewer() -> str:
    try:
        reviewer = getpass.getuser()
    except OSError:
        reviewer = ""
    return reviewer or "release-reviewer"


def _utc_now_iso() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


if __name__ == "__main__":
    raise SystemExit(main())
