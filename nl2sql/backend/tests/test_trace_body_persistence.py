"""全文の所属は正本書込みの後、commit前に登録し、結果行の寿命を延ばさない。"""

from __future__ import annotations

import json
from contextlib import contextmanager
from dataclasses import asdict
from typing import Any

import pytest
from pr_backend_core.observability.trace_bodies import BodyOwner, prepare_body

from app.features.nl2sql import trace_bodies
from app.features.nl2sql.db_admin_chat_store import DbAdminChatStore
from app.features.nl2sql.incremental_store import OracleIncrementalNl2SqlRepository
from app.features.nl2sql.models import DbAdminChatPlan, DbAdminChatTurn


class Connection:
    def __init__(self, *, fail_commit: bool = False, row: Any = None) -> None:
        self.events: list[str] = []
        self.sql: list[tuple[str, Any]] = []
        self.fail_commit = fail_commit
        self.row = row
        self.rowcount = 1

    def cursor(self) -> Any:
        return self

    def __enter__(self) -> Any:
        return self

    def __exit__(self, *_: Any) -> None:
        pass

    def setinputsizes(self, **_: Any) -> None:
        pass

    def execute(self, sql: str, binds: Any = None) -> None:
        self.sql.append((sql, binds))
        self.events.append("sql")

    def fetchone(self) -> Any:
        return self.row

    def fetchall(self) -> Any:
        return [("removed-a",)]

    def commit(self) -> None:
        self.events.append("commit")
        if self.fail_commit:
            raise RuntimeError("synthetic commit failure")

    def rollback(self) -> None:
        self.events.append("rollback")

    @contextmanager
    def connect(self) -> Any:
        yield self


def _persistence(monkeypatch: pytest.MonkeyPatch, conn: Connection, *, fault: bool = False) -> Any:
    body = prepare_body("合成の全文", part_characters=3)
    details = {
        "span-a": {
            "call": {"input": {"status": "recorded", "body_reference": asdict(body.reference)}}
        }
    }
    owners: list[BodyOwner] = []

    class Persistence:
        def register(self, cursor: Any, owner: BodyOwner, value: Any) -> Any:
            assert cursor is conn
            assert any(
                "MERGE INTO NL2SQL_STATE_DOCUMENTS" in sql
                or "INSERT INTO NL2SQL_DB_ADMIN_CHAT_TURNS" in sql
                or "UPDATE NL2SQL_DB_ADMIN_CHAT_TURNS" in sql
                for sql, _ in conn.sql
            )
            assert value == details
            conn.events.append("register")
            owners.append(owner)
            return {body.reference.body_id: "queue_full"} if fault else {}

        def confirm_saved(self, _details: Any) -> None:
            assert "commit" in conn.events
            conn.events.append("confirm")

    monkeypatch.setattr(trace_bodies, "_PERSISTENCE", Persistence())
    return details, owners


@pytest.mark.parametrize("collection", ["jobs", "history"])
@pytest.mark.parametrize("fail_commit", [False, True])
def test_primary_write_registers_before_commit_and_confirms_only_committed_snapshot(
    monkeypatch: pytest.MonkeyPatch, collection: str, fail_commit: bool
) -> None:
    conn = Connection(fail_commit=fail_commit)
    details, owners = _persistence(monkeypatch, conn)
    repository = OracleIncrementalNl2SqlRepository(connection_factory=conn.connect)
    if fail_commit:
        with pytest.raises(RuntimeError, match="synthetic commit failure"):
            repository.put_document(collection, "record-a", {"span_details": details})
        assert "confirm" not in conn.events
    else:
        repository.put_document(collection, "record-a", {"span_details": details})
        assert conn.events.index("register") < conn.events.index("commit")
        assert conn.events[-1] == "confirm"
    assert owners == [BodyOwner("nl2sql", collection, "record-a")]


