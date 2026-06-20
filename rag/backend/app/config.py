"""アプリケーション設定。

環境変数 / `.env` から読み込む。シークレットはコードにハードコードしない。
"""

import json
from functools import lru_cache
from pathlib import Path
from typing import Literal, Self

from pydantic import BaseModel, Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

AuthMode = Literal["local", "production"]
UploadStorageBackend = Literal["local", "oci"]
AuditPersistence = Literal["log", "oracle", "both"]
ParserAdapterBackend = Literal[
    "local",
    "auto",
    "docling",
    "marker",
    "unstructured",
    "mineru",
    "dots_ocr",
    "glm_ocr",
    # service 系 backend（外部 Python package / parser microservice ではなく OCI クラウド
    # サービスを backend から直接呼ぶ）。enterprise_ai_vlm は OCI Enterprise AI VLM を
    # fallback ではなく明示選択し、oci_document_understanding は OCI Document Understanding
    # の非同期 processor job で OCR/表抽出する。
    "enterprise_ai_vlm",
    "oci_document_understanding",
]
PreprocessProfile = Literal[
    "passthrough",
    "text_normalize",
    "office_to_pdf",
    "pdf_to_page_images",
    "csv_to_json",
    "excel_to_json",
    "url_to_markdown",
    "image_enhance",
    "pii_redact",
]
ChunkingStrategy = Literal[
    "structure_aware",
    "recursive_character",
    "sentence_window",
    "hierarchical_parent_child",
    "markdown_heading",
    "page_level",
    "fixed_size",
]
RetrievalStrategy = Literal[
    "hybrid_rrf",
    "vector",
    "keyword",
    "graph_augmented",
    "select_ai_structured",
    "business_context_strict",
    "corrective_multi_query",
    "reasoning_tree_search",
    "colpali_visual_retrieval",
]
PostRetrievalPipeline = Literal[
    "custom",
    "lean",
    "verified_context",
    "context_enrich",
    "compact",
    "full_governed",
]
GenerationProfile = Literal[
    "grounded_concise",
    "detailed_cited",
    "strict_extractive",
    "structured_json",
    "bilingual_ja_en",
    "inline_cited",
    "custom",
]
GuardrailPolicyName = Literal[
    "standard",
    "strict",
    "lenient",
    "regulated",
]
# Guardrail のバックエンド。local(既定)は in-process 決定論ヒューリスティック。
# oci_guardrails は OCI Generative AI Guardrails(ApplyGuardrails、検出専用 API)を併用し、
# 未設定/失敗時は local へ安全に縮退する。
GuardrailBackend = Literal[
    "local",
    "oci_guardrails",
]
VectorIndexProfile = Literal[
    "balanced",
    "accurate",
    "fast",
]
EvaluationSuite = Literal[
    "request_only",
    "retrieval_focused",
    "balanced",
    "strict_ci",
    "ragas_like",
]
GraphProfile = Literal[
    "off",
    "entities",
    "full",
]
AgenticProfile = Literal[
    "off",
    "smart_routing",
    "query_rewrite",
    "hyde",
    "decompose",
    "multi_hop",
]
EnterpriseAiVlmInputMode = Literal["auto", "files_api", "inline_image"]
# --- NL2SQL パイプラインアダプター(Select AI 中核)---
# ルーティング(profile 自動選択 / 複雑度で単段↔多段)。off=現行(ルーティングなし)。
Nl2SqlRouterProfile = Literal["off", "classifier", "complexity_aware"]
# SQL 安全ポリシー。read_only(既定)は SELECT のみ、strict は object allowlist+row limit+
# semantic_verify、sandboxed は専用低権限ロール実行。
Nl2SqlGuardrailPolicy = Literal["read_only", "strict", "sandboxed"]
# 意味キャッシュ。off(既定)/ nl_sql / nl_result / sql_result。
Nl2SqlCachePolicy = Literal["off", "nl_sql", "nl_result", "sql_result"]
# 生成バックエンド(Generation)。select_ai_agent(既定・RUN_TEAM)/ select_ai(GENERATE)/
# app_enterprise_ai(アプリ側オーケストレーション)。
Nl2SqlGenerationBackend = Literal["select_ai_agent", "select_ai", "app_enterprise_ai"]
BACKEND_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_MODEL_SETTINGS_FILE = "model-settings.json"
DEFAULT_LOCAL_STORAGE_DIR = "/u01/production-ready-rag"


class EnterpriseAiConfiguredModel(BaseModel):
    """OCI Enterprise AI provider に登録する LLM。"""

    model_id: str = Field(default="", max_length=256)
    display_name: str = Field(default="", max_length=256)
    vision_enabled: bool = Field(default=False)

    @field_validator("model_id", "display_name")
    @classmethod
    def strip_text(cls, value: str) -> str:
        """前後空白を設定値へ混入させない。"""
        return value.strip()


class _PersistedEnterpriseAiSettings(BaseModel):
    """UI から保存された Enterprise AI モデル設定。"""

    endpoint: str = Field(default="", max_length=2048)
    project_ocid: str = Field(default="", max_length=512)
    api_key: str = Field(default="", max_length=4096)
    models: list[EnterpriseAiConfiguredModel] = Field(default_factory=list, max_length=20)
    default_model_id: str = Field(default="", max_length=256)
    api_path: str = Field(default="/responses", max_length=512)
    vlm_input_mode: EnterpriseAiVlmInputMode = "auto"
    text_payload_template: str = Field(default="", max_length=20000)
    vision_payload_template: str = Field(default="", max_length=20000)
    text_response_path: str = Field(default="", max_length=1024)
    vision_response_path: str = Field(default="", max_length=1024)
    timeout_seconds: float = Field(default=600.0, gt=0.0, le=600.0)
    max_retries: int = Field(default=3, ge=0, le=5)
    llm_max_output_tokens: int = Field(default=1200, ge=1, le=65536)
    vlm_max_output_tokens: int = Field(default=65536, ge=1, le=65536)

    @field_validator(
        "endpoint",
        "project_ocid",
        "api_key",
        "default_model_id",
        "api_path",
        "text_payload_template",
        "vision_payload_template",
        "text_response_path",
        "vision_response_path",
    )
    @classmethod
    def strip_text(cls, value: str) -> str:
        """前後空白を設定値へ混入させない。"""
        return value.strip()

    @model_validator(mode="after")
    def validate_model_catalog(self) -> "_PersistedEnterpriseAiSettings":
        """保存済み catalog の重複と default 参照を検証する。"""
        model_ids = [model.model_id for model in self.models if model.model_id]
        if len(model_ids) != len(set(model_ids)):
            raise ValueError("Enterprise AI の model ID は重複できません。")
        if self.default_model_id and self.default_model_id not in model_ids:
            raise ValueError("Enterprise AI default model は catalog 内から選択してください。")
        return self


class _PersistedGenerativeAiSettings(BaseModel):
    """UI から保存された OCI Generative AI embedding/rerank 設定。"""

    embedding_model: str = Field(default="cohere.embed-v4.0", max_length=256)
    embedding_dim: int = Field(default=1536, ge=1536, le=1536)
    rerank_model: str = Field(default="cohere.rerank-v4.0-fast", max_length=256)

    @field_validator("embedding_model", "rerank_model")
    @classmethod
    def strip_text(cls, value: str) -> str:
        """前後空白を設定値へ混入させない。"""
        return value.strip()


class _PersistedModelSettings(BaseModel):
    """UI 保存用のモデル設定ファイル schema。"""

    version: Literal[1] = 1
    enterprise_ai: _PersistedEnterpriseAiSettings = Field(
        default_factory=_PersistedEnterpriseAiSettings
    )
    generative_ai: _PersistedGenerativeAiSettings = Field(
        default_factory=_PersistedGenerativeAiSettings
    )


