"""RAG の versioned Oracle system schema を明示操作する manager。

アプリ起動時には DDL を実行しない。管理 API / CLI から明示された場合だけ、
``oracle_schema`` の正本を使って不足オブジェクトの作成・migration・全再作成を行う。
"""

from __future__ import annotations

import hashlib
import logging
import re
import uuid
from collections.abc import Callable, Iterator, Sequence
from contextlib import AbstractContextManager, contextmanager, suppress
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Literal

from app.clients.oracle import (
    ORACLE_TEXT_LEXER_PREFERENCE,
    ORACLE_TEXT_STOPLIST,
    OracleClient,
)
from app.rag.oracle_schema import (
    SCHEMA_VERSION,
    OracleSchemaSection,
    oracle_schema_migration_sections,
    oracle_schema_sections,
    split_sql_statements,
)

logger = logging.getLogger(__name__)

SystemSchemaStatus = Literal["missing", "partial", "outdated", "ready"]
SystemSchemaOperation = Literal["no_op", "initialized", "migrated", "recreated"]

RECREATE_CONFIRMATION = "RECREATE_RAG_SYSTEM_TABLES"
CONTROL_TABLE = "RAG_SCHEMA_OPERATIONS"
MIGRATION_TABLE = "RAG_SCHEMA_MIGRATIONS"
CONTROL_KEY = "system_schema"
DEFAULT_DDL_LOCK_TIMEOUT_SECONDS = 5
_ACTIVE_JOB_STATES = ("QUEUED", "RUNNING")
_IGNORED_APPLY_CODES = frozenset(
    {
        "ORA-00001",  # migration ledger / seed の競合
        "ORA-00955",  # object already exists
        "ORA-01430",  # column already exists
        "ORA-01442",  # column is already NOT NULL
        "ORA-01451",  # column is already NULL
    }
)
_IGNORED_DROP_CODES = frozenset({"ORA-00942", "ORA-01418"})
_CREATE_TABLE_PATTERN = re.compile(
    r"^\s*CREATE\s+TABLE\s+([A-Z][A-Z0-9_$#]*)\b",
    flags=re.IGNORECASE,
)
_CREATE_INDEX_PATTERN = re.compile(
    r"^\s*CREATE\s+(?:UNIQUE\s+|VECTOR\s+)?INDEX\s+([A-Z][A-Z0-9_$#]*)\b",
    flags=re.IGNORECASE,
)


@dataclass(frozen=True, slots=True)
class MigrationArtifact:
    """Python DDL 正本から得た 1 migration。"""

    name: str
    table_name: str
    sql: str

    @property
    def checksum(self) -> str:
        return hashlib.sha256(self.sql.encode("utf-8")).hexdigest()


MIGRATIONS: tuple[MigrationArtifact, ...] = tuple(
    MigrationArtifact(section.name, section.table_name.upper(), section.sql)
    for section in oracle_schema_migration_sections()
)

# 作成依存順。再作成時はこの逆順で DROP する。
# prefix scan は使用せず、この manifest へ追加された RAG object だけを管理する。
MANAGED_TABLES: tuple[str, ...] = (
    CONTROL_TABLE,
    MIGRATION_TABLE,
    "RAG_DOCUMENTS",
    "RAG_DOCUMENT_RECIPES",
    "RAG_KNOWLEDGE_BASES",
    "RAG_DOCUMENT_KNOWLEDGE_BASES",
    "RAG_BUSINESS_VIEWS",
    "RAG_PROMPT_VERSIONS",
    "RAG_GENERATION_SETTINGS",
    "RAG_CONVERSATIONS",
    "RAG_MESSAGES",
    "RAG_INGESTION_JOBS",
    "RAG_INGESTION_SEGMENTS",
    "RAG_CHUNKS",
    "RAG_CHUNK_SETS",
    "RAG_DOCUMENT_EXTRACTIONS",
    "RAG_ARTIFACT_LAYERS",
    "RAG_SEARCH_AUDIT",
    "RAG_INGESTION_AUDIT",
    "RAG_GRAPH_ENTITIES",
    "RAG_GRAPH_RELATIONSHIPS",
    "RAG_GRAPH_CLAIMS",
    "RAG_GRAPH_COMMUNITY_SUMMARIES",
    "RAG_GRAPH_ENTITY_CHUNKS",
    "RAG_AGENT_MEMORIES",
    "RAG_CITATION_FEEDBACK",
    "RAG_FEEDBACK_DETAILS",
    "RAG_EVALUATION_RUNS",
)

