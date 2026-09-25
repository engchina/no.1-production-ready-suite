"""system table 設定 API の契約テスト。"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest
from fastapi import HTTPException, Request
from pytest import MonkeyPatch

from app.api.routes import settings as settings_route
from app.auth import AuthSession
from app.main import app
from app.rag.system_schema import SystemSchemaError, system_schema_manager
from app.schemas.settings import SystemTablesInitializeRequest
from tests.support import AsgiTestClient

client = AsgiTestClient(app)


async def _run_inline(operation: Any, *args: Any, **kwargs: Any) -> Any:
    return operation(*args, **kwargs)


def _status_payload() -> dict[str, Any]:
    return {
        "status": "ready",
        "schema_version": "2",
        "schema_head": "20260703_002_feedback_details",
        "applied_versions": [],
        "pending_versions": [],
        "expected_object_count": 97,
        "existing_object_count": 97,
        "expected_table_count": 28,
        "existing_table_count": 28,
        "missing_objects": [],
        "retired_objects": [],
        "tables": [],
        "operation_state": {
            "status": "idle",
            "operation_kind": None,
            "lease_expires_at": None,
            "last_error_code": None,
            "schema_epoch": 1,
            "updated_at": None,
        },
    }


def test_system_tables_status_is_read_only(monkeypatch: MonkeyPatch) -> None:
    calls = 0

    def status() -> dict[str, Any]:
        nonlocal calls
        calls += 1
        return _status_payload()

    monkeypatch.setattr(system_schema_manager, "status", status)
    monkeypatch.setattr(asyncio, "to_thread", _run_inline)

    response = client.get("/api/settings/database/system-tables")

    assert response.status_code == 200
    assert response.json()["data"]["status"] == "ready"
    assert calls == 1


def test_initialize_system_tables_returns_typed_operation(monkeypatch: MonkeyPatch) -> None:
    captured: dict[str, Any] = {}

    def initialize(**kwargs: Any) -> dict[str, Any]:
        captured.update(kwargs)
        return {
            **_status_payload(),
            "operation": "initialized",
            "dropped_object_count": 0,
            "created_object_count": 96,
        }

    monkeypatch.setattr(system_schema_manager, "initialize", initialize)
    monkeypatch.setattr(asyncio, "to_thread", _run_inline)

    response = client.post(
        "/api/settings/database/system-tables/initialize",
        json={"recreate": False},
    )

    assert response.status_code == 200
    assert response.json()["data"]["operation"] == "initialized"
    assert captured == {"recreate": False, "confirmation": None}


def test_initialize_system_tables_preserves_retryable_error(monkeypatch: MonkeyPatch) -> None:
    def initialize(**_kwargs: Any) -> dict[str, Any]:
        raise SystemSchemaError(
            "ORA-00054",
            "ロックが解放されませんでした。",
            status_code=409,
        )

    monkeypatch.setattr(system_schema_manager, "initialize", initialize)
    monkeypatch.setattr(asyncio, "to_thread", _run_inline)

    response = client.post(
        "/api/settings/database/system-tables/initialize",
        json={"recreate": False},
    )

    assert response.status_code == 409
    assert response.headers["retry-after"] == "5"
    assert response.json()["error_code"] == "ORA-00054"


async def test_initialize_system_tables_rejects_non_admin() -> None:
    request = Request({"type": "http", "method": "POST", "path": "/", "headers": []})
    request.state.auth_session = AuthSession(
        user_id="user",
        username="user",
        role="USER",
        expires_at=0,
        remember_me=False,
    )

    with pytest.raises(HTTPException) as forbidden:
        await settings_route.initialize_system_tables(
            SystemTablesInitializeRequest(),
            request,
        )

    assert forbidden.value.status_code == 403
