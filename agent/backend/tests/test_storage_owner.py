"""保存先の所有権は復元・スケジューラより先に取得する（#4216）。"""

from __future__ import annotations

import subprocess
import sys
from collections.abc import AsyncIterator
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import FastAPI

from app.features.agent import storage_backend, storage_bootstrap, storage_owner


def test_second_file_process_is_rejected_and_crash_releases_lock(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "runtime.json"
    path.write_text('{"synthetic_run":"running","scheduled_runs":1}', encoding="utf-8")
    before = path.read_bytes()
    monkeypatch.setattr(storage_backend, "resolved_backend", lambda: "file")
    monkeypatch.setattr(
        storage_owner,
        "get_settings",
        lambda: SimpleNamespace(agent_runtime_snapshot_path=str(path)),
    )
    script = """
import sys
from types import SimpleNamespace
from app.features.agent import storage_owner, storage_backend
storage_backend.resolved_backend = lambda: "file"
storage_owner.get_settings = lambda: SimpleNamespace(agent_runtime_snapshot_path=sys.argv[1])
owner = storage_owner.StorageOwner()
owner.enabled = True
owner.acquire()
print("owned", flush=True)
sys.stdin.read()
"""
    process = subprocess.Popen(
        [sys.executable, "-c", script, str(path)],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        text=True,
    )
    first = storage_owner.StorageOwner()
    first.enabled = True
    try:
        assert process.stdout is not None and process.stdout.readline().strip() == "owned"
        with pytest.raises(storage_owner.StorageAlreadyOwnedError):
            first.acquire()
        assert path.read_bytes() == before
        process.kill()
        process.wait(timeout=10)
        first.acquire()
        first.acquire()
        assert path.read_bytes() == before
    finally:
        first.close()
        if process.poll() is None:
            process.kill()
        process.communicate(timeout=10)
    second = storage_owner.StorageOwner()
    second.enabled = True
    try:
        second.acquire()
    finally:
        second.close()


@pytest.mark.asyncio
async def test_lifespan_rejects_owner_before_loading_or_scheduling(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app import main

    def reject() -> None:
        raise storage_owner.StorageAlreadyOwnedError(storage_owner.OWNER_MESSAGE)

    monkeypatch.setattr(storage_owner.storage_owner, "acquire", reject)
    monkeypatch.setattr(
        storage_bootstrap,
        "start_background_load",
        lambda: pytest.fail("所有権のないプロセスは保存先を読まない"),
    )
    with pytest.raises(storage_owner.StorageAlreadyOwnedError):
        async with main.lifespan(main.app):
            pytest.fail("2 つ目の backend は起動しない")
    assert not storage_owner.storage_owner.enabled


def test_lost_oracle_owner_stops_old_backend(monkeypatch: pytest.MonkeyPatch) -> None:
    owner = storage_owner.StorageOwner()
    stopped: list[bool] = []

    class Disconnected:
        def ping(self) -> None:
            raise ConnectionError("合成の切断")

    owner._connection = Disconnected()
    monkeypatch.setattr(storage_owner, "OWNER_CHECK_SECONDS", 0)
    monkeypatch.setattr(owner, "_terminate", lambda: stopped.append(True))
    owner._watch_connection()
    assert stopped == [True]


@pytest.mark.asyncio
async def test_startup_failure_releases_file_owner(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from contextlib import asynccontextmanager

    from app import main

    path = tmp_path / "runtime.json"
    monkeypatch.setattr(storage_backend, "resolved_backend", lambda: "file")
    monkeypatch.setattr(
        storage_owner,
        "get_settings",
        lambda: SimpleNamespace(agent_runtime_snapshot_path=str(path)),
    )

    @asynccontextmanager
    async def fail(_app: FastAPI) -> AsyncIterator[None]:
        raise RuntimeError("合成の起動失敗")
        yield

    monkeypatch.setattr(main, "_application_lifespan", fail)
    with pytest.raises(RuntimeError, match="合成の起動失敗"):
        async with main.lifespan(main.app):
            pytest.fail("起動に失敗する")
    next_owner = storage_owner.StorageOwner()
    next_owner.enabled = True
    try:
        next_owner.acquire()
    finally:
        next_owner.close()


def test_shutdown_drains_storage_loader_before_releasing_owner(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import threading

    entered, release, finished = threading.Event(), threading.Event(), threading.Event()
    path = tmp_path / "runtime.json"
    monkeypatch.setattr(storage_backend, "resolved_backend", lambda: "file")
    monkeypatch.setattr(
        storage_owner,
        "get_settings",
        lambda: SimpleNamespace(agent_runtime_snapshot_path=str(path)),
    )
    first, second = storage_owner.StorageOwner(), storage_owner.StorageOwner()
    first.enabled = second.enabled = True
    first.acquire()
    storage_bootstrap.reset()

    def load() -> None:
        entered.set()
        assert release.wait(10)

    def shutdown() -> None:
        storage_bootstrap.stop_background_load()
        first.close()
        finished.set()

    monkeypatch.setattr(storage_bootstrap, "_load_until_ready", load)
    storage_bootstrap.start_background_load()
    assert entered.wait(5)
    worker = threading.Thread(target=shutdown)
    worker.start()
    try:
        assert not finished.wait(0.05)
        with pytest.raises(storage_owner.StorageAlreadyOwnedError):
            second.acquire()
    finally:
        release.set()
        worker.join(timeout=10)
        first.close()
    try:
        assert finished.is_set()
        second.acquire()
    finally:
        second.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("tick_fails", [False, True])
async def test_scheduler_shutdown_waits_for_database_tick(
    monkeypatch: pytest.MonkeyPatch, tick_fails: bool
) -> None:
    import asyncio
    import threading

    from app.features.agent import automations

    entered, release = threading.Event(), threading.Event()
    monkeypatch.setattr(storage_bootstrap, "is_ready", lambda: True)

    def tick() -> list[automations.AutomationFired]:
        entered.set()
        assert release.wait(10)
        if tick_fails:
            raise RuntimeError("合成の保存失敗")
        return []

    monkeypatch.setattr(automations, "scheduler_tick", tick)
    scheduler = asyncio.create_task(automations.run_scheduler())
    try:
        assert await asyncio.to_thread(entered.wait, 5)
        scheduler.cancel()
        await asyncio.sleep(0)
        assert not scheduler.done()
    finally:
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await scheduler


@pytest.mark.asyncio
async def test_uninitialized_schema_keeps_administration_available(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from contextlib import asynccontextmanager

    from app import main

    visited: list[bool] = []

    def missing() -> None:
        raise storage_owner.StorageNotInitializedError("合成の未作成の制御表")

    @asynccontextmanager
    async def administration(_app: FastAPI) -> AsyncIterator[None]:
        visited.append(True)
        yield

    monkeypatch.setattr(storage_owner.storage_owner, "acquire", missing)
    monkeypatch.setattr(main, "_application_lifespan", administration)
    async with main.lifespan(main.app):
        assert visited == [True]
    assert not storage_owner.storage_owner.enabled
