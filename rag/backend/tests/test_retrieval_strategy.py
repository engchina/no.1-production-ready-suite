"""検索 strategy router のテスト。

graph 経路は query 時の Settings でゲートしない(構築深度は文書レシピ単位のため)。
明示要求はそのまま通し、実データの有無は pipeline 側で判断する。
"""

from app.config import Settings
from app.rag.retrieval_strategy import resolve_retrieval_strategy
from app.schemas.search import SearchMode, SearchRequest, SearchStrategy


def test_hybrid_strategy_uses_requested_baseline_mode() -> None:
    settings = Settings.model_construct(rag_graph_enabled=False)
    request = SearchRequest(
        query="承認条件", mode=SearchMode.KEYWORD, strategy=SearchStrategy.HYBRID
    )

    resolved = resolve_retrieval_strategy(request, settings=settings, query=request.query)

    assert resolved.strategy == SearchStrategy.HYBRID
    assert resolved.mode == SearchMode.KEYWORD
    assert resolved.route_reason == "configured_hybrid"
    assert resolved.fallback_reason is None


def test_aggregate_query_uses_baseline_retrieval_without_sql_boundary() -> None:
    settings = Settings.model_construct(rag_graph_enabled=False)
    request = SearchRequest(query="索引済み文書の件数を集計して", mode=SearchMode.HYBRID)

    resolved = resolve_retrieval_strategy(request, settings=settings, query=request.query)

    assert resolved.strategy == SearchStrategy.HYBRID
    assert resolved.mode == SearchMode.HYBRID
    assert resolved.route_reason == "configured_hybrid"
    assert resolved.fallback_reason is None


def test_explicit_graph_strategy_is_preserved_even_when_global_profile_off() -> None:
    """global rag_graph_profile が off でも明示 graph 要求は維持する(レシピ構築を活かす)。"""
    settings = Settings.model_construct(rag_graph_enabled=False, rag_graph_profile="off")
    request = SearchRequest(
        query="文書全体の関係をまとめて",
        mode=SearchMode.VECTOR,
        strategy=SearchStrategy.GRAPH_GLOBAL,
    )

    resolved = resolve_retrieval_strategy(request, settings=settings, query=request.query)

    assert resolved.strategy == SearchStrategy.GRAPH_GLOBAL
    assert resolved.mode == SearchMode.VECTOR
    assert resolved.route_reason == "explicit_graph_global"
    assert resolved.fallback_reason is None


def test_global_graph_is_preserved_with_entities_profile() -> None:
    """entities 構築でも GRAPH_GLOBAL 要求は維持し、縮退判断は実行時に行う。"""
    settings = Settings.model_construct(rag_graph_enabled=False, rag_graph_profile="entities")
    request = SearchRequest(
        query="文書全体の関係をまとめて",
        mode=SearchMode.HYBRID,
        strategy=SearchStrategy.GRAPH_GLOBAL,
    )

    resolved = resolve_retrieval_strategy(request, settings=settings, query=request.query)

    assert resolved.strategy == SearchStrategy.GRAPH_GLOBAL
    assert resolved.route_reason == "explicit_graph_global"
    assert resolved.fallback_reason is None


def test_local_graph_is_preserved_with_entities() -> None:
    settings = Settings.model_construct(rag_graph_enabled=False, rag_graph_profile="entities")
    request = SearchRequest(
        query="承認条件の関係",
        mode=SearchMode.HYBRID,
        strategy=SearchStrategy.GRAPH_LOCAL,
    )

    resolved = resolve_retrieval_strategy(request, settings=settings, query=request.query)

    assert resolved.strategy == SearchStrategy.GRAPH_LOCAL
    assert resolved.route_reason == "explicit_graph_local"
    assert resolved.fallback_reason is None
