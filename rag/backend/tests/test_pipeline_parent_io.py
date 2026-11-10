"""外側の集約工程も、実際に渡した値と返却値を対応させる。"""

import json
from time import perf_counter
from types import SimpleNamespace
from typing import Any, cast

import pytest
from pr_backend_core.config.tracing import TracingSettings
from pr_backend_core.observability.call_policy import call_content_policy
from pr_backend_core.observability.tracing import (
    configure_tracing,
    operation_trace,
    shutdown_tracing,
)

from app.config import Settings
from app.rag import chat_attachments
from app.rag.answer_engine import AnswerEngine, AnswerOutcome
from app.rag.guardrails import GuardrailResult
from app.rag.pipeline import ChatTurn, RagPipeline
from app.schemas.search import RetrievedChunk, SearchRequest
from tests.test_answer_engine import FakeGenAi, SavingOracle
from tests.test_validation_stage_io import call_of


@pytest.fixture(autouse=True)
def tracing() -> Any:
    configure_tracing(TracingSettings(), service_name="synthetic")
    try:
        yield
    finally:
        shutdown_tracing()


@pytest.mark.parametrize("enabled", [True, False])
@pytest.mark.parametrize("generate", [True, False])
async def test_pipeline_parent_steps_keep_actual_values(
    monkeypatch: pytest.MonkeyPatch, enabled: bool, generate: bool
) -> None:
    chunk = RetrievedChunk(document_id="doc-a", chunk_id="chunk-a", text="合成の根拠", score=0.8)
    seen: dict[str, Any] = {}

    async def rewrite(self: Any, question: str, history: Any, **kwargs: Any) -> str:
        seen["rewrite"] = question
        return "合成の補完質問"

    async def attachment(question: str, conversation: str, **kwargs: Any) -> list[RetrievedChunk]:
        seen["attachment"] = (question, conversation)
        return [chunk]

    async def answer(self: Any, request: Any, **kwargs: Any) -> AnswerOutcome:
        seen["engine"] = request.query
        return AnswerOutcome(
            answer="合成の回答" if generate else "",
            citations=[chunk],
            context_text="合成の根拠本文",
            diagnostics={},
        )

    monkeypatch.setattr(RagPipeline, "_safe_rewritten_query", rewrite)
    monkeypatch.setattr(
        chat_attachments,
        "chat_attachment_service",
        lambda: SimpleNamespace(reading_status=lambda: "done"),
    )
    monkeypatch.setattr(chat_attachments, "attachment_evidence", attachment)
    monkeypatch.setattr(AnswerEngine, "run", answer)
    oracle = SavingOracle()
    pipeline = RagPipeline(
        settings=Settings(_env_file=None, rag_history_rewrite_enabled=True),
        oracle=cast(Any, oracle),
        genai=cast(Any, FakeGenAi()),
        attachment_conversation_id="conversation-a",
    )
    request = SearchRequest(
        query="合成の元質問", search_answer_profile_id="profile-a", generate_answer=generate
    )
    with (
        call_content_policy(lambda: enabled),
        operation_trace("rag.search", operation_id="synthetic") as summary,
    ):
        response = await pipeline._run_engine(
            request,
            trace_id="synthetic",
            started_at=perf_counter(),
            query_guardrail=GuardrailResult(
                allowed=True, sanitized_text=request.query, findings=[]
            ),
            token_callback=None,
            history=[ChatTurn(role="USER", content="合成の履歴")],
        )
    assert seen == {
        "rewrite": "合成の元質問",
        "attachment": ("合成の補完質問", "conversation-a"),
        "engine": "合成の補完質問",
    }
    assert len(response.citations) == 1
    for stage in [
        "history_rewrite",
        "attachment_evidence",
        "answer" if generate else "evidence",
        *(["answer_record_save"] if generate else []),
    ]:
        call = call_of(summary, stage)
        assert call.capture_enabled is enabled
        if not enabled:
            assert call.input.text == call.output.text == ""
            continue
        assert call.visibility == "system_admin"
        input_data, output = json.loads(call.input.text), json.loads(call.output.text)
        if stage == "history_rewrite":
            assert input_data["渡した会話履歴"][0]["content"] == "合成の履歴"
            assert output["返却された補完質問"] == seen["engine"]
        elif stage == "attachment_evidence":
            assert input_data["会話 ID"] == seen["attachment"][1]
            assert output["返却した根拠"][0]["chunk_id"] == chunk.chunk_id
        elif stage in {"answer", "evidence"}:
            assert input_data["検索要求"]["query"] == seen["engine"]
            assert input_data["添付の根拠"][0]["text"] == chunk.text
            assert output["回答"] == response.answer
            assert output["根拠本文"] == "合成の根拠本文"
        else:
            assert input_data["回答"] == response.answer
            assert output["保存関数から正常に戻った"] is True


