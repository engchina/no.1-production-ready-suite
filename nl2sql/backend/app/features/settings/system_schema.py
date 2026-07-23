"""NL2SQL の versioned system table を明示操作する共有 manager。"""

from __future__ import annotations

import hashlib
import logging
import re
import uuid
from collections.abc import Callable, Iterator, Sequence
from contextlib import AbstractContextManager, contextmanager, suppress
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

from app.clients.oracle_runtime import get_oracle_pool_manager
from app.settings import get_settings

logger = logging.getLogger(__name__)

SystemSchemaStatus = Literal["missing", "partial", "outdated", "ready"]
SystemSchemaOperation = Literal["no_op", "initialized", "migrated", "recreated"]

RECREATE_CONFIRMATION = "RECREATE_NL2SQL_SYSTEM_TABLES"
CONTROL_TABLE = "NL2SQL_SCHEMA_OPERATIONS"
MIGRATION_TABLE = "NL2SQL_SCHEMA_MIGRATIONS"
CONTROL_KEY = "system_schema"

_IGNORED_APPLY_CODES = frozenset(
    {
        "ORA-00001",  # idempotent seed / migration ledger insert
        "ORA-00955",  # object already exists
        "ORA-01430",  # column already exists
        "ORA-01442",  # column is already NOT NULL
        "ORA-01451",  # column is already NULL
    }
)
_IGNORED_DROP_CODES = frozenset({"ORA-00942", "ORA-01418", "ORA-02289"})
_ACTIVE_JOB_STATES = ("ACCEPTED", "PENDING", "QUEUED", "RUNNING", "PROCESSING")
_CREATE_OBJECT_PATTERN = re.compile(
    r"\bCREATE\s+(?:UNIQUE\s+|VECTOR\s+)?"
    r"(TABLE|INDEX|SEQUENCE)\s+([A-Z][A-Z0-9_$#]*)",
    flags=re.IGNORECASE,
)
_ADD_CONSTRAINT_PATTERN = re.compile(
    r"^\s*ALTER\s+TABLE\s+([A-Z][A-Z0-9_$#]*)\s+"
    r"ADD\s+CONSTRAINT\s+([A-Z][A-Z0-9_$#]*)\b",
    flags=re.IGNORECASE | re.DOTALL,
)


@dataclass(frozen=True, slots=True)
class MigrationArtifact:
    version: int
    filename: str
    description: str

    @property
    def path(self) -> Path:
        return Path(__file__).resolve().parents[3] / "migrations" / self.filename

    @property
    def checksum(self) -> str:
        return hashlib.sha256(self.path.read_bytes()).hexdigest()

    @property
    def created_objects(self) -> frozenset[tuple[str, str]]:
        """この artifact が所有する CREATE object を決定論的に抽出する。"""

        return frozenset(
            (name.upper(), object_type.upper())
            for object_type, name in _CREATE_OBJECT_PATTERN.findall(
                self.path.read_text(encoding="utf-8")
            )
        )


@dataclass(frozen=True, slots=True)
class ManagedForeignKey:
    """再試行時に定義まで検証する system schema の外部キー。"""

    name: str
    table_name: str
    columns: tuple[str, ...]
    referenced_table_name: str
    referenced_columns: tuple[str, ...]
    delete_rule: str = "CASCADE"


MIGRATIONS: tuple[MigrationArtifact, ...] = (
    MigrationArtifact(0, "000_system_schema_control.sql", "system schema control"),
    MigrationArtifact(1, "001_ontology_store.sql", "ontology store"),
    MigrationArtifact(2, "002_ontology_semantics.sql", "ontology semantics"),
    MigrationArtifact(3, "003_incremental_nl2sql_state.sql", "incremental nl2sql state"),
    MigrationArtifact(
        5,
        "005_feedback_classifier_learning.sql",
        "feedback classifier learning metadata",
    ),
    MigrationArtifact(6, "006_incremental_job_leases.sql", "incremental job leases"),
    MigrationArtifact(
        7,
        "007_profile_ontology_lifecycle.sql",
        "profile ontology lifecycle integrity",
    ),
    MigrationArtifact(
        8,
        "008_quality_evaluation_jobs.sql",
        "durable NL2SQL quality evaluation jobs",
    ),
)

