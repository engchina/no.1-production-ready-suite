"""LLM の応答が送り直しても未完了だった回答の失敗の返し方（#2023）。

検索（REST・SSE）・チャット・MCP・品質評価の 1 ケースは、未完了の理由ごとの文（何が起きたかと
直し方）を返し、技術的な値（理由・出力の上限・使った token・推論の量）は「詳細」の項目に分ける。
予期しない例外は定型文のまま（内部の文を出さない）。未完了の応答は adapter
（`rag_engine.adapters.oci`）が作る形のスタブで再現する。
"""

from __future__ import annotations

import json
import logging
from types import SimpleNamespace

import pytest
from pr_backend_core.mcp import mcp_error_from_exception
from pytest import LogCaptureFixture, MonkeyPatch
from rag_engine.adapters import oci
from rag_engine.generation.execution_record import AnswerExecutionError

from app.api.routes import chat as chat_route
from app.api.routes import search as search_route
from app.main import app
from app.rag.answer_failure import (
    ANSWER_INCOMPLETE_ERROR_CODE,
    ANSWER_INCOMPLETE_ERROR_TYPE,
    describe_answer_failure,
)
from app.rag.evaluation import EVALUATION_CASE_ERROR_MESSAGE, EvaluationRunner
from app.schemas.evaluation import EvaluationCase
from app.schemas.search import SearchResponse
from tests.support import AsgiTestClient

client = AsgiTestClient(app)

_PROVIDER = SimpleNamespace(model="stub-model", label="stub", reasoning_effort="low")
_USAGE = SimpleNamespace(
    input_tokens=100,
    output_tokens=2000,
    input_tokens_details=None,
    output_tokens_details=SimpleNamespace(reasoning_tokens=1990),
)


def _answer_failure(reason: str = "max_output_tokens") -> AnswerExecutionError:
    """adapter が送り直しても未完了で送出する失敗を、回答の実行記録の例外で包んだもの。

    本番と同じ例外の連鎖にする。
    """
    response = SimpleNamespace(
        status="incomplete", incomplete_details=SimpleNamespace(reason=reason), usage=_USAGE
    )
    raw = oci.IncompleteResponse(f"stub の Responses API 応答が未完了です: {reason}", reason)
    final = oci._incomplete_error(
        raw,
        response,
        _PROVIDER,  # type: ignore[arg-type]
        2000,
        retried=True,
        lowered_effort=True,
        reasoning_effort="low",
    )
    final.__cause__ = raw
    error = AnswerExecutionError(str(final), "質問")
    error.execution_steps = (  # type: ignore[attr-defined]
        {"name": "回答の作成", "status": "エラーで中断"},
        {"name": "根拠の評価", "status": "complete"},
    )
    error.__cause__ = final
    return error


class _IncompletePipeline:
    def __init__(self, **_kwargs: object) -> None:
        pass

    async def run(self, *_args: object, **_kwargs: object) -> SearchResponse:
        raise _answer_failure()


def _error_event(text: str) -> dict[str, object]:
    block = text.split("event: error\n", 1)[1]
    event: dict[str, object] = json.loads(block.split("\n", 1)[0].removeprefix("data: "))
    return event


def test_incomplete_failure_has_the_reason_specific_message_and_details() -> None:
    failure = describe_answer_failure(_answer_failure(), "定型文")

    assert failure.incomplete
    assert failure.error_type == ANSWER_INCOMPLETE_ERROR_TYPE
    assert failure.reason == "max_output_tokens"
    assert failure.stage == "回答の作成"
    assert "回答が長くなり" in failure.message
    assert "出力の上限を広げ、推論の量を下げて送り直しても" in failure.message
    assert "質問を分けて" in failure.message
    # 技術的な値は「詳細」の文だけに入れる（messaging.md §10.3）。
    assert "tokens（うち推論" not in failure.message
    assert failure.detail is not None
    assert "出力の上限=2000 tokens" in failure.detail
    assert "使った出力=2000 tokens（うち推論 1990）" in failure.detail
    assert "推論の量=low" in failure.detail


