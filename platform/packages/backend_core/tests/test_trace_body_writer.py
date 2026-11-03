"""本文writerの有界受付・障害分離・停止を決定論的に確認する。"""

import threading

import pytest

from pr_backend_core.observability.trace_bodies import BodyFailure, BodyOwner, prepare_body
from pr_backend_core.observability.trace_body_writer import BodyWriteJob, TraceBodyWriter


def job(text: str = "合成本文") -> BodyWriteJob:
    return BodyWriteJob(
        prepare_body(text, part_characters=3), (BodyOwner("rag", "message", "record-a"),)
    )


@pytest.mark.parametrize("capacity", ["jobs", "bytes"])
def test_capacity_includes_inflight_body_and_rejection_does_not_call_db(capacity: str) -> None:
    entered, release = threading.Event(), threading.Event()
    stored: list[str] = []
    errors: list[BodyFailure] = []
    first = job()

    def persist(value: BodyWriteJob) -> None:
        entered.set()
        assert release.wait(3)
        stored.append("".join(part.text for part in value.body.parts()))

    writer = TraceBodyWriter(
        persist,
        lambda _, reason: errors.append(reason),
        max_jobs=1 if capacity == "jobs" else 3,
        max_bytes=first.body.reference.byte_count if capacity == "bytes" else 10000,
    )
    writer.start()
    try:
        assert writer.submit(first).accepted
        assert entered.wait(3)
        rejected = writer.submit(job())
        assert not rejected.accepted and rejected.failure_code == "queue_full"
        assert writer.pending == (1, first.body.reference.byte_count)
        assert not stored and not errors
    finally:
        release.set()
        assert writer.stop(timeout=3)
    assert stored == [first.body.text]
    assert writer.pending == (0, 0)


def test_large_body_keeps_tail_and_queue_order() -> None:
    stored: list[str] = []
    writer = TraceBodyWriter(
        lambda value: stored.append(value.body.text), lambda *_: None, max_jobs=3, max_bytes=1000000
    )
    values = [job("先頭" + "本文" * 40000 + "末尾"), job("次の本文")]
    writer.start()
    for value in values:
        assert writer.submit(value).accepted
    assert writer.stop(timeout=3)
    assert stored == [value.body.text for value in values]
    assert stored[0].endswith("末尾")


def test_failure_does_not_stop_later_body_or_escape_to_answer(
    caplog: pytest.LogCaptureFixture,
) -> None:
    stored: list[str] = []
    failures: list[BodyFailure] = []

    def persist(value: BodyWriteJob) -> None:
        if value.body.text == "first":
            raise RuntimeError("sensitive-exception-value")
        stored.append(value.body.text)

    def fail(_: BodyWriteJob, reason: BodyFailure) -> None:
        failures.append(reason)
        raise RuntimeError("sensitive-second-value")

    writer = TraceBodyWriter(persist, fail, max_jobs=3, max_bytes=1000)
    writer.start()
    assert writer.submit(job("first")).accepted
    assert writer.submit(job("second")).accepted
    assert writer.stop(timeout=3)
    assert stored == ["second"] and failures == ["write_failed"]
    assert "sensitive" not in caplog.text
    assert writer.pending == (0, 0)


def test_shutdown_timeout_never_flushes_db_on_caller_thread() -> None:
    entered, release = threading.Event(), threading.Event()

    def persist(_: BodyWriteJob) -> None:
        entered.set()
        assert release.wait(3)

    writer = TraceBodyWriter(persist, lambda *_: None, max_jobs=2, max_bytes=1000)
    assert writer.submit(job()).failure_code == "writer_stopped"
    writer.start()
    try:
        assert writer.submit(job()).accepted
        assert entered.wait(3)
        assert not writer.stop(timeout=0)
        assert writer.submit(job()).failure_code == "writer_stopped"
    finally:
        release.set()
        assert writer.stop(timeout=3)
    assert writer.pending == (0, 0)


def test_body_cannot_be_queued_without_real_owner() -> None:
    with pytest.raises(ValueError, match="persisted record owner"):
        BodyWriteJob(prepare_body("body", part_characters=3), ())