# DROP 対象は必ずこの manifest に明記する。NL2SQL_* の prefix scan は使用しない。
MANAGED_TABLES: tuple[str, ...] = (
    CONTROL_TABLE,
    MIGRATION_TABLE,
    "NL2SQL_ONTOLOGY_REVISIONS",
    "NL2SQL_ONTOLOGY_NODES",
    "NL2SQL_ONTOLOGY_EDGES",
    "NL2SQL_ONTOLOGY_PROFILE_VIEWS",
    "NL2SQL_ONTOLOGY_QUERY_SESSIONS",
    "NL2SQL_ONTOLOGY_ARTIFACTS",
    "NL2SQL_ONTOLOGY_PROPOSALS",
    "NL2SQL_ONTOLOGY_IDEMPOTENCY",
    "NL2SQL_ONTOLOGY_SOURCE_DOCS",
    "NL2SQL_ONTOLOGY_JOBS",
    "NL2SQL_ONTOLOGY_RECOMMENDATIONS",
    "NL2SQL_ONTOLOGY_RDF_DATA",
    "NL2SQL_CHANGE_TOKENS",
    "NL2SQL_PROFILES",
    "NL2SQL_SCHEMA_CATALOG_HEAD",
    "NL2SQL_SCHEMA_OBJECTS",
    "NL2SQL_SCHEMA_COLUMNS",
    "NL2SQL_SCHEMA_CONSTRAINTS",
    "NL2SQL_SCHEMA_DEPENDENCIES",
    "NL2SQL_SCHEMA_SAMPLES",
    "NL2SQL_SCHEMA_REFRESH_JOBS",
    "NL2SQL_STATE_DOCUMENTS",
    "NL2SQL_MIGRATION_OUTBOX",
    "NL2SQL_ONTOLOGY_PROFILE_VIEW_REVISIONS",
    "NL2SQL_EVALUATION_JOBS",
    "NL2SQL_EVALUATION_RESULTS",
)

MANAGED_INDEXES: tuple[str, ...] = (
    "IX_NL2SQL_ONT_NODE_PHYSICAL",
    "IX_NL2SQL_ONT_NODE_EMBED",
    "IX_NL2SQL_ONT_EDGE_SOURCE",
    "IX_NL2SQL_ONT_EDGE_TARGET",
    "IX_NL2SQL_ONT_SESSION_PROFILE",
    "IX_NL2SQL_ONT_ART_SESSION",
    "IX_NL2SQL_ONT_PROP_SESSION",
    "IX_NL2SQL_ONT_IDEMPOTENCY_RESOURCE",
    "IX_NL2SQL_ONT_SOURCE_PROFILE",
    "IX_NL2SQL_ONT_JOB_STATE",
    "IX_NL2SQL_ONT_REC_QUESTION",
    "UX_NL2SQL_ONT_ONE_PUBLISHED",
    "IX_NL2SQL_PROFILES_LIST",
    "IX_NL2SQL_SCHEMA_OBJECT_LIST",
    "IX_NL2SQL_SCHEMA_COLUMN_SEARCH",
    "IX_NL2SQL_SCHEMA_REFRESH_STATE",
    "IX_NL2SQL_STATE_DOCUMENT_LIST",
    "UX_NL2SQL_MIGRATION_OUTBOX_VERSION",
    "IX_NL2SQL_ONT_VIEW_REVISION",
    "IX_NL2SQL_ONT_PROPOSAL_PROFILE",
    "IX_NL2SQL_SCHEMA_REFRESH_LEASE",
    "IX_NL2SQL_EVAL_JOB_STATE",
    "IX_NL2SQL_EVAL_JOB_LEASE",
)

MANAGED_SEQUENCES: tuple[str, ...] = (
    "NL2SQL_ONTOLOGY_RDF_SEQ",
    "NL2SQL_MIGRATION_SNAPSHOT_SEQ",
)

MANAGED_OBJECTS: tuple[tuple[str, str], ...] = (
    *((name, "TABLE") for name in MANAGED_TABLES),
    *((name, "INDEX") for name in MANAGED_INDEXES),
    *((name, "SEQUENCE") for name in MANAGED_SEQUENCES),
)
DOMAIN_TABLES = frozenset(MANAGED_TABLES) - {CONTROL_TABLE, MIGRATION_TABLE}

# Explicitly preserved even though some names share the NL2SQL namespace.
PRESERVED_TABLES = frozenset(
    {
        "NL2SQL_APP_USERS",
        "NL2SQL_APP_ROLES",
        "NL2SQL_APP_USER_ROLES",
        "NL2SQL_APP_ROLE_PERMISSIONS",
        "NL2SQL_APP_DATA_ENTITLEMENTS",
        "NL2SQL_AUTH_SESSIONS",
        "NL2SQL_AUTH_AUDIT_LOG",
        "NL2SQL_DEEPSEC_MIGRATIONS",
        "NL2SQL_FEEDBACK_VECTORS",
    }
)