def test_content_filter_failure_says_the_safety_check_stopped_it() -> None:
    failure = describe_answer_failure(_answer_failure("content_filter"), "定型文")

    assert failure.reason == "content_filter"
    assert "安全の判定" in failure.message


def test_unexpected_failure_keeps_the_fixed_message() -> None:
    failure = describe_answer_failure(RuntimeError("oracle dsn=secret-host"), "定型文")

    assert (failure.message, failure.error_type, failure.reason) == ("定型文", "RuntimeError", None)
    assert failure.event_fields() == {"message": "定型文", "error_type": "RuntimeError"}


def test_stream_returns_the_reason_and_details_and_logs_the_reason(
    monkeypatch: MonkeyPatch, caplog: LogCaptureFixture
) -> None:
    monkeypatch.setattr(search_route, "RagPipeline", _IncompletePipeline)

    with caplog.at_level(logging.ERROR, logger=search_route.__name__):
        response = client.post("/api/search/stream", json={"query": "条件の多い質問"})

    assert response.status_code == 200
    event = _error_event(response.text)
    assert event["error_type"] == ANSWER_INCOMPLETE_ERROR_TYPE
    assert event["reason"] == "max_output_tokens"
    assert event["stage"] == "回答の作成"
    assert event["trace_id"]
    assert "回答が長くなり" in str(event["message"])
    assert search_route.STREAM_ERROR_MESSAGE not in str(event["message"])
    assert "出力の上限=2000 tokens" in str(event["detail"])
    # journal に理由が残る（#2023）。
    record = next(r for r in caplog.records if r.getMessage() == "rag_search_stream_failed")
    assert record.__dict__["incomplete_reason"] == "max_output_tokens"
    assert "出力の上限=2000 tokens" in record.__dict__["incomplete_detail"]


def test_rest_search_returns_502_with_the_reason_and_mcp_gets_the_code(
    monkeypatch: MonkeyPatch,
) -> None:
    monkeypatch.setattr(search_route, "RagPipeline", _IncompletePipeline)

    response = client.post("/api/search", json={"query": "条件の多い質問"})

    assert response.status_code == 502
    [message] = response.json()["error_messages"]
    assert "回答が長くなり" in message
    # MCP の rag_search（同じ `_run_search_with_timeout`）は、共通の error code の
    # ツールのエラーになる。
    converted = mcp_error_from_exception(search_route.AnswerIncompleteHTTPError(message))
    assert converted is not None
    assert (converted.code, converted.status, converted.message) == (
        ANSWER_INCOMPLETE_ERROR_CODE,
        502,
        message,
    )


def test_chat_error_message_names_the_reason() -> None:
    assert "回答が長くなり" in chat_route._chat_error_message(_answer_failure())
    assert (
        chat_route._chat_error_message(RuntimeError("secret-host"))
        == search_route.STREAM_ERROR_MESSAGE
    )


@pytest.mark.parametrize(
    ("raises_incomplete", "expected_type"),
    [(True, ANSWER_INCOMPLETE_ERROR_TYPE), (False, "RuntimeError")],
    ids=["incomplete", "unexpected"],
)
async def test_evaluation_case_error_keeps_the_reason(
    raises_incomplete: bool, expected_type: str
) -> None:
    class FailingPipeline:
        async def run(self, *_args: object, **_kwargs: object) -> SearchResponse:
            if raises_incomplete:
                raise _answer_failure()
            raise RuntimeError("raw secret detail")

    metrics = await EvaluationRunner(pipeline=FailingPipeline()).run(
        cases=[
            EvaluationCase(id="case-a", query="条件の多い質問", relevant_document_ids=["doc-1"])
        ],
        top_k=5,
    )

    [result] = metrics.case_results
    assert result.error_type == expected_type
    if raises_incomplete:
        assert result.error_message is not None
        assert "回答が長くなり" in result.error_message
        assert result.error_stage == "回答の作成"
    else:
        assert result.error_message == EVALUATION_CASE_ERROR_MESSAGE
        assert "raw secret detail" not in str(result.model_dump(mode="json"))
