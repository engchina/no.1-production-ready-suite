"""整理接続の期限、取消、lease境界をモデル・実DBなしで確認する。"""

import asyncio
from collections.abc import Callable, Mapping
from typing import cast

import pytest

from app.clients.native_maintenance_store import NativeMaintenanceStore
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

    def acquire_connection(self) -> Connection:
        return self.connection

    async def _db_call_runner(self, callback: Callable[[], None]) -> None:
        callback()


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


async def test_total_deadline_prevents_later_optimizer_call(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    connection = Connection()
    store = NativeMaintenanceStore(cast(OracleClient, Oracle(connection)))
    # lease取得・件数照合後に予算切れ。各SQLに180秒ずつ足し続けない。
    readings = iter([0.0, 0.0, 0.0, 0.0, 0.0, 181.0])
    monkeypatch.setattr("app.clients.native_maintenance_store.monotonic", lambda: next(readings))
    with pytest.raises(TimeoutError):
        await store.optimize(LEASE)
    assert not any("OPTIMIZE_INDEX" in sql for sql in connection.sql)
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
