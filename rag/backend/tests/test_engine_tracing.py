"""engine へ注入した observer が backend に依存せず親子関係を保つ。"""

import asyncio
import importlib
from typing import Any

import pytest
from pr_backend_core.config.tracing import TracingSettings
from pr_backend_core.observability.tracing import (
    configure_tracing,
    operation_trace,
    shutdown_tracing,
)
from rag_engine.generation.execution_record import _execution_step
from rag_engine.resources.tracing import bind_trace_observer, observe_call

from app.clients.oci_genai import OciGenAiClient
from app.config import Settings
from app.rag.pipeline import _observe_engine_call


@pytest.mark.asyncio
async def test_engine_stage_and_client_keep_parent_across_thread() -> None:
    def run_engine() -> None:
        with _execution_step("合成の工程"), observe_call("chat", "model-a") as observation:
            observation.request(
                {"input": "合成の入力", "authorization": "synthetic-secret"},
                schema={"type": "object"},
            )
            observation.response({"output": "合成の結果"})
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
        assert llm.display and llm.display.details and llm.display.details.call
        call = llm.display.details.call
        assert call.visibility == "system_admin"
        assert "合成の入力" in call.input.text
        assert "synthetic-secret" not in call.input.text
        assert "合成の結果" in call.output.text
        assert call.schema_.status == "recorded"
        assert "合成の入力" not in str(llm.attributes)
        assert stage.parent_span_id == summary.root_span_id
        assert llm.parent_span_id == stage.span_id
        assert llm.attributes["gen_ai.usage.output_tokens"] == 0
        assert "gen_ai.usage.cache_read.input_tokens" not in llm.attributes
    finally:
        shutdown_tracing()


def test_sdk_snapshot_keeps_rerank_diagnostics_and_omits_image() -> None:
    models = importlib.import_module("oci.generative_ai_inference.models")

    request = models.EmbedTextDetails(
        inputs=["合成の入力"],
        serving_mode=models.OnDemandServingMode(model_id="synthetic-model"),
        embed_contents=[
            models.EmbedImageContent(
                image_url=models.ImageUrl(url="data:image/png;base64,synthetic-image")
            )
        ],
    )
    configure_tracing(TracingSettings(), service_name="synthetic")
    try:
        with (
            operation_trace("rag.search", operation_id="synthetic") as summary,
            _observe_engine_call("rerank", "synthetic-model") as observation,
        ):
            observation.request(request)
            observation.response({"document_ranks": [{"index": 0, "relevance_score": 0.9}]})
            observation.display(
                details={"code": [{"label": "rerank", "text": "{}", "language": "json"}]}
            )
        call_span = next(span for span in summary.spans if span.operation_name == "gen_ai.rerank")
        assert call_span.display and call_span.display.details and call_span.display.details.call
        details = call_span.display.details
        assert details.call and details.call.visibility == "system_admin"
        assert (
            "合成の入力" in details.call.input.text and "synthetic-model" in details.call.input.text
        )
        assert "synthetic-image" not in details.call.input.text
        assert "media omitted" in details.call.input.text
        assert '"relevance_score": 0.9' in details.call.output.text
        assert details.call.schema_.status == "not_recorded"
        assert details.code and details.code[0].label == "rerank"
    finally:
        shutdown_tracing()


@pytest.mark.asyncio
@pytest.mark.parametrize("operation", ["embeddings", "rerank"])
@pytest.mark.parametrize("failed", [False, True])
async def test_backend_sdk_boundary_keeps_request_response_and_failure(
    operation: str, failed: bool
) -> None:
    from types import SimpleNamespace

    models = importlib.import_module("oci.generative_ai_inference.models")
    sent: list[object] = []

    def invoke(request: object) -> object:
        sent.append(request)
        if failed:
            raise RuntimeError("合成の接続失敗")
        data = (
            models.EmbedTextResult(embeddings=[[1.0, 0.0, 0.0]])
            if operation == "embeddings"
            else models.RerankTextResult(
                document_ranks=[models.DocumentRank(index=0, relevance_score=0.9)]
            )
        )
        return SimpleNamespace(data=data)

    client = OciGenAiClient(
        settings=Settings(oci_compartment_id="synthetic-compartment"),
        inference_client=SimpleNamespace(embed_text=invoke, rerank_text=invoke),
    )
    configure_tracing(TracingSettings(), service_name="synthetic")
    try:
        with operation_trace("rag.search", operation_id="synthetic") as summary:
            call = (
                client._embed_with_oci(["合成の入力"], input_type="SEARCH_QUERY")
                if operation == "embeddings"
                else client._rerank_with_oci("合成の入力", ["合成の資料"], 1)
            )
            if failed:
                with pytest.raises(RuntimeError, match="合成の接続失敗"):
                    await call
            else:
                await call
        span = next(span for span in summary.spans if span.operation_name == f"gen_ai.{operation}")
        assert span.display and span.display.details and span.display.details.call
        details = span.display.details.call
        assert details.visibility == "system_admin" and "合成の入力" in details.input.text
        assert (
            details.output.status == "recorded"
            and ("RuntimeError" in details.output.text) == failed
        )
        assert len(sent) == 1
        assert "合成の入力" not in str(span.attributes)
    finally:
        shutdown_tracing()


