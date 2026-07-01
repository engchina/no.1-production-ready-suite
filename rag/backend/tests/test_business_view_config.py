"""業務ビュー(Business View)設定解決の単体テスト(DB 非依存)。"""

from app.config import get_settings
from app.rag.business_view_config import (
    BusinessViewConfig,
    dump_business_view_config,
    parse_business_view_config,
    resolve_business_view_settings,
)
from app.rag.kb_adapter_config import KnowledgeBaseQueryConfig


def test_empty_config_keeps_global_settings() -> None:
    """空設定はグローバルをそのまま返し、上書きは効かない。"""
    settings = get_settings()
    merged, applied = resolve_business_view_settings(settings, BusinessViewConfig())
    assert applied is False
    assert merged is settings


def test_query_overrides_apply() -> None:
    """query 設定はグローバルへ上書きされる。"""
    settings = get_settings()
    config = BusinessViewConfig(
        knowledge_base_ids=["kb-1", "kb-2"],
        query=KnowledgeBaseQueryConfig(generation_profile="detailed_cited"),
    )
    merged, applied = resolve_business_view_settings(settings, config)
    assert applied is True
    assert merged.rag_generation_profile == "detailed_cited"
    # グローバルは破壊しない。
    assert settings.rag_generation_profile == "grounded_concise"


def test_persona_injects_system_prompt_override() -> None:
    """persona(system_prompt + 既定言語)は generation 上書きへ注入される。"""
    settings = get_settings()
    config = BusinessViewConfig(
        system_prompt="あなたは経理規程アシスタントです。",
        default_language="日本語",
    )
    merged, applied = resolve_business_view_settings(settings, config)
    assert applied is True
    override = merged.rag_generation_system_prompt_override
    assert override is not None
    assert "経理規程アシスタント" in override
    assert "日本語" in override


def test_dump_parse_roundtrip() -> None:
    """dump -> parse で設定が保たれる。"""
    config = BusinessViewConfig(
        knowledge_base_ids=["kb-1", " kb-1 ", "kb-2"],
        query=KnowledgeBaseQueryConfig(retrieval_strategy="vector"),
        system_prompt="persona",
        default_language="ja",
        serving_mode="fused",
    )
    restored = parse_business_view_config(dump_business_view_config(config))
    assert restored.query.retrieval_strategy == "vector"
    assert restored.system_prompt == "persona"
    assert restored.serving_mode == "fused"
    # 正規化で重複・空白は取り除かれる。
    assert restored.normalized_knowledge_base_ids() == ["kb-1", "kb-2"]


def test_serving_mode_defaults_to_fused() -> None:
    """全 active レシピ融合が既定で、不要な上書きを作らない。"""
    settings = get_settings()
    config = BusinessViewConfig()
    assert config.serving_mode == "fused"
    merged, applied = resolve_business_view_settings(settings, config)
    assert applied is False
    assert merged.rag_serving_mode == "fused"


def test_legacy_single_is_normalized_to_fused() -> None:
    """互換読取した single も runtime と次回保存では fused へ正規化する。"""
    settings = get_settings()
    assert settings.rag_serving_mode == "fused"
    config = BusinessViewConfig(serving_mode="single")
    merged, _applied = resolve_business_view_settings(settings, config)
    assert merged.rag_serving_mode == "fused"
    assert dump_business_view_config(config)["serving_mode"] == "fused"
    assert settings.rag_serving_mode == "fused"


def test_parse_tolerates_broken_payload() -> None:
    """壊れた永続値は空設定へ縮退する。"""
    restored = parse_business_view_config({"query": "not-a-dict"})
    assert restored.normalized_knowledge_base_ids() == []
    assert restored.system_prompt is None
