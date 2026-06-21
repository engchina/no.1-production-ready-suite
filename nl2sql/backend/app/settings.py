"""サービス設定。共通基底 BaseServiceSettings を継承し、ドメイン設定を足す。"""

from functools import lru_cache
from pathlib import Path

from pr_backend_core.config import BaseServiceSettings


class Settings(BaseServiceSettings):
    """サービス固有設定。

    OCI/Oracle 等の接続設定はここに追加する（例: oracle_dsn, oci_region ...）。
    """

    service_name: str = "production-ready-nl2sql"
    enable_metrics: bool = True
    oracle_user: str = ""
    oracle_password: str = ""
    oracle_dsn: str = ""
    oracle_client_lib_dir: str = "/u01/aipoc/instantclient_23_26"
    oracle_wallet_dir: str = ""
    oracle_wallet_password: str = ""
    oracle_adb_ocid: str = ""
    oci_region: str = ""
    oci_compartment_id: str = ""
    oci_config_file: str = "~/.oci/config"
    oci_config_profile: str = "DEFAULT"
    oci_profile: str = "DEFAULT"
    oci_auth_mode: str = "config_file"
    oci_user_ocid: str = ""
    oci_fingerprint: str = ""
    oci_tenancy_ocid: str = ""
    oci_key_file: str = ""
    oci_genai_endpoint: str = ""
    oci_genai_embed_model_id: str = "cohere.embed-v4.0"
    oci_genai_rerank_model_id: str = "cohere.rerank-v4.0-fast"
    oci_enterprise_ai_endpoint: str = ""
    oci_enterprise_ai_project_ocid: str = ""
    oci_enterprise_ai_api_key: str = ""
    oci_enterprise_ai_default_model: str = ""
    oci_enterprise_ai_llm_model: str = ""
    oci_enterprise_ai_llm_path: str = "/responses"
    oci_enterprise_ai_llm_payload_template: str = ""
    oci_enterprise_ai_llm_response_path: str = ""
    oci_enterprise_ai_vlm_model: str = ""
    oci_enterprise_ai_vlm_payload_template: str = ""
    oci_enterprise_ai_vlm_response_path: str = ""
    oci_enterprise_ai_vlm_input_mode: str = "auto"
    oci_enterprise_ai_timeout_seconds: float = 600.0
    oci_enterprise_ai_max_retries: int = 3
    oci_enterprise_ai_llm_max_output_tokens: int = 1200
    model_settings_file: str = "model-settings.json"
    upload_storage_backend: str = "local"
    local_storage_dir: str = "/u01/production-ready-rag"
    object_storage_region: str = ""
    object_storage_namespace: str = ""
    object_storage_bucket: str = ""
    max_upload_bytes: int = 200 * 1024 * 1024
    # NL2SQL 安全境界（既定: SELECT のみ許可）。DDL/DML/PLSQL は禁止する方針。
    nl2sql_allow_select_only: bool = True
    nl2sql_default_row_limit: int = 100
    # deterministic: local/CI 用 mock, oracle: python-oracledb 経由で Oracle / Select AI を呼ぶ。
    nl2sql_runtime_mode: str = "deterministic"
    # memory: local/CI 用, oracle: Oracle JSON CLOB table へ profile/job/history 等を保存。
    nl2sql_persistence_mode: str = "memory"
    nl2sql_oracle_state_table: str = "NL2SQL_STATE_STORE"
    # ユーザ要望により Oracle Select AI / Select AI Agent を NL2SQL エンジンとして同時サポートする。
    # local / CI では deterministic adapter、実運用では Oracle DB adapter に差し替える。
    nl2sql_select_ai_enabled: bool = True
    nl2sql_select_ai_agent_enabled: bool = True
    nl2sql_enterprise_ai_direct_enabled: bool = True
    nl2sql_select_ai_profile_prefix: str = "NL2SQL"
    nl2sql_select_ai_provider: str = "oci"
    nl2sql_select_ai_credential_name: str = ""
    nl2sql_select_ai_model: str = ""
    nl2sql_schema_sample_rows: int = 3
    nl2sql_schema_sample_columns_per_table: int = 6
    nl2sql_oracle_connect_timeout_seconds: int = 5
    nl2sql_csv_import_max_rows: int = 5000
    nl2sql_csv_import_max_columns: int = 200
    nl2sql_feedback_embedding_enabled: bool = False
    nl2sql_feedback_vector_table: str = "NL2SQL_FEEDBACK_VECTORS"
    nl2sql_feedback_vector_index: str = "NL2SQL_FEEDBACK_VEC_IDX"

    @property
    def resolved_oracle_wallet_dir(self) -> str:
        """RAG と同じく ORACLE_CLIENT_LIB_DIR/network/admin を Wallet 配置先にする。"""
        client_lib_dir = self.oracle_client_lib_dir.strip()
        if client_lib_dir:
            return str(Path(client_lib_dir).expanduser() / "network" / "admin")
        return self.oracle_wallet_dir.strip()


@lru_cache
def get_settings() -> Settings:
    """設定シングルトン。"""
    return Settings()
