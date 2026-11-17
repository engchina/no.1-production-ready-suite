"""HNSW 索引の vector memory の見積もり（`pr_system_settings.vector_memory`。#5278）。"""

from __future__ import annotations

import re
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

import pytest

from pr_system_settings.vector_memory import (
    HnswIndexSpec,
    VectorMemoryEstimator,
    advisor_bytes,
    hnsw_indexes_from_statements,
    load_vector_memory,
)

_HNSW = """CREATE VECTOR INDEX demo_embedding_hnsw_idx
    ON demo_embeddings (embedding)
    ORGANIZATION INMEMORY NEIGHBOR GRAPH
    DISTANCE COSINE
    WITH TARGET ACCURACY 95
    PARAMETERS (TYPE HNSW, NEIGHBORS 32, EFCONSTRUCTION 200)"""


def test_hnsw_indexes_are_parsed_from_create_vector_index_only() -> None:
    specs = hnsw_indexes_from_statements(
        [
            _HNSW,
            "CREATE VECTOR INDEX DEMO_ACCURACY_IDX ON DEMO_NODES (EMBEDDING) "
            "ORGANIZATION INMEMORY NEIGHBOR GRAPH DISTANCE COSINE WITH TARGET ACCURACY 90",
            # IVF（NEIGHBOR PARTITIONS）と通常の索引は vector memory に載らない。
            "CREATE VECTOR INDEX DEMO_IVF_IDX ON DEMO_NODES (EMBEDDING) "
            "ORGANIZATION NEIGHBOR PARTITIONS",
            "CREATE INDEX DEMO_ITEMS_IDX ON DEMO_ITEMS (ITEM_ID)",
        ]
    )
    assert specs == (
        HnswIndexSpec("DEMO_EMBEDDING_HNSW_IDX", "DEMO_EMBEDDINGS", "EMBEDDING", 32, 95),
        HnswIndexSpec("DEMO_ACCURACY_IDX", "DEMO_NODES", "EMBEDDING", None, 90),
    )
    # neighbors と accuracy は同時に渡せないため、neighbors を渡す。
    assert specs[0].advisor_parameters() == '{"neighbors": 32}'
    assert specs[1].advisor_parameters() == '{"accuracy": 90}'
    assert HnswIndexSpec("X", "T", "C").advisor_parameters() is None


@pytest.mark.parametrize(
    ("response", "expected"),
    [
        ('{"index_type": "HNSW", "vector_memory_size": 59918628}', 59918628),
        ('{"result": {"suggested_size_bytes": "76396251"}}', 76396251),
        ("Suggested vector memory pool size: 53926765 Bytes", 53926765),
        ('{"index_type": "HNSW"}', None),
        ("", None),
    ],
    ids=["flat-json", "nested-json", "text", "no-size", "empty"],
)
def test_advisor_bytes_reads_the_suggested_size(response: str, expected: int | None) -> None:
    assert advisor_bytes(response) == expected


class _Var:
    def __init__(self) -> None:
        self.value: str | None = None

    def getvalue(self) -> str | None:
        return self.value


class _Cursor:
    def __init__(self, database: _Database) -> None:
        self.database = database
        self.rows: list[tuple[Any, ...]] = []

    def __enter__(self) -> _Cursor:
        return self

    def __exit__(self, *_args: object) -> None:
        return None

    def var(self, _type: type, _size: int) -> _Var:
        return _Var()

    def fetchone(self) -> tuple[Any, ...] | None:
        return self.rows[0] if self.rows else None

    def fetchall(self) -> list[tuple[Any, ...]]:
        return self.rows

    def execute(self, statement: str, params: dict[str, Any] | None = None) -> None:
        sql = re.sub(r"\s+", " ", statement).upper()
        params = params or {}
        self.database.statements.append(sql)
        if "V$VECTOR_MEMORY_POOL" in sql:
            if self.database.pool_error:
                raise RuntimeError(self.database.pool_error)
            self.rows = [self.database.pool]
        elif "V$VECTOR_INDEX" in sql:
            if self.database.index_error:
                raise RuntimeError(self.database.index_error)
            self.rows = list(self.database.index_usage.items())
        elif "INDEX_VECTOR_MEMORY_ADVISOR" in sql:
            self.database.advisor_params.append(dict(params))
            if self.database.advisor_error:
                raise RuntimeError(self.database.advisor_error)
            params["response"].value = self.database.advisor_response


