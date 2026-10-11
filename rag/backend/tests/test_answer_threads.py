"""回答を上限で打ち切ったとき、回答の thread が LLM を呼び続けないことを検証する（#1422）。

遅い LLM はスタブ（`rag_engine.adapters.oci` の client を差し替え、渡された timeout だけ待って
timeout の例外を出す。実際の HTTP の timeout と同じ振る舞い）。時間は短い上限で確かめる。
"""

from __future__ import annotations

import asyncio
import contextlib
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from types import SimpleNamespace
from typing import Any
from unittest import mock

import pytest
from rag_engine.adapters import oci
from rag_engine.config import get_settings as engine_get_settings
from rag_engine.models.llm import TextOutput
from rag_engine.resources.deadline import (
    Deadline,
    DeadlineExceeded,
    bind_deadline,
    current_deadline,
)

from app.config import Settings
from app.rag import answer_threads
from app.rag.answer_engine import _run_on_loop
from app.rag.answer_threads import run_answer_in_thread
from app.rag.answer_timeout import AnswerTimeoutError, run_answer_with_timeout

_PROVIDER: Any = SimpleNamespace(model="stub-model", label="stub")
# 遅い LLM の 1 回の応答時間（打ち切りの上限より十分長い）。
_SLOW_LLM_SECONDS = 30.0


class _SlowLlm:
    """遅い LLM。呼び出しごとに timeout と再試行の設定を記録し、timeout だけ待って失敗する。"""

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []
        self._lock = threading.Lock()

    def client(self, options: dict[str, Any] | None = None) -> Any:
        owner = self

        class _Responses:
            def parse(self, **kwargs: Any) -> Any:
                with owner._lock:
                    owner.calls.append(dict(options or {}))
                timeout = float((options or {}).get("timeout", _SLOW_LLM_SECONDS))
                time.sleep(min(timeout, _SLOW_LLM_SECONDS))
                raise TimeoutError("LLM の応答が timeout しました。")

        class _Client:
            responses = _Responses()

            def with_options(self, **new_options: Any) -> Any:
                return owner.client(new_options)

        return _Client()


def _engine_settings() -> Any:
    return replace(
        engine_get_settings(environ={}, dotenv_path=None),
        llm_request_timeout_seconds=600,
        llm_retries=3,
        llm_retry_initial_wait_seconds=0.0,
        llm_retry_max_wait_seconds=0.0,
    )


def _answer_flow(llm: _SlowLlm, finished: threading.Event, steps: int = 4) -> str:
    """回答フローの代わり。LLM を何度か呼び、失敗は握りつぶして次の工程へ進む。

    rag_engine の補助の工程（質問の拡張など）も、失敗を握りつぶして回答を続ける。
    """
    settings = _engine_settings()
    try:
        with mock.patch.object(oci, "_openai_client_for_provider", return_value=llm.client()):
            for _ in range(steps):
                with contextlib.suppress(Exception):
                    oci._parse_with_retry(
                        lambda: oci._openai_parsed([], settings, TextOutput, _PROVIDER),
                        "Responses text",
                        settings,
                        retries=oci._llm_transport_retries(settings),
                    )
        return "answer"
    finally:
        finished.set()


@pytest.fixture
def short_grace(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(answer_threads, "ANSWER_DEADLINE_GRACE_SECONDS", 0.05)


@pytest.fixture
async def small_default_executor() -> Any:
    """既定の executor を 1 本にする（DB・OCI SDK の呼び出しの thread が足りない状態）。"""
    loop = asyncio.get_running_loop()
    executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="test-default")
    loop.set_default_executor(executor)
    yield executor
    executor.shutdown(wait=False)


