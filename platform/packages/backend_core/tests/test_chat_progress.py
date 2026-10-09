"""チャットの処理の段階のイベントの記録・組み立て・配信（#1359）。"""

from __future__ import annotations

import json
from collections.abc import Callable
from datetime import UTC, datetime, timedelta

import pytest
from fastapi import FastAPI, Header, Query
from fastapi.testclient import TestClient

from pr_backend_core.chat_progress import (
    CHAT_PROGRESS_SSE_EVENT,
    CHAT_PROGRESS_SSE_HEARTBEAT_EVENT,
    ChatProgressPage,
    ChatProgressRecorder,
    ChatProgressStepEvent,
    ChatProgressTerminalEvent,
    chat_progress_cursor,
    chat_progress_page,
    chat_progress_sse_response,
    chat_progress_sse_stream,
    dump_chat_progress_events,
    fold_chat_progress_events,
    parse_chat_progress_events,
)

T0 = datetime(2026, 10, 9, 1, 0, 0, tzinfo=UTC)


def _clock() -> Callable[[], datetime]:
    ticks = iter(range(10_000))
    return lambda: T0 + timedelta(seconds=next(ticks))


def _recorder(**kwargs: object) -> ChatProgressRecorder:
    return ChatProgressRecorder("job-1", clock=_clock(), **kwargs)  # type: ignore[arg-type]


def _statuses(recorder: ChatProgressRecorder) -> dict[str, str]:
    return {step.step_id: step.status for step in recorder.steps()}


def test_recorder_numbers_events_and_orders_steps_by_declaration() -> None:
    sink: list[object] = []
    recorder = _recorder(sink=sink.append)
    recorder.declare("queue", "generate", "execute")
    recorder.start("queue")
    recorder.start("generate", exclusive=True)
    recorder.finish("generate", params={"engine": "select_ai"})
    recorder.start("execute")
    recorder.complete("done")

    events = recorder.events
    assert [event.seq for event in events] == list(range(1, len(events) + 1))
    assert sink == events
    assert [step.step_id for step in recorder.steps()] == ["queue", "generate", "execute"]
    assert _statuses(recorder) == {"queue": "done", "generate": "done", "execute": "done"}
    assert isinstance(events[-1], ChatProgressTerminalEvent)
    assert recorder.terminal == "done"
    generate = recorder.step("generate")
    assert generate is not None and generate.params == {"engine": "select_ai"}
    assert generate.started_at is not None and generate.finished_at is not None


def test_recorder_never_moves_a_step_backwards() -> None:
    recorder = _recorder()
    recorder.start("retrieve")
    recorder.finish("retrieve")
    before = recorder.last_seq
    # 回答フローが前の段階の工程へ戻っても（#1358）、完了した段階を実行中に戻さない。
    assert recorder.start("retrieve") is None
    assert recorder.last_seq == before
    assert _statuses(recorder) == {"retrieve": "done"}
    # 同じ段階の間の確定（完了 → 失敗）は受け付ける。
    assert recorder.finish("retrieve", "failed") is not None
    assert _statuses(recorder) == {"retrieve": "failed"}


def test_recorder_skips_unchanged_updates() -> None:
    recorder = _recorder()
    recorder.start("plan")
    seq = recorder.last_seq
    assert recorder.start("plan") is None
    assert recorder.update("plan") is None
    assert recorder.update("missing", detail="x") is None
    assert recorder.last_seq == seq
    assert recorder.update("plan", detail="2 回目") is not None


def test_complete_finishes_running_and_skips_pending() -> None:
    recorder = _recorder()
    recorder.declare("a", "b", "c")
    recorder.start("a")
    recorder.complete("failed")
    assert _statuses(recorder) == {"a": "failed", "b": "skipped", "c": "skipped"}
    skipped = recorder.step("b")
    assert skipped is not None and skipped.finished_at is None
    # 終端の後は記録しない。
    assert recorder.start("c") is None
    assert recorder.complete("done") is None


def test_cancel_marks_running_steps_skipped() -> None:
    recorder = _recorder()
    recorder.start("a")
    recorder.complete("cancelled")
    step = recorder.step("a")
    assert step is not None and step.status == "skipped" and step.finished_at is not None


