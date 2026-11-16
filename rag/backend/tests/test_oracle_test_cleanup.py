"""合成データの削除 commit と原生索引の整理を分離する。"""

from collections.abc import Mapping

import pytest

from tests import _oracle_test_db


class CleanupConnection:
    def __init__(
        self, deleted: int, present: bool, stored: int, visible: int, fail: bool = False
    ) -> None:
        self.call_timeout = 1000
        self.deleted = deleted
        self.stored = stored
        self.visible = visible
        self.present = present
        self.fail = fail
        self.rowcount = 0
        self.calls: list[str] = []
        self.optimized = False

    def cursor(self) -> "CleanupConnection":
        return self

    def execute(self, sql: str, binds: Mapping[str, object] | None = None) -> None:
        self.calls.append(sql)
        self.rowcount = self.deleted if sql.startswith("DELETE FROM rag_chunks WHERE") else 0
        if "OPTIMIZE_INDEX" in sql:
            if self.fail:
                raise RuntimeError("合成の整理失敗")
            self.optimized = True

    def fetchone(self) -> tuple[int, ...]:
        if "FROM user_indexes" in self.calls[-1]:
            return (int(self.present),)
        return (self.visible if self.optimized else self.stored, self.visible)

    def fetchall(self) -> list[tuple[str]]:
        return [("captured-event",)] if self.deleted or self.stored > self.visible else []

    def commit(self) -> None:
        self.calls.append("commit")

    def rollback(self) -> None:
        self.calls.append("rollback")

    def close(self) -> None:
        self.calls.append("close")


@pytest.mark.parametrize(
    "deleted,present,stored,visible,optimized",
    [(2, True, 4, 2, True), (0, True, 4, 2, True), (0, True, 2, 2, False), (2, False, 4, 0, False)],
    ids=["fixture-deleted", "api-already-deleted", "no-debt", "missing-index"],
)
def test_cleanup_purges_deleted_vectors_after_commit_only_when_needed(
    monkeypatch: pytest.MonkeyPatch,
    deleted: int,
    present: bool,
    stored: int,
    visible: int,
    optimized: bool,
) -> None:
    connection = CleanupConnection(deleted, present, stored, visible)
    monkeypatch.setattr(_oracle_test_db, "_cleanup_connection", lambda: connection)
    _oracle_test_db.cleanup_to_baseline()
    operations = [sql for sql in connection.calls if "OPTIMIZE_INDEX" in sql]
    assert bool(operations) == optimized
    if optimized:
        assert connection.calls.index("commit") < connection.calls.index(operations[0])
        assert "OPTLEVEL_FULL" in operations[0]
        assert "maxtime" not in operations[0].lower()
    if present and optimized:
        assert any("WHERE event_id=:event_id" in sql for sql in connection.calls)
    assert "rollback" not in connection.calls


def test_cleanup_does_not_hide_index_purge_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    connection = CleanupConnection(0, True, 4, 2, fail=True)
    monkeypatch.setattr(_oracle_test_db, "_cleanup_connection", lambda: connection)
    with pytest.raises(RuntimeError, match="合成の整理失敗"):
        _oracle_test_db.cleanup_to_baseline()
    assert "commit" in connection.calls
    assert connection.calls[-1] == "rollback"


def test_cleanup_rejects_unpurged_vectors(monkeypatch: pytest.MonkeyPatch) -> None:
    connection = CleanupConnection(0, True, 4, 2)
    original_execute = connection.execute

    def ineffective(sql: str, binds: Mapping[str, object] | None = None) -> None:
        original_execute(sql, binds)
        connection.optimized = False

    monkeypatch.setattr(connection, "execute", ineffective)
    monkeypatch.setattr(_oracle_test_db, "_cleanup_connection", lambda: connection)
    with pytest.raises(RuntimeError, match="整理が残っています"):
        _oracle_test_db.cleanup_to_baseline()
    assert connection.calls[-1] == "rollback"


def test_cleanup_does_not_acknowledge_failed_full(monkeypatch: pytest.MonkeyPatch) -> None:
    connection = CleanupConnection(0, True, 4, 2, fail=True)
    monkeypatch.setattr(_oracle_test_db, "_cleanup_connection", lambda: connection)
    with pytest.raises(RuntimeError, match="合成の整理失敗"):
        _oracle_test_db.cleanup_to_baseline()
    assert not any("WHERE event_id=:event_id" in sql for sql in connection.calls)
    assert connection.call_timeout == 1000


def test_session_start_keeps_the_request_when_vectors_stay_unpurged(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """別の worker の削除で残骸が残っても、開始を失敗にせず、整理の要求を消さない（#4177）。"""
    connection = CleanupConnection(0, True, 4, 2)
    original_execute = connection.execute

    def ineffective(sql: str, binds: Mapping[str, object] | None = None) -> None:
        original_execute(sql, binds)
        connection.optimized = False

    monkeypatch.setattr(connection, "execute", ineffective)

    _oracle_test_db.settle_at_session_start(connection)

    assert any("OPTIMIZE_INDEX" in sql for sql in connection.calls)
    assert not any("WHERE event_id=:event_id" in sql for sql in connection.calls)


def test_session_start_does_not_hide_a_failed_full() -> None:
    connection = CleanupConnection(0, True, 4, 2, fail=True)

    with pytest.raises(RuntimeError, match="合成の整理失敗"):
        _oracle_test_db.settle_at_session_start(connection)


class PingConnection:
    """後始末の接続の使い回しを確かめる合成の接続。"""

    def __init__(self, healthy: bool = True) -> None:
        self.healthy = healthy
        self.pings = 0
        self.closed = False

    def ping(self) -> None:
        self.pings += 1
        if not self.healthy:
            raise RuntimeError("合成の切断")

    def close(self) -> None:
        self.closed = True


def test_cleanup_connection_is_reused_within_a_worker(monkeypatch: pytest.MonkeyPatch) -> None:
    """テストごとに接続を作らず、1 本を使い回す（listener への接続の集中を避ける。#5156）。"""
    created: list[PingConnection] = []

    def connect() -> PingConnection:
        created.append(PingConnection())
        return created[-1]

    monkeypatch.setattr(_oracle_test_db, "_cleanup_connection_instance", None)
    monkeypatch.setattr(_oracle_test_db, "_connect", connect)

    first = _oracle_test_db._cleanup_connection()
    second = _oracle_test_db._cleanup_connection()

    assert first is second
    assert len(created) == 1
    assert first.pings == 1
    _oracle_test_db.close_cleanup_connection()
    assert first.closed
    assert _oracle_test_db._cleanup_connection_instance is None


def test_cleanup_connection_reconnects_when_the_session_is_gone(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    broken = PingConnection(healthy=False)
    fresh = PingConnection()
    monkeypatch.setattr(_oracle_test_db, "_cleanup_connection_instance", broken)
    monkeypatch.setattr(_oracle_test_db, "_connect", lambda: fresh)

    assert _oracle_test_db._cleanup_connection() is fresh
    assert broken.closed
    assert _oracle_test_db._cleanup_connection_instance is fresh
