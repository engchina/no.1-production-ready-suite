"""Archive a verified Agent Runtime release bundle and its evidence artifacts.

The archiver is intentionally filesystem based: it verifies every hash recorded
in the release bundle, copies the bundle and required artifacts into a stable
archive directory, and writes an archive record that can be uploaded to OCI
Object Storage or any immutable release-record store by the platform pipeline.
"""

from __future__ import annotations

import argparse
import json
import shutil
from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from agent_runtime_build_release_bundle import _bundle_content_sha256
from agent_runtime_release_gate_check import _sha256_file
from agent_runtime_validate_evidence import _load_json, _secret_scan_violations

JsonObject = dict[str, Any]


def main() -> int:
    args = _parse_args()
    bundle_path = Path(args.bundle_file)
    bundle = _load_json(bundle_path)
    bundle_summary_path = _bundle_summary_path(bundle_path, args.bundle_summary_markdown)
    archive_root = _archive_root(bundle, args.archive_root)
    archive_id = args.archive_id or _archive_id(bundle, bundle_path)
    archive_dir = archive_root / archive_id
    copied_artifacts, violations = _archive_artifacts(
        bundle,
        bundle_path=bundle_path,
        bundle_summary_path=bundle_summary_path,
    )
    if not args.dry_run and archive_dir.exists() and not args.overwrite:
        violations.append("archive_dir_exists")

    archive_record: JsonObject = {
        "schema_version": "1.0",
        "name": "agent-runtime-release-archive-record",
        "ok": not violations,
        "created_at": datetime.now(UTC).isoformat(),
        "generated_by": args.generated_by,
        "dry_run": args.dry_run,
        "bundle_file": str(bundle_path),
        "bundle_sha256": _sha256_file(bundle_path) if bundle_path.is_file() else "",
        "bundle_content_sha256": _string(bundle.get("bundle_content_sha256")),
        "environment": bundle.get("environment") if isinstance(bundle, dict) else None,
        "archive_root": str(archive_root),
        "archive_id": archive_id,
        "archive_dir": str(archive_dir),
        "archive_policy": _mapping(bundle.get("archive_policy")),
        "artifacts": copied_artifacts,
        "violations": violations,
    }
    if not violations and not args.dry_run:
        _write_archive(archive_dir, copied_artifacts, archive_record)
    markdown_summary = _markdown_summary(archive_record)
    output = json.dumps(archive_record, ensure_ascii=False, indent=2, sort_keys=True)
    if args.output:
        Path(args.output).write_text(f"{output}\n", encoding="utf-8")
    if args.archive_summary_markdown:
        Path(args.archive_summary_markdown).write_text(
            markdown_summary,
            encoding="utf-8",
        )
    print(output)
    return 0 if not violations else 3


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("bundle_file")
    parser.add_argument(
        "--bundle-summary-markdown",
        default="",
        help="Markdown summary generated beside the bundle. Defaults to <bundle>.md.",
    )
    parser.add_argument(
        "--archive-root",
        default="",
        help=(
            "Filesystem root for the long-term archive. Defaults to the bundle "
            "archive_policy.archive_location when it is a local path."
        ),
    )
    parser.add_argument(
        "--archive-id",
        default="",
        help="Optional stable archive directory name. Defaults to environment + bundle hash.",
    )
    parser.add_argument("--generated-by", default="agent-runtime-release-archiver")
    parser.add_argument("--output", default="")
    parser.add_argument(
        "--archive-summary-markdown",
        default="",
        help="Optional Markdown summary path for the archive record.",
    )
    parser.add_argument(
        "--overwrite",
        action="store_true",
        help="Replace an existing archive directory after hash verification passes.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Validate and print the archive plan without copying files.",
    )
    return parser.parse_args()


def _bundle_summary_path(bundle_path: Path, explicit_path: str) -> Path:
    if explicit_path:
        return Path(explicit_path)
    return bundle_path.with_suffix(".md")


