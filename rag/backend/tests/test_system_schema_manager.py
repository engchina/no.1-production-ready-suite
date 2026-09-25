"""RAG Oracle system schema manager の決定論テスト。"""

from __future__ import annotations

import re
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from app.rag.system_schema import (
    DOMAIN_TABLES,
    MANAGED_INDEXES,
    MANAGED_OBJECTS,
    MANAGED_TABLES,
    MIGRATIONS,
    RECREATE_CONFIRMATION,
    RETIRED_MANAGED_OBJECTS,
    SystemSchemaActiveJobsError,
    SystemSchemaBusyError,
    SystemSchemaError,
    SystemSchemaManager,
    classify_system_schema_status,
    managed_manifest_from_schema,
)


class _FakeCursor:
    def __init__(self, database: _FakeDatabase) -> None:
        self.database = database
        self.rows: list[tuple[Any, ...]] = []
        self.rowcount = 0

    def __enter__(self) -> _FakeCursor:
        return self

    def __exit__(self, *_args: object) -> None:
        return None

    def execute(
        self,
        statement: str,
        parameters: dict[str, Any] | None = None,
    ) -> None:
        sql = re.sub(r"\s+", " ", statement).strip()
        upper = sql.upper()
        params = parameters or {}
        self.rows = []
        self.rowcount = 0

        if upper.startswith("SELECT OBJECT_NAME, OBJECT_TYPE, CREATED FROM USER_OBJECTS"):
            names = {str(value).upper() for value in params.values()}
            self.rows = [
                (name, object_type, created_at)
                for (name, object_type), created_at in sorted(self.database.objects.items())
                if name in names and object_type in {"TABLE", "INDEX"}
            ]
            return
        if upper.startswith("SELECT PRE_NAME FROM CTX_USER_PREFERENCES"):
            if ("RAG_TEXT_WORLD_LEXER", "TEXT_PREFERENCE") in self.database.objects:
                self.rows = [("RAG_TEXT_WORLD_LEXER",)]
            return
        if upper.startswith("SELECT SPL_NAME FROM CTX_USER_STOPLISTS"):
            if ("RAG_TEXT_STOPLIST", "TEXT_STOPLIST") in self.database.objects:
                self.rows = [("RAG_TEXT_STOPLIST",)]
            return
        if upper.startswith("SELECT MIGRATION_NAME, CHECKSUM FROM RAG_SCHEMA_MIGRATIONS"):
            self.rows = sorted(self.database.migrations.items())
            return
        if upper.startswith("SELECT TABLE_NAME, NUM_ROWS, LAST_ANALYZED FROM USER_TABLES"):
            names = {str(value).upper() for value in params.values()}
            self.rows = [
                (name, 0, None)
                for name in sorted(names)
                if (name, "TABLE") in self.database.objects
            ]
            return
        if upper.startswith("SELECT STATUS, OPERATION_KIND"):
            operation = self.database.operation
            if operation is not None:
                self.rows = [
                    (
                        operation["status"],
                        operation["operation_kind"],
                        operation["lease_expires_at"],
                        operation["last_error_code"],
                        operation["schema_epoch"],
                        operation["updated_at"],
                    )
                ]
            return
        if upper.startswith("SELECT COUNT(*) FROM RAG_INGESTION_JOBS"):
            self.rows = [(self.database.active_jobs,)]
            return

        if upper.startswith("ALTER SESSION SET DDL_LOCK_TIMEOUT"):
            return
        if upper.startswith("CREATE TABLE"):
            name = upper.split()[2]
            if (name, "TABLE") in self.database.objects:
                raise RuntimeError("ORA-00955: name is already used by an existing object")
            self.database.objects[(name, "TABLE")] = datetime.now(UTC)
            return
        index_match = re.match(
            r"CREATE (?:UNIQUE |VECTOR )?INDEX ([A-Z][A-Z0-9_$#]*)",
            upper,
        )
        if index_match is not None:
            name = index_match.group(1)
            if self.database.fail_drop_or_create_with_lock:
                self.database.fail_drop_or_create_with_lock = False
                raise RuntimeError("ORA-00054: resource busy")
            if (name, "INDEX") in self.database.objects:
                raise RuntimeError("ORA-00955: name is already used by an existing object")
            self.database.objects[(name, "INDEX")] = datetime.now(UTC)
            return
        if "CTX_DDL.CREATE_PREFERENCE" in upper:
            self.database.objects[("RAG_TEXT_WORLD_LEXER", "TEXT_PREFERENCE")] = None
        if "CTX_DDL.CREATE_STOPLIST" in upper:
            self.database.objects[("RAG_TEXT_STOPLIST", "TEXT_STOPLIST")] = None
        if "CTX_DDL.DROP_PREFERENCE" in upper:
            self.database.objects.pop(("RAG_TEXT_WORLD_LEXER", "TEXT_PREFERENCE"), None)
            return
        if "CTX_DDL.DROP_STOPLIST" in upper:
            self.database.objects.pop(("RAG_TEXT_STOPLIST", "TEXT_STOPLIST"), None)
            return
        if upper.startswith("DROP INDEX"):
            name = upper.split()[2]
            self.database.objects.pop((name, "INDEX"), None)
            return
        if upper.startswith("DROP TABLE"):
            name = upper.split()[2]
            self.database.objects.pop((name, "TABLE"), None)
            if name == "RAG_SCHEMA_MIGRATIONS":
                self.database.migrations.clear()
            return
        if upper.startswith("MERGE INTO RAG_SCHEMA_OPERATIONS"):
            if self.database.operation is None:
                self.database.operation = self.database.idle_operation()
            return
        if upper.startswith("MERGE INTO RAG_SCHEMA_MIGRATIONS"):
            self.database.migrations[str(params["migration_name"])] = str(params["checksum"])
            return
        if upper.startswith("UPDATE RAG_SCHEMA_OPERATIONS"):
            self._update_operation(upper, params)
            return

    def _update_operation(self, upper: str, params: dict[str, Any]) -> None:
        operation = self.database.operation
        if operation is None:
            return
        now = datetime.now(UTC)
        owner = str(params.get("lease_owner") or "")
        if "SET STATUS = 'RUNNING'" in upper:
            if (
                operation["status"] == "RUNNING"
                and operation["lease_expires_at"] is not None
                and operation["lease_expires_at"] >= now
            ):
                return
            operation.update(
                {
                    "status": "RUNNING",
                    "operation_kind": params["operation_kind"],
                    "lease_owner": owner,
                    "lease_expires_at": now + timedelta(seconds=int(params["lease_seconds"])),
                    "last_error_code": None,
                    "updated_at": now,
                }
            )
            self.rowcount = 1
            return
        if operation.get("lease_owner") != owner:
            return
        if "SET STATUS = 'IDLE'" in upper:
            if "SCHEMA_EPOCH = SCHEMA_EPOCH + 1" in upper:
                operation["schema_epoch"] = int(operation["schema_epoch"]) + 1
            operation.update(
                {
                    "status": "IDLE",
                    "operation_kind": None,
                    "lease_owner": None,
                    "lease_expires_at": None,
                    "last_error_code": None,
                    "updated_at": now,
                }
            )
            self.rowcount = 1
            return
        if "SET STATUS = 'FAILED'" in upper:
            operation.update(
                {
                    "status": "FAILED",
                    "operation_kind": None,
                    "lease_owner": None,
                    "lease_expires_at": None,
                    "last_error_code": params["error_code"],
                    "updated_at": now,
                }
            )
            self.rowcount = 1
            return
        operation["lease_expires_at"] = now + timedelta(seconds=int(params["lease_seconds"]))
        operation["updated_at"] = now
        self.rowcount = 1

    def fetchall(self) -> list[tuple[Any, ...]]:
        return self.rows

    def fetchone(self) -> tuple[Any, ...] | None:
        return self.rows[0] if self.rows else None


