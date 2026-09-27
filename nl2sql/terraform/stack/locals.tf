locals {
  compute_subnet_prohibits_public_ip = coalesce(
    data.oci_core_subnet.selected_compute_subnet.prohibit_public_ip_on_vnic,
    false,
  )

  app_name        = "production-ready-nl2sql"
  app_repo_dir    = "no.1-production-ready-suite/nl2sql"
  wallet_dir_host = "/u01/aipoc/wallet"
  oracle_connection_security = (
    var.adb_is_mtls_connection_required ? "wallet_mtls" : "walletless_tls"
  )

  # NL2SQL 固有の設定（backend/.env）。3製品共通の設定は platform_env（platform/.env）に置く（#211）。
  backend_env = <<-EOT
NL2SQL_APP_VERSION=0.1.0
NL2SQL_LOG_LEVEL=INFO
NL2SQL_ENVIRONMENT=${var.app_environment}
NL2SQL_SERVICE_NAME=${local.app_name}
NL2SQL_CORS_ORIGINS=["http://localhost","http://127.0.0.1"]
NL2SQL_ENABLE_METRICS=true
NL2SQL_DEBUG=false

NL2SQL_APP_AUTH_ENABLED=true
NL2SQL_APP_AUTH_SESSION_COOKIE_NAME=nl2sql_session
NL2SQL_APP_AUTH_CSRF_COOKIE_NAME=nl2sql_csrf

NL2SQL_ORACLE_DEEPSEC_ENABLED=${var.oracle_deepsec_enabled}
NL2SQL_ORACLE_DEEPSEC_DATA_USER=DEEPSEC_DATA_USER
NL2SQL_ORACLE_DEEPSEC_DATA_USER_PASSWORD=${var.oracle_deepsec_enabled ? var.oracle_deepsec_data_user_password : ""}

NL2SQL_RUNTIME_MODE=oracle
NL2SQL_PERSISTENCE_MODE=oracle
NL2SQL_ORACLE_STATE_TABLE=NL2SQL_STATE_STORE
NL2SQL_STATE_BACKEND=incremental
NL2SQL_SCHEMA_REFRESH_WORKER_ENABLED=true
NL2SQL_SCHEMA_REFRESH_WORKER_MODE=external
NL2SQL_QUALITY_EVALUATION_WORKER_MODE=external
NL2SQL_ONTOLOGY_WORKER_MODE=external
NL2SQL_SELECT_AI_CREDENTIAL_NAME=OCI_CRED
NL2SQL_SELECT_AI_REGION=us-chicago-1
EOT

  # 3製品共通の設定（platform/.env。変数名は PLATFORM_*）。システム設定画面の保存先もここ（#211）。
  platform_env = <<-EOT
PLATFORM_ADMIN_LOGIN_USER_ID=${var.app_admin_login_user_id}
PLATFORM_ADMIN_LOGIN_USER_PASSWORD=${var.app_admin_login_user_password}
PLATFORM_AUTH_COOKIE_SECURE=${var.app_auth_cookie_secure}

PLATFORM_ORACLE_USER=${local.effective_oracle_user}
PLATFORM_ORACLE_PASSWORD=${local.effective_oracle_password}
PLATFORM_ORACLE_DSN=${local.effective_oracle_dsn}
PLATFORM_ORACLE_DRIVER_MODE=thin
PLATFORM_ORACLE_CONNECTION_SECURITY=${local.oracle_connection_security}
PLATFORM_ORACLE_CLIENT_LIB_DIR=
PLATFORM_ORACLE_WALLET_DIR=${local.wallet_dir_host}
PLATFORM_ORACLE_WALLET_PASSWORD=${local.effective_oracle_wallet_password}
PLATFORM_ORACLE_ADB_OCID=${local.effective_adb_ocid}
PLATFORM_ORACLE_ADB_REGION=${var.region}

PLATFORM_OCI_REGION=${var.region}
PLATFORM_OCI_COMPARTMENT_ID=${var.compartment_ocid}

PLATFORM_MODEL_SETTINGS_FILE=/u01/data/production-ready-nl2sql/model-settings.json

PLATFORM_UPLOAD_STORAGE_BACKEND=local
PLATFORM_LOCAL_STORAGE_DIR=/u01/data/production-ready-nl2sql
PLATFORM_OBJECT_STORAGE_REGION=${var.region}
PLATFORM_OBJECT_STORAGE_NAMESPACE=
PLATFORM_OBJECT_STORAGE_BUCKET=nl2sql-originals
EOT

  cloud_init_rendered = templatefile("${path.module}/cloud_init/bootstrap.template.yaml", {
    adb_name            = local.effective_adb_name
    adb_ocid            = local.effective_adb_ocid
    application_git_ref = var.application_git_ref
    application_git_url = var.application_git_url
    application_port    = tostring(var.application_port)
    backend_env         = base64gzip(local.backend_env)
    platform_env        = base64gzip(local.platform_env)
    compartment_ocid    = var.compartment_ocid
    db_dsn              = local.effective_oracle_dsn
    region              = var.region
    wallet_content      = data.external.wallet_files.result.wallet_content
    wallet_dir_host     = local.wallet_dir_host
  })

  cloud_init_user_data = base64gzip(local.cloud_init_rendered)
}
