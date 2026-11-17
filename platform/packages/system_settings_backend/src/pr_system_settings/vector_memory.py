"""HNSW 索引の vector memory の見積もり（システムテーブルの画面。#5278）。

HNSW 索引（`ORGANIZATION INMEMORY NEIGHBOR GRAPH`）は vector memory（Vector Pool）に載る。
索引ごとに、見込みの必要量（`DBMS_VECTOR.INDEX_VECTOR_MEMORY_ADVISOR`）・今の使用量
（`V$VECTOR_INDEX`）と、Vector Pool の今の空き（`V$VECTOR_MEMORY_POOL`）を読む。

- 読むだけで DDL は実行しない。権限・ビューが無い環境（Oracle の版・ADB 以外）では、取れなかった
  値を None にし、ORA コードだけを返す（SQL・資格情報は返さない）。
- advisor の `RESPONSE_JSON` の形は公式文書に書かれていないため、JSON の中の大きさの数値か、
  「<数> Bytes」の文から読む（読めなければ None）。
- 共有パッケージは oracledb に依存しない（OUT の変数は DB-API の `cursor.var(str, size)`）。
"""

from __future__ import annotations

import json
import re
import threading
import time
from collections.abc import Callable, Iterable, Sequence
from contextlib import AbstractContextManager
from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel, Field

ConnectionFactory = Callable[[], AbstractContextManager[Any]]

# 状態の取り直し（操作中は 4 秒ごと）のたびに advisor を呼ばないよう、見積もりを保つ秒数。
VECTOR_MEMORY_CACHE_SECONDS = 60.0
# advisor の RESPONSE_JSON から読む長さ（要約の数値だけを使う）。
_ADVISOR_RESPONSE_CHARS = 4000

_IDENTIFIER = r"[A-Za-z][A-Za-z0-9_$#]*"
_HNSW_CREATE_PATTERN = re.compile(
    rf"^\s*CREATE\s+VECTOR\s+INDEX\s+({_IDENTIFIER})\s+ON\s+({_IDENTIFIER})\s*\(\s*({_IDENTIFIER})\s*\)",
    flags=re.IGNORECASE,
)
_HNSW_ORGANIZATION_PATTERN = re.compile(
    r"\bORGANIZATION\s+INMEMORY\s+NEIGHBOR\s+GRAPH\b", flags=re.IGNORECASE
)
_NEIGHBORS_PATTERN = re.compile(r"\bNEIGHBORS\s+(\d+)", flags=re.IGNORECASE)
_ACCURACY_PATTERN = re.compile(r"\bTARGET\s+ACCURACY\s+(\d+)", flags=re.IGNORECASE)
_BYTES_TEXT_PATTERN = re.compile(r"(\d+)\s*Bytes", flags=re.IGNORECASE)
_ORA_CODE_PATTERN = re.compile(r"ORA-\d{5}", flags=re.IGNORECASE)


@dataclass(frozen=True, slots=True)
class HnswIndexSpec:
    """DDL の正本の HNSW 索引（名前・表・列と、advisor に渡す構築の値）。"""

    name: str
    table_name: str
    column_name: str
    neighbors: int | None = None
    target_accuracy: int | None = None

    def advisor_parameters(self) -> str | None:
        """advisor の PARAMETER_JSON（neighbors と accuracy は同時に渡せない。neighbors が先）。"""

        if self.neighbors is not None:
            return json.dumps({"neighbors": self.neighbors})
        if self.target_accuracy is not None:
            return json.dumps({"accuracy": self.target_accuracy})
        return None


def hnsw_indexes_from_statements(statements: Iterable[str]) -> tuple[HnswIndexSpec, ...]:
    """DDL の文の並びから、HNSW 索引の `CREATE VECTOR INDEX` を拾う（形式の解析だけ）。"""

    specs: dict[str, HnswIndexSpec] = {}
    for statement in statements:
        match = _HNSW_CREATE_PATTERN.match(statement)
        if match is None or _HNSW_ORGANIZATION_PATTERN.search(statement) is None:
            continue
        neighbors = _NEIGHBORS_PATTERN.search(statement)
        accuracy = _ACCURACY_PATTERN.search(statement)
        name = match.group(1).upper()
        specs[name] = HnswIndexSpec(
            name=name,
            table_name=match.group(2).upper(),
            column_name=match.group(3).upper(),
            neighbors=int(neighbors.group(1)) if neighbors else None,
            target_accuracy=int(accuracy.group(1)) if accuracy else None,
        )
    return tuple(specs.values())


