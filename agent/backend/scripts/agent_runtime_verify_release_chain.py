"""Verify the complete Agent Runtime release evidence chain.

This script ties together runner readiness, preflight, evidence, human review,
release bundle, archive record, and optional upload manifest. It is the final
deterministic check that catches cross-file drift after individual gates have
passed.
"""

from __future__ import annotations

import argparse
import json
from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from agent_runtime_build_release_bundle import (
    _bundle_content_sha256,
    _validate_preflight,
    _validate_runner_readiness,
)
from agent_runtime_release_gate_check import _sha256_file, _validate_review
from agent_runtime_validate_evidence import (
    DEFAULT_MANIFEST_PATH,
    _load_json,
    _secret_scan_violations,
    _validate_evidence,
)

JsonObject = dict[str, Any]
REQUIRED_BUNDLE_ARTIFACTS = (
    "runner_readiness",
    "preflight",
    "evidence",
    "evidence_summary",
    "review",
    "validation_manifest",
)


def main() -> int:
    args = _parse_args()
    paths = _paths(args)
    manifest = _load_json(paths["manifest"])
    runner_readiness = _load_json(paths["runner_readiness"])
    preflight = _load_json(paths["preflight"])
    evidence = _load_json(paths["evidence"])
    review = _load_json(paths["review"])
    bundle = _load_json(paths["bundle"])
    archive_record = _load_optional_json(paths.get("archive_record"))
    archive_dir_verification = _load_optional_json(paths.get("archive_dir_verification"))
    upload_manifest = _load_optional_json(paths.get("upload_manifest"))

    stages = [
        _stage(
            "runner_readiness",
            paths["runner_readiness"],
            _file_violations(paths["runner_readiness"])
            + _path_secret_violations(paths["runner_readiness"])
            + [
                f"runner_readiness.{violation}"
                for violation in _validate_runner_readiness(
                    runner_readiness,
                    expected_environment=args.environment,
                    allow_dry_run=args.allow_dry_run,
                )
            ],
        ),
        _stage(
            "preflight",
            paths["preflight"],
            _file_violations(paths["preflight"])
            + _path_secret_violations(paths["preflight"])
            + [
                f"preflight.{violation}"
                for violation in _validate_preflight(
                    preflight,
                    expected_environment=args.environment,
                    allow_dry_run=args.allow_dry_run,
                )
            ],
        ),
        _stage(
            "evidence",
            paths["evidence"],
            _file_violations(paths["evidence"])
            + _path_secret_violations(paths["evidence"])
            + [
                f"evidence.{violation}"
                for violation in _validate_evidence(
                    evidence,
                    allow_dry_run=args.allow_dry_run,
                    manifest=manifest,
                )
            ],
        ),
        _stage(
            "review",
            paths["review"],
            _file_violations(paths["review"])
            + _path_secret_violations(paths["review"])
            + _validate_review(
                review,
                evidence=evidence,
                evidence_sha256=_sha256_file(paths["evidence"]),
                manifest_sha256=_sha256_file(paths["manifest"]),
                expected_environment=args.environment,
            ),
        ),
        _stage(
            "bundle",
            paths["bundle"],
            _file_violations(paths["bundle"])
            + _path_secret_violations(paths["bundle"])
            + _path_secret_violations(paths["bundle_summary"])
            + _validate_bundle(
                bundle,
                expected_environment=args.environment,
                allow_dry_run=args.allow_dry_run,
                source_paths=paths,
            ),
        ),
        _stage(
            "archive",
            paths.get("archive_record"),
            _path_secret_violations(paths.get("archive_record"))
            + _validate_archive(
                archive_record,
                archive_record_path=paths.get("archive_record"),
                bundle=bundle,
                bundle_path=paths["bundle"],
                bundle_summary_path=paths["bundle_summary"],
                expected_environment=args.environment,
                allow_dry_run=args.allow_dry_run,
                required=args.require_archive,
            ),
        ),
        _stage(
            "archive_dir",
            paths.get("archive_dir_verification"),
            _path_secret_violations(paths.get("archive_dir_verification"))
            + _validate_archive_dir_verification(
                archive_dir_verification,
                archive_dir_verification_path=paths.get("archive_dir_verification"),
                archive_record=archive_record,
                archive_record_path=paths.get("archive_record"),
                expected_environment=args.environment,
                required=args.require_archive_dir_verification,
            ),
        ),
        _stage(
            "upload",
            paths.get("upload_manifest"),
            _path_secret_violations(paths.get("upload_manifest"))
            + _validate_upload(
                upload_manifest,
                upload_manifest_path=paths.get("upload_manifest"),
                archive_record=archive_record,
                archive_record_path=paths.get("archive_record"),
                expected_environment=args.environment,
                required=args.require_upload,
                require_uploaded=args.require_upload_executed,
            ),
        ),
    ]
    violations = [
        violation for stage in stages for violation in _string_list(stage.get("violations"))
    ]
    result: JsonObject = {
        "schema_version": "1.0",
        "name": "agent-runtime-release-chain-verification",
        "ok": not violations,
        "created_at": datetime.now(UTC).isoformat(),
        "generated_by": args.generated_by,
        "environment": args.environment,
        "allow_dry_run": args.allow_dry_run,
        "stages": stages,
        "violations": violations,
    }
    output = json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True)
    if args.output:
        Path(args.output).write_text(f"{output}\n", encoding="utf-8")
    if args.summary_markdown:
        Path(args.summary_markdown).write_text(_markdown_summary(result), encoding="utf-8")
    print(output)
    return 0 if not violations else 3


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--environment", required=True)
    parser.add_argument("--runner-readiness", default="")
    parser.add_argument("--preflight", default="")
    parser.add_argument("--evidence", default="")
    parser.add_argument("--evidence-summary", default="")
    parser.add_argument("--review", default="")
    parser.add_argument("--bundle", default="")
    parser.add_argument("--bundle-summary", default="")
    parser.add_argument("--archive-record", default="")
    parser.add_argument("--archive-dir-verification", default="")
    parser.add_argument("--upload-manifest", default="")
    parser.add_argument(
        "--manifest",
        default=str(DEFAULT_MANIFEST_PATH),
        help="Production validation manifest used to validate the evidence.",
    )
    parser.add_argument("--generated-by", default="agent-runtime-release-chain-verifier")
    parser.add_argument("--output", default="")
    parser.add_argument("--summary-markdown", default="")
    parser.add_argument("--allow-dry-run", action="store_true")
    parser.add_argument("--require-archive", action="store_true")
    parser.add_argument("--require-archive-dir-verification", action="store_true")
    parser.add_argument("--require-upload", action="store_true")
    parser.add_argument("--require-upload-executed", action="store_true")
    return parser.parse_args()


