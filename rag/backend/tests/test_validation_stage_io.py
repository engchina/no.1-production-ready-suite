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
from app.schemas.search import SearchRequest
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
