"""Agent のシステムテーブル（versioned Oracle system schema）を明示操作する manager（#751）。

RAG / NL2SQL と同じく、platform の骨格
（`pr_system_settings.system_schema.SystemSchemaManagerBase`）に、Agent の manifest と
DDL の正本を渡す。アプリの起動時には DDL を実行しない。管理 API（運用設定 > システムテーブル）と
CLI（`python -m app.cli.agent_system_schema`）から明示されたときだけ、不足分の作成・
未適用の migration・廃止 object の削除・確認語付きの全再作成を行う。

- 接続は共通 `.env` の `PLATFORM_ORACLE_*`（`app.oracle_connection`）。
- 共通認証の `PLATFORM_*` と組み込み SYSTEM_ADMIN ロールは、`AGENT_ROLE_*` の FK の参照先なので
  先に冪等に用意する（RAG と同じ）。管理対象ではないため、全再作成でも削除しない。
- Run・業務 Agent の保存先（`AGENT_RUNTIME_CHECKPOINTS` と監査用の projection
  `AGENT_RUNTIME_*`）と、画面で変えた定義（Skill・プラグイン・MCP 接続・ツール権限）の
  `AGENT_CONTROL_PLANE_ITEMS` もここで作る（#764。以前は Runtime repository が別の接続設定で
  作っていた）。全再作成はこれらも消す（Run の履歴も消える）。
"""

from __future__ import annotations

import hashlib
import logging
import re
from collections.abc import Callable, Sequence
from contextlib import AbstractContextManager
from dataclasses import dataclass
from typing import Any

from pr_system_settings.auth.migrations import apply_platform_auth_schema
from pr_system_settings.auth.store import PLATFORM_AUTH_TABLES, OracleAuthStore
from pr_system_settings.system_schema import (
    IGNORED_APPLY_CODES,
    IGNORED_DROP_CODES,
    ForeignKeySpec,
    SystemSchemaError,
    SystemSchemaManagerBase,
    SystemSchemaStatus,
    bind_list,
    foreign_keys_from_create_table,
    iso_timestamp,
)
from pr_system_settings.system_schema import (
    classify_system_schema_status as classify_schema_status,
)

from app.oracle_connection import platform_oracle_connection
from app.security.permissions import RETIRED_PERMISSION_CODES

logger = logging.getLogger(__name__)

RECREATE_CONFIRMATION = "RECREATE_AGENT_SYSTEM_TABLES"
CONTROL_TABLE = "AGENT_SCHEMA_OPERATIONS"
MIGRATION_TABLE = "AGENT_SCHEMA_MIGRATIONS"
DEFAULT_DDL_LOCK_TIMEOUT_SECONDS = 5

_CREATE_TABLE_PATTERN = re.compile(r"^\s*CREATE\s+TABLE\s+([A-Z][A-Z0-9_$#]*)\b", re.IGNORECASE)
_CREATE_INDEX_PATTERN = re.compile(
    r"^\s*CREATE\s+(?:UNIQUE\s+)?INDEX\s+([A-Z][A-Z0-9_$#]*)\b", re.IGNORECASE
)

# ---- DDL の正本 ---------------------------------------------------------------

