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

import pytest

os.environ["NL2SQL_ENABLE_METRICS"] = "false"
os.environ["NL2SQL_DEBUG"] = "false"
# 開発者の共通 .env（platform/.env）を Settings に読ませない（#211）。
os.environ["PLATFORM_ENV_FILE"] = os.path.join(
    os.path.dirname(__file__), "__missing_platform_env__", ".env"
)
os.environ["NL2SQL_SYNTHETIC_WORKER_MODE"] = "external"
os.environ["NL2SQL_RUNTIME_MODE"] = "deterministic"
os.environ["NL2SQL_PERSISTENCE_MODE"] = "memory"
os.environ["NL2SQL_SELECT_AI_CREDENTIAL_NAME"] = ""
os.environ["NL2SQL_APP_AUTH_ENABLED"] = "false"
os.environ["PLATFORM_ORACLE_USER"] = "APP"
os.environ["NL2SQL_ORACLE_DEEPSEC_ENABLED"] = "false"


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

    fastapi_dependency_utils.run_in_threadpool = _run_sync_in_test_thread  # type: ignore[attr-defined, assignment]
    fastapi_routing.run_in_threadpool = _run_sync_in_test_thread  # type: ignore[attr-defined, assignment]
    starlette_concurrency.run_in_threadpool = _run_sync_in_test_thread  # type: ignore[assignment]


_install_test_threadpool_for_asgi_tests()


@pytest.fixture(autouse=True)
def _isolate_model_secret_env_file(tmp_path: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    """設定の読み書きを、開発者の backend/.env と共通 .env から切り離す（#103 / #211）。

    共通 .env（API key・システム設定画面の保存先）と model-settings.json は tmp_path に置く。
    """
    import app.security.deepsec as deepsec_module
    import app.settings as app_settings

    monkeypatch.setattr(app_settings, "BACKEND_ENV_FILE", tmp_path / "backend.env")
    monkeypatch.setattr(app_settings, "PLATFORM_ENV_FILE", tmp_path / "platform.env")
    monkeypatch.setattr(deepsec_module, "_BACKEND_ENV_FILE", tmp_path / "backend.env")
