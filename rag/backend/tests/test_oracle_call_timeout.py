"""回答の経路の DB の呼出し 1 回の上限(python-oracledb の call_timeout。#5381)。

- ``db_call_timeout`` の範囲の中だけ、共有 pool から借りた接続に上限を当て、pool へ返す前に元の値へ
  戻す(取込・保守などの範囲の外の呼出しに残さない)。
- 範囲は ``asyncio.to_thread`` の thread にも引き継ぐ。
- ``RagPipeline.run`` が設定の値で範囲を作る。
外部 I/O は替え玉(DB には接続しない)。
"""

from __future__ import annotations

import asyncio
from typing import Any, cast

import pytest

from app.clients.oracle import OracleClient
from app.clients.oracle_call_timeout import (
    applied_call_timeout,
    current_db_call_timeout_ms,
    db_call_timeout,
)
from app.config import Settings, get_settings
from app.rag.pipeline import RagPipeline
from app.schemas.search import SearchRequest, SearchResponse


class _Connection:
    """call_timeout を持つ接続の替え玉(SQL の実行の間の値と、close の時の値を残す)。"""

    def __init__(self, call_timeout: int = 0) -> None:
        self.call_timeout = call_timeout
        self.closed_with: list[int] = []
        self.commits = 0
        self.rollbacks = 0

    def commit(self) -> None:
        self.commits += 1

    def rollback(self) -> None:
        self.rollbacks += 1

    def close(self) -> None:
        self.closed_with.append(self.call_timeout)


class _Pool:
    def __init__(self, connection: _Connection) -> None:
        self.connection = connection

    def acquire(self) -> _Connection:
        return self.connection


def _client(connection: _Connection) -> OracleClient:
    return OracleClient(settings=Settings(), pool=_Pool(connection))  # type: ignore[arg-type]


def test_scope_sets_and_restores_call_timeout_on_borrowed_connection() -> None:
    connection = _Connection(call_timeout=0)
    client = _client(connection)
    seen: list[int] = []

    with db_call_timeout(60):
        client._run_with_connection(lambda conn: seen.append(cast(Any, conn).call_timeout))

    assert seen == [60_000]
    # pool へ返す(close)前に元の値へ戻す。
    assert connection.closed_with == [0]


def test_outside_scope_leaves_call_timeout_unchanged() -> None:
    connection = _Connection(call_timeout=0)
    client = _client(connection)
    seen: list[int] = []

    client._run_with_connection(lambda conn: seen.append(cast(Any, conn).call_timeout))

    assert current_db_call_timeout_ms() is None
    assert seen == [0]
    assert connection.closed_with == [0]


def test_failed_call_restores_call_timeout_before_returning_connection() -> None:
    connection = _Connection(call_timeout=0)
    client = _client(connection)

    def timeout(conn: Any) -> None:
        assert conn.call_timeout == 1_500
        raise RuntimeError("DPY-4024: call timeout of 1500 ms exceeded")

    with db_call_timeout(1.5), pytest.raises(RuntimeError, match="DPY-4024"):
        client._run_with_connection(timeout)

    assert connection.call_timeout == 0
    assert connection.closed_with == [0]


def test_transaction_applies_call_timeout_through_commit_and_restores() -> None:
    connection = _Connection(call_timeout=0)
    client = _client(connection)
    seen: list[int] = []

    def operation(conn: Any) -> str:
        seen.append(cast(Any, conn).call_timeout)
        return "ok"

    with db_call_timeout(30):
        assert client._run_transaction_sync(operation) == "ok"

    assert seen == [30_000]
    assert connection.commits == 1
    assert connection.closed_with == [0]


def test_shorter_existing_call_timeout_is_kept() -> None:
    connection = _Connection(call_timeout=5_000)

    with db_call_timeout(60), applied_call_timeout(connection):
        assert connection.call_timeout == 5_000
    with db_call_timeout(2), applied_call_timeout(connection):
        assert connection.call_timeout == 2_000
    assert connection.call_timeout == 5_000


def test_connection_without_call_timeout_is_used_as_is() -> None:
    connection = object()

    with db_call_timeout(60), applied_call_timeout(connection):
        assert not hasattr(connection, "call_timeout")


@pytest.mark.parametrize("seconds", [None, 0, -1], ids=["none", "zero", "negative"])
def test_non_positive_scope_has_no_limit(seconds: float | None) -> None:
    with db_call_timeout(seconds):
        assert current_db_call_timeout_ms() is None


async def test_scope_reaches_db_thread() -> None:
    """``asyncio.to_thread``(既定の DB の runner)の thread にも範囲が引き継がれる。"""
    connection = _Connection(call_timeout=0)
    client = _client(connection)
    seen: list[int] = []

    with db_call_timeout(45):
        await client._db_call_runner(
            lambda: client._run_with_connection(
                lambda conn: seen.append(cast(Any, conn).call_timeout)
            )
        )
    await asyncio.to_thread(
        lambda: client._run_with_connection(lambda conn: seen.append(cast(Any, conn).call_timeout))
    )

    assert seen == [45_000, 0]


async def test_pipeline_run_binds_configured_db_call_timeout(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = get_settings().model_copy(update={"rag_db_call_timeout_seconds": 7.0})
    pipeline = RagPipeline(settings=settings)
    request = SearchRequest(query="合成の質問")
    seen: list[int | None] = []

    async def run(_self: Any, *_args: Any, **_kwargs: Any) -> SearchResponse:
        seen.append(current_db_call_timeout_ms())
        return SearchResponse(
            query=request.query, answer="合成回答", citations=[], trace_id="trace-a", elapsed_ms=0
        )

    monkeypatch.setattr(RagPipeline, "_run", run)
    await pipeline.run(request)

    assert seen == [7_000]
    assert current_db_call_timeout_ms() is None


def test_db_call_timeout_setting_default_and_range() -> None:
    assert Settings().rag_db_call_timeout_seconds == 60.0
    with pytest.raises(ValueError):
        Settings(rag_db_call_timeout_seconds=0)
    with pytest.raises(ValueError):
        Settings(rag_db_call_timeout_seconds=1201)
