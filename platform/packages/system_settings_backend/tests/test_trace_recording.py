"""共通の単一設定・既定の無効・cache・失敗時の無効・要求の経路で DB を待たないことを検証する。"""

import threading
import time
from collections.abc import Callable

from fastapi import FastAPI
from fastapi.testclient import TestClient

from pr_system_settings.trace_recording import (
    CACHE_SECONDS,
    InMemoryTraceRecordingStore,
    TraceRecordingProvider,
    TraceRecordingSettings,
    build_trace_recording_router,
)


def _run_now(refresh: Callable[[], None]) -> None:
    """読み直しをその場で行う（cache の規則だけを決定論で確かめる）。"""
    refresh()


def test_shared_switch_default_cache_and_saved_value() -> None:
    clock = [0.0]
    store = InMemoryTraceRecordingStore()
    first = TraceRecordingProvider(store, clock=lambda: clock[0], run_in_background=_run_now)
    second = TraceRecordingProvider(store, clock=lambda: clock[0], run_in_background=_run_now)
    assert not first.enabled() and not second.enabled()
    first.save(True)
    assert first.enabled() is True and second.enabled() is False
    clock[0] = 5.1
    assert second.enabled() is True
    second.save(False)
    clock[0] += 5.1
    assert first.enabled() is False


def test_failure_is_not_a_last_known_on_value() -> None:
    class Broken(InMemoryTraceRecordingStore):
        broken = False

        def load(self) -> TraceRecordingSettings:
            if self.broken:
                raise RuntimeError("private")
            return super().load()

    clock = [0.0]
    store = Broken()
    store.save(True)
    provider = TraceRecordingProvider(store, clock=lambda: clock[0], run_in_background=_run_now)
    assert provider.enabled() is True
    store.broken = True
    clock[0] = 6.0
    assert provider.enabled() is False


def test_api_persists_strict_boolean_and_keeps_metadata() -> None:
    provider = TraceRecordingProvider(InMemoryTraceRecordingStore())
    app = FastAPI()
    app.include_router(build_trace_recording_router(provider=lambda: provider), prefix="/settings")
    client = TestClient(app)
    assert client.get("/settings/trace-recording").json()["data"]["enabled"] is False
    assert (
        client.put("/settings/trace-recording", json={"enabled": True}).json()["data"]["enabled"]
        is True
    )
    assert client.put("/settings/trace-recording", json={"enabled": "false"}).status_code == 422
    assert (
        client.put(
            "/settings/trace-recording", json={"enabled": False, "arbitrary": True}
        ).status_code
        == 422
    )


def test_policy_cache_does_not_read_the_database_for_each_call() -> None:
    class Counting(InMemoryTraceRecordingStore):
        reads = 0

        def load(self) -> TraceRecordingSettings:
            self.reads += 1
            return super().load()

    clock = [0.0]
    store = Counting()
    provider = TraceRecordingProvider(store, clock=lambda: clock[0], run_in_background=_run_now)
    for _ in range(1000):
        assert provider.enabled() is False
    assert store.reads == 1
    clock[0] = 6.0
    provider.enabled()
    assert store.reads == 2


def test_write_dependency_blocks_an_unauthorized_change() -> None:
    from fastapi import Depends, HTTPException

    def deny() -> None:
        raise HTTPException(403)

    provider = TraceRecordingProvider(InMemoryTraceRecordingStore())
    app = FastAPI()
    app.include_router(
        build_trace_recording_router(provider=lambda: provider, write_dependencies=[Depends(deny)])
    )
    assert TestClient(app).put("/trace-recording", json={"enabled": True}).status_code == 403
    assert provider.enabled() is False


def test_expired_value_is_returned_while_one_refresh_runs_in_the_background() -> None:
    clock = [0.0]
    pending: list[Callable[[], None]] = []
    store = InMemoryTraceRecordingStore()
    store.save(True)
    provider = TraceRecordingProvider(
        store, clock=lambda: clock[0], run_in_background=pending.append
    )
    # まだ一度も読めていない間は無効。
    assert provider.enabled() is False
    assert provider.enabled() is False
    assert len(pending) == 1
    pending.pop()()
    assert provider.enabled() is True
    clock[0] = CACHE_SECONDS + 1
    store.save(False)
    for _ in range(100):
        assert provider.enabled() is True  # 読み直しの間は最後に読めた値
    assert len(pending) == 1
    pending.pop()()
    assert provider.enabled() is False


