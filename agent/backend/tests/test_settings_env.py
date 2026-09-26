"""Settings の環境変数名と共通 `.env` の読み込み（#211）。"""

from pathlib import Path

import pytest
from pr_backend_core.config import settings_env_names

import app.settings as app_settings
from app.settings import Settings, resolve_model_settings_file


def test_settings_env_names_split_platform_and_agent() -> None:
    names = settings_env_names(Settings)

    assert names["oracle_dsn"] == "PLATFORM_ORACLE_DSN"
    assert names["oci_region"] == "PLATFORM_OCI_REGION"
    assert names["local_storage_dir"] == "PLATFORM_LOCAL_STORAGE_DIR"
    assert names["model_settings_file"] == "PLATFORM_MODEL_SETTINGS_FILE"
    assert names["oci_enterprise_ai_api_key"] == "PLATFORM_OCI_ENTERPRISE_AI_API_KEY"
    assert names["service_name"] == "AGENT_SERVICE_NAME"
    assert names["log_level"] == "AGENT_LOG_LEVEL"
    assert names["cors_origins"] == "AGENT_CORS_ORIGINS"
    assert names["max_upload_bytes"] == "AGENT_MAX_UPLOAD_BYTES"
    # 既に AGENT_ で始まる属性は接頭辞を重ねない。
    assert names["agent_runtime_oracle_dsn"] == "AGENT_RUNTIME_ORACLE_DSN"
    assert all(name.startswith(("PLATFORM_", "AGENT_")) for name in names.values())


def test_settings_reads_platform_env_then_backend_env(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    for name in ("PLATFORM_ORACLE_DSN", "AGENT_LOG_LEVEL", "ORACLE_DSN", "LOG_LEVEL"):
        monkeypatch.delenv(name, raising=False)
    platform_env = tmp_path / "platform.env"
    platform_env.write_text("PLATFORM_ORACLE_DSN=shared_high\n", encoding="utf-8")
    backend_env = tmp_path / "backend.env"
    backend_env.write_text("AGENT_LOG_LEVEL=DEBUG\n", encoding="utf-8")

    settings = Settings(_env_file=(platform_env, backend_env))

    assert settings.oracle_dsn == "shared_high"
    assert settings.log_level == "DEBUG"


def test_legacy_env_names_are_ignored(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    for name in ("PLATFORM_OCI_REGION", "AGENT_LOG_LEVEL", "LOG_LEVEL"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("OCI_REGION", "ap-osaka-1")
    legacy_env = tmp_path / "legacy.env"
    legacy_env.write_text("LOG_LEVEL=DEBUG\n", encoding="utf-8")

    settings = Settings(_env_file=legacy_env)

    assert settings.oci_region == ""
    assert settings.log_level == "INFO"


def test_model_settings_file_resolves_next_to_platform_env(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(app_settings, "PLATFORM_ENV_FILE", tmp_path / "platform" / ".env")

    assert (
        resolve_model_settings_file("model-settings.json")
        == (tmp_path / "platform" / "model-settings.json").resolve()
    )
    absolute = tmp_path / "data" / "model-settings.json"
    assert resolve_model_settings_file(str(absolute)) == absolute
