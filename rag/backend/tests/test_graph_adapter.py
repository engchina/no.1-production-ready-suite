"""GraphRAG アダプター(知識グラフ構築の深さプロファイル)のテスト。"""

from app.config import Settings
from app.rag.graph_adapter import (
    GRAPH_PROFILE_ORDER,
    graph_adapter_runtime_settings,
    normalize_graph_profile,
    resolve_graph_adapter,
)
from app.rag.kb_adapter_config import (
    KnowledgeBaseAdapterConfig,
    KnowledgeBaseIngestionConfig,
    resolve_effective_adapter_config,
    resolve_effective_settings,
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


def test_legacy_rag_graph_enabled_is_moved_to_profile_on_load() -> None:
    """legacy フラグは起動時に profile へ移り、構築判定と表示の正本が 1 つになる(#274)。"""
    settings = Settings(rag_graph_profile="off", rag_graph_enabled=True)

    assert settings.rag_graph_profile == "full"
    assert settings.rag_graph_enabled is False
    # 明示の profile は legacy フラグで上書きしない。
    entities = Settings(rag_graph_profile="entities", rag_graph_enabled=True)
    assert entities.rag_graph_profile == "entities"
    assert resolve_graph_adapter(entities).profile == "entities"


def test_document_recipe_off_disables_graph_even_with_legacy_flag() -> None:
    """legacy フラグの環境でも、文書レシピの「構築しない」は構築判定と表示の両方で off(#274)。"""
    global_settings = Settings(rag_graph_profile="off", rag_graph_enabled=True)
    recipe = KnowledgeBaseAdapterConfig(ingestion=KnowledgeBaseIngestionConfig(graph_profile="off"))

    effective = resolve_effective_settings(global_settings, recipe, scope="ingestion")
    inherited = resolve_effective_adapter_config(global_settings, KnowledgeBaseAdapterConfig())

    assert resolve_graph_adapter(effective).enabled is False
    assert effective.rag_graph_profile == "off"
    # 上書きしない文書の実効値(表示)は、取込で実際に使う full と一致する。
    assert inherited.ingestion.graph_profile == "full"
    assert resolve_graph_adapter(global_settings).profile == "full"


def test_runtime_settings_orders_and_marks_selected() -> None:
    runtime = graph_adapter_runtime_settings(Settings(rag_graph_profile="entities"))
    assert tuple(status.name for status in runtime.profiles) == GRAPH_PROFILE_ORDER
    selected = [status.name for status in runtime.profiles if status.selected]
    assert selected == ["entities"]


def test_normalize_graph_profile_defaults() -> None:
    assert normalize_graph_profile("nope") == "off"
    assert normalize_graph_profile("full") == "full"