# 適用済み migration の台帳と、複数 replica の間で排他する操作の lease（RAG / NL2SQL と同じ形）。
CONTROL_STATEMENTS: tuple[str, ...] = (
    f"""
    CREATE TABLE {MIGRATION_TABLE} (
        MIGRATION_NAME VARCHAR2(128) NOT NULL,
        DESCRIPTION VARCHAR2(256) NOT NULL,
        CHECKSUM VARCHAR2(64) NOT NULL,
        APPLIED_AT TIMESTAMP WITH TIME ZONE DEFAULT SYSTIMESTAMP NOT NULL,
        CONSTRAINT PK_AGENT_SCHEMA_MIGRATIONS PRIMARY KEY (MIGRATION_NAME)
    )
    """,
    f"""
    CREATE TABLE {CONTROL_TABLE} (
        OPERATION_KEY VARCHAR2(32) NOT NULL,
        STATUS VARCHAR2(16) DEFAULT 'IDLE' NOT NULL,
        OPERATION_KIND VARCHAR2(16),
        LEASE_OWNER VARCHAR2(64),
        LEASE_EXPIRES_AT TIMESTAMP WITH TIME ZONE,
        LAST_ERROR_CODE VARCHAR2(64),
        SCHEMA_EPOCH NUMBER(19) DEFAULT 0 NOT NULL,
        UPDATED_AT TIMESTAMP WITH TIME ZONE DEFAULT SYSTIMESTAMP NOT NULL,
        CONSTRAINT PK_AGENT_SCHEMA_OPERATIONS PRIMARY KEY (OPERATION_KEY),
        CONSTRAINT CK_AGENT_SCHEMA_OP_STATUS CHECK (STATUS IN ('IDLE', 'RUNNING', 'FAILED'))
    )
    """,
    f"""
    INSERT INTO {CONTROL_TABLE} (OPERATION_KEY, STATUS, SCHEMA_EPOCH)
    VALUES ('system_schema', 'IDLE', 0)
    """,
)

# ロールに付ける Agent の権限と対象範囲（#215 / #750）。ロール本体は共通の PLATFORM_ROLES。
# エージェントは Runtime repository の状態のため、AGENT_ID に FK は持てない。エージェントの削除で
# `remove_agent_assignments` が AGENT_ID で消すため、AGENT_ID の index を持つ。
BASE_STATEMENTS: tuple[str, ...] = (
    """
    CREATE TABLE AGENT_ROLE_PERMISSIONS (
        ROLE_ID VARCHAR2(36) NOT NULL,
        PERMISSION_CODE VARCHAR2(128) NOT NULL,
        CONSTRAINT PK_AGENT_ROLE_PERMISSIONS PRIMARY KEY (ROLE_ID, PERMISSION_CODE),
        CONSTRAINT FK_AGENT_ROLE_PERMISSIONS_ROLE FOREIGN KEY (ROLE_ID)
            REFERENCES PLATFORM_ROLES (ROLE_ID) ON DELETE CASCADE
    )
    """,
    """
    CREATE TABLE AGENT_ROLE_AGENTS (
        ROLE_ID VARCHAR2(36) NOT NULL,
        AGENT_ID VARCHAR2(128) NOT NULL,
        CONSTRAINT PK_AGENT_ROLE_AGENTS PRIMARY KEY (ROLE_ID, AGENT_ID),
        CONSTRAINT FK_AGENT_ROLE_AGENTS_ROLE FOREIGN KEY (ROLE_ID)
            REFERENCES PLATFORM_ROLES (ROLE_ID) ON DELETE CASCADE
    )
    """,
    "CREATE INDEX AGENT_ROLE_AGENTS_AGENT_IDX ON AGENT_ROLE_AGENTS (AGENT_ID)",
)

