"""システムテーブルの管理の骨格（`pr_system_settings.system_schema`。#325）の決定論テスト。"""

from __future__ import annotations

import re
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from pr_system_settings.system_schema import (
    RECREATE_CONFIRMATION_REQUIRED,
    SystemSchemaActiveJobsError,
    SystemSchemaBusyError,
    SystemSchemaError,
    SystemSchemaManagerBase,
    SystemTableOperationState,
    SystemTablesInitializeRequest,
    bind_list,
    clamp_ddl_lock_timeout,
    classify_system_schema_status,
    idle_operation_state,
    iso_timestamp,
    oracle_error_code,
    require_recreate_confirmation,
    system_tables_status_error,
)

DOMAIN = ("DEMO_ITEMS", "DEMO_LOGS")
MANAGED = (
    ("DEMO_SCHEMA_OPERATIONS", "TABLE"),
    ("DEMO_ITEMS", "TABLE"),
    ("DEMO_LOGS", "TABLE"),
    ("DEMO_ITEMS_IDX", "INDEX"),
)


# ---- 状態の分類・確認語・安全化 -----------------------------------------------


def _classify(objects: set[tuple[str, str]], *, current: bool = True) -> str:
    return classify_system_schema_status(
        objects,
        domain_tables=DOMAIN,
        managed_objects=MANAGED,
        retired_objects=(("DEMO_OLD_IDX", "INDEX"),),
        migrations_current=current,
    )


def test_status_is_classified_into_four_states() -> None:
    assert _classify(set()) == "missing"
    # 制御テーブルだけでは業務テーブルが無いので missing。
    assert _classify({("DEMO_SCHEMA_OPERATIONS", "TABLE")}) == "missing"
    assert _classify({("DEMO_ITEMS", "TABLE")}) == "partial"
    assert _classify(set(MANAGED), current=False) == "outdated"
    assert _classify(set(MANAGED) | {("DEMO_OLD_IDX", "INDEX")}) == "outdated"
    assert _classify(set(MANAGED)) == "ready"


def test_recreate_confirmation_requires_exact_match() -> None:
    require_recreate_confirmation(recreate=False, confirmation=None, expected="RECREATE_DEMO")
    require_recreate_confirmation(
        recreate=True, confirmation="RECREATE_DEMO", expected="RECREATE_DEMO"
    )
    for wrong in (None, "", "recreate_demo", " RECREATE_DEMO"):
        with pytest.raises(SystemSchemaError) as error:
            require_recreate_confirmation(
                recreate=True, confirmation=wrong, expected="RECREATE_DEMO"
            )
        assert error.value.code == RECREATE_CONFIRMATION_REQUIRED
        assert error.value.status_code == 422


def test_errors_expose_only_ora_codes() -> None:
    assert oracle_error_code(RuntimeError("password=secret ora-00942: missing")) == "ORA-00942"
    assert oracle_error_code(RuntimeError("password=secret")) == "SCHEMA_OPERATION_FAILED"

    unavailable = system_tables_status_error(RuntimeError("dsn=secret ORA-12514"))
    assert unavailable.status_code == 503
    assert unavailable.detail == "システムテーブルの状態を取得できませんでした (ORA-12514)。"
    hidden = system_tables_status_error(RuntimeError("dsn=secret"))
    assert "secret" not in str(hidden.detail)
    assert "SCHEMA_STATUS_UNAVAILABLE" in str(hidden.detail)


def test_only_lock_timeout_is_retryable() -> None:
    locked = SystemSchemaError("ORA-00054", "locked", status_code=409)
    assert locked.retryable is True
    assert locked.retry_headers == {"Retry-After": "5"}
    other = SystemSchemaError("ORA-00600", "failed")
    assert other.retryable is False
    assert other.retry_headers is None
    jobs = SystemSchemaActiveJobsError("実行中の job があります。")
    assert (jobs.code, jobs.status_code, jobs.public_message) == (
        "SCHEMA_JOBS_RUNNING",
        409,
        "実行中の job があります。",
    )


