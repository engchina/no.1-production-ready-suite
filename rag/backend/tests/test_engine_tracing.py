"""engine へ注入した observer が backend に依存せず親子関係を保つ。"""

import asyncio

import pytest
from pr_backend_core.config.tracing import TracingSettings
from pr_backend_core.observability.tracing import (
    configure_tracing,
    operation_trace,
    shutdown_tracing,
)
from rag_engine.generation.execution_record import _execution_step
from rag_engine.resources.tracing import bind_trace_observer, observe_call

from app.rag.pipeline import _observe_engine_call


@pytest.mark.asyncio
async def test_engine_stage_and_client_keep_parent_across_thread() -> None:
    def run_engine() -> None:
        with _execution_step("合成の工程"), observe_call("chat", "model-a") as observation:
            observation.usage({"input_tokens": 10, "output_tokens": 0})

    configure_tracing(TracingSettings(), service_name="synthetic")
    try:
        with (
            operation_trace("rag.search", operation_id="synthetic") as summary,
            bind_trace_observer(_observe_engine_call),
        ):
            await asyncio.to_thread(run_engine)
        stage = next(span for span in summary.spans if span.operation_name == "rag.engine.stage")
        llm = next(span for span in summary.spans if span.operation_name == "gen_ai.chat")
        assert stage.parent_span_id == summary.root_span_id
        assert llm.parent_span_id == stage.span_id
        assert llm.attributes["gen_ai.usage.output_tokens"] == 0
        assert "gen_ai.usage.cache_read.input_tokens" not in llm.attributes
    finally:
        shutdown_tracing()