MANAGED_INDEXES: tuple[str, ...] = (
    "RAG_DOCUMENTS_CONTENT_SHA256_IDX",
    "RAG_DOCUMENTS_STATUS_UPLOADED_IDX",
    "RAG_DOCUMENTS_TENANT_STATUS_UPLOADED_IDX",
    "RAG_DOCUMENT_RECIPES_STATUS_IDX",
    "RAG_KNOWLEDGE_BASES_TENANT_NAME_UIDX",
    "RAG_KNOWLEDGE_BASES_TENANT_STATUS_IDX",
    "RAG_DOCUMENT_KNOWLEDGE_BASES_DOCUMENT_IDX",
    "RAG_DOCUMENT_KNOWLEDGE_BASES_TENANT_KB_IDX",
    "RAG_BUSINESS_VIEWS_TENANT_NAME_UIDX",
    "RAG_BUSINESS_VIEWS_TENANT_STATUS_IDX",
    "RAG_PROMPT_VERSIONS_CREATED_IDX",
    "RAG_CONVERSATIONS_BUSINESS_VIEW_IDX",
    "RAG_CONVERSATIONS_TENANT_VIEW_UPDATED_IDX",
    "RAG_MESSAGES_CONVERSATION_CREATED_IDX",
    "RAG_MESSAGES_REPLY_TO_IDX",
    "RAG_MESSAGES_TENANT_CREATED_IDX",
    "RAG_MESSAGES_TRACE_IDX",
    "RAG_INGESTION_JOBS_DOCUMENT_IDX",
    "RAG_INGESTION_JOBS_RECIPE_IDX",
    "RAG_INGESTION_JOBS_TENANT_QUEUED_IDX",
    "RAG_INGESTION_SEGMENTS_DOCUMENT_STATUS_IDX",
    "RAG_INGESTION_SEGMENTS_RECIPE_STATUS_IDX",
    "RAG_INGESTION_SEGMENTS_TENANT_STATUS_IDX",
    "RAG_CHUNKS_EMBEDDING_HNSW_IDX",
    "RAG_CHUNKS_TEXT_IDX",
    "RAG_CHUNKS_TENANT_DOCUMENT_IDX",
    "RAG_CHUNKS_CHUNK_SET_IDX",
    "RAG_CHUNK_SETS_DOCUMENT_IDX",
    "RAG_CHUNK_SETS_SERVING_IDX",
    "RAG_CHUNK_SETS_RECIPE_IDX",
    "RAG_CHUNK_SETS_RECIPE_ACTIVE_UIDX",
    "RAG_CHUNK_SETS_EXTRACTION_IDX",
    "RAG_DOC_EXT_STATUS_IDX",
    "RAG_ARTIFACT_LAYERS_PARENT_IDX",
    "RAG_SEARCH_AUDIT_CONFIG_IDX",
    "RAG_SEARCH_AUDIT_CREATED_OUTCOME_IDX",
    "RAG_SEARCH_AUDIT_QUERY_HASH_IDX",
    "RAG_SEARCH_AUDIT_TENANT_CREATED_IDX",
    "RAG_SEARCH_AUDIT_TRACE_IDX",
    "RAG_INGESTION_AUDIT_DOCUMENT_CREATED_IDX",
    "RAG_INGESTION_AUDIT_PARSER_CREATED_IDX",
    "RAG_INGESTION_AUDIT_SOURCE_SHA256_IDX",
    "RAG_INGESTION_AUDIT_TENANT_CREATED_IDX",
    "RAG_INGESTION_AUDIT_TRACE_IDX",
    "RAG_GRAPH_ENTITIES_CHUNK_SET_IDX",
    "RAG_GRAPH_ENTITIES_TENANT_NAME_IDX",
    "RAG_GRAPH_REL_SOURCE_IDX",
    "RAG_GRAPH_REL_TARGET_IDX",
    "RAG_GRAPH_CLAIM_ENTITY_IDX",
    "RAG_GRAPH_COMMUNITY_TENANT_IDX",
    "RAG_GRAPH_COMMUNITY_CHUNK_SET_IDX",
    "RAG_GRAPH_ENTITY_CHUNKS_CHUNK_IDX",
    "RAG_GRAPH_ENTITY_CHUNKS_CHUNK_SET_IDX",
    "RAG_AGENT_MEMORIES_EMBEDDING_HNSW_IDX",
    "RAG_AGENT_MEMORIES_TEXT_IDX",
    "RAG_AGENT_MEMORIES_SCOPE_IDX",
    "RAG_AGENT_MEMORIES_TRACE_IDX",
    "RAG_CITATION_FEEDBACK_TRACE_IDX",
    "RAG_CITATION_FEEDBACK_TENANT_CREATED_IDX",
    "RAG_FEEDBACK_BUSINESS_CREATED_IDX",
    "RAG_FEEDBACK_USER_TRACE_IDX",
    "RAG_FEEDBACK_DETAILS_TENANT_CREATED_IDX",
    "RAG_FEEDBACK_DETAILS_MESSAGE_IDX",
    "RAG_FEEDBACK_DETAILS_TEXT_IDX",
    "RAG_EVALUATION_RUNS_BEST_EXPERIMENT_IDX",
    "RAG_EVALUATION_RUNS_RESULT_HASH_IDX",
    "RAG_EVALUATION_RUNS_TENANT_CREATED_IDX",
)

