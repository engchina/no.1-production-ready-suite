from __future__ import annotations

import asyncio
import re
from collections.abc import AsyncGenerator
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any, cast

import httpx
import pytest
from fastapi import HTTPException, Request

from app.features.settings import router as settings_router
from app.features.settings import system_schema_runtime
from app.features.settings.system_schema import (
    CONTROL_TABLE,
    MANAGED_INDEXES,
    MANAGED_OBJECTS,
    MANAGED_SEQUENCES,
    MANAGED_TABLES,
    MIGRATIONS,
    PRESERVED_TABLES,
    SystemSchemaBusyError,
    SystemSchemaError,
    SystemSchemaManager,
    classify_system_schema_status,
    split_migration_sql,
)
from app.main import app
from app.security import dependencies as security_dependencies
from app.security.domain import Principal
from app.security.permissions import permission_for_route
from app.security.service import SecurityApiError
from app.settings import get_settings


def _created_objects() -> dict[str, set[str]]:
    created: dict[str, set[str]] = {
        "TABLE": set(),
        "INDEX": set(),
        "SEQUENCE": set(),
    }
    pattern = re.compile(
        r"CREATE\s+(?:UNIQUE\s+|VECTOR\s+)?"
        r"(TABLE|INDEX|SEQUENCE)\s+([A-Z0-9_]+)",
        flags=re.IGNORECASE,
    )
    for migration in MIGRATIONS:
        for object_type, name in pattern.findall(migration.path.read_text(encoding="utf-8")):
            created[object_type.upper()].add(name.upper())
    return created


def _ready_operation_state(epoch: int = 1) -> dict[str, Any]:
    return {
        "status": "idle",
        "operation_kind": None,
        "lease_expires_at": None,
        "last_error_code": None,
        "schema_epoch": epoch,
        "updated_at": "2026-07-19T00:00:00+00:00",
    }


def _status_payload(status: str, *, existing: int | None = None) -> dict[str, Any]:
    expected = len(MANAGED_OBJECTS)
    return {
        "status": status,
        "schema_head": 6,
        "applied_versions": [0, 1, 2, 3, 5, 6] if status == "ready" else [],
        "pending_versions": [] if status == "ready" else [0, 1, 2, 3, 5, 6],
        "expected_object_count": expected,
        "existing_object_count": expected if existing is None else existing,
        "expected_table_count": len(MANAGED_TABLES),
        "existing_table_count": len(MANAGED_TABLES) if status == "ready" else 0,
        "missing_objects": [],
        "tables": [],
        "operation_state": _ready_operation_state(),
    }


def test_manifest_covers_every_core_create_and_excludes_preserved_tables() -> None:
    created = _created_objects()

    assert created["TABLE"] == set(MANAGED_TABLES)
    assert created["INDEX"] == set(MANAGED_INDEXES)
    assert created["SEQUENCE"] == set(MANAGED_SEQUENCES)
    assert set(MIGRATIONS[index].version for index in range(len(MIGRATIONS))) == {
        0,
        1,
        2,
        3,
        5,
        6,
    }
    assert all("security" not in migration.filename for migration in MIGRATIONS)
    assert set(MANAGED_TABLES).isdisjoint(PRESERVED_TABLES)
    assert "NL2SQL_FEEDBACK_VECTORS" not in MANAGED_TABLES
    assert "NL2SQL_STATE_STORE" not in MANAGED_TABLES
    assert "USER_BUSINESS_SENTINEL" not in MANAGED_TABLES


def test_manifest_checksums_are_actual_sha256_values() -> None:
    for migration in MIGRATIONS:
        assert migration.path.is_file()
        assert re.fullmatch(r"[0-9a-f]{64}", migration.checksum)


