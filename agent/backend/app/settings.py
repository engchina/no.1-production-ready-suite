"""サービス設定。共通基底 BaseServiceSettings を継承し、ドメイン設定を足す。"""

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pr_backend_core.config import (
    BaseServiceSettings,
    platform_env_file,
    product_settings_config,
)
from pr_system_settings.model import (
    EnterpriseAiConfiguredModel,
    ModelSecretStateMixin,
    ModelSettingsStore,
)
from pydantic import Field

BACKEND_DIR = Path(__file__).resolve().parents[1]
# 製品固有の設定（`AGENT_*`）は `backend/.env`（画面からは書かない）。
# 3製品共通の設定（`PLATFORM_*`）を置く共通 `.env`（既定は `<repo>/platform/.env`。#211）。
PLATFORM_ENV_FILE = platform_env_file(BACKEND_DIR)


class Settings(ModelSecretStateMixin, BaseServiceSettings):
    """サービス固有設定。

    環境変数名は属性名から決まる（#211）。3製品共通の属性（OCI 認証・アップロード保存先・
    モデル・データベース）は共通 `.env` の `PLATFORM_*`、それ以外は `backend/.env` の `AGENT_*`。
    """

    model_config = product_settings_config(prefix="AGENT_", backend_dir=BACKEND_DIR)

    service_name: str = "production-ready-agent"
    # --- 認証（#215）---
    # RAG / NL2SQL と同じ共通認証（#750）。ユーザー・ロールはどちらも Oracle の PLATFORM_*。
    # local: ログインを省略し、全権限のローカル利用者として使う（開発用）。
    # production: 共通認証（PLATFORM_* のユーザー・ロール）のログインを必須にする。Cookie の
    #   ないリクエストは 401（Runtime からの呼出しは Binding token の /api/mcp/{binding_id}）。
    auth_mode: Literal["local", "production"] = "local"
    # 構成管理者と認証ポリシーは共通 `.env` の PLATFORM_ADMIN_* / PLATFORM_AUTH_*（#211）、
    # Cookie 名は製品ごとの AGENT_APP_AUTH_*。構成管理者 token の署名鍵に service_name を使う。
    app_admin_login_user_id: str = ""
    app_admin_login_user_password: str = ""
    app_auth_cookie_secure: bool = False
    app_auth_session_cookie_name: str = "agent_session"
    app_auth_csrf_cookie_name: str = "agent_csrf"
    app_auth_idle_timeout_minutes: int = 60
    app_auth_absolute_timeout_hours: int = 12
    app_auth_failed_login_limit: int = 5
    app_auth_lockout_minutes: int = 15
    app_auth_password_min_length: int = 12
    app_auth_password_max_length: int = 128
    app_auth_argon2_time_cost: int = 3
    app_auth_argon2_memory_kib: int = 65536
    app_auth_argon2_parallelism: int = 4
    # サービス間の token の署名鍵（共通 `.env` の PLATFORM_SERVICE_TOKEN_SECRET。#230 / #233）。
    app_service_token_secret: str = ""
    # システム設定（3製品共通）。
    oci_config_file: str = "~/.oci/config"
    oci_config_profile: str = "DEFAULT"
    oci_compartment_id: str = ""
    oci_region: str = ""
    object_storage_region: str = ""
    object_storage_namespace: str = ""
    object_storage_bucket: str = "production-ready"
    upload_storage_backend: str = "local"
    local_storage_dir: str = "/u01/data/production-ready-agent"
    max_upload_bytes: int = 100 * 1024 * 1024
    model_settings_file: str = "model-settings.json"
    oci_enterprise_ai_endpoint: str = ""
    oci_enterprise_ai_project_ocid: str = ""
    oci_enterprise_ai_api_key: str = ""
    # OCI Enterprise AI のセカンダリ接続（#533）。モデルを呼ぶ接続は
    # enterprise_ai_connection_for_model で引く。
    oci_enterprise_ai_secondary_endpoint: str = ""
    oci_enterprise_ai_secondary_project_ocid: str = ""
    oci_enterprise_ai_secondary_api_key: str = ""
    oci_enterprise_ai_models: list[EnterpriseAiConfiguredModel] = Field(default_factory=list)
    oci_enterprise_ai_default_text_model: str = ""
    oci_enterprise_ai_default_vision_model: str = ""
    oci_enterprise_ai_llm_path: str = "/responses"
    oci_enterprise_ai_vlm_path: str = "/responses"
    oci_enterprise_ai_vlm_input_mode: str = "auto"
    oci_enterprise_ai_llm_payload_template: str = ""
    oci_enterprise_ai_vlm_payload_template: str = ""
    oci_enterprise_ai_llm_response_path: str = ""
    oci_enterprise_ai_vlm_response_path: str = ""
    oci_enterprise_ai_timeout_seconds: float = 600.0
    oci_enterprise_ai_max_retries: int = 3
    oci_enterprise_ai_llm_max_output_tokens: int = 1200
    oci_enterprise_ai_vlm_max_output_tokens: int = 65536
    oci_genai_embedding_model: str = "cohere.embed-v4.0"
    oci_genai_embedding_dim: int = 1536
    oci_genai_rerank_model: str = "cohere.rerank-v4.0-fast"
    oracle_dsn: str | None = None
    oracle_user: str | None = None
    oracle_password: str | None = None
    oracle_client_lib_dir: str = "/u01/aipoc/instantclient_23_26"
    oracle_wallet_dir: str | None = None
    oracle_wallet_password: str | None = None
    oracle_adb_ocid: str | None = None
    oracle_adb_region: str | None = None
    oracle_tcp_connect_timeout_seconds: float = 10.0
    oracle_db_test_timeout_seconds: float = 15.0
    # RAG / NL2SQL は各製品の MCP（`POST /api/mcp`）を Run の利用者として呼ぶ（#233）。
    # 例: http://rag-host/api/mcp。token は共通 `.env` の PLATFORM_SERVICE_TOKEN_SECRET で署名する。
    # LLM を使う呼び出しがあるため、タイムアウトは NL2SQL の待ち時間（最大 45 秒）より長くする。
    agent_external_rag_mcp_url: str | None = None
    agent_external_rag_timeout_seconds: float = 60.0
    agent_external_rag_max_retries: int = 3
    agent_external_nl2sql_mcp_url: str | None = None
    agent_external_nl2sql_timeout_seconds: float = 60.0
    agent_external_nl2sql_default_limit: int = 100
    agent_external_nl2sql_max_retries: int = 3
    # Run の利用者がいない呼び出し（Binding 経由の MCP・外部 RBAC で作った Run）で使う共通認証の
    # ログインユーザー ID。空ならその呼び出しは失敗する。
    agent_mcp_service_user_login_id: str = ""
    agent_external_mcp_base_url: str | None = None
    agent_external_mcp_api_key: str | None = None
    agent_external_mcp_session_id: str | None = None
    agent_external_mcp_oauth_token_url: str | None = None
    agent_external_mcp_oauth_client_id: str | None = None
    agent_external_mcp_oauth_client_secret: str | None = None
    agent_external_mcp_oauth_scope: str | None = None
    agent_external_mcp_timeout_seconds: float = 10.0
    agent_external_mcp_max_retries: int = 3
    # 複数 MCP server の宣言。JSON list か {"servers": [...]}。各項目は
    # server_id/id 必須、base_url・auth・timeout_seconds を任意で持つ。
    agent_external_mcp_servers_json: str | None = None
    # Skill 外部定義: 中立ディレクトリ(skills/<id>/SKILL.md)と JSON 宣言。
    agent_skills_dir: str | None = None
    agent_skills_definitions_json: str | None = None
    # Plugin 宣言: install 済み plugin manifest と marketplace ソース。
    agent_plugins_json: str | None = None
    agent_plugin_marketplaces_json: str | None = None
    # 組み込み Runtime の実行（#754）。in_process は API のプロセスで実行し、
    # それ以外は runtime_dispatcher（別プロセス）が claim して実行する。
    agent_runtime_dispatch_mode: str = "in_process"
    agent_runtime_dispatch_poll_seconds: float = 1.0
    agent_runtime_dispatch_lease_seconds: int = 120
    agent_planner_provider: str = "heuristic"
    agent_planner_oci_responses_base_url: str | None = None
    agent_planner_oci_responses_api_key: str | None = None
    agent_planner_oci_responses_model: str | None = None
    agent_planner_oci_responses_project: str | None = None
    agent_planner_oci_agent_endpoint: str | None = None
    agent_planner_oci_agent_api_key: str | None = None
    # Deprecated compatibility aliases. Prefer AGENT_PLANNER_OCI_RESPONSES_*.
    agent_planner_enterprise_ai_endpoint: str | None = None
    agent_planner_enterprise_ai_api_key: str | None = None
    agent_planner_timeout_seconds: float = 8.0
    agent_planner_max_retries: int = 3
    agent_planner_fallback_to_heuristic: bool = True
    agent_planner_allowed_tool_names: str = "agent_skill_run"
    agent_planner_allow_command_generation: bool = False
    agent_permission_default_mode: str = "approval"
    agent_memory_enabled: bool = True
    agent_runtime_repository_backend: str = "memory"
    agent_runtime_snapshot_path: str | None = None
    agent_runtime_oracle_dsn: str | None = None
    agent_runtime_oracle_user: str | None = None
    agent_runtime_oracle_password: str | None = None
    # ADB の Wallet(mTLS) で接続する場合の Wallet 展開先と Wallet password（Thin mode）。
    # 未設定なら従来どおり user/password/dsn だけで接続する。
    agent_runtime_oracle_wallet_dir: str | None = None
    agent_runtime_oracle_wallet_password: str | None = None
    agent_runtime_oracle_table: str = "AGENT_RUNTIME_CHECKPOINTS"
    agent_runtime_oracle_checkpoint_key: str = "default"
    agent_runtime_oracle_create_schema: bool = True
    agent_runtime_oracle_projection_prefix: str = "AGENT_RUNTIME"
    agent_runtime_oracle_projection_retention_days: int = 0
    agent_runtime_oracle_projection_write_mode: str = "replace"
    agent_max_tool_calls_per_run: int = 20
    agent_max_pending_approvals_per_run: int = 5
    agent_metrics_enabled: bool = True
    agent_trace_events_enabled: bool = True
    agent_trace_events_buffer_size: int = 500
    agent_trace_events_retention_seconds: int = 86_400
    agent_trace_exporter_url: str | None = None
    agent_trace_exporter_api_key: str | None = None
    agent_trace_exporter_timeout_seconds: float = 2.0
    agent_trace_exporter_retry_queue_size: int = 100
    agent_trace_exporter_retry_max_attempts: int = 3
    agent_trace_exporter_retry_base_delay_seconds: float = 1.0
    agent_trace_exporter_retry_max_delay_seconds: float = 60.0
    agent_trace_exporter_retry_worker_enabled: bool = True
    agent_trace_exporter_retry_worker_interval_seconds: float = 5.0
    agent_trace_exporter_retry_worker_batch_size: int = 100
    agent_trace_sample_rate: float = 1.0
    agent_langfuse_host: str | None = None
    agent_langfuse_public_key: str | None = None
    agent_langfuse_secret_key: str | None = None
    agent_opentelemetry_endpoint: str | None = None
    agent_command_tools_enabled: bool = False
    agent_command_workspace_root: str = "."
    agent_command_allowed_prefixes: str = ""
    agent_command_default_timeout_seconds: float = 10.0
    agent_command_max_timeout_seconds: float = 30.0
    agent_command_output_limit_bytes: int = 20_000
    agent_command_sanitized_env_enabled: bool = True
    agent_command_env_allowlist: str = "PATH,HOME,LANG,LC_ALL,LC_CTYPE,TERM"
    agent_command_max_memory_mb: int = 512
    agent_command_max_open_files: int = 64
    agent_command_start_new_session: bool = True
    agent_command_isolation_mode: str = "process"
    agent_command_container_image: str | None = None
    agent_command_container_network: str = "none"
    agent_command_container_security_opts: str = "no-new-privileges:true"
    agent_command_container_userns: str | None = None
    agent_command_container_user: str | None = None
    agent_artifact_storage_backend: str = "inline"
    agent_artifact_storage_path: str = ".agent-artifacts"

    @property
    def local_debug_enabled(self) -> bool:
        """local mode: 画面は全権限のローカル利用者（DB セッションを作らない）。"""
        return self.auth_mode == "local"

    @property
    def app_auth_enabled(self) -> bool:
        """production mode: 画面は共通認証のログインを必須にする。"""
        return self.auth_mode == "production"

    @property
    def oracle_driver_mode(self) -> str:
        """Agent は python-oracledb の Thin mode だけで接続する（Wallet の判定用）。"""
        return "thin"

    @property
    def oracle_connection_security(self) -> str:
        """Agent は Wallet mTLS だけに対応する。"""
        return "wallet_mtls"

    @property
    def resolved_oracle_adb_region(self) -> str:
        """ADB 管理用の region。未設定なら PLATFORM_OCI_REGION を使う。"""
        return (self.oracle_adb_region or self.oci_region or "").strip()

    @property
    def resolved_oracle_wallet_dir(self) -> str:
        """参照実装と同じく PLATFORM_ORACLE_CLIENT_LIB_DIR/network/admin を Wallet 配置先にする。"""
        client_lib_dir = self.oracle_client_lib_dir.strip()
        if client_lib_dir:
            return str(Path(client_lib_dir).expanduser() / "network" / "admin")
        return (self.oracle_wallet_dir or "").strip()


