"""設定値の安全な制約テスト。"""

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
    assert settings.rag_pdf_max_pages_per_segment == 3
    assert settings.rag_pdf_max_segments == 300

    with pytest.raises(ValidationError):
        Settings(rag_pdf_max_pages_per_segment=0)
    with pytest.raises(ValidationError):
        Settings(rag_pdf_max_segments=0)


def test_enterprise_ai_response_paths_default_to_auto_detection() -> None:
    """Enterprise AI response path は既定で空にし、既知 envelope 自動判定を使う。"""
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


def test_max_chunks_per_document_is_bounded() -> None:
    """1 文書あたり chunk 数の上限は正の値に制限する。"""
    assert Settings().rag_max_chunks_per_document == 512

    with pytest.raises(ValidationError):
        Settings(rag_max_chunks_per_document=0)


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