def test_recorder_resumes_from_saved_events() -> None:
    first = _recorder()
    first.declare("a", "b")
    first.start("a")
    saved = parse_chat_progress_events(
        json.loads(json.dumps(dump_chat_progress_events(first.events)))
    )

    resumed = ChatProgressRecorder("job-1", events=saved, clock=_clock())
    assert resumed.last_seq == first.last_seq
    assert _statuses(resumed) == {"a": "running", "b": "pending"}
    event = resumed.start("b", exclusive=True)
    assert event is not None and event.seq == first.last_seq + 2  # a の完了の後


def test_new_attempt_resets_steps_and_keeps_numbering() -> None:
    recorder = _recorder()
    recorder.declare("a", "b")
    recorder.start("a")
    seq = recorder.last_seq
    recorder.new_attempt(2)
    assert recorder.steps() == []
    recorder.declare("a", "b")
    assert recorder.events[-1].seq == seq + 2
    assert recorder.events[-1].attempt == 2
    fold = fold_chat_progress_events(recorder.events)
    assert fold.attempt == 2
    assert {step.step_id: step.status for step in fold.step_list()} == {
        "a": "pending",
        "b": "pending",
    }
    # 古い試行の番号は受け付けない。
    recorder.new_attempt(1)
    assert recorder.attempt == 2


def test_recorder_with_larger_attempt_than_saved_starts_a_new_attempt() -> None:
    first = _recorder()
    first.start("a")
    resumed = ChatProgressRecorder("job-1", attempt=3, events=first.events, clock=_clock())
    assert resumed.steps() == []
    resumed.start("a")
    assert resumed.events[-1].attempt == 3


def _step(seq: int, step_id: str, status: str, *, attempt: int = 0) -> ChatProgressStepEvent:
    return ChatProgressStepEvent.model_validate(
        {
            "seq": seq,
            "target_id": "job-1",
            "attempt": attempt,
            "emitted_at": T0.isoformat(),
            "step_id": step_id,
            "status": status,
        }
    )


@pytest.mark.parametrize(
    ("events", "expected"),
    [
        pytest.param(
            [_step(2, "a", "done"), _step(1, "a", "running")], {"a": "done"}, id="out-of-order"
        ),
        pytest.param(
            [_step(1, "a", "running"), _step(1, "a", "running"), _step(2, "b", "running")],
            {"a": "running", "b": "running"},
            id="duplicate",
        ),
        pytest.param(
            [_step(1, "a", "done"), _step(2, "a", "running")], {"a": "done"}, id="backwards"
        ),
        pytest.param(
            [_step(1, "a", "done"), _step(2, "a", "running", attempt=1)],
            {"a": "running"},
            id="new-attempt",
        ),
        pytest.param(
            [_step(1, "a", "running", attempt=1), _step(2, "b", "running", attempt=0)],
            {"a": "running"},
            id="old-attempt",
        ),
    ],
)
def test_fold_drops_old_duplicate_and_backward_events(
    events: list[ChatProgressStepEvent], expected: dict[str, str]
) -> None:
    fold = fold_chat_progress_events(events)
    assert {step.step_id: step.status for step in fold.step_list()} == expected


def test_parse_drops_malformed_items() -> None:
    raw = [
        {"seq": 1, "target_id": "t", "emitted_at": T0.isoformat(), "type": "x"},
        {
            "seq": 0,
            "target_id": "t",
            "emitted_at": T0.isoformat(),
            "step_id": "a",
            "status": "running",
        },
        "broken",
        _step(1, "a", "running").model_dump(mode="json"),
    ]
    assert [event.seq for event in parse_chat_progress_events(raw)] == [1]
    assert parse_chat_progress_events({"steps": []}) == []


def test_page_returns_events_after_since() -> None:
    recorder = _recorder()
    recorder.declare("a", "b")
    recorder.start("a")
    page = chat_progress_page("job-1", recorder.events, since=2)
    assert [event.seq for event in page.events] == [3]
    assert page.last_seq == 3 and not page.terminal
    recorder.complete()
    assert recorder.page(since=99).terminal is True
    assert recorder.page(since=99).events == []


@pytest.mark.parametrize(
    ("since", "header", "expected"),
    [
        pytest.param(None, None, 0, id="none"),
        pytest.param(3, None, 3, id="since"),
        pytest.param(3, "7", 7, id="header-newer"),
        pytest.param(9, "7", 9, id="since-newer"),
        pytest.param(None, "x", 0, id="broken-header"),
        pytest.param(-1, None, 0, id="negative"),
    ],
)
def test_cursor_takes_the_newer_position(
    since: int | None, header: str | None, expected: int
) -> None:
    assert chat_progress_cursor(since=since, last_event_id=header) == expected


