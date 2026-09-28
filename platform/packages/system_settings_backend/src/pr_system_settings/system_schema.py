"""システムテーブル（製品の versioned Oracle system schema）の管理の骨格（3製品共通。#325 段階 4）。

RAG と NL2SQL が別々に持っていた schema manager の共通部分を置く。

- 公開状態の分類（`missing` / `partial` / `outdated` / `ready`）
- 操作の lease（`<製品>_SCHEMA_OPERATIONS` の 1 行を、複数 replica の間で排他する）と
  `schema_epoch`（成功した操作ごとに増える。別 replica の cache の破棄に使う）
- 適用済み migration の台帳（`<製品>_SCHEMA_MIGRATIONS`。checksum が一致したものだけを適用済みに
  する）
- 全再作成の確認語の検証（DB に触る前に、完全一致だけを通す）
- 操作の失敗の安全化（公開するのは ORA コードだけ。SQL・資格情報は返さない）と、ORA-00054 の 409
- API の型（操作の状態・初期化の request）と、状態を取得できないときの 503

製品に残すもの（ここには置かない）:

- 必要なテーブル・索引などの一覧（manifest）と DDL の正本、migration の適用方法
- 状態の組み立て（`_status_on`）と初期化の手順（`_initialize_on`）
- 実行中の job の確認、接続 pool、製品名が入る文言と確認語

アプリの起動時には DDL を実行しない。管理 API / CLI から明示されたときだけ操作する。
共有パッケージは oracledb に依存しない（接続は製品が `connection_factory` で渡す）。
"""

from __future__ import annotations

import logging
import re
import uuid
from abc import ABC, abstractmethod
from collections.abc import Callable, Collection, Iterable, Mapping, Sequence
from contextlib import AbstractContextManager, suppress
from datetime import UTC, datetime
from typing import Any, ClassVar, Literal

from fastapi import HTTPException
from pydantic import BaseModel, Field

SystemSchemaStatus = Literal["missing", "partial", "outdated", "ready"]
SystemSchemaOperation = Literal["no_op", "initialized", "migrated", "recreated"]
SystemSchemaOperationStatus = Literal["idle", "running", "failed"]
SystemSchemaOperationKind = Literal["initialize", "recreate"]

# 制御テーブルの行のキー（製品ごとに 1 行）。
CONTROL_KEY = "system_schema"
DEFAULT_LEASE_SECONDS = 900
MIN_LEASE_SECONDS = 30
MAX_DDL_LOCK_TIMEOUT_SECONDS = 120
# ORA-00054（DDL のロック待ちの時間切れ）を返すときの Retry-After（秒）。
LOCK_RETRY_AFTER_SECONDS = 5

RECREATE_CONFIRMATION_REQUIRED = "SCHEMA_RECREATE_CONFIRMATION_REQUIRED"
SCHEMA_OPERATION_FAILED = "SCHEMA_OPERATION_FAILED"
SCHEMA_STATUS_UNAVAILABLE = "SCHEMA_STATUS_UNAVAILABLE"
ORA_RESOURCE_BUSY = "ORA-00054"

# 冪等な適用で読み飛ばす Oracle のエラー。
IGNORED_APPLY_CODES = frozenset(
    {
        "ORA-00001",  # migration の台帳・seed の重複
        "ORA-00955",  # object already exists
        "ORA-01430",  # column already exists
        "ORA-01442",  # column is already NOT NULL
        "ORA-01451",  # column is already NULL
    }
)
# DROP で読み飛ばす Oracle のエラー（既に無い）。
IGNORED_DROP_CODES = frozenset({"ORA-00942", "ORA-01418"})

_ORA_CODE_PATTERN = re.compile(r"ORA-\d{5}", flags=re.IGNORECASE)

ConnectionFactory = Callable[[], AbstractContextManager[Any]]


class SystemSchemaError(RuntimeError):
    """secret や SQL を含めない schema operation error。"""

    def __init__(self, code: str, public_message: str, *, status_code: int = 500) -> None:
        super().__init__(public_message)
        self.code = code
        self.public_message = public_message
        self.status_code = status_code

    @property
    def retryable(self) -> bool:
        """ロック待ちの時間切れ（ORA-00054）だけは、待てば再試行できる。"""

        return self.code == ORA_RESOURCE_BUSY

    @property
    def retry_headers(self) -> dict[str, str] | None:
        return {"Retry-After": str(LOCK_RETRY_AFTER_SECONDS)} if self.retryable else None


