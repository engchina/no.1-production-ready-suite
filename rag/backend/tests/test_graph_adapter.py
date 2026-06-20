"""GraphRAG アダプター(知識グラフ構築の深さプロファイル)のテスト。"""

from app.config import Settings
from app.rag.graph_adapter import (
    GRAPH_PROFILE_ORDER,
    graph_adapter_runtime_settings,
    normalize_graph_profile,
    resolve_graph_adapter,
)


def test_off_disables_graph_build() -> None:
    """既定 off は KG を構築しない(現行挙動)。"""
    params = resolve_graph_adapter(Settings(rag_graph_profile="off"))
    assert params.profile == "off"
    assert params.enabled is False
    assert params.build_claims is False
    assert params.build_community_summaries is False


def test_entities_builds_lightweight_kg_without_claims() -> None:
    """entities は entities + relationships のみで claims/community を抑制する。"""
    params = resolve_graph_adapter(Settings(rag_graph_profile="entities"))
    assert params.enabled is True
    assert params.build_claims is False
    assert params.build_community_summaries is False


def test_full_builds_claims_and_community_summaries() -> None:
    params = resolve_graph_adapter(Settings(rag_graph_profile="full"))
    assert params.enabled is True
    assert params.build_claims is True
    assert params.build_community_summaries is True


def test_legacy_rag_graph_enabled_maps_to_full() -> None:
    """legacy `rag_graph_enabled=True` は profile off でも full 相当として後方互換を保つ。"""
    params = resolve_graph_adapter(Settings(rag_graph_profile="off", rag_graph_enabled=True))
    assert params.profile == "full"
    assert params.enabled is True
    assert params.build_claims is True
    assert params.build_community_summaries is True


def test_runtime_settings_orders_and_marks_selected() -> None:
    runtime = graph_adapter_runtime_settings(Settings(rag_graph_profile="entities"))
    assert tuple(status.name for status in runtime.profiles) == GRAPH_PROFILE_ORDER
    selected = [status.name for status in runtime.profiles if status.selected]
    assert selected == ["entities"]


def test_normalize_graph_profile_defaults() -> None:
    assert normalize_graph_profile("nope") == "off"
    assert normalize_graph_profile("full") == "full"
