"""サービス設定。共通基底 BaseServiceSettings を継承し、ドメイン設定を足す。"""

from __future__ import annotations

import logging
from functools import lru_cache
from pathlib import Path
from typing import Literal

from pr_backend_core.config import BaseServiceSettings
from pr_system_settings.model import EnterpriseAiConfiguredModel as EnterpriseAiConfiguredModel
from pr_system_settings.model import ModelSecretStateMixin, ModelSettingsStore
from pr_system_settings.model import (
    enterprise_ai_default_model_id as enterprise_ai_default_model_id,
)
from pr_system_settings.model import enterprise_ai_model_catalog as enterprise_ai_model_catalog
from pr_system_settings.model import enterprise_ai_vision_model_id as enterprise_ai_vision_model_id
from pydantic import Field, field_validator, model_validator

BACKEND_DIR = Path(__file__).resolve().parents[1]
BACKEND_ENV_FILE = BACKEND_DIR / ".env"
DEFAULT_MODEL_SETTINGS_FILE = "model-settings.json"
logger = logging.getLogger(__name__)


class Settings(ModelSecretStateMixin, BaseServiceSettings):
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
    # Deep Data Security は python-oracledb Thin mode のみ対応する。
    # Thick は DeepSec 無効時の互換用途に限る。
    oracle_driver_mode: str = "thin"
    oracle_connection_security: str = "wallet_mtls"
    oracle_client_lib_dir: str = ""
    oracle_wallet_dir: str = "/u01/aipoc/wallet"
    oracle_wallet_password: str = ""
    oracle_deepsec_enabled: bool = False
    oracle_deepsec_data_user: str = "DEEPSEC_DATA_USER"
    oracle_deepsec_data_user_password: str = ""
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
    local_storage_dir: str = "/u01/data/production-ready-nl2sql"
    object_storage_region: str = ""
    object_storage_namespace: str = ""
    object_storage_bucket: str = "nl2sql-originals"
    max_upload_bytes: int = 200 * 1024 * 1024
    # NL2SQL 安全境界（既定: SELECT のみ許可）。DDL/DML/PLSQL は禁止する方針。
    nl2sql_allow_select_only: bool = True
    nl2sql_default_row_limit: int = 100
    # 同時に実行する NL2SQL job worker 数の上限(超過分は pending のまま待機)。
    # in-process queue worker 数をこの値で抑え、Oracle セッションの枯渇を防ぐ。
    nl2sql_job_max_concurrency: int = Field(default=4, ge=1, le=64)
    # inprocess: local/CI 用 bounded worker、external: API は永続 queue への投入だけを行う。
    nl2sql_synthetic_worker_mode: Literal["inprocess", "external"] = "inprocess"
    nl2sql_job_worker_mode: Literal["inprocess", "external"] = "inprocess"
    nl2sql_job_worker_poll_seconds: float = 1.0
    nl2sql_job_lease_seconds: float = 900.0
    # legacy snapshot の running job が lease を持たずこの秒数更新されない場合のみ、
    # 再起動前の中断として扱う。新しい queue job は lease 期限後に worker が再 claim する。
    nl2sql_job_stale_after_seconds: float = Field(default=1800.0, ge=60.0)
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
    # inprocess: local 開発用 thread、external: API は job 永続化だけを行う。
    nl2sql_schema_refresh_worker_mode: str = "inprocess"
    nl2sql_profile_list_refresh_timeout_seconds: float = Field(default=600.0, ge=30)
    nl2sql_schema_refresh_lease_seconds: float = 900.0
    # 明示 system schema DDL が既存 DML lock の解放を待つ上限。
    nl2sql_system_schema_ddl_lock_timeout_seconds: int = Field(
        default=30,
        ge=0,
        le=120,
    )
    # SQL生成評価は local で inprocess、Oracle 運用で external worker を使う。
    nl2sql_quality_evaluation_worker_mode: str = "inprocess"
    nl2sql_quality_evaluation_worker_poll_seconds: float = 1.0
    nl2sql_quality_evaluation_lease_seconds: float = 900.0
    nl2sql_quality_evaluation_attempt_timeout_seconds: float = 300.0
    nl2sql_quality_evaluation_max_file_bytes: int = 10 * 1024 * 1024
    nl2sql_quality_evaluation_max_cases: int = 100
    nl2sql_quality_evaluation_max_attempts: int = 1000
    # Ontology worker / reasoning。typo(例: "in-process")だと job が誰にも処理されず
    # queued のまま沈黙するため、起動時に Literal で検証する。
    nl2sql_ontology_worker_mode: Literal["inprocess", "external"] = "inprocess"
    # AI 構築の抽出/命名呼び出し専用の出力トークン上限。汎用の
    # oci_enterprise_ai_llm_max_output_tokens(既定 1200)では SCHEMA_NAMING の
    # JSON が途中で切れて全体失敗するため、抽出系は大きめの予算を使う。
    nl2sql_ontology_extraction_max_output_tokens: int = 8000
    # GraphRAG 流 gleaning(取りこぼし回収の追加パス)。0 で無効。
    nl2sql_ontology_extraction_gleaning_passes: int = 1
    # 1 回の抽出呼び出しに載せる資料本文の上限(チャンクが大きいほど抽出漏れが増えるため)。
    nl2sql_ontology_extraction_batch_max_chars: int = 12000
    # 公開前の Markdown 解析は自動再送せず、待機時間を含む期限を永続化する。
    nl2sql_ontology_preparation_timeout_seconds: float = Field(default=600.0, gt=0)
    nl2sql_ontology_worker_poll_seconds: float = 1.0
    nl2sql_ontology_validation_timeout_seconds: float = Field(default=600.0, gt=0)
    nl2sql_ontology_publish_timeout_seconds: float = Field(default=600.0, gt=0)
    nl2sql_ontology_worker_claim_timeout_seconds: float = 3900.0
    nl2sql_ontology_build_timeout_seconds: float = Field(default=21600.0, gt=0)
    nl2sql_ontology_build_lease_seconds: float = Field(default=120.0, ge=30)
    # Oracle Profile 同期は永続 job で実行し、DB round-trip と job 全体を別々に制限する。
    nl2sql_oracle_call_timeout_seconds: float = 120.0
    nl2sql_profile_sync_job_timeout_seconds: float = 300.0
    nl2sql_ontology_reasoning_profile: str = "owl2rl"
    nl2sql_ontology_shacl_enabled: bool = True
    nl2sql_ontology_profile_confirmation_required: bool = True
    nl2sql_ontology_confirmation_ttl_seconds: int = 900
    # ユーザ要望により Oracle Select AI / Select AI Agent を NL2SQL エンジンとして同時サポートする。
    # local / CI では deterministic adapter、実運用では Oracle DB adapter に差し替える。
    nl2sql_select_ai_enabled: bool = True
    nl2sql_select_ai_agent_enabled: bool = True
    nl2sql_enterprise_ai_direct_enabled: bool = True
    nl2sql_select_ai_profile_prefix: str = "NL2SQL"
    nl2sql_select_ai_provider: str = "oci"
    nl2sql_select_ai_credential_name: str = "OCI_CRED"
    nl2sql_select_ai_region: str = "us-chicago-1"
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
    app_admin_login_user_id: str = ""
    app_admin_login_user_password: str = ""
    app_auth_cookie_secure: bool = False
    app_auth_session_cookie_name: str = "nl2sql_session"
    app_auth_csrf_cookie_name: str = "nl2sql_csrf"
    app_auth_idle_timeout_minutes: int = 60
    app_auth_absolute_timeout_hours: int = 12
    app_auth_failed_login_limit: int = 5
    app_auth_lockout_minutes: int = 15
    app_auth_password_min_length: int = 12
    app_auth_password_max_length: int = 128
    app_auth_argon2_time_cost: int = 3
    app_auth_argon2_memory_kib: int = 65536
    app_auth_argon2_parallelism: int = 4

    @model_validator(mode="after")
    def validate_security_boundaries(self) -> Settings:
        """非 local 環境で debug bypass と非 Secure cookie を fail-closed にする。"""
        if self.oracle_deepsec_enabled and self.oracle_driver_mode.strip().lower() != "thin":
            raise ValueError(
                "ORACLE_DEEPSEC_ENABLED=true の場合は ORACLE_DRIVER_MODE=thin が必要です。"
            )
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
    def resolved_oracle_wallet_dir(self) -> str:
        """明示 Wallet 配置先を優先し、未指定時だけ Thick の既定位置を返す。"""
        wallet_dir = self.oracle_wallet_dir.strip()
        if wallet_dir:
            return wallet_dir
        if self.oracle_driver_mode.strip().lower() == "thin":
            return ""
        client_lib_dir = self.oracle_client_lib_dir.strip()
        if client_lib_dir:
            return str(Path(client_lib_dir).expanduser() / "network" / "admin")
        return ""

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

    @field_validator("oracle_connection_security")
    @classmethod
    def validate_oracle_connection_security(cls, value: str) -> str:
        normalized = value.strip().lower()
        if normalized not in {"wallet_mtls", "walletless_tls"}:
            raise ValueError(
                "ORACLE_CONNECTION_SECURITY は wallet_mtls または "
                "walletless_tls を指定してください。"
            )
        return normalized


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
    MODEL_SETTINGS_STORE.reset()


def resolve_model_settings_file(path_value: str) -> Path:
    """MODEL_SETTINGS_FILE を backend/.env と同じディレクトリ基準で解決する。"""
    raw_path = path_value.strip() or DEFAULT_MODEL_SETTINGS_FILE
    path = Path(raw_path).expanduser()
    if path.is_absolute():
        return path
    return (BACKEND_DIR / path).resolve()


# モデル設定の読み書きは3製品共通（platform の pr_system_settings。#103）。
MODEL_SETTINGS_STORE = ModelSettingsStore(
    resolve_path=lambda settings: resolve_model_settings_file(settings.model_settings_file),
    env_file=lambda _settings: BACKEND_ENV_FILE,
)


def load_persisted_model_settings(settings: Settings) -> None:
    """UI 保存済みのモデル設定 JSON があれば Settings へ上書き適用する。"""
    MODEL_SETTINGS_STORE.load(settings)


def reload_persisted_model_settings_if_changed(settings: Settings) -> None:
    """別 worker が保存したモデル設定を次回リクエストで取り込む。"""
    MODEL_SETTINGS_STORE.reload_if_changed(settings)
