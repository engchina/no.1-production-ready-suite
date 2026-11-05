"""製品・実行・試行の絞り込み後に読取上限を適用する。"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

import pytest
from pr_backend_core.observability.trace_store import TraceProduct, TraceSpanOwner, span_row
from pr_backend_core.observability.tracing import SpanUpdate

from app.features.nl2sql.models import TraceProgressData
from app.features.nl2sql.trace_spans import read_trace_progress

if TYPE_CHECKING:
    from trace_span_fake import TraceTable

pytest_plugins = ["trace_span_fake"]


def _row(
    index: int, *, product: TraceProduct = "nl2sql", operation: str = "job", attempt: int = 0
) -> dict[str, Any]:
    return span_row(
        SpanUpdate(
            trace_id="b" * 32,
            span_id=f"{index:016x}",
            operation_name="synthetic.step",
            status="done",
            started_at=datetime(2026, 1, 1, tzinfo=UTC),
        ),
        TraceSpanOwner(
            product=product, operation_id=operation, owner_user_uuid="owner", attempt=attempt
        ),
    )


def _read() -> TraceProgressData:
    return read_trace_progress(
        target_id="job",
        operation_id="job",
        trace_id="b" * 32,
        owner_user_uuid="owner",
        viewer_is_admin=False,
        terminal="done",
        dropped_spans=7,
    )


@pytest.mark.parametrize("count", [999, 1000, 1001])
def test_trace_read_limit_is_distinct_from_write_drops(trace_table: TraceTable, count: int) -> None:
    for index in range(count):
        row = _row(index)
        trace_table.db.rows[(row["trace_id"], row["span_id"])] = row
    data = _read()
    assert len([event for event in data.events if event.type == "step"]) == min(count, 1000)
    assert data.read_truncated is (count > 1000)
    assert data.dropped_spans == 7


def test_other_products_operations_and_old_attempts_do_not_consume_limit(
    trace_table: TraceTable,
) -> None:
    for index in range(1001):
        for row in (
            _row(index, product="agent"),
            _row(index + 2000, operation="other"),
            _row(index + 4000, attempt=1),
        ):
            trace_table.db.rows[(row["trace_id"], row["span_id"])] = row
    for index in (6000, 6001):
        row = _row(index, attempt=2)
        trace_table.db.rows[(row["trace_id"], row["span_id"])] = row
    data = _read()
    steps = [event for event in data.events if event.type == "step"]
    assert len(steps) == 2
    assert {event.step_id for event in steps} == {f"{index:016x}" for index in (6000, 6001)}
    assert data.read_truncated is False
