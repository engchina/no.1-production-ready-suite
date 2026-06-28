"""Evaluation スイート/閾値の決定論解決(backend / サービス共有)。

suite → CI gate 用の閾値(metric 名→最低値の dict)を決定論で解決する。
Ragas / AutoRAG / FlashRAG 観点の名前付き閾値を束ねる。閾値は素の dict[str, float] で受け渡し、
backend が `EvaluationThresholds` へ写す。request_only は閾値なし(None)。Settings 非依存。
外部評価 SaaS / LLM-as-judge は導入しない(決定論指標のみ)。
"""

from __future__ import annotations

from dataclasses import dataclass

EVALUATION_SUITES: tuple[str, ...] = (
    "request_only",
    "retrieval_focused",
    "balanced",
    "strict_ci",
    "ragas_like",
)
DEFAULT_EVALUATION_SUITE = "request_only"


@dataclass(frozen=True)
class EvaluationSpec:
    name: str
    origin: str
    recommended_for: tuple[str, ...]
    thresholds: dict[str, float] | None  # None は request_only(プリセット閾値なし)


EVALUATION_SPECS: dict[str, EvaluationSpec] = {
    "request_only": EvaluationSpec(
        "request_only", "current_request_thresholds", ("ad_hoc", "manual"), None
    ),
    "retrieval_focused": EvaluationSpec(
        "retrieval_focused",
        "retrieval_quality",
        ("retrieval", "recall"),
        {"precision_at_k": 0.6, "recall_at_k": 0.8, "mrr": 0.7},
    ),
    "balanced": EvaluationSpec(
        "balanced",
        "general_rag",
        ("general", "balanced"),
        {
            "precision_at_k": 0.6,
            "recall_at_k": 0.8,
            "mrr": 0.7,
            "answer_keyword_hit_rate": 0.9,
            "groundedness_pass_rate": 0.9,
        },
    ),
    "strict_ci": EvaluationSpec(
        "strict_ci",
        "strict_ci_gate",
        ("ci", "regression"),
        {
            "precision_at_k": 0.7,
            "recall_at_k": 0.85,
            "mrr": 0.75,
            "answer_keyword_hit_rate": 0.9,
            "groundedness_pass_rate": 0.95,
            "citation_traceability_coverage": 0.9,
        },
    ),
    "ragas_like": EvaluationSpec(
        "ragas_like",
        "ragas",
        ("ragas", "answer_quality"),
        {
            "faithfulness": 0.8,
            "context_precision": 0.7,
            "context_recall": 0.8,
            "response_relevancy": 0.7,
        },
    ),
}


@dataclass(frozen=True)
class EvaluationResolved:
    suite: str
    thresholds: dict[str, float] | None


def normalize_evaluation_suite(value: object) -> str:
    normalized = str(value).casefold()
    return normalized if normalized in EVALUATION_SPECS else DEFAULT_EVALUATION_SUITE


def resolve_evaluation(suite: object) -> EvaluationResolved:
    """suite から CI gate 用閾値 dict を解決する(request_only は None)。"""
    name = normalize_evaluation_suite(suite)
    spec = EVALUATION_SPECS[name]
    thresholds = dict(spec.thresholds) if spec.thresholds is not None else None
    return EvaluationResolved(suite=name, thresholds=thresholds)
