"""Evaluation アダプター(評価スイート/閾値)のテスト。"""

from app.config import Settings
from app.rag.evaluation_adapter import (
    EVALUATION_SUITE_ORDER,
    evaluation_adapter_runtime_settings,
    normalize_evaluation_suite,
    resolve_evaluation_adapter,
    resolve_evaluation_suite,
)


def test_request_only_has_no_preset_thresholds() -> None:
    """既定 request_only はプリセット閾値なし(None)で現行挙動と一致。"""
    assert resolve_evaluation_suite("request_only") is None
    params = resolve_evaluation_adapter(Settings())
    assert params.suite == "request_only"
    assert params.thresholds is None


def test_retrieval_focused_sets_retrieval_thresholds() -> None:
    thresholds = resolve_evaluation_suite("retrieval_focused")
    assert thresholds is not None
    assert thresholds.precision_at_k == 0.6
    assert thresholds.recall_at_k == 0.8
    assert thresholds.mrr == 0.7
    assert thresholds.groundedness_pass_rate is None


def test_balanced_adds_answer_and_groundedness() -> None:
    thresholds = resolve_evaluation_suite("balanced")
    assert thresholds is not None
    assert thresholds.answer_keyword_hit_rate == 0.9
    assert thresholds.groundedness_pass_rate == 0.9


def test_strict_ci_sets_high_thresholds_with_traceability() -> None:
    thresholds = resolve_evaluation_suite("strict_ci")
    assert thresholds is not None
    assert thresholds.groundedness_pass_rate == 0.95
    assert thresholds.citation_traceability_coverage == 0.9


def test_ragas_like_focuses_on_answer_quality_metrics() -> None:
    thresholds = resolve_evaluation_suite("ragas_like")
    assert thresholds is not None
    assert thresholds.faithfulness == 0.8
    assert thresholds.context_precision == 0.7
    assert thresholds.context_recall == 0.8
    assert thresholds.response_relevancy == 0.7


def test_runtime_settings_orders_and_marks_selected() -> None:
    runtime = evaluation_adapter_runtime_settings(Settings(rag_evaluation_suite="ragas_like"))
    assert tuple(status.name for status in runtime.suites) == EVALUATION_SUITE_ORDER
    selected = [status.name for status in runtime.suites if status.selected]
    assert selected == ["ragas_like"]


def test_normalize_evaluation_suite_defaults() -> None:
    assert normalize_evaluation_suite("nope") == "request_only"
    assert normalize_evaluation_suite("strict_ci") == "strict_ci"
