"""共通 `.env` と環境変数名の規則（#211）。"""

from pathlib import Path

import pytest

from pr_backend_core.config import (
    BaseServiceSettings,
    platform_env_file,
    product_settings_config,
    settings_env_names,
)


def _settings_class(tmp_path: Path) -> type[BaseServiceSettings]:
    backend_dir = tmp_path / "rag" / "backend"
    backend_dir.mkdir(parents=True)

    class Settings(BaseServiceSettings):
        model_config = product_settings_config(prefix="RAG_", backend_dir=backend_dir)

        oracle_dsn: str = ""
        app_auth_idle_timeout_minutes: int = 60
        auth_mode: str = "local"
        rag_chunk_size: int = 1

    return Settings


def test_env_names_split_platform_and_product(tmp_path: Path) -> None:
    names = settings_env_names(_settings_class(tmp_path))
    assert names["oracle_dsn"] == "PLATFORM_ORACLE_DSN"
    assert names["app_auth_idle_timeout_minutes"] == "PLATFORM_AUTH_IDLE_TIMEOUT_MINUTES"
    assert names["auth_mode"] == "RAG_AUTH_MODE"
    assert names["rag_chunk_size"] == "RAG_CHUNK_SIZE"
    assert names["log_level"] == "RAG_LOG_LEVEL"


def test_reads_platform_then_product_env_and_ignores_old_names(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("PLATFORM_ENV_FILE", raising=False)
    settings_cls = _settings_class(tmp_path)
    (tmp_path / "platform").mkdir()
    (tmp_path / "platform" / ".env").write_text(
        "PLATFORM_ORACLE_DSN=shared\nPLATFORM_AUTH_IDLE_TIMEOUT_MINUTES=30\n", encoding="utf-8"
    )
    (tmp_path / "rag" / "backend" / ".env").write_text(
        "RAG_AUTH_MODE=database\nORACLE_DSN=old\nAUTH_MODE=old\n", encoding="utf-8"
    )
    monkeypatch.setenv("ORACLE_DSN", "old")
    monkeypatch.setenv("RAG_CHUNK_SIZE", "5")
    settings = settings_cls()
    assert settings.oracle_dsn == "shared"  # type: ignore[attr-defined]
    assert settings.app_auth_idle_timeout_minutes == 30  # type: ignore[attr-defined]
    assert settings.auth_mode == "database"  # type: ignore[attr-defined]
    assert settings.rag_chunk_size == 5  # type: ignore[attr-defined]
    # 属性名での生成（テスト用）も使える。
    assert settings_cls(oracle_dsn="kw").oracle_dsn == "kw"  # type: ignore[attr-defined]


def test_old_names_are_not_read_even_without_new_names(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """新名がなくても、旧名（属性名と同じ名前）の環境変数と .env は読まない（#211）。"""
    monkeypatch.delenv("PLATFORM_ENV_FILE", raising=False)
    settings_cls = _settings_class(tmp_path)
    (tmp_path / "rag" / "backend" / ".env").write_text(
        "LOG_LEVEL=WARN\nAUTH_MODE=old\n", encoding="utf-8"
    )
    monkeypatch.setenv("ORACLE_DSN", "old")
    monkeypatch.setenv("RAG_CHUNK_SIZE", "7")
    settings = settings_cls()
    assert settings.oracle_dsn == ""  # type: ignore[attr-defined]
    assert settings.log_level == "INFO"
    assert settings.auth_mode == "local"  # type: ignore[attr-defined]
    assert settings.rag_chunk_size == 7  # type: ignore[attr-defined]


def test_platform_env_file_can_be_overridden(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    backend_dir = tmp_path / "nl2sql" / "backend"
    monkeypatch.delenv("PLATFORM_ENV_FILE", raising=False)
    assert platform_env_file(backend_dir) == tmp_path.resolve() / "platform" / ".env"
    monkeypatch.setenv("PLATFORM_ENV_FILE", str(tmp_path / "shared.env"))
    assert platform_env_file(backend_dir) == tmp_path / "shared.env"
