"""`.env` / `.env.example` / model-settings.json の read-only audit。"""

from __future__ import annotations

import json
import re
import stat
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

from app.settings import Settings

Severity = Literal["error", "warning", "info"]
ENV_ASSIGNMENT_RE = re.compile(
    r"^\s*(?:export\s+)?(?P<key>[A-Za-z_][A-Za-z0-9_]*)\s*=\s*(?P<value>.*)$"
)
EXAMPLE_EMPTY_KEYS = frozenset(
    {
        "APP_AUTH_SECRET",
        "OCI_COMPARTMENT_ID",
        "OCI_ENTERPRISE_AI_API_KEY",
        "OCI_ENTERPRISE_AI_PROJECT_OCID",
        "OCI_FINGERPRINT",
        "OCI_KEY_FILE",
        "OCI_TENANCY_OCID",
        "OCI_USER_OCID",
        "ORACLE_ADB_OCID",
        "ORACLE_DEEPSEC_END_USER_PASSWORD",
        "ORACLE_DSN",
        "ORACLE_PASSWORD",
        "ORACLE_WALLET_PASSWORD",
    }
)


@dataclass(frozen=True)
class EnvDocument:
    """値を外へ公開せず audit 内だけで扱う env document。"""

    values: dict[str, str]
    duplicates: tuple[str, ...] = ()
    malformed_lines: tuple[int, ...] = ()


@dataclass(frozen=True)
class AuditFinding:
    severity: Severity
    code: str
    keys: tuple[str, ...] = ()
    lines: tuple[int, ...] = ()

    def to_dict(self) -> dict[str, object]:
        result: dict[str, object] = {"code": self.code, "severity": self.severity}
        if self.keys:
            result["keys"] = list(self.keys)
        if self.lines:
            result["lines"] = list(self.lines)
        return result


@dataclass
class ConfigAuditResult:
    findings: list[AuditFinding] = field(default_factory=list)
    actual_env_present: bool = False
    model_settings_present: bool = False
    overridden_keys: tuple[str, ...] = ()

    @property
    def ok(self) -> bool:
        return not any(item.severity == "error" for item in self.findings)

    def to_dict(self) -> dict[str, object]:
        ordered = sorted(
            self.findings,
            key=lambda item: (item.severity, item.code, item.keys, item.lines),
        )
        return {
            "actual_env_present": self.actual_env_present,
            "findings": [item.to_dict() for item in ordered],
            "model_settings_present": self.model_settings_present,
            "ok": self.ok,
            "overridden_keys": list(self.overridden_keys),
        }


def parse_env_document(path: Path) -> EnvDocument:
    """dotenv の assignment key/value と構文エラー位置だけを取り出す。"""
    values: dict[str, str] = {}
    duplicates: set[str] = set()
    malformed: list[int] = []
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        match = ENV_ASSIGNMENT_RE.match(line)
        if match is None:
            malformed.append(line_number)
            continue
        key = match.group("key")
        if key in values:
            duplicates.add(key)
        values[key] = match.group("value").strip()
    return EnvDocument(
        values=values,
        duplicates=tuple(sorted(duplicates)),
        malformed_lines=tuple(malformed),
    )


def audit_configuration(
    *,
    example_path: Path,
    env_path: Path,
    model_settings_path: Path,
) -> ConfigAuditResult:
    """3 設定ファイルを変更せず、secret value を返さずに検査する。"""
    result = ConfigAuditResult(
        actual_env_present=env_path.is_file(),
        model_settings_present=model_settings_path.is_file(),
    )
    if not example_path.is_file():
        result.findings.append(AuditFinding("error", "ENV_EXAMPLE_MISSING"))
        return result

    known_keys = frozenset(name.upper() for name in Settings.model_fields)
    example = parse_env_document(example_path)
    _audit_env_document(result, example, known_keys, source="example")

    missing_template_keys = tuple(sorted(known_keys - example.values.keys()))
    if missing_template_keys:
        result.findings.append(
            AuditFinding("error", "ENV_EXAMPLE_FIELDS_MISSING", missing_template_keys)
        )
    populated_sensitive_keys = tuple(
        sorted(
            key
            for key in EXAMPLE_EMPTY_KEYS
            if key in example.values and _unquote(example.values[key])
        )
    )
    if populated_sensitive_keys:
        result.findings.append(
            AuditFinding(
                "error",
                "ENV_EXAMPLE_SENSITIVE_VALUE_NONEMPTY",
                populated_sensitive_keys,
            )
        )

    actual = EnvDocument(values={})
    if env_path.is_file():
        actual = parse_env_document(env_path)
        _audit_env_document(result, actual, known_keys, source="actual")
        result.overridden_keys = tuple(
            sorted(
                key
                for key, value in actual.values.items()
                if key in example.values and value != example.values[key]
            )
        )
        _audit_env_permissions(result, env_path)

    effective = dict(example.values)
    effective.update(actual.values)
    _audit_security_combinations(result, effective)
    _audit_model_settings(result, model_settings_path)
    return result


