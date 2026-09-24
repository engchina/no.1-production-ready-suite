"""Prepare or upload an Agent Runtime release archive to OCI Object Storage.

By default this script is a safe dry-run: it validates archive-record.json,
verifies the archived files still match their recorded hashes, and prints a
tamper-evident upload manifest. Add --execute-upload and --retention-confirmed
to perform OCI SDK put_object calls on a runner that has OCI credentials, an
immutable / retention-enabled bucket, and the optional oci package installed.
"""

from __future__ import annotations

import argparse
import importlib
import json
from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from agent_runtime_release_gate_check import _sha256_file
from agent_runtime_validate_evidence import _load_json

JsonObject = dict[str, Any]


def main() -> int:
    args = _parse_args()
    archive_record_path = Path(args.archive_record_file)
    archive_record = _load_json(archive_record_path)
    upload_objects, violations = _upload_objects(
        archive_record,
        archive_record_path=archive_record_path,
    )
    if not args.bucket_name:
        violations.append("bucket_name_required")
    if args.execute_upload and not args.retention_confirmed:
        violations.append("retention_not_confirmed")
    for item in upload_objects:
        item["object_name"] = _object_name(
            args.object_prefix,
            _string(item.get("relative_path")),
        )

    uploaded_count = 0
    if args.execute_upload and not violations:
        upload_violations, uploaded_count = _execute_upload(
            upload_objects,
            namespace=args.namespace,
            bucket_name=args.bucket_name,
            object_prefix=args.object_prefix,
            config_file=args.oci_config_file,
            profile=args.oci_profile,
        )
        violations.extend(upload_violations)

    manifest: JsonObject = {
        "schema_version": "1.0",
        "name": "agent-runtime-release-archive-upload",
        "ok": not violations,
        "created_at": datetime.now(UTC).isoformat(),
        "generated_by": args.generated_by,
        "execute_upload": args.execute_upload,
        "retention_confirmed": args.retention_confirmed,
        "bucket_name": args.bucket_name,
        "namespace": args.namespace,
        "object_prefix": args.object_prefix,
        "archive_record_file": str(archive_record_path),
        "archive_id": (
            archive_record.get("archive_id") if isinstance(archive_record, dict) else None
        ),
        "environment": (
            archive_record.get("environment") if isinstance(archive_record, dict) else None
        ),
        "objects": upload_objects,
        "object_count": len(upload_objects),
        "uploaded_count": uploaded_count,
        "violations": violations,
    }
    output = json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True)
    markdown_summary = _markdown_summary(manifest)
    if args.output:
        Path(args.output).write_text(f"{output}\n", encoding="utf-8")
    if args.upload_summary_markdown:
        Path(args.upload_summary_markdown).write_text(
            markdown_summary,
            encoding="utf-8",
        )
    print(output)
    return 0 if not violations else 3


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("archive_record_file")
    parser.add_argument("--bucket-name", default="")
    parser.add_argument("--namespace", default="")
    parser.add_argument(
        "--object-prefix",
        default="agent-runtime/release-archives",
        help="Object name prefix. archive_id and relative archive paths are appended.",
    )
    parser.add_argument("--generated-by", default="agent-runtime-release-archive-uploader")
    parser.add_argument("--output", default="")
    parser.add_argument("--upload-summary-markdown", default="")
    parser.add_argument("--oci-config-file", default="")
    parser.add_argument("--oci-profile", default="DEFAULT")
    parser.add_argument(
        "--execute-upload",
        action="store_true",
        help="Perform OCI Object Storage uploads. Omitted means validate and plan only.",
    )
    parser.add_argument(
        "--retention-confirmed",
        action="store_true",
        help=(
            "Confirm the target bucket has immutable retention configured. "
            "Required with --execute-upload."
        ),
    )
    return parser.parse_args()


def _upload_objects(
    archive_record: JsonObject,
    *,
    archive_record_path: Path,
) -> tuple[list[JsonObject], list[str]]:
    violations: list[str] = []
    if archive_record.get("_load_error"):
        return [], [f"archive_record.{archive_record.get('_load_error')}"]
    if archive_record.get("ok") is not True:
        violations.append("archive_record.not_ok")

    archive_dir = Path(_string(archive_record.get("archive_dir")))
    if not archive_dir.is_absolute():
        archive_dir = archive_record_path.resolve().parent
    archive_id = _string(archive_record.get("archive_id")) or "unknown"
    upload_objects: list[JsonObject] = [
        _upload_object(
            kind="archive_record",
            source_path=archive_record_path,
            archive_dir=archive_dir,
            archive_id=archive_id,
        )
    ]
    archive_summary_path_raw = _string(archive_record.get("archive_summary_path"))
    if archive_summary_path_raw:
        archive_summary_path = Path(archive_summary_path_raw)
        upload_objects.append(
            _upload_object(
                kind="archive_summary",
                source_path=archive_summary_path,
                archive_dir=archive_dir,
                archive_id=archive_id,
            )
        )
    for artifact in _artifact_list(archive_record.get("artifacts")):
        archive_path = Path(_string(artifact.get("archive_path")))
        kind = _string(artifact.get("kind")) or "artifact"
        upload_objects.append(
            _upload_object(
                kind=kind,
                source_path=archive_path,
                archive_dir=archive_dir,
                archive_id=archive_id,
                expected_sha256=_string(artifact.get("sha256")),
            )
        )

    for item in upload_objects:
        kind = _string(item.get("kind")) or "object"
        source_path = Path(_string(item.get("source_path")))
        if not source_path.exists():
            violations.append(f"object.{kind}.missing")
            continue
        if not source_path.is_file():
            violations.append(f"object.{kind}.not_file")
            continue
        actual_sha256 = _sha256_file(source_path)
        item["sha256"] = actual_sha256
        item["size_bytes"] = source_path.stat().st_size
        expected_sha256 = _string(item.get("expected_sha256"))
        if expected_sha256 and actual_sha256 != expected_sha256:
            violations.append(f"object.{kind}.sha256_mismatch")
    return upload_objects, violations