# Run・業務 Agent の checkpoint（snapshot の JSON）と、oracle_normalized の監査用の
# projection（#764）。名前は `app.features.agent.runtime` の RUNTIME_CHECKPOINT_TABLE /
# RUNTIME_PROJECTION_PREFIX と同じ。
RUNTIME_STATEMENTS: tuple[str, ...] = (
    """
    CREATE TABLE AGENT_RUNTIME_CHECKPOINTS (
        CHECKPOINT_KEY VARCHAR2(128) PRIMARY KEY,
        SNAPSHOT_JSON CLOB NOT NULL,
        UPDATED_AT TIMESTAMP WITH TIME ZONE DEFAULT SYSTIMESTAMP NOT NULL
    )
    """,
    """
    CREATE TABLE AGENT_RUNTIME_RUNS (
        RUN_ID VARCHAR2(128) PRIMARY KEY,
        AGENT_ID VARCHAR2(128) NOT NULL,
        STATUS VARCHAR2(32) NOT NULL,
        GOAL CLOB NOT NULL,
        METADATA_JSON CLOB,
        PENDING_TOOL_CALLS_JSON CLOB,
        CREATED_AT TIMESTAMP WITH TIME ZONE NOT NULL,
        UPDATED_AT TIMESTAMP WITH TIME ZONE NOT NULL
    )
    """,
    """
    CREATE TABLE AGENT_RUNTIME_EVENTS (
        EVENT_ID VARCHAR2(128) PRIMARY KEY,
        RUN_ID VARCHAR2(128) NOT NULL,
        EVENT_TYPE VARCHAR2(128) NOT NULL,
        MESSAGE CLOB NOT NULL,
        PAYLOAD_JSON CLOB,
        CREATED_AT TIMESTAMP WITH TIME ZONE NOT NULL
    )
    """,
    """
    CREATE TABLE AGENT_RUNTIME_STEPS (
        STEP_ID VARCHAR2(128) PRIMARY KEY,
        RUN_ID VARCHAR2(128) NOT NULL,
        KIND VARCHAR2(64) NOT NULL,
        STATUS VARCHAR2(32) NOT NULL,
        TOOL_NAME VARCHAR2(256),
        APPROVAL_ID VARCHAR2(128),
        TOOL_CALL_JSON CLOB,
        TOOL_RESULT_JSON CLOB,
        STARTED_AT TIMESTAMP WITH TIME ZONE,
        COMPLETED_AT TIMESTAMP WITH TIME ZONE
    )
    """,
    """
    CREATE TABLE AGENT_RUNTIME_APPROVALS (
        APPROVAL_ID VARCHAR2(128) PRIMARY KEY,
        RUN_ID VARCHAR2(128) NOT NULL,
        STEP_ID VARCHAR2(128) NOT NULL,
        TOOL_NAME VARCHAR2(256) NOT NULL,
        STATUS VARCHAR2(32) NOT NULL,
        REASON CLOB NOT NULL,
        DECIDED_BY VARCHAR2(256),
        DECIDED_AT TIMESTAMP WITH TIME ZONE,
        CREATED_AT TIMESTAMP WITH TIME ZONE NOT NULL,
        TOOL_CALL_JSON CLOB NOT NULL
    )
    """,
    """
    CREATE TABLE AGENT_RUNTIME_ARTIFACTS (
        ARTIFACT_ID VARCHAR2(128) PRIMARY KEY,
        RUN_ID VARCHAR2(128) NOT NULL,
        NAME VARCHAR2(512) NOT NULL,
        KIND VARCHAR2(128) NOT NULL,
        CONTENT_JSON CLOB NOT NULL,
        CREATED_AT TIMESTAMP WITH TIME ZONE NOT NULL
    )
    """,
    "CREATE INDEX AGENT_RUNTIME_RUNS_STATUS_CREATED_IX ON AGENT_RUNTIME_RUNS (STATUS, CREATED_AT)",
    "CREATE INDEX AGENT_RUNTIME_EVENTS_RUN_TYPE_CREATED_IX "
    "ON AGENT_RUNTIME_EVENTS (RUN_ID, EVENT_TYPE, CREATED_AT)",
    "CREATE INDEX AGENT_RUNTIME_STEPS_RUN_TOOL_STATUS_IX "
    "ON AGENT_RUNTIME_STEPS (RUN_ID, TOOL_NAME, STATUS, COMPLETED_AT)",
    "CREATE INDEX AGENT_RUNTIME_STEPS_ERROR_CODE_IX ON AGENT_RUNTIME_STEPS "
    "(JSON_VALUE(TOOL_RESULT_JSON, '$.error_code' RETURNING VARCHAR2(128)))",
    "CREATE INDEX AGENT_RUNTIME_APPROVALS_RUN_STATUS_IX "
    "ON AGENT_RUNTIME_APPROVALS (RUN_ID, STATUS, CREATED_AT)",
    "CREATE INDEX AGENT_RUNTIME_ARTIFACTS_RUN_KIND_IX "
    "ON AGENT_RUNTIME_ARTIFACTS (RUN_ID, KIND, CREATED_AT)",
)

