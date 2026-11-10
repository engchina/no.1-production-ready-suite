"""保存指示の読取失敗と合法な未編集を分け、下流実行前に止める。"""

import asyncio
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest
from fastapi import Depends, FastAPI
from rag_engine.resources.runtime import resolve_prompt, saved_prompt_overrides
from starlette.requests import Request

from app.rag import prompt_runtime

_KEY = "evaluation.coverage"


def _app(seen: list[str]) -> FastAPI:
    app = FastAPI(dependencies=[Depends(prompt_runtime.published_prompt_dependency)])

    @app.api_route("/{path:path}", methods=["GET", "POST", "PUT", "PATCH", "DELETE"])
    async def endpoint(path: str) -> dict[str, str]:
        await asyncio.sleep(0)
        value = resolve_prompt(_KEY, "builtin")
        seen.append(value)
        return {"prompt": value}

    return app


async def _request(app: FastAPI, method: str, path: str) -> httpx.Response:
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app, raise_app_exceptions=False), base_url="http://test"
    ) as client:
        return await client.request(method, path)


@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("POST", "/api/search"),
        ("PUT", "/api/documents/d1"),
        ("PATCH", "/api/history/run-a"),
    ],
)
async def test_failed_read_never_runs_downstream(
    monkeypatch: pytest.MonkeyPatch, method: str, path: str
) -> None:
    reader = AsyncMock(side_effect=RuntimeError("synthetic read failure"))
    monkeypatch.setattr(
        prompt_runtime, "OracleClient", lambda: SimpleNamespace(answer_prompt_overrides=reader)
    )
    seen: list[str] = []
    response = await _request(_app(seen), method, path)
    assert response.status_code == 500
    assert seen == []
    reader.assert_awaited_once()
    assert saved_prompt_overrides() is None


@pytest.mark.parametrize("method", ["GET", "DELETE"])
async def test_other_methods_do_not_read_configuration(
    monkeypatch: pytest.MonkeyPatch, method: str
) -> None:
    reader = AsyncMock(side_effect=RuntimeError("not called"))
    monkeypatch.setattr(
        prompt_runtime, "OracleClient", lambda: SimpleNamespace(answer_prompt_overrides=reader)
    )
    seen: list[str] = []
    response = await _request(_app(seen), method, "/api/documents")
    assert response.status_code == 200
    assert seen == ["builtin"]
    reader.assert_not_awaited()


async def test_successful_empty_overrides_allow_builtin(monkeypatch: pytest.MonkeyPatch) -> None:
    reader = AsyncMock(return_value={})
    monkeypatch.setattr(
        prompt_runtime, "OracleClient", lambda: SimpleNamespace(answer_prompt_overrides=reader)
    )
    seen: list[str] = []
    response = await _request(_app(seen), "POST", "/api/search")
    assert response.status_code == 200
    assert seen == ["builtin"]
    reader.assert_awaited_once()
    assert saved_prompt_overrides() is None


async def test_successful_overrides_are_isolated(monkeypatch: pytest.MonkeyPatch) -> None:
    reader = AsyncMock(side_effect=[{_KEY: "first"}, {_KEY: "second"}])
    monkeypatch.setattr(
        prompt_runtime, "OracleClient", lambda: SimpleNamespace(answer_prompt_overrides=reader)
    )
    seen: list[str] = []
    results = await asyncio.gather(
        _request(_app(seen), "POST", "/api/search"),
        _request(_app(seen), "POST", "/api/search"),
    )
    assert [response.status_code for response in results] == [200, 200]
    assert sorted(seen) == ["first", "second"]
    assert saved_prompt_overrides() is None
    assert resolve_prompt(_KEY, "builtin") == "builtin"


async def test_exception_releases_saved_scope(monkeypatch: pytest.MonkeyPatch) -> None:
    reader = AsyncMock(return_value={_KEY: "saved"})
    monkeypatch.setattr(
        prompt_runtime, "OracleClient", lambda: SimpleNamespace(answer_prompt_overrides=reader)
    )
    request = Request({"type": "http", "method": "POST", "path": "/api/search", "headers": []})
    with pytest.raises(RuntimeError, match="downstream"):
        async with asynccontextmanager(prompt_runtime.published_prompt_dependency)(request):
            assert saved_prompt_overrides() == {_KEY: "saved"}
            raise RuntimeError("downstream")
    assert saved_prompt_overrides() is None
    assert resolve_prompt(_KEY, "builtin") == "builtin"
