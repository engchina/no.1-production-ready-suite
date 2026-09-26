"""サービス設定。共通基底 BaseServiceSettings を継承し、ドメイン設定を足す。"""

from functools import lru_cache
from pathlib import Path

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
    # システム設定（3製品共通）。
    oci_config_file: str = "~/.oci/config"
    oci_config_profile: str = "DEFAULT"
    oci_compartment_id: str = ""
    oci_region: str = ""
    object_storage_region: str = ""
    object_storage_namespace: str = ""
    object_storage_bucket: str = ""
    upload_storage_backend: str = "local"
    local_storage_dir: str = "/u01/data/production-ready-agent"
    max_upload_bytes: int = 100 * 1024 * 1024
    model_settings_file: str = "model-settings.json"
    oci_enterprise_ai_endpoint: str = ""
    oci_enterprise_ai_project_ocid: str = ""
    oci_enterprise_ai_api_key: str = ""
    oci_enterprise_ai_models: list[EnterpriseAiConfiguredModel] = Field(default_factory=list)
    oci_enterprise_ai_default_model: str = ""
    oci_enterprise_ai_llm_model: str = ""
    oci_enterprise_ai_vlm_model: str = ""
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
    agent_external_rag_base_url: str | None = None
    agent_external_rag_api_key: str | None = None
    agent_external_rag_timeout_seconds: float = 10.0
    agent_external_rag_max_retries: int = 3
    agent_external_nl2sql_base_url: str | None = None
    agent_external_nl2sql_api_key: str | None = None
    agent_external_nl2sql_timeout_seconds: float = 15.0
    agent_external_nl2sql_default_limit: int = 100
    agent_external_nl2sql_max_retries: int = 3
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
    # Control Plane が管理する外部 Runtime。secret は値ではなく env 名で参照する。
    agent_runtime_adapter_timeout_seconds: float = 30.0
    agent_runtime_bindings_dir: str = ".agent-runtime-bindings"
    agent_runtime_dispatch_mode: str = "in_process"
    agent_runtime_dispatch_poll_seconds: float = 1.0
    agent_runtime_dispatch_lease_seconds: int = 120
    agent_runtime_service_control_enabled: bool = False
    agent_runtime_service_control_command: str = "docker compose"
    agent_runtime_service_control_timeout_seconds: float = 300.0
    agent_control_plane_public_base_url: str = "http://backend:8000/api"
    agent_control_plane_mcp_token_secret: str | None = None
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
    agent_rbac_enabled: bool = False
    agent_rbac_actor_header: str = "x-agent-actor"
    agent_rbac_roles_header: str = "x-agent-roles"
    agent_rbac_business_views_header: str = "x-agent-business-views"
    agent_rbac_actor_policies_json: str | None = None
    agent_rbac_identity_header: str = "x-agent-identity"
    agent_rbac_identity_hmac_secret: str | None = None
    agent_rbac_policy_url: str | None = None
    agent_rbac_policy_api_key: str | None = None
    agent_rbac_policy_timeout_seconds: float = 2.0
    agent_rbac_policy_cache_seconds: int = 60
    agent_rbac_jwt_bearer_enabled: bool = False
    agent_rbac_jwt_hs256_secret: str | None = None
    agent_rbac_jwt_jwks_url: str | None = None
    agent_rbac_jwt_jwks_cache_seconds: int = 300
    agent_rbac_jwt_issuer: str | None = None
    agent_rbac_jwt_audience: str | None = None
    agent_rbac_jwt_roles_claim: str = "roles"
    agent_rbac_jwt_business_views_claim: str = "business_view_ids"
    agent_rbac_jwt_agent_ids_claim: str = "agent_ids"
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
