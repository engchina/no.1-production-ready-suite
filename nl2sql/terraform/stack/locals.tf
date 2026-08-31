locals {
  compute_subnet_prohibits_public_ip = coalesce(
    data.oci_core_subnet.selected_compute_subnet.prohibit_public_ip_on_vnic,
    false,
  )

  app_name          = "production-ready-nl2sql"
  app_repo_dir      = "no.1-production-ready-nl2sql"
  platform_repo_dir = "no.1-production-ready-platform"
  wallet_dir_host   = "/u01/aipoc/wallet"
  oracle_connection_security = (
    var.adb_is_mtls_connection_required ? "wallet_mtls" : "walletless_tls"
  )

  backend_env = <<-EOT
APP_VERSION=0.1.0
LOG_LEVEL=INFO
ENVIRONMENT=${var.app_environment}
SERVICE_NAME=${local.app_name}
CORS_ORIGINS=["http://localhost","http://127.0.0.1"]
ENABLE_METRICS=true
DEBUG=false

APP_AUTH_ENABLED=true
APP_ADMIN_LOGIN_USER_ID=${var.app_admin_login_user_id}
APP_ADMIN_LOGIN_USER_PASSWORD=${var.app_admin_login_user_password}
APP_AUTH_COOKIE_SECURE=${var.app_auth_cookie_secure}
APP_AUTH_SESSION_COOKIE_NAME=nl2sql_session
APP_AUTH_CSRF_COOKIE_NAME=nl2sql_csrf

ORACLE_USER=${local.effective_oracle_user}
ORACLE_PASSWORD=${local.effective_oracle_password}
ORACLE_DSN=${local.effective_oracle_dsn}
ORACLE_DRIVER_MODE=thin
ORACLE_CONNECTION_SECURITY=${local.oracle_connection_security}
ORACLE_CLIENT_LIB_DIR=
ORACLE_WALLET_DIR=${local.wallet_dir_host}
ORACLE_WALLET_PASSWORD=${local.effective_oracle_wallet_password}
ORACLE_DEEPSEC_ENABLED=true
ORACLE_DEEPSEC_DATA_USER=DEEPSEC_DATA_USER
ORACLE_DEEPSEC_DATA_USER_PASSWORD=${var.oracle_deepsec_data_user_password}
ORACLE_ADB_OCID=${local.effective_adb_ocid}
ORACLE_ADB_REGION=${var.region}

OCI_REGION=${var.region}
OCI_COMPARTMENT_ID=${var.compartment_ocid}

MODEL_SETTINGS_FILE=/u01/data/production-ready-nl2sql/model-settings.json

UPLOAD_STORAGE_BACKEND=local
LOCAL_STORAGE_DIR=/u01/data/production-ready-nl2sql
OBJECT_STORAGE_REGION=${var.region}
OBJECT_STORAGE_NAMESPACE=
OBJECT_STORAGE_BUCKET=nl2sql-originals

NL2SQL_RUNTIME_MODE=oracle
NL2SQL_PERSISTENCE_MODE=oracle
NL2SQL_ORACLE_STATE_TABLE=NL2SQL_STATE_STORE
NL2SQL_STATE_BACKEND=incremental
NL2SQL_SCHEMA_REFRESH_WORKER_ENABLED=true
NL2SQL_SCHEMA_REFRESH_WORKER_MODE=external
NL2SQL_QUALITY_EVALUATION_WORKER_MODE=external
NL2SQL_ONTOLOGY_WORKER_MODE=external
NL2SQL_SELECT_AI_CREDENTIAL_NAME=OCI_CRED
NL2SQL_SELECT_AI_REGION=ap-osaka-1
EOT

  cloud_init_rendered = templatefile("${path.module}/cloud_init/bootstrap.template.yaml", {
    adb_name            = local.effective_adb_name
    adb_ocid            = local.effective_adb_ocid
    application_git_ref = var.application_git_ref
    application_git_url = var.application_git_url
    application_port    = tostring(var.application_port)
    backend_env         = base64gzip(local.backend_env)
    compartment_ocid    = var.compartment_ocid
    db_dsn              = local.effective_oracle_dsn
    platform_git_ref    = var.platform_git_ref
    platform_git_url    = var.platform_git_url
    region              = var.region
    wallet_content      = data.external.wallet_files.result.wallet_content
    wallet_dir_host     = local.wallet_dir_host
  })

  cloud_init_user_data = base64gzip(local.cloud_init_rendered)
}
