"""サービス設定。共通基底 BaseServiceSettings を継承し、ドメイン設定を足す。"""

from __future__ import annotations

import json
import logging
from functools import lru_cache
from pathlib import Path
from typing import Any

from pr_backend_core.config import BaseServiceSettings
from pydantic import BaseModel, Field, PrivateAttr, field_validator, model_validator

DEFAULT_MODEL_SETTINGS_FILE = "model-settings.json"
logger = logging.getLogger(__name__)


class EnterpriseAiConfiguredModel(BaseModel):
    """UI から登録する Enterprise AI model catalog entry。"""

    model_id: str = ""
    display_name: str = ""
    vision_enabled: bool = False


class _PersistedEnterpriseAiSettings(BaseModel):
    endpoint: str = ""
    project_ocid: str = ""
    # v1 読み込み互換専用。v2 の writer は secret を JSON へ出力しない。
    api_key: str | None = None
    models: list[EnterpriseAiConfiguredModel] = Field(default_factory=list)
    default_model_id: str = ""
    api_path: str = "/responses"
    vlm_input_mode: str = "auto"
    text_payload_template: str = ""
    vision_payload_template: str = ""
    text_response_path: str = ""
    vision_response_path: str = ""
    timeout_seconds: float = 600.0
    max_retries: int = 3
    llm_max_output_tokens: int = 1200
    vlm_max_output_tokens: int = 65536


class _PersistedGenerativeAiSettings(BaseModel):
    embedding_model: str = "cohere.embed-v4.0"
    embedding_dim: int = 1536
    rerank_model: str = "cohere.rerank-v4.0-fast"


class _PersistedModelSettings(BaseModel):
    version: int = 1
    enterprise_ai: _PersistedEnterpriseAiSettings = Field(
        default_factory=_PersistedEnterpriseAiSettings
    )
    generative_ai: _PersistedGenerativeAiSettings = Field(
        default_factory=_PersistedGenerativeAiSettings
    )

    @field_validator("version")
    @classmethod
    def validate_version(cls, value: int) -> int:
        if value not in {1, 2}:
            raise ValueError("model-settings.json の version は 1 または 2 が必要です。")
        return value


