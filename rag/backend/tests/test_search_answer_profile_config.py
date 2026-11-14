"""検索・回答プロファイル(Search Answer Profile)設定解決の単体テスト(DB 非依存)。"""

import pytest
from pydantic import ValidationError

from app.config import get_settings
from app.rag.kb_adapter_config import KnowledgeBaseQueryConfig
from app.rag.search_answer_profile_config import (
    SearchAnswerProfileConfig,
    dump_search_answer_profile_config,
    parse_search_answer_profile_config,
    resolve_search_answer_profile_settings,
)


def test_empty_config_keeps_global_settings() -> None:
    """空設定はグローバルをそのまま返し、上書きは効かない。"""
    settings = get_settings()
    merged, applied = resolve_search_answer_profile_settings(settings, SearchAnswerProfileConfig())
    assert applied is False
    assert merged is settings


def test_query_overrides_apply() -> None:
    """query 設定(回答の検索と生成・安全チェック)はグローバルへ上書きされる。"""
    settings = get_settings()
    config = SearchAnswerProfileConfig(
        knowledge_base_ids=["kb-1", "kb-2"],
        query=KnowledgeBaseQueryConfig(max_anchors_per_document=2),
    )
    merged, applied = resolve_search_answer_profile_settings(settings, config)
    assert applied is True
    assert merged.rag_max_anchors_per_document == 2
    assert merged.guardrails_input_enabled is settings.guardrails_input_enabled
    # グローバルは破壊しない。
    assert settings.guardrails_input_enabled is merged.guardrails_input_enabled


# 定義から消した欄(#4784)。全体の設定だけで決まる欄・画面に無い業務ガイドの照合の 2 欄・旧データ用の
# vector_index_profile と、それより前に消した欄の代表。
RETIRED_QUERY_FIELDS = {
    "neighbor_child_count": 0,
    "rerank_enabled": False,
    "auto_field_filter_enabled": True,
    "graph_expansion_enabled": True,
    "graph_expansion_max_chunks": 3,
    "screen_linking_enabled": True,
    "guide_step_retrieval_enabled": False,
    "guide_step_max_queries": 7,
    "guide_step_deadline_seconds": 37.5,
    "entity_expansion_enabled": False,
    "entity_expansion_max_chunks": 3,
    "section_expansion_enabled": False,
    "section_expansion_max_levels": 2,
    "section_expansion_input_token_percent": 40,
    "support_guide_semantic_match_enabled": True,
    "support_guide_close_match_clarification_enabled": True,
    "vector_index_profile": "accurate",
    "evaluation_suite": "strict_ci",
    "guardrail_policy": "regulated",
    "min_similarity": 0.9,
}


def test_query_has_only_the_applied_fields() -> None:
    """プロファイルの query は、適用する欄だけを持つ(#4784)。"""
    assert set(KnowledgeBaseQueryConfig.model_fields) == {
        "max_anchors_per_document",
        "support_guide_enabled",
    }


@pytest.mark.parametrize("field", sorted(RETIRED_QUERY_FIELDS))
def test_retired_query_field_is_a_validation_error(field: str) -> None:
    """定義から消した欄を送ると検証エラーになる(受け付けて捨てない。#4784)。"""
    with pytest.raises(ValidationError, match=field):
        SearchAnswerProfileConfig.model_validate({"query": {field: RETIRED_QUERY_FIELDS[field]}})


def test_saved_persona_is_ignored_on_load_and_dropped_on_save() -> None:
    """旧 standard の persona(system prompt / 既定言語。#595 で削除)は読み捨て、次回保存で消える。

    他の上書きは生きたまま残す(設定全体を空へ縮退させない)。
    """
    settings = get_settings()
    config = parse_search_answer_profile_config(
        {
            "knowledge_base_ids": ["kb-1"],
            "system_prompt": "合成の system prompt",
            "default_language": "日本語",
            "query": {"max_anchors_per_document": 5},
        }
    )

    assert config.normalized_knowledge_base_ids() == ["kb-1"]
    merged, applied = resolve_search_answer_profile_settings(settings, config)
    assert applied is True
    assert merged.rag_max_anchors_per_document == 5
    dumped = dump_search_answer_profile_config(config)
    assert "system_prompt" not in dumped
    assert "default_language" not in dumped
    assert dumped["query"] == {"max_anchors_per_document": 5, "support_guide_enabled": None}


def test_dump_parse_roundtrip() -> None:
    """dump -> parse で設定が保たれる。"""
    config = SearchAnswerProfileConfig(
        knowledge_base_ids=["kb-1", " kb-1 ", "kb-2"],
        query=KnowledgeBaseQueryConfig(max_anchors_per_document=3),
    )
    restored = parse_search_answer_profile_config(dump_search_answer_profile_config(config))
    assert restored.query.max_anchors_per_document == 3
    # 正規化で重複・空白は取り除かれる。
    assert restored.normalized_knowledge_base_ids() == ["kb-1", "kb-2"]


def test_default_config_does_not_override_settings() -> None:
    """上書きの無い設定は global の Settings をそのまま使う。"""
    settings = get_settings()
    merged, applied = resolve_search_answer_profile_settings(settings, SearchAnswerProfileConfig())
    assert applied is False
    assert merged == settings


def test_saved_serving_mode_is_ignored_and_not_saved_again() -> None:
    """配信モード(serving_mode)は削除した(#1331)。保存済みの値は読み捨て、次回保存で書かない。"""
    config = parse_search_answer_profile_config(
        {"version": 1, "knowledge_base_ids": ["kb-1"], "serving_mode": "single"}
    )
    assert config.normalized_knowledge_base_ids() == ["kb-1"]
    assert "serving_mode" not in dump_search_answer_profile_config(config)
    assert not hasattr(get_settings(), "rag_serving_mode")


def test_parse_tolerates_broken_payload() -> None:
    """壊れた永続値は空設定へ縮退する。"""
    restored = parse_search_answer_profile_config({"query": "not-a-dict"})
    assert restored.normalized_knowledge_base_ids() == []
    assert restored.query == KnowledgeBaseQueryConfig()


def test_support_guide_switch_is_per_profile_and_matching_follows_global_settings() -> None:
    """業務ガイドの使用はプロファイルごと。照合の方法(意味の照合・聞き返し)は全体の設定だけ(#4784)。"""
    for matching in (True, False):
        settings = get_settings().model_copy(
            update={
                "rag_support_guide_enabled": True,
                "rag_support_guide_semantic_match_enabled": matching,
                "rag_support_guide_close_match_clarification_enabled": matching,
            }
        )
        for enabled in [None, False, True]:
            config = parse_search_answer_profile_config(
                {"query": {"support_guide_enabled": enabled}}
            )
            reloaded = parse_search_answer_profile_config(dump_search_answer_profile_config(config))
            resolved, _ = resolve_search_answer_profile_settings(settings, reloaded)
            assert resolved.rag_support_guide_enabled is (enabled is True)
            assert resolved.rag_support_guide_semantic_match_enabled is matching
            assert resolved.rag_support_guide_close_match_clarification_enabled is matching
        missing, _ = resolve_search_answer_profile_settings(
            settings, parse_search_answer_profile_config({})
        )
        assert missing.rag_support_guide_enabled is False
        assert settings.rag_support_guide_enabled is True
