"""設定値の安全な制約テスト。"""

import tomllib
from pathlib import Path

import pytest
from pydantic import ValidationError

from app import config as config_module
from app.config import (
    DEFAULT_LOCAL_STORAGE_DIR,
    DEFAULT_MODEL_SETTINGS_FILE,
    Settings,
    resolve_model_settings_file,
)


def test_chunking_strategy_defaults_to_structure_aware() -> None:
    """Chunking アダプターの既定戦略は structure_aware。"""
    settings = Settings()
    assert settings.rag_chunking_strategy == "structure_aware"
    assert settings.rag_chunk_child_size == 320
    assert settings.rag_chunk_sentence_window_size == 3
    assert settings.rag_chunk_min_chars == 0
    assert settings.rag_chunk_delimiter == "\\n\\n"


def test_chunk_child_size_and_min_chars_must_be_smaller_than_chunk_size() -> None:
    """適用 strategy では child_size / min_chars の誤設定を起動時に拒否する。"""
    with pytest.raises(ValidationError):
        Settings(
            rag_chunking_strategy="hierarchical_parent_child",
            rag_chunk_size=800,
            rag_chunk_child_size=800,
        )
    with pytest.raises(ValidationError):
        Settings(rag_chunk_size=300, rag_chunk_min_chars=300)
    assert Settings(
        rag_chunking_strategy="fixed_size",
        rag_chunk_size=800,
        rag_chunk_child_size=800,
        rag_chunk_min_chars=800,
    )


def test_unknown_chunking_strategy_is_rejected() -> None:
    """未知の chunking 戦略名は Literal で拒否する。"""
    with pytest.raises(ValidationError):
        Settings(rag_chunking_strategy="semantic_double_pass")


def test_retrieval_and_grounding_defaults_match_current_behavior() -> None:
    """Retrieval/Grounding アダプターの既定は現行挙動と一致させる。"""
    settings = Settings()
    assert settings.rag_retrieval_strategy == "hybrid_rrf"
    assert settings.rag_post_retrieval_pipeline == "custom"


def test_unknown_retrieval_strategy_is_rejected() -> None:
    with pytest.raises(ValidationError):
        Settings(rag_retrieval_strategy="hyde_fusion")


def test_unknown_post_retrieval_pipeline_is_rejected() -> None:
    with pytest.raises(ValidationError):
        Settings(rag_post_retrieval_pipeline="agentic_loop")


def test_generation_and_guardrail_defaults_match_current_behavior() -> None:
    """Generation/Guardrail アダプターの既定は現行挙動と一致させる。"""
    settings = Settings()
    assert settings.rag_generation_profile == "grounded_concise"
    assert settings.rag_guardrail_policy == "standard"


def test_unknown_generation_profile_is_rejected() -> None:
    with pytest.raises(ValidationError):
        Settings(rag_generation_profile="chain_of_thought")


def test_unknown_guardrail_policy_is_rejected() -> None:
    with pytest.raises(ValidationError):
        Settings(rag_guardrail_policy="paranoid")


def test_vector_index_profile_defaults_to_balanced() -> None:
    """Vector Index アダプターの既定 balanced は現行挙動と一致させる。"""
    assert Settings().rag_vector_index_profile == "balanced"


def test_unknown_vector_index_profile_is_rejected() -> None:
    with pytest.raises(ValidationError):
        Settings(rag_vector_index_profile="ivf_flat")


def test_evaluation_suite_defaults_to_request_only() -> None:
    """Evaluation アダプターの既定 request_only は現行挙動と一致させる。"""
    assert Settings().rag_evaluation_suite == "request_only"


def test_unknown_evaluation_suite_is_rejected() -> None:
    with pytest.raises(ValidationError):
        Settings(rag_evaluation_suite="autorag_tuner")


def test_graph_profile_defaults_to_off() -> None:
    """GraphRAG アダプターの既定 off は KG 非構築(現行挙動)と一致させる。"""
    assert Settings().rag_graph_profile == "off"


def test_automatic_document_stage_progression_is_enabled_by_default() -> None:
    settings = Settings()
    assert settings.rag_auto_parse_after_preprocess_enabled is True
    assert settings.rag_auto_chunk_after_extract_enabled is True
    assert settings.rag_auto_index_after_chunk_enabled is True


def test_unknown_graph_profile_is_rejected() -> None:
    with pytest.raises(ValidationError):
        Settings(rag_graph_profile="neo4j")


