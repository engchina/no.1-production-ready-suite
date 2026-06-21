"""NL2SQL persistence store boundary.

local / CI は memory store を使い、production では Oracle 26ai 内の JSON CLOB
テーブルへ snapshot を保存する。service 層は Pydantic model の shape を保ったまま
store を差し替えられる。
"""

from __future__ import annotations

import json
import re
import threading
from collections.abc import Callable
from contextlib import AbstractContextManager
from typing import Any, Protocol

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

    1 row に NL2SQL state snapshot を保存する。DDL は idempotent に作成し、既存 table
    がある場合はそのまま利用する。
    """

    mode = "oracle"

    def __init__(
        self,
        *,
        connection_factory: Callable[[], AbstractContextManager[Any]],
        table_name: str,
    ) -> None:
        self._connection_factory = connection_factory
        self._table_name = self._validate_table_name(table_name)
        self._initialized = False
        self._lock = threading.RLock()

    @property
    def table_name(self) -> str:
        return self._table_name

    def load_snapshot(self) -> dict[str, Any] | None:
        with self._lock:
            self._ensure_table()
            with self._connection_factory() as conn, conn.cursor() as cursor:
                cursor.execute(
                    f"SELECT state_json FROM {self._table_name} WHERE state_key = :key",
                    {"key": _STATE_KEY},
                )
                row = cursor.fetchone()
            if not row:
                return None
            raw = _read_lob(row[0])
            return json.loads(raw) if raw else None

    def save_snapshot(self, snapshot: dict[str, Any]) -> None:
        payload = json.dumps(snapshot, ensure_ascii=False)
        with self._lock:
            self._ensure_table()
            with self._connection_factory() as conn, conn.cursor() as cursor:
                cursor.execute(
                    f"""
                    MERGE INTO {self._table_name} target
                    USING (
                        SELECT :state_key AS state_key, :state_json AS state_json
                        FROM dual
                    ) source
                    ON (target.state_key = source.state_key)
                    WHEN MATCHED THEN UPDATE SET
                        target.state_json = source.state_json,
                        target.updated_at = SYSTIMESTAMP
                    WHEN NOT MATCHED THEN INSERT (
                        state_key, state_json, updated_at
                    ) VALUES (
                        source.state_key, source.state_json, SYSTIMESTAMP
                    )
                    """,
                    {"state_key": _STATE_KEY, "state_json": payload},
                )
                conn.commit()

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
            try:
                cursor.execute(f"""
                    CREATE TABLE {self._table_name} (
                        state_key VARCHAR2(64) PRIMARY KEY,
                        state_json CLOB CHECK (state_json IS JSON),
                        updated_at TIMESTAMP WITH TIME ZONE DEFAULT SYSTIMESTAMP NOT NULL
                    )
                    """)
                conn.commit()
            except Exception as exc:
                if "ORA-00955" not in str(exc):
                    raise
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
        return str(read())
    return str(value)
