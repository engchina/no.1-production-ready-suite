"""正本を保存した取引だけが全文を登録し、記録障害は正本を取り消さない。"""

import copy
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

import pytest
from test_trace_bodies import OwnerCursor

from pr_backend_core.config.tracing import TracingSettings
from pr_backend_core.observability.call_details import call_content
from pr_backend_core.observability.trace_bodies import BodyOwner
from pr_backend_core.observability.trace_body_persistence import TraceBodyPersistence


class TransactionCursor(OwnerCursor):
    def __init__(self) -> None:
        super().__init__()
        self.primary_saved = False
        self.fail_attach = False
        self.savepoint: tuple[Any, ...] | None = None
        self.rollback_count = 0

    def __enter__(self) -> "TransactionCursor":
        return self

    def __exit__(self, *_: Any) -> None:
        pass

    def execute(self, sql: str, binds: dict[str, Any] | None = None) -> None:
        if sql == "SAVEPOINT trace_body_registration":
            self.savepoint = copy.deepcopy(
                (self.manifests, self.owners, self.parts, self.part_rows, self.failures)
            )
        elif sql == "ROLLBACK TO SAVEPOINT trace_body_registration":
            assert self.savepoint is not None
            self.manifests, self.owners, self.parts, self.part_rows, self.failures = self.savepoint
            self.rollback_count += 1
        elif self.fail_attach and "MERGE INTO PLATFORM_TRACE_BODY_OWNERS" in sql:
            raise RuntimeError("synthetic storage failure")
        else:
            super().execute(sql, binds or {})


class Database:
    def __init__(self) -> None:
        self.data = TransactionCursor()
        self.lock = threading.RLock()
        self.commits = 0
        self.rollbacks = 0

    @contextmanager
    def connection(self) -> Iterator["Database"]:
        # workerは正本保存取引の終了後に登録済み所属を読む。
        with self.lock:
            yield self

    def cursor(self) -> TransactionCursor:
        return self.data

    def commit(self) -> None:
        self.commits += 1

    def rollback(self) -> None:
        self.rollbacks += 1


def manager(database: Database, **settings: Any) -> TraceBodyPersistence:
    return TraceBodyPersistence(
        database.connection,
        clob_type=object(),
        settings=TracingSettings(**settings),
    )


def test_full_capture_is_handed_to_writer_after_primary_save() -> None:
    database = Database()
    persistence = manager(database)
    text = "synthetic input " * 10000 + "final-condition"
    persistence.start()
    try:
        with persistence.capture(), database.connection():
            content = call_content(text)
            assert content.body_reference is not None and content.preview_only
            database.data.primary_saved = True
            assert not persistence.register(
                database.data,
                BodyOwner("rag", "history", "record-a"),
                {"span-a": {"call": {"input": content.model_dump()}}},
            )
            assert database.data.manifests[content.body_reference.body_id][5] == "pending"
            assert not database.data.parts
            database.commit()
        assert persistence.stop()
        assert database.data.primary_saved
        assert database.data.manifests[content.body_reference.body_id][5] == "complete"
        assert "".join(database.data.parts[content.body_reference.body_id]) == text
    finally:
        persistence.stop()


def test_transaction_retry_keeps_full_text_until_commit_confirmation() -> None:
    database = Database()
    persistence = manager(database)
    first_done = threading.Event()
    persist = persistence.writer._persist

    def observe_attempt(job: Any) -> None:
        persist(job)
        first_done.set()

    persistence.writer._persist = observe_attempt
    persistence.start()
    try:
        with persistence.capture():
            content = call_content("synthetic input " * 10000 + "final-condition")
            assert content.body_reference is not None
            details = {"span-a": {"call": {"input": content.model_dump()}}}
            owner = BodyOwner("rag", "history", "record-a")
            with database.connection():
                assert not persistence.register(database.data, owner, details)
                # 最初の正本取引がrollbackした後に、workerが到着する。
                database.data.manifests.clear()
                database.data.owners.clear()
            assert first_done.wait(3)
            assert not database.data.parts
            assert persistence.buffer.pending[0] == 1
            with database.connection():
                assert not persistence.register(database.data, owner, details)
                database.data.primary_saved = True
                database.commit()
            persistence.confirm_saved(details)
            assert persistence.buffer.pending == (0, 0)
        assert persistence.stop()
        assert database.data.manifests[content.body_reference.body_id][5] == "complete"
        assert "".join(database.data.parts[content.body_reference.body_id]).endswith(
            "final-condition"
        )
    finally:
        persistence.stop()


