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
    """query 設定(回答の検索と生成・安全チェック)はグローバルへ上書きされる。"""
    settings = get_settings()
    config = BusinessViewConfig(
        knowledge_base_ids=["kb-1", "kb-2"],
        query=KnowledgeBaseQueryConfig(
            docrag_query_strategy="rag_fusion", guardrail_policy="strict"
        ),
    )
    merged, applied = resolve_business_view_settings(settings, config)
    assert applied is True
    assert merged.rag_docrag_query_strategy == "rag_fusion"
    assert merged.rag_guardrail_policy == "strict"
    # グローバルは破壊しない。
    assert settings.rag_docrag_query_strategy == "auto_routing"


def test_legacy_vector_index_is_read_but_not_applied_or_saved() -> None:
    """旧 vector index 値は読めるが、共有索引設定を上書きせず次回保存で除外する。"""
    settings = get_settings()
    config = parse_business_view_config(
        {
            "query": {
                "docrag_answer_flow": "standard_rag",
                "vector_index_profile": "accurate",
            }
        }
    )

    assert config.query.vector_index_profile == "accurate"
    merged, applied = resolve_business_view_settings(settings, config)
    assert applied is True
    assert merged.rag_docrag_answer_flow == "standard_rag"
    assert merged.rag_vector_index_profile == settings.rag_vector_index_profile
    dumped_query = dump_business_view_config(config)["query"]
    assert isinstance(dumped_query, dict)
    assert "vector_index_profile" not in dumped_query


def test_saved_evaluation_suite_is_ignored_on_load_and_dropped_on_save() -> None:
    """業務ビューは品質評価を上書きしない。保存済みの値は読み込み時に無視し、次回保存で消える(#301)。"""
    settings = get_settings()
    config = parse_business_view_config(
        {
            "query": {
                "docrag_rerank_enabled": False,
                "evaluation_suite": "strict_ci",
            }
        }
    )

    # 他の上書きは生きたまま、評価スイートだけを捨てる(設定全体を空へ縮退させない)。
    assert config.query.docrag_rerank_enabled is False
    assert "evaluation_suite" not in KnowledgeBaseQueryConfig.model_fields
    merged, applied = resolve_business_view_settings(settings, config)
    assert applied is True
    assert merged.rag_evaluation_suite == settings.rag_evaluation_suite
    dumped_query = dump_business_view_config(config)["query"]
    assert isinstance(dumped_query, dict)
    assert "evaluation_suite" not in dumped_query
    assert dumped_query["docrag_rerank_enabled"] is False


def test_saved_standard_options_are_ignored_on_load_and_dropped_on_save() -> None:
    """旧 standard の回答エンジンの値(#595 で削除)は読み込み時に捨て、次回保存で消える。

    検索モード・検索オプション・根拠確認・回答スタイルと、persona(system prompt / 既定言語)。
    他の上書きは生きたまま残す(設定全体を空へ縮退させない)。
    """
    settings = get_settings()
    config = parse_business_view_config(
        {
            "knowledge_base_ids": ["kb-1"],
            "system_prompt": "あなたは経理規程アシスタントです。",
            "default_language": "日本語",
            "query": {
                "retrieval_strategy": "business_context_strict",
                "retrieval_query_expansion": False,
                "retrieval_query_expansion_llm": True,
                "retrieval_gap_stop": True,
                "retrieval_corrective": True,
                "retrieval_business_fit_weighting": True,
                "post_retrieval_pipeline": "lean",
                "generation_profile": "structured_json",
                "docrag_neighbor_child_count": 5,
            },
        }
    )

    assert config.normalized_knowledge_base_ids() == ["kb-1"]
    assert config.query.docrag_neighbor_child_count == 5
    merged, applied = resolve_business_view_settings(settings, config)
    assert applied is True
    assert merged.rag_docrag_neighbor_child_count == 5
    dumped = dump_business_view_config(config)
    assert "system_prompt" not in dumped
    assert "default_language" not in dumped
    dumped_query = dumped["query"]
    assert isinstance(dumped_query, dict)
    for removed in (
        "retrieval_strategy",
        "retrieval_query_expansion",
        "retrieval_query_expansion_llm",
        "retrieval_gap_stop",
        "retrieval_corrective",
        "retrieval_business_fit_weighting",
        "post_retrieval_pipeline",
        "generation_profile",
    ):
        assert removed not in dumped_query
    assert dumped_query["docrag_neighbor_child_count"] == 5


def test_dump_parse_roundtrip() -> None:
    """dump -> parse で設定が保たれる。"""
    config = BusinessViewConfig(
        knowledge_base_ids=["kb-1", " kb-1 ", "kb-2"],
        query=KnowledgeBaseQueryConfig(docrag_screen_linking_enabled=True),
        serving_mode="fused",
    )
    restored = parse_business_view_config(dump_business_view_config(config))
    assert restored.query.docrag_screen_linking_enabled is True
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
    assert restored.query == KnowledgeBaseQueryConfig()
