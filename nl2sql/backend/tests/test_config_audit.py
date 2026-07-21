"""Configuration catalog / secret boundary audit tests."""

from __future__ import annotations

import json
from pathlib import Path

from app.config_audit import audit_configuration, stable_audit_json


def test_repository_env_example_is_complete_and_secret_free(tmp_path: Path) -> None:
    backend_dir = Path(__file__).resolve().parents[1]

    result = audit_configuration(
        example_path=backend_dir / ".env.example",
        env_path=tmp_path / "missing.env",
        model_settings_path=tmp_path / "missing-model-settings.json",
    )

    assert result.ok is True
    assert result.findings == []


def test_audit_reports_unknown_duplicate_malformed_and_security_combinations(
    tmp_path: Path,
) -> None:
    backend_dir = Path(__file__).resolve().parents[1]
    example = backend_dir / ".env.example"
    env_file = tmp_path / ".env"
    env_file.write_text(
        "ENVIRONMENT=production\n"
        "DEBUG=true\n"
        "DEBUG=false\n"
        "APP_AUTH_ENABLED=true\n"
        "APP_AUTH_COOKIE_SECURE=false\n"
        "UNKNOWN_SETTING=value\n"
        "not an assignment\n",
        encoding="utf-8",
    )
    env_file.chmod(0o600)

    result = audit_configuration(
        example_path=example,
        env_path=env_file,
        model_settings_path=tmp_path / "missing.json",
    )
    codes = {item.code for item in result.findings}

    assert result.ok is False
    assert "ENV_ACTUAL_DUPLICATE_KEYS" in codes
    assert "ENV_ACTUAL_MALFORMED_LINES" in codes
    assert "ENV_ACTUAL_UNKNOWN_KEYS" in codes
    assert "NONLOCAL_AUTH_COOKIE_NOT_SECURE" in codes


def test_audit_detects_legacy_json_without_disclosing_secret(tmp_path: Path) -> None:
    backend_dir = Path(__file__).resolve().parents[1]
    model_settings = tmp_path / "model-settings.json"
    model_settings.write_text(
        json.dumps(
            {
                "version": 1,
                "enterprise_ai": {"api_key": "do-not-disclose"},
            }
        ),
        encoding="utf-8",
    )
    model_settings.chmod(0o600)

    result = audit_configuration(
        example_path=backend_dir / ".env.example",
        env_path=tmp_path / "missing.env",
        model_settings_path=model_settings,
    )
    output = stable_audit_json(result)

    assert "MODEL_SETTINGS_LEGACY_SECRET" in output
    assert "MODEL_SETTINGS_VERSION_LEGACY" in output
    assert "do-not-disclose" not in output