@pytest.mark.parametrize(
    ("settings", "started", "reason"),
    [
        ({}, False, "writer_stopped"),
        ({"trace_body_queue_bytes": 1}, True, "queue_full"),
        ({"trace_body_buffer_bytes": 1}, True, "queue_full"),
    ],
)
def test_admission_failure_preserves_primary_and_records_actual_reason(
    settings: dict[str, Any], started: bool, reason: str
) -> None:
    database = Database()
    persistence = manager(database, **settings)
    if started:
        persistence.start()
    try:
        with persistence.capture(), database.connection():
            content = call_content("synthetic input " * 10000)
            assert content.body_reference is not None
            body_id = content.body_reference.body_id
            database.data.primary_saved = True
            faults = persistence.register(
                database.data,
                BodyOwner("agent", "run", "record-a"),
                {"span-a": {"call": {"output": content.model_dump()}}},
            )
            assert faults == {body_id: reason}
            assert database.data.manifests[body_id][5] == "incomplete"
            assert database.data.failures[body_id] == reason
            assert database.data.primary_saved
        assert persistence.buffer.pending == (0, 0)
    finally:
        assert persistence.stop()


def test_owner_attach_failure_rolls_back_only_trace_registration() -> None:
    database = Database()
    persistence = manager(database)
    database.data.fail_attach = True
    with persistence.capture():
        content = call_content("synthetic input " * 10000)
        assert content.body_reference is not None
        database.data.primary_saved = True
        assert persistence.register(
            database.data,
            BodyOwner("nl2sql", "job", "record-a"),
            {"span-a": {"call": {"input": content.model_dump()}}},
        ) == {content.body_reference.body_id: "storage_unavailable"}
        assert database.data.primary_saved and database.data.rollback_count == 1
        assert not database.data.manifests and not database.data.owners
    assert persistence.buffer.pending == (0, 0)


def test_short_or_missing_call_content_does_not_touch_database() -> None:
    database = Database()
    persistence = manager(database)
    assert not persistence.register(
        database.data,
        BodyOwner("rag", "message", "record-a"),
        {"span-a": {"call": {"input": call_content("small").model_dump()}}},
    )
    assert database.data.savepoint is None


def test_background_write_failure_rolls_back_and_marks_record_incomplete(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database = Database()
    persistence = manager(database)

    def fail(*_: Any, **__: Any) -> None:
        raise RuntimeError("synthetic storage failure")

    monkeypatch.setattr(
        "pr_backend_core.observability.trace_body_persistence.write_registered_body", fail
    )
    persistence.start()
    try:
        with persistence.capture(), database.connection():
            content = call_content("synthetic input " * 10000)
            assert content.body_reference is not None
            database.data.primary_saved = True
            assert not persistence.register(
                database.data,
                BodyOwner("rag", "history", "record-a"),
                {"span-a": {"call": {"input": content.model_dump()}}},
            )
            database.commit()
        assert persistence.stop()
        body_id = content.body_reference.body_id
        assert database.rollbacks == 1 and database.data.primary_saved
        assert database.data.failures[body_id] == "write_failed"
        assert database.data.manifests[body_id][5] == "incomplete"
    finally:
        persistence.stop()


def test_nested_capture_keeps_failed_pipeline_body_until_outer_error_record_save() -> None:
    database = Database()
    persistence = manager(database)
    persistence.start()
    try:
        with persistence.capture():
            try:
                with persistence.capture():
                    content = call_content("synthetic failed input " * 10000)
                    assert content.body_reference is not None
                    raise RuntimeError("synthetic pipeline failure")
            except RuntimeError:
                assert persistence.buffer.pending[0] == 1
                details = {"span-a": {"call": {"input": content.model_dump()}}}
                with database.connection():
                    database.data.primary_saved = True
                    assert not persistence.register(
                        database.data, BodyOwner("rag", "history", "record-a"), details
                    )
                    database.commit()
                persistence.confirm_saved(details)
                assert persistence.buffer.pending == (0, 0)
        assert persistence.stop()
        assert content.body_reference is not None
        assert database.data.manifests[content.body_reference.body_id][5] == "complete"
    finally:
        persistence.stop()