def _parse_sse(text: str) -> list[dict[str, str]]:
    frames = []
    for block in text.split("\n\n"):
        if not block.strip():
            continue
        frame: dict[str, str] = {}
        for line in block.split("\n"):
            key, _, value = line.partition(": ")
            frame[key] = value
        frames.append(frame)
    return frames


async def test_sse_stream_sends_new_events_heartbeats_and_closes_on_terminal() -> None:
    recorder = _recorder()
    recorder.declare("a")
    pages = 0

    async def fetch(cursor: int) -> ChatProgressPage:
        nonlocal pages
        pages += 1
        if pages == 3:
            recorder.start("a")
        if pages == 5:
            recorder.complete()
        return recorder.page(since=cursor)

    now = [0.0]

    async def sleep(seconds: float) -> None:
        now[0] += seconds

    chunks = [
        chunk
        async for chunk in chat_progress_sse_stream(
            fetch,
            since=0,
            poll_seconds=5,
            heartbeat_seconds=4,
            sleep=sleep,
            monotonic=lambda: now[0],
        )
    ]
    frames = _parse_sse("".join(chunks))
    assert frames[0] == {"retry": "2000"}
    progress = [frame for frame in frames if frame.get("event") == CHAT_PROGRESS_SSE_EVENT]
    assert [frame["id"] for frame in progress] == [str(event.seq) for event in recorder.events]
    assert json.loads(progress[-1]["data"])["type"] == "terminal"
    heartbeats = [f for f in frames if f.get("event") == CHAT_PROGRESS_SSE_HEARTBEAT_EVENT]
    assert heartbeats and json.loads(heartbeats[0]["data"]) == {"last_seq": 1}


async def test_sse_stream_resumes_after_cursor_and_ends_when_target_is_gone() -> None:
    recorder = _recorder()
    recorder.declare("a", "b", "c")
    calls = 0

    async def fetch(cursor: int) -> ChatProgressPage | None:
        nonlocal calls
        calls += 1
        return recorder.page(since=cursor) if calls == 1 else None

    async def sleep(_: float) -> None:
        return None

    chunks = [chunk async for chunk in chat_progress_sse_stream(fetch, since=1, sleep=sleep)]
    ids = [f["id"] for f in _parse_sse("".join(chunks)) if "id" in f]
    assert ids == ["2", "3"]


async def test_sse_stream_closes_after_max_seconds() -> None:
    recorder = _recorder()
    recorder.start("a")
    now = [0.0]

    async def fetch(cursor: int) -> ChatProgressPage:
        return recorder.page(since=cursor)

    async def sleep(seconds: float) -> None:
        now[0] += seconds

    chunks = [
        chunk
        async for chunk in chat_progress_sse_stream(
            fetch, poll_seconds=1, max_seconds=3, sleep=sleep, monotonic=lambda: now[0]
        )
    ]
    assert len([c for c in chunks if c.startswith("id: ")]) == 1


def _app(recorder: ChatProgressRecorder) -> FastAPI:
    app = FastAPI()

    async def fetch(cursor: int) -> ChatProgressPage:
        return recorder.page(since=cursor)

    @app.get("/progress/stream")
    async def stream(
        since: int | None = Query(default=None),
        last_event_id: str | None = Header(default=None, alias="Last-Event-ID"),
    ) -> object:
        return await chat_progress_sse_response(
            fetch, since=since, last_event_id=last_event_id, poll_seconds=0.01
        )

    return app


def test_sse_response_streams_and_returns_204_when_finished() -> None:
    recorder = _recorder()
    recorder.declare("a")
    recorder.start("a")
    recorder.complete()
    client = TestClient(_app(recorder))

    response = client.get("/progress/stream", params={"since": 1})
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    assert response.headers["x-accel-buffering"] == "no"
    ids = [f["id"] for f in _parse_sse(response.text) if "id" in f]
    assert ids == [str(seq) for seq in range(2, recorder.last_seq + 1)]

    # 張り直し（`Last-Event-ID` が終端の番号）は 204。ブラウザの EventSource は張り直しをやめる。
    finished = client.get("/progress/stream", headers={"Last-Event-ID": str(recorder.last_seq)})
    assert finished.status_code == 204