class VectorMemoryIndexData(BaseModel):
    """HNSW 索引 1 件の見積もり（取れない値は None）。"""

    name: str
    table_name: str
    column_name: str
    # 索引が vector memory に載っているか（V$VECTOR_INDEX に行がある）。読めなければ None。
    loaded: bool | None = None
    # 見込みの必要量（advisor の推奨の大きさ。今の行数と次元から求める）。
    estimated_bytes: int | None = None
    # 今の使用量（V$VECTOR_INDEX.USED_BYTES）。
    used_bytes: int | None = None
    # advisor を呼べなかったときの ORA コード。
    error_code: str | None = None


class VectorMemoryData(BaseModel):
    """vector memory の見積もり（HNSW 索引を持つ製品だけが返す）。"""

    # Vector Pool の大きさを読めたか（False なら画面は「取得できません」）。
    available: bool
    error_code: str | None = None
    pool_allocated_bytes: int | None = None
    pool_used_bytes: int | None = None
    pool_free_bytes: int | None = None
    indexes: list[VectorMemoryIndexData] = Field(default_factory=list)


def _error_code(exc: BaseException) -> str:
    match = _ORA_CODE_PATTERN.search(str(exc))
    return match.group(0).upper() if match else "UNAVAILABLE"


def _as_int(value: Any) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _read_text(value: Any) -> str:
    if value is None:
        return ""
    reader = getattr(value, "read", None)
    return str(reader() if callable(reader) else value)


def _bytes_from_json(value: Any) -> int | None:
    """JSON の中で、名前に size / memory / bytes を含む数値を探す（大きさの要約を読む）。"""

    if isinstance(value, dict):
        for key, item in value.items():
            lowered = str(key).lower()
            if any(part in lowered for part in ("size", "memory", "bytes")):
                number = _as_int(item) if not isinstance(item, (dict, list, bool)) else None
                if number is not None:
                    return number
        for item in value.values():
            found = _bytes_from_json(item)
            if found is not None:
                return found
    if isinstance(value, list):
        for item in value:
            found = _bytes_from_json(item)
            if found is not None:
                return found
    return None


def advisor_bytes(response: str) -> int | None:
    """advisor の RESPONSE_JSON から推奨の大きさ（バイト）を読む（読めなければ None）。"""

    text = response.strip()
    if not text:
        return None
    try:
        parsed = json.loads(text)
    except ValueError:
        parsed = None
    if parsed is not None:
        found = _bytes_from_json(parsed)
        if found is not None:
            return found
    match = _BYTES_TEXT_PATTERN.search(text)
    return int(match.group(1)) if match else None


def _load_pool(connection: Any) -> tuple[int, int]:
    with connection.cursor() as cursor:
        cursor.execute("SELECT SUM(ALLOC_BYTES), SUM(USED_BYTES) FROM V$VECTOR_MEMORY_POOL")
        row = cursor.fetchone()
    allocated = _as_int(row[0]) if row else None
    used = _as_int(row[1]) if row else None
    return allocated or 0, used or 0


def _load_index_usage(connection: Any, names: Sequence[str]) -> dict[str, int]:
    binds = {f"index_name_{position}": name for position, name in enumerate(names)}
    placeholders = ", ".join(f":{key}" for key in binds)
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT INDEX_NAME, SUM(USED_BYTES) FROM V$VECTOR_INDEX "
            "WHERE OWNER = SYS_CONTEXT('USERENV', 'CURRENT_SCHEMA') "
            f"AND INDEX_NAME IN ({placeholders}) "  # nosec B608 - 固定の bind
            "GROUP BY INDEX_NAME",
            binds,
        )
        return {str(row[0]).upper(): _as_int(row[1]) or 0 for row in cursor.fetchall()}


