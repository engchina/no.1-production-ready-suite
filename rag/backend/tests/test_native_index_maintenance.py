"""原生索引の整理は実残数を確認し、整理の結果を確定し、業務transactionを扱わない。"""

from collections.abc import Mapping
from typing import Literal

import pytest

from app.clients.native_index_maintenance import (
    NativeIndexMaintenanceIncomplete,
    NativeIndexVerificationFailed,
    maintain_native_vectors,
)


class IndexConnection:
    def __init__(
        self, counts: list[tuple[int, int]], fail: bool = False, fail_full: bool = False
    ) -> None:
        self.counts = iter(counts)
        self.fail = fail
        self.fail_full = fail_full
        self.calls: list[str] = []

    def cursor(self) -> "IndexConnection":
        return self

    def execute(self, statement: str, binds: Mapping[str, object] | None = None) -> None:
        self.calls.append(statement)
        if self.fail and "OPTIMIZE_INDEX" in statement:
            raise RuntimeError("合成の整理失敗")
        if self.fail_full and "OPTLEVEL_FULL" in statement:
            raise RuntimeError("合成の FULL の失敗")

    def fetchone(self) -> tuple[int, int]:
        return next(self.counts)

    def close(self) -> None:
        self.calls.append("close")

    def commit(self) -> None:
        # 整理だけの接続。確定するのは OPTIMIZE_INDEX が開いた transaction(#5199)。
        self.calls.append("commit")

    def rollback(self) -> None:
        raise AssertionError("業務transactionをrollbackしない")

    def optimizers(self) -> list[str]:
        return [
            "semantic" if "SECTION_SEMANTIC_INDEX" in sql else "full"
            for sql in self.calls
            if "OPTIMIZE_INDEX" in sql
        ]


def test_pending_request_runs_semantic_even_when_vector_counts_are_equal() -> None:
    connection = IndexConnection([(2, 2), (2, 2)])
    result = maintain_native_vectors(connection)
    assert result.strategy == "semantic"
    assert not result.full_completed
    assert (result.before.stored, result.after.visible) == (2, 2)
    assert connection.optimizers() == ["semantic"]
    operation = next(sql for sql in connection.calls if "OPTIMIZE_INDEX" in sql)
    assert "OPTLEVEL_TOKEN_TYPE" in operation
    assert "maxtime" not in operation.lower()


def test_semantic_cleanup_restores_current_vectors_without_full_or_rebuild() -> None:
    connection = IndexConnection([(12, 2), (2, 2)])
    result = maintain_native_vectors(connection)
    assert result.after.stored == result.after.visible == 2
    assert connection.optimizers() == ["semantic"]
    assert not any("REBUILD" in sql for sql in connection.calls)


def test_semantic_that_leaves_debt_stays_pending_without_starting_full() -> None:
    """FULL は間隔の内に始めない。要求を残し、間隔が過ぎた FULL で片付ける(#4214)。"""
    connection = IndexConnection([(12, 2), (12, 2)])
    with pytest.raises(NativeIndexMaintenanceIncomplete):
        maintain_native_vectors(connection, plan="semantic")
    assert connection.optimizers() == ["semantic"]


def test_full_that_clears_semantic_debt_is_not_bounded_by_maxtime() -> None:
    """Oracle Free では semantic が正常終了しても件数が減らない(#4021)。"""
    connection = IndexConnection([(12, 2), (12, 2), (2, 2)])
    result = maintain_native_vectors(connection, plan="semantic_then_full")
    assert connection.optimizers() == ["semantic", "full"]
    assert result.strategy == "semantic_then_full"
    assert result.full_completed
    full = [sql for sql in connection.calls if "OPTLEVEL_FULL" in sql]
    assert "maxtime" not in full[0].lower()


def test_remaining_debt_is_retryable_and_not_reported_as_complete() -> None:
    connection = IndexConnection([(12, 2), (8, 2), (8, 2)])
    with pytest.raises(NativeIndexMaintenanceIncomplete):
        maintain_native_vectors(connection, plan="semantic_then_full")