class Settings(BaseSettings):
    """環境変数ベースの設定。"""

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # --- アプリ ---
    app_name: str = "production-ready-rag"
    environment: str = Field(default="dev")
    log_level: str = Field(default="INFO")
    app_version: str = Field(default="0.1.0")
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
    model_settings_file: str = Field(
        default=DEFAULT_MODEL_SETTINGS_FILE,
        description="UI から保存したモデル設定 JSON。存在する場合は .env より優先する。",
    )
    # CORS 許可オリジン（フロントエンド）
    cors_origins: list[str] = Field(default=["http://localhost:3000"])

    # --- OCI 共通 ---
    oci_config_file: str = Field(default="~/.oci/config")
    oci_config_profile: str = Field(default="DEFAULT")
    oci_region: str = Field(default="ap-osaka-1")
    oci_compartment_id: str = Field(default="")

    # --- OCI Enterprise AI（LLM / Vision-capable LLM）---
    # 注意: OCI Generative AI の chat 推論 API ではなく Enterprise AI を使う
    oci_enterprise_ai_endpoint: str = Field(default="")
    oci_enterprise_ai_project_ocid: str = Field(default="")
    oci_enterprise_ai_api_key: str = Field(default="")
    oci_enterprise_ai_models: list[EnterpriseAiConfiguredModel] = Field(default_factory=list)
    oci_enterprise_ai_default_model: str = Field(default="")
    # 互換用: 旧設定名。新 UI/API では models/default_model を正とする。
    oci_enterprise_ai_llm_model: str = Field(default="")
    oci_enterprise_ai_vlm_model: str = Field(default="")
    oci_enterprise_ai_llm_path: str = Field(default="/responses")
    oci_enterprise_ai_vlm_path: str = Field(default="/responses")
    oci_enterprise_ai_vlm_input_mode: EnterpriseAiVlmInputMode = Field(
        default="auto",
        description=(
            "Enterprise AI VLM への入力搬送方式。auto は画像を inline、非画像を Files API。"
            "files_api は VLM 入力を明示的に /files 経由へ送る。inline_image は画像のみ inline。"
        ),
    )
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
    oci_enterprise_ai_timeout_seconds: float = Field(default=600.0, gt=0.0, le=600.0)
    oci_enterprise_ai_max_retries: int = Field(default=3, ge=0, le=5)
    oci_enterprise_ai_llm_max_output_tokens: int = Field(default=1200, ge=1, le=65536)
    oci_enterprise_ai_vlm_max_output_tokens: int = Field(default=65536, ge=1, le=65536)

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
    oracle_adb_ocid: str = Field(
        default="",
        description=(
            "Autonomous Database 操作対象の OCID。起動 / 停止 / 情報取得に使う。"
            "ベクトル検索とは別経路の OCI Database 制御プレーン操作用。"
        ),
    )
    oracle_tcp_connect_timeout_seconds: float = Field(
        default=10.0,
        gt=0.0,
        le=120.0,
        description="Oracle TCP 接続の待機秒数。ADB/Wallet 疎通確認を長時間ブロックしない。",
    )
    oracle_db_test_timeout_seconds: float = Field(
        default=15.0,
        gt=0.0,
        le=180.0,
        description="Oracle 接続テスト API 全体の待機秒数。",
    )
    oracle_select_ai_profile: str = Field(
        default="",
        description="Oracle Select AI で使う DBMS_CLOUD_AI profile 名。DB 側で管理する。",
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

    # --- ローカルアップロード保存先 ---
    local_storage_dir: str = Field(default=DEFAULT_LOCAL_STORAGE_DIR)
    max_upload_bytes: int = Field(default=200 * 1024 * 1024, ge=1)
    allowed_upload_content_types: list[str] = Field(
        default=[
            "application/pdf",
            "image/gif",
            "image/jpeg",
            "image/jpg",
            "image/png",
            "image/tif",
            "image/webp",
            "image/tiff",
            "text/plain",
            "text/markdown",
            "text/csv",
            "text/tab-separated-values",
            "text/html",
            "application/xhtml+xml",
            "application/json",
            "application/jsonl",
            "application/jsonlines",
            "application/ndjson",
            "application/xml",
            "application/csv",
            "application/x-ndjson",
            "message/rfc822",
            "application/eml",
            "application/vnd.ms-outlook",
            "application/x-msg",
            "application/msword",
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            "application/vnd.ms-powerpoint",
            "application/vnd.openxmlformats-officedocument.presentationml.presentation",
            "application/vnd.ms-excel",
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            "audio/aac",
            "audio/flac",
            "audio/mp3",
            "audio/mpeg",
            "audio/mp4",
            "audio/ogg",
            "audio/wave",
            "audio/wav",
            "audio/x-flac",
            "audio/x-m4a",
            "audio/x-wav",
            "application/ogg",
            "application/octet-stream",
        ]
    )

    # --- 取込 queue ---
    ingestion_queue_startup_recovery_enabled: bool = Field(
        default=True,
        description="起動時に永続化済み QUEUED job と stale RUNNING job を自動回復する。",
    )
    ingestion_queue_startup_drain_limit: int = Field(default=50, ge=1, le=500)
    ingestion_queue_stale_running_seconds: float = Field(default=3600.0, gt=0.0, le=86400.0)
    ingestion_queue_recovery_interval_seconds: float = Field(
        default=60.0,
        gt=0.0,
        le=3600.0,
        description=(
            "専用ワーカーが起動後も stale/固着文書の復旧を再実行する最短間隔（秒）。"
            "クラッシュで INGESTING のまま取り残された文書を再起動なしで回復させる。"
        ),
    )
    ingestion_queue_worker_concurrency: int = Field(default=2, ge=1, le=16)
    ingestion_job_max_attempts: int = Field(default=3, ge=1, le=20)
    ingestion_queue_dedicated_worker_enabled: bool = Field(
        default=True,
        description=(
            "True にすると取込はキュー投入のみとし、専用ワーカー（in-process または別プロセス）"
            "がジョブを消費する。HTTP リクエスト内では取込を実行しない。"
        ),
    )
    ingestion_queue_poll_interval_seconds: float = Field(
        default=2.0,
        gt=0.0,
        le=60.0,
        description="専用ワーカーが QUEUED ジョブをポーリングする間隔（秒）。",
    )
    ingestion_queue_inprocess_worker_enabled: bool = Field(
        default=True,
        description=(
            "専用ワーカーモード時に API プロセス内（lifespan）でもワーカーを起動するか。"
            "別プロセスのワーカーへ完全に切り出す場合は False にする。"
        ),
    )
    ingestion_queue_process_isolation_enabled: bool = Field(
        default=True,
        description=(
            "in-process ワーカーが job 本体を subprocess で実行し、Docling/OCR/CUDA 初期化を "
            "API プロセスから隔離する。専用 worker container では False にして直接実行できる。"
        ),
    )

    # --- RAG ---
    rag_chunk_size: int = Field(default=800, ge=200, le=4000)
    rag_chunk_overlap: int = Field(default=120, ge=0, le=1000)
    rag_chunking_strategy: ChunkingStrategy = Field(
        default="structure_aware",
        description=(
            "chunks 段階の分割戦略(Chunking アダプター)。"
            "structure_aware は element/section/table 認識、recursive_character は固定長、"
            "sentence_window は文単位、hierarchical_parent_child は親子、"
            "markdown_heading は章節単位、page_level はページ単位、"
            "fixed_size は章節・文境界を無視した純粋な固定長分割。"
        ),
    )
    rag_chunk_child_size: int = Field(
        default=320,
        ge=80,
        le=4000,
        description=(
            "hierarchical_parent_child 戦略で親 chunk を再分割する子 chunk の目標文字数。"
            "rag_chunk_size より小さくする。"
        ),
    )
    rag_chunk_sentence_window_size: int = Field(
        default=3,
        ge=1,
        le=20,
        description="sentence_window 戦略で 1 chunk にまとめる文の数。",
    )
    rag_chunk_min_chars: int = Field(
        default=0,
        ge=0,
        le=2000,
        description=(
            "この文字数未満の微小 chunk を隣接 chunk へ吸収する下限。0 で無効。"
            "rag_chunk_size より小さくする。"
        ),
    )
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
    rag_context_adaptive_expansion_enabled: bool = Field(
        default=False,
        description=(
            "query overlap と section/chunk group lineage で必要な隣接 context だけを追加する。"
        ),
    )
    rag_context_adaptive_neighbor_window: int = Field(
        default=1,
        ge=0,
        le=5,
        description="adaptive context expansion が確認する anchor 前後 chunk 数。",
    )
    rag_context_adaptive_min_overlap: float = Field(
        default=0.08,
        ge=0.0,
        le=1.0,
        description="adaptive context expansion で query feature overlap による追加を許す下限。",
    )
    rag_context_dependency_promotion_enabled: bool = Field(
        default=False,
        description=(
            "rerank 後に parent/child element lineage で関連 chunk を context 候補へ昇格する。"
        ),
    )
    rag_context_dependency_max_chunks: int = Field(
        default=4,
        ge=1,
        le=20,
        description="dependency-linked context promotion で anchor ごとに追加する chunk 数の上限。",
    )
    rag_navigation_summary_enabled: bool = Field(
        default=False,
        description=(
            "取込時に navigation tree の各章節 node を OCI Enterprise AI LLM で要約し、"
            "progressive disclosure / Navigate retrieval に使う（既定 OFF）。"
        ),
    )
    rag_navigation_summary_max_nodes: int = Field(
        default=24,
        ge=1,
        le=200,
        description="navigation node 要約を生成する node 数の上限（LLM 呼び出し回数の bound）。",
    )
    rag_asset_summary_enabled: bool = Field(
        default=False,
        description=(
            "取込時に図・表・chart を OCI Enterprise AI VLM/LLM で要約し、検索可能な figure "
            "element として source chunk に紐付ける（Knowhere 由来。既定 OFF）。"
        ),
    )
    rag_asset_summary_max_assets: int = Field(
        default=24,
        ge=1,
        le=200,
        description="asset 要約を生成する asset 数の上限（VLM/LLM 呼び出し回数の bound）。",
    )
    rag_field_extraction_enabled: bool = Field(
        default=False,
        description=(
            "取込時に field schema 定義に従い OCI Enterprise AI structured output で named "
            "field/entity を抽出する（PoweRAG/LangExtract 由来。既定 OFF）。"
        ),
    )
    rag_context_compression_enabled: bool = Field(
        default=False,
        description=(
            "LLM context 投入前に query 関連 sentence/line だけを抽出して chunk text を圧縮する。"
        ),
    )
    rag_grounding_crag_confidence_threshold: float = Field(
        default=0.35,
        ge=0.0,
        le=1.0,
        description=(
            "CRAG: grounding preset が corrective(verified_context/full_governed)のとき、rerank の"
            "最高スコアがこの閾値未満なら query を書き換えて 1 回だけ corrective 再検索する。"
            "閾値 0.0 は実質無効(corrective 経路でも再検索しない)。"
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
    rag_embedding_cache_enabled: bool = Field(
        default=True,
        description=(
            "同一 process 内で OCI Generative AI embedding 結果を LRU cache する。"
            "cache key は本文 hash と model/input_type/dimension だけで構成する。"
        ),
    )
    rag_embedding_cache_max_entries: int = Field(default=4096, ge=0, le=200000)
    rag_embedding_batch_size: int = Field(
        default=96,
        ge=1,
        le=1024,
        description=(
            "OCI Generative AI embedding へ 1 回に送る text 数。"
            "大きな文書取込や query expansion で API payload を過大化しない。"
        ),
    )
    rag_rerank_cache_enabled: bool = Field(
        default=True,
        description=(
            "同一 process 内で OCI Generative AI rerank 結果を LRU cache する。"
            "cache key は query/document hash と model/top_n だけで構成する。"
        ),
    )
    rag_rerank_cache_max_entries: int = Field(default=1024, ge=0, le=100000)
    rag_search_timeout_seconds: float = Field(default=30.0, gt=0.0, le=300.0)
    rag_graph_enabled: bool = Field(
        default=False,
        description=(
            "Oracle 内の軽量 KG / community summary を使う GraphRAG-lite 経路。"
            "未整備環境では hybrid へ安全に fallback する。"
        ),
    )
    rag_stream_realtime_enabled: bool = Field(
        default=False,
        description=(
            "Enterprise AI のリアルタイム token stream を使う場合の feature flag。"
            "無効時も既存 SSE event contract は維持する。"
        ),
    )
    rag_agent_memory_search_enabled: bool = Field(
        default=True,
        description=(
            "Oracle 26ai に保存した Agent Memory を履歴 memory として retrieval に加える。"
            "user/thread/agent scope がない request では安全側で無効化する。"
        ),
    )
    rag_agent_memory_writeback_enabled: bool = Field(
        default=True,
        description=(
            "根拠付き回答の要約を Oracle 26ai Agent Memory へ writeback する。"
            "user/thread/agent scope がない request では保存しない。"
        ),
    )
    rag_agent_memory_top_k: int = Field(
        default=3,
        ge=0,
        le=20,
        description="Agent Memory retrieval で取得する履歴 memory 数。",
    )
    rag_agent_memory_max_chars: int = Field(
        default=1200,
        ge=100,
        le=4000,
        description="Agent Memory に保存する回答要約 text の最大文字数。",
    )
    dashboard_query_timeout_seconds: float = Field(
        default=8.0,
        gt=0.0,
        le=60.0,
        description="ダッシュボード初期集計の DB 待機秒数。DB 停止時に Skeleton を長時間残さない。",
    )
    db_read_timeout_seconds: float = Field(
        default=8.0,
        gt=0.0,
        le=60.0,
        description=(
            "閲覧系一覧/集計 API（ドキュメント・取込ジョブ・ナレッジベース）の DB 待機秒数。"
            "DB 停止時に 500 ではなく空データ + warning で縮退応答するための上限。"
        ),
    )
    rag_pdf_segmentation_enabled: bool = Field(
        default=True,
        description="PDF を VLM へ送る前にページ単位の小さな PDF segment へ分割する。",
    )
    rag_pdf_max_pages_per_segment: int = Field(default=3, ge=1, le=50)
    rag_pdf_max_segments: int = Field(default=300, ge=1, le=2000)
    rag_retrieval_strategy: RetrievalStrategy = Field(
        default="hybrid_rrf",
        description=(
            "検索段階の Retrieval アダプター。hybrid_rrf は hybrid + query expansion + RRF、"
            "vector/keyword は単一モード、graph_augmented/select_ai_structured は構造寄り、"
            "business_context_strict は業務適合加重 + gap-stop、"
            "corrective_multi_query は多 query + 不足時の再検索。"
            "per-request の strategy/mode を明示した場合はそちらを優先する。"
        ),
    )
    rag_post_retrieval_pipeline: PostRetrievalPipeline = Field(
        default="custom",
        description=(
            "検索後処理の Grounding アダプター。custom は既存 rag_context_* フラグを尊重し、"
            "lean/verified_context/context_enrich/compact/full_governed は検証・整形段の"
            "プリセットとして任意段(diversity/expansion/dependency/compression)を束ねる。"
        ),
    )
    rag_generation_profile: GenerationProfile = Field(
        default="grounded_concise",
        description=(
            "回答生成の Generation アダプター。grounded_concise(既定)は現行 system prompt、"
            "detailed_cited は出典 ID 明示、strict_extractive は抽出のみ・推測禁止、"
            "structured_json は JSON 構造化出力、bilingual_ja_en は日本語+英語要約。"
        ),
    )
    rag_generation_system_prompt_override: str | None = Field(
        default=None,
        description=(
            "回答生成 system prompt の上書き。業務アシスタント(Business View)の persona を"
            "クエリ時に注入するための runtime 上書きで env からは設定しない(既定 None=上書きなし)。"
            "設定時は Generation アダプターの profile prompt より優先する。"
        ),
    )
    rag_guardrail_policy: GuardrailPolicyName = Field(
        default="standard",
        description=(
            "安全の Guardrail アダプター。standard(既定)は現行フラグ、"
            "strict/regulated は groundedness 厳格化、lenient は warning 抑制。"
        ),
    )
    rag_guardrail_backend: GuardrailBackend = Field(
        default="local",
        description=(
            "Guardrail のバックエンド。local(既定)は in-process 決定論ヒューリスティック"
            "(現行挙動)。oci_guardrails は OCI Generative AI Guardrails(ApplyGuardrails、"
            "content moderation / PII / prompt injection の検出専用 API)を併用し、未設定/失敗時"
            "は local へ安全に縮退する。確定スタックは不変(別 LLM provider・外部 DB は不採用)。"
        ),
    )
    oci_guardrails_compartment_id: str = Field(
        default="",
        description="OCI Guardrails の compartment OCID。空欄時は oci_compartment_id を使う。",
    )
    oci_guardrails_endpoint: str = Field(
        default="",
        description="OCI Generative AI Inference のサービスエンドポイント上書き(空欄は SDK 既定)。",
    )
    oci_guardrails_prompt_injection_threshold: float = Field(
        default=0.5,
        ge=0.0,
        le=1.0,
        description="prompt injection の risk score をブロック扱いにする閾値(0.0–1.0)。",
    )
    rag_evaluation_suite: EvaluationSuite = Field(
        default="request_only",
        description=(
            "評価の Evaluation アダプター。request_only(既定)はプリセット閾値なしで現行どおり"
            "request の thresholds を使う。retrieval_focused/balanced/strict_ci/ragas_like は"
            "CI gate 用の名前付き閾値スイートを既定として補う(request の thresholds が最優先)。"
        ),
    )
    rag_graph_profile: GraphProfile = Field(
        default="off",
        description=(
            "GraphRAG アダプター(知識グラフ構築の深さ)。off(既定)は KG を構築しない、"
            "entities は entities+relationships のみ、full は claims+community summary まで構築。"
            "legacy の RAG_GRAPH_ENABLED=true は full 相当として扱う。"
        ),
    )
    rag_agentic_profile: AgenticProfile = Field(
        default="off",
        description=(
            "Agentic アダプター(LLM 補助のクエリ計画)。off(既定)は LLM 計画なし、"
            "query_rewrite は検索向け書き換え、decompose は sub-question 分解、"
            "multi_hop は分解 + 弱根拠時に 1 回追加分解。off 以外は追加 LLM 呼び出しが発生する。"
        ),
    )
    rag_agentic_max_subqueries: int = Field(
        default=3,
        ge=1,
        le=8,
        description="Agentic アダプターが query variant へ注入する sub-question の上限。",
    )
    rag_vector_index_profile: VectorIndexProfile = Field(
        default="balanced",
        description=(
            "索引/検索精度の Vector Index アダプター。balanced(既定)は"
            "ORACLE_VECTOR_TARGET_ACCURACY をそのまま使い、accurate は高再現(98)、"
            "fast は低レイテンシ(85)へ検索時 target accuracy を上書きする。"
            "推奨 HNSW ビルドパラメータは設定画面に表示し、適用には索引再作成が必要。"
        ),
    )
    # --- NL2SQL パイプラインアダプター(Select AI 中核)---
    nl2sql_generation_backend: Nl2SqlGenerationBackend = Field(
        default="select_ai_agent",
        description=(
            "SQL 生成バックエンド。select_ai_agent(既定)は DBMS_CLOUD_AI_AGENT.RUN_TEAM、"
            "select_ai は SET_PROFILE→DBMS_CLOUD_AI.GENERATE、app_enterprise_ai はアプリ側"
            "オーケストレーション。確定スタックは不変(別 LLM provider は不採用)。"
        ),
    )
    nl2sql_router_profile: Nl2SqlRouterProfile = Field(
        default="off",
        description=(
            "ルーティングの Router アダプター。off(既定)は固定 profile/backend、"
            "classifier は質問埋め込み+決定論分類器で profile を自動選択、complexity_aware は"
            "複雑度で select_ai(単段)↔select_ai_agent(多段)を振り分ける(コスト最適化)。"
        ),
    )
    nl2sql_router_complexity_threshold: int = Field(
        default=2,
        ge=1,
        le=6,
        description="complexity_aware で多段(select_ai_agent)へ切替える複雑度シグナル数の閾値。",
    )
    nl2sql_guardrail_policy: Nl2SqlGuardrailPolicy = Field(
        default="read_only",
        description=(
            "SQL ガードレールの Guardrail アダプター。read_only(既定)は SELECT のみ許可し"
            "DDL/DML/複文をブロック、strict は object allowlist+row limit+EXPLAIN+semantic_verify、"
            "sandboxed は専用低権限ロールで実行。実行前の人手承認ゲートは必須。"
        ),
    )
    nl2sql_guardrail_max_rows: int = Field(
        default=1000,
        ge=1,
        le=1_000_000,
        description="strict/sandboxed で許可する最大取得行数(row limit)。",
    )
    nl2sql_guardrail_run_role: str = Field(
        default="",
        description="sandboxed で実行に用いる専用低権限 DB ロール名(空欄は接続ユーザのまま)。",
    )
    nl2sql_cache_policy: Nl2SqlCachePolicy = Field(
        default="off",
        description=(
            "意味キャッシュの Cache アダプター。off(既定)/ nl_sql(NL→SQL)/ nl_result(NL→結果)"
            "/ sql_result(SQL→結果)。NL 類似は Oracle 26ai ベクトル検索(Cohere 埋め込み)で判定。"
        ),
    )
    nl2sql_cache_similarity_threshold: float = Field(
        default=0.95,
        ge=0.0,
        le=1.0,
        description="NL 類似キャッシュをヒット扱いにする cosine 類似度の下限(0.0–1.0)。",
    )
    nl2sql_cache_ttl_seconds: int = Field(
        default=300,
        ge=0,
        le=86_400,
        description="キャッシュエントリの TTL 秒(0 は無期限)。鮮度要件で調整する。",
    )
    # --- Select AI プロビジョニング(credential/profile/tool/agent/task/team)---
    select_ai_model: str = Field(
        default="",
        description="Select AI profile の model 名(空欄は未設定。region 非対応時は fallback)。",
    )
    select_ai_oci_endpoint_id: str = Field(
        default="",
        description=(
            "Select AI profile の oci_endpoint_id。OCI Enterprise AI / 専用エンドポイントを指す"
            "(モデルの頭脳を Enterprise AI に寄せる)。空欄は profile attributes へ含めない。"
        ),
    )
    select_ai_region: str = Field(
        default="",
        description="Select AI profile の region(空欄は OCI 設定の region を使う)。",
    )
    select_ai_embedding_model: str = Field(
        default="cohere.embed-v4.0",
        description="Select AI RAG / 類似例検索に使う埋め込みモデル(OCI GenAI Cohere)。",
    )
    select_ai_credential_name: str = Field(
        default="",
        description="Select AI profile が使う既存 credential 名(空欄は決定論命名を生成)。",
    )
    select_ai_response_language: str = Field(
        default="日本語",
        description="Select AI Agent task の応答言語(既定 日本語)。",
    )
    select_ai_max_tokens: int = Field(
        default=0,
        ge=0,
        le=128_000,
        description="Select AI profile の max_tokens(0 は profile attributes へ含めない)。",
    )
    select_ai_api_format: str = Field(
        default="",
        description="Select AI profile の oci_apiformat(空欄は含めない。例: COHERE / GENERIC)。",
    )
    # --- 前処理(Preprocess)ステージ(parse の前の原本変換)---
    rag_preprocess_profile: PreprocessProfile = Field(
        default="passthrough",
        description=(
            "parse の前に原本を一度だけ canonical な中間物へ変換する前処理プリセット。"
            "passthrough(既定)は変換せず現行挙動と一致。text_normalize は文字コード/"
            "Unicode/空白を正規化(in-process)。office_to_pdf は Office→PDF、"
            "pdf_to_page_images は PDF→ページ画像、csv_to_json は CSV→構造化 JSON、"
            "excel_to_json は Excel(.xls/.xlsx)→構造化 JSON、url_to_markdown は "
            "URL→クリーン Markdown(trafilatura、外部 SaaS 非使用)、image_enhance は "
            "スキャン画像の OCR 向け補正(OpenCV)、pii_redact は取込時の PII マスク"
            "(Presidio + 日本語 NER、外部 SaaS 非使用)"
            "(いずれも各々独立した前処理マイクロサービス)。"
        ),
    )
    rag_preprocess_enabled: bool = Field(
        default=False,
        description=(
            "前処理マイクロサービスへの HTTP 委譲を有効化する。OFF(既定)は in-process で "
            "扱える profile(passthrough / text_normalize)のみ実行し、サービス必須の変換は "
            "passthrough へ安全に縮退する。"
        ),
    )
    rag_preprocess_office_to_pdf_service_url: str = Field(
        default="http://preprocess-office-to-pdf:8000",
        description="Office→PDF 前処理マイクロサービスの base URL。",
    )
    rag_preprocess_pdf_to_page_images_service_url: str = Field(
        default="http://preprocess-pdf-to-page-images:8000",
        description="PDF→ページ画像PDF 前処理マイクロサービスの base URL。",
    )
    rag_preprocess_csv_to_json_service_url: str = Field(
        default="http://preprocess-csv-to-json:8000",
        description="CSV→構造化 JSON 前処理マイクロサービスの base URL。",
    )
    rag_preprocess_excel_to_json_service_url: str = Field(
        default="http://preprocess-excel-to-json:8000",
        description="Excel(.xls/.xlsx)→構造化 JSON 前処理マイクロサービスの base URL。",
    )
    rag_preprocess_url_to_markdown_service_url: str = Field(
        default="http://preprocess-url-to-markdown:8000",
        description="URL→クリーン Markdown 前処理マイクロサービスの base URL。",
    )
    rag_preprocess_image_enhance_service_url: str = Field(
        default="http://preprocess-image-enhance:8000",
        description="画像補正(OCR 前処理)マイクロサービスの base URL。",
    )
    rag_preprocess_pii_redact_service_url: str = Field(
        default="http://preprocess-pii-redact:8000",
        description="PII マスク(取込時)前処理マイクロサービスの base URL。",
    )
    rag_preprocess_service_timeout_seconds: float = Field(
        default=300.0,
        gt=0,
        description=(
            "前処理マイクロサービス呼び出しの HTTP timeout(秒)。"
            "超過・接続失敗時は warning を付けて passthrough(原本そのまま parse)へ縮退する。"
        ),
    )
    rag_canonical_artifact_prefix: str = Field(
        default="artifacts/canonical",
        max_length=256,
        description="前処理で生成した正規化原本(canonical source)の Object Storage key prefix。",
    )
    rag_parser_adapter_backend: ParserAdapterBackend = Field(
        default="local",
        description=(
            "Docling/Marker/Unstructured 互換 adapter の選択。"
            "local は標準 parser のみ、auto は有効化 flag の adapter を順に試す。"
        ),
    )
    rag_parser_docling_enabled: bool = Field(
        default=False,
        description=(
            "Docling adapter を feature flag で有効化する。未導入時は安全に fallback する。"
        ),
    )
    rag_parser_marker_enabled: bool = Field(
        default=False,
        description="Marker adapter を feature flag で有効化する。未導入時は安全に fallback する。",
    )
    rag_parser_unstructured_enabled: bool = Field(
        default=False,
        description=(
            "Unstructured adapter を feature flag で有効化する。未導入時は安全に fallback する。"
        ),
    )
    rag_parser_mineru_enabled: bool = Field(
        default=False,
        description=(
            "MinerU adapter(PoweRAG 由来)を feature flag で有効化する。未導入時は安全に "
            "fallback する。実 OCR は OCI Enterprise AI VLM へ再マップ。"
        ),
    )
    rag_parser_dots_ocr_enabled: bool = Field(
        default=False,
        description=(
            "Dots.OCR adapter(PoweRAG 由来)を feature flag で有効化する。未導入時は安全に "
            "fallback する。GPU parser マイクロサービスで実 OCR を行う。"
        ),
    )
    rag_parser_glm_ocr_enabled: bool = Field(
        default=False,
        description=(
            "GLM-OCR adapter(HuggingFace zai-org/GLM-OCR)を feature flag で有効化する。"
            "未導入時は安全に fallback する。GPU parser マイクロサービスで実 OCR を行う。"
        ),
    )
    rag_parser_docling_service_url: str = Field(
        default="http://parser-docling:8000",
        description="Docling parser マイクロサービスの base URL。",
    )
    rag_parser_marker_service_url: str = Field(
        default="http://parser-marker:8000",
        description="Marker parser マイクロサービスの base URL。",
    )
    rag_parser_unstructured_service_url: str = Field(
        default="http://parser-unstructured:8000",
        description="Unstructured parser マイクロサービスの base URL。",
    )
    rag_parser_mineru_service_url: str = Field(
        default="http://parser-mineru:8000",
        description="MinerU(GPU)parser マイクロサービスの base URL。",
    )
    rag_parser_dots_ocr_service_url: str = Field(
        default="http://parser-dots-ocr:8000",
        description="Dots.OCR(GPU)parser マイクロサービスの base URL。",
    )
    rag_parser_glm_ocr_service_url: str = Field(
        default="http://parser-glm-ocr:8000",
        description="GLM-OCR(GPU)parser マイクロサービスの base URL。",
    )
    rag_parser_asr_enabled: bool = Field(
        default=True,
        description=(
            "音声/動画の文字起こし(ASR)を有効化する。audio source kind は OCI AI Speech →"
            "ローカル faster-whisper(parser-asr)→ 未対応 の順で解決する。OFF にすると音声は"
            "従来どおり未対応として扱う。"
        ),
    )
    rag_parser_asr_service_url: str = Field(
        default="http://parser-asr:8000",
        description="ASR(GPU faster-whisper)parser マイクロサービスの base URL。",
    )
    rag_parser_service_timeout_seconds: float = Field(
        default=300.0,
        gt=0,
        description=(
            "parser マイクロサービス呼び出しの HTTP timeout(秒)。"
            "超過・接続失敗時は warning を付けて local/Enterprise AI fallback へ縮退する。"
        ),
    )
    # --- pipeline ステージのプラグイン(マイクロサービス)化 ---
    # 各 pipeline ステージ(chunking 等)を独立サービスとして remote 委譲する。未達/timeout/無効時は
    # backend in-process(同一 rag_pipeline_core ロジック)へ安全縮退する。
    rag_pipeline_stage_timeout_seconds: float = Field(
        default=120.0,
        gt=0,
        description=(
            "pipeline ステージサービス呼び出しの HTTP timeout(秒)。"
            "超過・接続失敗時は warning を付けて in-process へ安全縮退する。"
        ),
    )
    rag_chunking_service_enabled: bool = Field(
        default=False,
        description=(
            "chunking ステージを chunking マイクロサービスへ HTTP 委譲する。OFF(既定)は "
            "backend in-process で実行(現行挙動)。未達/失敗時はいずれも in-process へ縮退する。"
        ),
    )
    rag_chunking_service_url: str = Field(
        default="http://pipeline-chunking:8000",
        description="chunking ステージマイクロサービスの base URL。",
    )
    rag_vector_index_service_enabled: bool = Field(
        default=False,
        description=(
            "vector_index プロファイル解決を vector_index マイクロサービスへ委譲する。OFF(既定)は "
            "in-process(現行挙動)。未達/失敗時はいずれも in-process へ縮退する。"
        ),
    )
    rag_vector_index_service_url: str = Field(
        default="http://pipeline-vector-index:8000",
        description="vector_index ステージマイクロサービスの base URL。",
    )
    rag_graph_service_enabled: bool = Field(
        default=False,
        description=(
            "graphrag プロファイル解決を graphrag マイクロサービスへ委譲する。OFF(既定)は "
            "in-process(現行挙動)。未達/失敗時はいずれも in-process へ縮退する。"
        ),
    )
    rag_graph_service_url: str = Field(
        default="http://pipeline-graphrag:8000",
        description="graphrag ステージマイクロサービスの base URL。",
    )
    rag_generation_service_enabled: bool = Field(
        default=False,
        description=(
            "generation の system prompt 解決を generation マイクロサービスへ委譲する。OFF(既定)は "
            "in-process(現行挙動)。未達/失敗時はいずれも in-process へ縮退する。custom/persona "
            "override は backend 側で上乗せする。"
        ),
    )
    rag_generation_service_url: str = Field(
        default="http://pipeline-generation:8000",
        description="generation ステージマイクロサービスの base URL。",
    )
    rag_guardrail_service_enabled: bool = Field(
        default=False,
        description=(
            "guardrail の policy 解決(groundedness 閾値 + 監査強調)を guardrail マイクロサービスへ"
            "委譲する。OFF(既定)は in-process(現行挙動)。未達/失敗時はいずれも in-process へ縮退。"
            "OCI Generative AI Guardrails backend(rag_guardrail_backend)とは別レイヤーで共存。"
        ),
    )
    rag_guardrail_service_url: str = Field(
        default="http://pipeline-guardrail:8000",
        description="guardrail ステージマイクロサービスの base URL。",
    )
    rag_agentic_service_enabled: bool = Field(
        default=False,
        description=(
            "agentic の profile 解決(クエリ計画の挙動フラグ)を agentic マイクロサービスへ委譲する。"
            "OFF(既定)は in-process(現行挙動)。未達/失敗時はいずれも in-process へ縮退する。"
            "実 LLM クエリ計画は backend が OCI Enterprise AI で行う。"
        ),
    )
    rag_agentic_service_url: str = Field(
        default="http://pipeline-agentic:8000",
        description="agentic ステージマイクロサービスの base URL。",
    )
    rag_grounding_service_enabled: bool = Field(
        default=False,
        description=(
            "grounding の preset 解決(検索後処理段フラグ)を grounding マイクロサービスへ委譲する。"
            "OFF(既定)は in-process(現行挙動)。未達/失敗時はいずれも in-process へ縮退する。"
            "custom preset は backend の legacy rag_context_* 設定をそのまま使う。"
        ),
    )
    rag_grounding_service_url: str = Field(
        default="http://pipeline-grounding:8000",
        description="grounding ステージマイクロサービスの base URL。",
    )
    rag_evaluation_service_enabled: bool = Field(
        default=False,
        description=(
            "evaluation の suite→閾値解決を evaluation マイクロサービスへ委譲する。OFF(既定)は "
            "in-process(現行挙動)。未達/失敗時はいずれも in-process へ縮退する。"
        ),
    )
    rag_evaluation_service_url: str = Field(
        default="http://pipeline-evaluation:8000",
        description="evaluation ステージマイクロサービスの base URL。",
    )
    rag_retrieval_service_enabled: bool = Field(
        default=False,
        description=(
            "retrieval の strategy 解決(検索挙動フラグ)を retrieval マイクロサービスへ委譲する。"
            "OFF(既定)は in-process(現行挙動)。未達/失敗時はいずれも in-process へ縮退する。"
            "実 retrieval(Oracle 26ai 経路)は backend が実行する。"
        ),
    )
    rag_retrieval_service_url: str = Field(
        default="http://pipeline-retrieval:8000",
        description="retrieval ステージマイクロサービスの base URL。",
    )
    rag_graph_temporal_enabled: bool = Field(
        default=False,
        description=(
            "Temporal GraphRAG: full プロファイル時に KG の entity/relationship へ timestamp を"
            "付与し、検索時に時間文脈フィルタを可能にする。off/entities では無効。"
        ),
    )
    rag_raptor_enabled: bool = Field(
        default=False,
        description=(
            "RAPTOR 再帰要約索引: chunking 後に leaf chunk を再帰 cluster + OCI Enterprise AI で"
            "要約し、多層级 summary node を leaf と一緒に索引する。OFF(既定)は leaf のみ。"
            "追加 LLM 呼び出しを伴う opt-in。要約失敗時は leaf のみへ安全縮退する。"
        ),
    )
    rag_raptor_cluster_size: int = Field(
        default=5,
        ge=2,
        le=50,
        description="RAPTOR の 1 cluster あたり chunk 数(要約単位)。",
    )
    rag_raptor_max_levels: int = Field(
        default=2,
        ge=1,
        le=5,
        description="RAPTOR 要約 tree の最大階層数。",
    )
    rag_parser_readiness_probe_enabled: bool = Field(
        default=False,
        description=(
            "readiness 画面の adapter version/可用性を parser サービスの /health 問い合わせで "
            "解決する。OFF(既定)は backend プロセス内の import 検出にフォールバック(開発/テスト "
            "用)。compose / 本番では true にしてサービスの導入状況を表示する。"
        ),
    )
    rag_parser_readiness_probe_timeout_seconds: float = Field(
        default=2.0,
        gt=0,
        description="readiness の /health 問い合わせ timeout(秒)。",
    )
    # --- サービス管理（前処理 / Parser マイクロサービスの稼働可視化・起動/停止）---
    rag_service_control_enabled: bool = Field(
        default=False,
        description=(
            "サービス管理画面からの起動/停止(docker compose 制御)を有効化する。"
            "OFF(既定)は稼働状態の可視化のみで、制御 API は 409(control_disabled)で拒否する。"
            "ON にする場合は backend が docker CLI を実行できる必要がある(ホスト直起動、または "
            "docker.sock + docker CLI のマウント)。"
        ),
    )
    rag_service_control_command: str = Field(
        default="docker compose",
        description=(
            "サービス起動/停止に使う compose コマンドのベース(空白区切り)。"
            "service 名はカタログの allowlist 経由でのみ付与し、任意コマンドは受け付けない。"
        ),
    )
    rag_service_control_timeout_seconds: float = Field(
        default=60.0,
        gt=0,
        description="サービス起動/停止 subprocess の timeout(秒)。超過は失敗として構造化返却する。",
    )
    rag_service_status_probe_timeout_seconds: float = Field(
        default=2.0,
        gt=0,
        description=(
            "サービス管理画面が各マイクロサービスの /health を問い合わせる timeout(秒)。"
            "接続拒否/timeout は stopped、到達したが status!=ok は degraded として表示する。"
        ),
    )
    # --- OCI Document Understanding（service 系 parser backend）---
    # 別 OCI サービス(oci.ai_document)。非同期 processor job で日本語 OCR/表抽出する。
    # 入出力は Object Storage 経由。未設定/失敗時は安全に既存フローへ縮退する。
    oci_document_understanding_compartment_id: str = Field(
        default="",
        description=(
            "OCI Document Understanding の compartment OCID。空のときは "
            "oci_compartment_id を使う。"
        ),
    )
    oci_document_understanding_namespace: str = Field(
        default="",
        description=(
            "DU 入出力 Object Storage の namespace。空のときは object_storage_namespace を使う。"
        ),
    )
    oci_document_understanding_input_bucket: str = Field(
        default="",
        description=(
            "DU 入力ファイルを置く Object Storage bucket。空のときは object_storage_bucket を使う。"
        ),
    )
    oci_document_understanding_output_bucket: str = Field(
        default="",
        description="DU 結果 JSON の出力先 bucket。空のときは入力 bucket を使う。",
    )
    oci_document_understanding_input_prefix: str = Field(
        default="document-understanding/input",
        max_length=256,
        description="DU 入力ファイルの Object Storage key prefix。",
    )
    oci_document_understanding_output_prefix: str = Field(
        default="document-understanding/output",
        max_length=256,
        description="DU 結果 JSON の Object Storage key prefix。",
    )
    oci_document_understanding_language: str = Field(
        default="JPN",
        max_length=8,
        description="DU の言語ヒント(ISO 639-2、日本語は JPN)。",
    )
    oci_document_understanding_features: list[str] = Field(
        default_factory=lambda: ["DOCUMENT_TEXT_EXTRACTION", "TABLE_EXTRACTION"],
        description="DU で要求する analysis feature 種別。",
    )
    oci_document_understanding_poll_interval_seconds: float = Field(
        default=5.0,
        gt=0,
        le=60.0,
        description="DU processor job の状態 poll 間隔(秒)。",
    )
    oci_document_understanding_timeout_seconds: float = Field(
        default=600.0,
        gt=0,
        le=3600.0,
        description="DU processor job 完了待ちの上限(秒)。超過時は安全に縮退する。",
    )
    # --- OCI AI Speech(音声文字起こし。空欄はローカル faster-whisper へ縮退)---
    oci_speech_compartment_id: str = Field(
        default="",
        description="OCI AI Speech の compartment OCID。空欄時は oci_compartment_id を使う。",
    )
    oci_speech_namespace: str = Field(
        default="",
        description="Speech 入出力 Object Storage の namespace。空欄は object_storage_namespace。",
    )
    oci_speech_input_bucket: str = Field(
        default="",
        description="Speech 入力 bucket。空欄は object_storage_bucket。",
    )
    oci_speech_output_bucket: str = Field(
        default="",
        description="Speech 出力 bucket。空欄は入力 bucket と同じ。",
    )
    oci_speech_input_prefix: str = Field(
        default="speech/input",
        description="Speech 入力 object の key prefix。",
    )
    oci_speech_output_prefix: str = Field(
        default="speech/output",
        description="Speech 出力 JSON の key prefix。",
    )
    oci_speech_language: str = Field(
        default="ja",
        description="文字起こしの言語コード(既定 日本語)。",
    )
    oci_speech_poll_interval_seconds: float = Field(
        default=5.0,
        gt=0,
        description="Speech transcription job の状態 poll 間隔(秒)。",
    )
    oci_speech_timeout_seconds: float = Field(
        default=900.0,
        gt=0,
        le=7200.0,
        description="Speech job 完了待ちの上限(秒)。超過時はローカル faster-whisper へ縮退。",
    )
    rag_segment_checkpoint_enabled: bool = Field(
        default=True,
        description="取込 segment checkpoint を Oracle に永続化し、失敗 segment の再試行に使う。",
    )
    rag_extraction_artifact_cache_enabled: bool = Field(
        default=True,
        description="構造化抽出 JSON artifact を chunk/embedding 前に Object Storage へ保存する。",
    )
    rag_extraction_artifact_prefix: str = Field(
        default="artifacts/extractions",
        max_length=256,
        description="構造化抽出 artifact の Object Storage key prefix。",
    )
    rag_review_gate_enabled: bool = Field(
        default=False,
        description=(
            "True のときファイル処理を 2 段階(parse → 人がプレビュー確認 → index)にする。"
            "前段 EXTRACT job は抽出後 REVIEW で停止し、承認 API 経由の INDEX job で索引する。"
            "False(既定)は従来どおり 1 ジョブで INDEXED まで一気通貫する。"
        ),
    )

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
        description="tenant/user id を監査ログへ hash 化するときの任意 salt。.env から注入する。",
    )
    audit_persistence: AuditPersistence = Field(
        default="log",
        description="RAG 監査イベントの保存先。log / oracle / both。",
    )

    # --- Trace export（OpenTelemetry / Langfuse gateway 連携用）---
    trace_export_http_endpoint: str = Field(default="")
    trace_export_http_bearer_token: str = Field(default="")
    trace_export_timeout_seconds: float = Field(default=2.0, gt=0.0, le=30.0)
    trace_export_queue_size: int = Field(default=1024, ge=1, le=100000)

    @field_validator("model_settings_file")
    @classmethod
    def normalize_model_settings_file(cls, value: str) -> str:
        """空指定は backend/.env と同じ階層の既定ファイルへ戻す。"""
        return value.strip() or DEFAULT_MODEL_SETTINGS_FILE

    @model_validator(mode="after")
    def validate_rag_chunk_settings(self) -> Self:
        """chunk size と各 chunking 戦略パラメータの整合性を起動時に検証する。"""
        if self.rag_chunk_overlap >= self.rag_chunk_size:
            raise ValueError("RAG_CHUNK_OVERLAP は RAG_CHUNK_SIZE より小さくしてください。")
        if self.rag_chunk_child_size >= self.rag_chunk_size:
            raise ValueError("RAG_CHUNK_CHILD_SIZE は RAG_CHUNK_SIZE より小さくしてください。")
        if self.rag_chunk_min_chars >= self.rag_chunk_size:
            raise ValueError("RAG_CHUNK_MIN_CHARS は RAG_CHUNK_SIZE より小さくしてください。")
        return self

    @property
    def resolved_oracle_wallet_dir(self) -> str:
        """参照実装と同じく ORACLE_CLIENT_LIB_DIR/network/admin を Wallet 配置先にする。"""
        client_lib_dir = self.oracle_client_lib_dir.strip()
        if client_lib_dir:
            return str(Path(client_lib_dir).expanduser() / "network" / "admin")
        return self.oracle_wallet_dir.strip()


_MODEL_SETTINGS_STATE: dict[str, int | str | None] = {"path": None, "mtime_ns": None}


def enterprise_ai_model_catalog(settings: Settings) -> list[EnterpriseAiConfiguredModel]:
    """Enterprise AI の登録モデル一覧を返す。旧 LLM/VLM 設定からも補完する。"""
    configured = [
        model
        for model in (
            _coerce_enterprise_ai_model(item)
            for item in getattr(settings, "oci_enterprise_ai_models", [])
        )
        if model.model_id
    ]
    if configured:
        return configured
    return _legacy_enterprise_ai_model_catalog(settings)


def enterprise_ai_default_model_id(settings: Settings) -> str:
    """通常の LLM 呼び出しで使う既定モデル ID を返す。"""
    configured_default = getattr(settings, "oci_enterprise_ai_default_model", "").strip()
    if configured_default:
        return configured_default
    legacy_default = getattr(settings, "oci_enterprise_ai_llm_model", "").strip()
    if legacy_default:
        return legacy_default
    catalog = enterprise_ai_model_catalog(settings)
    return catalog[0].model_id if catalog else ""


def enterprise_ai_vision_model_id(settings: Settings) -> str:
    """Vision/OCR 呼び出しで使うモデル ID を返す。"""
    catalog = enterprise_ai_model_catalog(settings)
    default_model = enterprise_ai_default_model_id(settings)
    for model in catalog:
        if model.model_id == default_model and model.vision_enabled:
            return model.model_id
    for model in catalog:
        if model.vision_enabled:
            return model.model_id
    legacy_vision = getattr(settings, "oci_enterprise_ai_vlm_model", "").strip()
    if legacy_vision:
        return legacy_vision
    return ""


def _coerce_enterprise_ai_model(
    value: EnterpriseAiConfiguredModel | dict[str, object],
) -> EnterpriseAiConfiguredModel:
    """Settings の model_construct や env JSON 由来の値を model object へ寄せる。"""
    if isinstance(value, EnterpriseAiConfiguredModel):
        return value
    return EnterpriseAiConfiguredModel.model_validate(value)


def _legacy_enterprise_ai_model_catalog(settings: Settings) -> list[EnterpriseAiConfiguredModel]:
    """旧 LLM/VLM model ID から新しい model catalog を作る。"""
    llm_model = getattr(settings, "oci_enterprise_ai_llm_model", "").strip()
    vlm_model = getattr(settings, "oci_enterprise_ai_vlm_model", "").strip()
    models: list[EnterpriseAiConfiguredModel] = []
    if llm_model:
        models.append(
            EnterpriseAiConfiguredModel(
                model_id=llm_model,
                display_name=llm_model,
                vision_enabled=bool(vlm_model and vlm_model == llm_model),
            )
        )
    if vlm_model and vlm_model != llm_model:
        models.append(
            EnterpriseAiConfiguredModel(
                model_id=vlm_model,
                display_name=vlm_model,
                vision_enabled=True,
            )
        )
    return models


@lru_cache
def _settings_singleton() -> Settings:
    """環境変数/.env と永続化ファイルから初期 Settings を作る。"""
    settings = Settings()
    load_persisted_model_settings(settings)
    return settings


def get_settings() -> Settings:
    """設定のシングルトンを返す。永続化ファイルの更新があれば再読込する。"""
    settings = _settings_singleton()
    reload_persisted_model_settings_if_changed(settings)
    return settings


def reset_settings_cache() -> None:
    """テストや明示的な再初期化のため Settings singleton を破棄する。"""
    _settings_singleton.cache_clear()
    _MODEL_SETTINGS_STATE["path"] = None
    _MODEL_SETTINGS_STATE["mtime_ns"] = None


def resolve_model_settings_file(path_value: str) -> Path:
    """MODEL_SETTINGS_FILE を backend/.env と同じディレクトリ基準で解決する。"""
    raw_path = path_value.strip() or DEFAULT_MODEL_SETTINGS_FILE
    path = Path(raw_path).expanduser()
    if path.is_absolute():
        return path
    return (BACKEND_ROOT / path).resolve()


def load_persisted_model_settings(settings: Settings) -> None:
    """UI 保存済みのモデル設定 JSON があれば Settings へ上書き適用する。"""
    path = resolve_model_settings_file(settings.model_settings_file)
    if not path.is_file():
        _remember_model_settings_file(path, None)
        return

    try:
        stat_result = path.stat()
        data = json.loads(path.read_text(encoding="utf-8"))
        persisted = _PersistedModelSettings.model_validate(data)
    except (OSError, ValueError) as exc:
        raise ValueError(f"モデル設定ファイルを読み込めません: {path}") from exc

    _apply_persisted_model_settings(settings, persisted)
    _remember_model_settings_file(path, stat_result.st_mtime_ns)


def reload_persisted_model_settings_if_changed(settings: Settings) -> None:
    """別 worker が保存したモデル設定を次回リクエストで取り込む。"""
    path = resolve_model_settings_file(settings.model_settings_file)
    mtime_ns = _model_settings_mtime_ns(path)
    if _MODEL_SETTINGS_STATE["path"] == str(path) and _MODEL_SETTINGS_STATE["mtime_ns"] == mtime_ns:
        return
    if mtime_ns is None:
        _remember_model_settings_file(path, None)
        return
    load_persisted_model_settings(settings)


def _apply_persisted_model_settings(
    settings: Settings,
    persisted: _PersistedModelSettings,
) -> None:
    """永続化 schema を既存 Settings フィールドへ再マッピングする。"""
    enterprise_ai = persisted.enterprise_ai
    generative_ai = persisted.generative_ai
    models = [model for model in enterprise_ai.models if model.model_id]
    default_model = enterprise_ai.default_model_id or (models[0].model_id if models else "")

    settings.oci_enterprise_ai_endpoint = enterprise_ai.endpoint
    settings.oci_enterprise_ai_project_ocid = enterprise_ai.project_ocid
    settings.oci_enterprise_ai_api_key = enterprise_ai.api_key
    settings.oci_enterprise_ai_models = models
    settings.oci_enterprise_ai_default_model = default_model
    settings.oci_enterprise_ai_llm_model = default_model
    settings.oci_enterprise_ai_vlm_model = _persisted_vision_model_id(models, default_model)
    settings.oci_enterprise_ai_llm_path = enterprise_ai.api_path
    settings.oci_enterprise_ai_vlm_path = enterprise_ai.api_path
    settings.oci_enterprise_ai_vlm_input_mode = enterprise_ai.vlm_input_mode
    settings.oci_enterprise_ai_llm_payload_template = enterprise_ai.text_payload_template
    settings.oci_enterprise_ai_vlm_payload_template = enterprise_ai.vision_payload_template
    settings.oci_enterprise_ai_llm_response_path = enterprise_ai.text_response_path
    settings.oci_enterprise_ai_vlm_response_path = enterprise_ai.vision_response_path
    settings.oci_enterprise_ai_timeout_seconds = enterprise_ai.timeout_seconds
    settings.oci_enterprise_ai_max_retries = enterprise_ai.max_retries
    settings.oci_enterprise_ai_llm_max_output_tokens = enterprise_ai.llm_max_output_tokens
    settings.oci_enterprise_ai_vlm_max_output_tokens = enterprise_ai.vlm_max_output_tokens

    settings.oci_genai_embedding_model = generative_ai.embedding_model
    settings.oci_genai_embedding_dim = generative_ai.embedding_dim
    settings.oci_genai_rerank_model = generative_ai.rerank_model


def _persisted_vision_model_id(
    models: list[EnterpriseAiConfiguredModel],
    default_model: str,
) -> str:
    """Vision/OCR 用 model を default 優先で選ぶ。"""
    for model in models:
        if model.model_id == default_model and model.vision_enabled:
            return model.model_id
    for model in models:
        if model.vision_enabled:
            return model.model_id
    return ""


def _model_settings_mtime_ns(path: Path) -> int | None:
    """モデル設定ファイルの mtime を nanosecond で返す。存在しなければ None。"""
    try:
        return path.stat().st_mtime_ns if path.is_file() else None
    except OSError:
        return None


def _remember_model_settings_file(path: Path, mtime_ns: int | None) -> None:
    """現在プロセスが最後に取り込んだ設定ファイル情報を記録する。"""
    _MODEL_SETTINGS_STATE["path"] = str(path)
    _MODEL_SETTINGS_STATE["mtime_ns"] = mtime_ns
