"""関係情報の構築アダプター(構築する / しない)のテスト。"""

from dataclasses import fields

from pytest import MonkeyPatch
from rag_pipeline_core.graph import GraphResolved
from rag_pipeline_core.stage import GraphStageResponse

from app.config import Settings
from app.rag.graph_adapter import (
    GRAPH_PROFILE_ORDER,
    GraphAdapterParams,
    GraphAdapterRuntimeSettings,
    graph_adapter_runtime_settings,
    normalize_graph_profile,
    resolve_graph_adapter,
)
from app.rag.kb_adapter_config import (
    KnowledgeBaseAdapterConfig,
    KnowledgeBaseIngestionConfig,
    parse_adapter_config,
    resolve_effective_settings,
)
from app.schemas.document import DocumentProcessingConfig


def test_off_disables_graph_build() -> None:
    """既定 off は関係情報を構築しない。"""
    params = resolve_graph_adapter(Settings(rag_graph_profile="off"))
    assert params == GraphAdapterParams(profile="off", enabled=False)


def test_entities_builds_graph() -> None:
    """entities は文書と章・節の見出しのつながり(entity + relationship)を構築する。"""
    params = resolve_graph_adapter(Settings(rag_graph_profile="entities"))
    assert params == GraphAdapterParams(profile="entities", enabled=True)


def test_profiles_are_only_off_and_entities() -> None:
    """選択肢は「構築しない」「構築する」の 2 つ。claims / community summary は作らない(#621)。"""
    assert GRAPH_PROFILE_ORDER == ("off", "entities")
    for dataclass_type in (GraphResolved, GraphAdapterParams, GraphAdapterRuntimeSettings):
        names = {field.name for field in fields(dataclass_type)}
        assert not {name for name in names if "claim" in name or "communit" in name}
    assert not {
        name for name in GraphStageResponse.model_fields if "claim" in name or "communit" in name
    }


def test_document_recipe_off_overrides_global_entities() -> None:
    """文書レシピの「構築しない」は、全体の既定が「構築する」でも構築しない。"""
    global_settings = Settings(rag_graph_profile="entities")
    recipe = KnowledgeBaseAdapterConfig(ingestion=KnowledgeBaseIngestionConfig(graph_profile="off"))

    effective = resolve_effective_settings(global_settings, recipe, scope="ingestion")

    assert resolve_graph_adapter(effective).enabled is False
    assert effective.rag_graph_profile == "off"


def test_stored_full_override_is_read_as_entities_until_migration() -> None:
    """migration の適用前に残る保存値 full は、同じ関係情報を作る entities として読む(#621)。

    KB・文書・レシピ・取込ジョブの上書き全体を検証エラーで失わないため。
    """
    kb_config = parse_adapter_config(
        {"version": 2, "ingestion": {"graph_profile": "full", "chunk_size": 900}}
    )
    assert kb_config.ingestion.graph_profile == "entities"
    assert kb_config.ingestion.chunk_size == 900
    recipe = DocumentProcessingConfig.model_validate({"graph_profile": "FULL"})
    assert recipe.graph_profile == "entities"


def test_graph_temporal_setting_is_removed(monkeypatch: MonkeyPatch) -> None:
    """未実装だった Temporal GraphRAG の設定は持たず、旧 env も読まない(#301)。"""
    monkeypatch.setenv("RAG_GRAPH_TEMPORAL_ENABLED", "true")
    settings = Settings(rag_graph_profile="entities")

    assert "rag_graph_temporal_enabled" not in Settings.model_fields
    assert not hasattr(settings, "rag_graph_temporal_enabled")
    # 構築フラグの解決結果・サービス応答・画面の snapshot にも timestamp 付与の項目を残さない。
    for dataclass_type in (GraphResolved, GraphAdapterParams, GraphAdapterRuntimeSettings):
        assert "temporal" not in {field.name for field in fields(dataclass_type)}
    assert "temporal" not in GraphStageResponse.model_fields


def test_runtime_settings_orders_and_marks_selected() -> None:
    runtime = graph_adapter_runtime_settings(Settings(rag_graph_profile="entities"))
    assert tuple(status.name for status in runtime.profiles) == GRAPH_PROFILE_ORDER
    selected = [status.name for status in runtime.profiles if status.selected]
    assert selected == ["entities"]


def test_normalize_graph_profile_defaults() -> None:
    assert normalize_graph_profile("nope") == "off"
    assert normalize_graph_profile("ENTITIES") == "entities"
    # 削除した full は未知の値として既定 off へ寄せる(保存値は migration で書き換える)。
    assert normalize_graph_profile("full") == "off"