def test_small_helpers() -> None:
    assert bind_list("name_", ["A", "B"]) == (":name_0, :name_1", {"name_0": "A", "name_1": "B"})
    assert iso_timestamp(None) is None
    assert iso_timestamp(datetime(2026, 1, 2, 3, 4, 5)) == "2026-01-02T03:04:05+00:00"
    assert clamp_ddl_lock_timeout(-1) == 0
    assert clamp_ddl_lock_timeout(999) == 120
    assert idle_operation_state()["status"] == "idle"


def test_api_models_keep_the_contract() -> None:
    state = SystemTableOperationState.model_validate(idle_operation_state())
    assert state.schema_epoch == 0
    assert SystemTablesInitializeRequest().model_dump() == {"recreate": False, "confirmation": None}
    with pytest.raises(ValueError):
        SystemTablesInitializeRequest(recreate=True, confirmation="X" * 129)


# ---- lease・台帳・初期化の手順（fake の Oracle） --------------------------------


class _FakeDatabase:
    def __init__(self) -> None:
        self.objects: dict[tuple[str, str], datetime | None] = {}
        self.migrations: dict[str, str] = {}
        self.operation: dict[str, Any] = {
            "status": "IDLE",
            "operation_kind": None,
            "lease_owner": None,
            "lease_expires_at": None,
            "last_error_code": None,
            "schema_epoch": 0,
            "updated_at": datetime.now(UTC),
        }
        self.statements: list[str] = []

    @contextmanager
    def connection(self) -> Iterator[_FakeConnection]:
        yield _FakeConnection(self)


class _FakeConnection:
    def __init__(self, database: _FakeDatabase) -> None:
        self.database = database

    def cursor(self) -> _FakeCursor:
        return _FakeCursor(self.database)

    def commit(self) -> None:
        return None


class _FakeCursor:
    def __init__(self, database: _FakeDatabase) -> None:
        self.database = database
        self.rows: list[tuple[Any, ...]] = []
        self.rowcount = 0

    def __enter__(self) -> _FakeCursor:
        return self

    def __exit__(self, *_args: object) -> None:
        return None

    def fetchall(self) -> list[tuple[Any, ...]]:
        return self.rows

    def fetchone(self) -> tuple[Any, ...] | None:
        return self.rows[0] if self.rows else None

    def execute(self, statement: str, params: dict[str, Any] | None = None) -> None:
        sql = re.sub(r"\s+", " ", statement).strip().upper()
        params = params or {}
        self.database.statements.append(sql)
        self.rows = []
        self.rowcount = 0
        operation = self.database.operation
        now = datetime.now(UTC)
        if sql.startswith("SELECT STATUS, OPERATION_KIND"):
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
        elif sql.startswith("SELECT MIGRATION_NAME, CHECKSUM FROM DEMO_SCHEMA_MIGRATIONS"):
            self.rows = sorted(self.database.migrations.items())
        elif sql.startswith("SELECT TABLE_NAME, NUM_ROWS, LAST_ANALYZED FROM USER_TABLES"):
            self.rows = [(str(value), 12, None) for value in params.values()]
        elif sql.startswith("MERGE INTO DEMO_SCHEMA_MIGRATIONS"):
            self.database.migrations[str(params["migration_name"])] = str(params["checksum"])
        elif sql.startswith("DROP "):
            name = sql.split()[2]
            if not any(key[0] == name for key in self.database.objects):
                raise RuntimeError("ORA-00942: table or view does not exist")
            self.database.objects = {
                key: value for key, value in self.database.objects.items() if key[0] != name
            }
        elif "SET STATUS = 'RUNNING'" in sql:
            expires = operation["lease_expires_at"]
            if operation["status"] == "RUNNING" and expires is not None and expires >= now:
                return
            operation.update(
                status="RUNNING",
                operation_kind=params["operation_kind"],
                lease_owner=params["lease_owner"],
                lease_expires_at=now + timedelta(seconds=int(params["lease_seconds"])),
                last_error_code=None,
            )
            self.rowcount = 1
        elif sql.startswith("UPDATE DEMO_SCHEMA_OPERATIONS"):
            if operation["lease_owner"] != params.get("lease_owner"):
                return
            if "SET STATUS = 'IDLE'" in sql:
                if "SCHEMA_EPOCH = SCHEMA_EPOCH + 1" in sql:
                    operation["schema_epoch"] += 1
                operation.update(
                    status="IDLE", operation_kind=None, lease_owner=None, lease_expires_at=None
                )
            elif "SET STATUS = 'FAILED'" in sql:
                operation.update(
                    status="FAILED",
                    operation_kind=None,
                    lease_owner=None,
                    lease_expires_at=None,
                    last_error_code=params["error_code"],
                )
            elif operation["status"] == "RUNNING":
                operation["lease_expires_at"] = now + timedelta(
                    seconds=int(params["lease_seconds"])
                )
            else:
                return
            self.rowcount = 1


