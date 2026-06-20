"""KB 単位アダプター設定(kb_adapter_config)の単体テスト。"""

import pytest

from app.config import get_settings
from app.rag.kb_adapter_config import (
    KbAdapterConfigError,
    KnowledgeBaseAdapterConfig,
    apply_adapter_config_or_global,
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


def test_query_scope_overlays_only_query_fields() -> None:
    """query scope は Retrieval 以降だけ上書きし、Parser/Chunking には触れない。"""
    settings = get_settings()
    config = _config(
        ingestion={"chunk_size": 1200},
        query={"retrieval_strategy": "vector", "guardrail_policy": "strict"},
    )

    effective = resolve_effective_settings(settings, config, scope="query")

    assert effective.rag_retrieval_strategy == "vector"
    assert effective.rag_guardrail_policy == "strict"
    assert effective.rag_chunk_size == settings.rag_chunk_size


def test_settings_overrides_uses_allowlist_only() -> None:
    """Settings へ渡す上書きは scope の allowlist フィールドに限定される。"""
    config = _config(
        ingestion={"parser_adapter_backend": "docling", "chunk_overlap": 60},
    )
    overrides = config.settings_overrides("ingestion")
    assert overrides == {
        "rag_parser_adapter_backend": "docling",
        # backend 選択時は対応 feature flag も自動有効化する。
        "rag_parser_docling_enabled": True,
        "rag_chunk_overlap": 60,
    }
    assert config.settings_overrides("query") == {}


def test_external_parser_backend_auto_enables_feature_flag() -> None:
    """外部 parser backend を選ぶと対応 feature flag が自動で有効になる。"""
    # グローバル既定が無効でも backend 選択で有効化されることを確かめる。
    settings = get_settings().model_copy(update={"rag_parser_unstructured_enabled": False})
    config = _config(ingestion={"parser_adapter_backend": "unstructured"})

    overrides = config.settings_overrides("ingestion")
    assert overrides["rag_parser_unstructured_enabled"] is True

    effective = resolve_effective_settings(settings, config, scope="ingestion")
    assert effective.rag_parser_adapter_backend == "unstructured"
    assert effective.rag_parser_unstructured_enabled is True


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


def test_local_and_auto_backends_do_not_auto_enable_flags() -> None:
    """local / auto は特定 adapter flag を自動有効化しない。"""
    for backend in ("local", "auto"):
        config = _config(ingestion={"parser_adapter_backend": backend})
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


def test_apply_or_global_reports_applied_true_on_valid_override() -> None:
    """有効な上書きは applied=True で別 Settings を返す。"""
    settings = get_settings()
    config = _config(query={"vector_index_profile": "accurate"})
    merged, applied = apply_adapter_config_or_global(settings, config, scope="query")
    assert applied is True
    assert merged is not settings
    assert merged.rag_vector_index_profile == "accurate"


def test_parse_is_tolerant_of_legacy_and_unknown_keys() -> None:
    """旧 free-form retrieval_config や未知キーは捨てて空設定へ縮退する。"""
    assert parse_adapter_config(None).is_empty()
    assert parse_adapter_config({}).is_empty()
    assert parse_adapter_config({"top_k": 20, "rerank": True}).is_empty()
    # 不正な値が混じっていても空へ縮退して例外を出さない。
    assert parse_adapter_config({"ingestion": "broken"}).is_empty()


def test_dump_parse_round_trip() -> None:
    """dump → parse でフィールドが保たれる。"""
    config = _config(
        ingestion={"chunking_strategy": "page_level", "parser_adapter_backend": "marker"},
        query={"evaluation_suite": "strict_ci"},
    )
    restored = parse_adapter_config(dump_adapter_config(config))
    assert restored.model_dump() == config.model_dump()


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
    """継承(None)フィールドはグローバル既定で埋められ、上書きはそのまま残る。"""
    settings = get_settings()
    config = _config(
        ingestion={"chunking_strategy": "page_level"},
        query={"vector_index_profile": "accurate"},
    )

    effective = resolve_effective_adapter_config(settings, config)

    # 上書きフィールドは override 値。
    assert effective.ingestion.chunking_strategy == "page_level"
    assert effective.query.vector_index_profile == "accurate"
    # 継承フィールドはグローバル既定で解決され、None にはならない。
    assert effective.ingestion.preprocess_profile == settings.rag_preprocess_profile
    assert effective.ingestion.parser_adapter_backend == settings.rag_parser_adapter_backend
    assert effective.query.generation_profile == settings.rag_generation_profile
    assert effective.query.retrieval_strategy == settings.rag_retrieval_strategy


def test_resolve_effective_adapter_config_all_global_when_empty() -> None:
    """空設定では全フィールドがグローバル既定で解決される(None が無い)。"""
    settings = get_settings()
    effective = resolve_effective_adapter_config(settings, KnowledgeBaseAdapterConfig())

    assert effective.ingestion.chunking_strategy == settings.rag_chunking_strategy
    assert effective.ingestion.chunk_size == settings.rag_chunk_size
    assert effective.query.guardrail_policy == settings.rag_guardrail_policy
    assert effective.query.evaluation_suite == settings.rag_evaluation_suite
    # 解決済みは表示専用なので継承判定の元データ(config)は変更しない。
    assert KnowledgeBaseAdapterConfig().is_empty()


def test_invalid_literal_value_is_rejected_at_validation() -> None:
    """存在しない戦略名は pydantic バリデーションで弾く。"""
    with pytest.raises(ValueError):
        KnowledgeBaseAdapterConfig.model_validate(
            {"query": {"retrieval_strategy": "does_not_exist"}}
        )
