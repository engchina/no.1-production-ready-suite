"""観測性 helper のテスト。"""

import logging
from typing import Any, cast

from pytest import LogCaptureFixture

from app.rag.observability import record_trace_span


def test_record_trace_span_keeps_low_cardinality_safe_attributes(
    caplog: LogCaptureFixture,
) -> None:
    """trace span は scalar attribute と error type だけを構造化ログへ出す。"""
    with caplog.at_level(logging.INFO, logger="app.trace"):
        event = record_trace_span(
            trace_id="trace-1",
            span_name="generation",
            outcome="error",
            seconds=0.12345,
            attributes={
                "model": "local",
                "context_chars": 120,
                "raw_query": "x" * 300,
                "nested": {"secret": "do-not-log"},
            },
            error=RuntimeError("secret invoice INV-SECRET"),
        )

    assert event.duration_ms == 123.45
    assert event.error_type == "RuntimeError"
    assert event.attributes["model"] == "local"
    assert event.attributes["context_chars"] == 120
    assert len(cast(str, event.attributes["raw_query"])) == 200
    assert "nested" not in event.attributes

    record = next(record for record in caplog.records if record.message == "rag_trace_span")
    trace_event = cast(Any, record).trace_event
    assert trace_event["error_type"] == "RuntimeError"
    assert "INV-SECRET" not in str(trace_event)
    assert "do-not-log" not in str(trace_event)
