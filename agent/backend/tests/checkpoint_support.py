"""テストの偽の Oracle の cursor が使う、Run の checkpoint（`AGENT_RUNTIME_CHECKPOINTS`）の SQL。

`runtime_checkpoint_store` の版つきの読み書き（#1916）を、行の JSON（`rows`）と版（`versions`）の
dict で再現する。版の列が無い DB（migration 015 の前）は `versioned=False`（版つきの SELECT が
ORA-00904 になり、確かめない MERGE で書く）。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class CheckpointResult:
    row: tuple[Any, ...] | None = None
    rowcount: int = 0


@dataclass
class CheckpointTable:
    rows: dict[str, str] = field(default_factory=dict)
    versions: dict[str, int] = field(default_factory=dict)
    writers: dict[str, str] = field(default_factory=dict)
    versioned: bool = True

    def execute(self, statement: str, params: dict[str, Any]) -> CheckpointResult | None:
        """checkpoint の SQL なら実行した結果、それ以外は None。"""
        normalized = " ".join(statement.upper().split())
        key = str(params.get("checkpoint_key"))
        if normalized.startswith("SELECT SNAPSHOT_JSON, VERSION_NO"):
            if not self.versioned:
                raise RuntimeError('ORA-00904: "VERSION_NO": invalid identifier')
            text = self.rows.get(key)
            if text is None:
                return CheckpointResult()
            return CheckpointResult(
                row=(text, self.versions.get(key, 0), self.writers.get(key)), rowcount=1
            )
        if normalized.startswith("SELECT SNAPSHOT_JSON"):
            text = self.rows.get(key)
            return CheckpointResult(row=(text,) if text is not None else None)
        if normalized.startswith("INSERT INTO AGENT_RUNTIME_CHECKPOINTS"):
            if key in self.rows:
                raise RuntimeError("ORA-00001: unique constraint violated")
            self._store(key, params, version=1)
            return CheckpointResult(rowcount=1)
        if normalized.startswith("UPDATE AGENT_RUNTIME_CHECKPOINTS"):
            if key not in self.rows or self.versions.get(key, 0) != params["expected_version"]:
                return CheckpointResult(rowcount=0)
            self._store(key, params, version=self.versions.get(key, 0) + 1)
            return CheckpointResult(rowcount=1)
        if normalized.startswith("MERGE INTO AGENT_RUNTIME_CHECKPOINTS"):
            self.rows[key] = str(params["snapshot_json"])
            return CheckpointResult(rowcount=1)
        return None

    def _store(self, key: str, params: dict[str, Any], *, version: int) -> None:
        self.rows[key] = str(params["snapshot_json"])
        self.versions[key] = version
        self.writers[key] = str(params.get("writer_id"))

    def connect(self) -> CheckpointConnection:
        """`connect_platform_oracle` の代わり（checkpoint の SQL だけを受け付ける）。"""
        return CheckpointConnection(self)


class CheckpointCursor:
    def __init__(self, table: CheckpointTable) -> None:
        self._table = table
        self._row: tuple[Any, ...] | None = None
        self.rowcount = 0

    def __enter__(self) -> CheckpointCursor:
        return self

    def __exit__(self, *_args: object) -> None:
        return None

    def setinputsizes(self, **_sizes: Any) -> None:
        return None

    def execute(self, statement: str, **params: Any) -> None:
        result = self._table.execute(statement, params)
        if result is None:  # pragma: no cover - 想定外の SQL を見逃さない
            raise AssertionError(statement)
        self._row, self.rowcount = result.row, result.rowcount

    def fetchone(self) -> tuple[Any, ...] | None:
        return self._row


class CheckpointConnection:
    def __init__(self, table: CheckpointTable) -> None:
        self._table = table

    def __enter__(self) -> CheckpointConnection:
        return self

    def __exit__(self, *_args: object) -> None:
        return None

    def cursor(self) -> CheckpointCursor:
        return CheckpointCursor(self._table)

    def commit(self) -> None:
        return None
