"""worker thread から event loop へ非同期処理を投げる（#5764）。

回答・取込の同期処理（``rag_engine``）は worker thread で動き、I/O・進捗の通知は event loop で
行う。``asyncio.run_coroutine_threadsafe`` は投げる側の thread で coroutine を作るため、loop が
止まった・閉じた（評価の打ち切りの後に process を終える場合など）ときに、await されない coroutine
が残って ``RuntimeWarning: coroutine ... was never awaited`` になる。ここでは coroutine を loop の
thread で作る。
"""

from __future__ import annotations

import asyncio
import concurrent.futures
import contextlib
from collections.abc import Awaitable, Callable


def submit_to_loop[R](
    factory: Callable[[], Awaitable[R]], loop: asyncio.AbstractEventLoop
) -> concurrent.futures.Future[R]:
    """``factory()`` を ``loop`` で始め、結果の future を待たずに返す。

    ``factory`` は loop の thread で呼ぶ。loop が止まっていて始まらなければ coroutine を作らない。
    閉じた loop には ``RuntimeError`` を送出する（coroutine は作らない）。返した future を取り消す
    と、loop の task も取り消す。
    """
    result: concurrent.futures.Future[R] = concurrent.futures.Future()

    def cancel_task(task: asyncio.Future[R]) -> None:
        if loop.is_closed():
            return
        # 確かめた後に閉じた loop では task も止まっている。
        with contextlib.suppress(RuntimeError):
            loop.call_soon_threadsafe(task.cancel)

    def copy(task: asyncio.Future[R]) -> None:
        if task.cancelled():
            result.cancel()
            return
        if not result.set_running_or_notify_cancel():
            return
        error = task.exception()
        if error is not None:
            result.set_exception(error)
        else:
            result.set_result(task.result())

    def start() -> None:
        if result.cancelled():
            return
        try:
            task = asyncio.ensure_future(factory())
        except Exception as exc:  # noqa: BLE001 - 呼び出し側の future へ渡す
            if result.set_running_or_notify_cancel():
                result.set_exception(exc)
            return
        task.add_done_callback(copy)
        result.add_done_callback(lambda done: cancel_task(task) if done.cancelled() else None)

    loop.call_soon_threadsafe(start)
    return result