def test_refresh_started_before_save_does_not_restore_the_old_value() -> None:
    clock = [0.0]
    pending: list[Callable[[], None]] = []
    store = InMemoryTraceRecordingStore()
    provider = TraceRecordingProvider(
        store, clock=lambda: clock[0], run_in_background=pending.append
    )

    class Stale(InMemoryTraceRecordingStore):
        def load(self) -> TraceRecordingSettings:
            return TraceRecordingSettings(enabled=False)

    assert provider.enabled() is False
    provider.store = Stale()  # 保存より前に読んだ古い値を返す読み直し
    provider.save(True)
    pending.pop()()
    assert provider.enabled() is True


def test_refresh_that_cannot_start_is_retried_on_the_next_call() -> None:
    attempts = []

    def broken(refresh: Callable[[], None]) -> None:
        attempts.append(refresh)
        if len(attempts) == 1:
            raise RuntimeError("can't start new thread")
        refresh()

    store = InMemoryTraceRecordingStore()
    store.save(True)
    provider = TraceRecordingProvider(store, run_in_background=broken)
    assert provider.enabled() is False
    assert provider.enabled() is True
    assert len(attempts) == 2


def _wait_until(condition: Callable[[], bool], timeout: float = 5.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if condition():
            return True
        time.sleep(0.01)
    return bool(condition())


class _PoolLockStore(InMemoryTraceRecordingStore):
    """共有 pool の lock を取ってから読む合成の store（#5181）。"""

    def __init__(self, pool_lock: threading.Lock) -> None:
        super().__init__()
        self.pool_lock = pool_lock
        self.reads = 0

    def load(self) -> TraceRecordingSettings:
        self.reads += 1
        if not self.pool_lock.acquire(timeout=5):
            raise TimeoutError("合成の pool の待ちの上限")
        try:
            return super().load()
        finally:
            self.pool_lock.release()


def _health_app() -> FastAPI:
    from pr_backend_core.app import create_app

    return create_app(service_name="test-service", version="1.0.0", cors_origins=[])


def test_expired_cache_does_not_stop_the_event_loop_while_pool_lock_is_held() -> None:
    """別の thread が pool の lock を持つ間に期限が切れても、要求は DB を待たない（#5181）。"""
    from pr_backend_core.observability.call_policy import call_content_policy

    pool_lock = threading.Lock()
    store = _PoolLockStore(pool_lock)
    store.save(True)
    clock = [0.0]
    provider = TraceRecordingProvider(store, clock=lambda: clock[0])
    assert _wait_until(provider.enabled)
    reads_before = store.reads

    pool_lock.acquire()  # 保守の接続の返却が pool の lock を持ったままの状態
    try:
        clock[0] = CACHE_SECONDS + 1
        with call_content_policy(provider.enabled), TestClient(_health_app()) as client:
            started = time.monotonic()
            responses = [client.get("/api/health") for _ in range(3)]
            elapsed = time.monotonic() - started
        assert [response.status_code for response in responses] == [200, 200, 200]
        assert elapsed < 1.0
        # 読み直しの間は最後に読めた値を返し、読み直しは 1 本だけ。
        assert provider.enabled() is True
        assert store.reads - reads_before == 1
    finally:
        pool_lock.release()
    store.save(False)
    assert _wait_until(lambda: not provider.enabled())


def test_unreachable_database_does_not_stop_health_and_stays_disabled() -> None:
    """DB に届かない間も /api/health はすぐ返り、記録は無効のまま（#5140）。"""
    from pr_backend_core.observability.call_policy import call_content_policy

    release = threading.Event()

    class Unreachable(InMemoryTraceRecordingStore):
        reads = 0

        def load(self) -> TraceRecordingSettings:
            self.reads += 1
            release.wait(timeout=5)  # pool の取得の待ち（実際は 30 秒以上）
            raise RuntimeError("DPY-4005 合成")

    store = Unreachable()
    provider = TraceRecordingProvider(store)
    try:
        with call_content_policy(provider.enabled), TestClient(_health_app()) as client:
            started = time.monotonic()
            statuses = [client.get("/api/health").status_code for _ in range(3)]
            elapsed = time.monotonic() - started
        assert statuses == [200, 200, 200]
        assert elapsed < 1.0
        assert provider.enabled() is False
        assert store.reads == 1
    finally:
        release.set()
    assert _wait_until(lambda: provider._cached is not None)
    assert provider.enabled() is False
