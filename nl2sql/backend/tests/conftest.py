"""pytest 共通設定。

ローカル `.env` は実 Oracle smoke 用に `NL2SQL_RUNTIME_MODE=oracle` へ切り替えられる。
単体テストは CI と同じ deterministic/memory 実行に固定し、開発者環境の `.env` に依存させない。
"""

from __future__ import annotations

import asyncio
import contextvars
import functools
import os
import threading
from collections.abc import Callable
from typing import Any

os.environ["ENABLE_METRICS"] = "false"
os.environ["DEBUG"] = "false"
os.environ["NL2SQL_RUNTIME_MODE"] = "deterministic"
os.environ["NL2SQL_PERSISTENCE_MODE"] = "memory"
os.environ["NL2SQL_SELECT_AI_CREDENTIAL_NAME"] = ""
os.environ["APP_AUTH_ENABLED"] = "false"
os.environ["ORACLE_USER"] = "APP"
os.environ["ORACLE_DEEPSEC_ENABLED"] = "false"


async def _run_sync_in_test_thread[T](
    function: Callable[..., T],
    *args: Any,
    **kwargs: Any,
) -> T:
    """Run sync FastAPI callables off-loop without AnyIO's test-time worker hang."""

    loop = asyncio.get_running_loop()
    future: asyncio.Future[T] = loop.create_future()
    context = contextvars.copy_context()
    call = functools.partial(function, *args, **kwargs)

    def complete(ok: bool, value: T | BaseException) -> None:
        if future.cancelled():
            return
        if ok:
            future.set_result(value)  # type: ignore[arg-type]
            return
        future.set_exception(value)  # type: ignore[arg-type]

    def run() -> None:
        try:
            result = context.run(call)
        except BaseException as exc:
            loop.call_soon_threadsafe(complete, False, exc)
            return
        loop.call_soon_threadsafe(complete, True, result)

    threading.Thread(target=run, name="pytest-fastapi-sync-call", daemon=True).start()
    while not future.done():
        await asyncio.sleep(0.001)
    return future.result()


def _install_test_threadpool_for_asgi_tests() -> None:
    import fastapi.dependencies.utils as fastapi_dependency_utils
    import fastapi.routing as fastapi_routing
    import starlette.concurrency as starlette_concurrency

    fastapi_dependency_utils.run_in_threadpool = _run_sync_in_test_thread
    fastapi_routing.run_in_threadpool = _run_sync_in_test_thread
    starlette_concurrency.run_in_threadpool = _run_sync_in_test_thread


_install_test_threadpool_for_asgi_tests()