def test_database_failure_does_not_start_another_optimizer() -> None:
    connection = IndexConnection([(12, 2)], fail=True)
    with pytest.raises(RuntimeError, match="合成の整理失敗"):
        maintain_native_vectors(connection)
    assert len(connection.optimizers()) == 1


def test_plan_full_skips_semantic_after_a_failed_attempt() -> None:
    connection = IndexConnection([(12, 2), (2, 2)])
    result = maintain_native_vectors(connection, plan="full")
    assert connection.optimizers() == ["full"]
    assert result.strategy == "full"
    assert result.full_completed


def test_due_full_runs_after_semantic_so_the_shortage_is_fixed_first() -> None:
    connection = IndexConnection([(12, 2), (2, 2), (2, 2)])
    result = maintain_native_vectors(connection, plan="semantic_then_full")
    assert connection.optimizers() == ["semantic", "full"]
    assert result.strategy == "semantic_then_full"


def test_due_full_failure_keeps_the_completed_semantic_cleanup() -> None:
    connection = IndexConnection([(12, 2), (2, 2)], fail_full=True)
    result = maintain_native_vectors(connection, plan="semantic_then_full")
    assert connection.optimizers() == ["semantic", "full"]
    assert result.strategy == "semantic"
    assert not result.full_completed
    assert result.after.stored == result.after.visible == 2


def test_full_failure_is_reported_when_vector_debt_remains() -> None:
    connection = IndexConnection([(12, 2), (12, 2)], fail_full=True)
    with pytest.raises(RuntimeError, match="合成の FULL の失敗"):
        maintain_native_vectors(connection, plan="semantic_then_full")


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
    # 整理の確定(#5199)と件数の検査は、整理の期限ではなく検査の上限で行う。
    assert order == ["call", "call", "call", "verify", "verify", "verify"]


def test_verification_failure_is_distinct_from_optimizer_failure() -> None:
    connection = IndexConnection([(2, 2)])
    with pytest.raises(NativeIndexVerificationFailed) as raised:
        maintain_native_vectors(connection)
    assert isinstance(raised.value.__cause__, StopIteration)
    assert len(connection.optimizers()) == 1


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


@pytest.mark.parametrize(
    ("plan", "counts", "optimizers"),
    [
        ("semantic", [(12, 2), (2, 2)], ["semantic"]),
        ("semantic_then_full", [(12, 2), (2, 2), (2, 2)], ["semantic", "full"]),
        ("full", [(12, 2), (2, 2)], ["full"]),
    ],
    ids=["semantic", "semantic-then-full", "full"],
)
def test_each_optimizer_is_committed_before_verification(
    plan: Literal["semantic", "semantic_then_full", "full"],
    counts: list[tuple[int, int]],
    optimizers: list[str],
) -> None:
    """semantic の OPTIMIZE_INDEX は transaction を開いたまま返る(実 DB で確認)。確定しないと
    接続の返却・close で rollback され、整理が毎回やり直しになる(#5199)。"""
    connection = IndexConnection(counts)
    maintain_native_vectors(connection, plan=plan)
    assert connection.optimizers() == optimizers
    for position, call in enumerate(connection.calls):
        if "OPTIMIZE_INDEX" in call:
            assert connection.calls[position + 1] == "commit"
    assert connection.calls.count("commit") == len(optimizers)


def test_failed_optimizer_is_not_committed() -> None:
    connection = IndexConnection([(12, 2)], fail=True)
    with pytest.raises(RuntimeError, match="合成の整理失敗"):
        maintain_native_vectors(connection)
    assert "commit" not in connection.calls


def test_failed_full_keeps_the_committed_semantic_cleanup() -> None:
    connection = IndexConnection([(12, 2), (2, 2)], fail_full=True)
    result = maintain_native_vectors(connection, plan="semantic_then_full")
    assert result.strategy == "semantic"
    semantic = next(i for i, sql in enumerate(connection.calls) if "SECTION_SEMANTIC" in sql)
    assert connection.calls[semantic + 1] == "commit"
    assert connection.calls.count("commit") == 1