# 画面・API で変えた定義（#764）。ITEM_KIND は skill / plugin / marketplace / mcp_connection /
# tool_policy / api_key（#778）。ITEM_JSON は定義の JSON（MCP 接続の秘密は暗号化した値、
# API キーは秘密を持たず SHA-256 の hash だけ）。
CONTROL_PLANE_STATEMENTS: tuple[str, ...] = (
    """
    CREATE TABLE AGENT_CONTROL_PLANE_ITEMS (
        ITEM_KIND VARCHAR2(32) NOT NULL,
        ITEM_ID VARCHAR2(200) NOT NULL,
        ITEM_JSON CLOB NOT NULL,
        UPDATED_AT TIMESTAMP WITH TIME ZONE DEFAULT SYSTIMESTAMP NOT NULL,
        CONSTRAINT PK_AGENT_CONTROL_PLANE_ITEMS PRIMARY KEY (ITEM_KIND, ITEM_ID)
    )
    """,
)

# 未初期化の DB と全再作成で作る、Agent の管理対象の DDL の全体。
DOMAIN_STATEMENTS: tuple[str, ...] = (
    *BASE_STATEMENTS,
    *RUNTIME_STATEMENTS,
    *CONTROL_PLANE_STATEMENTS,
)


@dataclass(frozen=True, slots=True)
class MigrationArtifact:
    """台帳に記録する 1 migration（checksum は SQL から決まる）。"""

    name: str
    description: str
    statements: tuple[str, ...]
    # データを消す migration の説明（空なら破壊的でない。checksum には含めない。#619）。
    destructive_note: str = ""

    @property
    def checksum(self) -> str:
        normalized = "\n;\n".join(" ".join(statement.split()) for statement in self.statements)
        return hashlib.sha256(normalized.encode("utf-8")).hexdigest()

    @property
    def destructive(self) -> bool:
        return bool(self.destructive_note)


def _retired_codes_sql(codes: tuple[str, ...]) -> str:
    return ", ".join(f"'{code}'" for code in codes)


# migration の SQL は checksum に入るため、適用済みの migration の対象は固定する。
# 新しく廃止したコードは新しい migration で消す（全体は RETIRED_PERMISSION_CODES）。
_RETIRED_CODES_002 = ("menu.dashboard",)
_RETIRED_CODES_004 = ("menu.settings_external_rag", "menu.settings_external_nl2sql")
_RETIRED_CODES_005 = ("menu.settings_connection",)
if set(_RETIRED_CODES_002) | set(_RETIRED_CODES_004) | set(_RETIRED_CODES_005) != set(
    RETIRED_PERMISSION_CODES
):  # pragma: no cover - 定義の誤りを起動時に検出
    raise RuntimeError("廃止した権限コードを消す migration を追加してください。")

