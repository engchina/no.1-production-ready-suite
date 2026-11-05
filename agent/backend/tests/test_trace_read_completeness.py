"""読み取り上限と最新試行の選択は、メモリでも保存表でも同じ境界にする。"""

from datetime import UTC, datetime

import pytest
from pr_backend_core.observability.trace_store import TraceSpanOwner
from pr_backend_core.observability.tracing import SpanUpdate

from app.features.agent.trace_spans import MemoryTraceSpans


def _submit(store: MemoryTraceSpans, index: int, *, attempt: int = 0, owner: str = "owner") -> None:
    store.submit(
        SpanUpdate(
            trace_id="a" * 32,
            span_id=f"{index:016x}",
            operation_name="synthetic.step",
            status="done",
            started_at=datetime(2026, 1, 1, tzinfo=UTC),
        ),
        TraceSpanOwner(product="agent", operation_id="run", owner_user_uuid=owner, attempt=attempt),
    )


@pytest.mark.parametrize("count", [999, 1000, 1001])
def test_memory_trace_reports_read_truncation(count: int) -> None:
    store = MemoryTraceSpans()
    for index in range(count):
        _submit(store, index)
    page = store.fetch_page("a" * 32, viewer_user_uuid="owner")
    assert len(page.spans) == min(count, 1000)
    assert page.truncated is (count > 1000)


def test_memory_owner_and_latest_attempt_are_selected_before_limit() -> None:
    store = MemoryTraceSpans()
    for index in range(1001):
        _submit(store, index, attempt=1)
        _submit(store, index + 2000, attempt=3, owner="other")
    _submit(store, 5000, attempt=2)
    _submit(store, 5001, attempt=2)
    page = store.fetch_page("a" * 32, viewer_user_uuid="owner", limit=2)
    assert [span.span_id for span in page.spans] == [f"{index:016x}" for index in (5000, 5001)]
    assert page.truncated is False
