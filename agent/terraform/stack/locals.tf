locals {
  compute_subnet_prohibits_public_ip = coalesce(
    data.oci_core_subnet.selected_compute_subnet.prohibit_public_ip_on_vnic,
    false,
  )

  app_name        = "production-ready-agent"
  app_repo_dir    = "no.1-production-ready-suite/agent"
  data_dir_host   = "/u01/data/production-ready-agent"
  wallet_dir_host = "/u01/aipoc/wallet"

  # Agent は python-oracledb Thin mode + Wallet(mTLS) だけで接続する。
  # Runtime 状態の Oracle repository は起動時に自分の table を作成する（DDL は Terraform に持たない）。
  # gunicorn は 1 worker・dispatcher は in_process に固定する（checkpoint repository は process 内に状態を持つため）。
  backend_env = <<-EOT
APP_VERSION=0.1.0
LOG_LEVEL=INFO
ENVIRONMENT=production
SERVICE_NAME=${local.app_name}
CORS_ORIGINS=["http://localhost","http://127.0.0.1"]

ORACLE_USER=${local.effective_oracle_user}
ORACLE_PASSWORD=${local.effective_oracle_password}
ORACLE_DSN=${local.effective_oracle_dsn}
ORACLE_CLIENT_LIB_DIR=
ORACLE_WALLET_DIR=${local.wallet_dir_host}
ORACLE_WALLET_PASSWORD=${local.effective_oracle_wallet_password}
ORACLE_ADB_OCID=${local.effective_adb_ocid}
ORACLE_ADB_REGION=${var.region}

OCI_REGION=${var.region}
OCI_COMPARTMENT_ID=${var.compartment_ocid}

MODEL_SETTINGS_FILE=${local.data_dir_host}/model-settings.json

UPLOAD_STORAGE_BACKEND=local
LOCAL_STORAGE_DIR=${local.data_dir_host}
OBJECT_STORAGE_REGION=${var.region}
OBJECT_STORAGE_NAMESPACE=
OBJECT_STORAGE_BUCKET=

AGENT_RUNTIME_REPOSITORY_BACKEND=${var.agent_runtime_repository_backend}
AGENT_RUNTIME_DISPATCH_MODE=in_process
AGENT_RUNTIME_ORACLE_DSN=${local.effective_oracle_dsn}
AGENT_RUNTIME_ORACLE_USER=${local.effective_oracle_user}
AGENT_RUNTIME_ORACLE_PASSWORD=${local.effective_oracle_password}
AGENT_RUNTIME_ORACLE_WALLET_DIR=${local.wallet_dir_host}
AGENT_RUNTIME_ORACLE_WALLET_PASSWORD=${local.effective_oracle_wallet_password}
AGENT_RUNTIME_ORACLE_CREATE_SCHEMA=true
AGENT_RUNTIME_BINDINGS_DIR=${local.data_dir_host}/bindings
AGENT_ARTIFACT_STORAGE_PATH=${local.data_dir_host}/artifacts
AGENT_RUNTIME_SERVICE_CONTROL_ENABLED=false
AGENT_CONTROL_PLANE_PUBLIC_BASE_URL=${trimspace(var.agent_control_plane_public_base_url)}
AGENT_CONTROL_PLANE_MCP_TOKEN_SECRET=${var.agent_control_plane_mcp_token_secret}
EOT

  cloud_init_rendered = templatefile("${path.module}/cloud_init/bootstrap.template.yaml", {
    adb_name            = local.effective_adb_name
    adb_ocid            = local.effective_adb_ocid
    application_git_ref = var.application_git_ref
    application_git_url = var.application_git_url
    application_port    = tostring(var.application_port)
    backend_env         = base64gzip(local.backend_env)
    basic_auth_password = base64gzip(var.app_basic_auth_password)
    basic_auth_user     = var.app_basic_auth_user
    compartment_ocid    = var.compartment_ocid
    db_dsn              = local.effective_oracle_dsn
    region              = var.region
    wallet_content      = data.external.wallet_files.result.wallet_content
    wallet_dir_host     = local.wallet_dir_host
  })

  cloud_init_user_data = base64gzip(local.cloud_init_rendered)
}
