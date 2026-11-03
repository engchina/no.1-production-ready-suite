"""DB の読み取りの失敗を、空のデータではなく API の失敗として返すテスト（#4217）。"""

import asyncio
from collections.abc import Awaitable, Callable

import pytest

from app.api.errors import (
    DATABASE_READ_FAILED_CODE,
    DATABASE_READ_TIMEOUT_CODE,
    DATABASE_UNAVAILABLE_CODE,
    DatabaseReadFailedError,
    DatabaseReadTimeoutError,
    DatabaseUnavailableError,
)
from app.api.routes import chat as chat_route
from app.api.routes import documents as documents_route
from app.api.routes import knowledge_bases as knowledge_bases_route
from app.api.routes import search_answer_profiles as search_answer_profiles_route
from app.config import get_settings
from app.db_read_failures import read_or_raise
from app.main import app
from tests.support import AsgiTestClient

client = AsgiTestClient(app)


class _RaisingOracle:
    """全 DB 呼び出しが渡された例外を投げる fake。"""

    def __init__(self, error: Exception) -> None:
        self._error = error

    def __getattr__(self, _name: str) -> Callable[..., Awaitable[object]]:
        async def _raise(*_args: object, **_kwargs: object) -> object:
            raise self._error

        return _raise


class _HangingOracle:
    """全 DB 呼び出しが返らない fake（DB の応答待ちを再現する）。"""

    def __getattr__(self, _name: str) -> Callable[..., Awaitable[object]]:
        async def _hang(*_args: object, **_kwargs: object) -> object:
            await asyncio.sleep(60)
            raise AssertionError("unreachable")

        return _hang


LIST_ROUTES = [
    ("/api/documents", documents_route),
    ("/api/documents/ingestion-jobs", documents_route),
    ("/api/knowledge-bases", knowledge_bases_route),
    ("/api/knowledge-bases/kb-a/graph", knowledge_bases_route),
    ("/api/search-answer-profiles", search_answer_profiles_route),
    ("/api/chat/conversations?search_answer_profile_id=profile-a", chat_route),
]


def _assert_failure(response, *, status: int, code: str) -> None:
    assert response.status_code == status
    body = response.json()
    assert body["error_code"] == code
    # 読み取れなかったときに空のデータを返さない（画面が 0 件と取り違える）。
    assert body["data"] is None
    assert body["warning_messages"] == []
    assert body["error_messages"]


@pytest.mark.parametrize(("path", "route"), LIST_ROUTES)
def test_list_timeout_is_503_with_code(
    monkeypatch: pytest.MonkeyPatch, path: str, route: object
) -> None:
    """応答の遅い読み取りは 503 + `database_read_timeout` で返す。"""
    monkeypatch.setattr(get_settings(), "db_read_timeout_seconds", 0.01)
    monkeypatch.setattr(route, "OracleClient", lambda: _HangingOracle())

    response = client.get(path)

    _assert_failure(response, status=503, code=DATABASE_READ_TIMEOUT_CODE)
    assert response.headers["retry-after"] == "5"


@pytest.mark.parametrize(("path", "route"), LIST_ROUTES)
def test_list_connection_failure_is_503_with_code(
    monkeypatch: pytest.MonkeyPatch, path: str, route: object
) -> None:
    """接続の失敗は 503 + `database_unavailable` で返す。"""
    error = RuntimeError("DPY-6005: cannot connect to database")
    monkeypatch.setattr(route, "OracleClient", lambda: _RaisingOracle(error))

    _assert_failure(client.get(path), status=503, code=DATABASE_UNAVAILABLE_CODE)


@pytest.mark.parametrize(("path", "route"), LIST_ROUTES)
def test_list_other_failure_does_not_claim_connection_failure(
    monkeypatch: pytest.MonkeyPatch, path: str, route: object
) -> None:
    """SQL の失敗などは「接続できない」と言わない。"""
    error = RuntimeError("ORA-00942: table or view does not exist")
    monkeypatch.setattr(route, "OracleClient", lambda: _RaisingOracle(error))

    _assert_failure(client.get(path), status=500, code=DATABASE_READ_FAILED_CODE)


async def test_read_or_raise_returns_value() -> None:
    async def loader() -> str:
        return "value"

    assert await read_or_raise(loader, timeout_seconds=1, log_label="test") == "value"


async def test_read_or_raise_treats_pool_wait_timeout_as_timeout() -> None:
    """pool の待ちの timeout は DB が混んでいる状態で、接続の失敗と言わない。"""

    async def loader() -> str:
        raise RuntimeError("DPY-4005: timed out waiting for the connection pool")

    with pytest.raises(DatabaseReadTimeoutError):
        await read_or_raise(loader, timeout_seconds=1, log_label="test")


@pytest.mark.parametrize(
    ("message", "expected"),
    [
        ("ORA-12541: no listener", DatabaseUnavailableError),
        ("database is down", DatabaseReadFailedError),
    ],
)
async def test_read_or_raise_classifies_by_oracle_code(message: str, expected: type) -> None:
    async def loader() -> str:
        raise RuntimeError(message)

    with pytest.raises(expected):
        await read_or_raise(loader, timeout_seconds=1, log_label="test")


@pytest.mark.parametrize("error", [TypeError("bug"), AttributeError("bug")])
async def test_read_or_raise_does_not_hide_programming_errors(error: Exception) -> None:
    """プログラムの誤りは DB の失敗に変えず、そのまま投げる（#476）。"""

    async def loader() -> object:
        raise error

    with pytest.raises(type(error)):
        await read_or_raise(loader, timeout_seconds=1, log_label="test")


async def test_read_or_raise_keeps_http_exceptions() -> None:
    """404 などの意図した応答は、そのまま返す（#476）。"""
    from fastapi import HTTPException

    async def loader() -> object:
        raise HTTPException(status_code=404, detail="not found")

    with pytest.raises(HTTPException):
        await read_or_raise(loader, timeout_seconds=1, log_label="test")