def test_agentic_profile_defaults_to_off() -> None:
    """Agentic アダプターの既定 off は LLM 計画なし(現行挙動)と一致させる。"""
    assert Settings(_env_file=None).rag_agentic_profile == "off"


def test_unknown_agentic_profile_is_rejected() -> None:
    with pytest.raises(ValidationError):
        Settings(rag_agentic_profile="react_agent")


def test_agentic_max_subqueries_defaults_and_bounds() -> None:
    assert Settings().rag_agentic_max_subqueries == 3
    with pytest.raises(ValidationError):
        Settings(rag_agentic_max_subqueries=0)
    with pytest.raises(ValidationError):
        Settings(rag_agentic_max_subqueries=9)


def test_embedding_dimension_is_fixed_to_oracle_vector_width() -> None:
    """embedding 次元は Oracle VECTOR(1536, FLOAT32) と一致させる。"""
    assert Settings().oci_genai_embedding_dim == 1536

    with pytest.raises(ValidationError):
        Settings(oci_genai_embedding_dim=1024)


def test_model_settings_file_defaults_to_relative_env_sibling_path(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """MODEL_SETTINGS_FILE は .env と同じ階層の相対 JSON を既定にする。"""
    monkeypatch.delenv("MODEL_SETTINGS_FILE", raising=False)

    settings = Settings(model_settings_file="")

    assert settings.model_settings_file == DEFAULT_MODEL_SETTINGS_FILE


def test_relative_model_settings_file_resolves_from_backend_root(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """相対 MODEL_SETTINGS_FILE は backend/.env と同じ階層を基準にする。"""
    backend_root = tmp_path / "backend"
    monkeypatch.setattr(config_module, "BACKEND_ROOT", backend_root)

    assert (
        resolve_model_settings_file("model-settings.json")
        == (backend_root / "model-settings.json").resolve()
    )


def test_enterprise_ai_max_retries_defaults_to_three_and_is_bounded() -> None:
    """Enterprise AI の最大リトライ回数は既定 3、0-5 に制限する。"""
    assert Settings().oci_enterprise_ai_max_retries == 3
    assert Settings(oci_enterprise_ai_max_retries=0).oci_enterprise_ai_max_retries == 0
    assert Settings(oci_enterprise_ai_max_retries=5).oci_enterprise_ai_max_retries == 5

    with pytest.raises(ValidationError):
        Settings(oci_enterprise_ai_max_retries=-1)
    with pytest.raises(ValidationError):
        Settings(oci_enterprise_ai_max_retries=6)


def test_http_service_retry_defaults_to_five_attempts_and_is_bounded() -> None:
    """HTTP マイクロサービス共通 retry は既定 5 試行、1-10 に制限する。"""
    settings = Settings()

    assert settings.rag_http_service_retry_attempts == 5
    assert Settings(rag_http_service_retry_attempts=1).rag_http_service_retry_attempts == 1
    assert Settings(rag_http_service_retry_attempts=10).rag_http_service_retry_attempts == 10

    with pytest.raises(ValidationError):
        Settings(rag_http_service_retry_attempts=0)
    with pytest.raises(ValidationError):
        Settings(rag_http_service_retry_attempts=11)


def test_vllm_sidecar_service_url_defaults() -> None:
    """外部 parser / sidecar の URL は Settings で管理する。"""
    settings = Settings()

    assert settings.rag_parser_unlimited_ocr_service_url == "http://parser-unlimited-ocr:8000"
    assert settings.rag_parser_dots_ocr_service_url == "http://parser-dots-ocr:8000"


def test_enterprise_ai_timeout_defaults_to_pdf_friendly_value() -> None:
    """PDF/VLM 取込は 60 秒を超えることがあるため既定 timeout を長めにする。"""
    assert Settings().oci_enterprise_ai_timeout_seconds == 600.0

    with pytest.raises(ValidationError):
        Settings(oci_enterprise_ai_timeout_seconds=0)


def test_enterprise_ai_output_token_limits_are_bounded() -> None:
    """VLM は大きめ、LLM は短めの既定出力上限を持つ。"""
    settings = Settings()

    assert settings.oci_enterprise_ai_llm_max_output_tokens == 1200
    assert settings.oci_enterprise_ai_vlm_max_output_tokens == 65536

    with pytest.raises(ValidationError):
        Settings(oci_enterprise_ai_vlm_max_output_tokens=0)
    with pytest.raises(ValidationError):
        Settings(oci_enterprise_ai_vlm_max_output_tokens=65537)


def test_pdf_segmentation_defaults_are_bounded() -> None:
    """大 PDF は VLM 前に小さな page segment へ分割する。"""
    settings = Settings()

    assert settings.rag_pdf_segmentation_enabled is True
    assert settings.rag_pdf_max_pages_per_segment == 10
    assert settings.rag_pdf_max_segments == 300

    with pytest.raises(ValidationError):
        Settings(rag_pdf_max_pages_per_segment=0)
    with pytest.raises(ValidationError):
        Settings(rag_pdf_max_segments=0)


def test_ingestion_queue_defaults_keep_api_process_non_blocking(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """ローカル既定でも HTTP リクエスト内で取込を実行しない。"""
    for key in (
        "INGESTION_QUEUE_DEDICATED_WORKER_ENABLED",
        "INGESTION_QUEUE_INPROCESS_WORKER_ENABLED",
        "INGESTION_QUEUE_PROCESS_ISOLATION_ENABLED",
        "INGESTION_QUEUE_STALE_RUNNING_SECONDS",
        "INGESTION_JOB_SUBPROCESS_TIMEOUT_SECONDS",
    ):
        monkeypatch.delenv(key, raising=False)
    settings = Settings(_env_file=None)

    assert settings.ingestion_queue_dedicated_worker_enabled is True
    assert settings.ingestion_queue_inprocess_worker_enabled is True
    assert settings.ingestion_queue_process_isolation_enabled is True
    assert settings.ingestion_queue_stale_running_seconds == 300.0
    assert settings.ingestion_job_subprocess_timeout_seconds == 1200.0


def test_parser_adapter_default_is_unstructured(monkeypatch: pytest.MonkeyPatch) -> None:
    """既定 backend は Unstructured(simple 形式の catch-all)。補助 adapter は任意依存で無効。"""
    for key in (
        "RAG_PARSER_ADAPTER_BACKEND",
        "RAG_PARSER_DOCLING_ENABLED",
        "RAG_PARSER_MARKER_ENABLED",
        "RAG_PARSER_UNSTRUCTURED_ENABLED",
        "RAG_PARSER_UNLIMITED_OCR_ENABLED",
        "RAG_PARSER_MINERU_ENABLED",
        "RAG_PARSER_DOTS_OCR_ENABLED",
        "RAG_PARSER_GLM_OCR_ENABLED",
    ):
        monkeypatch.delenv(key, raising=False)
    settings = Settings(_env_file=None)

    assert settings.rag_parser_adapter_backend == "unstructured"
    assert settings.rag_parser_docling_enabled is False
    assert settings.rag_parser_marker_enabled is False
    assert settings.rag_parser_unstructured_enabled is True
    assert settings.rag_parser_unlimited_ocr_enabled is False
    assert settings.rag_parser_mineru_enabled is False
    assert settings.rag_parser_dots_ocr_enabled is False
    assert settings.rag_parser_glm_ocr_enabled is False
    # auto(旧既定)は新既定 unstructured へ。local_partition は baseline 値 local へ。
    # local は正規化せず baseline 値として残す(runtime は ingestion で unstructured へマップ)。
    assert Settings(rag_parser_adapter_backend="auto").rag_parser_adapter_backend == "unstructured"
    assert Settings(rag_parser_adapter_backend="local").rag_parser_adapter_backend == "local"
    assert (
        Settings(rag_parser_adapter_backend="local_partition").rag_parser_adapter_backend == "local"
    )
    assert Settings(rag_parser_adapter_backend="docling").rag_parser_adapter_backend == "docling"
    assert Settings(rag_parser_docling_enabled=True).rag_parser_docling_enabled is True

    with pytest.raises(ValidationError):
        Settings(rag_parser_adapter_backend="llama_parse")


def test_parser_adapter_per_adapter_extras_are_declared_and_conflict_free() -> None:
    """外部 parser はサービス化。backend は per-adapter extra のみ宣言し combined extra は持たない。

    marker(pillow<11)と unstructured(pillow>=11.1)は共存不可のため、combined extra は
    そもそも lock 不能。両 extra を uv conflicts に宣言し universal lock を成立させる。
    """
    pyproject = tomllib.loads(
        (Path(__file__).resolve().parents[1] / "pyproject.toml").read_text(encoding="utf-8")
    )
    optional = pyproject["project"]["optional-dependencies"]

    assert optional["docling"] == ["docling==2.103.0"]
    assert optional["marker"] == ["marker-pdf[full]==1.10.2"]
    assert optional["unstructured"] == ["unstructured[all-docs]==0.23.1"]
    # 共存不可な combined extra は提供しない(サービス分離の理由)。
    assert "parser-adapters" not in optional
    # marker と unstructured は uv conflicts で排他宣言する。
    conflicts = pyproject["tool"]["uv"]["conflicts"]
    conflict_sets = [{entry["extra"] for entry in group} for group in conflicts]
    assert {"marker", "unstructured"} in conflict_sets
    # backend は共有 contract package を path 依存で取り込む。
    assert "rag-parser-core" in pyproject["project"]["dependencies"]


def test_tsv_upload_content_type_is_allowed_by_default() -> None:
    """TSV は table-preserving local parser と整合するため既定 upload whitelist に含める。"""
    assert "text/tab-separated-values" in Settings().allowed_upload_content_types


def test_json_lines_upload_content_types_are_allowed_by_default() -> None:
    """JSONL / NDJSON は local text parser と整合するため既定 upload whitelist に含める。"""
    allowed = set(Settings().allowed_upload_content_types)

    assert {
        "application/jsonl",
        "application/jsonlines",
        "application/ndjson",
        "application/x-ndjson",
    } <= allowed


def test_browser_previewable_image_content_types_are_allowed_by_default() -> None:
    """Enterprise AI image payload と整合する画像 MIME は既定 upload whitelist に含める。"""
    allowed = set(Settings().allowed_upload_content_types)

    assert {"image/gif", "image/jpeg", "image/jpg", "image/png", "image/webp"} <= allowed
    assert {"image/tif", "image/tiff"} <= allowed


def test_semantic_html_and_outlook_msg_content_types_are_allowed_by_default() -> None:
    """SourceProfile が判定できる XHTML / Outlook MSG MIME は upload whitelist に含める。"""
    allowed = set(Settings().allowed_upload_content_types)

    assert "application/xhtml+xml" in allowed
    assert {"application/vnd.ms-outlook", "application/x-msg"} <= allowed


def test_common_audio_content_types_are_allowed_for_explicit_skip_by_default() -> None:
    """音声は取込未対応だが、upload metadata と skipped reason を返せるよう許可する。"""
    allowed = set(Settings().allowed_upload_content_types)

    assert {
        "audio/aac",
        "audio/flac",
        "audio/mp3",
        "audio/mpeg",
        "audio/mp4",
        "audio/ogg",
        "audio/wav",
        "audio/x-m4a",
        "application/ogg",
    } <= allowed


def test_enterprise_ai_response_paths_default_to_known_envelope_detection() -> None:
    """Enterprise AI response path は既定で空にし、既知 envelope を順番に照合する。"""
    settings = Settings()

    assert settings.oci_enterprise_ai_llm_response_path == ""
    assert settings.oci_enterprise_ai_vlm_response_path == ""
    assert (
        Settings(
            oci_enterprise_ai_llm_response_path="/data/text"
        ).oci_enterprise_ai_llm_response_path
        == "/data/text"
    )


def test_chunk_overlap_must_be_smaller_than_chunk_size() -> None:
    """chunk overlap が chunk size 以上の誤設定は起動時に拒否する。"""
    with pytest.raises(ValidationError, match="RAG_CHUNK_OVERLAP"):
        Settings(rag_chunk_size=400, rag_chunk_overlap=400)


def test_upload_storage_backend_is_local_or_oci() -> None:
    """アップロード原本の保存先は local / oci のみ許可する。"""
    assert Settings().upload_storage_backend == "local"
    assert Settings(upload_storage_backend="oci").upload_storage_backend == "oci"

    with pytest.raises(ValidationError):
        Settings(upload_storage_backend="s3")


def test_local_storage_dir_defaults_to_u01_persistent_path() -> None:
    """local 保存の既定ディレクトリは /u01 配下の永続化想定パスにする。"""
    assert Settings().local_storage_dir == DEFAULT_LOCAL_STORAGE_DIR
    assert DEFAULT_LOCAL_STORAGE_DIR == "/u01/production-ready-rag"


def test_max_upload_bytes_defaults_to_200_mib_and_is_positive() -> None:
    """アップロードサイズ上限は既定 200 MiB、正の値に制限する。"""
    assert Settings().max_upload_bytes == 200 * 1024 * 1024

    with pytest.raises(ValidationError):
        Settings(max_upload_bytes=0)


def test_search_timeout_is_positive() -> None:
    """検索 timeout は正の秒数に制限する。"""
    assert Settings().rag_search_timeout_seconds == 30.0
    assert Settings().dashboard_query_timeout_seconds == 8.0
    assert Settings().db_read_timeout_seconds == 8.0

    with pytest.raises(ValidationError):
        Settings(rag_search_timeout_seconds=0)
    with pytest.raises(ValidationError):
        Settings(dashboard_query_timeout_seconds=0)
    with pytest.raises(ValidationError):
        Settings(db_read_timeout_seconds=0)


def test_oracle_connection_timeouts_are_bounded() -> None:
    """Oracle 接続テストは UI を長時間待たせない短い timeout を持つ。"""
    settings = Settings()

    assert settings.oracle_tcp_connect_timeout_seconds == 10.0
    assert settings.oracle_db_test_timeout_seconds == 15.0
    assert (
        Settings(
            oracle_tcp_connect_timeout_seconds=5, oracle_db_test_timeout_seconds=20
        ).oracle_db_test_timeout_seconds
        == 20
    )

    with pytest.raises(ValidationError):
        Settings(oracle_tcp_connect_timeout_seconds=0)
    with pytest.raises(ValidationError):
        Settings(oracle_db_test_timeout_seconds=0)


def test_oracle_vector_target_accuracy_is_bounded() -> None:
    """Oracle 近似 vector search の target accuracy は 1-100 に制限する。"""
    assert Settings().oracle_vector_target_accuracy == 95
    assert Settings(oracle_vector_target_accuracy=90).oracle_vector_target_accuracy == 90

    with pytest.raises(ValidationError):
        Settings(oracle_vector_target_accuracy=0)
    with pytest.raises(ValidationError):
        Settings(oracle_vector_target_accuracy=101)


def test_rag_rrf_k_is_bounded() -> None:
    """Hybrid retrieval の RRF 定数は正の安全な範囲に制限する。"""
    assert Settings().rag_rrf_k == 60
    assert Settings(rag_rrf_k=10).rag_rrf_k == 10

    with pytest.raises(ValidationError):
        Settings(rag_rrf_k=0)
    with pytest.raises(ValidationError):
        Settings(rag_rrf_k=1001)


def test_query_expansion_defaults_and_bounds() -> None:
    """retrieval query expansion は既定有効で、variant 数を制限する。"""
    settings = Settings()

    assert settings.rag_query_expansion_enabled is True
    assert settings.rag_query_expansion_max_variants == 3

    with pytest.raises(ValidationError):
        Settings(rag_query_expansion_max_variants=0)
    with pytest.raises(ValidationError):
        Settings(rag_query_expansion_max_variants=9)


def test_genai_cache_defaults_and_bounds() -> None:
    """embedding / rerank cache は既定有効で、容量を安全範囲に制限する。"""
    settings = Settings()

    assert settings.rag_embedding_cache_enabled is True
    assert settings.rag_embedding_cache_max_entries == 4096
    assert settings.rag_embedding_batch_size == 96
    assert settings.rag_rerank_cache_enabled is True
    assert settings.rag_rerank_cache_max_entries == 1024
    assert Settings(rag_embedding_cache_max_entries=0).rag_embedding_cache_max_entries == 0
    assert Settings(rag_embedding_batch_size=1).rag_embedding_batch_size == 1
    assert Settings(rag_rerank_cache_max_entries=0).rag_rerank_cache_max_entries == 0

    with pytest.raises(ValidationError):
        Settings(rag_embedding_cache_max_entries=-1)
    with pytest.raises(ValidationError):
        Settings(rag_embedding_batch_size=0)
    with pytest.raises(ValidationError):
        Settings(rag_rerank_cache_max_entries=-1)


def test_context_diversity_lambda_defaults_to_disabled_and_is_bounded() -> None:
    """context diversity は既定無効で、MMR 重みは 0-1 に制限する。"""
    assert Settings().rag_context_diversity_lambda == 1.0
    assert Settings(rag_context_diversity_lambda=0.35).rag_context_diversity_lambda == 0.35

    with pytest.raises(ValidationError):
        Settings(rag_context_diversity_lambda=-0.1)
    with pytest.raises(ValidationError):
        Settings(rag_context_diversity_lambda=1.1)


def test_context_group_expansion_defaults_to_disabled_and_is_bounded() -> None:
    """context group expansion は既定無効で、追加 sibling 数を制限する。"""
    settings = Settings()

    assert settings.rag_context_group_expansion_enabled is False
    assert settings.rag_context_group_max_chunks == 4
    assert Settings(rag_context_group_max_chunks=2).rag_context_group_max_chunks == 2

    with pytest.raises(ValidationError):
        Settings(rag_context_group_max_chunks=0)
    with pytest.raises(ValidationError):
        Settings(rag_context_group_max_chunks=21)


def test_context_adaptive_expansion_defaults_to_disabled_and_is_bounded() -> None:
    """adaptive context expansion は既定無効で、window/overlap を制限する。"""
    settings = Settings()

    assert settings.rag_context_adaptive_expansion_enabled is False
    assert settings.rag_context_adaptive_neighbor_window == 1
    assert settings.rag_context_adaptive_min_overlap == 0.08
    assert (
        Settings(
            rag_context_adaptive_expansion_enabled=True,
            rag_context_adaptive_neighbor_window=2,
            rag_context_adaptive_min_overlap=0.2,
        ).rag_context_adaptive_min_overlap
        == 0.2
    )

    with pytest.raises(ValidationError):
        Settings(rag_context_adaptive_neighbor_window=-1)
    with pytest.raises(ValidationError):
        Settings(rag_context_adaptive_neighbor_window=6)
    with pytest.raises(ValidationError):
        Settings(rag_context_adaptive_min_overlap=-0.01)
    with pytest.raises(ValidationError):
        Settings(rag_context_adaptive_min_overlap=1.01)


def test_context_dependency_promotion_defaults_to_disabled_and_is_bounded() -> None:
    """dependency context promotion は既定無効で、追加 chunk 数を制限する。"""
    settings = Settings()

    assert settings.rag_context_dependency_promotion_enabled is False
    assert settings.rag_context_dependency_max_chunks == 4
    assert (
        Settings(
            rag_context_dependency_promotion_enabled=True,
            rag_context_dependency_max_chunks=2,
        ).rag_context_dependency_max_chunks
        == 2
    )

    with pytest.raises(ValidationError):
        Settings(rag_context_dependency_max_chunks=0)
    with pytest.raises(ValidationError):
        Settings(rag_context_dependency_max_chunks=21)


def test_context_compression_defaults_to_disabled_and_is_bounded() -> None:
    """context compression は既定無効で、sentence/文字数上限を制限する。"""
    settings = Settings()

    assert settings.rag_context_compression_enabled is False
    assert settings.rag_context_compression_max_sentences == 3
    assert settings.rag_context_compression_max_chars_per_chunk == 1200

    with pytest.raises(ValidationError):
        Settings(rag_context_compression_max_sentences=0)
    with pytest.raises(ValidationError):
        Settings(rag_context_compression_max_sentences=11)
    with pytest.raises(ValidationError):
        Settings(rag_context_compression_max_chars_per_chunk=199)
    with pytest.raises(ValidationError):
        Settings(rag_context_compression_max_chars_per_chunk=8001)


def test_rate_limit_defaults_protect_expensive_endpoints() -> None:
    """高コスト API の limiter は既定で有効、正の上限を持つ。"""
    settings = Settings()

    assert settings.rate_limit_enabled is True
    assert settings.rate_limit_window_seconds == 60.0
    assert settings.rate_limit_search_requests > 0
    assert settings.rate_limit_evaluation_runs > 0
    assert settings.rate_limit_uploads > 0
    assert settings.rate_limit_ingest_requests > 0

    with pytest.raises(ValidationError):
        Settings(rate_limit_search_requests=0)


def test_sensitive_identifier_masking_is_enabled_by_default() -> None:
    """機微な識別子のマスクは既定で有効にする。"""
    assert Settings().guardrail_mask_sensitive_identifiers is True


def test_audit_context_hash_salt_is_optional() -> None:
    """監査 context hash salt は任意で、既定では空にする。"""
    assert Settings().audit_context_hash_salt == ""
    assert Settings(audit_context_hash_salt="env-provided-salt").audit_context_hash_salt == (
        "env-provided-salt"
    )


def test_trace_exporter_is_disabled_by_default_and_bounded() -> None:
    """trace exporter は既定では無効で、timeout/queue は過大設定を拒否する。"""
    settings = Settings()

    assert settings.trace_export_http_endpoint == ""
    assert settings.trace_export_timeout_seconds == 2.0
    assert settings.trace_export_queue_size == 1024

    with pytest.raises(ValidationError):
        Settings(trace_export_timeout_seconds=0)
    with pytest.raises(ValidationError):
        Settings(trace_export_queue_size=0)