class _DemoManager(SystemSchemaManagerBase):
    control_table = "DEMO_SCHEMA_OPERATIONS"
    migration_table = "DEMO_SCHEMA_MIGRATIONS"
    migration_key_column = "MIGRATION_NAME"
    managed_tables = ("DEMO_SCHEMA_OPERATIONS", "DEMO_SCHEMA_MIGRATIONS", "DEMO_ITEMS")
    recreate_confirmation = "RECREATE_DEMO_SYSTEM_TABLES"
    log_prefix = "demo"
    lock_timeout_guidance = "デモの job を止めてから、"

    def __init__(self, database: _FakeDatabase, *, fail_with: str | None = None) -> None:
        super().__init__(database.connection, lease_seconds=1, ddl_lock_timeout_seconds=7)
        self.database = database
        self.fail_with = fail_with
        self.control_schema_calls = 0

    def _ensure_control_schema(self) -> None:
        self.control_schema_calls += 1
        self.database.objects[("DEMO_SCHEMA_OPERATIONS", "TABLE")] = datetime.now(UTC)
        self.database.objects[("DEMO_SCHEMA_MIGRATIONS", "TABLE")] = datetime.now(UTC)

    def _status_on(self, connection: Any) -> dict[str, Any]:
        objects = dict(self.database.objects)
        applied = self._load_migrations(connection, objects)
        return {
            "status": classify_system_schema_status(
                set(objects),
                domain_tables=("DEMO_ITEMS",),
                managed_objects=(("DEMO_ITEMS", "TABLE"),),
                migrations_current=applied.get("001") == "sum-1",
            ),
            "existing_object_count": len(objects),
            "tables": self._load_table_metadata(connection, objects, owner="DEMO"),
            "operation_state": self._operation_payload(connection, objects),
        }

    def _table_identity_fields(self, name: str, owner: str) -> dict[str, Any]:
        return {"qualified_name": f"{owner}.{name}"}

    def _initialize_on(self, connection: Any, owner: str, *, recreate: bool) -> dict[str, Any]:
        before = self._status_on(connection)
        if before["status"] == "ready" and not recreate:
            return self._no_op_result(connection, owner)
        self._configure_ddl_lock_timeout(connection)
        if self.fail_with:
            raise RuntimeError(self.fail_with)
        dropped = self._execute_drop(connection, "DROP TABLE DEMO_ITEMS") if recreate else 0
        self._heartbeat(connection, owner)
        self.database.objects[("DEMO_ITEMS", "TABLE")] = datetime.now(UTC)
        with connection.cursor() as cursor:
            self._merge_migration(cursor, key="001", description="items", checksum="sum-1")
        self._finish_operation(connection, owner, increment_epoch=True)
        after = self._status_on(connection)
        return {
            **after,
            "operation": self._operation_name(before["status"], recreate=recreate),
            "dropped_object_count": dropped,
        }


def test_initialize_then_no_op_increments_epoch_only_on_change() -> None:
    database = _FakeDatabase()
    manager = _DemoManager(database)

    initialized = manager.initialize()
    assert initialized["operation"] == "initialized"
    assert initialized["status"] == "ready"
    assert initialized["operation_state"]["schema_epoch"] == 1
    assert initialized["operation_state"]["status"] == "idle"
    assert "ALTER SESSION SET DDL_LOCK_TIMEOUT = 7" in database.statements
    assert manager.is_ready() is True

    no_op = manager.initialize()
    assert no_op["operation"] == "no_op"
    assert (no_op["dropped_object_count"], no_op["created_object_count"]) == (0, 0)
    assert no_op["operation_state"]["schema_epoch"] == 1