def _archive_root(bundle: JsonObject, explicit_root: str) -> Path:
    if explicit_root:
        return Path(explicit_root)
    archive_policy = _mapping(bundle.get("archive_policy"))
    archive_location = _string(archive_policy.get("archive_location"))
    if not archive_location or "://" in archive_location:
        raise SystemExit(
            "--archive-root is required when archive_policy.archive_location is not "
            "a local filesystem path"
        )
    return Path(archive_location)


def _archive_id(bundle: JsonObject, bundle_path: Path) -> str:
    environment = _string(bundle.get("environment")) or "unknown"
    content_hash = _string(bundle.get("bundle_content_sha256")) or _sha256_file(bundle_path)
    return f"{_slug(environment)}-{content_hash[:12]}"


def _archive_artifacts(
    bundle: JsonObject,
    *,
    bundle_path: Path,
    bundle_summary_path: Path,
) -> tuple[list[JsonObject], list[str]]:
    violations: list[str] = []
    if bundle.get("_load_error"):
        return [], [f"bundle.{bundle.get('_load_error')}"]
    if bundle.get("ok") is not True:
        violations.append("bundle.not_ok")
    if bundle.get("violations") not in (None, []):
        violations.append("bundle.has_violations")
    if bundle.get("bundle_content_sha256") != _bundle_content_sha256(
        _artifact_list(bundle.get("artifacts"))
    ):
        violations.append("bundle.content_sha256_mismatch")

    bundle_dir = bundle_path.resolve().parent
    artifacts = [
        _copy_record(
            kind="release_bundle",
            source_path=bundle_path,
            expected_sha256=_sha256_file(bundle_path) if bundle_path.is_file() else "",
            archive_name="release-bundle.json",
        ),
        _copy_record(
            kind="release_bundle_summary",
            source_path=bundle_summary_path,
            expected_sha256=(
                _sha256_file(bundle_summary_path) if bundle_summary_path.is_file() else ""
            ),
            archive_name="release-bundle.md",
        ),
    ]
    for artifact in _artifact_list(bundle.get("artifacts")):
        kind = _string(artifact.get("kind")) or "artifact"
        source_path = _resolve_artifact_path(_string(artifact.get("path")), bundle_dir)
        artifacts.append(
            _copy_record(
                kind=kind,
                source_path=source_path,
                expected_sha256=_string(artifact.get("sha256")),
                archive_name=_archive_artifact_name(kind, source_path),
            )
        )

    for artifact in artifacts:
        kind = _string(artifact.get("kind")) or "artifact"
        source_path = Path(_string(artifact.get("source_path")))
        if not source_path.exists():
            violations.append(f"artifact.{kind}.missing")
            continue
        if not source_path.is_file():
            violations.append(f"artifact.{kind}.not_file")
            continue
        expected_sha256 = _string(artifact.get("expected_sha256"))
        actual_sha256 = _sha256_file(source_path)
        artifact["sha256"] = actual_sha256
        artifact["size_bytes"] = source_path.stat().st_size
        if expected_sha256 and actual_sha256 != expected_sha256:
            violations.append(f"artifact.{kind}.sha256_mismatch")
    violations.extend(_artifact_secret_violations(artifacts))
    return artifacts, violations


def _artifact_secret_violations(artifacts: list[JsonObject]) -> list[str]:
    violations: list[str] = []
    for artifact in artifacts:
        kind = _string(artifact.get("kind")) or "artifact"
        source_path = Path(_string(artifact.get("source_path")))
        if not source_path.exists() or not source_path.is_file():
            continue
        try:
            content = source_path.read_text(encoding="utf-8")
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


def _copy_record(
    *,
    kind: str,
    source_path: Path,
    expected_sha256: str,
    archive_name: str,
) -> JsonObject:
    return {
        "kind": kind,
        "source_path": str(source_path),
        "archive_name": archive_name,
        "expected_sha256": expected_sha256,
        "sha256": "",
        "size_bytes": 0,
    }


