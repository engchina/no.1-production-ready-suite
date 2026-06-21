"""サービス設定。共通基底 BaseServiceSettings を継承し、ドメイン設定を足す。"""

from functools import lru_cache

from pr_backend_core.config import BaseServiceSettings


class Settings(BaseServiceSettings):
    """サービス固有設定。

    OCI/Oracle 等の接続設定はここに追加する（例: oracle_dsn, oci_region ...）。
    """

    service_name: str = "production-ready-agent"
    # RAG 由来のシステム設定画面との互換設定。
    oci_config_file: str = "~/.oci/config"
    oci_config_profile: str = "DEFAULT"
    oci_user_ocid: str | None = None
    oci_fingerprint: str | None = None
    oci_tenancy_ocid: str | None = None
    oci_key_file: str = "~/.oci/oci_api_key.pem"
    oci_key_file_exists: bool = False
    oci_config_file_exists: bool = False
    oci_region: str = "us-chicago-1"
    object_storage_region: str = "ap-osaka-1"
    object_storage_namespace: str | None = None
    object_storage_bucket: str | None = None
    upload_storage_backend: str = "local"
    local_storage_dir: str = "/u01/production-ready-rag"
    max_upload_bytes: int = 100 * 1024 * 1024
    enterprise_ai_endpoint: str | None = None
    enterprise_ai_project_ocid: str | None = None
    enterprise_ai_api_key: str | None = None
    enterprise_ai_default_model_id: str | None = None
    enterprise_ai_api_path: str = "/responses"
    enterprise_ai_vlm_input_mode: str = "auto"
    enterprise_ai_text_response_path: str = "output_text"
    enterprise_ai_vision_response_path: str = "output_text"
    enterprise_ai_timeout_seconds: int = 60
    enterprise_ai_max_retries: int = 3
    embedding_model: str = "cohere.embed-v4.0"
    embedding_dim: int = 1536
    rerank_model: str = "cohere.rerank-v4.0-fast"
    oracle_dsn: str | None = None
    oracle_user: str | None = None
    oracle_password: str | None = None
    oracle_wallet_dir: str | None = None
    oracle_wallet_password: str | None = None
    oracle_wallet_uploaded: bool = False
    oracle_region: str | None = None
    adb_ocid: str | None = None
    agent_external_rag_base_url: str | None = None
    agent_external_rag_api_key: str | None = None
    agent_external_rag_timeout_seconds: float = 10.0
    agent_external_rag_max_retries: int = 1
    agent_external_nl2sql_base_url: str | None = None
    agent_external_nl2sql_api_key: str | None = None
    agent_external_nl2sql_timeout_seconds: float = 15.0
    agent_external_nl2sql_default_limit: int = 100
    agent_external_nl2sql_max_retries: int = 1
    agent_external_mcp_base_url: str | None = None
    agent_external_mcp_api_key: str | None = None
    agent_external_mcp_session_id: str | None = None
    agent_external_mcp_oauth_token_url: str | None = None
    agent_external_mcp_oauth_client_id: str | None = None
    agent_external_mcp_oauth_client_secret: str | None = None
    agent_external_mcp_oauth_scope: str | None = None
    agent_external_mcp_timeout_seconds: float = 10.0
    agent_external_mcp_max_retries: int = 1
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
    agent_planner_max_retries: int = 1
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


@lru_cache
def get_settings() -> Settings:
    """設定シングルトン。"""
    return Settings()