@pytest.mark.parametrize("enabled", [True, False])
async def test_retrieval_parent_has_actual_result(
    monkeypatch: pytest.MonkeyPatch, enabled: bool
) -> None:
    chunk = RetrievedChunk(document_id="doc-a", chunk_id="chunk-a", text="合成の根拠", score=0.8)

    async def retrieve(self: Any, request: SearchRequest) -> list[RetrievedChunk]:
        return [chunk]

    monkeypatch.setattr(AnswerEngine, "retrieve", retrieve)
    pipeline = RagPipeline(
        settings=Settings(_env_file=None),
        oracle=cast(Any, SavingOracle()),
        genai=cast(Any, FakeGenAi()),
    )
    request = SearchRequest(query="合成の検索", retrieval_only=True)
    with (
        call_content_policy(lambda: enabled),
        operation_trace("rag.search", operation_id="synthetic") as summary,
    ):
        response = await pipeline._run_engine_retrieval(
            request,
            trace_id="synthetic",
            started_at=perf_counter(),
            query_guardrail=GuardrailResult(
                allowed=True, sanitized_text=request.query, findings=[]
            ),
            progress_callback=None,
        )
    assert len(response.citations) == 1
    call = call_of(summary, "retrieval")
    assert call.capture_enabled is enabled
    if enabled:
        assert json.loads(call.input.text)["検索要求"]["query"] == request.query
        output = json.loads(call.output.text)
        assert output["返却件数"] == 1
        assert output["返却した根拠"][0]["text"] == chunk.text
    else:
        assert call.input.text == call.output.text == ""


@pytest.mark.parametrize("enabled", [True, False])
@pytest.mark.parametrize("cancelled", [True, False])
async def test_parent_failure_records_no_invented_return(enabled: bool, cancelled: bool) -> None:
    import asyncio

    from app.rag.pipeline import _observe_stage

    error = (
        asyncio.CancelledError("private-error-marker")
        if cancelled
        else RuntimeError("private-error-marker")
    )

    async def fail() -> str:
        raise error

    def forbidden(value: Any) -> Any:
        raise AssertionError("失敗した処理の成功snapshotを呼ばない")

    with (
        call_content_policy(lambda: enabled),
        operation_trace("rag.search", operation_id="synthetic") as summary,
        pytest.raises(type(error)),
    ):
        await _observe_stage(
            "synthetic",
            "answer_record_save",
            fail(),
            request_snapshot=lambda: {"記録 ID": "synthetic"},
            result_snapshot=forbidden,
        )
    call = call_of(summary, "answer_record_save")
    assert call.capture_enabled is enabled
    assert "private-error-marker" not in call.model_dump_json()
    if enabled:
        output = json.loads(call.output.text)
        assert output["返却結果"] is None
        assert output["実行状態"] == ("cancelled" if cancelled else "error")
        assert output["例外の型"] == type(error).__name__
    else:
        assert call.input.text == call.output.text == ""