# 未初期化の DB（`missing`）と全再作成では、正本の DDL で最新の形を作り、migration は実行せずに
# 記録だけする。それ以外は未適用・checksum 不一致の migration を順に適用する。
MIGRATIONS: tuple[MigrationArtifact, ...] = (
    MigrationArtifact(
        "20261002_001_role_access",
        "agent role permissions and agent scope",
        BASE_STATEMENTS,
    ),
    MigrationArtifact(
        "20261002_002_remove_retired_permission_codes",
        "remove retired permission codes from roles",
        (
            "DELETE FROM AGENT_ROLE_PERMISSIONS "  # nosec B608 - 固定の権限コード
            f"WHERE PERMISSION_CODE IN ({_retired_codes_sql(_RETIRED_CODES_002)})",
        ),
    ),
    MigrationArtifact(
        "20261002_003_retire_role_business_views",
        "retire agent role business views",
        ("DROP TABLE AGENT_ROLE_BUSINESS_VIEWS CASCADE CONSTRAINTS PURGE",),
        destructive_note=(
            "権限管理でロールに割り当てていた業務ビュー（AGENT_ROLE_BUSINESS_VIEWS）を削除します。"
            "#750 から使っていません。業務ビューの権限は RAG の権限管理で割り当てます。"
            "割り当てを控える必要があれば、先にテーブルを書き出してください。"
        ),
    ),
    MigrationArtifact(
        "20261002_004_remove_external_settings_permissions",
        "remove external RAG / NL2SQL menu permissions (merged into MCP connections)",
        (
            "DELETE FROM AGENT_ROLE_PERMISSIONS "  # nosec B608 - 固定の権限コード
            f"WHERE PERMISSION_CODE IN ({_retired_codes_sql(_RETIRED_CODES_004)})",
        ),
    ),
    MigrationArtifact(
        "20261002_005_remove_connection_settings_permission",
        "remove the placeholder agent connection settings menu permission",
        (
            "DELETE FROM AGENT_ROLE_PERMISSIONS "  # nosec B608 - 固定の権限コード
            f"WHERE PERMISSION_CODE IN ({_retired_codes_sql(_RETIRED_CODES_005)})",
        ),
    ),
    MigrationArtifact(
        "20261002_006_runtime_and_control_plane_tables",
        "manage run checkpoint / projection tables and add control plane items (#764)",
        # 以前は Runtime repository が作っていた。既存のテーブルは ORA-00955 として読み飛ばす。
        (*RUNTIME_STATEMENTS, *CONTROL_PLANE_STATEMENTS),
    ),
)

# ---- manifest -----------------------------------------------------------------

# 作成の依存順。全再作成はこの逆順で DROP する（制御テーブルは残す）。
MANAGED_TABLES: tuple[str, ...] = (
    CONTROL_TABLE,
    MIGRATION_TABLE,
    "AGENT_ROLE_PERMISSIONS",
    "AGENT_ROLE_AGENTS",
    "AGENT_RUNTIME_CHECKPOINTS",
    "AGENT_RUNTIME_RUNS",
    "AGENT_RUNTIME_EVENTS",
    "AGENT_RUNTIME_STEPS",
    "AGENT_RUNTIME_APPROVALS",
    "AGENT_RUNTIME_ARTIFACTS",
    "AGENT_CONTROL_PLANE_ITEMS",
)
MANAGED_INDEXES: tuple[str, ...] = (
    "AGENT_ROLE_AGENTS_AGENT_IDX",
    "AGENT_RUNTIME_RUNS_STATUS_CREATED_IX",
    "AGENT_RUNTIME_EVENTS_RUN_TYPE_CREATED_IX",
    "AGENT_RUNTIME_STEPS_RUN_TOOL_STATUS_IX",
    "AGENT_RUNTIME_STEPS_ERROR_CODE_IX",
    "AGENT_RUNTIME_APPROVALS_RUN_STATUS_IX",
    "AGENT_RUNTIME_ARTIFACTS_RUN_KIND_IX",
)
MANAGED_OBJECTS: tuple[tuple[str, str], ...] = (
    *((name, "TABLE") for name in MANAGED_TABLES),
    *((name, "INDEX") for name in MANAGED_INDEXES),
)
# 現行では使わないが既存の DB に残りうる object（作成・更新で削除する）。
RETIRED_MANAGED_OBJECTS: tuple[tuple[str, str], ...] = (
    # 権限管理の業務ビュー（#750 で使わなくなった）。更新では migration 003 が消す。
    # 全再作成は migration を実行せず記録だけするため、ここにも載せて残さない。
    ("AGENT_ROLE_BUSINESS_VIEWS", "TABLE"),
)
DOMAIN_TABLES = frozenset(MANAGED_TABLES) - {CONTROL_TABLE, MIGRATION_TABLE}
# 3 製品共通の認証テーブル。先に作るが管理対象ではないため、全再作成でも削除しない。
PRESERVED_TABLES: tuple[str, ...] = tuple(PLATFORM_AUTH_TABLES)

MANAGED_FOREIGN_KEYS: tuple[ForeignKeySpec, ...] = tuple(
    foreign_key
    for statement in BASE_STATEMENTS
    for foreign_key in foreign_keys_from_create_table(statement)
)

