"""整理接続の期限、取消、lease境界をモデル・実DBなしで確認する。"""

import asyncio
from collections.abc import Callable, Mapping
from typing import cast

import pytest

from app.clients.native_index_maintenance import NativeIndexMaintenanceIncomplete
from app.clients.native_maintenance_store import VERIFY_CALL_TIMEOUT_MS, NativeMaintenanceStore
from app.clients.oracle import OracleClient
from app.config import Settings
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

    def commit(self) -> None:
        pass

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
    """FULL を始めてよい時刻を、記録に応じて変える合成の状態。"""

    def __init__(self, *, full_due: bool | None, counts: tuple[int, int] = (2, 2)) -> None:
        super().__init__()
        # None は FULL の行がまだ無い状態。
        self.full_due = full_due
        self.counts = counts
        self.fail_full = False
        self.waiting = 0
        self.events: list[str] = []

    def execute(self, statement: str, binds: Mapping[str, object] | None = None) -> None:
        super().execute(statement, binds)
        if "OPTIMIZE_INDEX" in statement:
            step = "semantic" if "SECTION_SEMANTIC_INDEX" in statement else "full"
            self.events.append(step)
            if step == "full" and self.fail_full:
                raise RuntimeError("合成の FULL の失敗")
        elif "MERGE INTO" in statement:
            assert binds is not None
            self.events.append("reserve" if binds["start_full"] == 1 else "wait")
            self.waiting += cast(int, binds["waiting"])
            if binds["start_full"] == 1:
                self.full_due = False
        elif "last_completed_at=SYSTIMESTAMP" in statement:
            self.events.append("full_completed")
            self.waiting = 0

    def commit(self) -> None:
        self.events.append("commit")

    def fetchone(self) -> tuple[int, ...] | None:
        statement = self.sql[-1]
        if "CASE WHEN next_attempt_at<=SYSTIMESTAMP" in statement:
            return None if self.full_due is None else (1 if self.full_due else 0,)
        if "lease_until>" in statement:
            return (1,)
        return self.counts

    def optimizers(self) -> list[str]:
        return [event for event in self.events if event in {"semantic", "full"}]


@pytest.mark.parametrize(
    ("full_due", "attempt", "events", "optimizers", "completed"),
    [
        (False, 1, ("event-a",), ["semantic"], False),
        (True, 1, ("event-a",), ["semantic", "full"], True),
        (None, 1, ("event-a",), ["semantic", "full"], True),
        (False, 2, ("event-a",), ["semantic"], False),
        (True, 2, ("event-a",), ["full"], True),
        (True, 1, (), ["full"], True),
    ],
    ids=[
        "within-interval",
        "interval-passed",
        "never-ran",
        "retry-within-interval",
        "retry-after-interval",
        "owed-full-without-events",
    ],
)
async def test_plan_follows_the_full_interval(
    full_due: bool | None,
    attempt: int,
    events: tuple[str, ...],
    optimizers: list[str],
    completed: bool,
) -> None:
    connection = PlanConnection(full_due=full_due)
    store = NativeMaintenanceStore(cast(OracleClient, Oracle(connection)), full_interval_hours=6)

    await store.optimize(MaintenanceLease("worker-a", "token-a", events, attempt=attempt))

    assert connection.optimizers() == optimizers
    assert ("full_completed" in connection.events) is completed


async def test_full_start_is_committed_before_any_optimizer_runs() -> None:
    """時間切れや再起動の後も次の FULL を待たせるため、開始の前に記録を確定する(#4214)。"""
    connection = PlanConnection(full_due=True)
    store = NativeMaintenanceStore(cast(OracleClient, Oracle(connection)), full_interval_hours=6)

    await store.optimize(LEASE)

    assert connection.events[:3] == ["reserve", "commit", "semantic"]


async def test_failed_full_is_not_retried_until_the_interval_passes() -> None:
    """失敗の再試行で FULL を繰り返さない。残骸があれば semantic だけを再試行する(#4214)。"""
    connection = PlanConnection(full_due=True, counts=(12, 2))
    connection.fail_full = True
    store = NativeMaintenanceStore(cast(OracleClient, Oracle(connection)), full_interval_hours=6)

    with pytest.raises(RuntimeError, match="合成の FULL の失敗"):
        await store.optimize(LEASE)
    assert connection.optimizers() == ["semantic", "full"]

    connection.events.clear()
    for attempt in (2, 3, 4):
        with pytest.raises(NativeIndexMaintenanceIncomplete):
            await store.optimize(MaintenanceLease("worker-a", "token-a", ("event-a",), attempt))
    assert connection.optimizers() == ["semantic", "semantic", "semantic"]


