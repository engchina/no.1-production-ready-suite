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
  # Agent の backend/.env は RAG / NL2SQL の Compute の private IP を使う（#233）。同じ resource の中で
  # 互いを参照できないため、Agent の Compute は compute.tf の別の resource で作る。
  non_agent_products = toset([for product in local.selected_products : product if product != "agent"])
  agent_products     = toset([for product in local.selected_products : product if product == "agent"])

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
    ],
    var.rag_enable_parser_docling ? ["parser-docling"] : [],
    var.rag_enable_oci_cloud_parsers ? ["parser-oci-genai-vision", "parser-oci-document-understanding"] : [],
  )

  # rag/backend/.env（RAG_*。docker compose の env_file）。
  # ログインは共通認証（構成管理者 system_admin と、ユーザー管理で作る DB ユーザー。#214）。
  # RAG_ENVIRONMENT と service URL、PLATFORM_LOCAL_STORAGE_DIR / PLATFORM_OCI_CONFIG_FILE は
  # docker-compose.yml の environment が正本のため、ここには書かない。
  # RAG_AUDIT_CONTEXT_HASH_SALT は instance 上で生成する（state に残さない）。
  # ADB の DDL は Terraform に持たない。init_script.sh がアプリの system schema CLI で適用する。
  rag_backend_env = <<-EOT
RAG_APP_VERSION=0.1.0
RAG_LOG_LEVEL=INFO

RAG_AUTH_MODE=production
RAG_AUDIT_CONTEXT_HASH_SALT=

RAG_PARSER_ADAPTER_BACKEND=unstructured
RAG_PARSER_UNSTRUCTURED_ENABLED=true
RAG_SERVICE_CONTROL_ENABLED=false
EOT

  # ---------------------------------------------------------------- NL2SQL

  nl2sql_data_dir_host = "/u01/data/production-ready-nl2sql"
  nl2sql_oracle_connection_security = (
    var.adb_is_mtls_connection_required ? "wallet_mtls" : "walletless_tls"
  )

  # nl2sql/backend/.env（NL2SQL_*）。3製品共通の設定は platform_envs（platform/.env）に置く（#211）。
  nl2sql_backend_env = <<-EOT
NL2SQL_APP_VERSION=0.1.0
NL2SQL_LOG_LEVEL=INFO
NL2SQL_ENVIRONMENT=${var.nl2sql_app_environment}
NL2SQL_SERVICE_NAME=production-ready-nl2sql
NL2SQL_CORS_ORIGINS=["http://localhost","http://127.0.0.1"]
NL2SQL_ENABLE_METRICS=true
NL2SQL_DEBUG=false

NL2SQL_APP_AUTH_ENABLED=true
NL2SQL_APP_AUTH_SESSION_COOKIE_NAME=nl2sql_session
NL2SQL_APP_AUTH_CSRF_COOKIE_NAME=nl2sql_csrf

NL2SQL_ORACLE_DEEPSEC_ENABLED=${var.nl2sql_oracle_deepsec_enabled}
NL2SQL_ORACLE_DEEPSEC_DATA_USER=DEEPSEC_DATA_USER
NL2SQL_ORACLE_DEEPSEC_DATA_USER_PASSWORD=${var.nl2sql_oracle_deepsec_enabled ? var.nl2sql_oracle_deepsec_data_user_password : ""}

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
  # Runtime 状態の Oracle repository は起動時に自分の table を作成し、共通認証と Agent の権限の table は
  # init_script.sh が agent_security_migrate で作成する（DDL は Terraform に持たない）。
  # ログインは共通認証（構成管理者 system_admin と、ユーザー管理で作る DB ユーザー。#215）。
  # gunicorn は 1 worker・dispatcher は in_process に固定する（checkpoint repository は process 内に状態を持つため）。
  agent_data_dir_host = "/u01/data/production-ready-agent"

  # agent/backend/.env（AGENT_*）。3製品共通の設定は platform_envs（platform/.env）に置く（#211）。
  agent_backend_env = <<-EOT
AGENT_APP_VERSION=0.1.0
AGENT_LOG_LEVEL=INFO
AGENT_ENVIRONMENT=production
AGENT_SERVICE_NAME=production-ready-agent
AGENT_CORS_ORIGINS=["http://localhost","http://127.0.0.1"]

AGENT_AUTH_MODE=production

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

AGENT_EXTERNAL_RAG_MCP_URL=${lookup(local.product_mcp_urls, "rag", "")}
AGENT_EXTERNAL_NL2SQL_MCP_URL=${lookup(local.product_mcp_urls, "nl2sql", "")}
EOT

  # Agent が RAG / NL2SQL の MCP（POST /api/mcp）を呼ぶ URL（#233）。配備した製品だけ。
  # 同じ subnet の private IP の Nginx（/api/ を backend へ proxy）へ送る。通信は subnet の security list（stack の外）で許可する（#259）。
  # 認証は呼び出しごとのサービストークン（共通 .env の PLATFORM_SERVICE_TOKEN_SECRET）。
  application_port_suffix = var.application_port == 80 ? "" : ":${var.application_port}"
  product_mcp_urls = {
    for product, instance in oci_core_instance.product :
    product => "http://${instance.private_ip}${local.application_port_suffix}/api/mcp"
  }

  # ---------------------------------------------------------------- 共通 .env（platform/.env。#211）

  # 3製品共通の設定（PLATFORM_*）。各製品の Compute でリポジトリの platform/.env に置く
  # （既にあれば init_script.sh は上書きしない。画面で保存した値を消さないため）。
  # 値は python-dotenv が読む。single quote の中は展開されないため、入力値は single quote で囲む。
  # python-oracledb Thin mode + Wallet(mTLS) で接続する（PLATFORM_ORACLE_CLIENT_LIB_DIR は空）。
  platform_env = <<-EOT
