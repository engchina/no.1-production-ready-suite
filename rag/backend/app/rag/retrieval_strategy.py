"""検索 strategy を既存 Oracle retrieval mode へ安全に解決する。"""

from dataclasses import dataclass

from app.config import Settings
from app.schemas.search import SearchMode, SearchRequest, SearchStrategy

GLOBAL_QUERY_HINTS = (
    "全体",
    "全社",
    "横断",
    "傾向",
    "テーマ",
    "要約",
    "まとめ",
    "関係",
    "関連性",
    "across",
    "overall",
    "summarize",
    "summary",
    "theme",
    "relationship",
)
GRAPH_LOCAL_HINTS = (
    "関係",
    "関連",
    "つながり",
    "影響",
    "entity",
    "relationship",
    "related",
)
SELECT_AI_HINTS = (
    "件数",
    "合計",
    "平均",
    "最大",
    "最小",
    "集計",
    "ランキング",
    "count",
    "sum",
    "average",
    "avg",
    "max",
    "min",
)


@dataclass(frozen=True)
class ResolvedRetrievalStrategy:
    """実行する検索経路と fallback の非機密理由。"""

    strategy: SearchStrategy
    mode: SearchMode
    route_reason: str
    fallback_reason: str | None = None
    graph_hit_count: int = 0


def resolve_retrieval_strategy(
    request: SearchRequest,
    *,
    settings: Settings,
    query: str,
) -> ResolvedRetrievalStrategy:
    """SearchRequest.strategy を現行 retrieval 実装へ解決する。"""
    requested = request.strategy
    if requested == SearchStrategy.HYBRID:
        return ResolvedRetrievalStrategy(
            strategy=SearchStrategy.HYBRID,
            mode=SearchMode.HYBRID,
            route_reason="explicit_hybrid",
        )
    if requested == SearchStrategy.SELECT_AI:
        return ResolvedRetrievalStrategy(
            strategy=SearchStrategy.HYBRID,
            mode=SearchMode.HYBRID,
            route_reason="explicit_select_ai",
            fallback_reason="select_ai_uses_dedicated_endpoint",
        )
    if requested in (SearchStrategy.GRAPH_LOCAL, SearchStrategy.GRAPH_GLOBAL):
        return _resolve_graph_strategy(
            requested, settings=settings, route_reason=f"explicit_{requested.value}"
        )

    normalized_query = query.casefold()
    if _contains_any(normalized_query, SELECT_AI_HINTS):
        return ResolvedRetrievalStrategy(
            strategy=SearchStrategy.HYBRID,
            mode=request.mode,
            route_reason="auto_select_ai_candidate",
            fallback_reason="select_ai_uses_dedicated_endpoint",
        )
    if _contains_any(normalized_query, GLOBAL_QUERY_HINTS):
        return _resolve_graph_strategy(
            SearchStrategy.GRAPH_GLOBAL,
            settings=settings,
            route_reason="auto_graph_global_candidate",
            fallback_mode=request.mode,
        )
    if _contains_any(normalized_query, GRAPH_LOCAL_HINTS):
        return _resolve_graph_strategy(
            SearchStrategy.GRAPH_LOCAL,
            settings=settings,
            route_reason="auto_graph_local_candidate",
            fallback_mode=request.mode,
        )
    return ResolvedRetrievalStrategy(
        strategy=SearchStrategy.HYBRID,
        mode=request.mode,
        route_reason="auto_baseline_hybrid",
    )


def _resolve_graph_strategy(
    requested: SearchStrategy,
    *,
    settings: Settings,
    route_reason: str,
    fallback_mode: SearchMode = SearchMode.HYBRID,
) -> ResolvedRetrievalStrategy:
    """GraphRAG-lite が未有効なら既存 retrieval へ戻す。"""
    if not settings.rag_graph_enabled:
        return ResolvedRetrievalStrategy(
            strategy=SearchStrategy.HYBRID,
            mode=fallback_mode,
            route_reason=route_reason,
            fallback_reason="graph_disabled",
        )
    return ResolvedRetrievalStrategy(
        strategy=requested,
        mode=fallback_mode,
        route_reason=route_reason,
    )


def _contains_any(text: str, hints: tuple[str, ...]) -> bool:
    return any(hint.casefold() in text for hint in hints)