async def test_semantic_only_completion_leaves_full_owed() -> None:
    """間隔の内に終えた要求は FULL の借りを残す。新しい要求が無くても後で FULL を行う。"""
    connection = PlanConnection(full_due=False)
    store = NativeMaintenanceStore(cast(OracleClient, Oracle(connection)), full_interval_hours=6)

    await store.optimize(LEASE)

    assert connection.waiting == 1
    assert "reserve" not in connection.events


async def test_finished_full_clears_what_is_owed() -> None:
    connection = PlanConnection(full_due=True)
    store = NativeMaintenanceStore(cast(OracleClient, Oracle(connection)), full_interval_hours=6)

    await store.optimize(LEASE)

    assert connection.waiting == 0


async def test_deferred_full_stays_owed_when_vectors_are_clean() -> None:
    """FULL が時間切れでも件数が合えば要求は終える。借りは残し、次の間隔で再開する。"""
    connection = PlanConnection(full_due=True)
    connection.fail_full = True
    store = NativeMaintenanceStore(cast(OracleClient, Oracle(connection)), full_interval_hours=6)

    await store.optimize(LEASE)

    assert connection.waiting == 1
    assert "full_completed" not in connection.events
    assert connection.full_due is False


class ClaimConnection:
    def __init__(self, *, events: list[str], full_owed: bool) -> None:
        self.events = events
        self.full_owed = full_owed
        self.sql: list[str] = []

    def cursor(self) -> "ClaimConnection":
        return self

    def execute(self, statement: str, binds: Mapping[str, object] | None = None) -> None:
        self.sql.append(statement)

    def fetchone(self) -> tuple[int, ...] | None:
        if "FOR UPDATE SKIP LOCKED" in self.sql[-1]:
            return (0,)
        return (1,) if self.full_owed else None

    def fetchall(self) -> list[tuple[str]]:
        return [(event,) for event in self.events]

    def close(self) -> None:
        pass


class ClaimOracle:
    def __init__(self, connection: ClaimConnection) -> None:
        self.connection = connection

    async def _run_transaction(
        self, callback: Callable[[ClaimConnection], MaintenanceLease | None]
    ) -> MaintenanceLease | None:
        return callback(self.connection)


async def test_claim_takes_owed_full_when_no_request_is_waiting() -> None:
    """最後の要求の後も FULL が走るので、$VR と SEARCH の件数が収束する(#4214)。"""
    connection = ClaimConnection(events=[], full_owed=True)
    store = NativeMaintenanceStore(cast(OracleClient, ClaimOracle(connection)))

    lease = await store.claim("worker-a")

    assert lease is not None and lease.event_ids == ()
    assert any("SET lease_owner" in sql for sql in connection.sql)


async def test_claim_stays_idle_when_nothing_is_owed_or_not_due() -> None:
    connection = ClaimConnection(events=[], full_owed=False)
    store = NativeMaintenanceStore(cast(OracleClient, ClaimOracle(connection)))

    assert await store.claim("worker-a") is None
    assert not any("SET lease_owner" in sql for sql in connection.sql)
    owed = next(sql for sql in connection.sql if "attempt_count>0" in sql)
    assert "next_attempt_at<=SYSTIMESTAMP" in owed


def test_full_has_a_minimum_interval_by_default() -> None:
    """1 CPU の ADB で要求ごとの FULL が資源を使い切らないようにする(#4214)。"""
    field = Settings.model_fields["rag_native_index_full_optimize_interval_hours"]
    assert field.default == 6


async def test_failing_to_record_full_does_not_fail_the_finished_cleanup() -> None:
    connection = PlanConnection(full_due=True)
    oracle = Oracle(connection)
    oracle.fail_record = True
    store = NativeMaintenanceStore(cast(OracleClient, oracle), full_interval_hours=6)

    await store.optimize(LEASE)

    assert oracle.transactions == 1


async def test_total_deadline_prevents_later_optimizer_call(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    connection = Connection()
    store = NativeMaintenanceStore(cast(OracleClient, Oracle(connection)))
    # lease取得・件数照合後に予算切れ。各SQLに180秒ずつ足し続けない。
    readings = iter([0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 181.0])
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
