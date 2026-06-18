"""Retrieval アダプター(検索段階の手動選択プリセット)。

`parser_adapter_readiness.py` と同型で、選択された検索戦略と利用可能なプリセット一覧を
非機密の runtime snapshot として返す。実際の retrieval 実装は既存の hybrid / vector /
keyword / GraphRAG-lite / Select AI 経路へ解決する。外部検索エンジンは導入しない。
"""

from __future__ import annotations

from dataclasses import dataclass

from app.config import RetrievalStrategy, Settings
from app.schemas.search import SearchMode, SearchStrategy

RetrievalStrategyName = RetrievalStrategy
DEFAULT_RETRIEVAL_STRATEGY: RetrievalStrategyName = "hybrid_rrf"
RETRIEVAL_STRATEGY_ORDER: tuple[RetrievalStrategyName, ...] = (
    "hybrid_rrf",
    "vector",
    "keyword",
    "graph_augmented",
    "select_ai_structured",
    "business_context_strict",
    "corrective_multi_query",
)


@dataclass(frozen=True)
class RetrievalAdapterSpec:
    """1 検索戦略の由来と適用場面(機械可読の非機密 metadata)。"""

    name: RetrievalStrategyName
    origin: str
    recommended_for: tuple[str, ...]
    mode_override: SearchMode | None = None
    strategy_bias: SearchStrategy | None = None
    query_expansion: bool | None = None  # None は settings 既定に従う
    gap_stop: bool = False
    corrective_retrieval: bool = False
    business_fit_weighting: bool = False


RETRIEVAL_ADAPTER_SPECS: dict[RetrievalStrategyName, RetrievalAdapterSpec] = {
    "hybrid_rrf": RetrievalAdapterSpec(
        name="hybrid_rrf",
        origin="oracle_hybrid_rrf",
        recommended_for=("general", "faq", "policy"),
    ),
    "vector": RetrievalAdapterSpec(
        name="vector",
        origin="oracle_ai_vector_search",
        recommended_for=("semantic", "paraphrase"),
        mode_override=SearchMode.VECTOR,
        query_expansion=False,
    ),
    "keyword": RetrievalAdapterSpec(
        name="keyword",
        origin="oracle_text",
        recommended_for=("named_entity", "regulation"),
        mode_override=SearchMode.KEYWORD,
        query_expansion=False,
    ),
    "graph_augmented": RetrievalAdapterSpec(
        name="graph_augmented",
        origin="graphrag_lite",
        recommended_for=("relationship", "cross_document"),
        strategy_bias=SearchStrategy.GRAPH_GLOBAL,
    ),
    "select_ai_structured": RetrievalAdapterSpec(
        name="select_ai_structured",
        origin="oracle_select_ai",
        recommended_for=("aggregate", "structured"),
        strategy_bias=SearchStrategy.SELECT_AI,
    ),
    "business_context_strict": RetrievalAdapterSpec(
        name="business_context_strict",
        origin="aidb_business_context",
        recommended_for=("compliance", "enterprise"),
        gap_stop=True,
        business_fit_weighting=True,
    ),
    "corrective_multi_query": RetrievalAdapterSpec(
        name="corrective_multi_query",
        origin="crag_self_rag",
        recommended_for=("recall_critical", "ambiguous"),
        query_expansion=True,
        corrective_retrieval=True,
    ),
}


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
    normalized = str(value).casefold()
    if normalized in RETRIEVAL_ADAPTER_SPECS:
        return normalized
    return DEFAULT_RETRIEVAL_STRATEGY


def resolve_retrieval_adapter(settings: Settings) -> RetrievalAdapterParams:
    """Settings から Retrieval アダプターの解決済みパラメータを作る。"""
    strategy = normalize_retrieval_strategy(
        getattr(settings, "rag_retrieval_strategy", DEFAULT_RETRIEVAL_STRATEGY)
    )
    spec = RETRIEVAL_ADAPTER_SPECS[strategy]
    settings_expansion = bool(getattr(settings, "rag_query_expansion_enabled", True))
    query_expansion = (
        spec.query_expansion if spec.query_expansion is not None else settings_expansion
    )
    return RetrievalAdapterParams(
        strategy=strategy,
        mode_override=spec.mode_override,
        strategy_bias=spec.strategy_bias,
        query_expansion=query_expansion,
        gap_stop=spec.gap_stop,
        corrective_retrieval=spec.corrective_retrieval,
        business_fit_weighting=spec.business_fit_weighting,
    )


def retrieval_adapter_runtime_settings(settings: Settings) -> RetrievalAdapterRuntimeSettings:
    """Settings から Retrieval アダプター readiness snapshot を作る。"""
    params = resolve_retrieval_adapter(settings)
    statuses = tuple(
        RetrievalStrategyStatus(
            name=spec.name,
            origin=spec.origin,
            recommended_for=spec.recommended_for,
            selected=spec.name == params.strategy,
            gap_stop=spec.gap_stop,
            corrective_retrieval=spec.corrective_retrieval,
            business_fit_weighting=spec.business_fit_weighting,
        )
        for spec in (RETRIEVAL_ADAPTER_SPECS[name] for name in RETRIEVAL_STRATEGY_ORDER)
    )
    return RetrievalAdapterRuntimeSettings(
        strategy=params.strategy,
        query_expansion=params.query_expansion,
        gap_stop=params.gap_stop,
        corrective_retrieval=params.corrective_retrieval,
        business_fit_weighting=params.business_fit_weighting,
        strategies=statuses,
    )