def _audit_env_document(
    result: ConfigAuditResult,
    document: EnvDocument,
    known_keys: frozenset[str],
    *,
    source: Literal["example", "actual"],
) -> None:
    prefix = "ENV_EXAMPLE" if source == "example" else "ENV_ACTUAL"
    if document.duplicates:
        result.findings.append(
            AuditFinding("error", f"{prefix}_DUPLICATE_KEYS", document.duplicates)
        )
    if document.malformed_lines:
        result.findings.append(
            AuditFinding("error", f"{prefix}_MALFORMED_LINES", lines=document.malformed_lines)
        )
    unknown_keys = tuple(sorted(document.values.keys() - known_keys))
    if unknown_keys:
        result.findings.append(AuditFinding("error", f"{prefix}_UNKNOWN_KEYS", unknown_keys))


def _audit_security_combinations(
    result: ConfigAuditResult,
    effective: dict[str, str],
) -> None:
    environment = _unquote(effective.get("ENVIRONMENT", "local")).lower()
    debug = _as_bool(effective.get("DEBUG", "false"))
    auth_enabled = _as_bool(effective.get("APP_AUTH_ENABLED", "true"))
    cookie_secure = _as_bool(effective.get("APP_AUTH_COOKIE_SECURE", "false"))
    if environment != "local" and debug:
        result.findings.append(AuditFinding("error", "NONLOCAL_DEBUG_ENABLED", ("DEBUG",)))
    if environment != "local" and auth_enabled and not cookie_secure:
        result.findings.append(
            AuditFinding(
                "error",
                "NONLOCAL_AUTH_COOKIE_NOT_SECURE",
                ("APP_AUTH_COOKIE_SECURE",),
            )
        )


def _audit_model_settings(result: ConfigAuditResult, path: Path) -> None:
    if not path.is_file():
        return
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        result.findings.append(AuditFinding("error", "MODEL_SETTINGS_INVALID_JSON"))
        return
    if not isinstance(document, dict):
        result.findings.append(AuditFinding("error", "MODEL_SETTINGS_INVALID_ROOT"))
        return
    version = document.get("version")
    enterprise = document.get("enterprise_ai")
    legacy_secret = isinstance(enterprise, dict) and "api_key" in enterprise
    if legacy_secret:
        result.findings.append(
            AuditFinding("error", "MODEL_SETTINGS_LEGACY_SECRET", ("enterprise_ai.api_key",))
        )
    if version != 2:
        result.findings.append(AuditFinding("warning", "MODEL_SETTINGS_VERSION_LEGACY"))
    _audit_env_permissions(result, path, code="MODEL_SETTINGS_PERMISSIONS_NOT_0600")


def _audit_env_permissions(
    result: ConfigAuditResult,
    path: Path,
    *,
    code: str = "ENV_ACTUAL_PERMISSIONS_NOT_0600",
) -> None:
    try:
        mode = stat.S_IMODE(path.stat().st_mode)
    except OSError:
        result.findings.append(AuditFinding("error", f"{code}_UNREADABLE"))
        return
    if mode != 0o600:
        result.findings.append(AuditFinding("error", code))


def _unquote(value: str) -> str:
    normalized = value.strip()
    if len(normalized) >= 2 and normalized[0] == normalized[-1] and normalized[0] in {"'", '"'}:
        return normalized[1:-1].strip()
    return normalized


def _as_bool(value: str) -> bool:
    return _unquote(value).lower() in {"1", "true", "yes", "on"}


def stable_audit_json(result: ConfigAuditResult) -> str:
    """CI と運用で diff しやすい stable JSON。"""
    return json.dumps(result.to_dict(), ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def default_audit_paths(backend_dir: Path) -> tuple[Path, Path, Path]:
    """backend root 基準の既定 audit target。"""
    return (
        backend_dir / ".env.example",
        backend_dir / ".env",
        backend_dir / "model-settings.json",
    )