class SystemSchemaBusyError(SystemSchemaError):
    def __init__(self) -> None:
        super().__init__(
            "SCHEMA_OPERATION_IN_PROGRESS",
            "別のシステムテーブル操作が実行中です。完了後に状態を再取得してください。",
            status_code=409,
        )


class SystemSchemaActiveJobsError(SystemSchemaError):
    """全再作成の前に、実行中の job がある（文言は製品が渡す）。"""

    def __init__(self, public_message: str) -> None:
        super().__init__("SCHEMA_JOBS_RUNNING", public_message, status_code=409)


class SystemTableOperationState(BaseModel):
    """複数 replica が共有する schema operation lease の状態と直近の操作。"""

    status: SystemSchemaOperationStatus
    operation_kind: SystemSchemaOperationKind | None = None
    lease_expires_at: str | None = None
    last_error_code: str | None = None
    schema_epoch: int = 0
    updated_at: str | None = None


class SystemTablesInitializeRequest(BaseModel):
    """作成・更新、または全再作成（`recreate` と確認語）の request。"""

    recreate: bool = False
    confirmation: str | None = Field(default=None, max_length=128)


def oracle_error_code(exc: BaseException) -> str:
    """例外から ORA コードだけを取り出す（無ければ `SCHEMA_OPERATION_FAILED`）。"""

    match = _ORA_CODE_PATTERN.search(str(exc))
    return match.group(0).upper() if match else SCHEMA_OPERATION_FAILED


