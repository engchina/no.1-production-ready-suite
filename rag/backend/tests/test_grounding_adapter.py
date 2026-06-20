"""Grounding アダプター(検索後処理)のテスト。"""

from app.config import Settings
from app.rag.grounding_adapter import (
    GROUNDING_PIPELINE_ORDER,
    grounding_adapter_runtime_settings,
    normalize_post_retrieval_pipeline,
    resolve_grounding_adapter,
)


def test_custom_pipeline_honors_existing_flags() -> None:
    """custom は既存の rag_context_* フラグをそのまま反映する(後方互換)。"""
    params = resolve_grounding_adapter(
        Settings(
            rag_post_retrieval_pipeline="custom",
            rag_context_dependency_promotion_enabled=True,
            rag_context_diversity_lambda=0.5,
            rag_context_adaptive_expansion_enabled=True,
            rag_context_compression_enabled=True,
        )
    )
    assert params.pipeline == "custom"
    assert params.dependency_promotion_enabled is True
    assert params.diversity_lambda == 0.5
    assert params.diversity_enabled is True
    assert params.expansion_mode == "adaptive"
    assert params.compression_enabled is True


def test_custom_reproduces_group_plus_neighbor_dual_expansion() -> None:
    """custom で group + neighbor が併走する original 挙動を保つ。"""
    params = resolve_grounding_adapter(
        Settings(
            rag_post_retrieval_pipeline="custom",
            rag_context_group_expansion_enabled=True,
            rag_context_neighbor_window=2,
        )
    )
    assert params.expansion_mode == "group"
    assert params.neighbor_expansion_enabled is True


def test_custom_default_is_inert() -> None:
    """既定の custom は何も拡張しない(現行 default 挙動)。"""
    params = resolve_grounding_adapter(Settings())
    assert params.pipeline == "custom"
    assert params.dependency_promotion_enabled is False
    assert params.diversity_enabled is False
    assert params.expansion_mode == "none"
    assert params.neighbor_expansion_enabled is False
    assert params.compression_enabled is False


def test_lean_disables_all_optional_stages() -> None:
    params = resolve_grounding_adapter(
        Settings(
            rag_post_retrieval_pipeline="lean",
            rag_context_diversity_lambda=0.3,
            rag_context_adaptive_expansion_enabled=True,
        )
    )
    assert params.diversity_enabled is False
    assert params.expansion_mode == "none"
    assert params.neighbor_expansion_enabled is False
    assert params.compression_enabled is False
    assert params.dependency_promotion_enabled is False


def test_verified_context_enables_diversity_only() -> None:
    params = resolve_grounding_adapter(Settings(rag_post_retrieval_pipeline="verified_context"))
    assert params.diversity_enabled is True
    assert params.diversity_lambda < 1.0
    assert params.expansion_mode == "none"
    assert params.dependency_promotion_enabled is False
    assert params.compression_enabled is False


def test_full_governed_enables_all_stages() -> None:
    params = resolve_grounding_adapter(Settings(rag_post_retrieval_pipeline="full_governed"))
    assert params.dependency_promotion_enabled is True
    assert params.diversity_enabled is True
    assert params.expansion_mode == "adaptive"
    assert params.compression_enabled is True


def test_runtime_settings_orders_and_marks_selected() -> None:
    runtime = grounding_adapter_runtime_settings(
        Settings(rag_post_retrieval_pipeline="compact")
    )
    assert tuple(status.name for status in runtime.pipelines) == GROUNDING_PIPELINE_ORDER
    selected = [status.name for status in runtime.pipelines if status.selected]
    assert selected == ["compact"]


def test_normalize_post_retrieval_pipeline_defaults() -> None:
    assert normalize_post_retrieval_pipeline("nope") == "custom"
    assert normalize_post_retrieval_pipeline("full_governed") == "full_governed"
