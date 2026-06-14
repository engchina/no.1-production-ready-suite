"""pytest 共通 fixture。"""

from pathlib import Path

import pytest

from app.clients.oracle import reset_local_store
from app.config import Settings, get_settings
from app.rag.rate_limit import reset_rate_limiter


@pytest.fixture(autouse=True)
def isolated_local_state(tmp_path: Path) -> None:
    """各テストで local adapter の保存先とインメモリ store を分離する。"""
    reset_local_store()
    reset_rate_limiter()
    _reset_runtime_settings(get_settings(), tmp_path)


def _reset_runtime_settings(settings: Settings, tmp_path: Path) -> None:
    """mutable runtime settings をテスト既定値へ戻す。"""
    settings.ai_service_adapter = "local"
    settings.upload_storage_backend = "local"
    settings.object_storage_namespace = ""
    settings.object_storage_bucket = ""
    settings.oracle_client_lib_dir = str(tmp_path / "instantclient_23_26")
    settings.oracle_wallet_dir = ""
    settings.local_storage_dir = str(tmp_path / "storage")
    settings.max_upload_bytes = 200 * 1024 * 1024
    settings.rate_limit_enabled = True
    settings.auth_mode = "local"
    settings.auth_username = ""
    settings.auth_password = ""
    settings.auth_session_secret = ""
    settings.auth_session_timeout_seconds = 24 * 60 * 60
    settings.auth_cookie_secure = False
