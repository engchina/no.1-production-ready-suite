"""CRAGの評価状態と実IOが最終spanに残ることを確認する。"""

import json

import pytest
from pr_backend_core.config.tracing import TracingSettings
from pr_backend_core.observability.call_details import withhold_call_details
from pr_backend_core.observability.call_policy import call_content_policy
from pr_backend_core.observability.tracing import (
    SpanSummary,
    configure_tracing,
    operation_trace,
    shutdown_tracing,
)
from rag_engine.config import get_settings
from rag_engine.generation import answering as a
from rag_engine.generation.answer_models import AnswerContext, AnswerRecord
from rag_engine.resources.tracing import bind_trace_observer

from app.rag.pipeline import _observe_engine_call


@pytest.mark.parametrize("enabled", [True, False])
def test_failed_grade_keeps_actual_failure_and_retained_context(
    monkeypatch: pytest.MonkeyPatch, enabled: bool
) -> None:
    row = AnswerRecord(
        id="c1",
        engine="synthetic",
        engine_label="synthetic",
        page=1,
        seq_no=1,
        category="text",
        text="合成の根拠本文",
        source="synthetic.txt",
        chunk_uid="run:c1",
    )
    context = AnswerContext([row], row.text)
    monkeypatch.setattr(a, "build_adb_hybrid_answer_context", lambda *args, **kwargs: context)

    def fail(*args: object, **kwargs: object) -> object:
        raise ValueError("private exception sentinel")

    monkeypatch.setattr(a, "_grade_crag_retrieval", fail)
    configure_tracing(TracingSettings(), service_name="synthetic")
    try:
        with (
            call_content_policy(lambda: enabled),
            operation_trace("rag.search", operation_id="synthetic") as summary,
            bind_trace_observer(_observe_engine_call),
        ):
            result, _, attempts = a.build_crag_answer_context(
                "合成の質問",
                "synthetic-run",
                ["synthetic"],
                get_settings(environ={}, dotenv_path=None),
            )
        assert result.records == context.records
        assert attempts[0].grade_execution == "error"
        stage = next(
            span
            for span in summary.spans
            if span.display
            and span.display.params
            and span.display.params.get("name") == "根拠確認（1回目）"
        )
        restored = SpanSummary.model_validate_json(stage.model_dump_json())
        assert restored.status == "failed"
        assert (
            next(span for span in summary.spans if span.operation_name == "rag.search").status
            == "done"
        )
        assert restored.display and restored.display.details and restored.display.details.call
        call = restored.display.details.call
        assert call.capture_enabled is enabled
        assert "private exception sentinel" not in call.model_dump_json()
        if enabled:
            assert call.visibility == "system_admin"
            assert json.loads(call.input.text)["根拠レコード"][0]["ID"] == "run:c1"
            assert json.loads(call.output.text)["grade_error"] == "ValueError"
            hidden = withhold_call_details(restored.model_dump(), allow_restricted=False)
            assert "合成の根拠本文" not in str(hidden)
        else:
            assert not call.input.text and not call.output.text
    finally:
        shutdown_tracing()
