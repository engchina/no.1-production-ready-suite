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
AuthMode = Literal["local", "production"]
UploadStorageBackend = Literal["local", "oci"]


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
    auth_mode: AuthMode = Field(
        default="local",
        description="local では認証を無効化し、production ではログインを必須にする。",
    )
    auth_username: str = Field(default="")
    auth_password: str = Field(default="")
    auth_session_secret: str = Field(default="")
    auth_session_timeout_seconds: int = Field(default=24 * 60 * 60, ge=60, le=30 * 24 * 60 * 60)
    auth_cookie_name: str = Field(default="production_ready_rag_session")
    auth_cookie_secure: bool = Field(default=False)
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
    oci_enterprise_ai_project_ocid: str = Field(default="")
    oci_enterprise_ai_api_key: str = Field(default="")
    oci_enterprise_ai_llm_model: str = Field(default="")
    oci_enterprise_ai_vlm_model: str = Field(default="")
    oci_enterprise_ai_llm_path: str = Field(default="/responses")
    oci_enterprise_ai_vlm_path: str = Field(default="/responses")
    oci_enterprise_ai_llm_payload_template: str = Field(
        default="",
        description=(
            "Enterprise AI LLM endpoint の request JSON template。空なら標準 RAG payload。"
        ),
    )
    oci_enterprise_ai_vlm_payload_template: str = Field(
        default="",
        description=(
            "Enterprise AI VLM endpoint の request JSON template。空なら標準 OCR payload。"
        ),
    )
    oci_enterprise_ai_llm_response_path: str = Field(
        default="",
        description=(
            "Enterprise AI LLM response から回答候補を取り出す JSON Pointer。"
            "空なら既知 envelope を自動判定する。"
        ),
    )
    oci_enterprise_ai_vlm_response_path: str = Field(
        default="",
        description=(
            "Enterprise AI VLM response から StructuredExtraction 候補を取り出す JSON Pointer。"
            "空なら既知 envelope を自動判定する。"
        ),
    )
    oci_enterprise_ai_timeout_seconds: float = Field(default=60.0, gt=0.0, le=600.0)
    oci_enterprise_ai_max_retries: int = Field(default=3, ge=0, le=5)

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
    oracle_client_lib_dir: str = Field(default="/u01/aipoc/instantclient_23_26")
    oracle_wallet_dir: str = Field(
        default="",
        description=("互換用。Wallet 配置先は ORACLE_CLIENT_LIB_DIR/network/admin へ固定する。"),
    )
    oracle_wallet_password: str = Field(default="")
    oracle_select_ai_profile: str = Field(
        default="",
        description="Oracle Select AI で使う DBMS_CLOUD_AI profile 名。Vault/DB 側で管理する。",
    )
    oracle_select_ai_max_result_chars: int = Field(default=20000, ge=1000, le=200000)
    oracle_vector_target_accuracy: int = Field(
        default=95,
        ge=1,
        le=100,
        description="Oracle AI Vector Search の FETCH APPROX target accuracy。",
    )

    # --- OCI Object Storage ---
    object_storage_region: str = Field(default="ap-osaka-1")
    object_storage_namespace: str = Field(default="")
    object_storage_bucket: str = Field(default="")
    upload_storage_backend: UploadStorageBackend = Field(
        default="local",
        description=(
            "アップロード原本の保存先。local は LOCAL_STORAGE_DIR、" "oci は OCI Object Storage。"
        ),
    )

    # --- ローカル参照実装 ---
    local_storage_dir: str = Field(
        default_factory=lambda: str(Path(gettempdir()) / "production-ready-rag")
    )
    max_upload_bytes: int = Field(default=200 * 1024 * 1024, ge=1)
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
    rag_context_neighbor_window: int = Field(
        default=0,
        ge=0,
        le=5,
        description=("rerank 後の anchor chunk の前後から LLM context へ追加する隣接 chunk 数。"),
    )
    rag_context_diversity_lambda: float = Field(
        default=1.0,
        ge=0.0,
        le=1.0,
        description=("生成 context anchor の MMR 風 diversity 重み。1.0 は rerank 順を維持する。"),
    )
    rag_context_group_expansion_enabled: bool = Field(
        default=False,
        description=(
            "rerank 後の anchor chunk と同じ親 chunk group の sibling を LLM context へ追加する。"
        ),
    )
    rag_context_group_max_chunks: int = Field(
        default=4,
        ge=1,
        le=20,
        description="同一 chunk group から anchor ごとに追加する sibling chunk 数の上限。",
    )
    rag_context_compression_enabled: bool = Field(
        default=False,
        description=(
            "LLM context 投入前に query 関連 sentence/line だけを抽出して chunk text を圧縮する。"
        ),
    )
    rag_context_compression_max_sentences: int = Field(
        default=3,
        ge=1,
        le=10,
        description="context compression で 1 chunk から残す sentence/line 数の上限。",
    )
    rag_context_compression_max_chars_per_chunk: int = Field(
        default=1200,
        ge=200,
        le=8000,
        description="context compression 後の 1 chunk あたり最大文字数。",
    )
    rag_min_similarity: float = Field(default=0.05, ge=0.0, le=1.0)
    rag_rrf_k: int = Field(
        default=60,
        ge=1,
        le=1000,
        description="Hybrid retrieval の Reciprocal Rank Fusion 定数。",
    )
    rag_query_expansion_enabled: bool = Field(
        default=True,
        description="retrieval 前に deterministic な業務同義語 query expansion を行う。",
    )
    rag_query_expansion_max_variants: int = Field(
        default=3,
        ge=1,
        le=8,
        description="query expansion で retrieval に使う query variant 数の上限。",
    )
    rag_search_timeout_seconds: float = Field(default=30.0, gt=0.0, le=300.0)

    # --- レート制限（高コスト API の保護）---
    rate_limit_enabled: bool = Field(default=True)
    rate_limit_window_seconds: float = Field(default=60.0, gt=0.0, le=3600.0)
    rate_limit_search_requests: int = Field(default=60, ge=1, le=10000)
    rate_limit_evaluation_runs: int = Field(default=10, ge=1, le=1000)
    rate_limit_uploads: int = Field(default=30, ge=1, le=1000)
    rate_limit_ingest_requests: int = Field(default=20, ge=1, le=1000)

    # --- ガードレール ---
    guardrail_max_query_chars: int = Field(default=2000, ge=100, le=20000)
    guardrail_block_prompt_injection: bool = Field(default=True)
    guardrail_mask_sensitive_identifiers: bool = Field(default=True)

    # --- 監査 ---
    audit_context_hash_salt: str = Field(
        default="",
        description="tenant/user id を監査ログへ hash 化するときの任意 salt。Vault 注入を推奨。",
    )

    # --- Trace export（OpenTelemetry / Langfuse gateway 連携用）---
    trace_export_http_endpoint: str = Field(default="")
    trace_export_http_bearer_token: str = Field(default="")
    trace_export_timeout_seconds: float = Field(default=2.0, gt=0.0, le=30.0)
    trace_export_queue_size: int = Field(default=1024, ge=1, le=100000)

    @model_validator(mode="after")
    def validate_rag_chunk_settings(self) -> Self:
        """chunk overlap が chunk size 以上になる誤設定を起動時に拒否する。"""
        if self.rag_chunk_overlap >= self.rag_chunk_size:
            raise ValueError("RAG_CHUNK_OVERLAP は RAG_CHUNK_SIZE より小さくしてください。")
        return self

    @property
    def resolved_oracle_wallet_dir(self) -> str:
        """参照実装と同じく ORACLE_CLIENT_LIB_DIR/network/admin を Wallet 配置先にする。"""
        client_lib_dir = self.oracle_client_lib_dir.strip()
        if client_lib_dir:
            return str(Path(client_lib_dir).expanduser() / "network" / "admin")
        return self.oracle_wallet_dir.strip()


@lru_cache
def get_settings() -> Settings:
    """設定のシングルトンを返す。"""
    return Settings()
