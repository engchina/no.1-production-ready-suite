"""Container sandbox validation for `sandbox_command_run`.

Dry-run reports the resolved command. Live mode checks Docker/Podman
availability and, unless disabled, runs a small container smoke command with the
workspace mounted at `/workspace`. Optional gates can require rootless runtime,
seccomp, `no-new-privileges`, and `--network none`.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any


def main() -> int:
    args = _parse_args()
    workspace_root = Path(args.workspace_root).resolve()
    smoke_command = _container_smoke_command(args, workspace_root)
    if args.dry_run:
        return _print(
            {
                "ok": True,
                "dry_run": True,
                "runtime": args.runtime,
                "workspace_root": str(workspace_root),
                "image": args.image,
                "network": args.network,
                "security_opts": args.security_opt,
                "userns": args.userns,
                "user": args.user,
                "require_rootless": args.require_rootless,
                "require_seccomp": args.require_seccomp,
                "require_no_new_privileges": args.require_no_new_privileges,
                "require_network_none": args.require_network_none,
                "smoke_command": smoke_command,
            }
        )

    summary: dict[str, Any] = {
        "ok": True,
        "dry_run": False,
        "runtime": args.runtime,
        "workspace_root": str(workspace_root),
        "image": args.image,
        "checks": {},
    }
    summary["checks"]["runtime_version"] = _run(
        _runtime_version_command(args.runtime),
        args.timeout_seconds,
    )
    summary["checks"]["runtime_info"] = _run(
        _runtime_info_command(args.runtime),
        args.timeout_seconds,
    )
    summary["checks"]["security_profile"] = _security_profile_check(
        args,
        summary["checks"]["runtime_info"],
    )
    if not args.skip_smoke:
        summary["checks"]["smoke"] = _run(smoke_command, args.timeout_seconds)
    summary["ok"] = all(check["ok"] for check in summary["checks"].values())
    return _print(summary) if summary["ok"] else _print_error(summary)


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runtime", default=os.getenv("AGENT_COMMAND_CONTAINER_RUNTIME", "docker"))
    parser.add_argument(
        "--workspace-root",
        default=os.getenv("AGENT_COMMAND_WORKSPACE_ROOT", "."),
    )
    parser.add_argument(
        "--image",
        default=os.getenv("AGENT_COMMAND_CONTAINER_IMAGE") or "alpine:3.20",
    )
    parser.add_argument(
        "--network",
        default=os.getenv("AGENT_COMMAND_CONTAINER_NETWORK", "none"),
    )
    parser.add_argument(
        "--security-opt",
        action="append",
        default=_split_csv(
            os.getenv("AGENT_COMMAND_CONTAINER_SECURITY_OPTS", "no-new-privileges:true")
        ),
    )
    parser.add_argument("--userns", default=os.getenv("AGENT_COMMAND_CONTAINER_USERNS", ""))
    parser.add_argument("--user", default=os.getenv("AGENT_COMMAND_CONTAINER_USER", ""))
    parser.add_argument("--timeout-seconds", type=float, default=15.0)
    parser.add_argument("--require-rootless", action="store_true")
    parser.add_argument("--require-seccomp", action="store_true")
    parser.add_argument("--require-no-new-privileges", action="store_true")
    parser.add_argument("--require-network-none", action="store_true")
    parser.add_argument("--skip-smoke", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    if args.timeout_seconds <= 0:
        parser.error("--timeout-seconds must be greater than 0")
    if not args.runtime.strip():
        parser.error("--runtime must not be empty")
    args.security_opt = [opt for opt in args.security_opt if opt]
    return args


def _container_smoke_command(args: argparse.Namespace, workspace_root: Path) -> list[str]:
    command = [
        args.runtime,
        "run",
        "--rm",
        "--network",
        args.network,
    ]
    for security_opt in args.security_opt:
        command.extend(["--security-opt", security_opt])
    if args.userns:
        command.extend(["--userns", args.userns])
    if args.user:
        command.extend(["--user", args.user])
    command.extend(
        [
            "-v",
            f"{workspace_root}:/workspace:rw",
            "-w",
            "/workspace",
            args.image,
            "sh",
            "-c",
            "pwd && test -d /workspace",
        ]
    )
    return command


def _runtime_version_command(runtime: str) -> list[str]:
    if Path(runtime).name == "docker":
        return [runtime, "version", "--format", "{{json .}}"]
    return [runtime, "version", "--format", "json"]


def _runtime_info_command(runtime: str) -> list[str]:
    if Path(runtime).name == "docker":
        return [runtime, "info", "--format", "{{json .}}"]
    return [runtime, "info", "--format", "json"]


def _run(command: list[str], timeout_seconds: float) -> dict[str, Any]:
    try:
        completed = subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
            shell=False,
            check=False,
        )
    except FileNotFoundError as exc:
        return {"ok": False, "error": "command_not_found", "message": str(exc)}
    except subprocess.TimeoutExpired as exc:
        return {
            "ok": False,
            "error": "timeout",
            "stdout": _trim(exc.stdout),
            "stderr": _trim(exc.stderr),
        }
    return {
        "ok": completed.returncode == 0,
        "exit_code": completed.returncode,
        "stdout": _trim(completed.stdout),
        "stderr": _trim(completed.stderr),
    }


def _trim(value: object, limit: int = 2000) -> str:
    text = value.decode("utf-8", errors="replace") if isinstance(value, bytes) else str(value or "")
    return text if len(text) <= limit else f"{text[:limit]}...[truncated]"


def _security_profile_check(
    args: argparse.Namespace,
    runtime_info: dict[str, Any],
) -> dict[str, Any]:
    info_text = str(runtime_info.get("stdout", "")).lower()
    security_opts = [opt.lower() for opt in args.security_opt]
    properties = {
        "rootless": "rootless" in info_text,
        "seccomp": "seccomp" in info_text,
        "no_new_privileges": "no-new-privileges" in " ".join(security_opts),
        "network_none": args.network == "none",
        "userns_configured": bool(args.userns),
        "user_configured": bool(args.user),
    }
    missing: list[str] = []
    if args.require_rootless and not properties["rootless"]:
        missing.append("rootless")
    if args.require_seccomp and not properties["seccomp"]:
        missing.append("seccomp")
    if args.require_no_new_privileges and not properties["no_new_privileges"]:
        missing.append("no_new_privileges")
    if args.require_network_none and not properties["network_none"]:
        missing.append("network_none")
    return {
        "ok": not missing,
        "properties": properties,
        "missing": missing,
        "security_opts": args.security_opt,
        "userns": args.userns,
        "user": args.user,
    }


def _split_csv(value: str) -> list[str]:
    return [item.strip() for item in value.split(",") if item.strip()]


def _print(payload: dict[str, Any]) -> int:
    print(json.dumps(payload, ensure_ascii=False, sort_keys=True))
    return 0


def _print_error(payload: dict[str, Any]) -> int:
    print(json.dumps(payload, ensure_ascii=False, sort_keys=True), file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
