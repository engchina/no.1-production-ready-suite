"""Vector Index アダプター(索引/検索精度)のテスト。"""

from app.config import Settings
from app.rag.vector_index_adapter import (
    VECTOR_INDEX_PROFILE_ORDER,
    normalize_vector_index_profile,
    resolve_vector_index_adapter,
    vector_index_adapter_runtime_settings,
)


def test_balanced_respects_existing_target_accuracy_and_current_build() -> None:
    """balanced は既存 oracle_vector_target_accuracy と現行 HNSW ビルドを使う(挙動不変)。"""
    params = resolve_vector_index_adapter(
        Settings(rag_vector_index_profile="balanced", oracle_vector_target_accuracy=95)
    )
    assert params.profile == "balanced"
    assert params.target_accuracy == 95
    assert params.neighbors == 32
    assert params.efconstruction == 500
    assert params.distance == "COSINE"
    assert params.requires_reprovision is False


def test_balanced_honors_custom_target_accuracy_setting() -> None:
    params = resolve_vector_index_adapter(
        Settings(rag_vector_index_profile="balanced", oracle_vector_target_accuracy=90)
    )
    assert params.target_accuracy == 90
    assert params.requires_reprovision is False


def test_accurate_overrides_accuracy_and_requires_reprovision() -> None:
    params = resolve_vector_index_adapter(
        Settings(rag_vector_index_profile="accurate", oracle_vector_target_accuracy=95)
    )
    assert params.target_accuracy == 98
    assert params.neighbors == 48
    assert params.efconstruction == 800
    assert params.requires_reprovision is True


def test_fast_lowers_accuracy_and_requires_reprovision() -> None:
    params = resolve_vector_index_adapter(Settings(rag_vector_index_profile="fast"))
    assert params.target_accuracy == 85
    assert params.neighbors == 16
    assert params.requires_reprovision is True


def test_runtime_settings_orders_and_marks_selected() -> None:
    runtime = vector_index_adapter_runtime_settings(
        Settings(rag_vector_index_profile="accurate")
    )
    assert tuple(status.name for status in runtime.profiles) == VECTOR_INDEX_PROFILE_ORDER
    selected = [status.name for status in runtime.profiles if status.selected]
    assert selected == ["accurate"]


def test_normalize_vector_index_profile_defaults() -> None:
    assert normalize_vector_index_profile("nope") == "balanced"
    assert normalize_vector_index_profile("fast") == "fast"
