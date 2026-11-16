"""REST の検索で DB の読み取りが失敗したら、原因ごとの status と error_code で返す（#5233）。

すべての検索一覧が失敗したときの例外は、最初の失敗の例外を原因に結ぶ。
REST は原因の連鎖に ORA / DPY のコードがあるときだけ一覧の読み取りと同じ分類
（messaging.md §3.6.1）で返し、コードの無い例外は共通の 500 のまま。
SSE は文と工程を変えず、`error_code` に DB のコードを入れる。値・文は合成。
"""

from __future__ import annotations

import json

import pytest
from pytest import MonkeyPatch
from rag_engine.generation.execution_record import AnswerExecutionError

from app.api.errors import (
    DATABASE_READ_FAILED_CODE,
    DATABASE_READ_RETRY_AFTER_SECONDS,
    DATABASE_READ_TIMEOUT_CODE,
    DATABASE_UNAVAILABLE_CODE,
)
from app.api.routes import search as search_route
from app.clients.oracle import SearchHits
from app.main import UNHANDLED_ERROR_MESSAGE, app
from app.rag.answer_engine import _SearchState
from app.schemas.search import RetrievedChunk, SearchMode
from tests.support import AsgiTestClient
from tests.test_answer_degraded_steps import _degraded_search, _EmbedFails, _SearchLog

client = AsgiTestClient(app, raise_app_exceptions=False)


class _SyntheticDriverError(Exception):
    """driver の例外の代役（文の中の ORA / DPY のコードだけを持つ）。"""


def _pipeline_failing_with(cause: BaseException | None) -> type:
    class _Pipeline:
        def __init__(self, **_kwargs: object) -> None:
            pass

        async def run(self, *_args: object, **_kwargs: object) -> object:
            try:
                raise RuntimeError("すべての検索一覧の取得に失敗しました。") from cause
            except RuntimeError as exc:
                raise AnswerExecutionError(str(exc), "合成の質問") from exc

    return _Pipeline


@pytest.mark.parametrize(
    ("message", "status", "code"),
    [
        ("DPY-4011: 合成の接続の切断", 503, DATABASE_UNAVAILABLE_CODE),
        ("DPY-4005: 合成の pool の待ち", 503, DATABASE_READ_TIMEOUT_CODE),
        ("ORA-00942: 合成の表が無い", 500, DATABASE_READ_FAILED_CODE),
    ],
    ids=["connection", "pool-wait", "other-oracle"],
)
def test_rest_search_returns_the_database_failure_by_cause(
    monkeypatch: MonkeyPatch, message: str, status: int, code: str
) -> None:
    monkeypatch.setattr(
        search_route, "RagPipeline", _pipeline_failing_with(_SyntheticDriverError(message))
    )

    response = client.post("/api/search", json={"query": "合成の質問"})

    assert response.status_code == status
    body = response.json()
    assert body["error_code"] == code
    assert body["error_messages"] != [UNHANDLED_ERROR_MESSAGE]
    assert message not in json.dumps(body, ensure_ascii=False)
    if status == 503:
        assert response.headers["Retry-After"] == str(DATABASE_READ_RETRY_AFTER_SECONDS)


def test_rest_search_keeps_the_common_500_without_a_database_code(
    monkeypatch: MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        search_route, "RagPipeline", _pipeline_failing_with(ValueError("合成の失敗"))
    )

    response = client.post("/api/search", json={"query": "合成の質問"})

    assert response.status_code == 500
    assert response.json()["error_messages"] == [UNHANDLED_ERROR_MESSAGE]
    assert "error_code" not in response.json()


def test_stream_search_keeps_the_message_and_adds_the_database_code(
    monkeypatch: MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        search_route,
        "RagPipeline",
        _pipeline_failing_with(_SyntheticDriverError("DPY-4011: 合成の接続の切断")),
    )

    response = client.post("/api/search/stream", json={"query": "合成の質問"})

    block = response.text.split("event: error\n", 1)[1]
    event = json.loads(block.split("\n", 1)[0].removeprefix("data: "))
    assert event["message"] == search_route.STREAM_ERROR_MESSAGE
    assert event["error_code"] == "DPY-4011"


class _FailingSearch(_SearchLog):
    """検索の一覧をすべて、原因の例外つきの失敗で返す。"""

    async def hybrid_search(
        self,
        query: str,
        embedding: list[float],
        top_k: int,
        mode: SearchMode = SearchMode.HYBRID,
        filters: dict[str, str] | None = None,
    ) -> list[RetrievedChunk]:
        hits = SearchHits()
        hits.status = "failed"
        hits.failure = _SyntheticDriverError("DPY-4011: 合成の接続の切断")
        return hits


async def test_all_failed_search_lists_keep_the_first_failure_as_the_cause() -> None:
    with pytest.raises(RuntimeError, match="すべての検索一覧") as caught:
        await _degraded_search(_FailingSearch(), _EmbedFails(), _SearchState(), ["原質問"])

    assert isinstance(caught.value.__cause__, _SyntheticDriverError)
    assert "DPY-4011" in str(caught.value.__cause__)
