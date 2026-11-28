"""worker thread から event loop へ投げる処理が、await されない coroutine を残さない（#5764）。

標準評価のケースが時間切れになった後も回答の thread は動き続け、工程の終わりを通知する。
その後に loop を閉じて process を終えると、``asyncio.run_coroutine_threadsafe`` へ渡した
coroutine が await されず ``RuntimeWarning: coroutine '_call' was never awaited`` になっていた。
遅いモデルは event で止めたスタブ。
"""

from __future__ import annotations

import asyncio
import gc
import threading
import warnings
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

import pytest
from rag_engine.resources.deadline import DeadlineExceeded

from app.rag.answer_engine import _start_on_loop, _step_notifier
from app.rag.answer_threads import run_answer_in_thread
from app.rag.loop_calls import submit_to_loop

# 回答の thread が終わるのを待つ上限（秒）。遅いモデルのスタブは試験が event を立てるまで返らない。
_THREAD_WAIT_SECONDS = 10.0


@contextmanager
def _unawaited_warnings() -> Iterator[list[warnings.WarningMessage]]:
    """ブロックの中と、抜ける前の GC で出た「await されなかった coroutine」の警告を集める。"""
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        found: list[warnings.WarningMessage] = []
        yield found
        gc.collect()
        found.extend(w for w in caught if "was never awaited" in str(w.message))


def test_answer_thread_after_timeout_neither_notifies_nor_leaves_coroutines() -> None:
    """時間切れの後にモデルが返っても、通知・I/O を loop へ投げず、閉じた loop で警告が出ない。"""
    notified: list[str] = []
    release = threading.Event()
    reached_model = threading.Event()
    finished = threading.Event()
    after_timeout: list[BaseException] = []
    notify_errors: list[BaseException] = []

    async def step_callback(name: str, outcome: str, elapsed: float, record: Any) -> None:
        del outcome, elapsed, record
        notified.append(name)

    async def io() -> str:
        return "io"

    def slow_answer(loop: asyncio.AbstractEventLoop) -> None:
        notify = _step_notifier(step_callback, loop)
        try:
            notify("検索", "", 0.0, {})
            reached_model.set()
            release.wait(_THREAD_WAIT_SECONDS)  # 遅いモデル
            try:
                notify("回答の生成", "エラーで中断", 1.0, {})
            except RuntimeError as exc:  # 閉じた loop へ投げた
                notify_errors.append(exc)
            try:
                _start_on_loop(io, loop)
            except DeadlineExceeded as exc:
                after_timeout.append(exc)
        finally:
            finished.set()

    async def evaluate_case() -> None:
        loop = asyncio.get_running_loop()
        with pytest.raises(TimeoutError):
            await asyncio.wait_for(
                run_answer_in_thread(slow_answer, loop, timeout_seconds=30.0), timeout=0.5
            )
        assert reached_model.is_set()

    loop = asyncio.new_event_loop()
    with _unawaited_warnings() as unawaited:
        loop.run_until_complete(evaluate_case())
        loop.close()
        release.set()
        assert finished.wait(_THREAD_WAIT_SECONDS)

    assert notified == ["検索"]
    assert notify_errors == []
    assert len(after_timeout) == 1
    assert unawaited == []


def test_submit_to_closed_loop_raises_without_creating_a_coroutine() -> None:
    created: list[bool] = []

    async def work() -> None:
        created.append(True)

    loop = asyncio.new_event_loop()
    loop.close()
    with _unawaited_warnings() as unawaited, pytest.raises(RuntimeError):
        submit_to_loop(work, loop)
    assert created == []
    assert unawaited == []


def test_submit_to_stopped_loop_leaves_no_coroutine_when_the_loop_closes() -> None:
    created: list[bool] = []

    async def work() -> None:
        created.append(True)

    loop = asyncio.new_event_loop()
    with _unawaited_warnings() as unawaited:
        future = submit_to_loop(work, loop)
        loop.close()
    assert not future.done()
    assert created == []
    assert unawaited == []


async def test_submit_to_loop_returns_the_result_and_the_error() -> None:
    loop = asyncio.get_running_loop()

    async def ok() -> str:
        return "ok"

    async def failing() -> str:
        raise ValueError("合成の失敗")

    assert (
        await asyncio.to_thread(lambda: submit_to_loop(ok, loop).result(_THREAD_WAIT_SECONDS))
        == "ok"
    )
    with pytest.raises(ValueError, match="合成の失敗"):
        await asyncio.to_thread(lambda: submit_to_loop(failing, loop).result(_THREAD_WAIT_SECONDS))


async def test_cancelling_the_future_cancels_the_task_on_the_loop() -> None:
    loop = asyncio.get_running_loop()
    started = threading.Event()
    cancelled = asyncio.Event()

    async def slow() -> None:
        started.set()
        try:
            await asyncio.sleep(_THREAD_WAIT_SECONDS * 3)
        except asyncio.CancelledError:
            cancelled.set()
            raise

    def in_thread() -> None:
        future = submit_to_loop(slow, loop)
        assert started.wait(_THREAD_WAIT_SECONDS)
        future.cancel()

    await asyncio.to_thread(in_thread)
    await asyncio.wait_for(cancelled.wait(), timeout=_THREAD_WAIT_SECONDS)
