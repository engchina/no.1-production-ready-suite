"""Check whether the production validation runner is ready.

This check runs before live evidence collection. It reports only presence,
paths, and missing variable names; secret values are never printed.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from agent_runtime_validation_preflight import (
    BACKEND_DIR,
    OPTIONAL_ENV_GROUPS,
    REQUIRED_ENV_GROUPS,
    ROOT_DIR,
    _entrypoint_status,
    _env_group_status,
    _file_status,
    _runtime_status,
)

JsonObject = dict[str, Any]
DEFAULT_MANIFEST = ROOT_DIR / "docs" / "agent-runtime-production-validation.manifest.json"


def main() -> int:
    args = _parse_args()
    payload = build_readiness_payload(args)
    if args.output:
        Path(args.output).write_text(
            json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
    if args.summary_markdown:
        Path(args.summary_markdown).write_text(
            _markdown_summary(payload),
            encoding="utf-8",
        )
    print(json.dumps(payload, ensure_ascii=False, sort_keys=True))
    return 0 if payload["ok"] is True else 3


def build_readiness_payload(args: argparse.Namespace) -> JsonObject:
    required = {
        name: _env_group_status(env_names) for name, env_names in REQUIRED_ENV_GROUPS.items()
    }
    optional = {
        name: _env_group_status(env_names) for name, env_names in OPTIONAL_ENV_GROUPS.items()
    }
    runtime = _runtime_status(args.container_runtime)
    entrypoints = _entrypoint_statuses()
    manifest_status = _manifest_status(Path(args.manifest))
    archive_root = _archive_root_status(
        args.archive_root,
        require_archive_root=args.require_archive_root,
        upload_bucket_name=args.upload_bucket_name,
        execute_upload=args.execute_upload,
    )
    object_storage = _object_storage_status(
        bucket_name=args.upload_bucket_name,
        namespace=args.upload_namespace,
        object_prefix=args.upload_object_prefix,
        execute_upload=args.execute_upload,
        retention_confirmed=args.retention_confirmed,
        archive_root=archive_root,
    )
    violations = _violations(
        required=required,
        runtime=runtime,
        entrypoints=entrypoints,
        manifest_status=manifest_status,
        archive_root=archive_root,
        object_storage=object_storage,
    )
    return {
        "schema_version": "1.0",
        "name": "agent-runtime-runner-readiness",
        "ok": not violations,
        "created_at": datetime.now(tz=UTC).isoformat(),
        "generated_by": args.generated_by,
        "environment": args.environment,
        "required_secret_groups": required,
        "optional_secret_groups": optional,
        "container_runtime": runtime,
        "entrypoints": entrypoints,
        "validation_manifest": manifest_status,
        "archive_root": archive_root,
        "object_storage": object_storage,
        "violations": violations,
    }


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--environment", default="production")
    parser.add_argument(
        "--container-runtime",
        default=os.getenv("AGENT_COMMAND_CONTAINER_RUNTIME", "docker"),
    )
    parser.add_argument("--archive-root", default="")
    parser.add_argument("--require-archive-root", action="store_true")
    parser.add_argument("--upload-bucket-name", default="")
    parser.add_argument("--upload-namespace", default="")
    parser.add_argument(
        "--upload-object-prefix",
        default="agent-runtime/release-archives",
    )
    parser.add_argument("--execute-upload", action="store_true")
    parser.add_argument("--retention-confirmed", action="store_true")
    parser.add_argument("--manifest", default=str(DEFAULT_MANIFEST))
    parser.add_argument("--generated-by", default="agent-runtime-runner-readiness")
    parser.add_argument("--output", default="")
    parser.add_argument("--summary-markdown", default="")
    args = parser.parse_args()
    if not args.container_runtime.strip():
        parser.error("--container-runtime must not be empty")
    if args.upload_bucket_name.strip() and not args.upload_object_prefix.strip():
        parser.error("--upload-object-prefix must not be empty when upload is configured")
    return args


def _entrypoint_statuses() -> dict[str, JsonObject]:
    return {
        "local_wrapper": _entrypoint_status(
            ROOT_DIR / "scripts" / "validate-production-evidence.sh"
        ),
        "local_rehearsal": _entrypoint_status(
            ROOT_DIR / "scripts" / "rehearse-production-release-chain.sh"
        ),
        "github_workflow": _entrypoint_status(
            ROOT_DIR / ".github" / "workflows" / "production-validation.yml",
            executable_required=False,
        ),
        "runner_readiness": _entrypoint_status(
            BACKEND_DIR / "scripts" / "agent_runtime_runner_readiness_check.py",
            executable_required=False,
        ),
        "preflight": _entrypoint_status(
            BACKEND_DIR / "scripts" / "agent_runtime_validation_preflight.py",
            executable_required=False,
        ),
        "collector": _entrypoint_status(
            BACKEND_DIR / "scripts" / "agent_runtime_collect_validation_evidence.py",
            executable_required=False,
        ),
        "validator": _entrypoint_status(
            BACKEND_DIR / "scripts" / "agent_runtime_validate_evidence.py",
            executable_required=False,
        ),
        "release_review_scaffold": _entrypoint_status(
            BACKEND_DIR / "scripts" / "agent_runtime_scaffold_release_review.py",
            executable_required=False,
        ),
        "release_review_gate": _entrypoint_status(
            BACKEND_DIR / "scripts" / "agent_runtime_release_gate_check.py",
            executable_required=False,
        ),
        "release_bundle_builder": _entrypoint_status(
            BACKEND_DIR / "scripts" / "agent_runtime_build_release_bundle.py",
            executable_required=False,
        ),
        "release_archiver": _entrypoint_status(
            BACKEND_DIR / "scripts" / "agent_runtime_archive_release_bundle.py",
            executable_required=False,
        ),
        "release_archive_uploader": _entrypoint_status(
            BACKEND_DIR / "scripts" / "agent_runtime_upload_release_archive.py",
            executable_required=False,
        ),
        "release_chain_verifier": _entrypoint_status(
            BACKEND_DIR / "scripts" / "agent_runtime_verify_release_chain.py",
            executable_required=False,
        ),
    }


def _manifest_status(path: Path) -> JsonObject:
    status = _file_status(path)
    release_gate_keys: list[str] = []
    artifacts: list[str] = []
    entrypoints: list[str] = []
    if path.is_file():
        try:
            manifest = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            status["load_error"] = f"json_decode:{exc.lineno}:{exc.colno}"
        else:
            release_gate = manifest.get("release_gate")
            if isinstance(release_gate, Mapping):
                release_gate_keys = sorted(str(key) for key in release_gate)
            manifest_artifacts = manifest.get("artifacts")
            if isinstance(manifest_artifacts, list):
                artifacts = sorted(str(item) for item in manifest_artifacts)
            manifest_entrypoints = manifest.get("entrypoints")
            if isinstance(manifest_entrypoints, Mapping):
                entrypoints = sorted(str(key) for key in manifest_entrypoints)
    status["has_runner_readiness_command"] = "runner_readiness_command" in release_gate_keys
    status["has_runner_readiness_artifacts"] = (
        "backend/validation-runner-readiness.<environment>.json" in artifacts
        and "backend/validation-runner-readiness.<environment>.md" in artifacts
    )
    status["has_runner_readiness_entrypoint"] = "runner_readiness" in entrypoints
    return status


def _archive_root_status(
    archive_root: str,
    *,
    require_archive_root: bool,
    upload_bucket_name: str,
    execute_upload: bool,
) -> JsonObject:
    configured = bool(archive_root.strip())
    requires_archive_root = (
        require_archive_root or bool(upload_bucket_name.strip()) or execute_upload
    )
    status: JsonObject = {
        "configured": configured,
        "required": requires_archive_root,
        "path": archive_root,
        "exists": False,
        "is_dir": False,
        "parent_exists": False,
        "writable": False,
        "creatable": False,
    }
    if not configured:
        return status
    path = Path(archive_root)
    parent = path.parent if path.parent != Path("") else Path(".")
    status["exists"] = path.exists()
    status["is_dir"] = path.is_dir()
    status["parent_exists"] = parent.exists()
    status["writable"] = (path.is_dir() and os.access(path, os.W_OK | os.X_OK)) or (
        not path.exists() and parent.exists() and os.access(parent, os.W_OK | os.X_OK)
    )
    status["creatable"] = bool(
        path.is_dir()
        or (not path.exists() and parent.exists() and os.access(parent, os.W_OK | os.X_OK))
    )
    return status


def _object_storage_status(
    *,
    bucket_name: str,
    namespace: str,
    object_prefix: str,
    execute_upload: bool,
    retention_confirmed: bool,
    archive_root: Mapping[str, Any],
) -> JsonObject:
    bucket_configured = bool(bucket_name.strip())
    oci_sdk_available = importlib.util.find_spec("oci") is not None
    retention_required = bool(bucket_configured and execute_upload)
    return {
        "configured": bool(bucket_configured and object_prefix.strip()),
        "bucket_name_configured": bucket_configured,
        "namespace_configured": bool(namespace.strip()),
        "object_prefix_configured": bool(object_prefix.strip()),
        "object_prefix": object_prefix,
        "execute_upload": execute_upload,
        "oci_sdk_available": oci_sdk_available,
        "retention_confirmed": retention_confirmed,
        "retention_required": retention_required,
        "archive_root_configured": archive_root.get("configured") is True,
    }


def _violations(
    *,
    required: Mapping[str, JsonObject],
    runtime: Mapping[str, Any],
    entrypoints: Mapping[str, JsonObject],
    manifest_status: Mapping[str, Any],
    archive_root: Mapping[str, Any],
    object_storage: Mapping[str, Any],
) -> list[str]:
    violations: list[str] = []
    for name, status in required.items():
        missing = status.get("missing")
        if isinstance(missing, list) and missing:
            violations.append(f"secret_group:{name}")
    if runtime.get("configured") is not True:
        violations.append("container_runtime.missing")
    for name, status in entrypoints.items():
        if status.get("exists") is not True or status.get("executable") is not True:
            violations.append(f"entrypoint:{name}")
    if manifest_status.get("exists") is not True:
        violations.append("manifest.missing")
    elif manifest_status.get("is_file") is not True:
        violations.append("manifest.not_file")
    if manifest_status.get("load_error"):
        violations.append(f"manifest.{manifest_status['load_error']}")
    if manifest_status.get("has_runner_readiness_command") is not True:
        violations.append("manifest.runner_readiness_command_missing")
    if manifest_status.get("has_runner_readiness_artifacts") is not True:
        violations.append("manifest.runner_readiness_artifacts_missing")
    if manifest_status.get("has_runner_readiness_entrypoint") is not True:
        violations.append("manifest.runner_readiness_entrypoint_missing")
    if archive_root.get("required") is True and archive_root.get("configured") is not True:
        violations.append("archive_root.missing")
    elif archive_root.get("configured") is True and archive_root.get("creatable") is not True:
        violations.append("archive_root.not_writable_or_creatable")
    if object_storage.get("execute_upload") is True:
        if object_storage.get("bucket_name_configured") is not True:
            violations.append("object_storage.bucket_name_missing")
        if object_storage.get("object_prefix_configured") is not True:
            violations.append("object_storage.object_prefix_missing")
        if object_storage.get("oci_sdk_available") is not True:
            violations.append("object_storage.oci_sdk_missing")
        if object_storage.get("retention_confirmed") is not True:
            violations.append("object_storage.retention_not_confirmed")
    elif (
        object_storage.get("bucket_name_configured") is True
        and object_storage.get("archive_root_configured") is not True
    ):
        violations.append("archive_root.required_for_upload_manifest")
    return violations


def _markdown_summary(payload: JsonObject) -> str:
    lines = [
        "# Agent Runtime Runner Readiness",
        "",
        f"- Environment: `{payload.get('environment', '')}`",
        f"- Overall OK: `{payload.get('ok')}`",
        f"- Generated By: `{payload.get('generated_by', '')}`",
        f"- Created At: `{payload.get('created_at', '')}`",
        "",
        "## Required Secret Groups",
        "",
        "| Group | Configured | Missing Count |",
        "|---|---:|---:|",
    ]
    for name, value in _mapping(payload.get("required_secret_groups")).items():
        status = _mapping(value)
        missing = status.get("missing")
        missing_count = len(missing) if isinstance(missing, list) else 0
        lines.append(f"| {name} | {status.get('configured')} | {missing_count} |")
    runtime = _mapping(payload.get("container_runtime"))
    lines.extend(
        [
            "",
            "## Runner",
            "",
            "| Check | Value |",
            "|---|---|",
            f"| Container runtime configured | `{runtime.get('configured')}` |",
            f"| Container runtime | `{runtime.get('runtime', '')}` |",
            f"| Container runtime path | `{runtime.get('path', '')}` |",
            "",
            "## Archive Root",
            "",
            "| Check | Value |",
            "|---|---|",
        ]
    )
    archive_root = _mapping(payload.get("archive_root"))
    for key in ("required", "configured", "exists", "is_dir", "writable", "creatable"):
        lines.append(f"| {key} | `{archive_root.get(key)}` |")
    object_storage = _mapping(payload.get("object_storage"))
    lines.extend(
        [
            "",
            "## Object Storage",
            "",
            "| Check | Value |",
            "|---|---|",
        ]
    )
    for key in (
        "configured",
        "bucket_name_configured",
        "namespace_configured",
        "object_prefix_configured",
        "execute_upload",
        "oci_sdk_available",
        "retention_confirmed",
        "retention_required",
    ):
        lines.append(f"| {key} | `{object_storage.get(key)}` |")
    manifest = _mapping(payload.get("validation_manifest"))
    lines.extend(
        [
            "",
            "## Manifest",
            "",
            "| Check | Value |",
            "|---|---|",
            f"| Exists | `{manifest.get('exists')}` |",
            f"| Has runner readiness command | `{manifest.get('has_runner_readiness_command')}` |",
            "| Has runner readiness artifacts | `"
            f"{manifest.get('has_runner_readiness_artifacts')}` |",
            "| Has runner readiness entrypoint | `"
            f"{manifest.get('has_runner_readiness_entrypoint')}` |",
            "",
            "## Entrypoints",
            "",
            "| Entrypoint | Exists | Executable | Path |",
            "|---|---:|---:|---|",
        ]
    )
    for name, value in _mapping(payload.get("entrypoints")).items():
        status = _mapping(value)
        lines.append(
            "| "
            + " | ".join(
                [
                    name,
                    str(status.get("exists")),
                    str(status.get("executable")),
                    f"`{status.get('path', '')}`",
                ]
            )
            + " |"
        )
    violations = payload.get("violations")
    if isinstance(violations, list) and violations:
        lines.extend(["", "## Violations", ""])
        lines.extend(f"- `{violation}`" for violation in violations)
    lines.append("")
    return "\n".join(lines)


def _mapping(value: object) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


if __name__ == "__main__":
    raise SystemExit(main())
