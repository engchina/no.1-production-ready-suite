locals {
  compute_subnet_prohibits_public_ip = coalesce(
    data.oci_core_subnet.selected_compute_subnet.prohibit_public_ip_on_vnic,
    false,
  )

  app_name        = "production-ready-agent"
  app_repo_dir    = "no.1-production-ready-suite/agent"
  data_dir_host   = "/u01/data/production-ready-agent"
  wallet_dir_host = "/u01/aipoc/wallet"

  # 3製品共通の設定（#211）。init_script.sh がリポジトリの platform/.env へ置く。
  # Agent は python-oracledb Thin mode + Wallet(mTLS) だけで接続する。
  platform_env = <<-EOT
PLATFORM_ORACLE_USER=${local.effective_oracle_user}
PLATFORM_ORACLE_PASSWORD=${local.effective_oracle_password}
PLATFORM_ORACLE_DSN=${local.effective_oracle_dsn}
PLATFORM_ORACLE_CLIENT_LIB_DIR=
PLATFORM_ORACLE_WALLET_DIR=${local.wallet_dir_host}
PLATFORM_ORACLE_WALLET_PASSWORD=${local.effective_oracle_wallet_password}
PLATFORM_ORACLE_ADB_OCID=${local.effective_adb_ocid}
PLATFORM_ORACLE_ADB_REGION=${var.region}

PLATFORM_OCI_REGION=${var.region}
PLATFORM_OCI_COMPARTMENT_ID=${var.compartment_ocid}

PLATFORM_MODEL_SETTINGS_FILE=${local.data_dir_host}/model-settings.json

PLATFORM_UPLOAD_STORAGE_BACKEND=local
PLATFORM_LOCAL_STORAGE_DIR=${local.data_dir_host}
PLATFORM_OBJECT_STORAGE_REGION=${var.region}
PLATFORM_OBJECT_STORAGE_NAMESPACE=
PLATFORM_OBJECT_STORAGE_BUCKET=
EOT

  # Agent 固有の設定（AGENT_*）。init_script.sh が backend/.env へ置く。
  # Runtime 状態の Oracle repository は起動時に自分の table を作成する（DDL は Terraform に持たない）。
  # gunicorn は 1 worker・dispatcher は in_process に固定する（checkpoint repository は process 内に状態を持つため）。
  backend_env = <<-EOT
AGENT_APP_VERSION=0.1.0
AGENT_LOG_LEVEL=INFO
AGENT_ENVIRONMENT=production
AGENT_SERVICE_NAME=${local.app_name}
AGENT_CORS_ORIGINS=["http://localhost","http://127.0.0.1"]

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
    platform_env        = base64gzip(local.platform_env)
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
