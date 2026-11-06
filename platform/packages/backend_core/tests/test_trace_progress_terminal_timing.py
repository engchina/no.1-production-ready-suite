"""終了した実行の保存済みspanを別の閲覧時刻で読んでも計測値を作らない。"""

from dataclasses import asdict
from datetime import UTC, datetime, timedelta
from typing import Literal

import pytest

from pr_backend_core.chat_progress import ChatProgressStepEvent, parse_chat_progress_events
from pr_backend_core.observability.trace_store import StoredTraceSpan, trace_progress_events

AT = datetime(2026, 1, 1, tzinfo=UTC)
READ_TIMES = (AT + timedelta(seconds=10), AT + timedelta(seconds=100))
Terminal = Literal["done", "failed", "cancelled"]


def span(status: str, finished: datetime | None) -> StoredTraceSpan:
    return StoredTraceSpan(
        trace_id="0" * 32,
        span_id="1" * 16,
        parent_span_id="2" * 16,
        product="rag",
        operation_id="synthetic-run",
        attempt=1,
        root=False,
        owner_user_uuid="synthetic-user",
        name="synthetic.operation",
        span_kind="internal",
        status=status,
        started_at=AT,
        finished_at=finished,
        display_kind="synthetic_stage",
        display_params={},
        attributes={},
    )


@pytest.mark.parametrize("terminal", ["done", "failed", "cancelled"])
@pytest.mark.parametrize("finished", [None, AT + timedelta(seconds=2)], ids=["unknown", "measured"])
def test_terminal_running_span_keeps_only_its_recorded_finish(
    terminal: Terminal, finished: datetime | None
) -> None:
    stored = span("running", finished)
    before = asdict(stored)
    snapshots = [
        trace_progress_events(
            [stored], target_id="synthetic-run", terminal=terminal, emitted_at=now
        )
        for now in READ_TIMES
    ]
    expected_status = "skipped" if terminal == "cancelled" else terminal
    for snapshot, now in zip(snapshots, READ_TIMES, strict=True):
        step, end = snapshot
        assert step["status"] == expected_status
        assert step["started_at"] == AT.isoformat()
        assert step.get("finished_at") == (finished.isoformat() if finished else None)
        assert step.get("cancelled", False) == (terminal == "cancelled")
        assert step["parent_id"] == stored.parent_span_id
        assert step["step_id"] == stored.span_id
        assert end["status"] == terminal and end["type"] == "terminal"
        assert step["emitted_at"] == now.isoformat()
        parsed = parse_chat_progress_events(snapshot)
        assert len(parsed) == 2 and isinstance(parsed[0], ChatProgressStepEvent)
        assert parsed[0].finished_at == finished
    # 配信時刻だけは今回の読取時刻。工程の計測値と状態は同じ。
    assert {k: v for k, v in snapshots[0][0].items() if k != "emitted_at"} == {
        k: v for k, v in snapshots[1][0].items() if k != "emitted_at"
    }
    assert asdict(stored) == before


@pytest.mark.parametrize("status", ["done", "failed", "cancelled"])
@pytest.mark.parametrize("finished", [None, AT + timedelta(seconds=2)], ids=["unknown", "measured"])
def test_already_closed_span_keeps_status_and_optional_finish(
    status: str, finished: datetime | None
) -> None:
    stored = span(status, finished)
    for now in READ_TIMES:
        event = trace_progress_events(
            [stored], target_id="synthetic-run", terminal="done", emitted_at=now
        )[0]
        assert event["status"] == ("skipped" if status == "cancelled" else status)
        assert event.get("finished_at") == (finished.isoformat() if finished else None)


def test_active_execution_stays_running_without_manufactured_finish() -> None:
    stored = span("running", None)
    for now in READ_TIMES:
        events = trace_progress_events([stored], target_id="synthetic-run", emitted_at=now)
        assert len(events) == 1
        assert events[0]["status"] == "running"
        assert "finished_at" not in events[0]
