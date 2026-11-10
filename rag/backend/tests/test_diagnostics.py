"""RAG 診断情報のテスト。"""

import pytest

from app.config import Settings
from app.rag.diagnostics import build_search_diagnostics, rag_config_fingerprint
from app.schemas.search import SearchDiagnostics, SearchRequest


def test_search_diagnostics_exposes_execution_shape_without_secrets() -> None:
    """検索 diagnostics は回答の経路と設定の fingerprint を示し、secret 値を含めない。"""
    settings = Settings(
        oracle_password="super-secret-password",
        rag_guardrails_input_enabled=True,
        rag_guardrails_output_enabled=True,
    )
    request = SearchRequest(
        query="承認条件",
        top_k=7,
        filters={"status": "indexed", "file_name": "policy"},
        knowledge_base_ids=["kb-1", "kb-2"],
    )

    diagnostics = build_search_diagnostics(
        request,
        settings=settings,
        retrieval_strategy_adapter="grounded",
        guardrail_degraded=True,
        answer={"confidence": "high"},
    )

    assert diagnostics.retrieval_strategy == "hybrid"
    assert diagnostics.retrieval_strategy_adapter == "grounded"
    assert diagnostics.guardrails_input_enabled is True
    assert diagnostics.guardrails_output_enabled is True
    assert diagnostics.guardrail_degraded is True
    assert diagnostics.answer == {"confidence": "high"}
    assert diagnostics.filter_keys == ["file_name", "knowledge_base_id", "status"]
    assert diagnostics.knowledge_base_count == 2
    assert len(diagnostics.config_fingerprint) == 64
    assert "super-secret-password" not in diagnostics.model_dump_json()


def test_search_diagnostics_has_no_removed_standard_fields() -> None:
    """旧 standard の回答エンジンの診断(検索の内訳・context の件数など)は持たない(#595)。"""
    fields = set(SearchDiagnostics.model_fields)
    for removed in (
        "mode",
        "retrieval_breakdown",
        "retrieval_candidates",
        "generation_profile",
        "context_expanded_count",
        "agent_memory_retrieved_count",
        "stream_stage_timings",
        "crag_evidence_grade",
    ):
        assert removed not in fields
    # 保存済みの評価結果などに残る古い診断も読める(未知の項目は読み捨てる)。
    legacy = SearchDiagnostics.model_validate(
        {"mode": "hybrid", "retrieval_strategy": "hybrid", "retrieval_breakdown": {}}
    )
    assert legacy.retrieval_strategy == "hybrid"


def test_rag_config_fingerprint_changes_when_rag_parameters_change() -> None:
    """fingerprint は RAG の非機密設定変更を反映する。"""
    first = rag_config_fingerprint(Settings(rag_chunk_size=800))
    second = rag_config_fingerprint(Settings(rag_chunk_size=1200))

    assert first != second


def test_rag_config_fingerprint_changes_when_oracle_vector_accuracy_changes() -> None:
    """fingerprint は Oracle approximate search 精度の変更も反映する。"""
    # 設定の accuracy がそのまま効く balanced で比べる（既定の高精度は 98 固定。#272）。
    first = rag_config_fingerprint(
        Settings(oracle_vector_target_accuracy=95, rag_vector_index_profile="balanced")
    )
    second = rag_config_fingerprint(
        Settings(oracle_vector_target_accuracy=90, rag_vector_index_profile="balanced")
    )

    assert first != second


def test_rag_config_fingerprint_changes_when_rrf_k_changes() -> None:
    """fingerprint は hybrid RRF 定数の変更も反映する。"""
    first = rag_config_fingerprint(Settings(rag_rrf_k=60))
    second = rag_config_fingerprint(Settings(rag_rrf_k=10))

    assert first != second


def test_rag_config_fingerprint_changes_when_context_group_max_chunks_changes() -> None:
    """fingerprint は根拠の group から足す sibling 数の変更も反映する。"""
    first = rag_config_fingerprint(Settings(rag_context_group_max_chunks=4))
    second = rag_config_fingerprint(Settings(rag_context_group_max_chunks=2))

    assert first != second


def test_rag_config_fingerprint_changes_with_vector_index_profile() -> None:
    """fingerprint は検索インデックス profile の違い(検索時 accuracy)を反映する。"""
    fingerprints = {
        rag_config_fingerprint(Settings(rag_vector_index_profile=profile))
        for profile in ("balanced", "accurate", "fast")
    }

    assert len(fingerprints) == 3


