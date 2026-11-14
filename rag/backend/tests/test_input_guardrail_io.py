"""入力検査の実測と結果の再利用を、検査回数を変えず確認する。"""

import json
from typing import Any, cast

import pytest
from pr_backend_core.config.tracing import TracingSettings
from pr_backend_core.observability.call_policy import call_content_policy
from pr_backend_core.observability.tracing import (
    configure_tracing,
    operation_trace,
    shutdown_tracing,
)
from pr_system_settings.guardrails import GuardrailResult

from app.config import Settings
from app.rag import pipeline as module
from app.rag.pipeline import RagPipeline
from tests.test_validation_stage_io import call_of


@pytest.fixture(autouse=True)
def tracing() -> Any:
    configure_tracing(TracingSettings(), service_name="synthetic")
    try:
        yield
    finally:
        shutdown_tracing()


@pytest.mark.parametrize("enabled", [True, False])
@pytest.mark.parametrize("mode", ["allowed", "blocked", "degraded", "disabled", "reused", "error"])
async def test_query_guardrail_actual_io(
    monkeypatch: pytest.MonkeyPatch, enabled: bool, mode: str
) -> None:
    seen: list[str] = []
    expected = GuardrailResult(
        allowed=mode not in {"blocked", "degraded"},
        sanitized_text="合成の検査後の質問",
        findings=[],
        backend_degraded=mode == "degraded",
        skipped_reason="disabled" if mode == "disabled" else None,
    )

    class Policy:
        def validate_query(self, question: str) -> GuardrailResult:
            seen.append(question)
            if mode == "error":
                raise RuntimeError("private-error-marker")
            return expected

    if not enabled:

        def forbidden(*args: Any) -> Any:
            raise AssertionError("本文OFFで結果をsnapshotに変換しない")

        monkeypatch.setattr(module, "asdict", forbidden)
    pipeline = RagPipeline(settings=Settings(_env_file=None), guardrails=cast(Any, Policy()))
    with (
        call_content_policy(lambda: enabled),
        operation_trace("rag.search", operation_id="synthetic") as summary,
    ):
        if mode == "error":
            with pytest.raises(RuntimeError, match="private-error-marker"):
                await pipeline._check_query("合成の質問", purpose="search_question")
        else:
            result = await pipeline._check_query(
                "合成の質問",
                purpose="search_question",
                prechecked=expected if mode == "reused" else None,
            )
            assert result is expected
    assert seen == ([] if mode == "reused" else ["合成の質問"])
    call = call_of(summary, "query_guardrail")
    span = next(
        item for item in summary.spans if item.attributes.get("pr.stage") == "query_guardrail"
    )
    assert span.status == (
        "failed" if mode in {"error", "degraded"} else "skipped" if mode == "disabled" else "done"
    )
    # 受付時の結果の再利用は検査済みとして完了にし、詳細で再利用を示す（#5064）。
    labels = (
        {field.label: field.value for field in span.display.details.fields}
        if span.display and span.display.details
        else {}
    )
    if enabled and mode != "error":
        assert ("検査の実施" in labels) is (mode == "reused")
        if mode == "reused":
            assert labels["結果"] == "問題なし"
        if mode == "disabled":
            assert labels["結果"] == "無効のため省略"
    assert call.capture_enabled is enabled
    assert "private-error-marker" not in call.model_dump_json()
    if enabled:
        data = json.loads(call.input.text)
        assert data["質問"] == "合成の質問"
        assert data["既存結果の再利用"] is (mode == "reused")
        output = json.loads(call.output.text)
        assert output["実際に検査関数を呼んだ"] is (mode != "reused")
        if mode == "error":
            assert output["適用した結果"] is None
            assert output["例外の型"] == "RuntimeError"
        else:
            assert output["適用した結果"]["sanitized_text"] == expected.sanitized_text
            assert output["適用した結果"]["allowed"] is expected.allowed
        assert call.visibility == "system_admin"
    else:
        assert call.input.text == call.output.text == ""


async def test_caller_queries_keep_actual_checks_and_filtering() -> None:
    seen: list[str] = []

    class Policy:
        def validate_query(self, question: str) -> GuardrailResult:
            seen.append(question)
            return GuardrailResult(
                allowed=question != "合成の拒否",
                sanitized_text="合成のマスク後" if question != "合成の拒否" else "停止",
                findings=[],
            )

    pipeline = RagPipeline(settings=Settings(_env_file=None), guardrails=cast(Any, Policy()))
    with (
        call_content_policy(lambda: True),
        operation_trace("rag.search", operation_id="synthetic") as summary,
    ):
        assert await pipeline._checked_caller_queries(["合成の許可", "合成の拒否"]) == [
            "合成のマスク後"
        ]
    assert seen == ["合成の許可", "合成の拒否"]
    spans = [item for item in summary.spans if item.attributes.get("pr.stage") == "query_guardrail"]
    assert len(spans) == 2
    assert all(item.status == "done" for item in spans)