if set(PRESERVED_TABLES) & set(MANAGED_TABLES):  # pragma: no cover - 定義の誤りを起動時に検出
    raise RuntimeError("共通認証のテーブルを Agent の管理対象に含めないでください。")


def managed_manifest_from_schema() -> set[tuple[str, str]]:
    """DDL の正本の CREATE TABLE / INDEX（manifest と一致することをテストで確かめる）。"""

    objects: set[tuple[str, str]] = set()
    for statement in (*CONTROL_STATEMENTS, *DOMAIN_STATEMENTS):
        if (table := _CREATE_TABLE_PATTERN.match(statement)) is not None:
            objects.add((table.group(1).upper(), "TABLE"))
        if (index := _CREATE_INDEX_PATTERN.match(statement)) is not None:
            objects.add((index.group(1).upper(), "INDEX"))
    return objects


def classify_system_schema_status(
    objects: set[tuple[str, str]],
    applied_checksums: dict[str, str],
    *,
    foreign_keys_current: bool = True,
) -> SystemSchemaStatus:
    """Dictionary / 台帳の snapshot を四つの公開状態へ分類する（規則は platform）。"""

    return classify_schema_status(
        objects,
        domain_tables=DOMAIN_TABLES,
        managed_objects=MANAGED_OBJECTS,
        retired_objects=RETIRED_MANAGED_OBJECTS,
        migrations_current=all(
            applied_checksums.get(migration.name) == migration.checksum for migration in MIGRATIONS
        ),
        foreign_keys_current=foreign_keys_current,
    )


def pending_destructive_migrations(
    status: str, pending_versions: Sequence[str]
) -> list[dict[str, str]]:
    """未適用の、データを消す migration（未初期化の DB は実行しないので無い。#619）。"""

    if status == "missing":
        return []
    pending = set(pending_versions)
    return [
        {"name": migration.name, "description": migration.destructive_note}
        for migration in MIGRATIONS
        if migration.destructive and migration.name in pending
    ]


