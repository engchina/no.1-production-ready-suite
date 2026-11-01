"""合成データの削除 commit と原生索引の整理を分離する。"""

from collections.abc import Mapping

import pytest

from tests import _oracle_test_db


class CleanupConnection:
    def __init__(
        self, deleted: int, present: bool, stored: int, visible: int, fail: bool = False
    ) -> None:
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

    def commit(self) -> None:
        self.calls.append("commit")

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
    monkeypatch.setattr(_oracle_test_db, "_connect", lambda: connection)
    _oracle_test_db.cleanup_to_baseline()
    operations = [sql for sql in connection.calls if "OPTIMIZE_INDEX" in sql]
    assert bool(operations) == optimized
    if optimized:
        assert connection.calls.index("commit") < connection.calls.index(operations[0])
        assert "OPTLEVEL_TOKEN_TYPE" in operations[0]
        assert "SECTION_SEMANTIC_INDEX" in operations[0]
        assert "FULL" not in operations[0]
    assert connection.calls[-1] == "close"


def test_cleanup_does_not_hide_index_purge_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    connection = CleanupConnection(0, True, 4, 2, fail=True)
    monkeypatch.setattr(_oracle_test_db, "_connect", lambda: connection)
    with pytest.raises(RuntimeError, match="合成の整理失敗"):
        _oracle_test_db.cleanup_to_baseline()
    assert "commit" in connection.calls
    assert connection.calls[-1] == "close"


def test_cleanup_rejects_unpurged_vectors(monkeypatch: pytest.MonkeyPatch) -> None:
    connection = CleanupConnection(0, True, 4, 2)
    original_execute = connection.execute

    def ineffective(sql: str, binds: Mapping[str, object] | None = None) -> None:
        original_execute(sql, binds)
        connection.optimized = False

    monkeypatch.setattr(connection, "execute", ineffective)
    monkeypatch.setattr(_oracle_test_db, "_connect", lambda: connection)
    with pytest.raises(RuntimeError, match="before=4/2, after=4/2"):
        _oracle_test_db.cleanup_to_baseline()
    assert connection.calls[-1] == "close"


def test_cleanup_uses_full_only_if_semantic_purge_is_ineffective(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    connection = CleanupConnection(0, True, 4, 2)
    original_execute = connection.execute

    def semantic_noop(sql: str, binds: Mapping[str, object] | None = None) -> None:
        original_execute(sql, binds)
        if "SECTION_SEMANTIC_INDEX" in sql:
            connection.optimized = False

    monkeypatch.setattr(connection, "execute", semantic_noop)
    monkeypatch.setattr(_oracle_test_db, "_connect", lambda: connection)
    _oracle_test_db.cleanup_to_baseline()
    operations = [sql for sql in connection.calls if "OPTIMIZE_INDEX" in sql]
    assert len(operations) == 2
    assert "SECTION_SEMANTIC_INDEX" in operations[0]
    assert "OPTLEVEL_FULL" in operations[1]
    assert connection.calls[-1] == "close"
