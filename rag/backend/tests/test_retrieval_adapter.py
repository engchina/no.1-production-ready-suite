"""Retrieval アダプター(検索戦略)のテスト。"""

from pytest import MonkeyPatch

from app.clients.pipeline_stage import PipelineStageClient
from app.config import Settings
from app.rag.retrieval_adapter import (
    RETRIEVAL_STRATEGY_ORDER,
    normalize_retrieval_strategy,
    resolve_retrieval_adapter,
    retrieval_adapter_runtime_settings,
)
from app.schemas.search import SearchMode, SearchStrategy


def test_default_strategy_is_hybrid_rrf_with_settings_expansion() -> None:
    """既定は hybrid_rrf で query expansion は settings に従う。"""
    params = resolve_retrieval_adapter(Settings(rag_query_expansion_enabled=True))
    assert params.strategy == "hybrid_rrf"
    assert params.query_expansion is True
    assert params.mode_override is None
    assert params.strategy_bias is None
    assert params.gap_stop is False
    assert params.corrective_retrieval is False
    assert params.business_fit_weighting is False


def test_vector_and_keyword_disable_expansion_and_set_mode() -> None:
    vector = resolve_retrieval_adapter(Settings(rag_retrieval_strategy="vector"))
    assert vector.mode_override == SearchMode.VECTOR
    assert vector.query_expansion is False
    keyword = resolve_retrieval_adapter(Settings(rag_retrieval_strategy="keyword"))
    assert keyword.mode_override == SearchMode.KEYWORD
    assert keyword.query_expansion is False


def test_graph_augmented_sets_strategy_bias() -> None:
    graph = resolve_retrieval_adapter(Settings(rag_retrieval_strategy="graph_augmented"))
    assert graph.strategy_bias == SearchStrategy.GRAPH_GLOBAL


def test_business_context_strict_enables_gap_stop_and_business_fit() -> None:
    params = resolve_retrieval_adapter(Settings(rag_retrieval_strategy="business_context_strict"))
    assert params.gap_stop is True
    assert params.business_fit_weighting is True
    assert params.corrective_retrieval is False


def test_corrective_multi_query_enables_corrective_and_expansion() -> None:
    params = resolve_retrieval_adapter(
        Settings(rag_retrieval_strategy="corrective_multi_query", rag_query_expansion_enabled=False)
    )
    assert params.corrective_retrieval is True
    # spec forces query expansion on regardless of settings.
    assert params.query_expansion is True


def test_runtime_settings_orders_and_marks_selected() -> None:
    runtime = retrieval_adapter_runtime_settings(
        Settings(rag_retrieval_strategy="business_context_strict")
    )
    assert tuple(status.name for status in runtime.strategies) == RETRIEVAL_STRATEGY_ORDER
    selected = [status.name for status in runtime.strategies if status.selected]
    assert selected == ["business_context_strict"]


def test_normalize_retrieval_strategy_defaults() -> None:
    assert normalize_retrieval_strategy("nope") == "hybrid_rrf"
    assert normalize_retrieval_strategy("vector") == "vector"


def test_disabled_retrieval_service_uses_in_process_resolution(
    monkeypatch: MonkeyPatch,
) -> None:
    def fail_if_called(self: PipelineStageClient, request: object) -> object:
        raise AssertionError("disabled retrieval service must not be called")

    monkeypatch.setattr(PipelineStageClient, "run_retrieval", fail_if_called)

    params = resolve_retrieval_adapter(
        Settings(rag_retrieval_service_enabled=False, rag_retrieval_strategy="keyword")
    )

    assert params.mode_override == SearchMode.KEYWORD


def test_enabled_retrieval_service_unreachable_falls_back(
    monkeypatch: MonkeyPatch,
) -> None:
    def unavailable(self: PipelineStageClient, request: object) -> object:
        return None

    monkeypatch.setattr(PipelineStageClient, "run_retrieval", unavailable)

    params = resolve_retrieval_adapter(
        Settings(
            rag_retrieval_strategy="keyword",
            rag_retrieval_service_enabled=True,
            rag_retrieval_service_url="http://svc",
        )
    )
    assert params.mode_override == SearchMode.KEYWORD