def _paths(args: argparse.Namespace) -> dict[str, Path]:
    environment = str(args.environment)
    values = {
        "manifest": args.manifest,
        "runner_readiness": args.runner_readiness
        or f"validation-runner-readiness.{environment}.json",
        "preflight": args.preflight or f"validation-preflight.{environment}.json",
        "evidence": args.evidence or f"validation-evidence.{environment}.json",
        "evidence_summary": args.evidence_summary or f"validation-evidence.{environment}.md",
        "review": args.review or f"validation-review.{environment}.json",
        "bundle": args.bundle or f"validation-bundle.{environment}.json",
        "bundle_summary": args.bundle_summary or f"validation-bundle.{environment}.md",
    }
    paths = {key: Path(value) for key, value in values.items()}
    archive_record = args.archive_record or _existing_optional(
        f"validation-archive.{environment}.json"
    )
    upload_manifest = args.upload_manifest or _existing_optional(
        f"validation-upload.{environment}.json"
    )
    archive_dir_verification = args.archive_dir_verification or _existing_optional(
        f"validation-archive-dir.{environment}.json"
    )
    if archive_record:
        paths["archive_record"] = Path(archive_record)
    if archive_dir_verification:
        paths["archive_dir_verification"] = Path(archive_dir_verification)
    if upload_manifest:
        paths["upload_manifest"] = Path(upload_manifest)
    return paths


def _existing_optional(path: str) -> str:
    return path if Path(path).exists() else ""


def _load_optional_json(path: Path | None) -> JsonObject:
    return _load_json(path) if path is not None else {}


def _stage(stage: str, path: Path | None, violations: list[str]) -> JsonObject:
    return {
        "stage": stage,
        "path": str(path) if path is not None else "",
        "present": path is not None and path.exists(),
        "ok": not violations,
        "violations": violations,
    }


def _file_violations(path: Path) -> list[str]:
    if not path.exists():
        return ["file_missing"]
    if not path.is_file():
        return ["not_file"]
    return []


def _path_secret_violations(path: Path | None) -> list[str]:
    if path is None or not path.exists() or not path.is_file():
        return []
    try:
        content = path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        return [f"secret_scan_decode_failed:{path}"]
    except OSError:
        return [f"secret_scan_read_failed:{path}"]
    return [f"{violation}:{path}" for violation in _secret_scan_violations(content)]