PLATFORM_OCI_REGION=${var.region}
PLATFORM_OCI_COMPARTMENT_ID=${var.compartment_ocid}

PLATFORM_ORACLE_USER='${local.effective_oracle_user}'
PLATFORM_ORACLE_PASSWORD='${local.effective_oracle_password}'
PLATFORM_ORACLE_DSN='${local.effective_oracle_dsn}'
PLATFORM_ORACLE_DRIVER_MODE=thin
PLATFORM_ORACLE_CONNECTION_SECURITY=${local.nl2sql_oracle_connection_security}
PLATFORM_ORACLE_CLIENT_LIB_DIR=
PLATFORM_ORACLE_WALLET_DIR=${local.wallet_dir_host}
PLATFORM_ORACLE_WALLET_PASSWORD='${local.effective_oracle_wallet_password}'
PLATFORM_ORACLE_ADB_OCID=${local.effective_adb_ocid}
PLATFORM_ORACLE_ADB_REGION=${var.region}

PLATFORM_UPLOAD_STORAGE_BACKEND=local
PLATFORM_OBJECT_STORAGE_REGION=${var.region}
PLATFORM_OBJECT_STORAGE_BUCKET=production-ready

PLATFORM_ADMIN_LOGIN_USER_ID=${var.app_admin_login_user_id}
PLATFORM_ADMIN_LOGIN_USER_PASSWORD=${var.app_admin_login_user_password}

PLATFORM_SERVICE_TOKEN_SECRET=${random_password.service_token_secret.result}
EOT

  # 製品ごとの Compute に置く共通 .env の差分（データの置き場所と、Cookie を HTTPS 限定にするか）。
  # RAG の保存先と model-settings.json は docker-compose.yml が正本（PLATFORM_LOCAL_STORAGE_DIR）。
  platform_env_product = {
    rag    = <<-EOT

PLATFORM_AUTH_COOKIE_SECURE=${var.rag_app_auth_cookie_secure}
EOT
    nl2sql = <<-EOT

PLATFORM_AUTH_COOKIE_SECURE=${var.nl2sql_app_auth_cookie_secure}

PLATFORM_MODEL_SETTINGS_FILE=${local.nl2sql_data_dir_host}/model-settings.json
PLATFORM_LOCAL_STORAGE_DIR=${local.nl2sql_data_dir_host}
PLATFORM_OBJECT_STORAGE_NAMESPACE=
EOT
    agent  = <<-EOT

PLATFORM_AUTH_COOKIE_SECURE=${var.agent_app_auth_cookie_secure}

PLATFORM_MODEL_SETTINGS_FILE=${local.agent_data_dir_host}/model-settings.json
PLATFORM_LOCAL_STORAGE_DIR=${local.agent_data_dir_host}
PLATFORM_OBJECT_STORAGE_NAMESPACE=
EOT
  }

  platform_envs = {
    for product, extra in local.platform_env_product : product => "${local.platform_env}${extra}"
  }

  # ---------------------------------------------------------------- cloud-init

  # Agent の backend/.env は含めない（RAG / NL2SQL の Compute を参照するため。agent_cloud_init_user_data）。
  backend_envs = {
    rag    = local.rag_backend_env
    nl2sql = local.nl2sql_backend_env
  }

  # 製品ごとの差分（backend/.env、RAG の compose service）以外は全製品で同じ bootstrap を使う。
  # 使わない製品の値は空文字にする（テンプレートは製品で分岐して、その製品のファイルだけを書く）。
  cloud_init_common_vars = {
    adb_name            = local.effective_adb_name
    adb_ocid            = local.effective_adb_ocid
    application_git_ref = var.application_git_ref
    application_git_url = var.application_git_url
    application_port    = tostring(var.application_port)
    compartment_ocid    = var.compartment_ocid
    db_dsn              = local.effective_oracle_dsn
    region              = var.region
    wallet_content      = data.external.wallet_files.result.wallet_content
    wallet_dir_host     = local.wallet_dir_host
  }
  cloud_init_user_data = {
    for product in local.non_agent_products : product => base64gzip(templatefile("${path.module}/cloud_init/bootstrap.template.yaml", merge(local.cloud_init_common_vars, {
      product          = product
      backend_env      = base64gzip(local.backend_envs[product])
      platform_env     = base64gzip(local.platform_envs[product])
      compose_services = product == "rag" ? join(" ", local.rag_compose_services) : ""
    })))
  }
  # Agent は RAG / NL2SQL の private IP を backend/.env に書くため、それらの Compute の後に作る（別の local）。
  agent_cloud_init_user_data = base64gzip(templatefile("${path.module}/cloud_init/bootstrap.template.yaml", merge(local.cloud_init_common_vars, {
    product          = "agent"
    backend_env      = base64gzip(local.agent_backend_env)
    platform_env     = base64gzip(local.platform_envs["agent"])
    compose_services = ""
  })))
}
