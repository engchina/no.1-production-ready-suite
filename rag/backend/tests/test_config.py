"""設定値の安全な制約テスト。"""

import pytest
from pydantic import ValidationError

from app.config import Settings


def test_embedding_dimension_is_fixed_to_oracle_vector_width() -> None:
    """embedding 次元は Oracle VECTOR(1536, FLOAT32) と一致させる。"""
    assert Settings().oci_genai_embedding_dim == 1536

    with pytest.raises(ValidationError):
        Settings(oci_genai_embedding_dim=1024)


def test_chunk_overlap_must_be_smaller_than_chunk_size() -> None:
    """chunk overlap が chunk size 以上の誤設定は起動時に拒否する。"""
    with pytest.raises(ValidationError, match="RAG_CHUNK_OVERLAP"):
        Settings(rag_chunk_size=400, rag_chunk_overlap=400)


def test_max_chunks_per_document_is_bounded() -> None:
    """1 文書あたり chunk 数の上限は正の値に制限する。"""
    assert Settings().rag_max_chunks_per_document == 512

    with pytest.raises(ValidationError):
        Settings(rag_max_chunks_per_document=0)


def test_search_timeout_is_positive() -> None:
    """検索 timeout は正の秒数に制限する。"""
    assert Settings().rag_search_timeout_seconds == 30.0

    with pytest.raises(ValidationError):
        Settings(rag_search_timeout_seconds=0)


def test_rate_limit_defaults_protect_expensive_endpoints() -> None:
    """高コスト API の limiter は既定で有効、正の上限を持つ。"""
    settings = Settings()

    assert settings.rate_limit_enabled is True
    assert settings.rate_limit_window_seconds == 60.0
    assert settings.rate_limit_search_requests > 0
    assert settings.rate_limit_evaluation_runs > 0
    assert settings.rate_limit_uploads > 0
    assert settings.rate_limit_analyze_requests > 0
    assert settings.rate_limit_table_queries > 0

    with pytest.raises(ValidationError):
        Settings(rate_limit_search_requests=0)


def test_sensitive_identifier_masking_is_enabled_by_default() -> None:
    """機微な識別子のマスクは既定で有効にする。"""
    assert Settings().guardrail_mask_sensitive_identifiers is True


def test_audit_context_hash_salt_is_optional() -> None:
    """監査 context hash salt は任意で、既定では空にする。"""
    assert Settings().audit_context_hash_salt == ""
    assert Settings(audit_context_hash_salt="vault-provided-salt").audit_context_hash_salt == (
        "vault-provided-salt"
    )