MANAGED_TEXT_OBJECTS: tuple[tuple[str, str], ...] = (
    (ORACLE_TEXT_LEXER_PREFERENCE, "TEXT_PREFERENCE"),
    (ORACLE_TEXT_STOPLIST, "TEXT_STOPLIST"),
)

MANAGED_OBJECTS: tuple[tuple[str, str], ...] = (
    *((name, "TABLE") for name in MANAGED_TABLES),
    *((name, "INDEX") for name in MANAGED_INDEXES),
    *MANAGED_TEXT_OBJECTS,
)

# 旧 migration が一時的に作成したが、現行 runtime では使用しない object。
RETIRED_MANAGED_OBJECTS: tuple[tuple[str, str], ...] = (
    ("RAG_KB_CHUNK_SET_BINDINGS", "TABLE"),
    ("RAG_KB_CS_BIND_CS_IDX", "INDEX"),
    ("RAG_DOCUMENT_EXTRACTIONS_DOCUMENT_IDX", "INDEX"),
    # 2026-06 の schema が同じ列式を旧名で作成していた。Oracle は同一列リストの
    # 別名 index 作成を ORA-01408 で拒否するため、現行名の作成前にだけ明示削除する。
    ("RAG_INGESTION_SEGMENTS_RECIPE_IDX", "INDEX"),
)

DOMAIN_TABLES = frozenset(MANAGED_TABLES) - {CONTROL_TABLE, MIGRATION_TABLE}


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
            "待機中または実行中の取込ジョブがあります。完了またはキャンセルしてから再実行してください。",
            status_code=409,
        )


def oracle_error_code(exc: Exception) -> str:
    match = re.search(r"ORA-\d{5}", str(exc), flags=re.IGNORECASE)
    return match.group(0).upper() if match else "SCHEMA_OPERATION_FAILED"


def managed_manifest_from_schema(
    sections: Sequence[OracleSchemaSection] | None = None,
) -> set[tuple[str, str]]:
    """DDL 正本の CREATE TABLE / INDEX と Text object を抽出する。"""

    objects: set[tuple[str, str]] = set(MANAGED_TEXT_OBJECTS)
    for section in sections or oracle_schema_sections():
        for statement in split_sql_statements(section.sql):
            table_match = _CREATE_TABLE_PATTERN.match(statement)
            if table_match is not None:
                objects.add((table_match.group(1).upper(), "TABLE"))
            index_match = _CREATE_INDEX_PATTERN.match(statement)
            if index_match is not None:
                objects.add((index_match.group(1).upper(), "INDEX"))
    return objects


