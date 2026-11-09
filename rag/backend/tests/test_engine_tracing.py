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


@pytest.mark.parametrize("enabled", [False, True])
@pytest.mark.parametrize("outcome", ["complete", "skipped", "failed"])
def test_engine_stage_keeps_io_after_final_summary(enabled: bool, outcome: str) -> None:
    from pr_backend_core.observability.call_details import withhold_call_details
    from pr_backend_core.observability.call_policy import call_content_policy
    from pr_backend_core.observability.tracing import SpanSummary

    configure_tracing(TracingSettings(), service_name="synthetic")
    try:
        with (
            call_content_policy(lambda: enabled),
            operation_trace("rag.search", operation_id="synthetic") as summary,
            bind_trace_observer(_observe_engine_call),
            _execution_step("合成の工程", "合成の目的") as step,
        ):
            step.input({"候補": ["synthetic-source"], "authorization": "private-key"})
            step.output({"採用": ["synthetic-source"]})
            step.result("合成の結果")
            if outcome == "skipped":
                step.skip("合成の無効設定")
            elif outcome == "failed":
                step.status = "failed"
        stage = next(span for span in summary.spans if span.operation_name == "rag.engine.stage")
        restored = SpanSummary.model_validate_json(stage.model_dump_json())
        assert restored.status == ("done" if outcome == "complete" else outcome)
        assert restored.display and restored.display.details and restored.display.details.call
        call = restored.display.details.call
        assert call.capture_enabled is enabled
        payload = call.model_dump_json()
        assert "private-key" not in payload
        assert ("synthetic-source" in payload) is enabled
        if enabled:
            assert call.input.status == call.output.status == "recorded"
            assert call.visibility == "system_admin"
            hidden = withhold_call_details(restored.model_dump(), allow_restricted=False)
            assert "synthetic-source" not in str(hidden)
            assert hidden["display"]["details"]["call"]["input"]["status"] == "withheld"
            assert restored.display.details.fields
    finally:
        shutdown_tracing()


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
    from types import SimpleNamespace

    from pr_backend_core.observability.model_purpose import model_call_purpose
    from rag_engine.adapters import oci
    from rag_engine.config import LlmProviderSettings
    from rag_engine.config import get_settings as engine_settings
    from rag_engine.generation.batched_evidence import _reading_model, reading_unit_catalog
    from rag_engine.resources.call_context import call_context, update_call_context
    from rag_engine.resources.structured_output_retry import bind_structured_output_repair

    schema = _reading_model([{"text": "合成の本文。", "source_id": "source-a"}])
    actual_id = reading_unit_catalog([{"text": "合成の本文。", "source_id": "source-a"}]).unit_ids[
        0
    ]
    foreign_id = "U" + "f" * 24
    assert actual_id != foreign_id
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
            chosen = foreign_id if len(sent) == 1 else actual_id
            return Response(
                status="completed",
                output_text=json.dumps(
                    {"findings": [{"unit_ids": [chosen], "answer": "合成の回答"}]}
                ),
                usage=None,
            )

    def repair(error: Exception, will_retry: bool) -> str | None:
        update_call_context(
            {"pr.call.role": "batch_range_repair", "pr.retry.reason": "citation_unit_invalid"}
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
    settings = engine_settings(environ={}, dotenv_path=None)
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
        assert result.findings[0].unit_ids == [actual_id]
        calls = [span for span in summary.spans if span.operation_name == "gen_ai.chat"]
        assert len(sent) == len(calls) == 2
        assert [span.status for span in calls] == ["failed", "done"]
        assert [span.attributes["pr.attempt"] for span in calls] == [1, 2]
        # 同じ呼び出しの試行は同じ束ねの値を持ち、やり直す試行にだけ予定の印が付く（#4736）。
        groups = {span.attributes["pr.call.retry_group"] for span in calls}
        assert len(groups) == 1 and all(groups)
        assert ["pr.retry.scheduled" in span.attributes for span in calls] == [True, False]
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
        assert json.loads(recorded["output_text"])["findings"][0]["unit_ids"] == [foreign_id]
        assert (
            next(span for span in summary.spans if span.operation_name == "rag.search").status
            == "done"
        )
    finally:
        shutdown_tracing()


@pytest.mark.parametrize("enabled", [False, True])
def test_catalog_skip_keeps_real_io_through_final_span_summary(enabled: bool) -> None:
    from pr_backend_core.observability.call_policy import call_content_policy
    from pr_backend_core.observability.tracing import TraceSummary
    from rag_engine.config import get_settings as engine_settings
    from rag_engine.generation.answering import _catalog_article_children

    configure_tracing(TracingSettings(), service_name="synthetic")
    try:
        with (
            call_content_policy(lambda: enabled),
            operation_trace("rag.search", operation_id="synthetic") as summary,
            bind_trace_observer(_observe_engine_call),
        ):
            assert _catalog_article_children(
                "合成の質問", [], engine_settings(environ={}, dotenv_path=None)
            ) == ([], [])
        saved = TraceSummary.model_validate_json(summary.model_dump_json())
        stage = next(span for span in saved.spans if span.attributes.get("pr.stage") == "条の選択")
        assert stage.status == "skipped"
        assert stage.display and stage.display.details and stage.display.details.call
        call = stage.display.details.call
        assert call.capture_enabled is enabled
        if enabled:
            import json

            assert call.visibility == "system_admin"
            assert json.loads(call.input.text)["受け取った質問"] == "合成の質問"
            assert json.loads(call.output.text)["理由"] == "dependencies_unavailable"
        else:
            assert call.input.text == call.output.text == ""
    finally:
        shutdown_tracing()


@pytest.mark.parametrize("enabled", [False, True])
@pytest.mark.parametrize(
    "depth,expected_count,status", [(0, 0, "skipped"), (1, 0, "done"), (4, 3, "done")]
)
def test_diversity_stage_preserves_actual_status_and_counts_without_call_bodies(
    enabled: bool,
    depth: int,
    expected_count: int,
    status: str,
) -> None:
    from pr_backend_core.observability.call_policy import call_content_policy

    from app.rag.answer_engine import _observed_diverse_candidates

    configure_tracing(TracingSettings(), service_name="synthetic")
    try:
        with (
            call_content_policy(lambda: enabled),
            operation_trace("rag.search", operation_id="synthetic") as summary,
            bind_trace_observer(_observe_engine_call),
        ):
            result = _observed_diverse_candidates(
                ["a1", "a2", "b1", "b2"],
                1,
                lambda value: value[0],
                depth=depth,
                max_extra=100,
                new_document=2,
                present_document=1,
            )
        stage = next(span for span in summary.spans if span.operation_name == "rag.engine.stage")
        assert len(result) == expected_count
        assert stage.status == status
        assert stage.display is not None
        assert stage.display.params is not None
        assert stage.display.params["max_extra"] == 100
        assert stage.display.params["added_count"] == expected_count
        assert "note" not in stage.display.params
        assert stage.display.params["reason_code"] == (
            "depth_disabled" if depth == 0 else "no_match" if depth == 1 else "added"
        )
    finally:
        shutdown_tracing()


@pytest.mark.parametrize("enabled", [False, True])
@pytest.mark.parametrize(
    "reason,status",
    [
        ("configuration_disabled", "skipped"),
        ("no_match", "done"),
        ("selection_failed", "failed"),
        ("caller_queries", "skipped"),
    ],
)
def test_recorded_execution_decisions_keep_status_and_reason_without_client_calls(
    enabled: bool,
    reason: str,
    status: str,
) -> None:
    from pr_backend_core.observability.call_policy import call_content_policy
    from rag_engine.generation.answering import _record_catalog_skip

    from app.rag.answer_engine import _expansion_stage_outcome

    configure_tracing(TracingSettings(), service_name="synthetic")
    try:
        with (
            call_content_policy(lambda: enabled),
            operation_trace("rag.search", operation_id="synthetic") as summary,
            bind_trace_observer(_observe_engine_call),
        ):
            if reason == "caller_queries":
                _record_catalog_skip("条の選択", reason)
            elif reason == "selection_failed":
                with _execution_step("画面の選択") as step:
                    step.fail("取得に失敗したため、既存の候補で続けます。")
            else:
                with _observe_engine_call("stage", "実体・別名による検索拡張") as observation:
                    _expansion_stage_outcome(
                        observation, reason=reason if status == "skipped" else ""
                    )
        stages = [span for span in summary.spans if span.operation_name == "rag.engine.stage"]
        assert len(stages) == 1
        stage = stages[0]
        assert stage.status == status
        assert stage.display is not None and stage.display.params is not None
        assert stage.display.params["reason_code"] == reason
        assert stage.display.params["result_count"] == 0
        assert stage.display.params["execution_kind"] == (
            "decision" if status == "skipped" else "processing"
        )
        assert "note" not in stage.display.params
        assert not any(span.operation_name.startswith("gen_ai.") for span in summary.spans)
    finally:
        shutdown_tracing()