class Settings(BaseServiceSettings):
    """サービス固有設定。

    OCI/Oracle 等の接続設定はここに追加する（例: oracle_dsn, oci_region ...）。
    """

    service_name: str = "production-ready-nl2sql"
    # 参考実装互換の DEBUG。認証 bypass は ENVIRONMENT=local のときだけ許可する。
    debug: bool = False
    enable_metrics: bool = True
    oracle_user: str = ""
    oracle_password: str = ""
    oracle_dsn: str = ""
    # Deep Data Security の local END USER は python-oracledb Thin 接続を使う。
    # 既存環境との互換性のため DeepSec 無効時は thick を既定に保つ。
    oracle_driver_mode: str = "thick"
    oracle_client_lib_dir: str = "/u01/aipoc/instantclient_23_26"
    oracle_wallet_dir: str = ""
    oracle_wallet_password: str = ""
    oracle_deepsec_enabled: bool = False
    oracle_deepsec_end_user: str = "NL2SQL_APP_END_USER"
    oracle_deepsec_end_user_password: str = ""
    oracle_adb_ocid: str = ""
    oracle_adb_region: str = ""
    oci_region: str = ""
    oci_compartment_id: str = ""
    oci_config_file: str = "~/.oci/config"
    # OCI_CONFIG_PROFILE が正本。空の場合だけ非推奨の OCI_PROFILE へ fallback する。
    oci_config_profile: str = ""
    oci_profile: str = "DEFAULT"
    oci_auth_mode: str = "config_file"
    oci_user_ocid: str = ""
    oci_fingerprint: str = ""
    oci_tenancy_ocid: str = ""
    oci_key_file: str = ""
    oci_genai_endpoint: str = ""
    oci_genai_embedding_model: str = "cohere.embed-v4.0"
    oci_genai_embedding_dim: int = 1536
    oci_genai_rerank_model: str = "cohere.rerank-v4.0-fast"
    oci_genai_embed_model_id: str = "cohere.embed-v4.0"
    oci_genai_rerank_model_id: str = "cohere.rerank-v4.0-fast"
    oci_enterprise_ai_endpoint: str = ""
    oci_enterprise_ai_project_ocid: str = ""
    oci_enterprise_ai_api_key: str = ""
    oci_enterprise_ai_models: list[EnterpriseAiConfiguredModel] = Field(default_factory=list)
    oci_enterprise_ai_default_model: str = ""
    oci_enterprise_ai_llm_model: str = ""
    oci_enterprise_ai_llm_path: str = "/responses"
    oci_enterprise_ai_llm_payload_template: str = ""
    oci_enterprise_ai_llm_response_path: str = ""
    oci_enterprise_ai_vlm_model: str = ""
    oci_enterprise_ai_vlm_path: str = "/responses"
    oci_enterprise_ai_vlm_payload_template: str = ""
    oci_enterprise_ai_vlm_response_path: str = ""
    oci_enterprise_ai_vlm_input_mode: str = "auto"
    oci_enterprise_ai_timeout_seconds: float = 600.0
    oci_enterprise_ai_max_retries: int = 3
    oci_enterprise_ai_llm_max_output_tokens: int = 1200
    oci_enterprise_ai_vlm_max_output_tokens: int = 65536
    model_settings_file: str = DEFAULT_MODEL_SETTINGS_FILE
    upload_storage_backend: str = "local"
    local_storage_dir: str = "/u01/production-ready-nl2sql"
    object_storage_region: str = ""
    object_storage_namespace: str = ""
    object_storage_bucket: str = "nl2sql-originals"
    max_upload_bytes: int = 200 * 1024 * 1024
    # NL2SQL 安全境界（既定: SELECT のみ許可）。DDL/DML/PLSQL は禁止する方針。
    nl2sql_allow_select_only: bool = True
    nl2sql_default_row_limit: int = 100
    # deterministic: local/CI 用 mock, oracle: python-oracledb 経由で Oracle / Select AI を呼ぶ。
    nl2sql_runtime_mode: str = "deterministic"
    # oracle が既定。memory は local/CI で明示指定する非永続モード。
    nl2sql_persistence_mode: str = "oracle"
    nl2sql_oracle_state_table: str = "NL2SQL_STATE_STORE"
    # incremental: entity 単位 repository（production 既定）、legacy_snapshot: 移行前互換。
    nl2sql_state_backend: str = "incremental"
    nl2sql_migration_mirror_enabled: bool = False
    nl2sql_cache_ttl_seconds: float = 5.0
    nl2sql_profile_cache_max_entries: int = 1000
    nl2sql_schema_object_cache_max_entries: int = 500
    nl2sql_ontology_graph_cache_max_revisions: int = 3
    nl2sql_ontology_graph_cache_max_megabytes: int = 256
    nl2sql_schema_refresh_worker_enabled: bool = True
    nl2sql_schema_refresh_lease_seconds: float = 900.0
    # Ontology worker / reasoning。production では external worker + Oracle RDF network を指定する。
    nl2sql_ontology_worker_mode: str = "inprocess"
    nl2sql_ontology_worker_poll_seconds: float = 1.0
    nl2sql_ontology_worker_claim_timeout_seconds: float = 900.0
    nl2sql_ontology_reasoning_profile: str = "owl2rl"
    nl2sql_ontology_shacl_enabled: bool = True
    nl2sql_ontology_rdf_network_owner: str = ""
    nl2sql_ontology_rdf_network_name: str = ""
    nl2sql_ontology_profile_confirmation_required: bool = True
    nl2sql_ontology_confirmation_ttl_seconds: int = 900
    # ユーザ要望により Oracle Select AI / Select AI Agent を NL2SQL エンジンとして同時サポートする。
    # local / CI では deterministic adapter、実運用では Oracle DB adapter に差し替える。
    nl2sql_select_ai_enabled: bool = True
    nl2sql_select_ai_agent_enabled: bool = True
    nl2sql_enterprise_ai_direct_enabled: bool = True
    nl2sql_select_ai_profile_prefix: str = "NL2SQL"
    nl2sql_select_ai_provider: str = "oci"
    nl2sql_select_ai_credential_name: str = ""
    nl2sql_select_ai_model: str = ""
    nl2sql_schema_owner_allowlist: list[str] = Field(default_factory=list)
    nl2sql_schema_sample_rows: int = 3
    nl2sql_schema_sample_columns_per_table: int = 6
    oracle_db_test_timeout_seconds: float = 15.0
    oracle_tcp_connect_timeout_seconds: float = 10.0
    nl2sql_oracle_connect_timeout_seconds: int = 5
    nl2sql_csv_import_max_rows: int = 5000
    nl2sql_csv_import_max_columns: int = 200
    nl2sql_feedback_embedding_enabled: bool = False
    nl2sql_feedback_vector_table: str = "NL2SQL_FEEDBACK_VECTORS"
    nl2sql_feedback_vector_index: str = "NL2SQL_FEEDBACK_VEC_IDX"

    # アプリケーション認証/RBAC。local/CI は APP_AUTH_ENABLED=false を明示する。
    app_auth_enabled: bool = True
    app_auth_cookie_secure: bool = False
    app_auth_session_cookie_name: str = "nl2sql_session"
    app_auth_csrf_cookie_name: str = "nl2sql_csrf"
    app_auth_idle_timeout_minutes: int = 30
    app_auth_absolute_timeout_hours: int = 12
    app_auth_failed_login_limit: int = 5
    app_auth_lockout_minutes: int = 15
    app_auth_password_min_length: int = 12
    app_auth_password_max_length: int = 128
    app_auth_argon2_time_cost: int = 3
    app_auth_argon2_memory_kib: int = 65536
    app_auth_argon2_parallelism: int = 4

    _environment_enterprise_ai_api_key: str = PrivateAttr(default="")
    _model_secret_source: str = PrivateAttr(default="missing")
    _legacy_model_secret_detected: bool = PrivateAttr(default=False)
    _model_secret_state_initialized: bool = PrivateAttr(default=False)

    @model_validator(mode="after")
    def validate_security_boundaries(self) -> Settings:
        """非 local 環境で debug bypass と非 Secure cookie を fail-closed にする。"""
        if self.environment.strip().lower() == "local":
            return self
        if self.debug:
            raise ValueError("非 local 環境では DEBUG=true を指定できません。")
        if self.app_auth_enabled and not self.app_auth_cookie_secure:
            raise ValueError(
                "非 local 環境で認証を有効にする場合は APP_AUTH_COOKIE_SECURE=true が必要です。"
            )
        return self

    @property
    def local_debug_enabled(self) -> bool:
        """local 開発だけで有効になる fail-closed debug mode。"""
        return self.debug and self.environment.strip().lower() == "local"

    @property
    def resolved_oci_config_profile(self) -> str:
        """正式な OCI_CONFIG_PROFILE と旧 OCI_PROFILE を一か所で解決する。"""
        return self.oci_config_profile.strip() or self.oci_profile.strip() or "DEFAULT"

    @property
    def model_secret_source(self) -> str:
        """Enterprise AI API Key の実効的な取得元。"""
        return self._model_secret_source

    @property
    def legacy_model_secret_detected(self) -> bool:
        """model-settings.json に旧 secret field が残っているか。"""
        return self._legacy_model_secret_detected

    def prepare_model_secret_state(self) -> None:
        """JSON 再読込前に環境由来 secret を基準値へ戻す。"""
        if not self._model_secret_state_initialized:
            self._environment_enterprise_ai_api_key = self.oci_enterprise_ai_api_key.strip()
            self._model_secret_state_initialized = True
        self.oci_enterprise_ai_api_key = self._environment_enterprise_ai_api_key
        self._model_secret_source = (
            "environment" if self._environment_enterprise_ai_api_key else "missing"
        )
        self._legacy_model_secret_detected = False

    def set_runtime_enterprise_ai_api_key(self, api_key: str) -> None:
        """原子的 .env 更新後の secret 状態を現在プロセスへ反映する。"""
        normalized = api_key.strip()
        self._environment_enterprise_ai_api_key = normalized
        self._model_secret_state_initialized = True
        self.oci_enterprise_ai_api_key = normalized
        self._model_secret_source = "environment" if normalized else "missing"
        self._legacy_model_secret_detected = False

    def apply_legacy_enterprise_ai_api_key(self, api_key: str, *, detected: bool) -> None:
        """v1 JSON secret を環境未設定時だけ一時的に runtime へ適用する。"""
        normalized = api_key.strip()
        self._legacy_model_secret_detected = detected
        if not self._environment_enterprise_ai_api_key and normalized:
            self.oci_enterprise_ai_api_key = normalized
            self._model_secret_source = "legacy_json"

    @property
    def resolved_oracle_wallet_dir(self) -> str:
        """driver mode に応じた Wallet 配置先を返す。"""
        if self.oracle_driver_mode.strip().lower() == "thin":
            return self.oracle_wallet_dir.strip()
        client_lib_dir = self.oracle_client_lib_dir.strip()
        if client_lib_dir:
            return str(Path(client_lib_dir).expanduser() / "network" / "admin")
        return self.oracle_wallet_dir.strip()

    @property
    def resolved_oracle_adb_region(self) -> str:
        """ADB 管理専用 region。未設定なら OCI_REGION へ fallback する。"""
        return self.oracle_adb_region.strip() or self.oci_region.strip()

    @field_validator("model_settings_file")
    @classmethod
    def normalize_model_settings_file(cls, value: str) -> str:
        """空指定は backend/.env と同じ階層の既定ファイルへ戻す。"""
        return value.strip() or DEFAULT_MODEL_SETTINGS_FILE

    @field_validator("oracle_driver_mode")
    @classmethod
    def validate_oracle_driver_mode(cls, value: str) -> str:
        normalized = value.strip().lower()
        if normalized not in {"thin", "thick"}:
            raise ValueError("ORACLE_DRIVER_MODE は thin または thick を指定してください。")
        return normalized


