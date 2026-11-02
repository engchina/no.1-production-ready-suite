"""整理接続の期限、取消、lease境界をモデル・実DBなしで確認する。"""

import asyncio
from collections.abc import Callable, Mapping
from typing import cast

import pytest

from app.clients.native_maintenance_store import VERIFY_CALL_TIMEOUT_MS, NativeMaintenanceStore
from app.clients.oracle import OracleClient
from app.rag.native_index_maintenance import MaintenanceLease

LEASE = MaintenanceLease("worker-a", "token-a", ("event-a",))


class Connection:
    def __init__(self, *, valid: bool = True) -> None:
        self.call_timeout = 1000
        self.valid = valid
        self.sql: list[str] = []
        self.closed = False

    def cursor(self) -> "Connection":
        return self

    def execute(self, statement: str, binds: Mapping[str, object] | None = None) -> None:
        self.sql.append(statement)

    def fetchone(self) -> tuple[int, ...] | None:
        if "lease_until>" in self.sql[-1]:
            return (1,) if self.valid else None
        return (2, 2)

    def close(self) -> None:
        self.closed = True


class Oracle:
    def __init__(self, connection: Connection) -> None:
        self.connection = connection
        self.transactions = 0
        self.fail_record = False

    def acquire_connection(self) -> Connection:
        return self.connection

    async def _db_call_runner(self, callback: Callable[[], None]) -> None:
        callback()

    async def _run_transaction(self, callback: Callable[[Connection], None]) -> None:
        self.transactions += 1
        if self.fail_record:
            raise RuntimeError("合成の記録の失敗")
        callback(self.connection)


@pytest.mark.parametrize("valid", [True, False])
async def test_expired_lease_cannot_start_and_timeout_is_restored(valid: bool) -> None:
    connection = Connection(valid=valid)
    store = NativeMaintenanceStore(cast(OracleClient, Oracle(connection)))
    if valid:
        await store.optimize(LEASE)
    else:
        with pytest.raises(RuntimeError, match="lease"):
            await store.optimize(LEASE)
    assert any("OPTIMIZE_INDEX" in sql for sql in connection.sql) is valid
    assert not any("FOR UPDATE" in sql for sql in connection.sql)
    assert connection.call_timeout == 1000
    assert connection.closed


class PlanConnection(Connection):
    def __init__(self, *, full_due: tuple[int, ...] | None) -> None:
        super().__init__()
        self.full_due = full_due

    def fetchone(self) -> tuple[int, ...] | None:
        if "NUMTODSINTERVAL(:interval_hours" in self.sql[-1]:
            return self.full_due
        return super().fetchone()

    def optimizers(self) -> list[str]:
        return [
            "semantic" if "SECTION_SEMANTIC_INDEX" in sql else "full"
            for sql in self.sql
            if "OPTIMIZE_INDEX" in sql
        ]

    def recorded_full(self) -> bool:
        return any("MERGE INTO" in sql for sql in self.sql)


@pytest.mark.parametrize(
    ("full_due", "attempt", "optimizers", "recorded"),
    [
        ((0,), 1, ["semantic"], False),
        ((1,), 1, ["semantic", "full"], True),
        (None, 1, ["semantic", "full"], True),
        ((0,), 2, ["full"], True),
    ],
    ids=["within-interval", "interval-passed", "never-ran", "retry-after-failure"],
)
async def test_plan_follows_the_full_interval_and_the_attempt(
    full_due: tuple[int, ...] | None, attempt: int, optimizers: list[str], recorded: bool
) -> None:
    connection = PlanConnection(full_due=full_due)
    oracle = Oracle(connection)
    store = NativeMaintenanceStore(cast(OracleClient, oracle), full_interval_hours=24)

    await store.optimize(MaintenanceLease("worker-a", "token-a", ("event-a",), attempt=attempt))

    assert connection.optimizers() == optimizers
    assert connection.recorded_full() is recorded


async def test_failing_to_record_full_does_not_fail_the_finished_cleanup() -> None:
    connection = PlanConnection(full_due=(1,))
    oracle = Oracle(connection)
    oracle.fail_record = True
    store = NativeMaintenanceStore(cast(OracleClient, oracle), full_interval_hours=24)

    await store.optimize(LEASE)

    assert oracle.transactions == 1


async def test_total_deadline_prevents_later_optimizer_call(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    connection = Connection()
    store = NativeMaintenanceStore(cast(OracleClient, Oracle(connection)))
    # lease取得・件数照合後に予算切れ。各SQLに180秒ずつ足し続けない。
    readings = iter([0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 181.0])
    monkeypatch.setattr("app.clients.native_maintenance_store.monotonic", lambda: next(readings))
    with pytest.raises(TimeoutError):
        await store.optimize(LEASE)
    assert not any("OPTIMIZE_INDEX" in sql for sql in connection.sql)
    assert connection.call_timeout == 1000 and connection.closed


async def test_optimizer_returning_past_deadline_is_not_reported_as_failed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """整理が上限を超えて正常に返ったら、後の件数検査で失敗にせず完了へ進める（#4097）。"""
    clock = [0.0]
    verify_timeouts: list[int] = []

    class SlowOptimizer(Connection):
        def execute(self, statement: str, binds: Mapping[str, object] | None = None) -> None:
            super().execute(statement, binds)
            if "OPTIMIZE_INDEX" in statement:
                clock[0] = 500.0
            elif clock[0] > 0:
                verify_timeouts.append(self.call_timeout)

    connection = SlowOptimizer()
    store = NativeMaintenanceStore(cast(OracleClient, Oracle(connection)))
    monkeypatch.setattr("app.clients.native_maintenance_store.monotonic", lambda: clock[0])
    await store.optimize(LEASE)
    assert verify_timeouts == [VERIFY_CALL_TIMEOUT_MS]
    assert connection.call_timeout == 1000 and connection.closed


async def test_cancellation_does_not_report_running_sql_as_stopped() -> None:
    started = asyncio.Event()
    release = asyncio.Event()
    finished = asyncio.Event()

    class WaitingOracle:
        async def _db_call_runner(self, callback: Callable[[], None]) -> None:
            started.set()
            await release.wait()
            finished.set()

    store = NativeMaintenanceStore(cast(OracleClient, WaitingOracle()))
    task = asyncio.create_task(store.optimize(LEASE))
    await started.wait()
    task.cancel()
    await asyncio.sleep(0)
    assert not task.done() and not finished.is_set()
    release.set()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert finished.is_set()