class SystemSchemaManager(SystemSchemaManagerBase):
    """Agent の manifest と DDL の正本を、platform の骨格（lease・台帳）に渡す。"""

    control_table = CONTROL_TABLE
    migration_table = MIGRATION_TABLE
    migration_key_column = "MIGRATION_NAME"
    managed_tables = MANAGED_TABLES
    managed_foreign_keys = MANAGED_FOREIGN_KEYS
    # データを消す未適用の migration は、承認が無ければ作成・更新で当てない（#619）。
    guards_destructive_migrations = True
    recreate_confirmation = RECREATE_CONFIRMATION
    log_prefix = "agent"
    lock_timeout_guidance = "権限管理の保存を止めてから、"
    logger = logger

    def __init__(
        self,
        connection_factory: Callable[[], AbstractContextManager[Any]] | None = None,
        *,
        lease_seconds: int = 900,
        ddl_lock_timeout_seconds: int = DEFAULT_DDL_LOCK_TIMEOUT_SECONDS,
    ) -> None:
        super().__init__(
            connection_factory or platform_oracle_connection,
            lease_seconds=lease_seconds,
            ddl_lock_timeout_seconds=ddl_lock_timeout_seconds,
        )

    def _operation_kind(self, recreate: bool) -> str:
        # AGENT_SCHEMA_OPERATIONS.OPERATION_KIND は大文字で書く（RAG と同じ）。
        return "RECREATE" if recreate else "INITIALIZE"

    # ---- 状態 -------------------------------------------------------------

    def _status_on(self, connection: Any) -> dict[str, Any]:
        objects = self._load_objects(connection)
        expected = set(MANAGED_OBJECTS)
        existing = expected.intersection(objects)
        applied = self._load_migrations(connection, objects)
        matching = [m.name for m in MIGRATIONS if applied.get(m.name) == m.checksum]
        pending = [m.name for m in MIGRATIONS if applied.get(m.name) != m.checksum]
        foreign_keys = self._foreign_key_drift(connection)
        table_metadata = self._load_table_metadata(connection, objects)
        status = classify_system_schema_status(
            set(objects), applied, foreign_keys_current=foreign_keys.current
        )
        return {
            "status": status,
            "schema_head": MIGRATIONS[-1].name,
            "applied_versions": matching,
            "pending_versions": pending,
            "pending_destructive_migrations": pending_destructive_migrations(status, pending),
            "expected_object_count": len(expected),
            "existing_object_count": len(existing),
            "expected_table_count": len(MANAGED_TABLES),
            "existing_table_count": sum((name, "TABLE") in objects for name in MANAGED_TABLES),
            "missing_objects": [
                {"name": name, "object_type": object_type}
                for name, object_type in sorted(
                    expected - existing, key=lambda item: (item[1], item[0])
                )
            ],
            "retired_objects": [
                {"name": name, "object_type": object_type}
                for name, object_type in RETIRED_MANAGED_OBJECTS
                if (name, object_type) in objects
            ],
            **foreign_keys.status_fields(),
            "tables": table_metadata,
            # 全管理 object（テーブル・索引）。詳細の一覧を概要の件数とそろえる
            # （RAG / NL2SQL と同じ）。
            "objects": self._build_object_metadata(objects, table_metadata),
            "operation_state": self._operation_payload(connection, objects),
        }

    @staticmethod
    def _build_object_metadata(
        objects: dict[tuple[str, str], Any],
        table_metadata: Sequence[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        tables_by_name = {str(item["name"]): item for item in table_metadata}
        result: list[dict[str, Any]] = []
        for name, object_type in MANAGED_OBJECTS:
            table = tables_by_name.get(name) if object_type == "TABLE" else None
            result.append(
                {
                    "name": name,
                    "object_type": object_type,
                    "exists": (name, object_type) in objects,
                    "estimated_rows": table.get("estimated_rows") if table else None,
                    "created_at": iso_timestamp(objects.get((name, object_type))),
                    "last_analyzed_at": table.get("last_analyzed_at") if table else None,
                }
            )
        return result

    @staticmethod
    def _load_objects(connection: Any) -> dict[tuple[str, str], Any]:
        inspected = tuple(dict.fromkeys((*MANAGED_OBJECTS, *RETIRED_MANAGED_OBJECTS)))
        names = tuple(dict.fromkeys(name for name, _object_type in inspected))
        placeholders, binds = bind_list("object_name_", names)
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT OBJECT_NAME, OBJECT_TYPE, CREATED FROM USER_OBJECTS "
                f"WHERE OBJECT_NAME IN ({placeholders}) "  # nosec B608 - 固定の bind
                "AND OBJECT_TYPE IN ('TABLE', 'INDEX')",
                binds,
            )
            return {(str(row[0]).upper(), str(row[1]).upper()): row[2] for row in cursor.fetchall()}

    # ---- 作成・更新 ---------------------------------------------------------

    def _ensure_control_schema(self) -> None:
        with self._connection_factory() as connection:
            self._configure_ddl_lock_timeout(connection)
            self._execute_statements(connection, CONTROL_STATEMENTS)

    def _initialize_on(self, connection: Any, owner: str, *, recreate: bool) -> dict[str, Any]:
        dropped_count = 0
        applied_names: list[str] = []
        self._configure_ddl_lock_timeout(connection)
        # AGENT_ROLE_* は PLATFORM_ROLES を参照するため、共通認証の表を先に作る（冪等）。
        self._apply_platform_auth_schema(connection)
        before = self._status_on(connection)
        if before["status"] == "ready" and not recreate:
            return self._no_op_result(connection, owner)

        if recreate:
            dropped_count += self._drop_managed_objects(connection, owner)

        self._heartbeat(connection, owner)
        # 全再作成は台帳（AGENT_SCHEMA_MIGRATIONS）も消すため、制御テーブルから作り直す（冪等）。
        self._execute_statements(connection, (*CONTROL_STATEMENTS, *DOMAIN_STATEMENTS))
        if before["status"] == "missing" or recreate:
            for migration in MIGRATIONS:
                self._record_migration(connection, migration)
                applied_names.append(migration.name)
        else:
            pending = set(before["pending_versions"])
            for migration in MIGRATIONS:
                if migration.name not in pending:
                    continue
                self._heartbeat(connection, owner)
                self._execute_statements(
                    connection,
                    migration.statements,
                    ignored_codes=IGNORED_APPLY_CODES | IGNORED_DROP_CODES,
                )
                self._record_migration(connection, migration)
                applied_names.append(migration.name)

        dropped_count += self._drop_retired_objects(connection)
        foreign_keys = self._repair_foreign_keys(connection, owner)
        self._heartbeat(connection, owner)
        interim = self._status_on(connection)
        if interim["status"] != "ready":
            raise SystemSchemaError(
                "SCHEMA_POSTCONDITION_FAILED",
                "システムテーブル操作後も必須オブジェクトが不足しています。"
                "状態を再取得して再試行してください。",
            )
        self._finish_operation(connection, owner, increment_epoch=True)
        after = self._status_on(connection)
        operation = self._operation_name(before["status"], recreate=recreate)
        self._log_operation_succeeded(
            operation,
            after,
            dropped_object_count=dropped_count,
            applied_migrations=applied_names,
            added_foreign_keys=[item["name"] for item in foreign_keys["added"]],
        )
        return {
            **after,
            "operation": operation,
            "dropped_object_count": dropped_count,
            "created_object_count": (
                max(0, int(after["existing_object_count"]) - 1)
                if recreate
                else max(
                    0, int(after["existing_object_count"]) - int(before["existing_object_count"])
                )
            ),
        }

    @staticmethod
    def _apply_platform_auth_schema(connection: Any) -> None:
        """共通認証の PLATFORM_* と組み込み SYSTEM_ADMIN ロールを冪等に用意する。

        ユーザーは作らない（最初は構成管理者 `system_admin` でログインする）。
        """
        apply_platform_auth_schema(connection)
        with connection.cursor() as cursor:
            OracleAuthStore._merge_system_admin_role(cursor)
        connection.commit()

    def _record_migration(self, connection: Any, migration: MigrationArtifact) -> None:
        with connection.cursor() as cursor:
            self._merge_migration(
                cursor,
                key=migration.name,
                description=migration.description,
                checksum=migration.checksum,
            )
        connection.commit()

    def _drop_managed_objects(self, connection: Any, owner: str) -> int:
        objects = self._load_objects(connection)
        dropped = 0
        for index_name in reversed(MANAGED_INDEXES):
            if (index_name, "INDEX") in objects:
                dropped += self._execute_drop(connection, f"DROP INDEX {index_name}")
        retired_tables = tuple(
            name for name, object_type in RETIRED_MANAGED_OBJECTS if object_type == "TABLE"
        )
        for table_name in reversed((*MANAGED_TABLES, *retired_tables)):
            if table_name == CONTROL_TABLE or (table_name, "TABLE") not in objects:
                continue
            dropped += self._execute_drop(
                connection, f"DROP TABLE {table_name} CASCADE CONSTRAINTS PURGE"
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


system_schema_manager = SystemSchemaManager()


__all__ = [
    "BASE_STATEMENTS",
    "CONTROL_PLANE_STATEMENTS",
    "CONTROL_STATEMENTS",
    "DOMAIN_STATEMENTS",
    "RUNTIME_STATEMENTS",
    "CONTROL_TABLE",
    "DOMAIN_TABLES",
    "MANAGED_FOREIGN_KEYS",
    "MANAGED_INDEXES",
    "MANAGED_OBJECTS",
    "MANAGED_TABLES",
    "MIGRATIONS",
    "MIGRATION_TABLE",
    "PRESERVED_TABLES",
    "RECREATE_CONFIRMATION",
    "RETIRED_MANAGED_OBJECTS",
    "MigrationArtifact",
    "SystemSchemaError",
    "SystemSchemaManager",
    "classify_system_schema_status",
    "managed_manifest_from_schema",
    "pending_destructive_migrations",
    "system_schema_manager",
]