def _write_archive(
    archive_dir: Path,
    artifacts: list[JsonObject],
    archive_record: JsonObject,
) -> None:
    if archive_dir.exists():
        shutil.rmtree(archive_dir)
    artifacts_dir = archive_dir / "artifacts"
    artifacts_dir.mkdir(parents=True, exist_ok=False)
    for artifact in artifacts:
        archive_path = artifacts_dir / _string(artifact.get("archive_name"))
        shutil.copy2(_string(artifact.get("source_path")), archive_path)
        artifact["archive_path"] = str(archive_path)
    archive_record_path = archive_dir / "archive-record.json"
    archive_summary_path = archive_dir / "archive-record.md"
    archive_record["archive_record_path"] = str(archive_record_path)
    archive_record["archive_summary_path"] = str(archive_summary_path)
    archive_record_path.write_text(
        json.dumps(archive_record, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    archive_summary_path.write_text(_markdown_summary(archive_record), encoding="utf-8")


def _markdown_summary(record: JsonObject) -> str:
    archive_policy = _mapping(record.get("archive_policy"))
    artifacts = record.get("artifacts")
    lines = [
        "# Agent Runtime Release Archive",
        "",
        f"- Environment: `{record.get('environment', '')}`",
        f"- OK: `{record.get('ok')}`",
        f"- Dry-run: `{record.get('dry_run')}`",
        f"- Archive directory: `{record.get('archive_dir', '')}`",
        f"- Bundle SHA256: `{record.get('bundle_sha256', '')}`",
        f"- Bundle content SHA256: `{record.get('bundle_content_sha256', '')}`",
        f"- Generated by: `{record.get('generated_by', '')}`",
        "",
        "## Retention",
        "",
        f"- Retention days: `{archive_policy.get('retention_days', '')}`",
        f"- Archive location: `{archive_policy.get('archive_location', '')}`",
        f"- Archive owner: `{archive_policy.get('archive_owner', '')}`",
        (
            "- Immutable storage required: "
            f"`{archive_policy.get('immutable_storage_required', '')}`"
        ),
        "",
        "## Artifacts",
        "",
        "| Kind | Size Bytes | SHA256 | Archive Name | Source |",
        "|---|---:|---|---|---|",
    ]
    if isinstance(artifacts, list):
        for artifact in artifacts:
            status = _mapping(artifact)
            lines.append(
                "| "
                + " | ".join(
                    [
                        _string(status.get("kind")),
                        str(status.get("size_bytes", 0)),
                        f"`{status.get('sha256', '')}`",
                        f"`{status.get('archive_name', '')}`",
                        f"`{status.get('source_path', '')}`",
                    ]
                )
                + " |"
            )
    violations = record.get("violations")
    if isinstance(violations, list) and violations:
        lines.extend(["", "## Violations", ""])
        lines.extend(f"- `{violation}`" for violation in violations)
    lines.append("")
    return "\n".join(lines)


def _artifact_list(value: object) -> list[JsonObject]:
    if not isinstance(value, list):
        return []
    return [dict(item) for item in value if isinstance(item, Mapping)]


def _resolve_artifact_path(raw_path: str, bundle_dir: Path) -> Path:
    path = Path(raw_path)
    return path if path.is_absolute() else bundle_dir / path


def _archive_artifact_name(kind: str, source_path: Path) -> str:
    suffix = source_path.suffix
    return f"{_slug(kind)}{suffix}" if suffix else _slug(kind)


def _slug(value: str) -> str:
    normalized = [character.lower() if character.isalnum() else "-" for character in value.strip()]
    slug = "".join(normalized).strip("-")
    while "--" in slug:
        slug = slug.replace("--", "-")
    return slug or "unknown"


def _mapping(value: object) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _string(value: object) -> str:
    return value if isinstance(value, str) else ""


if __name__ == "__main__":
    raise SystemExit(main())
