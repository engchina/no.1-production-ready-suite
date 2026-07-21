"""NL2SQL persistence store boundary.

local / CI は memory store を使い、production では Oracle 26ai 内の JSON CLOB
テーブルへ snapshot を保存する。service 層は Pydantic model の shape を保ったまま
store を差し替えられる。
"""

from __future__ import annotations

import hashlib
import json
import re
import threading
from collections.abc import Callable, Mapping
from contextlib import AbstractContextManager
from importlib import import_module
from typing import Any, Protocol, cast

_STATE_KEY = "default"
_TABLE_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_$#]*$")


class Nl2SqlStore(Protocol):
    """NL2SQL state snapshot store contract."""

    mode: str

    def load_snapshot(self) -> dict[str, Any] | None:
        """Persisted snapshot を返す。未保存なら None。"""

    def save_snapshot(self, snapshot: dict[str, Any]) -> None:
        """最新 snapshot を保存する。"""

    def check(self) -> tuple[bool, str]:
        """診断用に store 可用性を返す。"""


class MemoryNl2SqlStore:
    """Process-local store used for deterministic local / CI runtime."""

    mode = "memory"

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._snapshot: dict[str, Any] | None = None

    def load_snapshot(self) -> dict[str, Any] | None:
        with self._lock:
            return json.loads(json.dumps(self._snapshot)) if self._snapshot else None

    def save_snapshot(self, snapshot: dict[str, Any]) -> None:
        with self._lock:
            self._snapshot = json.loads(json.dumps(snapshot, ensure_ascii=False))

    def check(self) -> tuple[bool, str]:
        return True, "memory store を使用しています。"


class OracleJsonNl2SqlStore:
    """Oracle JSON CLOB table backed store.

    旧互換/移行期間だけ 1 row に NL2SQL state snapshot を保存する。DDL は versioned
    migration の責務であり、この store は既存 table を bounded query で確認するだけ。
    """

    mode = "oracle"

    def __init__(
        self,
        *,
        connection_factory: Callable[[], AbstractContextManager[Any]],
        table_name: str,
        migration_mirror_enabled: bool = False,
    ) -> None:
        self._connection_factory = connection_factory
        self._table_name = self._validate_table_name(table_name)
        self._initialized = False
        self._lock = threading.RLock()
        self._migration_mirror_enabled = migration_mirror_enabled

    @property
    def table_name(self) -> str:
        return self._table_name

    def load_snapshot(self) -> dict[str, Any] | None:
        with self._lock:
            self._ensure_table()
            with self._connection_factory() as conn, conn.cursor() as cursor:
                # Safe: table name is validated by _validate_table_name.
                select_sql = f"SELECT state_json FROM {self._table_name} WHERE state_key = :key"  # nosec B608
                cursor.execute(
                    select_sql,
                    {"key": _STATE_KEY},
                )
                row = cursor.fetchone()
            if not row:
                return None
            value = row[0]
            if isinstance(value, Mapping):
                return cast(dict[str, Any], json.loads(json.dumps(value, ensure_ascii=False)))
            raw = _read_lob(value)
            return cast(dict[str, Any], json.loads(raw)) if raw else None

    def save_snapshot(self, snapshot: dict[str, Any]) -> None:
        payload = json.dumps(snapshot, ensure_ascii=False)
        with self._lock:
            self._ensure_table()
            with self._connection_factory() as conn, conn.cursor() as cursor:
                # Safe: table name is validated by _validate_table_name; values use binds.
                merge_sql = (
                    f"MERGE INTO {self._table_name} target "  # nosec B608
                    "USING ("
                    "  SELECT :state_key AS state_key, :state_json AS state_json"
                    "  FROM dual"
                    ") source "
                    "ON (target.state_key = source.state_key) "
                    "WHEN MATCHED THEN UPDATE SET "
                    "  target.state_json = source.state_json, "
                    "  target.updated_at = SYSTIMESTAMP "
                    "WHEN NOT MATCHED THEN INSERT ("
                    "  state_key, state_json, updated_at"
                    ") VALUES ("
                    "  source.state_key, source.state_json, SYSTIMESTAMP"
                    ")"
                )
                try:
                    _set_json_clob_input_size(cursor)
                    cursor.execute(
                        merge_sql,
                        {"state_key": _STATE_KEY, "state_json": payload},
                    )
                    if self._migration_mirror_enabled:
                        cursor.execute(
                            "INSERT INTO NL2SQL_MIGRATION_OUTBOX (SNAPSHOT_VERSION, "
                            "SNAPSHOT_CHECKSUM, STATE_JSON) VALUES "
                            "(NL2SQL_MIGRATION_SNAPSHOT_SEQ.NEXTVAL, :checksum, :state_json)",
                            {
                                "checksum": hashlib.sha256(payload.encode("utf-8")).hexdigest(),
                                "state_json": payload,
                            },
                        )
                    conn.commit()
                except Exception:
                    rollback = getattr(conn, "rollback", None)
                    if callable(rollback):
                        rollback()
                    raise

    def check(self) -> tuple[bool, str]:
        try:
            self._ensure_table()
        except Exception as exc:  # pragma: no cover - depends on live Oracle
            return False, f"Oracle store の確認に失敗しました: {exc}"
        return True, f"Oracle store table {self._table_name} を使用できます。"

    def _ensure_table(self) -> None:
        if self._initialized:
            return
        with self._connection_factory() as conn, conn.cursor() as cursor:
            # Runtime/request path では DDL を実行しない。旧 snapshot table も versioned
            # migration で準備済みであることを bounded scalar query だけで確認する。
            cursor.execute(
                f"SELECT 1 FROM {self._table_name} WHERE 1 = 0"  # nosec B608
            )
            self._initialized = True

    def _validate_table_name(self, table_name: str) -> str:
        normalized = table_name.strip().upper()
        if not _TABLE_NAME.fullmatch(normalized):
            raise ValueError("NL2SQL Oracle store table name が不正です。")
        return normalized


def _read_lob(value: Any) -> str:
    if value is None:
        return ""
    read = getattr(value, "read", None)
    if callable(read):
        raw = read()
        if isinstance(raw, bytes):
            return raw.decode("utf-8")
        return str(raw)
    if isinstance(value, bytes):
        return value.decode("utf-8")
    return str(value)


def _set_json_clob_input_size(cursor: Any) -> None:
    """Bind large JSON snapshots as CLOB to avoid ORA-01461 on Oracle."""
    set_input_sizes = getattr(cursor, "setinputsizes", None)
    if not callable(set_input_sizes):
        return
    try:
        oracledb = import_module("oracledb")
        db_type_clob = oracledb.DB_TYPE_CLOB
    except Exception:  # pragma: no cover - defensive when oracle driver is absent
        return
    set_input_sizes(state_json=db_type_clob)
