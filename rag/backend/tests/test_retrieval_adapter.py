"""Retrieval アダプター(検索モード + 合成トグル)のテスト。"""

from pytest import MonkeyPatch
from rag_pipeline_core.retrieval import decompose_retrieval_strategy

from app.clients.pipeline_stage import PipelineStageClient
from app.config import Settings
from app.rag.retrieval_adapter import (
    RETRIEVAL_MODE_ORDER,
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
    assert params.legacy_strategy is None


def test_vector_and_keyword_set_mode_and_follow_settings_expansion() -> None:
    """vector/keyword は mode override しつつ query expansion は settings トグルに従う。"""
    vector = resolve_retrieval_adapter(Settings(rag_retrieval_strategy="vector"))
    assert vector.mode_override == SearchMode.VECTOR
    assert vector.query_expansion is True
    keyword = resolve_retrieval_adapter(
        Settings(rag_retrieval_strategy="keyword", rag_query_expansion_enabled=False)
    )
    assert keyword.mode_override == SearchMode.KEYWORD
    assert keyword.query_expansion is False


def test_graph_augmented_sets_strategy_bias() -> None:
    graph = resolve_retrieval_adapter(Settings(rag_retrieval_strategy="graph_augmented"))
    assert graph.strategy_bias == SearchStrategy.GRAPH_GLOBAL


def test_toggles_compose_with_any_mode() -> None:
    """新トグルは任意の検索モードと合成できる(例: graph + corrective、keyword + gap-stop)。"""
    graph = resolve_retrieval_adapter(
        Settings(
            rag_retrieval_strategy="graph_augmented",
            rag_retrieval_corrective_enabled=True,
        )
    )
    assert graph.strategy_bias == SearchStrategy.GRAPH_GLOBAL
    assert graph.corrective_retrieval is True
    keyword = resolve_retrieval_adapter(
        Settings(
            rag_retrieval_strategy="keyword",
            rag_retrieval_gap_stop_enabled=True,
            rag_retrieval_business_fit_weighting_enabled=True,
        )
    )
    assert keyword.mode_override == SearchMode.KEYWORD
    assert keyword.gap_stop is True
    assert keyword.business_fit_weighting is True


def test_legacy_business_context_strict_decomposes_with_forced_toggles() -> None:
    """legacy business_context_strict は hybrid モード + gap-stop + 業務適合加重へ読み替える。"""
    params = resolve_retrieval_adapter(Settings(rag_retrieval_strategy="business_context_strict"))
    assert params.strategy == "hybrid_rrf"
    assert params.legacy_strategy == "business_context_strict"
    assert params.gap_stop is True
    assert params.business_fit_weighting is True
    assert params.corrective_retrieval is False


def test_legacy_corrective_multi_query_forces_expansion_over_settings() -> None:
    """legacy corrective_multi_query は settings 側 OFF でも expansion + corrective を強制する。"""
    params = resolve_retrieval_adapter(
        Settings(rag_retrieval_strategy="corrective_multi_query", rag_query_expansion_enabled=False)
    )
    assert params.strategy == "hybrid_rrf"
    assert params.legacy_strategy == "corrective_multi_query"
    assert params.corrective_retrieval is True
    assert params.query_expansion is True


def test_decompose_retrieval_strategy_table() -> None:
    """decompose の読み替え表: モード素通し / legacy 分解 / pending・未知値の縮退。"""
    for mode in ("hybrid_rrf", "vector", "keyword", "graph_augmented"):
        decomposed = decompose_retrieval_strategy(mode)
        assert decomposed.mode == mode
        assert decomposed.legacy_strategy is None
        assert not any(
            (
                decomposed.forced_query_expansion,
                decomposed.forced_gap_stop,
                decomposed.forced_corrective_retrieval,
                decomposed.forced_business_fit_weighting,
            )
        )
    strict = decompose_retrieval_strategy("business_context_strict")
    assert strict.mode == "hybrid_rrf"
    assert strict.forced_gap_stop and strict.forced_business_fit_weighting
    corrective = decompose_retrieval_strategy("corrective_multi_query")
    assert corrective.mode == "hybrid_rrf"
    assert corrective.forced_query_expansion and corrective.forced_corrective_retrieval
    for value in ("reasoning_tree_search", "colpali_visual_retrieval", "nope"):
        pending = decompose_retrieval_strategy(value)
        assert pending.mode == "hybrid_rrf"
        assert pending.legacy_strategy is None


def test_runtime_settings_exposes_modes_and_legacy_strategy() -> None:
    runtime = retrieval_adapter_runtime_settings(
        Settings(rag_retrieval_strategy="business_context_strict")
    )
    assert tuple(status.name for status in runtime.strategies) == RETRIEVAL_STRATEGY_ORDER
    assert tuple(status.name for status in runtime.modes) == RETRIEVAL_MODE_ORDER
    # 未配線戦略は設定 API の表面から除外する。
    names = {status.name for status in runtime.strategies}
    assert "reasoning_tree_search" not in names
    assert "colpali_visual_retrieval" not in names
    # legacy 値は hybrid モードへ読み替え、読み替え元を legacy_strategy として提示する。
    assert runtime.mode == "hybrid_rrf"
    assert runtime.legacy_strategy == "business_context_strict"
    assert [status.name for status in runtime.modes if status.selected] == ["hybrid_rrf"]
    assert runtime.gap_stop is True
    assert runtime.business_fit_weighting is True


def test_runtime_settings_new_form_has_no_legacy_strategy() -> None:
    runtime = retrieval_adapter_runtime_settings(Settings(rag_retrieval_strategy="keyword"))
    assert runtime.mode == "keyword"
    assert runtime.legacy_strategy is None
    assert [status.name for status in runtime.modes if status.selected] == ["keyword"]


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


def test_remote_resolution_receives_decomposed_mode(monkeypatch: MonkeyPatch) -> None:
    """legacy 値でも remote へはモードのみ渡し、トグル合成は backend 側で行う(新旧混在耐性)。"""
    seen: list[object] = []

    def capture(self: PipelineStageClient, request: object) -> object:
        seen.append(request)
        return None  # 未到達扱い → in-process 縮退

    monkeypatch.setattr(PipelineStageClient, "run_retrieval", capture)

    params = resolve_retrieval_adapter(
        Settings(
            rag_retrieval_strategy="business_context_strict",
            rag_retrieval_service_enabled=True,
            rag_retrieval_service_url="http://svc",
        )
    )
    assert len(seen) == 1
    assert getattr(seen[0], "strategy", None) == "hybrid_rrf"
    assert params.gap_stop is True
    assert params.business_fit_weighting is True
