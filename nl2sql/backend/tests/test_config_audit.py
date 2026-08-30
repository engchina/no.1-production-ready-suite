"""Configuration catalog / secret boundary audit tests."""

from __future__ import annotations

import json
from pathlib import Path

from app.config_audit import ENV_ASSIGNMENT_RE, audit_configuration, stable_audit_json
from app.settings import Settings


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


def test_audit_rejects_deepsec_with_thick_driver(tmp_path: Path) -> None:
    backend_dir = Path(__file__).resolve().parents[1]
    env_file = tmp_path / ".env"
    env_file.write_text(
        "ORACLE_DEEPSEC_ENABLED=true\n" "ORACLE_DRIVER_MODE=thick\n",
        encoding="utf-8",
    )
    env_file.chmod(0o600)

    result = audit_configuration(
        example_path=backend_dir / ".env.example",
        env_path=env_file,
        model_settings_path=tmp_path / "missing.json",
    )
    codes = {item.code for item in result.findings}

    assert result.ok is False
    assert "DEEPSEC_REQUIRES_THIN_DRIVER" in codes


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


def test_terraform_cloud_init_keeps_thin_mtls_without_instant_client() -> None:
    repo_root = Path(__file__).resolve().parents[2]
    locals_tf = (repo_root / "terraform" / "stack" / "locals.tf").read_text(encoding="utf-8")
    init_script = (repo_root / "init_script.sh").read_text(encoding="utf-8").lower()
    dockerfile = (repo_root / "backend" / "Dockerfile").read_text(encoding="utf-8").lower()

    assert "ORACLE_DEEPSEC_ENABLED=true" in locals_tf
    assert "ORACLE_DRIVER_MODE=thin" in locals_tf
    assert "ORACLE_CLIENT_LIB_DIR=" in locals_tf
    assert "instantclient" not in init_script
    assert "instantclient" not in dockerfile


def _terraform_backend_env_body() -> str:
    """locals.tf の backend_env heredoc 本文だけを取り出す。"""
    repo_root = Path(__file__).resolve().parents[2]
    locals_tf = (repo_root / "terraform" / "stack" / "locals.tf").read_text(encoding="utf-8")
    _, _, after = locals_tf.partition("backend_env = <<-EOT\n")
    assert after, "locals.tf に backend_env heredoc が見つかりません。"
    body, _, _ = after.partition("\nEOT")
    return body


def test_terraform_backend_env_renders_select_ai_credential_name() -> None:
    """Select AI credential 名は env 専用キーのため Terraform が書き込む必要がある。"""
    assert "NL2SQL_SELECT_AI_CREDENTIAL_NAME=OCI_CRED" in _terraform_backend_env_body()


def test_terraform_backend_env_keys_are_known_settings() -> None:
    """Terraform が撒くキーは Settings に存在する（未知キーは audit の error になる）。"""
    known_keys = {name.upper() for name in Settings.model_fields}
    rendered_keys = {
        match.group("key")
        for line in _terraform_backend_env_body().splitlines()
        if (match := ENV_ASSIGNMENT_RE.match(line)) is not None
    }

    assert rendered_keys, "backend_env から key を抽出できませんでした。"
    assert rendered_keys <= known_keys