def _upload_object(
    *,
    kind: str,
    source_path: Path,
    archive_dir: Path,
    archive_id: str,
    expected_sha256: str = "",
) -> JsonObject:
    return {
        "kind": kind,
        "source_path": str(source_path),
        "relative_path": _relative_archive_path(source_path, archive_dir, archive_id),
        "expected_sha256": expected_sha256,
        "sha256": "",
        "size_bytes": 0,
        "uploaded": False,
    }


def _relative_archive_path(source_path: Path, archive_dir: Path, archive_id: str) -> str:
    try:
        relative_path = source_path.resolve().relative_to(archive_dir.resolve())
    except ValueError:
        relative_path = Path(source_path.name)
    return str(Path(archive_id) / relative_path).replace("\\", "/")


def _execute_upload(
    upload_objects: list[JsonObject],
    *,
    namespace: str,
    bucket_name: str,
    object_prefix: str,
    config_file: str,
    profile: str,
) -> tuple[list[str], int]:
    violations: list[str] = []
    try:
        oci: Any = importlib.import_module("oci")
    except ImportError:
        return ["oci_sdk_unavailable"], 0
    try:
        if config_file:
            config = oci.config.from_file(file_location=config_file, profile_name=profile)
        else:
            config = oci.config.from_file(profile_name=profile)
        client = oci.object_storage.ObjectStorageClient(config)
        namespace_name = namespace or str(client.get_namespace().data)
    except Exception as exc:  # pragma: no cover - requires real OCI configuration
        return [f"oci_client_init_failed:{exc.__class__.__name__}"], 0

    uploaded_count = 0
    for item in upload_objects:
        object_name = _string(item.get("object_name")) or _object_name(
            object_prefix,
            _string(item.get("relative_path")),
        )
        item["object_name"] = object_name
        try:
            with Path(_string(item.get("source_path"))).open("rb") as body:
                client.put_object(
                    namespace_name=namespace_name,
                    bucket_name=bucket_name,
                    object_name=object_name,
                    put_object_body=body,
                )
        except Exception as exc:  # pragma: no cover - requires real OCI service
            violations.append(
                f"object.{_string(item.get('kind')) or 'object'}.upload_failed:"
                f"{exc.__class__.__name__}"
            )
            continue
        item["uploaded"] = True
        uploaded_count += 1
    return violations, uploaded_count


def _markdown_summary(manifest: JsonObject) -> str:
    objects = manifest.get("objects")
    lines = [
        "# Agent Runtime Release Archive Upload",
        "",
        f"- OK: `{manifest.get('ok')}`",
        f"- Execute upload: `{manifest.get('execute_upload')}`",
        f"- Retention confirmed: `{manifest.get('retention_confirmed')}`",
        f"- Bucket: `{manifest.get('bucket_name', '')}`",
        f"- Namespace: `{manifest.get('namespace', '')}`",
        f"- Object prefix: `{manifest.get('object_prefix', '')}`",
        f"- Archive id: `{manifest.get('archive_id', '')}`",
        f"- Environment: `{manifest.get('environment', '')}`",
        f"- Object count: `{manifest.get('object_count', 0)}`",
        f"- Uploaded count: `{manifest.get('uploaded_count', 0)}`",
        "",
        "## Objects",
        "",
        "| Kind | Size Bytes | SHA256 | Uploaded | Object Name | Source |",
        "|---|---:|---|---:|---|---|",
    ]
    if isinstance(objects, list):
        for item in objects:
            status = _mapping(item)
            object_name = _string(status.get("object_name")) or _object_name(
                _string(manifest.get("object_prefix")),
                _string(status.get("relative_path")),
            )
            lines.append(
                "| "
                + " | ".join(
                    [
                        _string(status.get("kind")),
                        str(status.get("size_bytes", 0)),
                        f"`{status.get('sha256', '')}`",
                        str(status.get("uploaded")),
                        f"`{object_name}`",
                        f"`{status.get('source_path', '')}`",
                    ]
                )
                + " |"
            )
    violations = manifest.get("violations")
    if isinstance(violations, list) and violations:
        lines.extend(["", "## Violations", ""])
        lines.extend(f"- `{violation}`" for violation in violations)
    lines.append("")
    return "\n".join(lines)


def _object_name(prefix: str, relative_path: str) -> str:
    cleaned_prefix = prefix.strip("/")
    cleaned_relative = relative_path.strip("/")
    return f"{cleaned_prefix}/{cleaned_relative}" if cleaned_prefix else cleaned_relative


def _artifact_list(value: object) -> list[JsonObject]:
    if not isinstance(value, list):
        return []
    return [dict(item) for item in value if isinstance(item, Mapping)]


def _mapping(value: object) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _string(value: object) -> str:
    return value if isinstance(value, str) else ""


if __name__ == "__main__":
    raise SystemExit(main())
