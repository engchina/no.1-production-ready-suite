"""アプリケーション設定。

環境変数 / `.env` から読み込む。シークレットはコードにハードコードしない。
"""

from functools import lru_cache
from pathlib import Path
from tempfile import gettempdir
from typing import Literal, Self

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

AiServiceAdapter = Literal["local", "oci"]


class Settings(BaseSettings):
    """環境変数ベースの設定。"""

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # --- アプリ ---
    app_name: str = "production-ready-rag"
    environment: str = Field(default="development")
    log_level: str = Field(default="INFO")
    app_version: str = Field(default="0.1.0")
    ai_service_adapter: AiServiceAdapter = Field(
        default="local",
        description=("local は開発・CI 用の deterministic 実装。oci は実 OCI/Oracle 接続用。"),
    )
    # CORS 許可オリジン（フロントエンド）
    cors_origins: list[str] = Field(default=["http://localhost:3000"])

    # --- OCI 共通 ---
    oci_config_file: str = Field(default="~/.oci/config")
    oci_config_profile: str = Field(default="DEFAULT")
    oci_region: str = Field(default="ap-osaka-1")
    oci_compartment_id: str = Field(default="")

    # --- OCI Enterprise AI（LLM / VLM）---
    # 注意: OCI Generative AI の chat 推論 API ではなく Enterprise AI を使う
    oci_enterprise_ai_endpoint: str = Field(default="")
    oci_enterprise_ai_llm_model: str = Field(default="")
    oci_enterprise_ai_vlm_model: str = Field(default="")

    # --- OCI Generative AI（埋め込み / リランク）---
    oci_genai_embedding_model: str = Field(default="cohere.embed-v4.0")
    oci_genai_embedding_dim: int = Field(
        default=1536,
        ge=1536,
        le=1536,
        description="Cohere Embed v4 と Oracle VECTOR(1536, FLOAT32) に合わせる。",
    )
    oci_genai_rerank_model: str = Field(default="cohere.rerank-v4.0-fast")

    # --- Oracle 26ai ---
    oracle_user: str = Field(default="")
    oracle_password: str = Field(default="")
    oracle_dsn: str = Field(default="")
    oracle_wallet_dir: str = Field(default="")
    oracle_wallet_password: str = Field(default="")
    oracle_select_ai_profile: str = Field(default="")

    # --- OCI Object Storage ---
    object_storage_namespace: str = Field(default="")
    object_storage_bucket: str = Field(default="")

    # --- ローカル参照実装 ---
    local_storage_dir: str = Field(
        default_factory=lambda: str(Path(gettempdir()) / "production-ready-rag")
    )
    max_upload_bytes: int = Field(default=20 * 1024 * 1024, ge=1)
    allowed_upload_content_types: list[str] = Field(
        default=[
            "application/pdf",
            "image/jpeg",
            "image/png",
            "image/tiff",
            "text/plain",
            "application/octet-stream",
        ]
    )

    # --- RAG ---
    rag_chunk_size: int = Field(default=800, ge=200, le=4000)
    rag_chunk_overlap: int = Field(default=120, ge=0, le=1000)
    rag_max_chunks_per_document: int = Field(default=512, ge=1, le=10000)
    rag_context_window_chars: int = Field(default=12000, ge=1000, le=100000)
    rag_min_similarity: float = Field(default=0.05, ge=0.0, le=1.0)
    rag_search_timeout_seconds: float = Field(default=30.0, gt=0.0, le=300.0)

    # --- レート制限（高コスト API の保護）---
    rate_limit_enabled: bool = Field(default=True)
    rate_limit_window_seconds: float = Field(default=60.0, gt=0.0, le=3600.0)
    rate_limit_search_requests: int = Field(default=60, ge=1, le=10000)
    rate_limit_evaluation_runs: int = Field(default=10, ge=1, le=1000)
    rate_limit_uploads: int = Field(default=30, ge=1, le=1000)
    rate_limit_analyze_requests: int = Field(default=20, ge=1, le=1000)
    rate_limit_table_queries: int = Field(default=60, ge=1, le=10000)

    # --- ガードレール ---
    guardrail_max_query_chars: int = Field(default=2000, ge=100, le=20000)
    guardrail_block_prompt_injection: bool = Field(default=True)
    guardrail_mask_sensitive_identifiers: bool = Field(default=True)

    # --- 監査 ---
    audit_context_hash_salt: str = Field(
        default="",
        description="tenant/user id を監査ログへ hash 化するときの任意 salt。Vault 注入を推奨。",
    )

    @model_validator(mode="after")
    def validate_rag_chunk_settings(self) -> Self:
        """chunk overlap が chunk size 以上になる誤設定を起動時に拒否する。"""
        if self.rag_chunk_overlap >= self.rag_chunk_size:
            raise ValueError("RAG_CHUNK_OVERLAP は RAG_CHUNK_SIZE より小さくしてください。")
        return self


@lru_cache
def get_settings() -> Settings:
    """設定のシングルトンを返す。"""
    return Settings()
