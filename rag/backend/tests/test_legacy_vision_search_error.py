"""旧い形式の画像説明の chunk で、検索を 500 にせず再処理が要る文書を返す（#4550）。

回答本文を分離していない旧い画像説明の chunk（`figure_text_source=vision` で
`answer_text_projection` が無い）は推測で復元も除外もしない。検索・回答は止めるが、REST は
`error_code` つきの 409、SSE・チャット・MCP・品質評価は同じ文で、再解析・再分割が要る文書を示す。
文書名と本文は合成の値。
"""

from __future__ import annotations

import json
import logging
from typing import Any, cast

import pytest
from pr_backend_core.mcp import McpToolError, mcp_error_from_exception
from pytest import LogCaptureFixture, MonkeyPatch
from rag_engine.generation.answer_records import _stored_chunk_answer_record
from rag_engine.generation.execution_record import AnswerExecutionError
from rag_parser_core.vision_text import (
    UNPROJECTED_VISION_DOCUMENTS_SHOWN,
    UnprojectedVisionSourceError,
)

from app.api.errors import DocumentReprocessRequiredError
from app.api.routes import chat as chat_route
from app.api.routes import search as search_route
from app.config import Settings
from app.main import app
from app.rag.answer_engine import AnswerEngine, _SearchState, _stored_child
from app.rag.answer_failure import DOCUMENT_REPROCESS_REQUIRED_CODE, describe_answer_failure
from app.schemas.search import RetrievedChunk, SearchRequest, SearchResponse
from tests.support import AsgiTestClient
from tests.test_answer_engine import FakeGenAi, FakeOracle

# 未処理の例外も応答（500）として受け取り、修正前の挙動と比べられるようにする。
client = AsgiTestClient(app, raise_app_exceptions=False)


def _legacy_hit(document_id: str, file_name: str, chunk_id: str) -> RetrievedChunk:
    return RetrievedChunk(
        document_id=document_id,
        chunk_id=chunk_id,
        text="合成の旧画像説明",
        score=1.0,
        file_name=file_name,
        metadata={"figure_text_source": "vision", "engine_search_text": "合成の旧画像説明"},
    )


def _current_hit(chunk_id: str) -> RetrievedChunk:
    return RetrievedChunk(
        document_id="document-c",
        chunk_id=chunk_id,
        text="合成の本文",
        score=0.5,
        file_name="文書C.pdf",
        metadata={"figure_text_source": "ocr"},
    )


class _LegacyPipeline:
    """保存済みの旧い chunk を検索の候補に変換したところで止まる pipeline（本番と同じ関数）。"""

    def __init__(self, **_kwargs: object) -> None:
        pass

    async def run(self, *_args: object, **_kwargs: object) -> SearchResponse:
        _stored_child(_legacy_hit("document-a", "文書A.pdf", "chunk-a"), rrf_score=1.0)
        raise AssertionError("旧い chunk は候補にしない")


def _error_event(text: str) -> dict[str, object]:
    block = text.split("event: error\n", 1)[1]
    event: dict[str, object] = json.loads(block.split("\n", 1)[0].removeprefix("data: "))
    return event


def test_rest_search_returns_409_with_document_names_instead_of_500(
    monkeypatch: MonkeyPatch, caplog: LogCaptureFixture
) -> None:
    monkeypatch.setattr(search_route, "RagPipeline", _LegacyPipeline)

    with caplog.at_level(logging.WARNING):
        response = client.post("/api/search", json={"query": "合成の質問"})

    assert response.status_code == 409
    body = response.json()
    assert body["error_code"] == DOCUMENT_REPROCESS_REQUIRED_CODE
    [message] = body["error_messages"]
    assert "「文書A.pdf」" in message
    assert "再解析または再分割" in message
    # ログは件数と code だけで、文書の名前・本文を残さない。
    record = next(
        r for r in caplog.records if r.getMessage() == "rag_search_document_reprocess_required"
    )
    assert record.__dict__["document_count"] == 1
    assert all("文書A.pdf" not in r.getMessage() for r in caplog.records)
    assert all("文書A.pdf" not in json.dumps(r.__dict__, default=str) for r in caplog.records)


