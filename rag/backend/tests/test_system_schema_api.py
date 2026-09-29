"""system table 設定 API の契約テスト。"""

from __future__ import annotations

import asyncio
from typing import Any

from pytest import MonkeyPatch

from app.main import app
from app.rag.system_schema import SystemSchemaError, system_schema_manager
from tests.security_support import enable_production_auth, login
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
    # 外部キーの差分が無い応答（旧い manager の形）でも空の一覧になる（#505）。
    assert response.json()["data"]["missing_foreign_keys"] == []
    assert response.json()["data"]["orphaned_foreign_keys"] == []


def test_system_tables_status_returns_foreign_key_drift(monkeypatch: MonkeyPatch) -> None:
    foreign_key = {
        "name": "RAG_CHUNK_SETS_DOCUMENT_FK",
        "table_name": "RAG_CHUNK_SETS",
        "columns": ["DOCUMENT_ID"],
        "referenced_table_name": "RAG_DOCUMENTS",
        "referenced_columns": ["DOCUMENT_ID"],
        "delete_rule": "CASCADE",
        "orphan_rows": 240,
    }
    monkeypatch.setattr(
        system_schema_manager,
        "status",
        lambda: {
            **_status_payload(),
            "status": "outdated",
            "missing_foreign_keys": [foreign_key],
            "orphaned_foreign_keys": [{**foreign_key, "name": "RAG_DOC_EXT_DOCUMENT_FK"}],
        },
    )
    monkeypatch.setattr(asyncio, "to_thread", _run_inline)

    response = client.get("/api/settings/database/system-tables")

    assert response.status_code == 200
    data = response.json()["data"]
    assert data["status"] == "outdated"
    assert data["missing_foreign_keys"] == [foreign_key]
    assert data["orphaned_foreign_keys"][0]["name"] == "RAG_DOC_EXT_DOCUMENT_FK"
    assert data["orphaned_foreign_keys"][0]["orphan_rows"] == 240


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


def test_initialize_system_tables_requires_system_tables_manage(
    monkeypatch: MonkeyPatch,
) -> None:
    """初期化・再作成は rag.system_tables.manage が必要（旧 ADMIN 判定の置き換え）。

    状態の参照はデータベース設定の権限だけでよい。
    """
    monkeypatch.setattr(system_schema_manager, "status", _status_payload)
    monkeypatch.setattr(
        system_schema_manager,
        "initialize",
        lambda **_kwargs: {
            **_status_payload(),
            "operation": "no_op",
            "dropped_object_count": 0,
            "created_object_count": 0,
        },
    )
    monkeypatch.setattr(asyncio, "to_thread", _run_inline)
    auth = enable_production_auth(monkeypatch)
    auth.user_with_permissions("db-viewer", ["menu.settings_database"])
    auth.user_with_permissions("table-manager", ["rag.system_tables.manage"])

    viewer = login(client, "db-viewer")
    assert client.get("/api/settings/database/system-tables", headers=viewer).status_code == 200
    forbidden = client.post(
        "/api/settings/database/system-tables/initialize",
        json={"recreate": False},
        headers=viewer,
    )
    assert forbidden.status_code == 403

    manager = login(client, "table-manager")
    allowed = client.post(
        "/api/settings/database/system-tables/initialize",
        json={"recreate": False},
        headers=manager,
    )
    assert allowed.status_code == 200
    assert allowed.json()["data"]["operation"] == "no_op"
