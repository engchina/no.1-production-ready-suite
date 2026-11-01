"""原生索引の整理は実残数を確認し、業務transactionを扱わない。"""

from collections.abc import Mapping

import pytest

from app.clients.native_index_maintenance import (
    NativeIndexMaintenanceIncomplete,
    maintain_native_vectors,
)


class IndexConnection:
    def __init__(self, counts: list[tuple[int, int]], fail: bool = False) -> None:
        self.counts = iter(counts)
        self.fail = fail
        self.calls: list[str] = []

    def cursor(self) -> "IndexConnection":
        return self

    def execute(self, statement: str, binds: Mapping[str, object] | None = None) -> None:
        self.calls.append(statement)
        if self.fail and "OPTIMIZE_INDEX" in statement:
            raise RuntimeError("合成の整理失敗")

    def fetchone(self) -> tuple[int, int]:
        return next(self.counts)

    def close(self) -> None:
        self.calls.append("close")

    def commit(self) -> None:
        raise AssertionError("業務transactionをcommitしない")

    def rollback(self) -> None:
        raise AssertionError("業務transactionをrollbackしない")


def test_clean_index_does_not_optimize() -> None:
    connection = IndexConnection([(2, 2)])
    result = maintain_native_vectors(connection)
    assert result.strategy == "none"
    assert (result.before.stored, result.after.visible) == (2, 2)
    assert not any("OPTIMIZE_INDEX" in sql for sql in connection.calls)


def test_semantic_cleanup_restores_current_vectors_without_full_optimize() -> None:
    connection = IndexConnection([(12, 2), (2, 2)])
    result = maintain_native_vectors(connection)
    assert result.strategy == "semantic"
    assert result.after.stored == result.after.visible == 2
    operations = [sql for sql in connection.calls if "OPTIMIZE_INDEX" in sql]
    assert len(operations) == 1
    assert "SECTION_SEMANTIC_INDEX" in operations[0]
    assert "REBUILD" not in operations[0]


def test_successful_semantic_noop_uses_bounded_full_fallback() -> None:
    connection = IndexConnection([(12, 2), (12, 2), (2, 2)])
    result = maintain_native_vectors(connection)
    assert result.strategy == "full_fallback"
    operations = [sql for sql in connection.calls if "OPTIMIZE_INDEX" in sql]
    assert len(operations) == 2
    assert "maxtime" in operations[1].lower()
    assert "REBUILD" not in operations[1]


def test_remaining_debt_is_retryable_and_not_reported_as_complete() -> None:
    connection = IndexConnection([(12, 2), (12, 2), (8, 2)])
    with pytest.raises(NativeIndexMaintenanceIncomplete):
        maintain_native_vectors(connection)


def test_database_failure_does_not_start_another_optimizer() -> None:
    connection = IndexConnection([(12, 2)], fail=True)
    with pytest.raises(RuntimeError, match="合成の整理失敗"):
        maintain_native_vectors(connection)
    assert len([sql for sql in connection.calls if "OPTIMIZE_INDEX" in sql]) == 1


def test_concurrent_insert_does_not_require_original_visible_count() -> None:
    connection = IndexConnection([(12, 2), (4, 4)])
    result = maintain_native_vectors(connection)
    assert result.after.visible == 4
    assert result.strategy == "semantic"


@pytest.mark.parametrize("index_name", ["IDX'; DROP TABLE T; --", "IDX.X", "", "a" * 31])
def test_index_identifier_is_validated_before_database_access(index_name: str) -> None:
    connection = IndexConnection([])
    with pytest.raises(ValueError, match="索引名"):
        maintain_native_vectors(connection, index_name=index_name)
    assert not connection.calls


@pytest.mark.parametrize("minutes", [0, -1, float("nan"), float("inf")])
def test_maintenance_time_budget_is_finite_and_positive(minutes: float) -> None:
    connection = IndexConnection([])
    with pytest.raises(ValueError, match="時間上限"):
        maintain_native_vectors(connection, full_optimize_minutes=minutes)
    assert not connection.calls
