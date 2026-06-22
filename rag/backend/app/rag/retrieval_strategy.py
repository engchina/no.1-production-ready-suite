"""検索 strategy を既存 Oracle retrieval mode へ安全に解決する。"""

from dataclasses import dataclass

from app.config import Settings
from app.rag.graph_adapter import resolve_graph_adapter
from app.schemas.search import SearchMode, SearchRequest, SearchStrategy


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
    _ = query
    requested = request.strategy
    if requested == SearchStrategy.HYBRID:
        return ResolvedRetrievalStrategy(
            strategy=SearchStrategy.HYBRID,
            mode=request.mode,
            route_reason="configured_hybrid",
        )
    if requested in (SearchStrategy.GRAPH_LOCAL, SearchStrategy.GRAPH_GLOBAL):
        return _resolve_graph_strategy(
            requested,
            settings=settings,
            route_reason=f"explicit_{requested.value}",
            fallback_mode=request.mode,
        )
    return ResolvedRetrievalStrategy(
        strategy=SearchStrategy.HYBRID,
        mode=request.mode,
        route_reason="configured_hybrid",
    )


def _resolve_graph_strategy(
    requested: SearchStrategy,
    *,
    settings: Settings,
    route_reason: str,
    fallback_mode: SearchMode = SearchMode.HYBRID,
) -> ResolvedRetrievalStrategy:
    """GraphRAG-lite が未有効なら既存 retrieval へ戻す。"""
    if not resolve_graph_adapter(settings).enabled:
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