MANAGED_FOREIGN_KEYS: tuple[ManagedForeignKey, ...] = (
    ManagedForeignKey(
        name="FK_NL2SQL_ONT_VIEW_REV_PROFILE",
        table_name="NL2SQL_ONTOLOGY_PROFILE_VIEW_REVISIONS",
        columns=("PROFILE_ID",),
        referenced_table_name="NL2SQL_PROFILES",
        referenced_columns=("PROFILE_ID",),
    ),
    ManagedForeignKey(
        name="FK_NL2SQL_ONT_VIEW_PROFILE",
        table_name="NL2SQL_ONTOLOGY_PROFILE_VIEWS",
        columns=("PROFILE_ID",),
        referenced_table_name="NL2SQL_PROFILES",
        referenced_columns=("PROFILE_ID",),
    ),
)
_MANAGED_FOREIGN_KEYS_BY_NAME = {
    constraint.name: constraint for constraint in MANAGED_FOREIGN_KEYS
}


class SystemSchemaError(RuntimeError):
    """secret や SQL を含めない schema operation error。"""

    def __init__(self, code: str, public_message: str, *, status_code: int = 500) -> None:
        super().__init__(public_message)
        self.code = code
        self.public_message = public_message
        self.status_code = status_code


class SystemSchemaBusyError(SystemSchemaError):
    def __init__(self) -> None:
        super().__init__(
            "SCHEMA_OPERATION_IN_PROGRESS",
            "別のシステムテーブル操作が実行中です。完了後に状態を再取得してください。",
            status_code=409,
        )


class SystemSchemaActiveJobsError(SystemSchemaError):
    def __init__(self) -> None:
        super().__init__(
            "SCHEMA_JOBS_RUNNING",
            "実行中の schema refresh、Ontology、または品質評価 job があります。"
            "完了または停止してから再実行してください。",
            status_code=409,
        )


def split_migration_sql(sql: str) -> list[str]:
    """現在の versioned migration（単純 SQL）を comment 除外して分割する。"""

    statements: list[str] = []
    current: list[str] = []
    for line in sql.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("--"):
            continue
        current.append(line)
        if stripped.endswith(";"):
            statement = "\n".join(current).rstrip().rstrip(";").strip()
            if statement:
                statements.append(statement)
            current = []
    if current:
        statement = "\n".join(current).strip()
        if statement:
            statements.append(statement)
    return statements


def oracle_error_code(exc: Exception) -> str:
    match = re.search(r"ORA-\d{5}", str(exc), flags=re.IGNORECASE)
    return match.group(0).upper() if match else "SCHEMA_OPERATION_FAILED"