def test_system_schema_status_has_four_deterministic_states() -> None:
    expected = set(MANAGED_OBJECTS)
    checksums = {migration.version: migration.checksum for migration in MIGRATIONS}
    one_domain_table = next(
        item
        for item in expected
        if item[1] == "TABLE"
        and item[0] not in {CONTROL_TABLE, "NL2SQL_SCHEMA_MIGRATIONS"}
    )

    assert classify_system_schema_status(set(), {}) == "missing"
    assert classify_system_schema_status({one_domain_table}, {}) == "partial"
    assert classify_system_schema_status(expected, {**checksums, 6: "stale"}) == "outdated"
    assert classify_system_schema_status(expected, checksums) == "ready"


def test_splitter_ignores_comments_and_keeps_multiline_statements() -> None:
    sql = "-- header\nCREATE TABLE EXAMPLE (\n ID NUMBER\n);\n-- trailing\n"
    assert split_migration_sql(sql) == ["CREATE TABLE EXAMPLE (\n ID NUMBER\n)"]


class _LeaseState:
    status = "IDLE"
    owner: str | None = None
    expires_at: datetime | None = None


class _LeaseCursor:
    def __init__(self, state: _LeaseState) -> None:
        self.state = state
        self.rowcount = 0

    def __enter__(self) -> _LeaseCursor:
        return self

    def __exit__(self, *_args: object) -> None:
        return None

    def execute(self, sql: str, binds: dict[str, Any]) -> None:
        assert "UPDATE NL2SQL_SCHEMA_OPERATIONS" in sql
        now = datetime.now(UTC)
        can_claim = (
            self.state.status != "RUNNING"
            or self.state.expires_at is None
            or self.state.expires_at < now
        )
        if can_claim:
            self.state.status = "RUNNING"
            self.state.owner = str(binds["lease_owner"])
            self.state.expires_at = now + timedelta(seconds=int(binds["lease_seconds"]))
            self.rowcount = 1
        else:
            self.rowcount = 0


class _LeaseConnection:
    def __init__(self, state: _LeaseState) -> None:
        self.state = state

    def cursor(self) -> _LeaseCursor:
        return _LeaseCursor(self.state)

    def commit(self) -> None:
        return None


def test_database_lease_rejects_concurrency_and_allows_expired_takeover() -> None:
    state = _LeaseState()

    @contextmanager
    def connection() -> Any:
        yield _LeaseConnection(state)

    manager = SystemSchemaManager(connection, lease_seconds=30)
    manager._claim_lease("first", "initialize")
    with pytest.raises(SystemSchemaBusyError):
        manager._claim_lease("second", "recreate")

    state.expires_at = datetime.now(UTC) - timedelta(seconds=1)
    manager._claim_lease("second", "recreate")
    assert state.owner == "second"


def test_recreate_requires_exact_confirmation_before_database_access() -> None:
    manager = SystemSchemaManager(lambda: pytest.fail("database must not be accessed"))

    with pytest.raises(SystemSchemaError) as error:
        manager.initialize(recreate=True, confirmation="wrong")

    assert error.value.code == "SCHEMA_RECREATE_CONFIRMATION_REQUIRED"
    assert error.value.status_code == 422


