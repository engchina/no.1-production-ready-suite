"""Collect Agent Runtime production validation evidence.

Dry-run mode composes the safe `--dry-run` output of each validation script.
Live mode executes the same probes against real Oracle / IdP / MCP / container
runtime configuration from environment variables and returns one evidence JSON.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

JsonObject = dict[str, Any]


def main() -> int:
    args = _parse_args()
    evidence: JsonObject = {
        "validated_at": datetime.now(UTC).isoformat(),
        "environment": args.environment,
        "validator": args.validator,
        "mode": args.mode,
        "oracle": _run_probe(_oracle_command(args), args.timeout_seconds),
        "rbac_jwks": _run_probe(_rbac_jwks_command(args), args.timeout_seconds),
        "mcp_oauth": _run_probe(_mcp_oauth_command(args), args.timeout_seconds),
        "container_sandbox": _run_probe(_container_sandbox_command(args), args.timeout_seconds),
        "notes": [],
    }
    evidence["ok"] = all(
        _probe_ok(evidence[section])
        for section in ["oracle", "rbac_jwks", "mcp_oauth", "container_sandbox"]
    )
    output = json.dumps(evidence, ensure_ascii=False, indent=2, sort_keys=True)
    if args.output:
        Path(args.output).write_text(f"{output}\n", encoding="utf-8")
    if args.summary_markdown:
        Path(args.summary_markdown).write_text(_markdown_summary(evidence), encoding="utf-8")
    print(output)
    return 0 if evidence["ok"] else 3


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", choices=["dry-run", "live"], default="dry-run")
    parser.add_argument("--environment", default="local")
    parser.add_argument("--validator", default="agent-runtime-validation")
    parser.add_argument("--output", default="")
    parser.add_argument("--summary-markdown", default="")
    parser.add_argument("--timeout-seconds", type=float, default=30.0)
    parser.add_argument("--oracle-runs", type=int, default=1000)
    parser.add_argument("--oracle-audit-iterations", type=int, default=20)
    parser.add_argument("--oracle-audit-limit", type=int, default=100)
    parser.add_argument("--oracle-sla-write-ms", type=int, default=60_000)
    parser.add_argument("--oracle-sla-audit-p95-ms", type=int, default=1000)
    parser.add_argument("--rotation-interval-seconds", type=float, default=0.0)
    parser.add_argument("--container-runtime", default="docker")
    parser.add_argument("--container-image", default="busybox:latest")
    parser.add_argument("--container-userns", default="private")
    parser.add_argument("--container-user", default="65532:65532")
    args = parser.parse_args()
    if args.timeout_seconds <= 0:
        parser.error("--timeout-seconds must be greater than 0")
    if args.oracle_runs < 1:
        parser.error("--oracle-runs must be greater than or equal to 1")
    if args.oracle_audit_iterations < 1:
        parser.error("--oracle-audit-iterations must be greater than or equal to 1")
    if args.oracle_audit_limit < 1:
        parser.error("--oracle-audit-limit must be greater than or equal to 1")
    if args.rotation_interval_seconds < 0:
        parser.error("--rotation-interval-seconds must be greater than or equal to 0")
    return args


def _oracle_command(args: argparse.Namespace) -> list[str]:
    command = [
        sys.executable,
        str(_script_path("agent_runtime_oracle_load_check.py")),
        "--runs",
        str(args.oracle_runs),
        "--audit-iterations",
        str(args.oracle_audit_iterations),
        "--audit-limit",
        str(args.oracle_audit_limit),
        "--sla-write-ms",
        str(args.oracle_sla_write_ms),
        "--sla-audit-p95-ms",
        str(args.oracle_sla_audit_p95_ms),
    ]
    if args.mode == "dry-run":
        command.append("--dry-run")
    return command


def _rbac_jwks_command(args: argparse.Namespace) -> list[str]:
    command = [
        sys.executable,
        str(_script_path("agent_runtime_gateway_jwks_check.py")),
        "--rotation-check",
        "--rotation-interval-seconds",
        str(args.rotation_interval_seconds),
    ]
    if args.mode == "dry-run":
        command.append("--dry-run")
    else:
        command.append("--backend-status")
    return command


def _mcp_oauth_command(args: argparse.Namespace) -> list[str]:
    command = [
        sys.executable,
        str(_script_path("agent_runtime_mcp_oauth_check.py")),
        "--tools-list",
        "--require-oauth",
        "--require-jwks",
        "--rotation-check",
        "--rotation-interval-seconds",
        str(args.rotation_interval_seconds),
    ]
    if args.mode == "dry-run":
        command.append("--dry-run")
    return command


def _container_sandbox_command(args: argparse.Namespace) -> list[str]:
    command = [
        sys.executable,
        str(_script_path("agent_runtime_container_sandbox_check.py")),
        "--runtime",
        args.container_runtime,
        "--image",
        args.container_image,
        "--network",
        "none",
        "--security-opt",
        "seccomp=default",
        "--userns",
        args.container_userns,
        "--user",
        args.container_user,
        "--require-rootless",
        "--require-seccomp",
        "--require-no-new-privileges",
        "--require-network-none",
    ]
    if args.mode == "dry-run":
        command.append("--dry-run")
    return command


def _run_probe(command: list[str], timeout_seconds: float) -> JsonObject:
    try:
        completed = subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
            shell=False,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        return {
            "ok": False,
            "exit_code": None,
            "error": "timeout",
            "command": _safe_command(command),
            "stdout": _trim(exc.stdout),
            "stderr": _trim(exc.stderr),
        }
    payload = _json_or_error(completed.stdout)
    return {
        "ok": completed.returncode == 0 and bool(payload.get("ok")),
        "exit_code": completed.returncode,
        "command": _safe_command(command),
        "payload": payload,
        "stderr": _trim(completed.stderr),
    }


def _json_or_error(value: str) -> JsonObject:
    try:
        payload = json.loads(value)
    except json.JSONDecodeError:
        return {"ok": False, "error": "invalid_json", "raw": _trim(value)}
    return payload if isinstance(payload, dict) else {"ok": False, "error": "non_object_json"}


def _probe_ok(probe: object) -> bool:
    return isinstance(probe, dict) and probe.get("ok") is True


def _markdown_summary(evidence: JsonObject) -> str:
    lines = [
        "# Agent Runtime Validation Evidence",
        "",
        f"- Environment: `{evidence.get('environment', '')}`",
        f"- Validator: `{evidence.get('validator', '')}`",
        f"- Mode: `{evidence.get('mode', '')}`",
        f"- Overall OK: `{evidence.get('ok')}`",
        f"- Validated at: `{evidence.get('validated_at', '')}`",
        "",
        "| Section | OK | Exit Code | Payload OK | Dry Run |",
        "|---|---:|---:|---:|---:|",
    ]
    for section in ["oracle", "rbac_jwks", "mcp_oauth", "container_sandbox"]:
        probe = evidence.get(section, {})
        payload = probe.get("payload", {}) if isinstance(probe, dict) else {}
        lines.append(
            "| "
            + " | ".join(
                [
                    section,
                    str(probe.get("ok") if isinstance(probe, dict) else None),
                    str(probe.get("exit_code") if isinstance(probe, dict) else None),
                    str(payload.get("ok") if isinstance(payload, dict) else None),
                    str(payload.get("dry_run") if isinstance(payload, dict) else None),
                ]
            )
            + " |"
        )
    lines.append("")
    return "\n".join(lines)


def _script_path(name: str) -> Path:
    return Path(__file__).resolve().parent / name


def _safe_command(command: list[str]) -> list[str]:
    redacted: list[str] = []
    redact_next = False
    for token in command:
        if redact_next:
            redacted.append("***")
            redact_next = False
            continue
        redacted.append(token)
        if token in {"--oauth-client-secret", "--api-key", "--policy-api-key", "--sample-token"}:
            redact_next = True
    return redacted


def _trim(value: object, limit: int = 2000) -> str:
    text = value.decode("utf-8", errors="replace") if isinstance(value, bytes) else str(value or "")
    return text if len(text) <= limit else f"{text[:limit]}...[truncated]"


if __name__ == "__main__":
    raise SystemExit(main())