class _FakeConnection:
    def __init__(self, database: _FakeDatabase) -> None:
        self.database = database

    def cursor(self) -> _FakeCursor:
        return _FakeCursor(self.database)

    def commit(self) -> None:
        return None


class _FakeDatabase:
    def __init__(self) -> None:
        self.objects: dict[tuple[str, str], datetime | None] = {}
        self.migrations: dict[str, str] = {}
        self.operation: dict[str, Any] | None = None
        self.active_jobs = 0
        self.fail_drop_or_create_with_lock = False

    @staticmethod
    def idle_operation() -> dict[str, Any]:
        return {
            "status": "IDLE",
            "operation_kind": None,
            "lease_owner": None,
            "lease_expires_at": None,
            "last_error_code": None,
            "schema_epoch": 0,
            "updated_at": datetime.now(UTC),
        }

    @contextmanager
    def connection(self) -> Iterator[_FakeConnection]:
        yield _FakeConnection(self)


def test_schema_manifest_matches_canonical_ddl() -> None:
    assert managed_manifest_from_schema() == set(MANAGED_OBJECTS)
    assert len(MANAGED_TABLES) == len(set(MANAGED_TABLES))
    assert len(MANAGED_INDEXES) == len(set(MANAGED_INDEXES))


def test_schema_status_classifies_all_four_states() -> None:
    ready_objects = set(MANAGED_OBJECTS)
    checksums = {migration.name: migration.checksum for migration in MIGRATIONS}
    assert classify_system_schema_status(set(), {}) == "missing"
    assert (
        classify_system_schema_status(
            {("RAG_DOCUMENTS", "TABLE")},
            {},
        )
        == "partial"
    )
    assert (
        classify_system_schema_status(
            ready_objects,
            {**checksums, MIGRATIONS[-1].name: "changed"},
        )
        == "outdated"
    )
    assert classify_system_schema_status(ready_objects, checksums) == "ready"


