"""検索 strategy を既存 Oracle retrieval mode へ安全に解決する。"""

from dataclasses import dataclass

from app.config import Settings
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
    """SearchRequest.strategy を現行 retrieval 実装へ解決する。

    graph 経路の可否はグローバル設定でゲートしない。構築深度は文書レシピ単位のため、
    query 時の Settings(global + Business View overlay)では判定できない。明示要求
    (request 明示 or 検索方法 graph_augmented の bias)はそのまま通し、実データの
    有無は pipeline 側の graph SQL 実行結果で判断する(空なら hybrid へ縮退)。
    """
    _ = query, settings
    requested = request.strategy
    if requested in (SearchStrategy.GRAPH_LOCAL, SearchStrategy.GRAPH_GLOBAL):
        return ResolvedRetrievalStrategy(
            strategy=requested,
            mode=request.mode,
            route_reason=f"explicit_{requested.value}",
        )
    return ResolvedRetrievalStrategy(
        strategy=SearchStrategy.HYBRID,
        mode=request.mode,
        route_reason="configured_hybrid",
    )
