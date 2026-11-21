"""DBを使う背景処理が完了する前に、共有poolを閉じない。"""

from __future__ import annotations

import asyncio
import threading
from contextlib import contextmanager
from types import SimpleNamespace
from typing import Any

import pytest

from app import main
from app.config import get_settings
from app.rag import native_index_maintenance, wiki_jobs


@pytest.mark.asyncio
@pytest.mark.parametrize("stage", ["warm", "wiki", "maintenance"])
async def test_shutdown_drains_background_db_work_before_closing_pool(
    monkeypatch: pytest.MonkeyPatch, stage: str
) -> None:
    """取消でto_threadだけが残る状況を同期し、poolを閉じる時点の完了を確かめる。"""
    settings = get_settings()
    monkeypatch.setattr(settings, "ingestion_queue_inprocess_worker_enabled", False)
    monkeypatch.setattr(settings, "ingestion_queue_dedicated_worker_enabled", True)
    started, release, finished = threading.Event(), threading.Event(), threading.Event()
    closed: list[bool] = []
    selected: list[asyncio.Task[Any]] = []

    def operation() -> None:
        started.set()
        try:
            if not release.wait(5):
                raise TimeoutError("合成のDB処理の解放待ち")
        finally:
            finished.set()

    async def background(name: str, stop: asyncio.Event | None = None) -> None:
        if name != stage:
            return
        task = asyncio.current_task()
        assert task is not None
        selected.append(task)
        await asyncio.to_thread(operation)
        if stop is not None:
            await stop.wait()

    async def warm(_settings: Any) -> None:
        await background("warm")

    async def wiki(stop: asyncio.Event) -> None:
        await background("wiki", stop)

    async def maintenance(stop: asyncio.Event) -> None:
        await background("maintenance", stop)

    async def shutdown() -> None:
        pass

    @contextmanager
    def tracing(*_args: Any, **_kwargs: Any) -> Any:
        yield

    def noop(*_args: Any, **_kwargs: Any) -> None:
        pass

    monkeypatch.setattr(main, "tracing_lifecycle", tracing)
    for name in (
        "configure_logging",
        "configure_trace_exporter",
        "_warn_pending_legacy_local_storage_dir",
        "start_trace_span_writer",
        "stop_trace_span_writer",
        "close_trace_exporter",
    ):
        monkeypatch.setattr(main, name, noop)
    monkeypatch.setattr(main, "_warm_search", warm)
    monkeypatch.setattr(native_index_maintenance, "run_native_maintenance_loop", maintenance)
    monkeypatch.setattr(
        wiki_jobs, "wiki_jobs", SimpleNamespace(run_forever=wiki, shutdown=shutdown)
    )
    service = SimpleNamespace(shutdown=shutdown)
    monkeypatch.setattr(main, "get_evaluation_job_service", lambda: service)
    monkeypatch.setattr(main, "get_chat_answer_run_service", lambda: service)
    monkeypatch.setattr(main, "close_oracle_pool", lambda: closed.append(finished.is_set()))

    context = main.lifespan(main.app)
    await context.__aenter__()
    closing: asyncio.Task[bool | None] | None = None
    try:
        assert await asyncio.wait_for(asyncio.to_thread(started.wait, 2), 3)
        closing = asyncio.create_task(context.__aexit__(None, None, None))
        # 終了処理を背景taskの待ちへ進める。DB処理はeventで止め、時間の重なりに依存しない。
        await asyncio.sleep(0)
        assert selected[0].cancelling() == 0, "実行中のDB処理を残してtaskだけを取り消した"
        assert closed == [], "DB処理が実行中なのにpoolを閉じた"
        assert not closing.done()
    finally:
        release.set()
        if closing is None:
            await context.__aexit__(None, None, None)
        else:
            await asyncio.wait_for(closing, 3)
        assert await asyncio.wait_for(asyncio.to_thread(finished.wait, 2), 3)
    assert closed == [True]
