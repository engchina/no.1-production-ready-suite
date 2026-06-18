"""Evaluation アダプター(評価スイート/閾値の手動選択プリセット)。

`vector_index_adapter.py` と同型で、選択された評価閾値スイートと利用可能なプリセット一覧を
非機密の runtime snapshot として返す。Ragas / AutoRAG / FlashRAG 的な評価観点を、外部評価 SaaS
や追加 LLM 呼び出しなしの決定論ヒューリスティック指標の名前付き閾値として束ねる。
"""

from __future__ import annotations

from dataclasses import dataclass

from app.config import EvaluationSuite, Settings
from app.schemas.evaluation import EvaluationThresholds

EvaluationSuiteName = EvaluationSuite
DEFAULT_EVALUATION_SUITE: EvaluationSuiteName = "request_only"
EVALUATION_SUITE_ORDER: tuple[EvaluationSuiteName, ...] = (
    "request_only",
    "retrieval_focused",
    "balanced",
    "strict_ci",
    "ragas_like",
)


@dataclass(frozen=True)
class EvaluationSuiteSpec:
    """1 評価スイートの由来と CI gate 用閾値。"""

    name: EvaluationSuiteName
    origin: str
    recommended_for: tuple[str, ...]
    # None は request_only(プリセット閾値なし)を意味する。
    thresholds: EvaluationThresholds | None
    focus_metrics: tuple[str, ...]


EVALUATION_ADAPTER_SPECS: dict[EvaluationSuiteName, EvaluationSuiteSpec] = {
    "request_only": EvaluationSuiteSpec(
        name="request_only",
        origin="current_request_thresholds",
        recommended_for=("ad_hoc", "manual"),
        thresholds=None,
        focus_metrics=(),
    ),
    "retrieval_focused": EvaluationSuiteSpec(
        name="retrieval_focused",
        origin="retrieval_quality",
        recommended_for=("retrieval", "recall"),
        thresholds=EvaluationThresholds(
            precision_at_k=0.6,
            recall_at_k=0.8,
            mrr=0.7,
        ),
        focus_metrics=("precision_at_k", "recall_at_k", "mrr"),
    ),
    "balanced": EvaluationSuiteSpec(
        name="balanced",
        origin="general_rag",
        recommended_for=("general", "balanced"),
        thresholds=EvaluationThresholds(
            precision_at_k=0.6,
            recall_at_k=0.8,
            mrr=0.7,
            answer_keyword_hit_rate=0.9,
            groundedness_pass_rate=0.9,
        ),
        focus_metrics=(
            "precision_at_k",
            "recall_at_k",
            "mrr",
            "answer_keyword_hit_rate",
            "groundedness_pass_rate",
        ),
    ),
    "strict_ci": EvaluationSuiteSpec(
        name="strict_ci",
        origin="strict_ci_gate",
        recommended_for=("ci", "regression"),
        thresholds=EvaluationThresholds(
            precision_at_k=0.7,
            recall_at_k=0.85,
            mrr=0.75,
            answer_keyword_hit_rate=0.9,
            groundedness_pass_rate=0.95,
            citation_traceability_coverage=0.9,
        ),
        focus_metrics=(
            "precision_at_k",
            "recall_at_k",
            "mrr",
            "groundedness_pass_rate",
            "citation_traceability_coverage",
        ),
    ),
    "ragas_like": EvaluationSuiteSpec(
        name="ragas_like",
        origin="ragas",
        recommended_for=("ragas", "answer_quality"),
        thresholds=EvaluationThresholds(
            faithfulness=0.8,
            context_precision=0.7,
            context_recall=0.8,
            response_relevancy=0.7,
        ),
        focus_metrics=(
            "faithfulness",
            "context_precision",
            "context_recall",
            "response_relevancy",
        ),
    ),
}


@dataclass(frozen=True)
class EvaluationAdapterParams:
    """評価へ渡す解決済みパラメータ。"""

    suite: EvaluationSuiteName
    thresholds: EvaluationThresholds | None
    focus_metrics: tuple[str, ...]


@dataclass(frozen=True)
class EvaluationSuiteStatus:
    """1 評価スイートの選択状態と閾値。"""

    name: EvaluationSuiteName
    origin: str
    recommended_for: tuple[str, ...]
    selected: bool
    thresholds: EvaluationThresholds | None
    focus_metrics: tuple[str, ...]


@dataclass(frozen=True)
class EvaluationAdapterRuntimeSettings:
    """Evaluation アダプターの非機密 runtime snapshot。"""

    suite: EvaluationSuiteName
    thresholds: EvaluationThresholds | None
    focus_metrics: tuple[str, ...]
    suites: tuple[EvaluationSuiteStatus, ...]


def normalize_evaluation_suite(value: object) -> EvaluationSuiteName:
    """未知のスイート名は既定 request_only へ寄せる。"""
    normalized = str(value).casefold()
    if normalized in EVALUATION_ADAPTER_SPECS:
        return normalized
    return DEFAULT_EVALUATION_SUITE


def resolve_evaluation_suite(value: object) -> EvaluationThresholds | None:
    """スイート名から CI gate 用閾値を解決する。request_only は None。"""
    spec = EVALUATION_ADAPTER_SPECS[normalize_evaluation_suite(value)]
    return spec.thresholds


def resolve_evaluation_adapter(settings: Settings) -> EvaluationAdapterParams:
    """Settings から Evaluation アダプターの解決済みパラメータを作る。"""
    suite = normalize_evaluation_suite(
        getattr(settings, "rag_evaluation_suite", DEFAULT_EVALUATION_SUITE)
    )
    spec = EVALUATION_ADAPTER_SPECS[suite]
    return EvaluationAdapterParams(
        suite=suite,
        thresholds=spec.thresholds,
        focus_metrics=spec.focus_metrics,
    )


def evaluation_adapter_runtime_settings(settings: Settings) -> EvaluationAdapterRuntimeSettings:
    """Settings から Evaluation アダプター readiness snapshot を作る。"""
    params = resolve_evaluation_adapter(settings)
    statuses = tuple(
        EvaluationSuiteStatus(
            name=spec.name,
            origin=spec.origin,
            recommended_for=spec.recommended_for,
            selected=spec.name == params.suite,
            thresholds=spec.thresholds,
            focus_metrics=spec.focus_metrics,
        )
        for spec in (EVALUATION_ADAPTER_SPECS[name] for name in EVALUATION_SUITE_ORDER)
    )
    return EvaluationAdapterRuntimeSettings(
        suite=params.suite,
        thresholds=params.thresholds,
        focus_metrics=params.focus_metrics,
        suites=statuses,
    )
