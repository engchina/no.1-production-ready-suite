"""有界trace読取の欠落と書込時欠落を、同じ合成DBで区別する。"""

from collections.abc import Iterator
from datetime import timedelta
from typing import Any

import pytest

from app.rag import trace_store
from app.rag.trace_store import TraceRecord
from tests.test_trace_api import AT, TRACE_ID, _span
from tests.trace_store_support import _COLUMNS, FakeTraceConnection, FakeTraceCursor, FakeTraceDb


class BoundedCursor(FakeTraceCursor):
    def execute(self, sql: str, binds: dict[str, Any]) -> None:
        super().execute(sql, binds)
        if "MAX(ATTEMPT)" in sql:
            rows = [r for r in self.db.rows.values() if r["trace_id"] == binds["trace_id"]]
            if binds.get("viewer") is not None:
                assert sql.count("OWNER_USER_UUID = :viewer") == 2
                rows = [r for r in rows if r["owner_user_uuid"] == binds["viewer"]]
            if binds.get("product") is not None:
                rows = [r for r in rows if r["product"] == binds["product"]]
            if binds.get("operation_id") is not None:
                rows = [r for r in rows if r["operation_id"] == binds["operation_id"]]
            latest = {}
            for row in rows:
                key = row["product"], row["operation_id"]
                latest[key] = max(latest.get(key, 0), row["attempt"])
            rows = [r for r in rows if r["attempt"] == latest[r["product"], r["operation_id"]]]
            rows.sort(key=lambda r: (r["started_at"], r["span_id"]))
            self._result = [tuple(r[col] for col in _COLUMNS) for r in rows]
        if "limit" in binds:
            self._result = self._result[: binds["limit"]]


class BoundedConnection(FakeTraceConnection):
    def cursor(self) -> BoundedCursor:
        return BoundedCursor(self.db)


@pytest.fixture
def db(monkeypatch: pytest.MonkeyPatch) -> FakeTraceDb:
    from contextlib import contextmanager

    db = FakeTraceDb()

    @contextmanager
    def connection() -> Iterator[BoundedConnection]:
        yield BoundedConnection(db)

    monkeypatch.setattr(trace_store, "_trace_connection", connection)
    return db


def add(
    db: FakeTraceDb,
    count: int,
    *,
    offset: int = 0,
    attempt: int = 0,
    owner: str = "synthetic-owner",
    product: str = "rag",
    operation: str = "synthetic-op",
) -> None:
    for index in range(offset, offset + count):
        sid = f"{index:016x}"
        _span(db, sid, owner=owner, operation_id=operation, root=index == offset)
        row = db.rows[TRACE_ID, sid]
        row.update(started_at=AT + timedelta(milliseconds=index), attempt=attempt, product=product)


def read(
    db: FakeTraceDb, *, dropped: int = 0, viewer: str | None = None
) -> trace_store.TraceSnapshot:
    return trace_store._read_snapshot(
        TraceRecord(trace_id=TRACE_ID, trace_dropped_spans=dropped),
        target_id="synthetic-run",
        terminal="done",
        lookup_operation_id=None,
        viewer=viewer,
    )


@pytest.mark.parametrize("count", [999, 1000, 1001])
def test_exact_limit_boundary_is_not_confused_with_writer_drops(
    db: FakeTraceDb, count: int
) -> None:
    add(db, count)
    snapshot = read(db, dropped=7)
    assert len([e for e in snapshot.events if e["type"] == "step"]) == min(count, 1000)
    assert snapshot.dropped_spans == 7
    assert getattr(snapshot, "read_truncated", False) is (count > 1000)
    assert all(b["limit"] <= 1001 for _, b in db.queries if "limit" in b)


def test_old_attempts_do_not_consume_latest_attempt_budget(db: FakeTraceDb) -> None:
    add(db, 1001)
    add(db, 2, offset=2000, attempt=1)
    snapshot = read(db)
    assert len([e for e in snapshot.events if e["type"] == "step"]) == 2
    assert not getattr(snapshot, "read_truncated", False)
    assert {e["step_id"] for e in snapshot.events if e["type"] == "step"} == {
        f"{i:016x}" for i in [2000, 2001]
    }


def test_owner_filter_precedes_latest_attempt_and_products_are_separate(db: FakeTraceDb) -> None:
    add(db, 1, attempt=1)
    add(db, 1001, offset=1000, attempt=99, owner="another-owner")
    add(db, 1, offset=3000, attempt=0, product="agent")
    snapshot = read(db, viewer="synthetic-owner")
    assert len([e for e in snapshot.events if e["type"] == "step"]) == 2
    assert not getattr(snapshot, "read_truncated", False)


@pytest.mark.parametrize("dropped", [0, 7])
def test_empty_span_snapshot_keeps_known_writer_drop(db: FakeTraceDb, dropped: int) -> None:
    snapshot = read(db, dropped=dropped)
    assert snapshot.events == []
    assert snapshot.dropped_spans == (dropped or None)
    assert not getattr(snapshot, "read_truncated", False)
