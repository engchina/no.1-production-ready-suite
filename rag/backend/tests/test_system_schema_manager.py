"""RAG Oracle system schema manager の決定論テスト。"""

from __future__ import annotations

import re
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from pr_system_settings.system_schema import ForeignKeySpec, foreign_keys_from_create_table

from app.rag.system_schema import (
    DOMAIN_TABLES,
    MANAGED_FOREIGN_KEYS,
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
        self.database.executed.append(upper)

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
        if upper.startswith("SELECT TABLE_NAME FROM USER_TABLES"):
            names = {str(value).upper() for value in params.values()}
            self.rows = [
                (name,) for name in sorted(names) if (name, "TABLE") in self.database.objects
            ]
            return
        if upper.startswith("SELECT CHILD.CONSTRAINT_NAME"):
            names = {str(value).upper() for value in params.values()}
            self.rows = [
                (
                    fk.name,
                    fk.table_name,
                    fk.referenced_table_name,
                    fk.delete_rule,
                    "ENABLED",
                    "VALIDATED" if validated else "NOT VALIDATED",
                    column,
                    referenced,
                )
                for fk, validated in sorted(
                    self.database.foreign_keys.values(), key=lambda item: item[0].name
                )
                if fk.table_name in names
                for column, referenced in zip(fk.columns, fk.referenced_columns, strict=True)
            ]
            return
        orphan_match = re.match(r"SELECT COUNT\(\*\) FROM ([A-Z0-9_$#]+) CHILD WHERE", upper)
        if orphan_match is not None:
            self.rows = [(self.database.orphans.get(orphan_match.group(1), 0),)]
            return
        if upper.startswith("ALTER TABLE") and " FOREIGN KEY " in upper:
            self.database.foreign_key_ddl.append(sql)
            self._add_foreign_key(upper)
            return

        if upper.startswith("ALTER SESSION SET DDL_LOCK_TIMEOUT"):
            return
        if upper.startswith("CREATE TABLE"):
            name = upper.split()[2]
            if (name, "TABLE") in self.database.objects:
                raise RuntimeError("ORA-00955: name is already used by an existing object")
            self.database.objects[(name, "TABLE")] = datetime.now(UTC)
            for foreign_key in foreign_keys_from_create_table(statement):
                self.database.foreign_keys[foreign_key.name] = (foreign_key, True)
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
            self.database.foreign_keys = {
                key: value
                for key, value in self.database.foreign_keys.items()
                if value[0].table_name != name
            }
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

    def _add_foreign_key(self, upper: str) -> None:
        """`ALTER TABLE ... ADD CONSTRAINT ... FOREIGN KEY` を Oracle と同じ規則で受ける。"""

        match = re.match(
            r"ALTER TABLE (\S+) ADD CONSTRAINT (\S+) FOREIGN KEY \(([^)]*)\) "
            r"REFERENCES (\S+) \(([^)]*)\)(?: ON DELETE (CASCADE|SET NULL))?( ENABLE NOVALIDATE)?$",
            upper,
        )
        assert match is not None, upper
        table, name, columns, referenced, referenced_columns, delete_rule, novalidate = (
            match.groups()
        )
        spec = ForeignKeySpec(
            name=name,
            table_name=table,
            columns=tuple(item.strip() for item in columns.split(",")),
            referenced_table_name=referenced,
            referenced_columns=tuple(item.strip() for item in referenced_columns.split(",")),
            delete_rule=delete_rule or "NO ACTION",
        )
        if any(fk.signature == spec.signature for fk, _ in self.database.foreign_keys.values()):
            raise RuntimeError("ORA-02275: such a referential constraint already exists")
        if name in self.database.foreign_keys:
            raise RuntimeError("ORA-02264: name already used by an existing constraint")
        if not novalidate and self.database.orphans.get(table, 0):
            raise RuntimeError("ORA-02298: cannot validate - parent keys not found")
        self.database.foreign_keys[name] = (spec, not novalidate)

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
        # 制約名 → (定義, 既存の行を検査済みか)。CREATE TABLE と ALTER TABLE で増える。
        self.foreign_keys: dict[str, tuple[ForeignKeySpec, bool]] = {}
        # 子の表 → 参照先の無い行の件数。
        self.orphans: dict[str, int] = {}
        self.foreign_key_ddl: list[str] = []
        self.executed: list[str] = []

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


# ---- 外部キーの差分（#505） ---------------------------------------------------

# 開発 DB で足りなかった、rag_documents への ON DELETE CASCADE の FK（#485 の調査）。
_DOCUMENT_CASCADE_FOREIGN_KEYS = {
    "RAG_CHUNK_SETS_DOCUMENT_FK": "RAG_CHUNK_SETS",
    "RAG_DOCUMENT_RECIPES_DOCUMENT_FK": "RAG_DOCUMENT_RECIPES",
    "RAG_DOC_EXT_DOCUMENT_FK": "RAG_DOCUMENT_EXTRACTIONS",
    "RAG_DOCUMENT_KNOWLEDGE_BASES_DOC_FK": "RAG_DOCUMENT_KNOWLEDGE_BASES",
    "RAG_ARTIFACT_LAYERS_DOCUMENT_FK": "RAG_ARTIFACT_LAYERS",
}


def _drop_foreign_keys(database: _FakeDatabase, *names: str) -> None:
    """古い版で作った表（FK が無い）を再現する。"""

    for name in names:
        database.foreign_keys.pop(name)


def test_manifest_foreign_keys_come_from_canonical_create_table() -> None:
    by_name = {foreign_key.name: foreign_key for foreign_key in MANAGED_FOREIGN_KEYS}

    assert len(by_name) == len(MANAGED_FOREIGN_KEYS)
    for name, table_name in _DOCUMENT_CASCADE_FOREIGN_KEYS.items():
        foreign_key = by_name[name]
        assert foreign_key.table_name == table_name
        assert foreign_key.columns == ("DOCUMENT_ID",)
        assert foreign_key.referenced_table_name == "RAG_DOCUMENTS"
        assert foreign_key.referenced_columns == ("DOCUMENT_ID",)
        assert foreign_key.delete_rule == "CASCADE"
    # 削除規則の無い FK は NO ACTION として比べる。
    assert by_name["RAG_DOCUMENTS_DUPLICATE_FK"].delete_rule == "NO ACTION"
    # 子の表は RAG の管理対象。参照先は RAG の表か共通認証の PLATFORM_ROLES。
    assert {foreign_key.table_name for foreign_key in MANAGED_FOREIGN_KEYS} <= set(MANAGED_TABLES)
    assert {foreign_key.referenced_table_name for foreign_key in MANAGED_FOREIGN_KEYS} <= {
        *MANAGED_TABLES,
        "PLATFORM_ROLES",
    }


def test_fresh_initialize_creates_tables_with_all_foreign_keys() -> None:
    database = _FakeDatabase()

    result = SystemSchemaManager(database.connection).initialize()

    assert result["status"] == "ready"
    assert result["missing_foreign_keys"] == []
    assert result["orphaned_foreign_keys"] == []
    # 表の作成で FK ごと作られるので、既存の表に足す DDL は出ない。
    assert database.foreign_key_ddl == []
    assert set(database.foreign_keys) >= {fk.name for fk in MANAGED_FOREIGN_KEYS}


def test_status_reports_missing_foreign_keys_and_initialize_adds_them() -> None:
    database = _FakeDatabase()
    manager = SystemSchemaManager(database.connection)
    manager.initialize()
    _drop_foreign_keys(database, *_DOCUMENT_CASCADE_FOREIGN_KEYS)

    status = manager.status()

    assert status["status"] == "outdated"
    assert status["pending_versions"] == []
    assert status["missing_objects"] == []
    assert {item["name"] for item in status["missing_foreign_keys"]} == set(
        _DOCUMENT_CASCADE_FOREIGN_KEYS
    )
    chunk_sets = next(
        item
        for item in status["missing_foreign_keys"]
        if item["name"] == "RAG_CHUNK_SETS_DOCUMENT_FK"
    )
    assert chunk_sets == {
        "name": "RAG_CHUNK_SETS_DOCUMENT_FK",
        "table_name": "RAG_CHUNK_SETS",
        "columns": ["DOCUMENT_ID"],
        "referenced_table_name": "RAG_DOCUMENTS",
        "referenced_columns": ["DOCUMENT_ID"],
        "delete_rule": "CASCADE",
        "orphan_rows": 0,
    }
    assert manager.is_ready() is False

    result = manager.initialize()

    assert result["operation"] == "migrated"
    assert result["status"] == "ready"
    assert result["missing_foreign_keys"] == []
    assert result["orphaned_foreign_keys"] == []
    assert (
        "ALTER TABLE RAG_CHUNK_SETS ADD CONSTRAINT RAG_CHUNK_SETS_DOCUMENT_FK "
        "FOREIGN KEY (DOCUMENT_ID) REFERENCES RAG_DOCUMENTS (DOCUMENT_ID) ON DELETE CASCADE"
    ) in database.foreign_key_ddl
    assert all("NOVALIDATE" not in statement for statement in database.foreign_key_ddl)
    assert all(database.foreign_keys[name][1] for name in _DOCUMENT_CASCADE_FOREIGN_KEYS)
    assert manager.initialize()["operation"] == "no_op"


def test_orphan_rows_add_foreign_key_novalidate_and_are_reported_without_deleting() -> None:
    database = _FakeDatabase()
    manager = SystemSchemaManager(database.connection)
    manager.initialize()
    _drop_foreign_keys(database, "RAG_CHUNK_SETS_DOCUMENT_FK", "RAG_DOC_EXT_DOCUMENT_FK")
    database.orphans["RAG_CHUNK_SETS"] = 240

    before = manager.status()
    orphan_counts = {item["name"]: item["orphan_rows"] for item in before["missing_foreign_keys"]}
    assert orphan_counts == {"RAG_CHUNK_SETS_DOCUMENT_FK": 240, "RAG_DOC_EXT_DOCUMENT_FK": 0}

    result = manager.initialize()

    assert result["status"] == "ready"
    assert result["missing_foreign_keys"] == []
    # 孤立した行がある FK は、既存の行を検査しない NOVALIDATE で足し、件数を警告として出す。
    assert [(item["name"], item["orphan_rows"]) for item in result["orphaned_foreign_keys"]] == [
        ("RAG_CHUNK_SETS_DOCUMENT_FK", 240)
    ]
    assert database.foreign_keys["RAG_CHUNK_SETS_DOCUMENT_FK"][1] is False
    assert database.foreign_keys["RAG_DOC_EXT_DOCUMENT_FK"][1] is True
    assert any(
        statement.startswith("ALTER TABLE RAG_CHUNK_SETS ")
        and statement.endswith(" ENABLE NOVALIDATE")
        for statement in database.foreign_key_ddl
    )
    # 既存の行は利用者の操作なしに消さない。
    assert not any(statement.startswith(("DELETE", "TRUNCATE")) for statement in database.executed)

    # 利用者が孤立した行を片付けたら警告は消える（状態は ready のまま）。
    database.orphans["RAG_CHUNK_SETS"] = 0
    cleaned = manager.status()
    assert cleaned["status"] == "ready"
    assert cleaned["orphaned_foreign_keys"] == []


def test_existing_foreign_key_with_another_name_is_not_added_again() -> None:
    database = _FakeDatabase()
    manager = SystemSchemaManager(database.connection)
    manager.initialize()
    # 旧版の migration が別名で作った、同じ定義の FK。
    spec, validated = database.foreign_keys.pop("RAG_DOC_EXT_DOCUMENT_FK")
    renamed = ForeignKeySpec(
        name="RAG_DOCUMENT_EXTRACTIONS_DOCUMENT_FK",
        table_name=spec.table_name,
        columns=spec.columns,
        referenced_table_name=spec.referenced_table_name,
        referenced_columns=spec.referenced_columns,
        delete_rule=spec.delete_rule,
    )
    database.foreign_keys[renamed.name] = (renamed, validated)

    assert manager.status()["status"] == "ready"
    assert manager.initialize()["operation"] == "no_op"
    assert database.foreign_key_ddl == []


def test_missing_table_is_reported_as_partial_not_as_missing_foreign_key() -> None:
    database = _FakeDatabase()
    manager = SystemSchemaManager(database.connection)
    manager.initialize()
    database.objects.pop(("RAG_CHUNK_SETS", "TABLE"))
    database.foreign_keys = {
        key: value
        for key, value in database.foreign_keys.items()
        if value[0].table_name != "RAG_CHUNK_SETS"
    }

    status = manager.status()

    assert status["status"] == "partial"
    assert status["missing_foreign_keys"] == []