class _Database:
    def __init__(self) -> None:
        self.pool: tuple[Any, ...] = (100_000_000, 40_000_000)
        self.index_usage: dict[str, int] = {}
        self.advisor_response = '{"vector_memory_size": 59918628}'
        self.pool_error: str | None = None
        self.index_error: str | None = None
        self.advisor_error: str | None = None
        self.statements: list[str] = []
        self.advisor_params: list[dict[str, Any]] = []
        self.connections = 0

    @contextmanager
    def connection(self) -> Iterator[_Connection]:
        self.connections += 1
        yield _Connection(self)


class _Connection:
    def __init__(self, database: _Database) -> None:
        self.database = database

    def cursor(self) -> _Cursor:
        return _Cursor(self.database)


_SPECS = hnsw_indexes_from_statements([_HNSW])


def test_load_vector_memory_reports_estimate_free_and_usage() -> None:
    database = _Database()
    database.index_usage = {"DEMO_EMBEDDING_HNSW_IDX": 12_000_000}
    with database.connection() as connection:
        data = load_vector_memory(connection, _SPECS)
    assert data["available"] is True
    assert data["pool_free_bytes"] == 60_000_000
    [index] = data["indexes"]
    assert index["estimated_bytes"] == 59918628
    assert index["loaded"] is True
    assert index["used_bytes"] == 12_000_000
    # 表と列を渡し、DDL の NEIGHBORS を advisor に渡す。DDL は実行しない。
    [params] = database.advisor_params
    assert (params["table_name"], params["column_name"]) == ("DEMO_EMBEDDINGS", "EMBEDDING")
    assert params["parameters"] == '{"neighbors": 32}'
    assert not any(
        sql.lstrip().startswith(("CREATE", "DROP", "ALTER")) for sql in database.statements
    )


def test_load_vector_memory_marks_unavailable_values_without_failing() -> None:
    """権限・ビューが無い環境では、取れない値を None にして ORA コードだけを返す。"""

    database = _Database()
    database.pool_error = "ORA-00942: table or view does not exist password=secret"
    database.index_error = "ORA-00942: table or view does not exist"
    database.advisor_error = "ORA-06550: PLS-00201 identifier 'DBMS_VECTOR' must be declared"
    with database.connection() as connection:
        data = load_vector_memory(connection, _SPECS)
    assert data["available"] is False
    assert data["error_code"] == "ORA-00942"
    assert data["pool_free_bytes"] is None
    [index] = data["indexes"]
    assert index["estimated_bytes"] is None
    assert index["loaded"] is None
    assert index["error_code"] == "ORA-06550"
    assert "secret" not in str(data)


def test_estimator_caches_and_returns_none_without_hnsw() -> None:
    database = _Database()
    now = [0.0]
    estimator = VectorMemoryEstimator(
        database.connection, _SPECS, cache_seconds=60, clock=lambda: now[0]
    )
    first = estimator.estimate()
    assert first is not None and first["available"] is True
    now[0] = 30.0
    assert estimator.estimate() is first
    assert database.connections == 1
    now[0] = 61.0
    estimator.estimate()
    assert database.connections == 2
    estimator.invalidate()
    estimator.estimate()
    assert database.connections == 3

    assert VectorMemoryEstimator(database.connection, ()).estimate() is None


def test_estimator_reports_unavailable_when_the_connection_fails() -> None:
    @contextmanager
    def broken() -> Iterator[Any]:
        raise RuntimeError("DPY-6005: cannot connect")
        yield  # pragma: no cover

    data = VectorMemoryEstimator(broken, _SPECS).estimate()
    assert data is not None
    assert data["available"] is False
    assert [index["name"] for index in data["indexes"]] == ["DEMO_EMBEDDING_HNSW_IDX"]
    assert data["indexes"][0]["estimated_bytes"] is None
