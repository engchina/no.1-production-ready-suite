"""RAGの実保存境界で、成功した正本だけに全文の所属を登録する。"""

from collections.abc import Mapping
from dataclasses import asdict
from datetime import UTC, datetime
from typing import Any

import pytest
from pr_backend_core.observability.trace_bodies import BodyOwner, prepare_body

from app.clients import oracle as oracle_module
from app.clients.oracle import OracleClient, StoredMessage
from app.clients.oracle_run_history import RunHistoryStore
from app.rag import trace_bodies
from tests.test_oracle_adapter import (
    FakeOracleCursor,
    FakeOraclePool,
    _oci_settings,
    _run_inline,
)


class PersistenceSpy:
    def __init__(self, pool: FakeOraclePool, *, fail: bool = False) -> None:
        self.pool = pool
        self.fail = fail
        self.owners: list[BodyOwner] = []
        self.confirmed: list[Mapping[str, Any]] = []

    def register(self, cursor: Any, owner: BodyOwner, details: Mapping[str, Any]) -> dict[str, str]:
        assert self.pool.connection.calls and self.pool.connection.commits == 0
        assert details.get("span-a")
        assert "PLATFORM_TRACE" not in self.pool.connection.calls[-1].statement
        self.owners.append(owner)
        return {reference["body_id"]: "queue_full"} if self.fail else {}

    def confirm_saved(self, details: Mapping[str, Any]) -> None:
        assert self.pool.connection.commits == 1
        self.confirmed.append(details)


reference = asdict(prepare_body("synthetic full body", part_characters=4).reference)


def record() -> dict[str, Any]:
    return {
        "trace_id": "a" * 32,
        "span_details": {
            "span-a": {
                "call": {
                    "input": {
                        "status": "recorded",
                        "text": "synthetic preview",
                        "preview_only": True,
                        "body_reference": dict(reference),
                    }
                }
            }
        },
    }


@pytest.mark.parametrize("kind", ["answer_record", "history"])
@pytest.mark.parametrize("fail", [False, True])
async def test_real_record_save_registers_after_primary_and_confirms_after_commit(
    monkeypatch: pytest.MonkeyPatch, kind: str, fail: bool
) -> None:
    pool = FakeOraclePool()
    spy = PersistenceSpy(pool, fail=fail)
    monkeypatch.setattr(trace_bodies, "_PERSISTENCE", spy)
    oracle = OracleClient(settings=_oci_settings(), pool=pool, db_call_runner=_run_inline)
    trace = record()
    if kind == "answer_record":
        await oracle.save_answer_record(
            {
                "trace_id": "record-a",
                "surface": "search",
                "answer_engine": "grounded",
                "question": "合成の質問",
                "answer": "合成の回答",
                "diagnostics": {"trace": trace},
            }
        )
    else:
        await RunHistoryStore(oracle).save_run(
            {
                "run_id": "record-a",
                "surface": "search",
                "status": "error",
                "stages": {"trace": trace},
            }
        )
    assert spy.owners == [BodyOwner("rag", kind, "record-a")]
    assert spy.confirmed == [trace["span_details"]]
    assert pool.connection.commits == 1 and pool.connection.rollbacks == 0
    assert len(pool.connection.calls) == (2 if fail else 1)
    if fail:
        values = str(pool.connection.calls[-1].parameters)
        assert "incomplete" in values and "queue_full" in values
        assert trace["span_details"]["span-a"]["call"]["input"]["status"] == "recorded"


@pytest.mark.parametrize("operation", ["progress", "finish", "close", "append"])
@pytest.mark.parametrize("updated", [0, 1])
async def test_message_registers_only_when_primary_write_succeeds(
    monkeypatch: pytest.MonkeyPatch, operation: str, updated: int
) -> None:
    pool = FakeOraclePool()
    spy = PersistenceSpy(pool)
    monkeypatch.setattr(trace_bodies, "_PERSISTENCE", spy)
    monkeypatch.setattr(FakeOracleCursor, "rowcount", updated, raising=False)
    monkeypatch.setattr(oracle_module, "_select_conversation", lambda *_: object())
    oracle = OracleClient(settings=_oci_settings(), pool=pool, db_call_runner=_run_inline)
    trace = record()
    message = StoredMessage(
        id="record-a",
        conversation_id="conversation-a",
        role="ASSISTANT",
        content="合成回答",
        created_at=datetime.now(UTC),
        progress=trace,
    )
    if operation == "progress":
        await oracle.update_chat_message_progress(
            "record-a", lease_owner="worker-a", progress=trace
        )
    elif operation == "finish":
        await oracle.finish_chat_message(message, lease_owner="worker-a")
    elif operation == "close":
        await oracle.close_streaming_chat_message(
            "record-a", status="ERROR", content="", progress=trace, scoped=False
        )
    else:
        await oracle.append_message(message)
    expected = bool(updated) or operation == "append"
    assert spy.owners == ([BodyOwner("rag", "message", "record-a")] if expected else [])
    assert len(spy.confirmed) == int(expected)


