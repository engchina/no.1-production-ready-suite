"""pytest 共通 fixture。"""

from pathlib import Path

import pytest
from pytest import MonkeyPatch

from app.clients.oracle import reset_local_store
from app.config import get_settings
from app.rag.rate_limit import reset_rate_limiter


@pytest.fixture(autouse=True)
def isolated_local_state(monkeypatch: MonkeyPatch, tmp_path: Path) -> None:
    """各テストで local adapter の保存先とインメモリ store を分離する。"""
    reset_local_store()
    reset_rate_limiter()
    settings = get_settings()
    monkeypatch.setattr(settings, "ai_service_adapter", "local")
    monkeypatch.setattr(settings, "local_storage_dir", str(tmp_path / "storage"))
    monkeypatch.setattr(settings, "max_upload_bytes", 20 * 1024 * 1024)
    monkeypatch.setattr(settings, "rate_limit_enabled", True)