def _iso(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        current = value if value.tzinfo else value.replace(tzinfo=UTC)
        return current.isoformat()
    return str(value)


def _bind_list(prefix: str, values: Sequence[str]) -> tuple[str, dict[str, str]]:
    binds = {f"{prefix}{index}": value for index, value in enumerate(values)}
    return ", ".join(f":{name}" for name in binds), binds


def classify_system_schema_status(
    objects: set[tuple[str, str]],
    applied_checksums: dict[int, str],
) -> SystemSchemaStatus:
    """Dictionary / ledger snapshot を四つの公開状態へ決定論的に分類する。"""

    expected = set(MANAGED_OBJECTS)
    existing_domain = {name for name in DOMAIN_TABLES if (name, "TABLE") in objects}
    if not existing_domain:
        return "missing"
    if expected - objects:
        return "partial"
    if any(
        applied_checksums.get(migration.version) != migration.checksum
        for migration in MIGRATIONS
    ):
        return "outdated"
    return "ready"


class SystemSchemaManager:
    """Status / initialize / recreate を同じ manifest と lease で提供する。"""

    def __init__(
        self,
        connection_factory: Callable[[], AbstractContextManager[Any]] | None = None,
        *,
        lease_seconds: int = 900,
        ddl_lock_timeout_seconds: int | None = None,
    ) -> None:
        self._connection_factory = connection_factory or self._default_connection
        self._lease_seconds = lease_seconds
        configured_timeout = (
            get_settings().nl2sql_system_schema_ddl_lock_timeout_seconds
            if ddl_lock_timeout_seconds is None
            else ddl_lock_timeout_seconds
        )
        self._ddl_lock_timeout_seconds = max(0, min(120, int(configured_timeout)))

    @staticmethod
    @contextmanager
    def _default_connection() -> Iterator[Any]:
        with get_oracle_pool_manager().control_connection() as connection:
            yield connection

    def status(self) -> dict[str, Any]:
        """USER_* dictionary と migration ledger だけを読む。DDL は実行しない。"""

        with self._connection_factory() as connection:
            return self._status_on(connection)

    def current_epoch(self) -> int | None:
        """別 replica の cache invalidation 用 epoch。control table 未作成時は None。"""

        with self._connection_factory() as connection:
            objects = self._load_objects(connection)
            if (CONTROL_TABLE, "TABLE") not in objects:
                return None
            operation = self._load_operation(connection)
            return int(operation["schema_epoch"])

    def initialize(
        self,
        *,
        recreate: bool = False,
        confirmation: str | None = None,
    ) -> dict[str, Any]:
        if recreate and confirmation != RECREATE_CONFIRMATION:
            raise SystemSchemaError(
                "SCHEMA_RECREATE_CONFIRMATION_REQUIRED",
                "すべて再作成するには確認値を正確に入力してください。",
                status_code=422,
            )

        owner = uuid.uuid4().hex
        kind = "recreate" if recreate else "initialize"
        self._ensure_control_schema()
        self._claim_lease(owner, kind)
        before: dict[str, Any] | None = None
        applied_versions: list[int] = []
        dropped_count = 0
        try:
            with self._connection_factory() as connection:
                before = self._status_on(connection)
                if before["status"] == "ready" and not recreate:
                    self._finish_operation(connection, owner, increment_epoch=False)
                    return {
                        **self._status_on(connection),
                        "operation": "no_op",
                        "dropped_object_count": 0,
                        "created_object_count": 0,
                    }
                self._configure_ddl_lock_timeout(connection)
                if recreate:
                    self._assert_no_active_jobs(connection)
                    dropped_count = self._drop_managed_objects(connection, owner)
                    migrations_to_apply = list(MIGRATIONS)
                else:
                    migrations_to_apply = self._plan_migrations(before)
                for migration in migrations_to_apply:
                    self._heartbeat(connection, owner)
                    self._apply_migration(connection, migration)
                    applied_versions.append(migration.version)
                    self._heartbeat(connection, owner)
                self._finish_operation(connection, owner, increment_epoch=True)
                after = self._status_on(connection)
                if after["status"] != "ready":
                    raise SystemSchemaError(
                        "SCHEMA_POSTCONDITION_FAILED",
                        "システムテーブル操作は完了しましたが、必須オブジェクトが不足しています。状態を再取得して再試行してください。",
                    )
                previous_existing = int(before["existing_object_count"])
                operation: SystemSchemaOperation
                if recreate:
                    operation = "recreated"
                elif before["status"] == "missing":
                    operation = "initialized"
                else:
                    operation = "migrated"
                logger.info(
                    "nl2sql_system_schema_operation_succeeded",
                    extra={
                        "operation": operation,
                        "schema_epoch": after["operation_state"]["schema_epoch"],
                        "applied_versions": applied_versions,
                        "dropped_object_count": dropped_count,
                    },
                )
                return {
                    **after,
                    "operation": operation,
                    "dropped_object_count": dropped_count,
                    "created_object_count": (
                        int(after["existing_object_count"]) - 1
                        if recreate
                        else max(0, int(after["existing_object_count"]) - previous_existing)
                    ),
                }
        except SystemSchemaError as exc:
            self._record_failure(owner, exc.code)
            logger.error(
                "nl2sql_system_schema_operation_failed",
                extra={
                    "operation": kind,
                    "error_code": exc.code,
                    "exception_type": type(exc).__name__,
                },
            )
            raise
        except Exception as exc:
            code = oracle_error_code(exc)
            self._record_failure(owner, code)
            logger.error(
                "nl2sql_system_schema_operation_failed",
                extra={
                    "operation": kind,
                    "error_code": code,
                    "exception_type": type(exc).__name__,
                },
            )
            if code == "ORA-00054":
                raise SystemSchemaError(
                    code,
                    "Oracle の対象オブジェクトのロックが "
                    f"{self._ddl_lock_timeout_seconds} 秒以内に解放されませんでした "
                    "(ORA-00054)。実行中の schema refresh、Ontology、品質評価 job "
                    "を完了または停止してから、状態を再取得して再試行してください。",
                    status_code=409,
                ) from exc
            raise SystemSchemaError(
                code,
                f"システムテーブル操作に失敗しました ({code})。"
                "状態を再取得して再試行してください。",
            ) from exc

    def _plan_migrations(self, status: dict[str, Any]) -> list[MigrationArtifact]:
        """未適用/checksum 不一致と欠損 object の所有 migration だけを選ぶ。"""

        pending_versions = {int(version) for version in status["pending_versions"]}
        missing_objects = {
            (str(item["name"]).upper(), str(item["object_type"]).upper())
            for item in status["missing_objects"]
        }
        owned_objects = frozenset(
            object_key
            for migration in MIGRATIONS
            for object_key in migration.created_objects
        )
        uncovered = missing_objects - owned_objects
        if uncovered:
            labels = ", ".join(f"{kind}:{name}" for name, kind in sorted(uncovered))
            raise SystemSchemaError(
                "SCHEMA_MIGRATION_MANIFEST_INCOMPLETE",
                f"不足オブジェクトに対応する migration がありません ({labels})。",
            )
        return [
            migration
            for migration in MIGRATIONS
            if migration.version in pending_versions
            or bool(migration.created_objects.intersection(missing_objects))
        ]

    def _status_on(self, connection: Any) -> dict[str, Any]:
        objects = self._load_objects(connection)
        expected = set(MANAGED_OBJECTS)
        existing = expected.intersection(objects)
        missing = sorted(
            (
                {"name": name, "object_type": object_type}
                for name, object_type in expected - existing
            ),
            key=lambda item: (item["object_type"], item["name"]),
        )
        applied = self._load_migrations(connection, objects)
        matching_versions = [
            item.version for item in MIGRATIONS if applied.get(item.version) == item.checksum
        ]
        pending_versions = [
            item.version for item in MIGRATIONS if applied.get(item.version) != item.checksum
        ]
        status = classify_system_schema_status(set(objects), applied)
        table_metadata = self._load_table_metadata(connection, objects)
        return {
            "status": status,
            "schema_head": MIGRATIONS[-1].version,
            "applied_versions": sorted(matching_versions),
            "pending_versions": pending_versions,
            "expected_object_count": len(expected),
            "existing_object_count": len(existing),
            "expected_table_count": len(MANAGED_TABLES),
            "existing_table_count": sum(
                (name, "TABLE") in objects for name in MANAGED_TABLES
            ),
            "missing_objects": missing,
            "tables": table_metadata,
            "operation_state": self._operation_payload(connection, objects),
        }

    def _load_objects(self, connection: Any) -> dict[tuple[str, str], Any]:
        names = tuple(dict.fromkeys(name for name, _object_type in MANAGED_OBJECTS))
        placeholders, binds = _bind_list("object_name_", names)
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT OBJECT_NAME, OBJECT_TYPE, CREATED FROM USER_OBJECTS "
                f"WHERE OBJECT_NAME IN ({placeholders}) "  # nosec B608 - fixed manifest binds
                "AND OBJECT_TYPE IN ('TABLE', 'INDEX', 'SEQUENCE')",
                binds,
            )
            return {
                (str(row[0]).upper(), str(row[1]).upper()): row[2]
                for row in cursor.fetchall()
            }

    def _load_migrations(
        self,
        connection: Any,
        objects: dict[tuple[str, str], Any],
    ) -> dict[int, str]:
        if (MIGRATION_TABLE, "TABLE") not in objects:
            return {}
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT VERSION_NO, CHECKSUM FROM NL2SQL_SCHEMA_MIGRATIONS "
                "ORDER BY VERSION_NO"
            )
            return {int(row[0]): str(row[1]) for row in cursor.fetchall()}

    def _load_table_metadata(
        self,
        connection: Any,
        objects: dict[tuple[str, str], Any],
    ) -> list[dict[str, Any]]:
        existing_names = [name for name in MANAGED_TABLES if (name, "TABLE") in objects]
        metadata: dict[str, tuple[Any, Any]] = {}
        if existing_names:
            placeholders, binds = _bind_list("table_name_", existing_names)
            with connection.cursor() as cursor:
                cursor.execute(
                    "SELECT TABLE_NAME, NUM_ROWS, LAST_ANALYZED FROM USER_TABLES "
                    f"WHERE TABLE_NAME IN ({placeholders})",  # nosec B608 - fixed manifest binds
                    binds,
                )
                metadata = {
                    str(row[0]).upper(): (row[1], row[2]) for row in cursor.fetchall()
                }
        return [
            {
                "name": name,
                "exists": (name, "TABLE") in objects,
                "estimated_rows": (
                    int(metadata[name][0])
                    if name in metadata and metadata[name][0] is not None
                    else None
                ),
                "created_at": _iso(objects.get((name, "TABLE"))),
                "last_analyzed_at": _iso(metadata[name][1]) if name in metadata else None,
            }
            for name in MANAGED_TABLES
        ]

    def _operation_payload(
        self,
        connection: Any,
        objects: dict[tuple[str, str], Any],
    ) -> dict[str, Any]:
        if (CONTROL_TABLE, "TABLE") not in objects:
            return {
                "status": "idle",
                "operation_kind": None,
                "lease_expires_at": None,
                "last_error_code": None,
                "schema_epoch": 0,
                "updated_at": None,
            }
        return self._load_operation(connection)

    def _load_operation(self, connection: Any) -> dict[str, Any]:
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT STATUS, OPERATION_KIND, LEASE_EXPIRES_AT, LAST_ERROR_CODE, "
                "SCHEMA_EPOCH, UPDATED_AT FROM NL2SQL_SCHEMA_OPERATIONS "
                "WHERE OPERATION_KEY = :operation_key",
                {"operation_key": CONTROL_KEY},
            )
            row = cursor.fetchone()
        if row is None:
            return {
                "status": "idle",
                "operation_kind": None,
                "lease_expires_at": None,
                "last_error_code": None,
                "schema_epoch": 0,
                "updated_at": None,
            }
        return {
            "status": str(row[0]).lower(),
            "operation_kind": str(row[1]).lower() if row[1] else None,
            "lease_expires_at": _iso(row[2]),
            "last_error_code": str(row[3]) if row[3] else None,
            "schema_epoch": int(row[4] or 0),
            "updated_at": _iso(row[5]),
        }

    def _ensure_control_schema(self) -> None:
        with self._connection_factory() as connection:
            objects = self._load_objects(connection)
            if (CONTROL_TABLE, "TABLE") in objects and (MIGRATION_TABLE, "TABLE") in objects:
                return
            self._configure_ddl_lock_timeout(connection)
            self._apply_migration(connection, MIGRATIONS[0])

    def _claim_lease(self, owner: str, operation_kind: str) -> None:
        with self._connection_factory() as connection, connection.cursor() as cursor:
            cursor.execute(
                """
                UPDATE NL2SQL_SCHEMA_OPERATIONS
                   SET STATUS = 'RUNNING',
                       OPERATION_KIND = :operation_kind,
                       LEASE_OWNER = :lease_owner,
                       LEASE_EXPIRES_AT = SYSTIMESTAMP
                           + NUMTODSINTERVAL(:lease_seconds, 'SECOND'),
                       LAST_ERROR_CODE = NULL,
                       UPDATED_AT = SYSTIMESTAMP
                 WHERE OPERATION_KEY = :operation_key
                   AND (
                       STATUS <> 'RUNNING'
                       OR LEASE_EXPIRES_AT IS NULL
                       OR LEASE_EXPIRES_AT < SYSTIMESTAMP
                   )
                """,
                {
                    "operation_kind": operation_kind,
                    "lease_owner": owner,
                    "lease_seconds": self._lease_seconds,
                    "operation_key": CONTROL_KEY,
                },
            )
            claimed = int(cursor.rowcount or 0) == 1
            connection.commit()
        if not claimed:
            raise SystemSchemaBusyError()

    def _heartbeat(self, connection: Any, owner: str) -> None:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                UPDATE NL2SQL_SCHEMA_OPERATIONS
                   SET LEASE_EXPIRES_AT = SYSTIMESTAMP
                           + NUMTODSINTERVAL(:lease_seconds, 'SECOND'),
                       UPDATED_AT = SYSTIMESTAMP
                 WHERE OPERATION_KEY = :operation_key
                   AND STATUS = 'RUNNING'
                   AND LEASE_OWNER = :lease_owner
                """,
                {
                    "lease_seconds": self._lease_seconds,
                    "operation_key": CONTROL_KEY,
                    "lease_owner": owner,
                },
            )
            renewed = int(cursor.rowcount or 0) == 1
            connection.commit()
        if not renewed:
            raise SystemSchemaBusyError()

    def _finish_operation(
        self,
        connection: Any,
        owner: str,
        *,
        increment_epoch: bool,
    ) -> None:
        epoch_sql = "SCHEMA_EPOCH + 1" if increment_epoch else "SCHEMA_EPOCH"
        with connection.cursor() as cursor:
            cursor.execute(
                "UPDATE NL2SQL_SCHEMA_OPERATIONS "
                "SET STATUS = 'IDLE', OPERATION_KIND = NULL, LEASE_OWNER = NULL, "
                "LEASE_EXPIRES_AT = NULL, LAST_ERROR_CODE = NULL, "
                f"SCHEMA_EPOCH = {epoch_sql}, UPDATED_AT = SYSTIMESTAMP "  # nosec B608
                "WHERE OPERATION_KEY = :operation_key AND LEASE_OWNER = :lease_owner",
                {"operation_key": CONTROL_KEY, "lease_owner": owner},
            )
            finished = int(cursor.rowcount or 0) == 1
            connection.commit()
        if not finished:
            raise SystemSchemaBusyError()

    def _record_failure(self, owner: str, error_code: str) -> None:
        with (
            suppress(Exception),
            self._connection_factory() as connection,
            connection.cursor() as cursor,
        ):
            cursor.execute(
                """
                UPDATE NL2SQL_SCHEMA_OPERATIONS
                   SET STATUS = 'FAILED', OPERATION_KIND = NULL, LEASE_OWNER = NULL,
                       LEASE_EXPIRES_AT = NULL, LAST_ERROR_CODE = :error_code,
                       UPDATED_AT = SYSTIMESTAMP
                 WHERE OPERATION_KEY = :operation_key AND LEASE_OWNER = :lease_owner
                """,
                {
                    "error_code": error_code[:64],
                    "operation_key": CONTROL_KEY,
                    "lease_owner": owner,
                },
            )
            connection.commit()

    def _configure_ddl_lock_timeout(self, connection: Any) -> None:
        """system schema 専用 session に bounded DDL wait を設定する。"""

        with connection.cursor() as cursor:
            cursor.execute(
                "ALTER SESSION SET DDL_LOCK_TIMEOUT = "
                f"{self._ddl_lock_timeout_seconds}"  # nosec B608 - bounded integer
            )

    def _apply_migration(self, connection: Any, migration: MigrationArtifact) -> None:
        statements = split_migration_sql(migration.path.read_text(encoding="utf-8"))
        with connection.cursor() as cursor:
            for statement in statements:
                foreign_key = self._managed_foreign_key_for_statement(statement)
                if foreign_key is not None:
                    state = self._foreign_key_state(connection, foreign_key)
                    if state == "matching":
                        continue
                    if state == "mismatch":
                        raise self._foreign_key_mismatch_error(foreign_key)
                try:
                    cursor.execute(statement)
                except Exception as exc:
                    code = oracle_error_code(exc)
                    if foreign_key is not None and code in {"ORA-02264", "ORA-02275"}:
                        state = self._foreign_key_state(connection, foreign_key)
                        if state == "matching":
                            continue
                        if state == "mismatch":
                            raise self._foreign_key_mismatch_error(foreign_key) from exc
                    if code in _IGNORED_APPLY_CODES:
                        continue
                    raise
            cursor.execute(
                """
                MERGE INTO NL2SQL_SCHEMA_MIGRATIONS target
                USING (
                    SELECT :version_no AS VERSION_NO,
                           :description AS DESCRIPTION,
                           :checksum AS CHECKSUM
                    FROM DUAL
                ) source
                ON (target.VERSION_NO = source.VERSION_NO)
                WHEN MATCHED THEN UPDATE SET
                    target.DESCRIPTION = source.DESCRIPTION,
                    target.CHECKSUM = source.CHECKSUM,
                    target.APPLIED_AT = SYSTIMESTAMP
                WHEN NOT MATCHED THEN INSERT
                    (VERSION_NO, DESCRIPTION, CHECKSUM, APPLIED_AT)
                VALUES
                    (source.VERSION_NO, source.DESCRIPTION, source.CHECKSUM, SYSTIMESTAMP)
                """,
                {
                    "version_no": migration.version,
                    "description": migration.description,
                    "checksum": migration.checksum,
                },
            )
            connection.commit()

    @staticmethod
    def _managed_foreign_key_for_statement(
        statement: str,
    ) -> ManagedForeignKey | None:
        match = _ADD_CONSTRAINT_PATTERN.search(statement)
        if match is None:
            return None
        table_name, constraint_name = (value.upper() for value in match.groups())
        foreign_key = _MANAGED_FOREIGN_KEYS_BY_NAME.get(constraint_name)
        if foreign_key is None:
            return None
        if foreign_key.table_name != table_name:
            raise SystemSchemaError(
                "SCHEMA_CONSTRAINT_MISMATCH",
                f"外部キー {constraint_name} の対象テーブル定義が一致しません。",
                status_code=409,
            )
        return foreign_key

    def _foreign_key_state(
        self,
        connection: Any,
        expected: ManagedForeignKey,
    ) -> Literal["missing", "matching", "mismatch"]:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT child.TABLE_NAME,
                       child.CONSTRAINT_TYPE,
                       child.DELETE_RULE,
                       parent.TABLE_NAME,
                       child.STATUS,
                       child.VALIDATED,
                       child.DEFERRABLE,
                       child.DEFERRED
                  FROM USER_CONSTRAINTS child
                  LEFT JOIN USER_CONSTRAINTS parent
                    ON parent.OWNER = child.R_OWNER
                   AND parent.CONSTRAINT_NAME = child.R_CONSTRAINT_NAME
                 WHERE child.CONSTRAINT_NAME = :constraint_name
                """,
                {"constraint_name": expected.name},
            )
            row = cursor.fetchone()
        if row is None:
            return "missing"
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT COLUMN_NAME
                  FROM USER_CONS_COLUMNS
                 WHERE CONSTRAINT_NAME = :constraint_name
                 ORDER BY POSITION
                """,
                {"constraint_name": expected.name},
            )
            columns = tuple(str(item[0]).upper() for item in cursor.fetchall())
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT referenced_column.COLUMN_NAME
                  FROM USER_CONSTRAINTS child
                  JOIN USER_CONS_COLUMNS referenced_column
                    ON referenced_column.OWNER = child.R_OWNER
                   AND referenced_column.CONSTRAINT_NAME = child.R_CONSTRAINT_NAME
                 WHERE child.CONSTRAINT_NAME = :constraint_name
                 ORDER BY referenced_column.POSITION
                """,
                {"constraint_name": expected.name},
            )
            referenced_columns = tuple(
                str(item[0]).upper() for item in cursor.fetchall()
            )
        actual = (
            str(row[0]).upper(),
            str(row[1]).upper(),
            str(row[2]).upper(),
            str(row[3]).upper() if row[3] else "",
            str(row[4]).upper(),
            str(row[5]).upper(),
            str(row[6]).upper(),
            str(row[7]).upper(),
            columns,
            referenced_columns,
        )
        wanted = (
            expected.table_name,
            "R",
            expected.delete_rule,
            expected.referenced_table_name,
            "ENABLED",
            "VALIDATED",
            "NOT DEFERRABLE",
            "IMMEDIATE",
            expected.columns,
            expected.referenced_columns,
        )
        return "matching" if actual == wanted else "mismatch"

    @staticmethod
    def _foreign_key_mismatch_error(
        foreign_key: ManagedForeignKey,
    ) -> SystemSchemaError:
        return SystemSchemaError(
            "SCHEMA_CONSTRAINT_MISMATCH",
            f"外部キー {foreign_key.name} は存在しますが、期待する定義と一致しません。"
            "Oracle の制約定義を確認してから再試行してください。",
            status_code=409,
        )

    def _assert_no_active_jobs(self, connection: Any) -> None:
        objects = self._load_objects(connection)
        for table_name in (
            "NL2SQL_SCHEMA_REFRESH_JOBS",
            "NL2SQL_ONTOLOGY_JOBS",
            "NL2SQL_EVALUATION_JOBS",
        ):
            if (table_name, "TABLE") not in objects:
                continue
            placeholders, binds = _bind_list("job_state_", _ACTIVE_JOB_STATES)
            with connection.cursor() as cursor:
                cursor.execute(
                    f"SELECT COUNT(*) FROM {table_name} "  # nosec B608 - fixed manifest value
                    f"WHERE UPPER(STATUS) IN ({placeholders})",  # nosec B608
                    binds,
                )
                row = cursor.fetchone()
            if row and int(row[0] or 0) > 0:
                raise SystemSchemaActiveJobsError()

    def _drop_managed_objects(self, connection: Any, owner: str) -> int:
        objects = self._load_objects(connection)
        dropped = 0
        for index_name in reversed(MANAGED_INDEXES):
            if (index_name, "INDEX") not in objects:
                continue
            dropped += self._execute_drop(connection, f"DROP INDEX {index_name}")
            self._heartbeat(connection, owner)
        for table_name in reversed(MANAGED_TABLES):
            if table_name == CONTROL_TABLE or (table_name, "TABLE") not in objects:
                continue
            dropped += self._execute_drop(
                connection,
                f"DROP TABLE {table_name} CASCADE CONSTRAINTS PURGE",
            )
            self._heartbeat(connection, owner)
        for sequence_name in reversed(MANAGED_SEQUENCES):
            if (sequence_name, "SEQUENCE") not in objects:
                continue
            dropped += self._execute_drop(connection, f"DROP SEQUENCE {sequence_name}")
            self._heartbeat(connection, owner)
        return dropped

    @staticmethod
    def _execute_drop(connection: Any, statement: str) -> int:
        with connection.cursor() as cursor:
            try:
                cursor.execute(statement)
            except Exception as exc:
                if oracle_error_code(exc) in _IGNORED_DROP_CODES:
                    return 0
                raise
            connection.commit()
        return 1


system_schema_manager = SystemSchemaManager()


__all__ = [
    "CONTROL_TABLE",
    "DOMAIN_TABLES",
    "MANAGED_FOREIGN_KEYS",
    "MANAGED_INDEXES",
    "MANAGED_OBJECTS",
    "MANAGED_SEQUENCES",
    "MANAGED_TABLES",
    "MIGRATIONS",
    "ManagedForeignKey",
    "PRESERVED_TABLES",
    "RECREATE_CONFIRMATION",
    "SystemSchemaActiveJobsError",
    "SystemSchemaBusyError",
    "SystemSchemaError",
    "SystemSchemaManager",
    "classify_system_schema_status",
    "split_migration_sql",
    "system_schema_manager",
]
