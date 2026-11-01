"""整理要求の取得snapshotだけを完了し、後着・失敗を失わない。"""

import asyncio

import pytest

from app.rag.native_index_maintenance import MaintenanceLease, run_maintenance_once


class QueueStore:
    def __init__(self) -> None:
        self.events = {"committed-a"}
        self.optimize_calls = 0
        self.failure = False
        self.error_type: str | None = None
        self.completed: list[tuple[str, ...]] = []
        self.lease_valid = True

    async def claim(self, owner: str) -> MaintenanceLease | None:
        if not self.events:
            return None
        return MaintenanceLease(owner, "token-a", tuple(sorted(self.events)))

    async def optimize(self) -> None:
        self.optimize_calls += 1
        # ID順で古い別transactionが、snapshot取得後にcommitする場合も残す。
        self.events.add("committed-0")
        await asyncio.sleep(0)
        if self.failure:
            raise RuntimeError("公開してはいけない合成詳細")

    async def heartbeat(self, lease: MaintenanceLease) -> bool:
        return self.lease_valid

    async def complete(self, lease: MaintenanceLease) -> bool:
        if not self.lease_valid:
            return False
        self.completed.append(lease.event_ids)
        self.events.difference_update(lease.event_ids)
        return True

    async def fail(self, lease: MaintenanceLease, error_type: str) -> None:
        self.error_type = error_type


@pytest.mark.asyncio
async def test_idle_queue_does_not_count_or_optimize_the_index() -> None:
    store = QueueStore()
    store.events.clear()
    assert await run_maintenance_once(store, "worker-a") == "idle"
    assert store.optimize_calls == 0


@pytest.mark.asyncio
async def test_only_claimed_committed_events_are_acknowledged() -> None:
    store = QueueStore()
    assert await run_maintenance_once(store, "worker-a") == "complete"
    assert store.completed == [("committed-a",)]
    assert store.events == {"committed-0"}


@pytest.mark.asyncio
async def test_failure_preserves_pending_events_and_retries() -> None:
    store = QueueStore()
    store.failure = True
    assert await run_maintenance_once(store, "worker-a") == "failed"
    assert store.error_type == "RuntimeError"
    assert store.completed == []
    assert "committed-a" in store.events
    store.failure = False
    assert await run_maintenance_once(store, "worker-a") == "complete"
    assert store.events == set()


@pytest.mark.asyncio
async def test_expired_lease_cannot_acknowledge_events() -> None:
    store = QueueStore()
    store.lease_valid = False
    assert await run_maintenance_once(store, "worker-a") == "lease_lost"
    assert "committed-a" in store.events
    assert not store.completed


class HeartbeatStore(QueueStore):
    def __init__(self) -> None:
        super().__init__()
        self.started = asyncio.Event()
        self.release = asyncio.Event()
        self.heartbeats = 0

    async def optimize(self) -> None:
        self.started.set()
        await self.release.wait()

    async def heartbeat(self, lease: MaintenanceLease) -> bool:
        self.heartbeats += 1
        self.release.set()
        return self.lease_valid


@pytest.mark.asyncio
@pytest.mark.parametrize("lease_valid", [True, False])
async def test_heartbeat_guards_completion_during_long_maintenance(lease_valid: bool) -> None:
    store = HeartbeatStore()
    store.lease_valid = lease_valid
    result = await asyncio.wait_for(
        run_maintenance_once(store, "worker-a", heartbeat_seconds=0.001), timeout=2
    )
    assert store.heartbeats >= 1
    assert result == ("complete" if lease_valid else "lease_lost")
    assert bool(store.completed) is lease_valid


@pytest.mark.asyncio
async def test_worker_shutdown_keeps_events_for_recovery() -> None:
    store = HeartbeatStore()
    task = asyncio.create_task(run_maintenance_once(store, "worker-a"))
    await asyncio.wait_for(store.started.wait(), timeout=2)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert store.events == {"committed-a"}
    assert not store.completed