def test_recreate_checks_confirmation_before_touching_the_database() -> None:
    database = _FakeDatabase()
    manager = _DemoManager(database)

    with pytest.raises(SystemSchemaError) as error:
        manager.initialize(recreate=True, confirmation="RECREATE")
    assert error.value.status_code == 422
    assert manager.control_schema_calls == 0
    assert database.statements == []

    manager.initialize()
    recreated = manager.initialize(recreate=True, confirmation="RECREATE_DEMO_SYSTEM_TABLES")
    assert recreated["operation"] == "recreated"
    assert recreated["dropped_object_count"] == 1
    assert recreated["operation_state"]["schema_epoch"] == 2
    # 既に無い object の DROP は 0 件として読み飛ばす。
    with database.connection() as connection:
        assert manager._execute_drop(connection, "DROP TABLE DEMO_UNKNOWN") == 0


def test_lease_rejects_concurrent_operation_and_allows_expired_takeover() -> None:
    database = _FakeDatabase()
    manager = _DemoManager(database)
    manager._ensure_control_schema()

    manager._claim_lease("first", "initialize")
    assert database.operation["operation_kind"] == "initialize"
    with pytest.raises(SystemSchemaBusyError) as busy:
        manager.initialize()
    assert busy.value.status_code == 409
    # 他者の lease の延長・終了はできない。
    with database.connection() as connection:
        with pytest.raises(SystemSchemaBusyError):
            manager._heartbeat(connection, "second")
        with pytest.raises(SystemSchemaBusyError):
            manager._finish_operation(connection, "second", increment_epoch=True)
    assert database.operation["lease_owner"] == "first"

    database.operation["lease_expires_at"] = datetime.now(UTC) - timedelta(seconds=1)
    assert manager.initialize()["status"] == "ready"


def test_failure_is_recorded_safely_and_can_be_retried() -> None:
    database = _FakeDatabase()
    manager = _DemoManager(database, fail_with="password=secret ORA-00600 full sql")

    with pytest.raises(SystemSchemaError) as error:
        manager.initialize()
    assert error.value.code == "ORA-00600"
    assert error.value.status_code == 500
    assert "secret" not in error.value.public_message
    assert database.operation["status"] == "FAILED"
    assert database.operation["last_error_code"] == "ORA-00600"
    assert manager.status()["operation_state"]["last_error_code"] == "ORA-00600"

    manager.fail_with = None
    assert manager.initialize()["operation"] == "initialized"


def test_lock_timeout_is_a_retryable_conflict_with_product_guidance() -> None:
    database = _FakeDatabase()
    manager = _DemoManager(database, fail_with="ORA-00054: resource busy")

    with pytest.raises(SystemSchemaError) as error:
        manager.initialize()
    assert error.value.code == "ORA-00054"
    assert error.value.status_code == 409
    assert error.value.retry_headers == {"Retry-After": "5"}
    assert error.value.public_message == (
        "Oracle の対象オブジェクトのロックが 7 秒以内に解放されませんでした (ORA-00054)。"
        "デモの job を止めてから、状態を再取得して再試行してください。"
    )


def test_status_reports_table_metadata_and_idle_state_without_control_table() -> None:
    database = _FakeDatabase()
    manager = _DemoManager(database)

    status = manager.status()
    assert status["status"] == "missing"
    assert status["operation_state"] == idle_operation_state()
    assert [table["exists"] for table in status["tables"]] == [False, False, False]

    manager.initialize()
    tables = {table["name"]: table for table in manager.status()["tables"]}
    assert tables["DEMO_ITEMS"]["exists"] is True
    assert tables["DEMO_ITEMS"]["estimated_rows"] == 12
    assert tables["DEMO_ITEMS"]["qualified_name"] == "DEMO.DEMO_ITEMS"