# fingerprint に入れない RAG の設定（#4141）。検索・回答の結果に効く設定を足すときは fingerprint に
# 入れ、効かないものだけをここに理由の分類つきで足す。下のテストが分類の漏れを失敗にする。
_FINGERPRINT_EXEMPT_PREFIXES = (
    # 取込の工程。成果は manifest の corpus（chunk_set の id と内容の版）に残る。
    "rag_parser_",
    "rag_preprocess_",
    "rag_pdf_",
    "rag_page_continuation_",
    "rag_heading_level_inference_",
    "rag_navigation_summary_",
    "rag_field_extraction_",
    "rag_auto_",
    # 運用・実行の並行数・待ち時間・再試行・cache。結果の定義を変えない。
    "rag_ai_call_",
    "rag_llm_",
    "rag_http_service_",
    "rag_service_",
    "rag_embedding_",
    "rag_rerank_cache_",
    "rag_evaluation_",
    # 別の機能の管理の設定。回答の入力に入らない。
    "rag_approved_faq_",
    "rag_support_guide_import_",
    "rag_chat_",
)
_FINGERPRINT_EXEMPT_FIELDS = frozenset(
    {
        # 取込の工程。
        "rag_canonical_artifact_prefix",
        "rag_chunk_child_target_chars",
        "rag_chunk_context_header_enabled",
        "rag_chunk_delimiter",
        "rag_chunk_separators",
        "rag_chunk_min_chars",
        "rag_chunk_parent_max_children",
        "rag_chunk_parent_max_pages",
        "rag_chunk_parent_target_chars",
        "rag_chunk_table_child_target_chars",
        "rag_chunking_strategy",
        "rag_entity_attribute_columns",
        "rag_entity_index_enabled",
        "rag_entity_name_columns",
        "rag_extraction_artifact_cache_enabled",
        "rag_extraction_artifact_prefix",
        "rag_graph_profile",
        "rag_review_gate_enabled",
        "rag_segment_checkpoint_enabled",
        "rag_vision_concurrency",
        "rag_vision_enabled",
        # 用途は再解析した版/corpusに固定し、現在のglobal値で既存資料を読み替えない。
        "rag_vision_image_purpose",
        "rag_xml_tree_json_source_url_template",
        # 運用・実行の待ち時間と並行数（同じ入力の回答を変える設定ではない）。
        "rag_answer_record_retention_days",
        "rag_chunk_set_retention_days",
        "rag_chunk_set_retained_versions",
        "rag_answer_timeout_seconds",
        "rag_native_index_full_optimize_interval_hours",
        "rag_rerank_max_documents_per_request",
        "rag_support_guide_max_published_revisions",
        "rag_extraction_max_revisions",
        "rag_section_max_revisions",
        # 要求ごとにプロファイルから解決する値。内容の版は manifest の profiles に残る。
        "rag_answer_contact_override",
        # 確認を通らなかったときのモデル（#4581）。manifest の profiles の escalation_model_id。
        "rag_answer_escalation_model",
        "rag_domain_keywords",
        "rag_guide_preview",
        "rag_language_pack_overrides",
        "rag_runtime_knowledge",
        "rag_search_answer_profile_revision",
        "rag_support_guide",
        # 言語資源の差のファイルの置き場所。値は path で、内容の版ではない。
        "rag_language_pack_file",
        # 形式別の既定（#4732）のファイルの置き場所。値は path で、内容は取込の設定（結果は
        # chunk_set の id に残る）。
        "rag_file_type_defaults_file",
    }
)


def _is_fingerprint_exempt(field: str) -> bool:
    return field in _FINGERPRINT_EXEMPT_FIELDS or field.startswith(_FINGERPRINT_EXEMPT_PREFIXES)


def _changed_value(value: object) -> object:
    """検証を通さない ``model_copy`` に渡す、元と違う値。"""
    if isinstance(value, bool):
        return not value
    if isinstance(value, int | float):
        return value + 1
    if isinstance(value, str):
        return f"{value}-changed"
    if isinstance(value, dict):
        return {**value, "changed": 1}
    if isinstance(value, list):
        return [*value, "changed"]
    return "changed"


_FINGERPRINTED_FIELDS = sorted(
    field
    for field in Settings.model_fields
    if field.startswith("rag_") and not _is_fingerprint_exempt(field)
)


@pytest.mark.parametrize("field", _FINGERPRINTED_FIELDS, ids=_FINGERPRINTED_FIELDS)
def test_rag_config_fingerprint_covers_settings_that_affect_results(field: str) -> None:
    """除外の分類に無い RAG の設定は fingerprint に入れる（入れ忘れを新しい設定でも失敗にする）。"""
    base = Settings()
    changed = base.model_copy(update={field: _changed_value(getattr(base, field))})

    assert rag_config_fingerprint(base) != rag_config_fingerprint(changed)


def test_rag_config_fingerprint_exempt_list_has_only_existing_settings() -> None:
    """消えた設定の名前を除外の一覧に残さない。"""
    assert not _FINGERPRINT_EXEMPT_FIELDS - set(Settings.model_fields)


def test_rag_config_fingerprint_changes_when_candidate_limit_changes() -> None:
    """rerank へ進める基本候補の上限（150 → 1000）で fingerprint が変わる（#4141）。"""
    assert rag_config_fingerprint(
        Settings(rag_retrieval_candidate_limit=150)
    ) != rag_config_fingerprint(Settings(rag_retrieval_candidate_limit=1000))


def test_image_purpose_changes_parse_identity_without_reinterpreting_query_settings() -> None:
    from app.rag.variant_keys import compute_extraction_recipe_id

    first = Settings(rag_vision_image_purpose="record")
    changed = first.model_copy(update={"rag_vision_image_purpose": "example"})
    assert compute_extraction_recipe_id("a" * 64, first) != compute_extraction_recipe_id(
        "a" * 64, changed
    )
    assert rag_config_fingerprint(first) == rag_config_fingerprint(changed)