def _advise(connection: Any, spec: HnswIndexSpec) -> int | None:
    parameters = spec.advisor_parameters()
    parameter_arg = "PARAMETER_JSON => :parameters, " if parameters is not None else ""
    with connection.cursor() as cursor:
        response = cursor.var(str, _ADVISOR_RESPONSE_CHARS)
        binds: dict[str, Any] = {
            "table_name": spec.table_name,
            "column_name": spec.column_name,
            "response": response,
        }
        if parameters is not None:
            binds["parameters"] = parameters
        cursor.execute(
            "DECLARE response_json CLOB; "
            "BEGIN DBMS_VECTOR.INDEX_VECTOR_MEMORY_ADVISOR("
            "TABLE_OWNER => SYS_CONTEXT('USERENV', 'CURRENT_SCHEMA'), "
            "TABLE_NAME => :table_name, COLUMN_NAME => :column_name, INDEX_TYPE => 'HNSW', "
            f"{parameter_arg}RESPONSE_JSON => response_json); "
            f":response := DBMS_LOB.SUBSTR(response_json, {_ADVISOR_RESPONSE_CHARS}, 1); END;",
            binds,
        )
        return advisor_bytes(_read_text(response.getvalue()))


def load_vector_memory(connection: Any, specs: Sequence[HnswIndexSpec]) -> dict[str, Any]:
    """HNSW 索引ごとの見込みの必要量と、Vector Pool の今の空きを読む（失敗は値を None にする）。"""

    data: dict[str, Any] = {"available": True, "error_code": None}
    try:
        allocated, used = _load_pool(connection)
    except Exception as exc:
        data.update(available=False, error_code=_error_code(exc))
    else:
        data.update(
            pool_allocated_bytes=allocated,
            pool_used_bytes=used,
            pool_free_bytes=max(0, allocated - used),
        )
    usage: dict[str, int] | None
    try:
        usage = _load_index_usage(connection, [spec.name for spec in specs]) if specs else {}
    except Exception:
        usage = None
    indexes: list[dict[str, Any]] = []
    for spec in specs:
        item: dict[str, Any] = {
            "name": spec.name,
            "table_name": spec.table_name,
            "column_name": spec.column_name,
            "loaded": None if usage is None else spec.name in usage,
            "used_bytes": None if usage is None else usage.get(spec.name),
        }
        try:
            item["estimated_bytes"] = _advise(connection, spec)
        except Exception as exc:
            item["estimated_bytes"] = None
            item["error_code"] = _error_code(exc)
        indexes.append(item)
    data["indexes"] = indexes
    return VectorMemoryData.model_validate(data).model_dump()


class VectorMemoryEstimator:
    """見積もりを一定の秒数だけ保つ（状態の polling のたびに advisor を呼ばない）。"""

    def __init__(
        self,
        connection_factory: ConnectionFactory,
        specs: Sequence[HnswIndexSpec],
        *,
        cache_seconds: float = VECTOR_MEMORY_CACHE_SECONDS,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._connection_factory = connection_factory
        self._specs = tuple(specs)
        self._cache_seconds = cache_seconds
        self._clock = clock
        self._lock = threading.Lock()
        self._cached: dict[str, Any] | None = None
        self._cached_at = 0.0

    def invalidate(self) -> None:
        with self._lock:
            self._cached = None

    def estimate(self) -> dict[str, Any] | None:
        """見積もり（HNSW 索引が無ければ None。接続できなければ「取得できません」）。"""

        if not self._specs:
            return None
        with self._lock:
            now = self._clock()
            if self._cached is not None and now - self._cached_at < self._cache_seconds:
                return self._cached
            try:
                with self._connection_factory() as connection:
                    data = load_vector_memory(connection, self._specs)
            except Exception as exc:
                data = VectorMemoryData(
                    available=False,
                    error_code=_error_code(exc),
                    indexes=[
                        VectorMemoryIndexData(
                            name=spec.name,
                            table_name=spec.table_name,
                            column_name=spec.column_name,
                        )
                        for spec in self._specs
                    ],
                ).model_dump()
            self._cached = data
            self._cached_at = now
            return data


__all__ = [
    "VECTOR_MEMORY_CACHE_SECONDS",
    "HnswIndexSpec",
    "VectorMemoryData",
    "VectorMemoryEstimator",
    "VectorMemoryIndexData",
    "advisor_bytes",
    "hnsw_indexes_from_statements",
    "load_vector_memory",
]