def _validate_bundle(
    bundle: JsonObject,
    *,
    expected_environment: str,
    allow_dry_run: bool,
    source_paths: Mapping[str, Path],
) -> list[str]:
    violations: list[str] = []
    if bundle.get("_load_error"):
        return [f"bundle.{bundle.get('_load_error')}"]
    if bundle.get("ok") is not True:
        violations.append("bundle.not_ok")
    if bundle.get("violations") not in (None, []):
        violations.append("bundle.has_violations")
    if bundle.get("environment") != expected_environment:
        violations.append("bundle.environment_mismatch")
    if bundle.get("allow_dry_run") is True and not allow_dry_run:
        violations.append("bundle.dry_run_not_allowed")
    if not isinstance(bundle.get("archive_policy"), Mapping):
        violations.append("bundle.archive_policy_missing")

    artifacts = _artifact_map(bundle.get("artifacts"))
    for kind in REQUIRED_BUNDLE_ARTIFACTS:
        artifact = artifacts.get(kind)
        if artifact is None:
            violations.append(f"bundle.artifact.{kind}.missing")
            continue
        source_path = source_paths[_source_path_key(kind)]
        expected_sha256 = _string(artifact.get("sha256"))
        actual_sha256 = _sha256_file(source_path)
        if expected_sha256 != actual_sha256:
            violations.append(f"bundle.artifact.{kind}.sha256_mismatch")
    bundle_content_sha256 = _string(bundle.get("bundle_content_sha256"))
    if bundle_content_sha256 != _bundle_content_sha256(_artifact_list(bundle.get("artifacts"))):
        violations.append("bundle.content_sha256_mismatch")
    return violations


def _validate_archive(
    archive_record: JsonObject,
    *,
    archive_record_path: Path | None,
    bundle: JsonObject,
    bundle_path: Path,
    bundle_summary_path: Path,
    expected_environment: str,
    allow_dry_run: bool,
    required: bool,
) -> list[str]:
    if archive_record_path is None:
        return ["archive_record_missing"] if required else []
    violations = _file_violations(archive_record_path)
    if archive_record.get("_load_error"):
        return violations + [f"archive_record.{archive_record.get('_load_error')}"]
    if archive_record.get("ok") is not True:
        violations.append("archive.not_ok")
    if archive_record.get("violations") not in (None, []):
        violations.append("archive.has_violations")
    if archive_record.get("environment") != expected_environment:
        violations.append("archive.environment_mismatch")
    if archive_record.get("dry_run") is True and not allow_dry_run:
        violations.append("archive.dry_run_not_allowed")
    if archive_record.get("bundle_sha256") != _sha256_file(bundle_path):
        violations.append("archive.bundle_sha256_mismatch")
    if archive_record.get("bundle_content_sha256") != bundle.get("bundle_content_sha256"):
        violations.append("archive.bundle_content_sha256_mismatch")
    if dict(_mapping(archive_record.get("archive_policy"))) != dict(
        _mapping(bundle.get("archive_policy"))
    ):
        violations.append("archive.archive_policy_mismatch")

    artifacts = _artifact_map(archive_record.get("artifacts"))
    bundle_artifacts = _artifact_map(bundle.get("artifacts"))
    violations.extend(
        _archive_artifact_sha_violations(
            artifacts,
            expected={
                "release_bundle": _sha256_file(bundle_path),
                "release_bundle_summary": _sha256_file(bundle_summary_path),
            },
        )
    )
    for kind, bundle_artifact in bundle_artifacts.items():
        archive_artifact = artifacts.get(kind)
        if archive_artifact is None:
            violations.append(f"archive.artifact.{kind}.missing")
            continue
        if archive_artifact.get("sha256") != bundle_artifact.get("sha256"):
            violations.append(f"archive.artifact.{kind}.sha256_mismatch")
    for kind, artifact in artifacts.items():
        archive_path = Path(_string(artifact.get("archive_path")))
        if not archive_path.exists():
            violations.append(f"archive.artifact.{kind}.file_missing")
            continue
        if _sha256_file(archive_path) != artifact.get("sha256"):
            violations.append(f"archive.artifact.{kind}.file_sha256_mismatch")
    return violations


def _archive_artifact_sha_violations(
    artifacts: Mapping[str, JsonObject],
    *,
    expected: Mapping[str, str],
) -> list[str]:
    violations: list[str] = []
    for kind, sha256 in expected.items():
        artifact = artifacts.get(kind)
        if artifact is None:
            violations.append(f"archive.artifact.{kind}.missing")
        elif artifact.get("sha256") != sha256:
            violations.append(f"archive.artifact.{kind}.sha256_mismatch")
    return violations