async def test_timeout_stops_llm_calls_in_answer_thread(
    short_grace: None, small_default_executor: Any
) -> None:
    llm = _SlowLlm()
    finished = threading.Event()
    db_elapsed: list[float] = []

    async def operation(_tracker: Any) -> str:
        task = asyncio.create_task(run_answer_in_thread(_answer_flow, llm, finished))
        await asyncio.sleep(0.05)
        # 回答の thread が LLM を待っている間も、既定の executor の処理（DB の呼び出しの
        # 代わり）は返る。
        started = time.monotonic()
        assert await asyncio.to_thread(lambda: "db") == "db"
        db_elapsed.append(time.monotonic() - started)
        return await task

    started = time.monotonic()
    with pytest.raises(AnswerTimeoutError) as raised:
        await run_answer_with_timeout(operation, Settings(), timeout_seconds=0.3)
    assert raised.value.timeout_seconds == 0.3
    assert time.monotonic() - started < 1.0
    assert db_elapsed and db_elapsed[0] < 0.5

    # 打ち切った後、回答の thread は LLM 1 回の timeout（600 秒）を待たずに終わる。
    assert await asyncio.to_thread(finished.wait, 3.0)
    # 呼んだ LLM は 1 回だけで、残りの工程・再試行で LLM を呼ばない。
    assert len(llm.calls) == 1
    [options] = llm.calls
    assert options["max_retries"] == 0
    assert options["timeout"] <= 0.3 + answer_threads.ANSWER_DEADLINE_GRACE_SECONDS


async def test_wait_for_cancel_stops_answer_thread(short_grace: None) -> None:
    llm = _SlowLlm()
    finished = threading.Event()
    with pytest.raises(TimeoutError):
        await asyncio.wait_for(
            run_answer_in_thread(_answer_flow, llm, finished, timeout_seconds=0.2), timeout=0.2
        )
    assert await asyncio.to_thread(finished.wait, 3.0)
    assert len(llm.calls) == 1


async def test_cancelled_task_stops_answer_thread() -> None:
    """チャットの停止（task の取り消し）でも、回答の thread は次の LLM を呼ばない。

    時刻の期限の無い文脈（取り消しだけを伝える期限）でも止まる。
    """
    llm = _SlowLlm()
    finished = threading.Event()

    # 期限が無いと LLM 1 回の timeout（600 秒）になるため、1 回の応答を短くする。
    slow = mock.patch(f"{__name__}._SLOW_LLM_SECONDS", 0.3)
    with slow:
        task = asyncio.create_task(run_answer_in_thread(_answer_flow, llm, finished))
        await asyncio.sleep(0.05)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert await asyncio.to_thread(finished.wait, 3.0)
    assert len(llm.calls) == 1
    assert llm.calls[0]["max_retries"] == 0


async def test_answer_thread_inherits_context() -> None:
    def read() -> float | None:
        deadline = current_deadline()
        assert deadline is not None
        return deadline.remaining()

    remaining = await run_answer_in_thread(read, timeout_seconds=30.0)
    assert remaining is not None
    assert 0 < remaining <= 30.0 + answer_threads.ANSWER_DEADLINE_GRACE_SECONDS


async def test_run_on_loop_cancels_io_after_deadline() -> None:
    loop = asyncio.get_running_loop()
    io_cancelled = asyncio.Event()

    async def slow_io() -> str:
        try:
            await asyncio.sleep(_SLOW_LLM_SECONDS)
        except asyncio.CancelledError:
            io_cancelled.set()
            raise
        return "io"

    def in_thread() -> None:
        with bind_deadline(Deadline(0.2)):
            _run_on_loop(slow_io, loop)

    started = time.monotonic()
    with pytest.raises(DeadlineExceeded):
        await asyncio.to_thread(in_thread)
    assert time.monotonic() - started < 2.0
    await asyncio.wait_for(io_cancelled.wait(), timeout=2.0)


async def test_run_on_loop_does_not_start_io_after_deadline() -> None:
    loop = asyncio.get_running_loop()
    started: list[bool] = []

    async def io() -> str:
        started.append(True)
        return "io"

    def in_thread() -> None:
        deadline = Deadline(30.0)
        deadline.cancel()
        with bind_deadline(deadline):
            _run_on_loop(io, loop)

    with pytest.raises(DeadlineExceeded):
        await asyncio.to_thread(in_thread)
    assert started == []


async def test_run_on_loop_returns_io_result_and_raises_io_timeout() -> None:
    loop = asyncio.get_running_loop()

    async def ok() -> str:
        return "io"

    async def failing() -> str:
        raise TimeoutError("DB の呼び出しが timeout しました。")

    assert await asyncio.to_thread(_run_on_loop, ok, loop) == "io"
    # I/O 自体の TimeoutError は、待ちの上限と取り違えずにそのまま送出する。
    with pytest.raises(TimeoutError, match="DB の呼び出し"):
        await asyncio.wait_for(asyncio.to_thread(_run_on_loop, failing, loop), timeout=2.0)
