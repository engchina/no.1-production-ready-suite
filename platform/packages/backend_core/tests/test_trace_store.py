"""共通の span の表（`PLATFORM_TRACE_SPANS`）の書き込み・読み出しと、MCP の trace
の受け渡し（#3226 の案 A）。

実 DB は使わない（DB-API の形の fake で SQL と bind を確かめる）。
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from pr_backend_core.chat_progress import chat_progress_details
from pr_backend_core.config.tracing import TracingSettings
from pr_backend_core.observability.mcp_trace import (
    incoming_mcp_traceparent,
    mcp_server_trace,
    mcp_trace_meta,
)
from pr_backend_core.observability.trace_store import (
    StoredTraceSpan,
    TraceSpanOwner,
    TraceSpanWriter,
    fetch_trace_spans,
    purge_trace_spans,
    trace_progress_events,
)
from pr_backend_core.observability.tracing import (
    SpanUpdate,
    configure_tracing,
    observed_span,
    operation_trace,
    shutdown_tracing,
)

OWNER = TraceSpanOwner(product="rag", operation_id="op-a", owner_user_uuid="user-a")


@pytest.fixture(autouse=True)
def reset_tracing() -> Iterator[None]:
    yield
    shutdown_tracing()


class FakeCursor:
    def __init__(self, db: FakeDb) -> None:
        self.db = db
        self.rowcount = 0

    def executemany(self, sql: str, rows: list[dict[str, Any]]) -> None:
        if self.db.fail_writes:
            self.db.fail_writes -= 1
            raise RuntimeError("ORA-03113")
        self.db.calls.append(("executemany", sql, [dict(row) for row in rows]))
        for row in rows:
            self.db.rows[(row["trace_id"], row["span_id"])] = dict(row)

    def execute(self, sql: str, binds: dict[str, Any]) -> None:
        self.db.calls.append(("execute", sql, dict(binds)))
        self.rowcount = self.db.purge_counts.pop(0) if self.db.purge_counts else 0

    def fetchall(self) -> list[tuple[Any, ...]]:
        return self.db.select_rows


class FakeConnection:
    def __init__(self, db: FakeDb) -> None:
        self.db = db

    def cursor(self) -> FakeCursor:
        return FakeCursor(self.db)

    def commit(self) -> None:
        self.db.commits += 1


class FakeDb:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str, Any]] = []
        self.rows: dict[tuple[str, str], dict[str, Any]] = {}
        self.commits = 0
        self.fail_writes = 0
        self.purge_counts: list[int] = []
        self.select_rows: list[tuple[Any, ...]] = []

    @contextmanager
    def connect(self) -> Iterator[FakeConnection]:
        yield FakeConnection(self)


def update(
    span_id: str,
    status: str = "running",
    *,
    name: str = "llm",
    root: bool = False,
    kind: str = "internal",
) -> SpanUpdate:
    now = datetime(2026, 1, 1, tzinfo=UTC)
    return SpanUpdate(
        span_id=span_id.rjust(16, "0"),
        trace_id="a" * 32,
        operation_name=name,
        status=status,  # type: ignore[arg-type]
        started_at=now,
        finished_at=None if status == "running" else now + timedelta(seconds=1),
        root=root,
        span_kind=kind,  # type: ignore[arg-type]
    )


def test_writer_coalesces_and_never_writes_step_details() -> None:
    """開始と終了は 1 行にまとめ、工程の詳細（検索文など）は表に入れない。"""
    db = FakeDb()
    writer = TraceSpanWriter(db.connect)
    configure_tracing(TracingSettings(), service_name="synthetic")
    with operation_trace("rag.answer", operation_id="op-a", listener=writer.listener(OWNER)):
        with observed_span("rag.engine.stage") as span:
            span.set_display(
                kind="answer_step",
                params={"name": "文書検索", "llm_calls": 1},
                details=chat_progress_details(fields=[("検索文", "synthetic-private-query")]),
            )
        with observed_span("mcp.request", kind="client"):
            pass
    writer.flush()
    rows = list(db.rows.values())
    assert {row["name"] for row in rows} == {"rag.answer", "rag.engine.stage", "mcp.request"}
    stage = next(row for row in rows if row["name"] == "rag.engine.stage")
    assert stage["status"] == "done" and stage["finished_at"] is not None
    assert stage["display_kind"] == "answer_step"
    assert json.loads(stage["display_params_json"]) == {"name": "文書検索", "llm_calls": 1}
    assert "synthetic-private-query" not in json.dumps(db.calls, default=str)
    root = next(row for row in rows if row["name"] == "rag.answer")
    assert root["is_root"] == 1 and root["owner_user_uuid"] == "user-a" and root["product"] == "rag"
    # 開始と終了が同じ batch に入れば、span ごとに 1 行だけ書く。
    assert sum(len(call[2]) for call in db.calls if call[0] == "executemany") == 3


def test_full_queue_drops_only_normal_span_starts_and_counts_them() -> None:
    db = FakeDb()
    writer = TraceSpanWriter(db.connect, queue_size=2)
    writer.submit(update("1"), OWNER)
    writer.submit(update("2"), OWNER)
    writer.submit(update("3"), OWNER)  # 満杯: 開始の知らせは捨てる
    writer.submit(update("4", "done"), OWNER)  # 終了は開始の知らせを押し出して入れる
    writer.submit(update("5", root=True), OWNER)  # 境界（root）は捨てない
    writer.submit(update("6", name="mcp.request", kind="client"), OWNER)
    writer.submit(update("7", name="mcp.server", kind="server"), OWNER)
    assert writer.dropped("a" * 32) == 2
    writer.flush()
    written = {row["span_id"].lstrip("0") for row in db.rows.values()}
    assert written == {"2", "4", "5", "6", "7"}


def test_failed_writes_retry_then_drop_normal_spans_but_keep_boundary_spans() -> None:
    db = FakeDb()
    clock = [0.0]
    writer = TraceSpanWriter(
        db.connect,
        retries=2,
        retry_backoff_seconds=0,
        boundary_retry_seconds=60,
        clock=lambda: clock[0],
    )
    writer.submit(update("1", "done"), OWNER)
    writer.submit(update("2", "done", root=True), OWNER)
    db.fail_writes = 2
    writer.flush()
    writer.flush()
    # 通常の span は 2 回失敗して捨て、数える。境界の span は再送を続ける。
    assert writer.dropped("a" * 32) == 1
    writer.flush()
    assert {row["span_id"].lstrip("0") for row in db.rows.values()} == {"2"}
    # 境界の span も、再送の期限を過ぎたら諦めて数える。
    writer.submit(update("3", "done", root=True), OWNER)
    db.fail_writes = 1
    writer.flush()
    clock[0] = 120
    db.fail_writes = 1
    writer.flush()
    assert writer.dropped("a" * 32) == 2


def test_retention_zero_never_deletes_and_positive_days_delete_old_spans() -> None:
    db = FakeDb()
    assert purge_trace_spans(db.connect, retention_days=0) == 0
    assert db.calls == []
    db.purge_counts = [3, 1]
    now = datetime(2026, 3, 1, tzinfo=UTC)
    assert purge_trace_spans(db.connect, retention_days=30, batch=3, now=now) == 4
    assert db.calls[0][0] == "execute" and "DELETE FROM PLATFORM_TRACE_SPANS" in db.calls[0][1]
    assert db.calls[0][2]["cutoff"] == datetime(2026, 1, 30)
    writer = TraceSpanWriter(db.connect)
    assert writer.retention_days == 0


def _row(span_id: str, parent: str | None, name: str, **extra: Any) -> tuple[Any, ...]:
    values = {
        "attempt": 0,
        "root": 0,
        "owner": "user-a",
        "status": "done",
        "finished": datetime(2026, 1, 1, 0, 0, 2),
        "kind": None,
        "params": None,
        "attrs": None,
        "operation": "op-a",
        **extra,
    }
    return (
        "a" * 32,
        span_id,
        parent,
        "rag",
        values["operation"],
        values["attempt"],
        values["root"],
        values["owner"],
        name,
        "internal",
        values["status"],
        datetime(2026, 1, 1, 0, 0, 1),
        values["finished"],
        values["kind"],
        values["params"],
        values["attrs"],
    )


def test_fetch_filters_by_owner_unless_system_admin() -> None:
    db = FakeDb()
    db.select_rows = [_row("1" * 16, None, "rag.answer", root=1, params='{"name":"x","bad":[1]}')]
    with db.connect() as connection:
        spans = fetch_trace_spans(connection, "a" * 32, viewer_user_uuid="user-a")
        fetch_trace_spans(connection, "a" * 32, viewer_user_uuid=None)
    own, admin = db.calls
    assert "OWNER_USER_UUID = :viewer" in own[1] and own[2]["viewer"] == "user-a"
    assert ":viewer" not in admin[1] and "viewer" not in admin[2]
    assert spans[0].root and spans[0].display_params == {"name": "x"}
    assert spans[0].started_at.tzinfo is not None


def _stored(span_id: str, parent: str | None, **extra: Any) -> StoredTraceSpan:
    base: dict[str, Any] = {
        "trace_id": "a" * 32,
        "span_id": span_id,
        "parent_span_id": parent,
        "product": "nl2sql",
        "operation_id": "job-a",
        "attempt": 0,
        "root": False,
        "owner_user_uuid": "user-a",
        "name": "nl2sql.generate_sql",
        "span_kind": "internal",
        "status": "done",
        "started_at": datetime(2026, 1, 1, tzinfo=UTC),
        "finished_at": datetime(2026, 1, 1, 0, 0, 1, tzinfo=UTC),
        "display_kind": None,
        "display_params": {},
        "attributes": {},
    }
    base.update(extra)
    return StoredTraceSpan(**base)


def test_progress_events_use_latest_attempt_and_product_record_as_final_state() -> None:
    spans = [
        _stored("1" * 16, None, root=True, attempt=0, name="nl2sql.execute"),
        _stored("2" * 16, "1" * 16, attempt=0),
        _stored("3" * 16, None, root=True, attempt=1, name="nl2sql.execute", status="running"),
        _stored("4" * 16, "3" * 16, attempt=1, display_kind="generate_sql", status="running"),
        _stored("5" * 16, "3" * 16, attempt=1, status="cancelled"),
    ]
    events = trace_progress_events(
        spans,
        target_id="job-a",
        terminal="failed",
        details={"4" * 16: {"fields": [{"label": "エンジン", "value": "synthetic"}]}},
    )
    steps = [event for event in events if event["type"] == "step"]
    # 前の試行の木は出さない。製品の記録が終端なら、表で実行中の span はその状態で閉じる。
    assert [event["step_id"][0] for event in steps] == ["3", "4", "5"]
    assert steps[1]["status"] == "failed" and "finished_at" in steps[1]
    assert (
        steps[1]["kind"] == "generate_sql"
        and steps[1]["details"]["fields"][0]["value"] == "synthetic"
    )
    assert steps[2]["status"] == "skipped" and steps[2]["cancelled"] is True
    assert steps[0]["root"] is True
    assert events[-1] == {**events[-1], "type": "terminal", "status": "failed"}


def test_mcp_trace_context_trusts_only_service_tokens() -> None:
    """サービストークンの traceparent だけを親にする。

    外部のものは新しい trace と span link にする。
    """
    exporter = InMemorySpanExporter()
    configure_tracing(TracingSettings(), service_name="synthetic", exporter=exporter)
    caller_trace = "b" * 32
    caller_span = "c" * 16
    incoming = f"00-{caller_trace}-{caller_span}-01"
    assert incoming_mcp_traceparent({"traceparent": incoming}, "invalid") == incoming
    assert incoming_mcp_traceparent({"traceparent": "invalid"}, incoming) == incoming
    db = FakeDb()
    writer = TraceSpanWriter(db.connect)
    owner = TraceSpanOwner(product="rag", operation_id="mcp-a", owner_user_uuid="user-a")
    with mcp_server_trace(
        operation_id="mcp-a", trusted=True, traceparent=incoming, listener=writer.listener(owner)
    ) as trusted:
        assert mcp_trace_meta()["traceparent"].split("-")[1] == caller_trace
    with mcp_server_trace(operation_id="mcp-b", trusted=False, traceparent=incoming) as external:
        pass
    writer.flush()
    assert trusted.telemetry_trace_id == caller_trace
    server_row = next(iter(db.rows.values()))
    assert server_row["parent_span_id"] == caller_span and server_row["span_kind"] == "server"
    assert server_row["is_root"] == 1
    assert external.telemetry_trace_id != caller_trace
    assert shutdown_tracing()
    exported = {span.context.trace_id: span for span in exporter.get_finished_spans()}
    linked = exported[int(external.telemetry_trace_id or "0", 16)]
    assert [f"{link.context.trace_id:032x}" for link in linked.links] == [caller_trace]
