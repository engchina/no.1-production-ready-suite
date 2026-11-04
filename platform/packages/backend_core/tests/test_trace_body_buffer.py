"""遅い正本保存と複数実行の本文量を、一時ownerを作らず扱う。"""

from collections.abc import Iterator
from contextlib import contextmanager

from pr_backend_core.observability.trace_bodies import BodyOwner, prepare_body
from pr_backend_core.observability.trace_body_buffer import TraceBodyBuffer
from pr_backend_core.observability.trace_body_writer import BodyWriteJob, TraceBodyWriter


@contextmanager
def writer_jobs(*, max_bytes: int = 10000) -> Iterator[tuple[TraceBodyWriter, list[BodyWriteJob]]]:
    saved: list[BodyWriteJob] = []
    writer = TraceBodyWriter(saved.append, lambda *_: None, max_jobs=5, max_bytes=max_bytes)
    writer.start()
    try:
        yield writer, saved
    finally:
        assert writer.stop(timeout=3)


def test_buffer_holds_full_text_until_real_record_is_registered() -> None:
    buffer = TraceBodyBuffer(max_bodies=2, max_bytes=10000)
    session = buffer.session()
    body = prepare_body("合成本文" * 100, part_characters=17)
    assert session.capture(body).accepted
    assert buffer.pending == (1, body.reference.byte_count)
    owner = BodyOwner("rag", "history", "record-a")
    with writer_jobs() as (writer, saved):
        (submission,) = buffer.handoff_registered([body.reference], owner=owner, writer=writer)
        assert submission.accepted
        assert buffer.pending == (0, 0)
        session.close()
    assert saved[0].body.text == body.text and saved[0].owners == (owner,)


def test_capture_budget_is_shared_across_sessions_and_unsaved_body_is_released() -> None:
    body = prepare_body("合成本文", part_characters=3)
    buffer = TraceBodyBuffer(max_bodies=1, max_bytes=body.reference.byte_count)
    first, second = buffer.session(), buffer.session()
    assert first.capture(body).accepted
    other = prepare_body(body.text, part_characters=3)
    assert second.capture(other).failure_code == "queue_full"
    first.close()
    assert second.capture(other).accepted
    second.close()
    assert buffer.pending == (0, 0)
    assert first.capture(body).failure_code == "writer_stopped"


def test_handoff_queue_failure_is_reported_without_retaining_unbounded_body() -> None:
    buffer = TraceBodyBuffer(max_bodies=2, max_bytes=10000)
    session = buffer.session()
    body = prepare_body("合成本文", part_characters=3)
    assert session.capture(body).accepted
    with writer_jobs(max_bytes=1) as (writer, saved):
        (submission,) = buffer.handoff_registered(
            [body.reference], owner=BodyOwner("agent", "run", "record-a"), writer=writer
        )
        assert not submission.accepted and submission.failure_code == "queue_full"
    assert buffer.pending == (0, 0) and not saved
    session.close()


def test_unknown_old_references_are_not_assumed_lost_or_recreated() -> None:
    buffer = TraceBodyBuffer(max_bodies=2, max_bytes=10000)
    reference = prepare_body("old", part_characters=3).reference
    with writer_jobs() as (writer, saved):
        assert (
            buffer.handoff_registered(
                [reference], owner=BodyOwner("nl2sql", "history", "record-a"), writer=writer
            )
            == ()
        )
    assert not saved
