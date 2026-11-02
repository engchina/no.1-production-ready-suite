"""原生索引の整理は実残数を確認し、業務transactionを扱わない。"""

from collections.abc import Mapping

import pytest

from app.clients.native_index_maintenance import (
    NativeIndexMaintenanceIncomplete,
    NativeIndexVerificationFailed,
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


def test_pending_request_runs_full_even_when_vector_counts_are_equal() -> None:
    connection = IndexConnection([(2, 2), (2, 2)])
    result = maintain_native_vectors(connection)
    assert result.strategy == "full"
    assert (result.before.stored, result.after.visible) == (2, 2)
    operations = [sql for sql in connection.calls if "OPTIMIZE_INDEX" in sql]
    assert len(operations) == 1
    assert "OPTLEVEL_FULL" in operations[0]
    assert "maxtime" not in operations[0].lower()


def test_full_cleanup_restores_current_vectors_without_rebuild() -> None:
    connection = IndexConnection([(12, 2), (2, 2)])
    result = maintain_native_vectors(connection)
    assert result.after.stored == result.after.visible == 2
    assert result.strategy == "full"
    assert not any("REBUILD" in sql for sql in connection.calls)


def test_remaining_debt_is_retryable_and_not_reported_as_complete() -> None:
    connection = IndexConnection([(12, 2), (8, 2)])
    with pytest.raises(NativeIndexMaintenanceIncomplete):
        maintain_native_vectors(connection)


def test_database_failure_does_not_start_another_optimizer() -> None:
    connection = IndexConnection([(12, 2)], fail=True)
    with pytest.raises(RuntimeError, match="合成の整理失敗"):
        maintain_native_vectors(connection)
    assert len([sql for sql in connection.calls if "OPTIMIZE_INDEX" in sql]) == 1


def test_verification_uses_its_own_hook_after_optimizer_returns() -> None:
    connection = IndexConnection([(2, 2), (2, 2)])
    order: list[str] = []

    def before_call() -> None:
        order.append("call")
        if any("OPTIMIZE_INDEX" in sql for sql in connection.calls):
            raise TimeoutError("整理の期限を後の検査へ持ち込まない")

    def before_verify() -> None:
        order.append("verify")

    result = maintain_native_vectors(
        connection, before_call=before_call, before_verify=before_verify
    )
    assert result.after.stored == 2
    assert order == ["call", "call", "call", "verify", "verify"]


def test_verification_failure_is_distinct_from_optimizer_failure() -> None:
    connection = IndexConnection([(2, 2)])
    with pytest.raises(NativeIndexVerificationFailed) as raised:
        maintain_native_vectors(connection)
    assert isinstance(raised.value.__cause__, StopIteration)
    assert len([sql for sql in connection.calls if "OPTIMIZE_INDEX" in sql]) == 1


def test_concurrent_insert_does_not_require_original_visible_count() -> None:
    connection = IndexConnection([(12, 2), (4, 4)])
    result = maintain_native_vectors(connection)
    assert result.after.visible == 4
    assert result.strategy == "full"


@pytest.mark.parametrize("index_name", ["IDX'; DROP TABLE T; --", "IDX.X", "", "a" * 31])
def test_index_identifier_is_validated_before_database_access(index_name: str) -> None:
    connection = IndexConnection([])
    with pytest.raises(ValueError, match="索引名"):
        maintain_native_vectors(connection, index_name=index_name)
    assert not connection.calls
