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
    environment: str = Field(default="development")
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
            "image/jpeg",
            "image/png",
            "image/tiff",
            "text/plain",
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
    ingestion_queue_worker_concurrency: int = Field(default=2, ge=1, le=16)
    ingestion_job_max_attempts: int = Field(default=3, ge=1, le=20)

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
    rag_embedding_cache_enabled: bool = Field(
        default=True,
        description=(
            "同一 process 内で OCI Generative AI embedding 結果を LRU cache する。"
            "cache key は本文 hash と model/input_type/dimension だけで構成する。"
        ),
    )
    rag_embedding_cache_max_entries: int = Field(default=4096, ge=0, le=200000)
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
