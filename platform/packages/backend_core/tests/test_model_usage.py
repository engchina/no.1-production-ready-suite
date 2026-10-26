"""実行のモデルの利用量の集計（`model_usage`。#3419・#3543）のテスト。

本物の `operation_trace` の span の知らせから集める。モデルは呼ばない（usage は合成の値）。
製品の経路（RAG の回答エンジン・NL2SQL のジョブ）を通した確認は各製品のテストにある。
"""

from __future__ import annotations

import pytest

from pr_backend_core.observability.genai import record_genai_usage
from pr_backend_core.observability.model_usage import (
    UNMEASURED_SOURCE_ATTRIBUTE,
    ModelUsageCollector,
    RunModelUsage,
    parse_run_usage,
    run_usage_summary_fields,
    run_usage_totals,
)
from pr_backend_core.observability.tracing import observed_span, operation_trace


def _llm_call(model: str, usage: dict[str, object] | None) -> None:
    with observed_span(
        "gen_ai.chat", kind="client", attributes={"gen_ai.request.model": model}
    ) as span:
        if usage is not None:
            record_genai_usage(span, usage)


def _unmeasured_call(source: str = "select_ai") -> None:
    """アプリが利用量を受け取れない呼び出し（DB の中でモデルを呼ぶ Select AI など）。"""
    with observed_span(
        "oracle.select_ai", kind="client", attributes={UNMEASURED_SOURCE_ATTRIBUTE: source}
    ):
        pass


def test_llm_calls_are_summed_and_other_spans_are_ignored() -> None:
    collector = ModelUsageCollector()
    with operation_trace("run", operation_id="usage-sum", listener=collector.observe):
        with observed_span("stage"):
            _llm_call("model-a", {"input_tokens": 100, "output_tokens": 20})
        _llm_call("model-b", {"prompt_tokens": 50, "completion_tokens": 5})
        with observed_span("gen_ai.embeddings", kind="client"):
            pass

    assert collector.summary(primary_model="model-b") == RunModelUsage(
        models=["model-b", "model-a"],
        requests=2,
        input_tokens=150,
        output_tokens=25,
        total_tokens=175,
        measurement="complete",
        measured_requests=2,
    )


def test_unmeasured_calls_alone_are_unmeasured_without_numbers() -> None:
    """計測できない呼び出しだけの実行は「未計測」（0 token と出さない）。"""
    collector = ModelUsageCollector()
    with operation_trace("run", operation_id="usage-unmeasured", listener=collector.observe):
        _unmeasured_call()
        _unmeasured_call()

    usage = collector.summary()
    assert usage is not None
    assert usage.measurement == "unmeasured"
    assert usage.unmeasured_sources == ["select_ai"]
    assert (usage.requests, usage.measured_requests) == (0, 0)
    assert run_usage_summary_fields(usage) == (None, "unmeasured")


def test_unmeasured_calls_with_llm_calls_are_partial() -> None:
    """計測できる呼び出しと混ざる実行は「一部計測」（計測できた分の数を残す）。"""
    collector = ModelUsageCollector()
    with operation_trace("run", operation_id="usage-mixed", listener=collector.observe):
        _llm_call("model-a", {"input_tokens": 10, "output_tokens": 2})
        _unmeasured_call("select_ai_agent")

    usage = collector.summary()
    assert usage is not None
    assert usage.measurement == "partial"
    assert usage.unmeasured_sources == ["select_ai_agent"]
    assert (usage.requests, usage.measured_requests, usage.total_tokens) == (1, 1, 12)
    assert run_usage_summary_fields(usage) == (12, "partial")


def test_failed_calls_and_truncated_traces_are_partial() -> None:
    collector = ModelUsageCollector()
    with operation_trace("run", operation_id="usage-failed", listener=collector.observe):
        _llm_call("model-a", {"input_tokens": 10, "output_tokens": 2})
        with pytest.raises(TimeoutError), observed_span("gen_ai.chat", kind="client"):
            raise TimeoutError

    usage = collector.summary()
    assert usage is not None
    assert (usage.requests, usage.measured_requests, usage.measurement) == (2, 1, "partial")

    complete = ModelUsageCollector()
    with operation_trace("run", operation_id="usage-truncated", listener=complete.observe):
        _llm_call("model-a", {"input_tokens": 1, "output_tokens": 1})
    assert complete.summary().measurement == "complete"  # type: ignore[union-attr]
    assert complete.summary(truncated=True).measurement == "partial"  # type: ignore[union-attr]


def test_runs_without_llm_calls_are_a_measured_zero_and_unstarted_runs_are_unknown() -> None:
    collector = ModelUsageCollector()
    with operation_trace("run", operation_id="usage-zero", listener=collector.observe):
        pass

    assert collector.summary() == RunModelUsage()
    assert ModelUsageCollector().summary() is None


@pytest.mark.parametrize(
    ("total", "measurement", "expected"),
    [
        (120, "complete", (120, "complete")),
        (7.0, "partial", (7, "partial")),
        (None, "complete", (0, "complete")),
        (0, "unmeasured", (None, "unmeasured")),
        (None, None, (None, None)),
        (5, "broken", (None, None)),
        (-1, "complete", (0, "complete")),
    ],
    ids=["complete", "partial-float", "no-total", "unmeasured", "legacy", "broken", "negative"],
)
def test_run_usage_totals(
    total: object, measurement: object, expected: tuple[int | None, str | None]
) -> None:
    assert run_usage_totals(total, measurement) == expected


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ({"requests": 2, "measurement": "unmeasured", "unmeasured_sources": ["select_ai"]}, 2),
        ({"models": ["m"], "requests": 1}, 1),
        (None, None),
        ("broken", None),
        ({"requests": -1}, None),
    ],
    ids=["unmeasured", "saved-before-sources", "legacy", "broken", "invalid"],
)
def test_parse_run_usage(value: object, expected: int | None) -> None:
    usage = parse_run_usage(value)
    assert (usage.requests if usage is not None else None) == expected