def iso_timestamp(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        current = value if value.tzinfo else value.replace(tzinfo=UTC)
        return current.isoformat()
    return str(value)


def bind_list(prefix: str, values: Sequence[str]) -> tuple[str, dict[str, str]]:
    """`IN (...)` の bind 変数の並びと値（manifest の固定値だけに使う）。"""

    binds = {f"{prefix}{index}": value for index, value in enumerate(values)}
    return ", ".join(f":{name}" for name in binds), binds


def clamp_ddl_lock_timeout(value: int) -> int:
    return max(0, min(MAX_DDL_LOCK_TIMEOUT_SECONDS, int(value)))


def require_recreate_confirmation(
    *,
    recreate: bool,
    confirmation: str | None,
    expected: str,
) -> None:
    """全再作成は確認語の完全一致だけを通す（DB に触る前に確かめる）。"""

    if recreate and confirmation != expected:
        raise SystemSchemaError(
            RECREATE_CONFIRMATION_REQUIRED,
            "すべて再作成するには確認値を正確に入力してください。",
            status_code=422,
        )


def classify_system_schema_status(
    objects: Collection[tuple[str, str]],
    *,
    domain_tables: Iterable[str],
    managed_objects: Iterable[tuple[str, str]],
    migrations_current: bool,
    retired_objects: Iterable[tuple[str, str]] = (),
) -> SystemSchemaStatus:
    """Dictionary / 台帳の snapshot を四つの公開状態へ決定論的に分類する。

    - `missing`: 業務テーブル（制御テーブル以外）が 1 つも無い
    - `partial`: 必須の object のどれかが無い
    - `outdated`: 廃止した object が残っている、または未適用 / checksum 不一致の migration がある
    - `ready`: それ以外
    """

    present = set(objects)
    if not any((name, "TABLE") in present for name in domain_tables):
        return "missing"
    if set(managed_objects) - present:
        return "partial"
    if set(retired_objects) & present:
        return "outdated"
    if not migrations_current:
        return "outdated"
    return "ready"


def idle_operation_state() -> dict[str, Any]:
    """制御テーブルが無い・行が無いときの操作の状態。"""

    return {
        "status": "idle",
        "operation_kind": None,
        "lease_expires_at": None,
        "last_error_code": None,
        "schema_epoch": 0,
        "updated_at": None,
    }


def system_tables_status_error(exc: BaseException) -> HTTPException:
    """状態を取得できないときの 503（公開するのは ORA コードだけ）。"""

    code = oracle_error_code(exc)
    safe_code = code if code.startswith("ORA-") else SCHEMA_STATUS_UNAVAILABLE
    return HTTPException(
        status_code=503,
        detail=f"システムテーブルの状態を取得できませんでした ({safe_code})。",
    )


class SystemSchemaManagerBase(ABC):
    """状態の取得・作成 / 更新・全再作成を、同じ manifest と lease で提供する骨格。

    製品は制御テーブル・台帳の名前と確認語を class 属性で渡し、次を実装する。
    `_status_on`（状態の組み立て）・`_ensure_control_schema`（制御テーブルの作成）・
    `_initialize_on`（lease を取った後の手順）。
    """

    control_table: ClassVar[str]
    migration_table: ClassVar[str]
    # 台帳のキー列（RAG は `MIGRATION_NAME`、NL2SQL は `VERSION_NO`）。
    migration_key_column: ClassVar[str]
    # 状態の `tables` に並べるテーブル（作成の依存順）。
    managed_tables: ClassVar[tuple[str, ...]]
    recreate_confirmation: ClassVar[str]
    # ログのイベント名の接頭辞（`rag` → `rag_system_schema_operation_succeeded`）。
    log_prefix: ClassVar[str]
    # ORA-00054 のときの製品固有の対処（「〜してから、」まで。後に「状態を再取得…」が続く）。
    lock_timeout_guidance: ClassVar[str] = ""
    control_key: ClassVar[str] = CONTROL_KEY
    ignored_drop_codes: ClassVar[frozenset[str]] = IGNORED_DROP_CODES
    logger: ClassVar[logging.Logger] = logging.getLogger(__name__)

    def __init__(
        self,
        connection_factory: ConnectionFactory,
        *,
        lease_seconds: int = DEFAULT_LEASE_SECONDS,
        ddl_lock_timeout_seconds: int,
    ) -> None:
        self._connection_factory = connection_factory
        self._lease_seconds = max(MIN_LEASE_SECONDS, int(lease_seconds))
        self._ddl_lock_timeout_seconds = clamp_ddl_lock_timeout(ddl_lock_timeout_seconds)

    # ---- 製品が実装するもの -------------------------------------------------

    @abstractmethod
    def _status_on(self, connection: Any) -> dict[str, Any]:
        """USER_* dictionary と台帳から公開の状態を組み立てる（DDL は実行しない）。"""

    @abstractmethod
    def _ensure_control_schema(self) -> None:
        """lease を取る前に、制御テーブルと台帳を冪等に用意する。"""

    @abstractmethod
    def _initialize_on(self, connection: Any, owner: str, *, recreate: bool) -> dict[str, Any]:
        """lease を取った後の作成・更新 / 全再作成。成功時は状態と操作の結果を返す。"""

    def _operation_kind(self, recreate: bool) -> str:
        """制御テーブルの OPERATION_KIND に書く値（CHECK 制約に合わせて製品が変えてよい）。"""

        return "recreate" if recreate else "initialize"

    def _migration_key(self, value: Any) -> Any:
        """台帳のキー列の値を、manifest の migration のキーの型にそろえる。"""

        return str(value)

    def _table_identity_fields(self, name: str, owner: str) -> dict[str, Any]:
        """状態の `tables` の各行に足す項目（NL2SQL は所有者付きの名前）。"""

        return {}

    # ---- 公開の操作 ---------------------------------------------------------

    def status(self) -> dict[str, Any]:
        """USER_* dictionary と台帳だけを読む。DDL は実行しない。"""

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
        """作成・更新（不足分と未適用の migration）、または確認語付きの全再作成。"""

        require_recreate_confirmation(
            recreate=recreate,
            confirmation=confirmation,
            expected=self.recreate_confirmation,
        )
        owner = uuid.uuid4().hex
        kind = self._operation_kind(recreate)
        self._ensure_control_schema()
        self._claim_lease(owner, kind)
        try:
            with self._connection_factory() as connection:
                return self._initialize_on(connection, owner, recreate=recreate)
        except SystemSchemaError as exc:
            self._record_failure(owner, exc.code)
            self._log_operation_failed(kind, exc.code, exc)
            raise
        except Exception as exc:
            code = oracle_error_code(exc)
            self._record_failure(owner, code)
            self._log_operation_failed(kind, code, exc)
            raise self._operation_error(code) from exc

    # ---- 手順の部品 ---------------------------------------------------------

    def _no_op_result(self, connection: Any, owner: str) -> dict[str, Any]:
        """最新のときは epoch を増やさずに lease を返し、変更なしの結果にする。"""

        self._finish_operation(connection, owner, increment_epoch=False)
        return {
            **self._status_on(connection),
            "operation": "no_op",
            "dropped_object_count": 0,
            "created_object_count": 0,
        }

    @staticmethod
    def _operation_name(before_status: str, *, recreate: bool) -> SystemSchemaOperation:
        if recreate:
            return "recreated"
        if before_status == "missing":
            return "initialized"
        return "migrated"

    def _log_operation_succeeded(
        self,
        operation: SystemSchemaOperation,
        after: Mapping[str, Any],
        *,
        dropped_object_count: int,
        **extra: Any,
    ) -> None:
        self.logger.info(
            f"{self.log_prefix}_system_schema_operation_succeeded",
            extra={
                "operation": operation,
                "schema_epoch": after["operation_state"]["schema_epoch"],
                **extra,
                "dropped_object_count": dropped_object_count,
            },
        )

    def _log_operation_failed(self, kind: str, code: str, exc: BaseException) -> None:
        self.logger.error(
            f"{self.log_prefix}_system_schema_operation_failed",
            extra={
                "operation": kind.lower(),
                "error_code": code,
                "exception_type": type(exc).__name__,
            },
        )

    def _operation_error(self, code: str) -> SystemSchemaError:
        """想定外の例外を、SQL や資格情報を含まない公開のエラーにする。"""

        if code == ORA_RESOURCE_BUSY:
            return SystemSchemaError(
                code,
                "Oracle の対象オブジェクトのロックが "
                f"{self._ddl_lock_timeout_seconds} 秒以内に解放されませんでした "
                f"(ORA-00054)。{self.lock_timeout_guidance}"
                "状態を再取得して再試行してください。",
                status_code=409,
            )
        return SystemSchemaError(
            code,
            f"システムテーブル操作に失敗しました ({code})。状態を再取得して再試行してください。",
        )

    # ---- 操作の lease -------------------------------------------------------

    def _operation_payload(
        self,
        connection: Any,
        objects: Mapping[tuple[str, str], Any],
    ) -> dict[str, Any]:
        if (self.control_table, "TABLE") not in objects:
            return idle_operation_state()
        return self._load_operation(connection)

    def _load_operation(self, connection: Any) -> dict[str, Any]:
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT STATUS, OPERATION_KIND, LEASE_EXPIRES_AT, LAST_ERROR_CODE, "
                f"SCHEMA_EPOCH, UPDATED_AT FROM {self.control_table} "  # nosec B608 - 固定の識別子
                "WHERE OPERATION_KEY = :operation_key",
                {"operation_key": self.control_key},
            )
            row = cursor.fetchone()
        if row is None:
            return idle_operation_state()
        return {
            "status": str(row[0]).lower(),
            "operation_kind": str(row[1]).lower() if row[1] else None,
            "lease_expires_at": iso_timestamp(row[2]),
            "last_error_code": str(row[3]) if row[3] else None,
            "schema_epoch": int(row[4] or 0),
            "updated_at": iso_timestamp(row[5]),
        }

    def _claim_lease(self, owner: str, operation_kind: str) -> None:
        """実行中でない（または期限切れの）ときだけ lease を取る。取れなければ 409。"""

        with self._connection_factory() as connection, connection.cursor() as cursor:
            cursor.execute(
                f"""
                UPDATE {self.control_table}
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
                """,  # nosec B608 - 固定の識別子
                {
                    "operation_kind": operation_kind,
                    "lease_owner": owner,
                    "lease_seconds": self._lease_seconds,
                    "operation_key": self.control_key,
                },
            )
            claimed = int(cursor.rowcount or 0) == 1
            connection.commit()
        if not claimed:
            raise SystemSchemaBusyError()

    def _heartbeat(self, connection: Any, owner: str) -> None:
        """長い操作の途中で lease を延長する。他者に奪われていたら 409。"""

        with connection.cursor() as cursor:
            cursor.execute(
                f"""
                UPDATE {self.control_table}
                   SET LEASE_EXPIRES_AT = SYSTIMESTAMP
                           + NUMTODSINTERVAL(:lease_seconds, 'SECOND'),
                       UPDATED_AT = SYSTIMESTAMP
                 WHERE OPERATION_KEY = :operation_key
                   AND STATUS = 'RUNNING'
                   AND LEASE_OWNER = :lease_owner
                """,  # nosec B608 - 固定の識別子
                {
                    "lease_seconds": self._lease_seconds,
                    "operation_key": self.control_key,
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
                f"UPDATE {self.control_table} "  # nosec B608 - 固定の識別子と式
                "SET STATUS = 'IDLE', OPERATION_KIND = NULL, LEASE_OWNER = NULL, "
                "LEASE_EXPIRES_AT = NULL, LAST_ERROR_CODE = NULL, "
                f"SCHEMA_EPOCH = {epoch_sql}, UPDATED_AT = SYSTIMESTAMP "
                "WHERE OPERATION_KEY = :operation_key AND LEASE_OWNER = :lease_owner",
                {"operation_key": self.control_key, "lease_owner": owner},
            )
            finished = int(cursor.rowcount or 0) == 1
            connection.commit()
        if not finished:
            raise SystemSchemaBusyError()

    def _record_failure(self, owner: str, error_code: str) -> None:
        """失敗を記録して lease を返す（記録自体の失敗は握りつぶす）。"""

        with (
            suppress(Exception),
            self._connection_factory() as connection,
            connection.cursor() as cursor,
        ):
            cursor.execute(
                f"""
                UPDATE {self.control_table}
                   SET STATUS = 'FAILED', OPERATION_KIND = NULL, LEASE_OWNER = NULL,
                       LEASE_EXPIRES_AT = NULL, LAST_ERROR_CODE = :error_code,
                       UPDATED_AT = SYSTIMESTAMP
                 WHERE OPERATION_KEY = :operation_key AND LEASE_OWNER = :lease_owner
                """,  # nosec B608 - 固定の識別子
                {
                    "error_code": error_code[:64],
                    "operation_key": self.control_key,
                    "lease_owner": owner,
                },
            )
            connection.commit()

    # ---- 台帳 ---------------------------------------------------------------

    def _load_migrations(
        self,
        connection: Any,
        objects: Mapping[tuple[str, str], Any],
    ) -> dict[Any, str]:
        """適用済み migration のキー → checksum（台帳が無ければ空）。"""

        if (self.migration_table, "TABLE") not in objects:
            return {}
        key = self.migration_key_column
        with connection.cursor() as cursor:
            cursor.execute(
                f"SELECT {key}, CHECKSUM FROM {self.migration_table} "  # nosec B608 - 固定の識別子
                f"ORDER BY {key}"
            )
            return {self._migration_key(row[0]): str(row[1]) for row in cursor.fetchall()}

    def _merge_migration(
        self,
        cursor: Any,
        *,
        key: Any,
        description: str,
        checksum: str,
    ) -> None:
        """台帳に 1 migration を記録する（同じキーは checksum と適用日時を更新する）。"""

        column = self.migration_key_column
        bind = column.lower()
        cursor.execute(
            f"""
            MERGE INTO {self.migration_table} target
            USING (
                SELECT :{bind} AS {column},
                       :description AS DESCRIPTION,
                       :checksum AS CHECKSUM
                FROM DUAL
            ) source
            ON (target.{column} = source.{column})
            WHEN MATCHED THEN UPDATE SET
                target.DESCRIPTION = source.DESCRIPTION,
                target.CHECKSUM = source.CHECKSUM,
                target.APPLIED_AT = SYSTIMESTAMP
            WHEN NOT MATCHED THEN INSERT
                ({column}, DESCRIPTION, CHECKSUM, APPLIED_AT)
            VALUES
                (source.{column}, source.DESCRIPTION, source.CHECKSUM, SYSTIMESTAMP)
            """,  # nosec B608 - 固定の識別子
            {bind: key, "description": description, "checksum": checksum},
        )

    # ---- dictionary と DDL の部品 -------------------------------------------

    def _load_table_metadata(
        self,
        connection: Any,
        objects: Mapping[tuple[str, str], Any],
        *,
        owner: str = "",
    ) -> list[dict[str, Any]]:
        """管理対象テーブルの存在・推定行数・作成日時・統計日時（USER_TABLES の概算）。"""

        existing_names = [name for name in self.managed_tables if (name, "TABLE") in objects]
        metadata: dict[str, tuple[Any, Any]] = {}
        if existing_names:
            placeholders, binds = bind_list("table_name_", existing_names)
            with connection.cursor() as cursor:
                cursor.execute(
                    "SELECT TABLE_NAME, NUM_ROWS, LAST_ANALYZED FROM USER_TABLES "
                    f"WHERE TABLE_NAME IN ({placeholders})",  # nosec B608 - 固定の bind
                    binds,
                )
                metadata = {str(row[0]).upper(): (row[1], row[2]) for row in cursor.fetchall()}
        return [
            {
                "name": name,
                **self._table_identity_fields(name, owner),
                "exists": (name, "TABLE") in objects,
                "estimated_rows": (
                    int(metadata[name][0])
                    if name in metadata and metadata[name][0] is not None
                    else None
                ),
                "created_at": iso_timestamp(objects.get((name, "TABLE"))),
                "last_analyzed_at": iso_timestamp(metadata[name][1]) if name in metadata else None,
            }
            for name in self.managed_tables
        ]

    def _configure_ddl_lock_timeout(self, connection: Any) -> None:
        """schema 操作の session に上限付きの DDL のロック待ちを設定する。"""

        with connection.cursor() as cursor:
            cursor.execute(
                f"ALTER SESSION SET DDL_LOCK_TIMEOUT = {self._ddl_lock_timeout_seconds}"  # nosec B608 - 上限付きの整数
            )

    @staticmethod
    def _execute_statements(
        connection: Any,
        statements: Sequence[str],
        *,
        ignored_codes: frozenset[str] = IGNORED_APPLY_CODES,
    ) -> None:
        """DDL を順に実行する（既にある等の `ignored_codes` は読み飛ばす）。"""

        with connection.cursor() as cursor:
            for statement in statements:
                try:
                    cursor.execute(statement)
                except Exception as exc:
                    if oracle_error_code(exc) in ignored_codes:
                        continue
                    raise
            connection.commit()

    def _execute_drop(self, connection: Any, statement: str) -> int:
        """manifest にある object を 1 つ削除する（既に無いときは 0）。"""

        with connection.cursor() as cursor:
            try:
                cursor.execute(statement)
            except Exception as exc:
                if oracle_error_code(exc) in self.ignored_drop_codes:
                    return 0
                raise
            connection.commit()
        return 1


__all__ = [
    "CONTROL_KEY",
    "DEFAULT_LEASE_SECONDS",
    "IGNORED_APPLY_CODES",
    "IGNORED_DROP_CODES",
    "LOCK_RETRY_AFTER_SECONDS",
    "MAX_DDL_LOCK_TIMEOUT_SECONDS",
    "MIN_LEASE_SECONDS",
    "ORA_RESOURCE_BUSY",
    "RECREATE_CONFIRMATION_REQUIRED",
    "SCHEMA_OPERATION_FAILED",
    "SCHEMA_STATUS_UNAVAILABLE",
    "ConnectionFactory",
    "SystemSchemaActiveJobsError",
    "SystemSchemaBusyError",
    "SystemSchemaError",
    "SystemSchemaManagerBase",
    "SystemSchemaOperation",
    "SystemSchemaOperationKind",
    "SystemSchemaOperationStatus",
    "SystemSchemaStatus",
    "SystemTableOperationState",
    "SystemTablesInitializeRequest",
    "bind_list",
    "clamp_ddl_lock_timeout",
    "classify_system_schema_status",
    "idle_operation_state",
    "iso_timestamp",
    "oracle_error_code",
    "require_recreate_confirmation",
    "system_tables_status_error",
]
