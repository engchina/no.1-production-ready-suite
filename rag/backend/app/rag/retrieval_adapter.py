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
    WIRED_RETRIEVAL_MODES,
    decompose_retrieval_strategy,
    resolve_retrieval,
)
from rag_pipeline_core.retrieval import (
    normalize_retrieval_strategy as _core_normalize,
)

from app.config import RetrievalMode, RetrievalStrategy, Settings
from app.schemas.search import SearchMode, SearchStrategy

RetrievalStrategyName = RetrievalStrategy
RetrievalModeName = RetrievalMode
DEFAULT_RETRIEVAL_STRATEGY: RetrievalStrategyName = "hybrid_rrf"
# 設定 API が公開・保存を受理する検索モード順(未配線戦略と legacy 複合値は含まない)。
RETRIEVAL_MODE_ORDER: tuple[RetrievalModeName, ...] = WIRED_RETRIEVAL_MODES  # type: ignore[assignment]


@dataclass(frozen=True)
class RetrievalAdapterParams:
    """検索段階へ渡す解決済みパラメータ。

    strategy は分解後の検索モード。legacy_strategy は .env / Business View に残る
    legacy 複合戦略の読み替え元(新形式なら None)。トグル4値は
    「settings トグル OR legacy 強制トグル」の合成結果(有効値)。
    """

    strategy: RetrievalModeName
    mode_override: SearchMode | None
    strategy_bias: SearchStrategy | None
    query_expansion: bool
    gap_stop: bool
    corrective_retrieval: bool
    business_fit_weighting: bool
    legacy_strategy: RetrievalStrategyName | None = None


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
    """Retrieval アダプターの非機密 runtime snapshot。

    トグル4値は有効値(settings トグル OR legacy 強制トグル)。legacy_strategy は
    legacy 複合値の読み替え元(新形式なら None)。
    """

    mode: RetrievalModeName
    legacy_strategy: RetrievalStrategyName | None
    query_expansion: bool
    gap_stop: bool
    corrective_retrieval: bool
    business_fit_weighting: bool
    modes: tuple[RetrievalStrategyStatus, ...]


def normalize_retrieval_strategy(value: object) -> RetrievalStrategyName:
    """未知の戦略名は既定 hybrid_rrf へ寄せる。"""
    return _core_normalize(value)  # type: ignore[return-value]


def _as_mode(value: str | None) -> SearchMode | None:
    return SearchMode(value) if value is not None else None


def _as_strategy(value: str | None) -> SearchStrategy | None:
    return SearchStrategy(value) if value is not None else None


def resolve_retrieval_adapter(settings: Settings) -> RetrievalAdapterParams:
    """Settings から Retrieval アダプターの解決済みパラメータを作る。

    strategy 値(legacy 複合値込み)をモード + 強制トグルへ分解し、モードの静的解決
    (mode_override / strategy_bias / query_expansion 既定)へ settings トグルを OR 合成する。
    `rag_retrieval_service_enabled` のときはモード解決を pipeline-retrieval サービスへ委譲する。
    無効時と remote 未到達時は in-process(同一 rag_pipeline_core ロジック)へ縮退する。
    トグルの最終合成は常に backend 側で行うため、新旧 service 混在でも結果は一致する。
    """
    decomposed = decompose_retrieval_strategy(
        getattr(settings, "rag_retrieval_strategy", DEFAULT_RETRIEVAL_STRATEGY)
    )
    settings_expansion = bool(getattr(settings, "rag_query_expansion_enabled", True))
    resolved = _resolve_static(settings, decomposed.mode, settings_expansion)
    return RetrievalAdapterParams(
        strategy=resolved.strategy,
        mode_override=_as_mode(resolved.mode_override),
        strategy_bias=_as_strategy(resolved.strategy_bias),
        query_expansion=resolved.query_expansion or decomposed.forced_query_expansion,
        gap_stop=bool(getattr(settings, "rag_retrieval_gap_stop_enabled", False))
        or resolved.gap_stop
        or decomposed.forced_gap_stop,
        corrective_retrieval=bool(getattr(settings, "rag_retrieval_corrective_enabled", False))
        or resolved.corrective_retrieval
        or decomposed.forced_corrective_retrieval,
        business_fit_weighting=bool(
            getattr(settings, "rag_retrieval_business_fit_weighting_enabled", False)
        )
        or resolved.business_fit_weighting
        or decomposed.forced_business_fit_weighting,
        legacy_strategy=decomposed.legacy_strategy,  # type: ignore[arg-type]
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


def _strategy_statuses(
    names: tuple[str, ...], selected: str
) -> tuple[RetrievalStrategyStatus, ...]:
    return tuple(
        RetrievalStrategyStatus(
            name=spec.name,  # type: ignore[arg-type]
            origin=spec.origin,
            recommended_for=spec.recommended_for,
            selected=spec.name == selected,
            gap_stop=spec.gap_stop,
            corrective_retrieval=spec.corrective_retrieval,
            business_fit_weighting=spec.business_fit_weighting,
        )
        for spec in (RETRIEVAL_SPECS[name] for name in names)
    )


def retrieval_adapter_runtime_settings(
    settings: Settings,
) -> RetrievalAdapterRuntimeSettings:
    """Settings から Retrieval アダプター readiness snapshot を作る。"""
    params = resolve_retrieval_adapter(settings)
    return RetrievalAdapterRuntimeSettings(
        mode=params.strategy,
        legacy_strategy=params.legacy_strategy,
        query_expansion=params.query_expansion,
        gap_stop=params.gap_stop,
        corrective_retrieval=params.corrective_retrieval,
        business_fit_weighting=params.business_fit_weighting,
        modes=_strategy_statuses(WIRED_RETRIEVAL_MODES, params.strategy),
    )
