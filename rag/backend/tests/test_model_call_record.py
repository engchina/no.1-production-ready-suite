"""記録の外で呼んだモデルの呼び出しを集め、暗号化して渡し、回答の記録に残す（#5773）。"""

from typing import Any

import pytest
from pr_backend_core.config.tracing import TracingSettings
from pr_backend_core.observability.call_policy import call_content_policy
from pr_backend_core.observability.tracing import (
    configure_tracing,
    observed_span,
    operation_trace,
    shutdown_tracing,
)

from app.rag import model_call_record
from app.rag.model_call_record import (
    collect_model_calls,
    defer_model_calls,
    open_model_calls,
    record_deferred_model_calls,
    reset_deferred_model_calls,
    seal_model_calls,
)

SECRET = "synthetic-signing-key-" * 3


@pytest.fixture(autouse=True)
def tracing() -> Any:
    configure_tracing(TracingSettings(), service_name="synthetic")
    try:
        with call_content_policy(lambda: True):
            yield
    finally:
        shutdown_tracing()


def _call(purpose: str = "query.rewrite") -> None:
    with observed_span(
        "gen_ai.chat", kind="client", attributes={"pr.gen_ai.purpose": purpose}
    ) as span:
        span.set_call_input({"input": "合成の入力"}, restricted=True)
        span.set_call_output({"output_text": "合成の応答"})


def test_collects_only_finished_model_calls_outside_a_trace() -> None:
    with collect_model_calls() as calls:
        _call()
        with observed_span("rag.other"):
            pass
        with operation_trace("rag.answer", operation_id="synthetic"):
            _call("answer.generate")
    assert [call.attributes["pr.gen_ai.purpose"] for call in calls] == ["query.rewrite"]
    assert calls[0].finished_at is not None


def test_sealed_calls_round_trip_and_hide_the_content() -> None:
    with collect_model_calls() as calls:
        _call()
    sealed = seal_model_calls(SECRET, calls)
    assert sealed and "合成の入力" not in sealed and "query.rewrite" not in sealed
    [opened] = open_model_calls(SECRET, sealed)
    assert opened == calls[0]
    assert opened.display is not None and opened.display.details is not None
    call = opened.display.details.call
    assert call is not None and call.visibility == "system_admin"
    assert "合成の入力" in call.input.text and "合成の応答" in call.output.text
    assert open_model_calls("another-synthetic-signing-key-" * 2, sealed) == ()
    assert open_model_calls(SECRET, sealed[:-4] + "AAAA") == ()
    assert seal_model_calls(SECRET, []) is None


def test_oversized_calls_drop_details_but_keep_the_call(monkeypatch: pytest.MonkeyPatch) -> None:
    with collect_model_calls() as calls:
        _call()
    monkeypatch.setattr(model_call_record, "SEALED_CALLS_MAX", 10)
    [opened] = open_model_calls(SECRET, seal_model_calls(SECRET, calls))
    assert opened.display is None
    assert opened.attributes["pr.gen_ai.purpose"] == "query.rewrite"


async def test_deferred_calls_are_recorded_under_the_answer_root() -> None:
    reset_deferred_model_calls()
    with defer_model_calls():
        _call("query.conditions")
    with operation_trace("rag.answer", operation_id="synthetic") as summary:
        record_deferred_model_calls()
    root = next(span for span in summary.spans if span.operation_name == "rag.answer")
    [call] = [span for span in summary.spans if span.operation_name == "gen_ai.chat"]
    assert call.parent_span_id == root.span_id
    assert call.attributes["pr.gen_ai.purpose"] == "query.conditions"
    assert call.display is not None and call.display.details is not None
    assert call.display.details.call is not None
    assert "合成の入力" in call.display.details.call.input.text
    reset_deferred_model_calls()
    with operation_trace("rag.answer", operation_id="synthetic-b") as summary:
        record_deferred_model_calls()
    assert not [span for span in summary.spans if span.operation_name == "gen_ai.chat"]