def resolve_model_settings_file(path_value: str) -> Path:
    """PLATFORM_MODEL_SETTINGS_FILE を共通 `.env` と同じディレクトリ基準で解決する。"""
    path = Path(path_value.strip() or "model-settings.json").expanduser()
    return path if path.is_absolute() else (PLATFORM_ENV_FILE.parent / path).resolve()


# モデル設定の読み書きは3製品共通（platform の pr_system_settings。#103）。
# model-settings.json と API key は3製品で共有する（共通 `.env`。#211）。
# テストで PLATFORM_ENV_FILE を差し替えられるよう、呼出時に module の値を参照する。
MODEL_SETTINGS_STORE = ModelSettingsStore(
    resolve_path=lambda settings: resolve_model_settings_file(settings.model_settings_file),
    env_file=lambda _settings: PLATFORM_ENV_FILE,
)


@lru_cache
def _settings_singleton() -> Settings:
    settings = Settings()
    MODEL_SETTINGS_STORE.load(settings)
    return settings


def get_settings() -> Settings:
    """設定シングルトン。保存済みのモデル設定が更新されていれば再読込する。"""
    settings = _settings_singleton()
    MODEL_SETTINGS_STORE.reload_if_changed(settings)
    return settings


def reset_settings_cache() -> None:
    """テストや明示的な再初期化のため Settings singleton を破棄する。"""
    _settings_singleton.cache_clear()
    MODEL_SETTINGS_STORE.reset()
