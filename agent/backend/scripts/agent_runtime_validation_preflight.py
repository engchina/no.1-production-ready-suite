"""Preflight required configuration for live Agent Runtime validation.

This script prints only configuration presence / missing variable names. It
never prints secret values.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
from collections.abc import Mapping
from pathlib import Path
from typing import Any

JsonObject = dict[str, Any]
ROOT_DIR = Path(__file__).resolve().parents[2]
BACKEND_DIR = ROOT_DIR / "backend"

REQUIRED_ENV_GROUPS = {
    "oracle": (
        "AGENT_RUNTIME_ORACLE_DSN",
        "AGENT_RUNTIME_ORACLE_USER",
        "AGENT_RUNTIME_ORACLE_PASSWORD",
    ),
    "rbac_jwks": (
        "AGENT_RUNTIME_BASE_URL",
        "AGENT_RBAC_JWT_JWKS_URL",
        "AGENT_RBAC_JWT_SAMPLE_TOKEN",
    ),
    "mcp_oauth": (
        "AGENT_EXTERNAL_MCP_BASE_URL",
        "AGENT_EXTERNAL_MCP_OAUTH_TOKEN_URL",
        "AGENT_EXTERNAL_MCP_OAUTH_CLIENT_ID",
        "AGENT_EXTERNAL_MCP_OAUTH_CLIENT_SECRET",
        "AGENT_RBAC_JWT_JWKS_URL",
    ),
}

OPTIONAL_ENV_GROUPS = {
    "rbac_policy": (
        "AGENT_RBAC_POLICY_URL",
        "AGENT_RBAC_POLICY_API_KEY",
    ),
    "mcp_optional": (
        "AGENT_EXTERNAL_MCP_API_KEY",
        "AGENT_EXTERNAL_MCP_SESSION_ID",
        "AGENT_EXTERNAL_MCP_OAUTH_SCOPE",
    ),
}


def main() -> int:
    args = _parse_args()
    required = {
        name: _env_group_status(env_names) for name, env_names in REQUIRED_ENV_GROUPS.items()
    }
    optional = {
        name: _env_group_status(env_names) for name, env_names in OPTIONAL_ENV_GROUPS.items()
    }
    runtime = _runtime_status(args.container_runtime)
    missing_required = {
        name: status["missing"] for name, status in required.items() if status["missing"]
    }
    release_chain = _release_chain_status(
        args.environment,
        required=required,
        runtime=runtime,
        missing_required=missing_required,
    )
    ok = not missing_required and runtime["configured"] and release_chain["configured"]
    payload: JsonObject = {
        "ok": ok,
        "environment": args.environment,
        "required": required,
        "optional": optional,
        "container_runtime": runtime,
        "missing_required": missing_required,
        "release_chain": release_chain,
    }
    if args.summary_markdown:
        Path(args.summary_markdown).write_text(
            _markdown_summary(payload),
            encoding="utf-8",
        )
    print(json.dumps(payload, ensure_ascii=False, sort_keys=True))
    return 0 if ok else 3


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--environment", default="production")
    parser.add_argument(
        "--container-runtime",
        default=os.getenv("AGENT_COMMAND_CONTAINER_RUNTIME", "docker"),
    )
    parser.add_argument("--summary-markdown", default="")
    args = parser.parse_args()
    if not args.container_runtime.strip():
        parser.error("--container-runtime must not be empty")
    return args


def _env_group_status(env_names: tuple[str, ...]) -> JsonObject:
    configured = [name for name in env_names if bool(os.getenv(name))]
    missing = [name for name in env_names if not os.getenv(name)]
    return {
        "configured": len(missing) == 0,
        "configured_count": len(configured),
        "required_count": len(env_names),
        "missing": missing,
    }


def _runtime_status(runtime: str) -> JsonObject:
    resolved = shutil.which(runtime)
    return {
        "configured": bool(resolved),
        "runtime": runtime,
        "path": resolved or "",
    }


def _release_chain_status(
    environment: str,
    *,
    required: Mapping[str, JsonObject],
    runtime: JsonObject,
    missing_required: Mapping[str, object],
) -> JsonObject:
    artifact_targets = _release_artifact_targets(environment)
    entrypoints = {
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
    manifest_status = _file_status(
        ROOT_DIR / "docs" / "agent-runtime-production-validation.manifest.json"
    )
    entrypoint_missing = [
        name
        for name, status in entrypoints.items()
        if status["exists"] is not True or status["executable"] is not True
    ]
    missing = [f"entrypoint:{name}" for name in entrypoint_missing]
    if manifest_status["exists"] is not True:
        missing.append("manifest")
    if runtime.get("configured") is not True:
        missing.append("runner:container_runtime")
    for group in missing_required:
        missing.append(f"secret_group:{group}")

    secret_groups = {
        name: {
            "configured": status.get("configured") is True,
            "missing_count": (
                len(status.get("missing", [])) if isinstance(status.get("missing"), list) else 0
            ),
        }
        for name, status in required.items()
    }
    configured = (
        not [
            name
            for name, status in entrypoints.items()
            if status["exists"] is not True or status["executable"] is not True
        ]
        and manifest_status["exists"] is True
    )
    ready = configured and runtime.get("configured") is True and not missing_required
    return {
        "configured": configured,
        "ready": ready,
        "requirements": {
            "secret_groups": {
                "configured": not missing_required,
                "groups": secret_groups,
                "missing_groups": list(missing_required),
            },
            "runner": {
                "container_runtime_configured": runtime.get("configured") is True,
                "container_runtime": runtime.get("runtime", ""),
                "container_runtime_path": runtime.get("path", ""),
            },
            "human_review_required": True,
            "review_json_target": artifact_targets["review"],
            "bundle_json_target": artifact_targets["bundle"],
        },
        "artifact_targets": artifact_targets,
        "entrypoints": entrypoints,
        "validation_manifest": manifest_status,
        "missing": missing,
    }


def _release_artifact_targets(environment: str) -> JsonObject:
    return {
        "runner_readiness": str(BACKEND_DIR / f"validation-runner-readiness.{environment}.json"),
        "preflight": str(BACKEND_DIR / f"validation-preflight.{environment}.json"),
        "evidence": str(BACKEND_DIR / f"validation-evidence.{environment}.json"),
        "evidence_summary": str(BACKEND_DIR / f"validation-evidence.{environment}.md"),
        "review": str(BACKEND_DIR / f"validation-review.{environment}.json"),
        "bundle": str(BACKEND_DIR / f"validation-bundle.{environment}.json"),
        "archive": str(BACKEND_DIR / f"validation-archive.{environment}.json"),
        "upload": str(BACKEND_DIR / f"validation-upload.{environment}.json"),
        "chain": str(BACKEND_DIR / f"validation-chain.{environment}.json"),
        "validation_manifest": str(
            ROOT_DIR / "docs" / "agent-runtime-production-validation.manifest.json"
        ),
    }


def _entrypoint_status(path: Path, *, executable_required: bool = True) -> JsonObject:
    status = _file_status(path)
    status["executable_required"] = executable_required
    status["executable"] = status["is_file"] is True and (
        not executable_required or os.access(path, os.X_OK)
    )
    return status


def _file_status(path: Path) -> JsonObject:
    return {
        "path": str(path),
        "exists": path.exists(),
        "is_file": path.is_file(),
    }


def _markdown_summary(payload: JsonObject) -> str:
    environment = payload.get("environment", "")
    release_chain = _mapping(payload.get("release_chain"))
    requirements = _mapping(release_chain.get("requirements"))
    secret_groups = _mapping(_mapping(requirements.get("secret_groups")).get("groups"))
    runner = _mapping(requirements.get("runner"))
    artifacts = _mapping(release_chain.get("artifact_targets"))
    entrypoints = _mapping(release_chain.get("entrypoints"))
    lines = [
        "# Agent Runtime Validation Preflight",
        "",
        f"- Environment: `{environment}`",
        f"- Overall OK: `{payload.get('ok')}`",
        f"- Release Chain Ready: `{release_chain.get('ready')}`",
        f"- Release Chain Configured: `{release_chain.get('configured')}`",
        "",
        "## Secret Groups",
        "",
        "| Group | Configured | Missing Count |",
        "|---|---:|---:|",
    ]
    for name, value in secret_groups.items():
        status = _mapping(value)
        lines.append(f"| {name} | {status.get('configured')} | {status.get('missing_count')} |")
    lines.extend(
        [
            "",
            "## Runner",
            "",
            "| Check | Value |",
            "|---|---|",
            f"| Container runtime configured | `{runner.get('container_runtime_configured')}` |",
            f"| Container runtime | `{runner.get('container_runtime', '')}` |",
            f"| Container runtime path | `{runner.get('container_runtime_path', '')}` |",
            "",
            "## Artifact Targets",
            "",
            "| Artifact | Path |",
            "|---|---|",
        ]
    )
    for name, path in artifacts.items():
        lines.append(f"| {name} | `{path}` |")
    lines.extend(
        [
            "",
            "## Entrypoints",
            "",
            "| Entrypoint | Exists | Executable | Path |",
            "|---|---:|---:|---|",
        ]
    )
    for name, value in entrypoints.items():
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
    missing = release_chain.get("missing")
    if isinstance(missing, list) and missing:
        lines.extend(["", "## Missing Items", ""])
        lines.extend(f"- `{item}`" for item in missing)
    lines.append("")
    return "\n".join(lines)


def _mapping(value: object) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


if __name__ == "__main__":
    raise SystemExit(main())