def test_stream_search_returns_the_reprocess_message_and_code(monkeypatch: MonkeyPatch) -> None:
    monkeypatch.setattr(search_route, "RagPipeline", _LegacyPipeline)

    response = client.post("/api/search/stream", json={"query": "合成の質問"})

    assert response.status_code == 200
    event = _error_event(response.text)
    assert event["error_type"] == "UnprojectedVisionSourceError"
    assert event["error_code"] == DOCUMENT_REPROCESS_REQUIRED_CODE
    assert "「文書A.pdf」" in str(event["message"])
    assert search_route.STREAM_ERROR_MESSAGE not in str(event["message"])


async def test_mcp_search_returns_a_tool_error_with_the_reprocess_code(
    monkeypatch: MonkeyPatch,
) -> None:
    from app.mcp import tools as mcp_tools

    monkeypatch.setattr(search_route, "RagPipeline", _LegacyPipeline)

    with pytest.raises(DocumentReprocessRequiredError) as caught:
        await mcp_tools._run_search(SearchRequest(query="合成の質問"))

    converted = mcp_error_from_exception(caught.value)
    assert isinstance(converted, McpToolError)
    assert (converted.code, converted.status) == (DOCUMENT_REPROCESS_REQUIRED_CODE, 409)
    assert "「文書A.pdf」" in converted.message


def test_chat_and_wrapped_engine_failures_keep_the_reprocess_message() -> None:
    # rag_engine の回答の工程で止まった失敗（実行記録の例外が包む）も同じ文にする。
    chunk = _stored_child(_current_hit("chunk-c"), rrf_score=1.0)
    chunk.metadata["figure_text_source"] = "vision"
    with pytest.raises(UnprojectedVisionSourceError) as caught:
        _stored_chunk_answer_record(chunk)
    wrapped = AnswerExecutionError("合成の失敗", "合成の質問")
    wrapped.__cause__ = caught.value

    failure = describe_answer_failure(wrapped, "定型文")

    assert failure.error_code == DOCUMENT_REPROCESS_REQUIRED_CODE
    assert failure.message == str(caught.value)
    assert "再解析または再分割" in failure.message
    assert chat_route._chat_error_message(wrapped) == failure.message


async def test_search_names_every_legacy_document_in_the_candidates() -> None:
    """最初の 1 件で止めず、候補の旧い chunk の文書をまとめて示す。旧い chunk を黙って外さない。"""

    class LegacyOracle(FakeOracle):
        def __init__(self) -> None:
            super().__init__()
            self.chunks = [
                _legacy_hit("document-a", "文書A.pdf", "chunk-a1"),
                _current_hit("chunk-c"),
                _legacy_hit("document-b", "文書B.pdf", "chunk-b"),
                _legacy_hit("document-a", "文書A.pdf", "chunk-a2"),
            ]

        async def hybrid_search(self, *_args: Any, **_kwargs: Any) -> list[RetrievedChunk]:
            return list(self.chunks)

    engine = AnswerEngine(
        Settings(), oracle=cast(Any, LegacyOracle()), genai=cast(Any, FakeGenAi())
    )

    with pytest.raises(UnprojectedVisionSourceError) as caught:
        await engine._search(
            SearchRequest(query="合成の質問"),
            _SearchState(),
            retrieval_queries=["合成の質問"],
            candidate_limit=4,
            expand_references=False,
            defer_context=True,
        )

    assert caught.value.documents == ("文書A.pdf", "文書B.pdf")
    assert "「文書A.pdf」、「文書B.pdf」" in str(caught.value)


def test_message_bounds_the_listed_documents() -> None:
    names = [f"文書{index}.pdf" for index in range(UNPROJECTED_VISION_DOCUMENTS_SHOWN + 3)]

    message = str(UnprojectedVisionSourceError(names))

    assert f"「文書{UNPROJECTED_VISION_DOCUMENTS_SHOWN - 1}.pdf」ほか 3 件" in message
    assert f"文書{UNPROJECTED_VISION_DOCUMENTS_SHOWN}.pdf" not in message
