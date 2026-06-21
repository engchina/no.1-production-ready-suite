"""Validate Agent Runtime production evidence JSON.

Default mode is release-strict: dry-run evidence is rejected. Use
`--allow-dry-run` only for CI contract tests or local rehearsal.
"""

from __future__ import annotations

import argparse
import json
import re
from collections.abc import Mapping
from pathlib import Path
from typing import Any

JsonObject = dict[str, Any]
DEFAULT_MANIFEST_PATH = (
    Path(__file__).resolve().parents[2]
    / "docs"
    / "agent-runtime-production-validation.manifest.json"
)
SECTIONS = ("oracle", "rbac_jwks", "mcp_oauth", "container_sandbox")
SECRET_PATTERNS = (
    ("bearer_token", re.compile(r"\bBearer\s+[A-Za-z0-9._~+/=-]{12,}", re.IGNORECASE)),
    ("jwt_token", re.compile(r"\beyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\b")),
    (
        "secret_assignment",
        re.compile(
            r"\b(password|passwd|secret|token|api[_-]?key|credential)" r"\s*[:=]\s*[^\s,;]{8,}",
            re.IGNORECASE,
        ),
    ),
)


def main() -> int:
    args = _parse_args()
    evidence = _load_json(Path(args.evidence_file))
    manifest = _load_json(Path(args.manifest))
    violations = _validate_evidence(
        evidence,
        allow_dry_run=args.allow_dry_run,
        manifest=manifest,
    )
    summary = {
        "ok": not violations,
        "evidence_file": args.evidence_file,
        "manifest_file": args.manifest,
        "allow_dry_run": args.allow_dry_run,
        "mode": evidence.get("mode") if isinstance(evidence, dict) else None,
        "violations": violations,
    }
    print(json.dumps(summary, ensure_ascii=False, sort_keys=True))
    return 0 if not violations else 3


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("evidence_file")
    parser.add_argument("--allow-dry-run", action="store_true")
    parser.add_argument(
        "--manifest",
        default=str(DEFAULT_MANIFEST_PATH),
        help="Production validation manifest containing release-gate evidence paths.",
    )
    return parser.parse_args()


def _load_json(path: Path) -> JsonObject:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {"_load_error": "file_not_found"}
    except json.JSONDecodeError as exc:
        return {"_load_error": "invalid_json", "message": str(exc)}
    return payload if isinstance(payload, dict) else {"_load_error": "non_object_json"}


def _validate_evidence(
    evidence: JsonObject,
    *,
    allow_dry_run: bool,
    manifest: JsonObject,
) -> list[str]:
    violations: list[str] = []
    if evidence.get("_load_error"):
        return [str(evidence["_load_error"])]
    manifest_violations = _validate_manifest(manifest)
    violations.extend(manifest_violations)
    required_paths = (
        _required_paths_by_section(manifest)
        if not manifest_violations
        else {section: () for section in SECTIONS}
    )
    if evidence.get("ok") is not True:
        violations.append("top_level_not_ok")
    violations.extend(_secret_scan_violations(evidence))
    mode = evidence.get("mode")
    if mode != "live" and not allow_dry_run:
        violations.append("mode_not_live")
    for key in ("validated_at", "environment", "validator"):
        if not isinstance(evidence.get(key), str) or not evidence.get(key):
            violations.append(f"missing_{key}")
    for section in SECTIONS:
        section_value = evidence.get(section)
        if not isinstance(section_value, dict):
            violations.append(f"{section}.missing")
            continue
        violations.extend(
            _validate_section(
                section,
                section_value,
                allow_dry_run=allow_dry_run,
                required_paths=required_paths[section],
            )
        )
    return violations


def _validate_manifest(manifest: JsonObject) -> list[str]:
    violations: list[str] = []
    if manifest.get("_load_error"):
        return [f"manifest.{manifest['_load_error']}"]
    if manifest.get("required_mode") != "live":
        violations.append("manifest.required_mode_not_live")
    release_gate = manifest.get("release_gate")
    if not isinstance(release_gate, Mapping):
        violations.append("manifest.release_gate_missing")
    else:
        if release_gate.get("rejects_dry_run") is not True:
            violations.append("manifest.release_gate.rejects_dry_run_not_true")
        if release_gate.get("secret_scan") is not True:
            violations.append("manifest.release_gate.secret_scan_not_true")
    required_sections = manifest.get("required_sections")
    if not isinstance(required_sections, Mapping):
        violations.append("manifest.required_sections_missing")
        return violations
    for section in SECTIONS:
        section_config = required_sections.get(section)
        if not isinstance(section_config, Mapping):
            violations.append(f"manifest.{section}.missing")
            continue
        required_paths = section_config.get("required_evidence_paths")
        if (
            not isinstance(required_paths, list)
            or not required_paths
            or not all(isinstance(path, str) and path for path in required_paths)
        ):
            violations.append(f"manifest.{section}.required_evidence_paths_invalid")
    return violations


def _required_paths_by_section(manifest: Mapping[str, Any]) -> dict[str, tuple[str, ...]]:
    required_sections = manifest.get("required_sections")
    if not isinstance(required_sections, Mapping):
        return {section: () for section in SECTIONS}

    paths_by_section: dict[str, tuple[str, ...]] = {}
    for section in SECTIONS:
        section_config = required_sections.get(section)
        if not isinstance(section_config, Mapping):
            paths_by_section[section] = ()
            continue
        raw_paths = section_config.get("required_evidence_paths")
        if not isinstance(raw_paths, list):
            paths_by_section[section] = ()
            continue
        section_prefix = f"{section}."
        paths_by_section[section] = tuple(
            path.removeprefix(section_prefix)
            for path in raw_paths
            if isinstance(path, str) and path
        )
    return paths_by_section


def _validate_section(
    section: str,
    value: Mapping[str, Any],
    *,
    allow_dry_run: bool,
    required_paths: tuple[str, ...],
) -> list[str]:
    violations: list[str] = []
    if value.get("ok") is not True:
        violations.append(f"{section}.not_ok")
    payload = value.get("payload")
    if not isinstance(payload, dict):
        violations.append(f"{section}.payload_missing")
        return violations
    if payload.get("ok") is not True:
        violations.append(f"{section}.payload_not_ok")
    if payload.get("dry_run") is True and not allow_dry_run:
        violations.append(f"{section}.dry_run_payload")
    if allow_dry_run and payload.get("dry_run") is True:
        return violations
    for path in required_paths:
        if _nested_value(value, path) is None:
            violations.append(f"{section}.missing_{path}")
    return violations


def _nested_value(value: Mapping[str, Any], dotted_path: str) -> object | None:
    current: object = value
    for part in dotted_path.split("."):
        if not isinstance(current, Mapping) or part not in current:
            return None
        current = current[part]
    return current


def _secret_scan_violations(value: object, path: str = "$") -> list[str]:
    violations: list[str] = []
    if isinstance(value, str):
        for name, pattern in SECRET_PATTERNS:
            if pattern.search(value):
                violations.append(f"secret_leak:{path}:{name}")
        return violations
    if isinstance(value, Mapping):
        for key, child in value.items():
            key_text = str(key).replace(".", "_")
            violations.extend(_secret_scan_violations(child, f"{path}.{key_text}"))
        return violations
    if isinstance(value, list):
        for index, child in enumerate(value):
            violations.extend(_secret_scan_violations(child, f"{path}[{index}]"))
    return violations


if __name__ == "__main__":
    raise SystemExit(main())