async def test_capture_decorator_keeps_original_return_and_failure() -> None:
    @trace_bodies.capture_trace_bodies
    async def operation(fail: bool) -> str:
        if fail:
            raise RuntimeError("synthetic failure")
        return "result"

    assert await operation(False) == "result"
    with pytest.raises(RuntimeError, match="synthetic failure"):
        await operation(True)


@pytest.mark.parametrize("kind", ["message", "history", "answer_record"])
async def test_real_delete_releases_owners_in_same_transaction_after_primary_delete(
    monkeypatch: pytest.MonkeyPatch, kind: str
) -> None:
    from app.clients import oracle_run_history as history_module

    pool = FakeOraclePool()
    oracle = OracleClient(settings=_oci_settings(), pool=pool, db_call_runner=_run_inline)
    monkeypatch.setattr(FakeOracleCursor, "rowcount", 1, raising=False)
    monkeypatch.setattr(oracle_module, "_select_conversation", lambda *_: object())
    released: list[str] = []

    def release(connection: Any, actual_kind: str) -> None:
        assert connection is pool.connection and connection.commits == 0
        assert connection.calls[-1].statement.startswith("DELETE FROM rag_")
        released.append(actual_kind)

    monkeypatch.setattr(oracle_module, "release_deleted_trace_body_owners", release)
    monkeypatch.setattr(history_module, "release_deleted_trace_body_owners", release)
    if kind == "message":
        await oracle.delete_conversation("conversation-a")
    elif kind == "history":
        assert await RunHistoryStore(oracle).purge_runs(30) == 1
    else:
        assert await oracle.purge_answer_records(30) == 1
    assert released == [kind] and pool.connection.commits == 1


@pytest.mark.parametrize("kind", ["history", "answer_record"])
async def test_unlimited_retention_never_releases_body_owners(
    monkeypatch: pytest.MonkeyPatch, kind: str
) -> None:
    pool = FakeOraclePool()
    oracle = OracleClient(settings=_oci_settings(), pool=pool, db_call_runner=_run_inline)
    if kind == "history":
        assert await RunHistoryStore(oracle).purge_runs(0) == 0
    else:
        assert await oracle.purge_answer_records(0) == 0
    assert not pool.connection.calls and pool.connection.commits == 0


def test_cascade_cleanup_uses_actual_missing_primary_and_is_independent_of_writer(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from types import SimpleNamespace

    rows = [("deleted-a",), ("deleted-b",)]
    statements: list[tuple[str, Any]] = []
    released: list[BodyOwner] = []

    class Cursor:
        def execute(self, sql: str, binds: Any) -> None:
            statements.append((sql, binds))

        def fetchall(self) -> list[tuple[str]]:
            return rows

        def close(self) -> None:
            pass

    cursor = Cursor()
    monkeypatch.setattr(trace_bodies, "_PERSISTENCE", None)
    monkeypatch.setattr(
        trace_bodies, "release_body_owners", lambda actual, owners: released.extend(owners)
    )
    trace_bodies.release_deleted_trace_body_owners(
        SimpleNamespace(cursor=lambda: cursor), "message"
    )
    assert (
        "NOT EXISTS (SELECT 1 FROM rag_messages p WHERE p.message_id = o.OWNER_ID)"
        in statements[0][0]
    )
    assert statements[0][1] == {"product": "rag", "kind": "message"}
    assert released == [
        BodyOwner("rag", "message", "deleted-a"),
        BodyOwner("rag", "message", "deleted-b"),
    ]


@pytest.mark.parametrize("restricted", [False, True])
@pytest.mark.parametrize("administrator", [False, True])
def test_saved_body_read_checks_visibility_before_opening_database(
    monkeypatch: pytest.MonkeyPatch, restricted: bool, administrator: bool
) -> None:
    from collections.abc import Iterator
    from contextlib import contextmanager
    from types import SimpleNamespace

    from pr_backend_core.observability import trace_body_reader
    from pr_backend_core.observability.trace_bodies import BodyPage

    saved = record()
    saved["span_details"]["span-a"]["call"]["visibility"] = (
        "system_admin" if restricted else "owner"
    )
    monkeypatch.setattr(
        trace_bodies,
        "current_audit_request_context",
        lambda: SimpleNamespace(all_users_visible=administrator),
    )
    opened: list[bool] = []

    @contextmanager
    def connection() -> Iterator[Any]:
        opened.append(True)
        yield SimpleNamespace(cursor=lambda: SimpleNamespace(close=lambda: None))

    monkeypatch.setattr(trace_bodies, "_connection", connection)
    monkeypatch.setattr(trace_body_reader, "read_body_page", lambda *_args, **_kwargs: None)
    page = trace_bodies.read_saved_trace_body(
        saved, "message", "record-a", span_id="span-a", slot="input", first_part=0
    )
    if restricted and not administrator:
        assert page is None and not opened
    else:
        assert isinstance(page, BodyPage) and page.status == "incomplete"
        assert page.failure_code == "body_unavailable" and opened == [True]
