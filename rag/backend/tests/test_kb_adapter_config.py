"""KB 単位構築設定(kb_adapter_config)の単体テスト。"""

import pytest

from app.config import get_settings
from app.rag.kb_adapter_config import (
    KbAdapterConfigError,
    KnowledgeBaseAdapterConfig,
    KnowledgeBaseQueryConfig,
    apply_adapter_config_or_global,
    compose_query_settings,
    dump_adapter_config,
    parse_adapter_config,
    resolve_effective_adapter_config,
    resolve_effective_settings,
)


def _config(**raw: object) -> KnowledgeBaseAdapterConfig:
    return KnowledgeBaseAdapterConfig.model_validate(raw)


def test_empty_config_returns_global_settings_unchanged() -> None:
    """上書きが無ければグローバル Settings をそのまま返す(同一オブジェクト)。"""
    settings = get_settings()
    config = KnowledgeBaseAdapterConfig()
    assert config.is_empty()
    assert resolve_effective_settings(settings, config, scope="ingestion") is settings
    assert resolve_effective_settings(settings, config, scope="query") is settings


def test_ingestion_scope_overlays_only_ingestion_fields() -> None:
    """ingestion scope は Parser/Chunking だけ上書きし、query 系には触れない。"""
    settings = get_settings()
    config = _config(
        ingestion={"chunking_strategy": "markdown_heading", "chunk_size": 1200},
        query={"generation_profile": "detailed_cited"},
    )

    effective = resolve_effective_settings(settings, config, scope="ingestion")

    assert effective.rag_chunking_strategy == "markdown_heading"
    assert effective.rag_chunk_size == 1200
    # query 系はグローバルのまま。
    assert effective.rag_generation_profile == settings.rag_generation_profile


def test_kb_query_scope_is_legacy_noop() -> None:
    """KB query scope は legacy として読めるが Settings へは反映しない。"""
    settings = get_settings()
    config = _config(
        ingestion={"chunk_size": 1200},
        query={"retrieval_strategy": "vector", "guardrail_policy": "strict"},
    )

    effective = resolve_effective_settings(settings, config, scope="query")

    assert effective is settings
    assert config.settings_overrides("query") == {}


def test_settings_overrides_uses_allowlist_only() -> None:
    """Settings へ渡す上書きは scope の allowlist フィールドに限定される。"""
    config = _config(
        ingestion={"parser_adapter_backend": "docling", "chunk_overlap": 60},
    )
    overrides = config.settings_overrides("ingestion")
    assert overrides == {
        "rag_parser_adapter_backend": "docling",
        # backend 選択時は対応 feature flag も有効化する。
        "rag_parser_docling_enabled": True,
        "rag_chunk_overlap": 60,
    }
    assert config.settings_overrides("query") == {}


def test_external_parser_backend_enables_feature_flag() -> None:
    """外部 parser backend を選ぶと対応 feature flag が有効になる。"""
    for backend, flag_field in (
        ("unstructured", "rag_parser_unstructured_enabled"),
        ("unlimited_ocr", "rag_parser_unlimited_ocr_enabled"),
        ("mineru", "rag_parser_mineru_enabled"),
        ("dots_ocr", "rag_parser_dots_ocr_enabled"),
        ("glm_ocr", "rag_parser_glm_ocr_enabled"),
    ):
        # グローバル既定が無効でも backend 選択で有効化されることを確かめる。
        settings = get_settings().model_copy(update={flag_field: False})
        config = _config(ingestion={"parser_adapter_backend": backend})

        overrides = config.settings_overrides("ingestion")
        assert overrides[flag_field] is True

        effective = resolve_effective_settings(settings, config, scope="ingestion")
        assert effective.rag_parser_adapter_backend == backend
        assert getattr(effective, flag_field) is True


def test_explicit_false_flag_is_not_overridden_by_backend_selection() -> None:
    """KB が flag を明示的に無効化していれば backend 選択でも上書きしない。"""
    config = _config(
        ingestion={
            "parser_adapter_backend": "unstructured",
            "parser_unstructured_enabled": False,
        },
    )
    overrides = config.settings_overrides("ingestion")
    assert overrides["rag_parser_unstructured_enabled"] is False


def test_local_backend_does_not_enable_adapter_flags() -> None:
    """local は特定 adapter flag を有効化しない。"""
    config = _config(ingestion={"parser_adapter_backend": "local"})
    overrides = config.settings_overrides("ingestion")
    assert "rag_parser_docling_enabled" not in overrides
    assert "rag_parser_marker_enabled" not in overrides
    assert "rag_parser_unstructured_enabled" not in overrides


def test_resolve_raises_on_chunk_inconsistency() -> None:
    """overlay 後に chunk_overlap >= chunk_size となる設定は拒否する。"""
    settings = get_settings()
    config = _config(ingestion={"chunk_size": 200, "chunk_overlap": 500})
    with pytest.raises(KbAdapterConfigError):
        resolve_effective_settings(settings, config, scope="ingestion")


def test_apply_or_global_falls_back_on_inconsistency() -> None:
    """堅牢版は矛盾時にグローバルへ縮退し applied=False を返す。"""
    settings = get_settings()
    config = _config(ingestion={"chunk_size": 200, "chunk_overlap": 500})
    merged, applied = apply_adapter_config_or_global(settings, config, scope="ingestion")
    assert merged is settings
    assert applied is False


def test_apply_or_global_ignores_kb_query_override() -> None:
    """KB query の legacy 上書きは applied=False でグローバルのまま返す。"""
    settings = get_settings()
    config = _config(query={"vector_index_profile": "accurate"})
    merged, applied = apply_adapter_config_or_global(settings, config, scope="query")
    assert applied is False
    assert merged is settings


