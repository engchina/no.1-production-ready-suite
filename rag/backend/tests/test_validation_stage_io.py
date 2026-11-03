"""最終照合と選択済み FAQ の実入出力を、保存した工程ごとに確かめる。"""

import json
from typing import Any, cast
from unittest.mock import AsyncMock

import pytest
from pr_backend_core.config.tracing import TracingSettings
from pr_backend_core.observability.call_policy import call_content_policy
from pr_backend_core.observability.tracing import (
    TraceSummary,
    configure_tracing,
    operation_trace,
    shutdown_tracing,
)

from app.config import Settings
from app.rag import answer_engine as module
from app.rag.answer_engine import AnswerEngine, AnswerOutcome
from app.rag.answer_validation import AnswerValidation
from app.schemas.search import RetrievedChunk, SearchRequest
from tests.test_answer_engine import FakeGenAi, FakeOracle


@pytest.fixture(autouse=True)
def tracing() -> Any:
    configure_tracing(TracingSettings(), service_name="synthetic")
    try:
        yield
    finally:
        shutdown_tracing()


def call_of(summary: TraceSummary, name: str) -> Any:
    saved = TraceSummary.model_validate_json(summary.model_dump_json())
    span = next(item for item in saved.spans if item.attributes.get("pr.stage") == name)
    assert span.display and span.display.details and span.display.details.call
    return span.display.details.call


def engine(**kwargs: Any) -> AnswerEngine:
    return AnswerEngine(
        Settings(rag_answer_audit_mode="standard"),
        oracle=cast(Any, FakeOracle()),
        genai=cast(Any, FakeGenAi()),
        **kwargs,
    )


@pytest.mark.parametrize("enabled", [True, False])
@pytest.mark.parametrize("failed", [True, False])
async def test_final_validation_records_actual_input_and_verdict(
    monkeypatch: pytest.MonkeyPatch, enabled: bool, failed: bool
) -> None:
    validator = AsyncMock(
        return_value=AnswerValidation(status="completed", valid=True, counts={"supported": 1}),
        side_effect=TimeoutError("private-error-body") if failed else None,
    )
    monkeypatch.setattr("app.rag.answer_validation.validate_answer", validator)
    if not enabled:

        def forbidden(value: Any) -> Any:
            raise AssertionError("OFF では本文を構築しない")

        monkeypatch.setattr(module, "asdict", forbidden)
    citation = RetrievedChunk(document_id="doc-a", chunk_id="chunk-a", text="合成根拠", score=1)
    outcome = AnswerOutcome(
        answer="合成回答", citations=[citation], diagnostics={}, context_text="合成根拠"
    )
    with (
        call_content_policy(lambda: enabled),
        operation_trace("rag.search", operation_id="synthetic") as summary,
    ):
        await engine()._validate_outcome(SearchRequest(query="合成の質問"), outcome)
    call = call_of(summary, module.ANSWER_VALIDATION_STEP)
    assert call.capture_enabled is enabled
    assert "private-error-body" not in call.model_dump_json()
    validator.assert_awaited_once()
    assert validator.await_args is not None
    if enabled:
        data = json.loads(call.input.text)
        assert data["質問"] == validator.await_args.args[0] == "合成の質問"
        assert data["回答"] == validator.await_args.args[1] == "合成回答"
        assert data["根拠 ID"] == [{"document_id": "doc-a", "chunk_id": "chunk-a"}]
        assert data["確認前提"] == validator.await_args.kwargs["premises"] == []
        result = json.loads(call.output.text)
        assert result["照合関数を呼び出した"] is True
        assert result["適用した検証結果"] == outcome.diagnostics["answer_validation"]
        if failed:
            assert result["例外の型"] == "TimeoutError"
            assert result["返却された検証結果"] is None
        else:
            assert result["返却された検証結果"]["valid"] is True
            assert result["返却された検証結果"]["counts"] == {"supported": 1}
        assert call.visibility == "system_admin"
    else:
        assert call.input.text == call.output.text == ""


async def test_final_validation_limit_does_not_invent_a_validator_call(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    validator = AsyncMock()
    monkeypatch.setattr("app.rag.answer_validation.validate_answer", validator)
    monkeypatch.setattr("app.rag.answer_validation.MAX_PREMISES", 1)
    premises = [{"question": "q1", "answer": "a1"}, {"question": "q2", "answer": "a2"}]
    monkeypatch.setattr(module, "_confirmed_validation_premises", lambda *args: premises)
    outcome = AnswerOutcome(answer="合成回答", citations=[], diagnostics={}, context_text="")
    with (
        call_content_policy(lambda: True),
        operation_trace("rag.search", operation_id="synthetic") as summary,
    ):
        await engine()._validate_outcome(SearchRequest(query="q"), outcome)
    validator.assert_not_awaited()
    call = call_of(summary, module.ANSWER_VALIDATION_STEP)
    assert json.loads(call.input.text)["確認前提"] == premises
    output = json.loads(call.output.text)
    assert output["返却された検証結果"]["status"] == "input_too_large"
    assert output["照合関数を呼び出した"] is False


@pytest.mark.parametrize("enabled", [True, False])
@pytest.mark.parametrize("failed", [True, False])
async def test_selected_faq_keeps_its_input_and_actual_return(
    monkeypatch: pytest.MonkeyPatch, failed: bool, enabled: bool
) -> None:
    runner = AsyncMock(
        return_value="合成回答", side_effect=RuntimeError("private-error-body") if failed else None
    )
    monkeypatch.setattr(module, "run_answer_in_thread", runner)
    with (
        call_content_policy(lambda: enabled),
        operation_trace("rag.search", operation_id="synthetic") as summary,
    ):
        if failed:
            with pytest.raises(RuntimeError):
                await engine(
                    approved_faq=("合成の既存質問", "合成の既存回答")
                )._answer_from_approved_faq(SearchRequest(query="合成の質問"))
        else:
            outcome = await engine(
                approved_faq=("合成の既存質問", "合成の既存回答")
            )._answer_from_approved_faq(SearchRequest(query="合成の質問"))
            assert outcome.answer.startswith("合成回答")
    call = call_of(summary, "承認済み FAQ からの回答")
    if not enabled:
        assert not call.capture_enabled
        assert call.input.text == call.output.text == ""
        return
    data = json.loads(call.input.text)
    assert runner.await_args is not None
    assert data["質問"] == runner.await_args.args[1] == "合成の質問"
    assert data["選択した FAQ の質問"] == runner.await_args.args[2] == "合成の既存質問"
    assert data["選択した FAQ の回答"] == runner.await_args.args[3] == "合成の既存回答"
    assert "private-error-body" not in call.model_dump_json()
    assert json.loads(call.output.text) == (
        {"error_type": "RuntimeError"} if failed else {"返却された回答": "合成回答"}
    )
