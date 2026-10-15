"""共通 `.env` に別のプロセスが保存した設定の読み直し（#2092）。"""

from pathlib import Path

import pytest

import app.settings as app_settings


def test_get_settings_picks_up_upload_storage_saved_by_another_process(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """RAG・Agent・別の worker が保存したアップロード保存先を、再起動なしで使う。"""
    for name in ("PLATFORM_UPLOAD_STORAGE_BACKEND", "PLATFORM_OBJECT_STORAGE_BUCKET"):
        monkeypatch.delenv(name, raising=False)
    platform_env = tmp_path / "shared-platform.env"
    platform_env.write_text("PLATFORM_UPLOAD_STORAGE_BACKEND=local\n", encoding="utf-8")
    monkeypatch.setattr(app_settings, "PLATFORM_ENV_FILE", platform_env)
    app_settings.reset_settings_cache()
    try:
        settings = app_settings.get_settings()
        assert settings.upload_storage_backend == "local"

        platform_env.write_text(
            "PLATFORM_UPLOAD_STORAGE_BACKEND=oci\nPLATFORM_OBJECT_STORAGE_BUCKET=example-bucket\n",
            encoding="utf-8",
        )
        reloaded = app_settings.get_settings()

        assert reloaded is settings
        assert reloaded.upload_storage_backend == "oci"
        assert reloaded.object_storage_bucket == "example-bucket"
    finally:
        app_settings.reset_settings_cache()


def test_get_settings_keeps_upload_storage_given_by_environment(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """環境変数で与えたアップロード保存先は、`.env` の変更で上書きしない。"""
    monkeypatch.setenv("PLATFORM_UPLOAD_STORAGE_BACKEND", "local")
    platform_env = tmp_path / "shared-platform.env"
    monkeypatch.setattr(app_settings, "PLATFORM_ENV_FILE", platform_env)
    app_settings.reset_settings_cache()
    try:
        settings = app_settings.get_settings()
        platform_env.write_text("PLATFORM_UPLOAD_STORAGE_BACKEND=oci\n", encoding="utf-8")

        assert app_settings.get_settings().upload_storage_backend == "local"
        assert settings.upload_storage_backend == "local"
    finally:
        app_settings.reset_settings_cache()