@pytest.fixture(autouse=True)
def enable_trace_content_for_snapshot_tests() -> Any:
    from pr_backend_core.observability.call_policy import call_content_policy

    with call_content_policy(lambda: True):
        yield


@pytest.mark.parametrize("status", ["failed", "エラーで中断"])
def test_handled_engine_failure_is_not_marked_done(status: str) -> None:
    configure_tracing(TracingSettings(), service_name="synthetic")
    try:
        with (
            operation_trace("rag.search", operation_id="synthetic") as summary,
            _observe_engine_call("stage", "合成の検索") as observation,
        ):
            observation.display(params={"status": status})
            observation.display(details={"fields": [{"label": "結果", "value": "合成の失敗"}]})
        stage = next(span for span in summary.spans if span.operation_name == "rag.engine.stage")
        assert stage.status == "failed"
    finally:
        shutdown_tracing()


def test_batch_validation_retry_is_a_separate_send_and_keeps_the_response(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from dataclasses import replace
    from types import SimpleNamespace

    from pr_backend_core.observability.model_purpose import model_call_purpose
    from rag_engine.adapters import oci
    from rag_engine.config import LlmProviderSettings
    from rag_engine.config import get_settings as engine_settings
    from rag_engine.generation.batched_evidence import _reading_model
    from rag_engine.resources.call_context import call_context, update_call_context
    from rag_engine.resources.structured_output_retry import bind_structured_output_repair

    schema = _reading_model([{"text": "合成の本文。", "source_id": "source-a"}])
    sent: list[dict[str, Any]] = []

    class Response(SimpleNamespace):
        def model_dump(self) -> dict[str, Any]:
            return vars(self)

    class Client:
        def __init__(self) -> None:
            self.responses = SimpleNamespace(parse=self.parse)

        def with_options(self, **kwargs: Any) -> "Client":
            return self

        def parse(self, **kwargs: Any) -> Any:
            import json

            sent.append(kwargs)
            last = "B1-2" if len(sent) == 1 else "B1-1"
            return Response(
                status="completed",
                output_text=json.dumps(
                    {"findings": [{"start_line": "B1-1", "end_line": last, "answer": "合成の回答"}]}
                ),
                usage=None,
            )

    def repair(error: Exception, will_retry: bool) -> str | None:
        update_call_context(
            {"pr.call.role": "batch_range_repair", "pr.retry.reason": "citation_range_invalid"}
        )
        return "合成の修正入力" if will_retry else None

    monkeypatch.setattr(oci, "_openai_client_for_provider", lambda *args: Client())
    provider = LlmProviderSettings(
        provider_id="synthetic",
        label="synthetic",
        region="",
        project_id="",
        api_key="",
        model="synthetic-model",
    )
    settings = replace(engine_settings(environ={}, dotenv_path=None), llm_retries=0)
    configure_tracing(TracingSettings(), service_name="synthetic")
    try:
        with (
            operation_trace("rag.search", operation_id="synthetic") as summary,
            bind_trace_observer(_observe_engine_call),
            model_call_purpose("evidence.read_batch"),
            call_context(
                {
                    "pr.call.role": "batch_read",
                    "pr.batch.index": 2,
                    "pr.batch.count": 2,
                    "pr.batch.pass": 1,
                }
            ),
            bind_structured_output_repair(repair),
        ):
            result: Any = oci._parse_with_retry(
                lambda: oci._openai_parsed([], settings, schema, provider),
                "synthetic",
                settings,
            )
        assert result.findings[0].end_line == "B1-1"
        calls = [span for span in summary.spans if span.operation_name == "gen_ai.chat"]
        assert len(sent) == len(calls) == 2
        assert [span.status for span in calls] == ["failed", "done"]
        assert [span.attributes["pr.attempt"] for span in calls] == [1, 2]
        assert [span.attributes["pr.gen_ai.purpose"] for span in calls] == [
            "evidence.read_batch",
            "evidence.repair_batch_range",
        ]
        assert all(span.attributes["pr.call.transport_status"] == "returned" for span in calls)
        assert [span.attributes["pr.call.validation_status"] for span in calls] == [
            "rejected",
            "accepted",
        ]
        assert all(span.attributes["pr.batch.index"] == 2 for span in calls)
        assert calls[0].display and calls[0].display.details and calls[0].display.details.call
        import json

        recorded = json.loads(calls[0].display.details.call.output.text)
        assert json.loads(recorded["output_text"])["findings"][0]["end_line"] == "B1-2"
        assert (
            next(span for span in summary.spans if span.operation_name == "rag.search").status
            == "done"
        )
    finally:
        shutdown_tracing()
