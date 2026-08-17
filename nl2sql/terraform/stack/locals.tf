locals {
  compute_subnet_prohibits_public_ip = coalesce(
    data.oci_core_subnet.selected_compute_subnet.prohibit_public_ip_on_vnic,
    false,
  )

  app_name             = "production-ready-nl2sql"
  app_repo_dir         = "no.1-production-ready-nl2sql"
  platform_repo_dir    = "no.1-production-ready-platform"
  wallet_dir_host      = "/u01/aipoc/wallet"
  wallet_dir_container = "/u01/aipoc/wallet"

  backend_env = <<-EOT
APP_VERSION=0.1.0
LOG_LEVEL=INFO
ENVIRONMENT=${var.app_environment}
SERVICE_NAME=${local.app_name}
CORS_ORIGINS=["http://localhost:${var.application_port}","http://127.0.0.1:${var.application_port}"]
ENABLE_METRICS=true
DEBUG=false

APP_AUTH_ENABLED=true
APP_AUTH_COOKIE_SECURE=${var.app_auth_cookie_secure}
APP_AUTH_SESSION_COOKIE_NAME=nl2sql_session
APP_AUTH_CSRF_COOKIE_NAME=nl2sql_csrf

ORACLE_USER=ADMIN
ORACLE_PASSWORD=${var.adb_password}
ORACLE_DSN=${lower(var.adb_name)}_high
ORACLE_DRIVER_MODE=thin
ORACLE_CLIENT_LIB_DIR=
ORACLE_WALLET_DIR=${local.wallet_dir_container}
ORACLE_WALLET_PASSWORD=${var.adb_password}
ORACLE_ADB_OCID=${oci_database_autonomous_database.generated_database_autonomous_database.id}
ORACLE_ADB_REGION=${var.region}

OCI_REGION=${var.region}
OCI_COMPARTMENT_ID=${var.compartment_ocid}

MODEL_SETTINGS_FILE=/u01/production-ready-nl2sql/model-settings.json

UPLOAD_STORAGE_BACKEND=local
LOCAL_STORAGE_DIR=/u01/production-ready-nl2sql
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
EOT
}