def _validate_upload(
    upload_manifest: JsonObject,
    *,
    upload_manifest_path: Path | None,
    archive_record: JsonObject,
    archive_record_path: Path | None,
    expected_environment: str,
    required: bool,
    require_uploaded: bool,
) -> list[str]:
    if upload_manifest_path is None:
        return ["upload_manifest_missing"] if required else []
    violations = _file_violations(upload_manifest_path)
    if upload_manifest.get("_load_error"):
        return violations + [f"upload_manifest.{upload_manifest.get('_load_error')}"]
    if upload_manifest.get("ok") is not True:
        violations.append("upload.not_ok")
    if upload_manifest.get("violations") not in (None, []):
        violations.append("upload.has_violations")
    if upload_manifest.get("environment") != expected_environment:
        violations.append("upload.environment_mismatch")
    if require_uploaded and upload_manifest.get("execute_upload") is not True:
        violations.append("upload.execute_upload_not_true")
    if (
        upload_manifest.get("execute_upload") is True
        and upload_manifest.get("retention_confirmed") is not True
    ):
        violations.append("upload.retention_not_confirmed")
    if archive_record and upload_manifest.get("archive_id") != archive_record.get("archive_id"):
        violations.append("upload.archive_id_mismatch")
    objects = _artifact_map(upload_manifest.get("objects"))
    if archive_record_path is not None:
        archive_record_object = objects.get("archive_record")
        if archive_record_object is None:
            violations.append("upload.object.archive_record.missing")
        elif archive_record_object.get("sha256") != _sha256_file(archive_record_path):
            violations.append("upload.object.archive_record.sha256_mismatch")
    archive_artifacts = _artifact_map(archive_record.get("artifacts"))
    for kind, archive_artifact in archive_artifacts.items():
        upload_object = objects.get(kind)
        if upload_object is None:
            violations.append(f"upload.object.{kind}.missing")
            continue
        if upload_object.get("sha256") != archive_artifact.get("sha256"):
            violations.append(f"upload.object.{kind}.sha256_mismatch")
    for kind, upload_object in objects.items():
        source_path = Path(_string(upload_object.get("source_path")))
        if not source_path.exists():
            violations.append(f"upload.object.{kind}.file_missing")
            continue
        if _sha256_file(source_path) != upload_object.get("sha256"):
            violations.append(f"upload.object.{kind}.file_sha256_mismatch")
        if require_uploaded and upload_object.get("uploaded") is not True:
            violations.append(f"upload.object.{kind}.not_uploaded")
    if require_uploaded and upload_manifest.get("uploaded_count") != upload_manifest.get(
        "object_count"
    ):
        violations.append("upload.uploaded_count_mismatch")
    return violations


def _validate_archive_dir_verification(
    verification: JsonObject,
    *,
    archive_dir_verification_path: Path | None,
    archive_record: JsonObject,
    archive_record_path: Path | None,
    expected_environment: str,
    required: bool,
) -> list[str]:
    if archive_dir_verification_path is None:
        return ["archive_dir_verification_missing"] if required else []
    violations = _file_violations(archive_dir_verification_path)
    if verification.get("_load_error"):
        return violations + [f"archive_dir.{verification.get('_load_error')}"]
    if verification.get("ok") is not True:
        violations.append("archive_dir.not_ok")
    if verification.get("violations") not in (None, []):
        violations.append("archive_dir.has_violations")
    if verification.get("environment") != expected_environment:
        violations.append("archive_dir.environment_mismatch")
    if archive_record and verification.get("archive_id") != archive_record.get("archive_id"):
        violations.append("archive_dir.archive_id_mismatch")
    if archive_record_path is not None and verification.get(
        "archive_record_sha256"
    ) != _sha256_file(archive_record_path):
        violations.append("archive_dir.archive_record_sha256_mismatch")
    return violations


def _source_path_key(bundle_artifact_kind: str) -> str:
    return "manifest" if bundle_artifact_kind == "validation_manifest" else bundle_artifact_kind


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


def _markdown_summary(result: JsonObject) -> str:
    stages = result.get("stages")
    lines = [
        "# Agent Runtime Release Chain Verification",
        "",
        f"- Environment: `{result.get('environment', '')}`",
        f"- OK: `{result.get('ok')}`",
        f"- Allow dry-run: `{result.get('allow_dry_run')}`",
        f"- Generated by: `{result.get('generated_by', '')}`",
        "",
        "## Stages",
        "",
        "| Stage | Present | OK | Path | Violations |",
        "|---|---:|---:|---|---|",
    ]
    if isinstance(stages, list):
        for stage in stages:
            status = _mapping(stage)
            lines.append(
                "| "
                + " | ".join(
                    [
                        _string(status.get("stage")),
                        str(status.get("present")),
                        str(status.get("ok")),
                        f"`{status.get('path', '')}`",
                        ", ".join(f"`{item}`" for item in _string_list(status.get("violations"))),
                    ]
                )
                + " |"
            )
    violations = result.get("violations")
    if isinstance(violations, list) and violations:
        lines.extend(["", "## Violations", ""])
        lines.extend(f"- `{violation}`" for violation in violations)
    lines.append("")
    return "\n".join(lines)


def _mapping(value: object) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _string(value: object) -> str:
    return value if isinstance(value, str) else ""


def _string_list(value: object) -> list[str]:
    return [str(item) for item in value] if isinstance(value, list) else []


if __name__ == "__main__":
    raise SystemExit(main())