_MODEL_SETTINGS_STATE: dict[str, int | str | None] = {"path": None, "mtime_ns": None}


def enterprise_ai_model_catalog(settings: Settings) -> list[EnterpriseAiConfiguredModel]:
    """Enterprise AI の登録モデル一覧を返す。旧 LLM/VLM 設定からも補完する。"""
    configured = [
        model
        for model in (
            _coerce_enterprise_ai_model(item) for item in settings.oci_enterprise_ai_models
        )
        if model.model_id
    ]
    if configured:
        return configured
    return _legacy_enterprise_ai_model_catalog(settings)


def enterprise_ai_default_model_id(settings: Settings) -> str:
    """通常の LLM 呼び出しで使う既定モデル ID を返す。"""
    configured_default = settings.oci_enterprise_ai_default_model.strip()
    if configured_default:
        return configured_default
    legacy_default = settings.oci_enterprise_ai_llm_model.strip()
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
    return settings.oci_enterprise_ai_vlm_model.strip()


def _coerce_enterprise_ai_model(
    value: EnterpriseAiConfiguredModel | dict[str, Any],
) -> EnterpriseAiConfiguredModel:
    """Settings の model_construct や env JSON 由来の値を model object へ寄せる。"""
    if isinstance(value, EnterpriseAiConfiguredModel):
        return value
    return EnterpriseAiConfiguredModel.model_validate(value)


def _legacy_enterprise_ai_model_catalog(settings: Settings) -> list[EnterpriseAiConfiguredModel]:
    """旧 LLM/VLM model ID から新しい model catalog を作る。"""
    llm_model = settings.oci_enterprise_ai_llm_model.strip()
    vlm_model = settings.oci_enterprise_ai_vlm_model.strip()
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
    """設定シングルトン。"""
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
    return (Path(__file__).resolve().parents[1] / path).resolve()


def load_persisted_model_settings(settings: Settings) -> None:
    """UI 保存済みのモデル設定 JSON があれば Settings へ上書き適用する。"""
    settings.prepare_model_secret_state()
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
    if settings.legacy_model_secret_detected:
        logger.warning(
            "legacy_model_secret_detected",
            extra={"warning_code": "MODEL_SETTINGS_LEGACY_SECRET"},
        )
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
    legacy_secret = (enterprise_ai.api_key or "").strip()
    settings.apply_legacy_enterprise_ai_api_key(
        legacy_secret if persisted.version == 1 else "",
        detected=bool(legacy_secret),
    )
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
    settings.oci_genai_embed_model_id = generative_ai.embedding_model
    settings.oci_genai_rerank_model_id = generative_ai.rerank_model


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
