"""Retrieval アダプター(検索段階の手動選択プリセット)。

strategy→検索挙動の静的解決は共有パッケージ ``rag_pipeline_core.retrieval`` を単一ソースとして
使い、backend と retrieval マイクロサービスが同一結果を返す。`rag_retrieval_service_enabled` が
真のとき静的解決を pipeline-retrieval サービスへ委譲する。無効時は in-process(同一ロジック)、
サービス未起動・未到達時も in-process へ縮退する。応答済み remote の HTTP error / 不正応答は
処理停止。mode/strategy は wire 中立の文字列で受け渡し backend で
SearchMode/SearchStrategy へ写す。実 retrieval は Oracle 26ai 経路を backend が実行する。
外部検索エンジンは導入しない。
"""

from __future__ import annotations

from dataclasses import dataclass

from rag_pipeline_core.retrieval import (
    RETRIEVAL_SPECS,
    WIRED_RETRIEVAL_STRATEGIES,
    resolve_retrieval,
)
from rag_pipeline_core.retrieval import (
    normalize_retrieval_strategy as _core_normalize,
)

from app.config import RetrievalStrategy, Settings
from app.schemas.search import SearchMode, SearchStrategy

RetrievalStrategyName = RetrievalStrategy
DEFAULT_RETRIEVAL_STRATEGY: RetrievalStrategyName = "hybrid_rrf"
# 設定 API が公開する戦略順。未配線(pending_execution)戦略は除外する。
RETRIEVAL_STRATEGY_ORDER: tuple[RetrievalStrategyName, ...] = WIRED_RETRIEVAL_STRATEGIES  # type: ignore[assignment]


@dataclass(frozen=True)
class RetrievalAdapterParams:
    """検索段階へ渡す解決済みパラメータ。"""

    strategy: RetrievalStrategyName
    mode_override: SearchMode | None
    strategy_bias: SearchStrategy | None
    query_expansion: bool
    gap_stop: bool
    corrective_retrieval: bool
    business_fit_weighting: bool


@dataclass(frozen=True)
class RetrievalStrategyStatus:
    """1 検索戦略の選択状態と適用場面。"""

    name: RetrievalStrategyName
    origin: str
    recommended_for: tuple[str, ...]
    selected: bool
    gap_stop: bool
    corrective_retrieval: bool
    business_fit_weighting: bool


@dataclass(frozen=True)
class RetrievalAdapterRuntimeSettings:
    """Retrieval アダプターの非機密 runtime snapshot。"""

    strategy: RetrievalStrategyName
    query_expansion: bool
    gap_stop: bool
    corrective_retrieval: bool
    business_fit_weighting: bool
    strategies: tuple[RetrievalStrategyStatus, ...]


def normalize_retrieval_strategy(value: object) -> RetrievalStrategyName:
    """未知の戦略名は既定 hybrid_rrf へ寄せる。"""
    return _core_normalize(value)  # type: ignore[return-value]


def _as_mode(value: str | None) -> SearchMode | None:
    return SearchMode(value) if value is not None else None


def _as_strategy(value: str | None) -> SearchStrategy | None:
    return SearchStrategy(value) if value is not None else None


def resolve_retrieval_adapter(settings: Settings) -> RetrievalAdapterParams:
    """Settings から Retrieval アダプターの解決済みパラメータを作る。

    `rag_retrieval_service_enabled` のときは pipeline-retrieval サービスへ委譲する。
    無効時と remote 未到達時は in-process(同一 rag_pipeline_core ロジック)へ縮退する。
    """
    strategy = normalize_retrieval_strategy(
        getattr(settings, "rag_retrieval_strategy", DEFAULT_RETRIEVAL_STRATEGY)
    )
    settings_expansion = bool(getattr(settings, "rag_query_expansion_enabled", True))
    resolved = _resolve_static(settings, strategy, settings_expansion)
    return RetrievalAdapterParams(
        strategy=strategy,
        mode_override=_as_mode(resolved.mode_override),
        strategy_bias=_as_strategy(resolved.strategy_bias),
        query_expansion=resolved.query_expansion,
        gap_stop=resolved.gap_stop,
        corrective_retrieval=resolved.corrective_retrieval,
        business_fit_weighting=resolved.business_fit_weighting,
    )


def _resolve_static(settings: Settings, strategy: str, settings_expansion: bool):  # type: ignore[no-untyped-def]
    """検索挙動の静的解決を service opt-in + disabled 時 in-process で行う。"""
    from rag_pipeline_core.retrieval import RetrievalResolved
    from rag_pipeline_core.stage import RetrievalStageRequest

    from app.clients.pipeline_stage import PipelineStageClient

    client = PipelineStageClient(settings)
    if client.is_enabled("retrieval"):
        response = client.run_retrieval(
            RetrievalStageRequest(strategy=strategy, settings_query_expansion=settings_expansion)
        )
        if response is not None:
            return RetrievalResolved(
                strategy=response.strategy,
                mode_override=response.mode_override,
                strategy_bias=response.strategy_bias,
                query_expansion=response.query_expansion,
                gap_stop=response.gap_stop,
                corrective_retrieval=response.corrective_retrieval,
                business_fit_weighting=response.business_fit_weighting,
            )
    return resolve_retrieval(strategy, settings_expansion)


def retrieval_adapter_runtime_settings(settings: Settings) -> RetrievalAdapterRuntimeSettings:
    """Settings から Retrieval アダプター readiness snapshot を作る。"""
    params = resolve_retrieval_adapter(settings)
    statuses = tuple(
        RetrievalStrategyStatus(
            name=spec.name,  # type: ignore[arg-type]
            origin=spec.origin,
            recommended_for=spec.recommended_for,
            selected=spec.name == params.strategy,
            gap_stop=spec.gap_stop,
            corrective_retrieval=spec.corrective_retrieval,
            business_fit_weighting=spec.business_fit_weighting,
        )
        for spec in (RETRIEVAL_SPECS[name] for name in WIRED_RETRIEVAL_STRATEGIES)
    )
    return RetrievalAdapterRuntimeSettings(
        strategy=params.strategy,
        query_expansion=params.query_expansion,
        gap_stop=params.gap_stop,
        corrective_retrieval=params.corrective_retrieval,
        business_fit_weighting=params.business_fit_weighting,
        strategies=statuses,
    )
