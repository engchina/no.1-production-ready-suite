"""過去区間・未実行のspanに、時刻を変えず実判断の入出力を付ける。"""

import json
from datetime import UTC, datetime

import pytest

from pr_backend_core.config.tracing import TracingSettings
from pr_backend_core.observability.call_policy import call_content_policy
from pr_backend_core.observability.tracing import (
    SpanDisplay,
    configure_tracing,
    operation_trace,
    record_span,
    shutdown_tracing,
)


@pytest.mark.parametrize("enabled", [True, False])
def test_skipped_snapshot_retains_zero_duration_and_policy(enabled: bool) -> None:
    built: list[str] = []
    events = []

    def request() -> object:
        built.append("input")
        return {"reason": "synthetic", "authorization": "private-key-sentinel"}

    def response() -> object:
        built.append("output")
        return {"executed": False}

    configure_tracing(TracingSettings(), service_name="synthetic")
    try:
        with (
            call_content_policy(lambda: enabled),
            operation_trace("task", operation_id="synthetic", listener=events.append) as trace,
        ):
            at = datetime.now(UTC)
            record_span(
                "synthetic.skip",
                started_at=at,
                finished_at=at,
                status="skipped",
                display=SpanDisplay(kind="synthetic"),
                input_snapshot=request,
                output_snapshot=response,
                restricted_call=True,
            )
        [span] = [item for item in trace.spans if item.operation_name == "synthetic.skip"]
        [update] = [item for item in events if item.operation_name == "synthetic.skip"]
        assert span.status == update.status == "skipped"
        assert span.started_at == span.finished_at == at
        assert span.duration_ms == 0
        assert span.display and span.display.details and span.display.details.call
        call = span.display.details.call
        assert call.capture_enabled is enabled
        assert built == (["input", "output"] if enabled else [])
        if enabled:
            assert call.visibility == "system_admin"
            assert json.loads(call.output.text) == {"executed": False}
            assert "private-key-sentinel" not in call.model_dump_json()
        else:
            assert not call.input.text and not call.output.text
        assert span.attributes == {"pr.operation.status": "skipped"}
    finally:
        shutdown_tracing()
