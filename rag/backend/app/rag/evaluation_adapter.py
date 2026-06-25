"""Evaluation アダプター(評価スイート/閾値の手動選択プリセット)。

suite→CI gate 用閾値の静的解決は共有パッケージ ``rag_pipeline_core.evaluation`` を単一ソースとして
使い、backend と evaluation マイクロサービスが同一結果を返す。`rag_evaluation_service_enabled` が
真のとき設定由来の解決を pipeline-evaluation サービスへ委譲する。無効時は in-process(同一
ロジック)、remote 未到達時も in-process へ縮退する。応答済み remote の HTTP error / 不正応答は
処理停止する。閾値 dict は backend で `EvaluationThresholds`
へ写す。外部評価 SaaS / LLM-as-judge は導入しない(決定論指標のみ)。
"""

from __future__ import annotations

from dataclasses import dataclass

from rag_pipeline_core.evaluation import (
    EVALUATION_SPECS,
    EVALUATION_SUITES,
    resolve_evaluation,
)
from rag_pipeline_core.evaluation import (
    normalize_evaluation_suite as _core_normalize,
)

from app.config import EvaluationSuite, Settings
from app.schemas.evaluation import EvaluationThresholds

EvaluationSuiteName = EvaluationSuite
DEFAULT_EVALUATION_SUITE: EvaluationSuiteName = "request_only"
EVALUATION_SUITE_ORDER: tuple[EvaluationSuiteName, ...] = EVALUATION_SUITES  # type: ignore[assignment]


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
    return _core_normalize(value)  # type: ignore[return-value]


def _thresholds_from_dict(thresholds: dict[str, float] | None) -> EvaluationThresholds | None:
    return EvaluationThresholds(**thresholds) if thresholds is not None else None


def resolve_evaluation_suite(value: object) -> EvaluationThresholds | None:
    """スイート名から CI gate 用閾値を解決する(request_only は None)。in-process(name→閾値)。"""
    return _thresholds_from_dict(resolve_evaluation(value).thresholds)


def resolve_evaluation_adapter(settings: Settings) -> EvaluationAdapterParams:
    """Settings から Evaluation アダプターの解決済みパラメータを作る。

    `rag_evaluation_service_enabled` のときは pipeline-evaluation サービスへ委譲する。
    無効時と remote 未到達時は in-process(同一 rag_pipeline_core ロジック)へ縮退する。
    """
    suite = normalize_evaluation_suite(
        getattr(settings, "rag_evaluation_suite", DEFAULT_EVALUATION_SUITE)
    )
    thresholds, focus = _resolve_static(settings, suite)
    return EvaluationAdapterParams(
        suite=suite,
        thresholds=_thresholds_from_dict(thresholds),
        focus_metrics=focus,
    )


def _resolve_static(
    settings: Settings, suite: str
) -> tuple[dict[str, float] | None, tuple[str, ...]]:
    """静的 (thresholds dict, focus_metrics) を opt-in service / disabled 時 local で解決する。"""
    from rag_pipeline_core.stage import EvaluationStageRequest

    from app.clients.pipeline_stage import PipelineStageClient

    client = PipelineStageClient(settings)
    if client.is_enabled("evaluation"):
        response = client.run_evaluation(EvaluationStageRequest(suite=suite))
        if response is not None:
            return response.thresholds, tuple(response.focus_metrics)
    resolved = resolve_evaluation(suite)
    return resolved.thresholds, resolved.focus_metrics


def evaluation_adapter_runtime_settings(settings: Settings) -> EvaluationAdapterRuntimeSettings:
    """Settings から Evaluation アダプター readiness snapshot を作る。"""
    params = resolve_evaluation_adapter(settings)
    statuses = tuple(
        EvaluationSuiteStatus(
            name=spec.name,  # type: ignore[arg-type]
            origin=spec.origin,
            recommended_for=spec.recommended_for,
            selected=spec.name == params.suite,
            thresholds=_thresholds_from_dict(spec.thresholds),
            focus_metrics=spec.focus_metrics,
        )
        for spec in (EVALUATION_SPECS[name] for name in EVALUATION_SUITES)
    )
    return EvaluationAdapterRuntimeSettings(
        suite=params.suite,
        thresholds=params.thresholds,
        focus_metrics=params.focus_metrics,
        suites=statuses,
    )
