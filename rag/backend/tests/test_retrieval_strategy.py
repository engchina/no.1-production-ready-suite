"""検索 strategy router のテスト。"""

from app.config import Settings
from app.rag.retrieval_strategy import resolve_retrieval_strategy
from app.schemas.search import SearchMode, SearchRequest, SearchStrategy


def test_explicit_hybrid_strategy_uses_baseline_hybrid_mode() -> None:
    settings = Settings.model_construct(rag_graph_enabled=False)
    request = SearchRequest(
        query="承認条件", mode=SearchMode.KEYWORD, strategy=SearchStrategy.HYBRID
    )

    resolved = resolve_retrieval_strategy(request, settings=settings, query=request.query)

    assert resolved.strategy == SearchStrategy.HYBRID
    assert resolved.mode == SearchMode.HYBRID
    assert resolved.route_reason == "explicit_hybrid"
    assert resolved.fallback_reason is None


def test_auto_graph_candidate_falls_back_when_graph_disabled() -> None:
    settings = Settings.model_construct(rag_graph_enabled=False)
    request = SearchRequest(query="文書全体の関係をまとめて", mode=SearchMode.VECTOR)

    resolved = resolve_retrieval_strategy(request, settings=settings, query=request.query)

    assert resolved.strategy == SearchStrategy.HYBRID
    assert resolved.mode == SearchMode.VECTOR
    assert resolved.route_reason == "auto_graph_global_candidate"
    assert resolved.fallback_reason == "graph_disabled"


def test_select_ai_candidate_keeps_dedicated_endpoint_boundary() -> None:
    settings = Settings.model_construct(rag_graph_enabled=False)
    request = SearchRequest(query="索引済み文書の件数を集計して", mode=SearchMode.HYBRID)

    resolved = resolve_retrieval_strategy(request, settings=settings, query=request.query)

    assert resolved.strategy == SearchStrategy.HYBRID
    assert resolved.mode == SearchMode.HYBRID
    assert resolved.route_reason == "auto_select_ai_candidate"
    assert resolved.fallback_reason == "select_ai_uses_dedicated_endpoint"


def test_graph_strategy_is_preserved_when_graph_enabled() -> None:
    settings = Settings.model_construct(rag_graph_enabled=True)
    request = SearchRequest(query="文書全体の関係をまとめて", mode=SearchMode.HYBRID)

    resolved = resolve_retrieval_strategy(request, settings=settings, query=request.query)

    assert resolved.strategy == SearchStrategy.GRAPH_GLOBAL
    assert resolved.mode == SearchMode.HYBRID
    assert resolved.route_reason == "auto_graph_global_candidate"
    assert resolved.fallback_reason is None
