from __future__ import annotations

import json
import re
from collections.abc import AsyncGenerator
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any, cast

import pytest
from fastapi import HTTPException, Request
from starlette.responses import JSONResponse

from app.features.settings import router as settings_router
from app.features.settings import system_schema_runtime
from app.features.settings.system_schema import (
    CONTROL_TABLE,
    MANAGED_FOREIGN_KEYS,
    MANAGED_INDEXES,
    MANAGED_OBJECTS,
    MANAGED_SEQUENCES,
    MANAGED_TABLES,
    MIGRATIONS,
    PRESERVED_TABLES,
    SystemSchemaActiveJobsError,
    SystemSchemaBusyError,
    SystemSchemaError,
    SystemSchemaManager,
    classify_system_schema_status,
    split_migration_sql,
)
from app.schemas.settings import SystemTablesInitializeRequest
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
    versions = [migration.version for migration in MIGRATIONS]
    return {
        "status": status,
        "schema_head": versions[-1],
        "applied_versions": versions if status == "ready" else [],
        "pending_versions": [] if status == "ready" else versions,
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
    assert [migration.version for migration in MIGRATIONS] == [0, 1, 2, 3, 5, 6, 7, 8]
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
    def __init__(
        self,
        initial_status: str,
        *,
        fail_once: bool = False,
        failure_message: str = "password=secret ORA-00600 full sql must not leak",
    ) -> None:
        super().__init__(
            lambda: self._connection(),
            ddl_lock_timeout_seconds=1,
        )
        self.initial_status = initial_status
        self.ready = initial_status == "ready"
        self.fail_once = fail_once
        self.failure_message = failure_message
        self.failures: list[str] = []
        self.applied_migrations: list[int] = []

    @contextmanager
    def _connection(self) -> Any:
        yield object()

    def _ensure_control_schema(self) -> None:
        return None

    def _claim_lease(self, owner: str, operation_kind: str) -> None:
        return None

    def _status_on(self, connection: Any) -> dict[str, Any]:
        return _status_payload("ready" if self.ready else self.initial_status, existing=0)

    def _configure_ddl_lock_timeout(self, connection: Any) -> None:
        return None

    def _apply_migration(self, connection: Any, migration: Any) -> None:
        if self.fail_once:
            self.fail_once = False
            raise RuntimeError(self.failure_message)
        self.applied_migrations.append(migration.version)
        if migration.version == MIGRATIONS[-1].version:
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
    no_op = ready.initialize()
    assert no_op["operation"] == "no_op"
    assert no_op["applied_versions"] == [migration.version for migration in MIGRATIONS]
    assert ready.applied_migrations == []

    manager = _WorkflowManager("missing", fail_once=True)
    with pytest.raises(SystemSchemaError) as error:
        manager.initialize()
    assert error.value.code == "ORA-00600"
    assert "secret" not in error.value.public_message
    assert manager.failures == ["ORA-00600"]

    retried = manager.initialize()
    assert retried["operation"] == "initialized"
    assert retried["status"] == "ready"
    assert retried["applied_versions"] == [migration.version for migration in MIGRATIONS]


def _partial_status(
    *,
    applied_versions: list[int],
    missing_objects: list[tuple[str, str]],
    pending_versions: list[int] | None = None,
) -> dict[str, Any]:
    return {
        **_status_payload("partial", existing=len(MANAGED_OBJECTS) - len(missing_objects)),
        "applied_versions": applied_versions,
        "pending_versions": (
            pending_versions
            if pending_versions is not None
            else [
                migration.version
                for migration in MIGRATIONS
                if migration.version not in applied_versions
            ]
        ),
        "missing_objects": [
            {"name": name, "object_type": object_type}
            for name, object_type in missing_objects
        ],
    }


def test_selective_plan_for_49_of_53_only_applies_versions_7_and_8() -> None:
    manager = SystemSchemaManager(ddl_lock_timeout_seconds=1)
    status = _partial_status(
        applied_versions=[0, 1, 2, 3, 5, 6],
        pending_versions=[7, 8],
        missing_objects=[
            ("NL2SQL_EVALUATION_JOBS", "TABLE"),
            ("NL2SQL_EVALUATION_RESULTS", "TABLE"),
            ("IX_NL2SQL_EVAL_JOB_STATE", "INDEX"),
            ("IX_NL2SQL_EVAL_JOB_LEASE", "INDEX"),
        ],
    )

    assert [migration.version for migration in manager._plan_migrations(status)] == [7, 8]


def test_selective_plan_replays_only_owner_of_one_missing_object() -> None:
    manager = SystemSchemaManager(ddl_lock_timeout_seconds=1)
    status = _partial_status(
        applied_versions=[migration.version for migration in MIGRATIONS],
        pending_versions=[],
        missing_objects=[("IX_NL2SQL_EVAL_JOB_LEASE", "INDEX")],
    )

    assert [migration.version for migration in manager._plan_migrations(status)] == [8]


def test_selective_plan_replays_only_checksum_mismatch() -> None:
    manager = SystemSchemaManager(ddl_lock_timeout_seconds=1)
    status = _partial_status(
        applied_versions=[
            migration.version for migration in MIGRATIONS if migration.version != 5
        ],
        pending_versions=[5],
        missing_objects=[],
    )

    assert [migration.version for migration in manager._plan_migrations(status)] == [5]


class _IncrementalWorkflowManager(_WorkflowManager):
    def __init__(self) -> None:
        super().__init__("partial")
        self.before = _partial_status(
            applied_versions=[0, 1, 2, 3, 5, 6],
            pending_versions=[7, 8],
            missing_objects=[
                ("NL2SQL_EVALUATION_JOBS", "TABLE"),
                ("NL2SQL_EVALUATION_RESULTS", "TABLE"),
                ("IX_NL2SQL_EVAL_JOB_STATE", "INDEX"),
                ("IX_NL2SQL_EVAL_JOB_LEASE", "INDEX"),
            ],
        )

    def _status_on(self, connection: Any) -> dict[str, Any]:
        return _status_payload("ready") if self.ready else self.before


def test_incremental_update_reaches_ready_without_replaying_old_migrations() -> None:
    manager = _IncrementalWorkflowManager()

    result = manager.initialize()

    assert manager.applied_migrations == [7, 8]
    assert result["operation"] == "migrated"
    assert result["status"] == "ready"
    assert result["existing_object_count"] == len(MANAGED_OBJECTS)
    assert result["applied_versions"] == [
        migration.version for migration in MIGRATIONS
    ]


class _RecordingCursor:
    def __init__(self, statements: list[str]) -> None:
        self.statements = statements

    def __enter__(self) -> _RecordingCursor:
        return self

    def __exit__(self, *_args: object) -> None:
        return None

    def execute(
        self,
        sql: str,
        binds: dict[str, Any] | None = None,
    ) -> None:
        self.statements.append(sql)


class _RecordingConnection:
    def __init__(self) -> None:
        self.statements: list[str] = []
        self.commits = 0

    def cursor(self) -> _RecordingCursor:
        return _RecordingCursor(self.statements)

    def commit(self) -> None:
        self.commits += 1


class _ForeignKeyStateManager(SystemSchemaManager):
    def __init__(self, states: dict[str, str]) -> None:
        super().__init__(ddl_lock_timeout_seconds=1)
        self.states = states

    def _foreign_key_state(self, connection: Any, expected: Any) -> Any:
        return self.states[expected.name]


def test_v7_resume_skips_matching_first_foreign_key_and_creates_second() -> None:
    first, second = MANAGED_FOREIGN_KEYS
    manager = _ForeignKeyStateManager(
        {
            first.name: "matching",
            second.name: "missing",
        }
    )
    connection = _RecordingConnection()

    manager._apply_migration(connection, next(item for item in MIGRATIONS if item.version == 7))

    executed = "\n".join(connection.statements)
    assert f"ADD CONSTRAINT\n    {first.name}" not in executed
    assert f"ADD CONSTRAINT\n    {second.name}" in executed
    assert "MERGE INTO NL2SQL_SCHEMA_MIGRATIONS" in executed
    assert connection.commits == 1


def test_v7_resume_rejects_same_name_with_wrong_foreign_key_definition() -> None:
    first, second = MANAGED_FOREIGN_KEYS
    manager = _ForeignKeyStateManager(
        {
            first.name: "matching",
            second.name: "mismatch",
        }
    )

    with pytest.raises(SystemSchemaError) as error:
        manager._apply_migration(
            _RecordingConnection(),
            next(item for item in MIGRATIONS if item.version == 7),
        )

    assert error.value.code == "SCHEMA_CONSTRAINT_MISMATCH"
    assert second.name in error.value.public_message


class _ForeignKeyDictionaryCursor:
    def __init__(self, connection: _ForeignKeyDictionaryConnection) -> None:
        self.connection = connection
        self.query_kind = ""

    def __enter__(self) -> _ForeignKeyDictionaryCursor:
        return self

    def __exit__(self, *_args: object) -> None:
        return None

    def execute(self, sql: str, binds: dict[str, Any]) -> None:
        if "SELECT child.TABLE_NAME" in sql:
            self.query_kind = "constraint"
        elif "SELECT referenced_column.COLUMN_NAME" in sql:
            self.query_kind = "referenced_columns"
        else:
            self.query_kind = "columns"

    def fetchone(self) -> tuple[str, ...]:
        return (
            "NL2SQL_ONTOLOGY_PROFILE_VIEWS",
            "R",
            "CASCADE",
            "NL2SQL_PROFILES",
            self.connection.status,
            "VALIDATED",
            "NOT DEFERRABLE",
            "IMMEDIATE",
        )

    def fetchall(self) -> list[tuple[str]]:
        return [("PROFILE_ID",)]


class _ForeignKeyDictionaryConnection:
    def __init__(self, *, status: str = "ENABLED") -> None:
        self.status = status

    def cursor(self) -> _ForeignKeyDictionaryCursor:
        return _ForeignKeyDictionaryCursor(self)


def test_foreign_key_dictionary_validation_checks_enforcement_state() -> None:
    manager = SystemSchemaManager(ddl_lock_timeout_seconds=1)
    expected = MANAGED_FOREIGN_KEYS[1]

    assert (
        manager._foreign_key_state(_ForeignKeyDictionaryConnection(), expected)
        == "matching"
    )
    assert (
        manager._foreign_key_state(
            _ForeignKeyDictionaryConnection(status="DISABLED"),
            expected,
        )
        == "mismatch"
    )


def test_schema_connection_sets_bounded_ddl_lock_timeout() -> None:
    manager = SystemSchemaManager(ddl_lock_timeout_seconds=30)
    connection = _RecordingConnection()

    manager._configure_ddl_lock_timeout(connection)

    assert connection.statements == ["ALTER SESSION SET DDL_LOCK_TIMEOUT = 30"]


def test_ora_00054_is_retryable_conflict_with_recovery_message() -> None:
    manager = _WorkflowManager(
        "missing",
        fail_once=True,
        failure_message="ORA-00054: resource busy and acquire with NOWAIT specified",
    )

    with pytest.raises(SystemSchemaError) as error:
        manager.initialize()

    assert error.value.code == "ORA-00054"
    assert error.value.status_code == 409
    assert "1 秒以内" in error.value.public_message
    assert "品質評価 job" in error.value.public_message
    assert manager.failures == ["ORA-00054"]

    result = manager.initialize()
    assert result["status"] == "ready"


class _ActiveJobCursor(_RecordingCursor):
    def __init__(self, statements: list[str], active_table: str) -> None:
        super().__init__(statements)
        self.active_table = active_table
        self.current_sql = ""

    def execute(
        self,
        sql: str,
        binds: dict[str, Any] | None = None,
    ) -> None:
        super().execute(sql, binds)
        self.current_sql = sql

    def fetchone(self) -> tuple[int]:
        return (1,) if self.active_table in self.current_sql else (0,)


class _ActiveJobConnection(_RecordingConnection):
    def __init__(self, active_table: str) -> None:
        super().__init__()
        self.active_table = active_table

    def cursor(self) -> _ActiveJobCursor:
        return _ActiveJobCursor(self.statements, self.active_table)


@pytest.mark.parametrize(
    "active_table",
    [
        "NL2SQL_SCHEMA_REFRESH_JOBS",
        "NL2SQL_ONTOLOGY_JOBS",
        "NL2SQL_EVALUATION_JOBS",
    ],
)
def test_recreate_rejects_each_active_persistent_job_type(
    monkeypatch: pytest.MonkeyPatch,
    active_table: str,
) -> None:
    manager = SystemSchemaManager(ddl_lock_timeout_seconds=1)
    connection = _ActiveJobConnection(active_table)
    existing_job_tables = {
        (name, "TABLE"): None
        for name in (
            "NL2SQL_SCHEMA_REFRESH_JOBS",
            "NL2SQL_ONTOLOGY_JOBS",
            "NL2SQL_EVALUATION_JOBS",
        )
    }
    monkeypatch.setattr(manager, "_load_objects", lambda _connection: existing_job_tables)

    with pytest.raises(SystemSchemaActiveJobsError):
        manager._assert_no_active_jobs(connection)


class _ApiManager:
    def status(self) -> dict[str, Any]:
        return _status_payload("partial", existing=4)

    def initialize(self, *, recreate: bool, confirmation: str | None) -> dict[str, Any]:
        result = _status_payload("ready")
        return {
            **result,
            "operation": "recreated" if recreate else "migrated",
            "applied_versions": [migration.version for migration in MIGRATIONS],
            "dropped_object_count": 42 if recreate else 0,
            "created_object_count": 48 if recreate else 44,
        }


class _LockedApiManager(_ApiManager):
    def initialize(self, *, recreate: bool, confirmation: str | None) -> dict[str, Any]:
        raise SystemSchemaError(
            "ORA-00054",
            "Oracle の対象オブジェクトのロックが待機時間内に解放されませんでした。",
            status_code=409,
        )


def _system_tables_request() -> Request:
    return Request(
        {
            "type": "http",
            "method": "POST",
            "path": "/api/settings/database/system-tables/initialize",
            "headers": [],
        }
    )


def test_system_tables_api_contract_and_strict_permission(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings_router, "system_schema_manager", _ApiManager())
    monkeypatch.setattr(
        settings_router,
        "reset_system_schema_runtime",
        lambda *, schema_epoch: None,
    )

    status = settings_router.get_system_tables_status()
    initialized = settings_router.initialize_system_tables(
        SystemTablesInitializeRequest(recreate=False),
        _system_tables_request(),
    )

    assert status.data is not None
    assert status.data.status == "partial"
    assert not isinstance(initialized, JSONResponse)
    assert initialized.data is not None
    assert initialized.data.operation == "migrated"
    assert permission_for_route("GET", "/settings/database/system-tables") == (
        "settings.database.view"
    )
    assert permission_for_route(
        "POST", "/settings/database/system-tables/initialize"
    ) == "settings.database.sql_execute"


def test_system_tables_api_exposes_retryable_ora_00054_contract(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings_router, "system_schema_manager", _LockedApiManager())

    response = settings_router.initialize_system_tables(
        SystemTablesInitializeRequest(recreate=False),
        _system_tables_request(),
    )

    assert isinstance(response, JSONResponse)
    assert response.status_code == 409
    assert response.headers["Retry-After"] == "5"
    assert json.loads(bytes(response.body)) == {
        "data": None,
        "error_messages": [
            "Oracle の対象オブジェクトのロックが待機時間内に解放されませんでした。"
        ],
        "warning_messages": [],
        "error_code": "ORA-00054",
    }


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