def test_initialize_is_idempotent_and_repairs_missing_index() -> None:
    database = _FakeDatabase()
    manager = SystemSchemaManager(database.connection)

    initialized = manager.initialize()
    assert initialized["status"] == "ready"
    assert initialized["operation"] == "initialized"

    no_op = manager.initialize()
    assert no_op["operation"] == "no_op"

    database.objects.pop((MANAGED_INDEXES[-1], "INDEX"))
    repaired = manager.initialize()
    assert repaired["status"] == "ready"
    assert repaired["operation"] == "migrated"
    assert (MANAGED_INDEXES[-1], "INDEX") in database.objects


def test_initialize_replaces_only_explicit_retired_index() -> None:
    database = _FakeDatabase()
    retired_index = ("RAG_INGESTION_SEGMENTS_RECIPE_IDX", "INDEX")
    unmanaged_index = ("CUSTOM_RECIPE_STATUS_IDX", "INDEX")
    database.objects[retired_index] = datetime.now(UTC)
    database.objects[unmanaged_index] = datetime.now(UTC)

    result = SystemSchemaManager(database.connection).initialize()

    assert result["status"] == "ready"
    assert retired_index in RETIRED_MANAGED_OBJECTS
    assert retired_index not in database.objects
    assert unmanaged_index in database.objects
    assert ("RAG_INGESTION_SEGMENTS_RECIPE_STATUS_IDX", "INDEX") in database.objects


def test_checksum_mismatch_reapplies_only_pending_migration() -> None:
    database = _FakeDatabase()
    manager = SystemSchemaManager(database.connection)
    manager.initialize()
    database.migrations[MIGRATIONS[0].name] = "mismatch"

    result = manager.initialize()

    assert result["status"] == "ready"
    assert database.migrations[MIGRATIONS[0].name] == MIGRATIONS[0].checksum


def test_recreate_requires_exact_confirmation_and_preserves_unmanaged_objects() -> None:
    database = _FakeDatabase()
    manager = SystemSchemaManager(database.connection)
    manager.initialize()
    database.objects[("CUSTOM_APPLICATION_TABLE", "TABLE")] = datetime.now(UTC)

    with pytest.raises(SystemSchemaError) as missing_confirmation:
        manager.initialize(recreate=True, confirmation="wrong")
    assert missing_confirmation.value.status_code == 422

    result = manager.initialize(
        recreate=True,
        confirmation=RECREATE_CONFIRMATION,
    )

    assert result["operation"] == "recreated"
    assert result["status"] == "ready"
    assert ("CUSTOM_APPLICATION_TABLE", "TABLE") in database.objects
    assert all((name, "TABLE") in database.objects for name in DOMAIN_TABLES)


def test_recreate_rejects_active_jobs_and_concurrent_lease() -> None:
    database = _FakeDatabase()
    manager = SystemSchemaManager(database.connection)
    manager.initialize()
    database.active_jobs = 1
    with pytest.raises(SystemSchemaActiveJobsError):
        manager.initialize(
            recreate=True,
            confirmation=RECREATE_CONFIRMATION,
        )

    database.active_jobs = 0
    assert database.operation is not None
    database.operation.update(
        {
            "status": "RUNNING",
            "lease_owner": "other",
            "lease_expires_at": datetime.now(UTC) + timedelta(minutes=5),
        }
    )
    with pytest.raises(SystemSchemaBusyError):
        manager.initialize()


def test_ora_00054_is_retryable_and_records_failure() -> None:
    database = _FakeDatabase()
    manager = SystemSchemaManager(database.connection, ddl_lock_timeout_seconds=1)
    manager.initialize()
    database.fail_drop_or_create_with_lock = True

    with pytest.raises(SystemSchemaError) as locked:
        manager.initialize(
            recreate=True,
            confirmation=RECREATE_CONFIRMATION,
        )

    assert locked.value.code == "ORA-00054"
    assert locked.value.status_code == 409
    assert database.operation is not None
    assert database.operation["status"] == "FAILED"
