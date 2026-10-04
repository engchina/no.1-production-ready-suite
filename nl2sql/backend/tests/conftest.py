"""pytest 共通設定。

ローカル `.env` は実 Oracle smoke 用に `NL2SQL_RUNTIME_MODE=oracle` へ切り替えられる。
単体テストは CI と同じ deterministic/memory 実行に固定し、開発者環境の `.env` に依存させない。
"""

from __future__ import annotations

import asyncio
import contextvars
import copy
import functools
import os
import threading
from collections.abc import Callable, Iterator
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
# 本番の Argon2 のコスト（time_cost=3・64 MiB・並列 4）は 1 回のハッシュに約 70ms かかる。
# テストは RAG / Agent のテスト（security_support.py）と同じ軽い値にする（#344）。
os.environ["PLATFORM_AUTH_ARGON2_TIME_COST"] = "1"
os.environ["PLATFORM_AUTH_ARGON2_MEMORY_KIB"] = "8192"
os.environ["PLATFORM_AUTH_ARGON2_PARALLELISM"] = "1"


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
def _restore_settings_singleton() -> Iterator[None]:
    """Settings の singleton の値を、テストの後にテストの前の値へ戻す。

    システム設定の API（データベース設定の PATCH など）は singleton の属性をその場で書き換える。
    monkeypatch で先に退避していない属性（`oracle_user` など）は次のテストに残り、
    実行の順序や xdist の振り分けで結果が変わっていた（例: 現在の schema owner が `ADMIN`）。
    ほかの autouse の fixture（monkeypatch を使うもの）より先に定義し、最後に戻す。
    """
    from app.settings import _settings_singleton

    settings = _settings_singleton()
    saved_fields = copy.deepcopy(settings.__dict__)
    saved_private = copy.deepcopy(settings.__pydantic_private__)
    saved_fields_set = set(settings.__pydantic_fields_set__)
    yield
    settings.__dict__.clear()
    settings.__dict__.update(saved_fields)
    object.__setattr__(settings, "__pydantic_private__", saved_private)
    object.__setattr__(settings, "__pydantic_fields_set__", saved_fields_set)


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


@pytest.fixture(autouse=True)
def _fresh_nl2sql_service_state() -> None:
    """module の singleton `nl2sql_service` の状態をテストごとに初期状態へ戻す。

    テストの singleton は memory store で動き、catalog・プロファイル・ジョブ・履歴を process の
    中で持ち続ける。API を通すテスト（サンプルの取り込み・catalog の作り直しなど）の書き込みが
    同じ process の後のテストに残り、実行の順序や xdist の振り分けで結果が変わっていた。
    router・MCP・quality evaluation などが同じ object を import しているため、module の属性は
    差し替えず、新しく作った service の状態で中身だけを置き換える（`__dict__` の代入 1 回）。
    """
    from app.features.nl2sql.service import Nl2SqlService, nl2sql_service

    nl2sql_service.__dict__ = Nl2SqlService().__dict__


@pytest.fixture(autouse=True)
def _no_retry_wait(monkeypatch: pytest.MonkeyPatch) -> None:
    """retry の間の待ちを実時間で待たない（#344）。

    逆生成の段階 retry は 1 秒 → 2 秒待つため、失敗の経路を通るテストが 1 件 3 秒かかっていた。
    待ち時間そのものを確かめるテストは、テストの中で `_retry_sleep` を差し替え直してよい。
    """
    import app.features.nl2sql.reverse_generation as reverse_generation

    monkeypatch.setattr(reverse_generation, "_retry_sleep", lambda _seconds: None)


@pytest.fixture(autouse=True)
def _fresh_database_status_cache() -> Iterator[None]:
    """DB の状態 API の `ok` の cache（#793）をテストごとに捨てる（テストの順序に依らない）。"""
    from pr_system_settings.database_status import clear_database_status_cache

    clear_database_status_cache()
    yield
    clear_database_status_cache()
