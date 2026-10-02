"""保存先の状態（`GET /api/runtime/storage`。#839）。"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
from pytest import MonkeyPatch

from app.features.agent import control_plane_store, storage_status
from app.features.agent.control_plane_store import FileItemStore, MemoryItemStore, OracleItemStore
from app.security.permissions import (
    ADMIN,
    MENU_AGENTS,
    MENU_CHAT,
    MENU_RUNS,
    MENU_RUNTIMES,
    MENU_SETTINGS_API_KEYS,
    MENU_SETTINGS_EXTERNAL_MCP,
    MENU_SETTINGS_RUNTIME_SNAPSHOT,
    MENU_SETTINGS_SYSTEM_TABLES,
    permission_for_route,
)
from app.settings import get_settings


@pytest.fixture(autouse=True)
def _restore_store() -> Iterator[None]:
    yield
    control_plane_store.set_control_plane_store(None)


def _database(monkeypatch: MonkeyPatch, *, configured: bool) -> None:
    monkeypatch.setattr(
        storage_status, "database_readiness", lambda _settings: "ok" if configured else "missing"
    )


def test_memory_with_database_configured_points_to_the_backend_setting(
    monkeypatch: MonkeyPatch,
) -> None:
    """DB は設定済みでも保存先が memory なら、理由は「保存先の設定」（DB の設定へ案内しない）。"""
    monkeypatch.setattr(get_settings(), "agent_runtime_repository_backend", "memory")
    control_plane_store.set_control_plane_store(MemoryItemStore())
    _database(monkeypatch, configured=True)

    status = storage_status.runtime_storage_status()

    assert status.backend == "memory"
    assert status.persistent is False
    assert status.database_configured is True
    assert status.reason == "memory_backend"


def test_memory_without_database_points_to_the_database_settings(monkeypatch: MonkeyPatch) -> None:
    monkeypatch.setattr(get_settings(), "agent_runtime_repository_backend", "memory")
    control_plane_store.set_control_plane_store(MemoryItemStore())
    _database(monkeypatch, configured=False)

    status = storage_status.runtime_storage_status()

    assert status.persistent is False
    assert status.database_configured is False
    assert status.reason == "database_not_configured"


def test_oracle_backend_is_persistent(monkeypatch: MonkeyPatch) -> None:
    monkeypatch.setattr(get_settings(), "agent_runtime_repository_backend", "oracle")
    control_plane_store.set_control_plane_store(OracleItemStore(connect_factory=lambda: None))
    _database(monkeypatch, configured=True)

    status = storage_status.runtime_storage_status()

    assert status.backend == "oracle_checkpoint"
    assert status.persistent is True
    assert status.reason is None


def test_file_without_snapshot_path_is_memory(tmp_path: Path, monkeypatch: MonkeyPatch) -> None:
    settings = get_settings()
    monkeypatch.setattr(settings, "agent_runtime_repository_backend", "file")
    monkeypatch.setattr(settings, "agent_runtime_snapshot_path", None)
    assert storage_status.effective_backend() == "memory"

    monkeypatch.setattr(settings, "agent_runtime_snapshot_path", str(tmp_path / "runtime.json"))
    control_plane_store.set_control_plane_store(FileItemStore(tmp_path / "items.json"))
    _database(monkeypatch, configured=False)
    status = storage_status.runtime_storage_status()
    assert status.backend == "file"
    assert status.persistent is True
    assert status.reason is None


def test_readiness_failure_is_reported_as_not_configured(monkeypatch: MonkeyPatch) -> None:
    def broken(_settings: object) -> str:
        raise OSError("wallet")

    monkeypatch.setattr(storage_status, "database_readiness", broken)
    assert storage_status.database_configured() is False


def test_storage_api_returns_status_without_connection_details(monkeypatch: MonkeyPatch) -> None:
    from security_support import client

    monkeypatch.setattr(get_settings(), "agent_runtime_repository_backend", "memory")
    control_plane_store.set_control_plane_store(MemoryItemStore())
    _database(monkeypatch, configured=True)

    response = client.get("/api/runtime/storage")

    assert response.status_code == 200, response.text
    assert response.json()["data"] == {
        "backend": "memory",
        "persistent": False,
        "database_configured": True,
        "reason": "memory_backend",
    }


def test_storage_api_is_readable_from_the_pages_that_show_the_notice() -> None:
    allowed = permission_for_route("GET", "/runtime/storage")
    assert allowed is not None
    for code in (
        MENU_AGENTS,
        MENU_RUNS,
        MENU_RUNTIMES,
        MENU_SETTINGS_API_KEYS,
        MENU_SETTINGS_EXTERNAL_MCP,
        MENU_SETTINGS_RUNTIME_SNAPSHOT,
        MENU_SETTINGS_SYSTEM_TABLES,
        ADMIN,
    ):
        assert code in allowed
    # チャットだけの利用者には出さない（直せない案内を出さない）。
    assert MENU_CHAT not in allowed