def test_recreate_drop_statements_only_use_manifest_allowlist(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manager = SystemSchemaManager()
    objects = set(MANAGED_OBJECTS) | {("USER_BUSINESS_SENTINEL", "TABLE")}
    statements: list[str] = []

    def record_drop(_connection: Any, statement: str) -> int:
        statements.append(statement)
        return 1

    monkeypatch.setattr(manager, "_load_objects", lambda _connection: dict.fromkeys(objects))
    monkeypatch.setattr(manager, "_heartbeat", lambda _connection, _owner: None)
    monkeypatch.setattr(
        manager,
        "_execute_drop",
        record_drop,
    )

    dropped = manager._drop_managed_objects(object(), "owner")

    assert dropped == len(MANAGED_INDEXES) + len(MANAGED_TABLES) - 1 + len(MANAGED_SEQUENCES)
    assert all("USER_BUSINESS_SENTINEL" not in statement for statement in statements)
    assert all("NL2SQL_AUTH_" not in statement for statement in statements)
    assert all("NL2SQL_FEEDBACK_VECTORS" not in statement for statement in statements)
    assert all(f"DROP TABLE {CONTROL_TABLE}" not in statement for statement in statements)


class _WorkflowManager(SystemSchemaManager):
    def __init__(self, initial_status: str, *, fail_once: bool = False) -> None:
        super().__init__(lambda: self._connection())
        self.initial_status = initial_status
        self.ready = initial_status == "ready"
        self.fail_once = fail_once
        self.failures: list[str] = []

    @contextmanager
    def _connection(self) -> Any:
        yield object()

    def _ensure_control_schema(self) -> None:
        return None

    def _claim_lease(self, owner: str, operation_kind: str) -> None:
        return None

    def _status_on(self, connection: Any) -> dict[str, Any]:
        return _status_payload("ready" if self.ready else self.initial_status, existing=0)

    def _apply_migration(self, connection: Any, migration: Any) -> None:
        if self.fail_once:
            self.fail_once = False
            raise RuntimeError("password=secret ORA-00600 full sql must not leak")
        if migration.version == 6:
            self.ready = True

    def _heartbeat(self, connection: Any, owner: str) -> None:
        return None

    def _finish_operation(
        self,
        connection: Any,
        owner: str,
        *,
        increment_epoch: bool,
    ) -> None:
        return None

    def _record_failure(self, owner: str, error_code: str) -> None:
        self.failures.append(error_code)


def test_initialize_is_idempotent_and_failure_is_safe_and_retryable() -> None:
    ready = _WorkflowManager("ready")
    assert ready.initialize()["operation"] == "no_op"

    manager = _WorkflowManager("missing", fail_once=True)
    with pytest.raises(SystemSchemaError) as error:
        manager.initialize()
    assert error.value.code == "ORA-00600"
    assert "secret" not in error.value.public_message
    assert manager.failures == ["ORA-00600"]

    retried = manager.initialize()
    assert retried["operation"] == "initialized"
    assert retried["status"] == "ready"
    assert retried["applied_versions"] == [0, 1, 2, 3, 5, 6]


class _ApiManager:
    def status(self) -> dict[str, Any]:
        return _status_payload("partial", existing=4)

    def initialize(self, *, recreate: bool, confirmation: str | None) -> dict[str, Any]:
        result = _status_payload("ready")
        return {
            **result,
            "operation": "recreated" if recreate else "migrated",
            "applied_versions": [0, 1, 2, 3, 5, 6],
            "dropped_object_count": 42 if recreate else 0,
            "created_object_count": 48 if recreate else 44,
        }


def _api_request(method: str, path: str, **kwargs: Any) -> httpx.Response:
    async def send() -> httpx.Response:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            return await client.request(method, path, **kwargs)

    return asyncio.run(send())


def test_system_tables_api_contract_and_strict_permission(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def inline_threadpool(function: Any, *args: Any, **kwargs: Any) -> Any:
        return function(*args, **kwargs)

    monkeypatch.setattr(settings_router, "system_schema_manager", _ApiManager())
    monkeypatch.setattr(settings_router, "run_in_threadpool", inline_threadpool)

    status = _api_request("GET", "/api/settings/database/system-tables")
    initialized = _api_request(
        "POST",
        "/api/settings/database/system-tables/initialize",
        json={"recreate": False},
    )

    assert status.status_code == 200
    assert status.json()["data"]["status"] == "partial"
    assert initialized.status_code == 200
    assert initialized.json()["data"]["operation"] == "migrated"
    assert permission_for_route("GET", "/settings/database/system-tables") == (
        "settings.database.view"
    )
    assert permission_for_route(
        "POST", "/settings/database/system-tables/initialize"
    ) == "settings.database.sql_execute"


def test_schema_epoch_change_resets_each_runtime_once(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[str] = []

    class FakeManager:
        epoch = 10

        def current_epoch(self) -> int:
            return self.epoch

    class FakeNl2Sql:
        uses_incremental_store = True

        def reset_after_system_schema_change(self) -> None:
            events.append("nl2sql-reset")

        def recover_persistence(self) -> None:
            events.append("persistence-recovered")

    class FakeOntology:
        def reset_after_system_schema_change(self) -> None:
            events.append("ontology-reset")

    manager = FakeManager()
    monkeypatch.setattr(system_schema_runtime, "system_schema_manager", manager)
    monkeypatch.setattr(system_schema_runtime, "nl2sql_service", FakeNl2Sql())
    monkeypatch.setattr(system_schema_runtime, "ontology_runtime", FakeOntology())
    system_schema_runtime.reset_observed_system_schema_epoch()

    assert system_schema_runtime.observe_system_schema_epoch() is False
    manager.epoch = 11
    assert system_schema_runtime.observe_system_schema_epoch() is True
    assert system_schema_runtime.observe_system_schema_epoch() is False
    assert events == ["nl2sql-reset", "ontology-reset", "persistence-recovered"]


@pytest.mark.asyncio
async def test_system_table_post_requires_csrf_and_sql_execute_permission(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = get_settings()
    monkeypatch.setattr(settings, "app_auth_enabled", True)
    monkeypatch.setattr(settings, "app_auth_session_cookie_name", "nl2sql_session")
    monkeypatch.setattr(settings, "app_auth_csrf_cookie_name", "nl2sql_csrf")

    async def inline_threadpool(function: Any, *args: Any, **kwargs: Any) -> Any:
        return function(*args, **kwargs)

    class FakeSecurityService:
        def __init__(self, principal: Principal) -> None:
            self.principal = principal

        def authenticate_session(self, token: str) -> Principal:
            assert token == "session-token"
            return self.principal

        def verify_csrf(
            self,
            principal: Principal,
            cookie_csrf: str,
            header_csrf: str,
        ) -> None:
            if cookie_csrf != "csrf-token" or header_csrf != cookie_csrf:
                raise SecurityApiError(403, "CSRF token を確認してください。")

    def principal(permissions: set[str]) -> Principal:
        return Principal(
            user_id="user-1",
            login_name="viewer",
            display_name="閲覧者",
            status="ACTIVE",
            force_password_change=False,
            role_codes=["DB_VIEWER"],
            permissions=permissions,
            data_entitlements=[],
            session_id="session-1",
            csrf_token_hash="hash",
        )

    def request(*, csrf: bool) -> Request:
        headers = [(b"cookie", b"nl2sql_session=session-token; nl2sql_csrf=csrf-token")]
        if csrf:
            headers.append((b"x-csrf-token", b"csrf-token"))
        scope = {
            "type": "http",
            "method": "POST",
            "path": "/api/settings/database/system-tables/initialize",
            "headers": headers,
            "route": SimpleNamespace(
                path="/api/settings/database/system-tables/initialize"
            ),
        }
        return Request(scope)

    monkeypatch.setattr(security_dependencies, "run_in_threadpool", inline_threadpool)

    viewer_service = FakeSecurityService(principal({"settings.database.view"}))
    monkeypatch.setattr(
        security_dependencies,
        "get_security_service",
        lambda: viewer_service,
    )
    with pytest.raises(HTTPException) as no_csrf:
        await anext(security_dependencies.authorize_api_request(request(csrf=False)))
    assert no_csrf.value.status_code == 403

    with pytest.raises(HTTPException) as no_execute:
        await anext(security_dependencies.authorize_api_request(request(csrf=True)))
    assert no_execute.value.status_code == 403

    executor_service = FakeSecurityService(
        principal({"settings.database.view", "settings.database.sql_execute"})
    )
    monkeypatch.setattr(
        security_dependencies,
        "get_security_service",
        lambda: executor_service,
    )
    dependency = cast(
        AsyncGenerator[None, None],
        security_dependencies.authorize_api_request(request(csrf=True)),
    )
    await anext(dependency)
    await dependency.aclose()
