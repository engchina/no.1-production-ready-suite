locals {
  compute_subnet_prohibits_public_ip = coalesce(
    data.oci_core_subnet.selected_compute_subnet.prohibit_public_ip_on_vnic,
    false,
  )

  wallet_dir_host = "/u01/aipoc/wallet"

  # 配備する製品。compute.tf はここで選ばれた製品ごとに Compute を1台作る（for_each の key は bool だけから決める）。
  product_enabled = {
    rag    = var.deploy_rag
    nl2sql = var.deploy_nl2sql
    agent  = var.deploy_agent
  }
  selected_products = toset([for product, enabled in local.product_enabled : product if enabled])

  product_instances = {
    rag = {
      display_name     = var.rag_instance_display_name
      ocpus            = var.rag_instance_flex_shape_ocpus
      memory_in_gbs    = var.rag_instance_flex_shape_memory
      boot_volume_size = var.rag_instance_boot_volume_size
    }
    nl2sql = {
      display_name     = var.nl2sql_instance_display_name
      ocpus            = var.nl2sql_instance_flex_shape_ocpus
      memory_in_gbs    = var.nl2sql_instance_flex_shape_memory
      boot_volume_size = var.nl2sql_instance_boot_volume_size
    }
    agent = {
      display_name     = var.agent_instance_display_name
      ocpus            = var.agent_instance_flex_shape_ocpus
      memory_in_gbs    = var.agent_instance_flex_shape_memory
      boot_volume_size = var.agent_instance_boot_volume_size
    }
  }

  # ---------------------------------------------------------------- RAG

  # rag/docker-compose.yml のうち、RAG の Compute が build・起動する service。
  # CPU の parser だけを配備する（parser-unstructured は既定の解析方式のため常に起動）。
  # GPU の service（compose の gpu profile）はこの stack では扱わない。
  rag_compose_services = concat(
    [
      "backend",
      "ingestion-worker",
      "preprocess-office-to-pdf",
      "preprocess-pdf-to-page-images",
      "preprocess-csv-to-json",
      "preprocess-excel-to-json",
      "preprocess-url-to-markdown",
      "preprocess-image-enhance",
      "preprocess-pii-redact",
      "parser-unstructured",
      "pipeline-generation",
      "pipeline-retrieval",
    ],
    var.rag_enable_parser_docling ? ["parser-docling"] : [],
    var.rag_enable_parser_marker ? ["parser-marker"] : [],
    var.rag_enable_oci_cloud_parsers ? ["parser-oci-genai-vision", "parser-oci-document-understanding"] : [],
  )

  # rag/backend/.env（docker compose の env_file）。compose の env_file は `$` を展開するため、
  # 入力値は single quote で囲んで文字どおりに渡す（入力の validation で single quote を禁止している）。
  # ENVIRONMENT / LOCAL_STORAGE_DIR / MODEL_SETTINGS_FILE / OCI_CONFIG_FILE と service URL は
  # docker-compose.yml の environment が正本のため、ここには書かない。
  # AUTH_SESSION_SECRET / AUDIT_CONTEXT_HASH_SALT は instance 上で生成する（state に残さない）。
  # RAG は python-oracledb Thin mode + Wallet(mTLS) で接続する（ORACLE_CLIENT_LIB_DIR は空）。
  # ADB の DDL は Terraform に持たない。init_script.sh がアプリの system schema CLI で適用する。
  rag_backend_env = <<-EOT
APP_VERSION=0.1.0
LOG_LEVEL=INFO

AUTH_MODE=production
AUTH_USERNAME='${var.rag_app_login_user}'
AUTH_PASSWORD='${var.rag_app_login_password}'
AUTH_SESSION_SECRET=
AUTH_COOKIE_SECURE=${var.rag_app_auth_cookie_secure}
AUDIT_CONTEXT_HASH_SALT=

OCI_REGION=${var.region}
OCI_COMPARTMENT_ID=${var.compartment_ocid}

ORACLE_USER='${local.effective_oracle_user}'
ORACLE_PASSWORD='${local.effective_oracle_password}'
ORACLE_DSN='${local.effective_oracle_dsn}'
ORACLE_CLIENT_LIB_DIR=
ORACLE_WALLET_DIR=${local.wallet_dir_host}
ORACLE_WALLET_PASSWORD='${local.effective_oracle_wallet_password}'
ORACLE_ADB_OCID=${local.effective_adb_ocid}
ORACLE_ADB_REGION=${var.region}

UPLOAD_STORAGE_BACKEND=local
OBJECT_STORAGE_REGION=${var.region}

RAG_PARSER_ADAPTER_BACKEND=unstructured
RAG_PARSER_UNSTRUCTURED_ENABLED=true
RAG_SERVICE_CONTROL_ENABLED=false
EOT

  # ---------------------------------------------------------------- NL2SQL

  nl2sql_data_dir_host = "/u01/data/production-ready-nl2sql"
  nl2sql_oracle_connection_security = (
    var.adb_is_mtls_connection_required ? "wallet_mtls" : "walletless_tls"
  )

  nl2sql_backend_env = <<-EOT
APP_VERSION=0.1.0
LOG_LEVEL=INFO
ENVIRONMENT=${var.nl2sql_app_environment}
SERVICE_NAME=production-ready-nl2sql
CORS_ORIGINS=["http://localhost","http://127.0.0.1"]
ENABLE_METRICS=true
DEBUG=false

APP_AUTH_ENABLED=true
APP_ADMIN_LOGIN_USER_ID=${var.nl2sql_app_admin_login_user_id}
APP_ADMIN_LOGIN_USER_PASSWORD=${var.nl2sql_app_admin_login_user_password}
APP_AUTH_COOKIE_SECURE=${var.nl2sql_app_auth_cookie_secure}
APP_AUTH_SESSION_COOKIE_NAME=nl2sql_session
APP_AUTH_CSRF_COOKIE_NAME=nl2sql_csrf

ORACLE_USER=${local.effective_oracle_user}
ORACLE_PASSWORD=${local.effective_oracle_password}
ORACLE_DSN=${local.effective_oracle_dsn}
ORACLE_DRIVER_MODE=thin
ORACLE_CONNECTION_SECURITY=${local.nl2sql_oracle_connection_security}
ORACLE_CLIENT_LIB_DIR=
ORACLE_WALLET_DIR=${local.wallet_dir_host}
ORACLE_WALLET_PASSWORD=${local.effective_oracle_wallet_password}
ORACLE_DEEPSEC_ENABLED=${var.nl2sql_oracle_deepsec_enabled}
ORACLE_DEEPSEC_DATA_USER=DEEPSEC_DATA_USER
ORACLE_DEEPSEC_DATA_USER_PASSWORD=${var.nl2sql_oracle_deepsec_enabled ? var.nl2sql_oracle_deepsec_data_user_password : ""}
ORACLE_ADB_OCID=${local.effective_adb_ocid}
ORACLE_ADB_REGION=${var.region}

OCI_REGION=${var.region}
OCI_COMPARTMENT_ID=${var.compartment_ocid}

MODEL_SETTINGS_FILE=${local.nl2sql_data_dir_host}/model-settings.json

UPLOAD_STORAGE_BACKEND=local
LOCAL_STORAGE_DIR=${local.nl2sql_data_dir_host}
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
NL2SQL_SELECT_AI_REGION=us-chicago-1
EOT

  # ---------------------------------------------------------------- Agent

  # Agent は python-oracledb Thin mode + Wallet(mTLS) だけで接続する。
  # Runtime 状態の Oracle repository は起動時に自分の table を作成する（DDL は Terraform に持たない）。
  # gunicorn は 1 worker・dispatcher は in_process に固定する（checkpoint repository は process 内に状態を持つため）。
  agent_data_dir_host = "/u01/data/production-ready-agent"

  agent_backend_env = <<-EOT
APP_VERSION=0.1.0
LOG_LEVEL=INFO
ENVIRONMENT=production
SERVICE_NAME=production-ready-agent
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

MODEL_SETTINGS_FILE=${local.agent_data_dir_host}/model-settings.json

UPLOAD_STORAGE_BACKEND=local
LOCAL_STORAGE_DIR=${local.agent_data_dir_host}
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
AGENT_RUNTIME_BINDINGS_DIR=${local.agent_data_dir_host}/bindings
AGENT_ARTIFACT_STORAGE_PATH=${local.agent_data_dir_host}/artifacts
AGENT_RUNTIME_SERVICE_CONTROL_ENABLED=false
AGENT_CONTROL_PLANE_PUBLIC_BASE_URL=${trimspace(var.agent_control_plane_public_base_url)}
AGENT_CONTROL_PLANE_MCP_TOKEN_SECRET=${var.agent_control_plane_mcp_token_secret}
EOT

  # ---------------------------------------------------------------- cloud-init

  backend_envs = {
    rag    = local.rag_backend_env
    nl2sql = local.nl2sql_backend_env
    agent  = local.agent_backend_env
  }

  # 製品ごとの差分（backend/.env、RAG の compose service、Agent の Basic 認証）以外は全製品で同じ bootstrap を使う。
  # 使わない製品の値は空文字にする（テンプレートは製品で分岐して、その製品のファイルだけを書く）。
  cloud_init_user_data = {
    for product in local.selected_products : product => base64gzip(templatefile("${path.module}/cloud_init/bootstrap.template.yaml", {
      product             = product
      adb_name            = local.effective_adb_name
      adb_ocid            = local.effective_adb_ocid
      application_git_ref = var.application_git_ref
      application_git_url = var.application_git_url
      application_port    = tostring(var.application_port)
      backend_env         = base64gzip(local.backend_envs[product])
      basic_auth_password = product == "agent" ? base64gzip(var.agent_app_basic_auth_password) : ""
      basic_auth_user     = product == "agent" ? var.agent_app_basic_auth_user : ""
      compartment_ocid    = var.compartment_ocid
      compose_services    = product == "rag" ? join(" ", local.rag_compose_services) : ""
      db_dsn              = local.effective_oracle_dsn
      region              = var.region
      wallet_content      = data.external.wallet_files.result.wallet_content
      wallet_dir_host     = local.wallet_dir_host
    }))
  }
}