def test_parse_is_tolerant_of_legacy_and_unknown_keys() -> None:
    """旧 free-form retrieval_config や未知キーは捨てて空設定へ縮退する。"""
    assert parse_adapter_config(None).is_empty()
    assert parse_adapter_config({}).is_empty()
    assert parse_adapter_config({"top_k": 20, "rerank": True}).is_empty()
    # 不正な値が混じっていても空へ縮退して例外を出さない。
    assert parse_adapter_config({"ingestion": "broken"}).is_empty()


def test_dump_parse_round_trip() -> None:
    """dump → parse で正規の KB 構築フィールドだけが保たれる。"""
    config = _config(
        ingestion={"chunking_strategy": "page_level", "parser_adapter_backend": "marker"},
        query={"evaluation_suite": "strict_ci"},
    )
    restored = parse_adapter_config(dump_adapter_config(config))
    assert restored.ingestion == config.ingestion
    assert restored.query == KnowledgeBaseQueryConfig()


def test_ingestion_scope_overlays_advanced_axes() -> None:
    """取込側の高度軸(graph / field / asset / nav)も KB 上書きで effective に反映される。"""
    settings = get_settings()
    config = _config(
        ingestion={
            "graph_profile": "entities",
            "field_extraction_enabled": True,
            "asset_summary_enabled": True,
            "navigation_summary_enabled": True,
        },
    )

    overrides = config.settings_overrides("ingestion")
    assert overrides == {
        "rag_graph_profile": "entities",
        "rag_field_extraction_enabled": True,
        "rag_asset_summary_enabled": True,
        "rag_navigation_summary_enabled": True,
    }

    effective = resolve_effective_settings(settings, config, scope="ingestion")
    assert effective.rag_graph_profile == "entities"
    assert effective.rag_field_extraction_enabled is True
    assert effective.rag_asset_summary_enabled is True
    assert effective.rag_navigation_summary_enabled is True
    # query 系は不変。
    assert effective.rag_generation_profile == settings.rag_generation_profile


def test_resolve_effective_adapter_config_fills_inherited_with_global() -> None:
    """構築設定の継承(None)フィールドはグローバル既定で埋められる。"""
    settings = get_settings()
    config = _config(
        ingestion={"chunking_strategy": "page_level"},
        query={"vector_index_profile": "accurate"},
    )

    effective = resolve_effective_adapter_config(settings, config)

    # 上書きフィールドは override 値。
    assert effective.ingestion.chunking_strategy == "page_level"
    # 継承フィールドはグローバル既定で解決され、None にはならない。
    assert effective.ingestion.preprocess_profile == settings.rag_preprocess_profile
    assert effective.ingestion.parser_adapter_backend == settings.rag_parser_adapter_backend
    # query は KB では legacy ignored なので解決済み表示にも出さない。
    assert effective.query == KnowledgeBaseQueryConfig()


def test_resolve_effective_adapter_config_all_global_when_empty() -> None:
    """空設定では全フィールドがグローバル既定で解決される(None が無い)。"""
    settings = get_settings()
    effective = resolve_effective_adapter_config(settings, KnowledgeBaseAdapterConfig())

    assert effective.ingestion.chunking_strategy == settings.rag_chunking_strategy
    assert effective.ingestion.chunk_size == settings.rag_chunk_size
    assert effective.query == KnowledgeBaseQueryConfig()
    # 解決済みは表示専用なので継承判定の元データ(config)は変更しない。
    assert KnowledgeBaseAdapterConfig().is_empty()


def test_compose_query_settings_empty_returns_global() -> None:
    """overlay が空(全継承)ならグローバルをそのまま返し applied=False。"""
    settings = get_settings()
    merged, applied = compose_query_settings(settings, [])
    assert merged is settings
    assert applied is False

    merged2, applied2 = compose_query_settings(
        settings, [KnowledgeBaseQueryConfig(), KnowledgeBaseQueryConfig()]
    )
    assert merged2 is settings
    assert applied2 is False


def test_compose_query_settings_higher_precedence_wins_per_field() -> None:
    """後の overlay(高優先)が同一フィールドを上書きし、別フィールドは両方効く。"""
    settings = get_settings()
    kb = KnowledgeBaseQueryConfig(guardrail_policy="strict", generation_profile="detailed_cited")
    view = KnowledgeBaseQueryConfig(generation_profile="strict_extractive")

    # 低優先=kb, 高優先=view の順で渡す。
    merged, applied = compose_query_settings(settings, [kb, view])

    assert applied is True
    # 同一フィールド(generation)は高優先 view が勝つ。
    assert merged.rag_generation_profile == "strict_extractive"
    # view が触れていない guardrail は下位 overlay の値が残る(per-field merge の肝)。
    assert merged.rag_guardrail_policy == "strict"


def test_compose_query_settings_null_does_not_wipe_lower_layer() -> None:
    """高優先 overlay の None フィールドは下位層の値を消さない。"""
    settings = get_settings()
    kb = KnowledgeBaseQueryConfig(guardrail_policy="strict")
    view = KnowledgeBaseQueryConfig()  # 何も設定しない

    merged, _ = compose_query_settings(settings, [kb, view])
    assert merged.rag_guardrail_policy == "strict"


def test_invalid_literal_value_is_rejected_at_validation() -> None:
    """存在しない戦略名は pydantic バリデーションで弾く。"""
    with pytest.raises(ValueError):
        KnowledgeBaseAdapterConfig.model_validate(
            {"query": {"retrieval_strategy": "does_not_exist"}}
        )