def test_fault_updates_saved_snapshot_and_etag_without_mutating_call_input(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    conn = Connection()
    details, _ = _persistence(monkeypatch, conn, fault=True)
    repository = OracleIncrementalNl2SqlRepository(connection_factory=conn.connect)
    repository.put_document("jobs", "record-a", {"span_details": details})
    update = next(binds for sql, binds in conn.sql if sql.startswith("UPDATE NL2SQL_STATE"))
    saved = json.loads(update["payload"])
    assert saved["span_details"]["span-a"]["call"]["input"]["failure_code"] == "queue_full"
    assert details["span-a"]["call"]["input"]["status"] == "recorded"
    assert len(update["etag"]) == 64


@pytest.mark.parametrize("collection", ["chat_results", "conversations", "ontology_build_jobs"])
def test_non_trace_documents_do_not_register_or_confirm_bodies(
    monkeypatch: pytest.MonkeyPatch, collection: str
) -> None:
    conn = Connection()
    details, owners = _persistence(monkeypatch, conn)
    repository = OracleIncrementalNl2SqlRepository(connection_factory=conn.connect)
    repository.put_document(collection, "record-a", {"span_details": details})
    assert not owners and "confirm" not in conn.events


def test_lost_execution_fence_never_registers_or_confirms_body(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    conn = Connection(row=(json.dumps({"status": "cancelled"}), "", "cancelled"))
    details, owners = _persistence(monkeypatch, conn)
    repository = OracleIncrementalNl2SqlRepository(connection_factory=conn.connect)
    assert (
        repository.patch_document_if_current(
            "jobs", "job-a", {"span_details": details}, expected={"status": "running"}
        )
        is None
    )
    assert not owners and conn.events[-1] == "rollback"


@pytest.mark.parametrize("ttl", [False, True])
def test_delete_releases_only_missing_primary_owners_in_the_same_transaction(
    monkeypatch: pytest.MonkeyPatch, ttl: bool
) -> None:
    conn = Connection()
    released: list[BodyOwner] = []

    def release(cursor: Any, owners: Any) -> None:
        assert cursor is conn
        assert "DELETE FROM NL2SQL_STATE_DOCUMENTS" in conn.sql[0][0]
        assert "NOT EXISTS" in conn.sql[-1][0]
        assert "commit" not in conn.events
        released.extend(owners)

    monkeypatch.setattr(trace_bodies, "release_body_owners", release)
    repository = OracleIncrementalNl2SqlRepository(connection_factory=conn.connect)
    if ttl:
        repository.delete_documents_older_than("history", seconds=100)
    else:
        repository.delete_document("history", "record-a")
    assert released == [BodyOwner("nl2sql", "history", "removed-a")]
    assert conn.events[-1] == "commit"


def test_db_admin_chat_cascade_releases_turns_after_actual_delete(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    conn = Connection()
    released: list[BodyOwner] = []

    def release(cursor: Any, owners: Any) -> None:
        assert cursor is conn
        assert "DELETE FROM NL2SQL_DB_ADMIN_CHATS" in conn.sql[0][0]
        assert "NL2SQL_DB_ADMIN_CHAT_TURNS" in conn.sql[-1][0]
        assert "commit" not in conn.events
        released.extend(owners)

    monkeypatch.setattr(trace_bodies, "release_body_owners", release)
    assert DbAdminChatStore(conn.connect).delete_conversation("conversation-a")
    assert released == [BodyOwner("nl2sql", "db_admin_turn", "removed-a")]


@pytest.mark.parametrize("update", [False, True])
def test_admin_turn_write_registers_after_primary_before_commit(
    monkeypatch: pytest.MonkeyPatch, update: bool
) -> None:
    conn = Connection()
    details, owners = _persistence(monkeypatch, conn)
    turn = DbAdminChatTurn(
        turn_id="turn-a",
        question="合成の質問",
        created_at="2026-01-01T00:00:00Z",
        generated_at="2026-01-01T00:00:00Z",
        plan=DbAdminChatPlan(sql="SELECT 1 FROM DUAL"),
        span_details=details,
    )
    store = DbAdminChatStore(conn.connect)
    if update:
        store.update_turn("chat-a", turn)
    else:
        store.append_turn("chat-a", turn)
    assert owners == [BodyOwner("nl2sql", "db_admin_turn", "turn-a")]
    assert conn.events.index("register") < conn.events.index("commit")
    assert conn.events[-1] == "confirm"


def test_recording_failure_annotation_does_not_abort_primary_save(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    conn = Connection()
    details, _ = _persistence(monkeypatch, conn, fault=True)
    execute = conn.execute

    def fail_annotation(sql: str, binds: Any = None) -> None:
        if sql.startswith("UPDATE NL2SQL_STATE_DOCUMENTS SET PAYLOAD_JSON"):
            raise RuntimeError("synthetic trace snapshot write failure")
        execute(sql, binds)

    monkeypatch.setattr(conn, "execute", fail_annotation)
    OracleIncrementalNl2SqlRepository(connection_factory=conn.connect).put_document(
        "jobs", "job-a", {"span_details": details}
    )
    assert conn.events[-2:] == ["commit", "confirm"]