def classify_system_schema_status(
    objects: set[tuple[str, str]],
    applied_checksums: dict[str, str],
) -> SystemSchemaStatus:
    """Dictionary / ledger snapshot を四つの公開状態へ分類する。"""

    if not any((name, "TABLE") in objects for name in DOMAIN_TABLES):
        return "missing"
    if set(MANAGED_OBJECTS) - objects:
        return "partial"
    if set(RETIRED_MANAGED_OBJECTS).intersection(objects):
        return "outdated"
    if any(applied_checksums.get(migration.name) != migration.checksum for migration in MIGRATIONS):
        return "outdated"
    return "ready"


def _bind_list(prefix: str, values: Sequence[str]) -> tuple[str, dict[str, str]]:
    binds = {f"{prefix}{index}": value for index, value in enumerate(values)}
    return ", ".join(f":{name}" for name in binds), binds


def _iso(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        current = value if value.tzinfo else value.replace(tzinfo=UTC)
        return current.isoformat()
    return str(value)


class SystemSchemaManager:
    """Status / initialize / recreate を同じ manifest と lease で提供する。"""

    def __init__(
        self,
        connection_factory: Callable[[], AbstractContextManager[Any]] | None = None,
        *,
        lease_seconds: int = 900,
        ddl_lock_timeout_seconds: int = DEFAULT_DDL_LOCK_TIMEOUT_SECONDS,
    ) -> None:
        self._connection_factory = connection_factory or self._default_connection
        self._lease_seconds = max(30, int(lease_seconds))
        self._ddl_lock_timeout_seconds = max(0, min(120, int(ddl_lock_timeout_seconds)))

    @staticmethod
    @contextmanager
    def _default_connection() -> Iterator[Any]:
        connection = OracleClient().connection_pool().acquire()
        try:
            yield connection
        finally:
            connection.close()

    def status(self) -> dict[str, Any]:
        """USER_* dictionary と migration ledger だけを読む。DDL は実行しない。"""

        with self._connection_factory() as connection:
            return self._status_on(connection)

    def is_ready(self) -> bool:
        """worker / readiness gate 向けの副作用なし判定。"""

        data = self.status()
        return bool(data["status"] == "ready" and data["operation_state"]["status"] != "running")

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
        operation_kind = "RECREATE" if recreate else "INITIALIZE"
        self._ensure_control_schema()
        self._claim_lease(owner, operation_kind)
        before: dict[str, Any] | None = None
        dropped_count = 0
        applied_names: list[str] = []
        try:
            with self._connection_factory() as connection:
                self._configure_ddl_lock_timeout(connection)
                before = self._status_on(connection)
                if before["status"] == "ready" and not recreate:
                    self._finish_operation(connection, owner, increment_epoch=False)
                    return {
                        **self._status_on(connection),
                        "operation": "no_op",
                        "dropped_object_count": 0,
                        "created_object_count": 0,
                    }

                if recreate:
                    self._assert_no_active_jobs(connection)
                    dropped_count += self._drop_managed_objects(connection, owner)

                self._heartbeat(connection, owner)
                self._apply_base_non_index_statements(connection)
                fresh_database = before["status"] == "missing" or recreate
                if fresh_database:
                    for migration in MIGRATIONS:
                        self._record_migration(connection, migration)
                        applied_names.append(migration.name)
                else:
                    pending = set(before["pending_versions"])
                    for migration in MIGRATIONS:
                        if migration.name not in pending:
                            continue
                        self._heartbeat(connection, owner)
                        self._apply_migration(connection, migration)
                        applied_names.append(migration.name)

                dropped_count += self._drop_retired_objects(connection)
                self._apply_missing_indexes(connection)
                self._heartbeat(connection, owner)
                interim = self._status_on(connection)
                if interim["status"] != "ready":
                    raise SystemSchemaError(
                        "SCHEMA_POSTCONDITION_FAILED",
                        "システムテーブル操作後も必須オブジェクトが不足しています。状態を再取得して再試行してください。",
                    )
                self._finish_operation(connection, owner, increment_epoch=True)
                after = self._status_on(connection)

                if recreate:
                    operation: SystemSchemaOperation = "recreated"
                elif before["status"] == "missing":
                    operation = "initialized"
                else:
                    operation = "migrated"
                logger.info(
                    "rag_system_schema_operation_succeeded",
                    extra={
                        "operation": operation,
                        "schema_epoch": after["operation_state"]["schema_epoch"],
                        "applied_migrations": applied_names,
                        "dropped_object_count": dropped_count,
                    },
                )
                return {
                    **after,
                    "operation": operation,
                    "dropped_object_count": dropped_count,
                    "created_object_count": (
                        max(0, int(after["existing_object_count"]) - 1)
                        if recreate
                        else max(
                            0,
                            int(after["existing_object_count"])
                            - int(before["existing_object_count"]),
                        )
                    ),
                }
        except SystemSchemaError as exc:
            self._record_failure(owner, exc.code)
            raise
        except Exception as exc:
            code = oracle_error_code(exc)
            self._record_failure(owner, code)
            logger.error(
                "rag_system_schema_operation_failed",
                extra={
                    "operation": operation_kind.lower(),
                    "error_code": code,
                    "exception_type": type(exc).__name__,
                },
            )
            if code == "ORA-00054":
                raise SystemSchemaError(
                    code,
                    "Oracle の対象オブジェクトのロックが解放されませんでした "
                    f"({self._ddl_lock_timeout_seconds} 秒、ORA-00054)。"
                    "取込処理を停止し、状態を再取得して再試行してください。",
                    status_code=409,
                ) from exc
            raise SystemSchemaError(
                code,
                f"システムテーブル操作に失敗しました ({code})。"
                "状態を再取得して再試行してください。",
            ) from exc

    def _status_on(self, connection: Any) -> dict[str, Any]:
        objects = self._load_objects(connection)
        expected = set(MANAGED_OBJECTS)
        existing = expected.intersection(objects)
        applied = self._load_migrations(connection, objects)
        matching = [
            migration.name
            for migration in MIGRATIONS
            if applied.get(migration.name) == migration.checksum
        ]
        pending = [
            migration.name
            for migration in MIGRATIONS
            if applied.get(migration.name) != migration.checksum
        ]
        return {
            "status": classify_system_schema_status(set(objects), applied),
            "schema_version": SCHEMA_VERSION,
            "schema_head": MIGRATIONS[-1].name if MIGRATIONS else SCHEMA_VERSION,
            "applied_versions": matching,
            "pending_versions": pending,
            "expected_object_count": len(expected),
            "existing_object_count": len(existing),
            "expected_table_count": len(MANAGED_TABLES),
            "existing_table_count": sum((name, "TABLE") in objects for name in MANAGED_TABLES),
            "missing_objects": [
                {"name": name, "object_type": object_type}
                for name, object_type in sorted(
                    expected - existing,
                    key=lambda item: (item[1], item[0]),
                )
            ],
            "retired_objects": [
                {"name": name, "object_type": object_type}
                for name, object_type in RETIRED_MANAGED_OBJECTS
                if (name, object_type) in objects
            ],
            "tables": self._load_table_metadata(connection, objects),
            "operation_state": self._operation_payload(connection, objects),
        }

    def _load_objects(self, connection: Any) -> dict[tuple[str, str], Any]:
        inspected = tuple(dict.fromkeys((*MANAGED_OBJECTS, *RETIRED_MANAGED_OBJECTS)))
        names = tuple(dict.fromkeys(name for name, _object_type in inspected))
        placeholders, binds = _bind_list("object_name_", names)
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT OBJECT_NAME, OBJECT_TYPE, CREATED FROM USER_OBJECTS "
                f"WHERE OBJECT_NAME IN ({placeholders}) "  # nosec B608 - fixed binds
                "AND OBJECT_TYPE IN ('TABLE', 'INDEX')",
                binds,
            )
            objects = {
                (str(row[0]).upper(), str(row[1]).upper()): row[2] for row in cursor.fetchall()
            }
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT PRE_NAME FROM CTX_USER_PREFERENCES "
                "WHERE PRE_NAME = :preference_name AND PRE_CLASS = 'LEXER'",
                {"preference_name": ORACLE_TEXT_LEXER_PREFERENCE},
            )
            if cursor.fetchone() is not None:
                objects[(ORACLE_TEXT_LEXER_PREFERENCE, "TEXT_PREFERENCE")] = None
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT SPL_NAME FROM CTX_USER_STOPLISTS WHERE SPL_NAME = :stoplist_name",
                {"stoplist_name": ORACLE_TEXT_STOPLIST},
            )
            if cursor.fetchone() is not None:
                objects[(ORACLE_TEXT_STOPLIST, "TEXT_STOPLIST")] = None
        return objects

    def _load_migrations(
        self,
        connection: Any,
        objects: dict[tuple[str, str], Any],
    ) -> dict[str, str]:
        if (MIGRATION_TABLE, "TABLE") not in objects:
            return {}
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT MIGRATION_NAME, CHECKSUM FROM RAG_SCHEMA_MIGRATIONS "
                "ORDER BY MIGRATION_NAME"
            )
            return {str(row[0]): str(row[1]) for row in cursor.fetchall()}

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
                    f"WHERE TABLE_NAME IN ({placeholders})",  # nosec B608 - fixed binds
                    binds,
                )
                metadata = {str(row[0]).upper(): (row[1], row[2]) for row in cursor.fetchall()}
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
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT STATUS, OPERATION_KIND, LEASE_EXPIRES_AT, LAST_ERROR_CODE, "
                "SCHEMA_EPOCH, UPDATED_AT FROM RAG_SCHEMA_OPERATIONS "
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
        control_section = oracle_schema_sections()[0]
        with self._connection_factory() as connection:
            self._configure_ddl_lock_timeout(connection)
            self._execute_statements(
                connection,
                split_sql_statements(control_section.sql),
                ignored_codes=_IGNORED_APPLY_CODES,
            )

    def _claim_lease(self, owner: str, operation_kind: str) -> None:
        with self._connection_factory() as connection, connection.cursor() as cursor:
            cursor.execute(
                """
                UPDATE RAG_SCHEMA_OPERATIONS
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
                UPDATE RAG_SCHEMA_OPERATIONS
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
                "UPDATE RAG_SCHEMA_OPERATIONS "
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
                UPDATE RAG_SCHEMA_OPERATIONS
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
        with connection.cursor() as cursor:
            cursor.execute(
                "ALTER SESSION SET DDL_LOCK_TIMEOUT = "
                f"{self._ddl_lock_timeout_seconds}"  # nosec B608 - bounded integer
            )

    def _apply_base_non_index_statements(self, connection: Any) -> None:
        statements = [
            statement
            for section in oracle_schema_sections()
            for statement in split_sql_statements(section.sql)
            if _CREATE_INDEX_PATTERN.match(statement) is None
        ]
        self._execute_statements(
            connection,
            statements,
            ignored_codes=_IGNORED_APPLY_CODES,
        )

    def _apply_missing_indexes(self, connection: Any) -> None:
        objects = self._load_objects(connection)
        missing = {name for name in MANAGED_INDEXES if (name, "INDEX") not in objects}
        statements = [
            statement
            for section in oracle_schema_sections()
            for statement in split_sql_statements(section.sql)
            if (match := _CREATE_INDEX_PATTERN.match(statement)) is not None
            and match.group(1).upper() in missing
        ]
        self._execute_statements(connection, statements, ignored_codes=_IGNORED_APPLY_CODES)

    def _apply_migration(self, connection: Any, migration: MigrationArtifact) -> None:
        self._execute_statements(
            connection,
            split_sql_statements(migration.sql),
            ignored_codes=_IGNORED_APPLY_CODES,
        )
        self._record_migration(connection, migration)

    @staticmethod
    def _record_migration(connection: Any, migration: MigrationArtifact) -> None:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                MERGE INTO RAG_SCHEMA_MIGRATIONS target
                USING (
                    SELECT :migration_name AS MIGRATION_NAME,
                           :description AS DESCRIPTION,
                           :checksum AS CHECKSUM
                    FROM DUAL
                ) source
                ON (target.MIGRATION_NAME = source.MIGRATION_NAME)
                WHEN MATCHED THEN UPDATE SET
                    target.DESCRIPTION = source.DESCRIPTION,
                    target.CHECKSUM = source.CHECKSUM,
                    target.APPLIED_AT = SYSTIMESTAMP
                WHEN NOT MATCHED THEN INSERT
                    (MIGRATION_NAME, DESCRIPTION, CHECKSUM, APPLIED_AT)
                VALUES
                    (source.MIGRATION_NAME, source.DESCRIPTION, source.CHECKSUM, SYSTIMESTAMP)
                """,
                {
                    "migration_name": migration.name,
                    "description": migration.table_name.lower(),
                    "checksum": migration.checksum,
                },
            )
            connection.commit()

    @staticmethod
    def _execute_statements(
        connection: Any,
        statements: Sequence[str],
        *,
        ignored_codes: frozenset[str],
    ) -> None:
        with connection.cursor() as cursor:
            for statement in statements:
                try:
                    cursor.execute(statement)
                except Exception as exc:
                    if oracle_error_code(exc) in ignored_codes:
                        continue
                    raise
            connection.commit()

    def _assert_no_active_jobs(self, connection: Any) -> None:
        objects = self._load_objects(connection)
        if ("RAG_INGESTION_JOBS", "TABLE") not in objects:
            return
        placeholders, binds = _bind_list("job_status_", _ACTIVE_JOB_STATES)
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT COUNT(*) FROM RAG_INGESTION_JOBS "
                f"WHERE UPPER(STATUS) IN ({placeholders})",  # nosec B608 - fixed states
                binds,
            )
            row = cursor.fetchone()
        if row and int(row[0] or 0) > 0:
            raise SystemSchemaActiveJobsError()

    def _drop_managed_objects(self, connection: Any, owner: str) -> int:
        objects = self._load_objects(connection)
        dropped = 0
        retired_indexes = tuple(
            name for name, object_type in RETIRED_MANAGED_OBJECTS if object_type == "INDEX"
        )
        for index_name in reversed((*MANAGED_INDEXES, *retired_indexes)):
            if (index_name, "INDEX") not in objects:
                continue
            dropped += self._execute_drop(connection, f"DROP INDEX {index_name}")
            self._heartbeat(connection, owner)
        dropped += self._drop_text_objects(connection, objects)
        retired_tables = tuple(
            name for name, object_type in RETIRED_MANAGED_OBJECTS if object_type == "TABLE"
        )
        for table_name in reversed((*MANAGED_TABLES, *retired_tables)):
            if table_name == CONTROL_TABLE or (table_name, "TABLE") not in objects:
                continue
            dropped += self._execute_drop(
                connection,
                f"DROP TABLE {table_name} CASCADE CONSTRAINTS PURGE",
            )
            self._heartbeat(connection, owner)
        return dropped

    def _drop_retired_objects(self, connection: Any) -> int:
        objects = self._load_objects(connection)
        dropped = 0
        for name, object_type in reversed(RETIRED_MANAGED_OBJECTS):
            if (name, object_type) not in objects:
                continue
            statement = (
                f"DROP TABLE {name} CASCADE CONSTRAINTS PURGE"
                if object_type == "TABLE"
                else f"DROP INDEX {name}"
            )
            dropped += self._execute_drop(connection, statement)
        return dropped

    @staticmethod
    def _drop_text_objects(
        connection: Any,
        objects: dict[tuple[str, str], Any],
    ) -> int:
        dropped = 0
        statements = (
            (
                (ORACLE_TEXT_LEXER_PREFERENCE, "TEXT_PREFERENCE"),
                f"BEGIN CTX_DDL.DROP_PREFERENCE('{ORACLE_TEXT_LEXER_PREFERENCE}'); END;",
            ),
            (
                (ORACLE_TEXT_STOPLIST, "TEXT_STOPLIST"),
                f"BEGIN CTX_DDL.DROP_STOPLIST('{ORACLE_TEXT_STOPLIST}'); END;",
            ),
        )
        with connection.cursor() as cursor:
            for key, statement in statements:
                if key not in objects:
                    continue
                cursor.execute(statement)
                dropped += 1
            connection.commit()
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
    "MANAGED_INDEXES",
    "MANAGED_OBJECTS",
    "MANAGED_TABLES",
    "MANAGED_TEXT_OBJECTS",
    "MIGRATIONS",
    "MIGRATION_TABLE",
    "RECREATE_CONFIRMATION",
    "RETIRED_MANAGED_OBJECTS",
    "MigrationArtifact",
    "SystemSchemaActiveJobsError",
    "SystemSchemaBusyError",
    "SystemSchemaError",
    "SystemSchemaManager",
    "classify_system_schema_status",
    "managed_manifest_from_schema",
    "oracle_error_code",
    "system_schema_manager",
]
