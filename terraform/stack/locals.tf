locals {
  compute_subnet_prohibits_public_ip = coalesce(
    data.oci_core_subnet.selected_compute_subnet.prohibit_public_ip_on_vnic,
    false,
  )

  wallet_dir_host = "/u01/aipoc/wallet"

  # 配備する製品。すべて 1 台の Compute（compute.tf の oci_core_instance.suite）に入れる（#1316）。
  product_enabled = {
    rag    = var.deploy_rag
    nl2sql = var.deploy_nl2sql
    agent  = var.deploy_agent
  }
  # 並びは rag → nl2sql → agent（platform/deploy/suite-init.sh もこの順に配備する）。
  selected_products = [for product in ["rag", "nl2sql", "agent"] : product if local.product_enabled[product]]

  # ---------------------------------------------------------------- RAG

  # RAG の Compute に配備する前処理 / parser（サービスごとの uv の venv と systemd の unit。#286）。
  # backend と ingestion-worker は常に配備するため、この一覧には含めない。
  # CPU の parser だけを配備する（parser-docling は既定の解析エンジンのため常に配備する。
  # parser-unstructured は Docling が扱えない形式を取り込む場合だけ、入力で選んで配備する）。
  # GPU の parser（ASR）はこの stack では扱わない。
  rag_services = concat(
    [
      "preprocess-office-to-pdf",
      "preprocess-pdf-to-page-images",
      "preprocess-csv-to-json",
      "preprocess-excel-to-json",
      "preprocess-url-to-markdown",
      "preprocess-image-enhance",
      "preprocess-pii-redact",
      "parser-docling",
    ],
    var.rag_enable_parser_unstructured ? ["parser-unstructured"] : [],
    var.rag_enable_oci_cloud_parsers ? ["parser-oci-genai-vision", "parser-oci-document-understanding"] : [],
  )

  # rag/backend/.env（RAG_*）。backend が python-dotenv で読み、OCI parser の unit も EnvironmentFile で読む。
  # ログインは共通認証（構成管理者 system_admin と、ユーザー管理で作る DB ユーザー。#214）。
  # RAG_ENVIRONMENT は init_script.sh が書く systemd の unit の Environment が正本、service URL と
  # PLATFORM_LOCAL_STORAGE_DIR / PLATFORM_OCI_CONFIG_FILE は Settings の既定値（127.0.0.1:<port>・
  # /u01/data/production-ready-rag・実行ユーザーの ~/.oci/config）を使うため、ここには書かない。
  # サービス管理画面の起動 / 停止は、sudoers で許可した unit の systemctl だけを実行する（#286）。
  # RAG_AUDIT_CONTEXT_HASH_SALT は instance 上で生成する（state に残さない）。
  # ADB の DDL は Terraform に持たない。init_script.sh がアプリの system schema CLI で適用する。
  rag_backend_env = <<-EOT
RAG_APP_VERSION=0.1.0
RAG_LOG_LEVEL=INFO

RAG_AUTH_MODE=production
RAG_AUDIT_CONTEXT_HASH_SALT=

RAG_PARSER_ADAPTER_BACKEND=docling
RAG_PARSER_DOCLING_ENABLED=true
RAG_SERVICE_CONTROL_ENABLED=true
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
  # 共通認証・Agent の権限・Run の保存先・定義の table は、init_script.sh が agent_system_schema --initialize で
  # 作成する（DDL は Terraform に持たない。#751 / #764）。DB は共通の PLATFORM_ORACLE_*（platform/.env）で接続する。
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
AGENT_ARTIFACT_STORAGE_PATH=${local.agent_data_dir_host}/artifacts

AGENT_EXTERNAL_RAG_MCP_URL=${local.product_mcp_urls["rag"]}
AGENT_EXTERNAL_NL2SQL_MCP_URL=${local.product_mcp_urls["nl2sql"]}
EOT

  # Agent が RAG / NL2SQL の MCP（POST /api/mcp）を呼ぶ URL（#233）。配備した製品だけ。
  # 同じ Compute の backend（127.0.0.1。Nginx を通さない）を直接呼ぶ（#1316）。port は各製品の init_script.sh の
  # BACKEND_PORT と platform/deploy/suite-nginx.sh と同じ（RAG 8000・NL2SQL 8010）。
  # 認証は呼び出しごとのサービストークン（共通 .env の PLATFORM_SERVICE_TOKEN_SECRET）。
  product_mcp_urls = {
    rag    = var.deploy_rag ? "http://127.0.0.1:8000/api/mcp" : ""
    nl2sql = var.deploy_nl2sql ? "http://127.0.0.1:8010/api/mcp" : ""
  }

  # ---------------------------------------------------------------- 共通 .env（platform/.env。#211）

  # 3製品共通の設定（PLATFORM_*）。Compute のリポジトリの platform/.env に置き、3 製品が同じファイルを読む
  # （既にあれば init_script.sh は上書きしない。画面で保存した値を消さないため）。
  # 値は python-dotenv が読む。single quote の中は展開されないため、入力値は single quote で囲む。
  # python-oracledb Thin mode + Wallet(mTLS) で接続する（PLATFORM_ORACLE_CLIENT_LIB_DIR は空）。
  # データの置き場所（PLATFORM_LOCAL_STORAGE_DIR）と model-settings.json は書かず、各製品の Settings の既定値を使う
  # （データは /u01/data/production-ready-<製品>、model-settings.json は platform/.env と同じ場所で 3 製品が共有）。
  # Cookie は HTTPS が on なら Secure にする（3 製品で同じ。#1316）。
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
PLATFORM_OBJECT_STORAGE_NAMESPACE=

PLATFORM_ADMIN_LOGIN_USER_ID=${var.app_admin_login_user_id}
PLATFORM_ADMIN_LOGIN_USER_PASSWORD=${var.app_admin_login_user_password}

PLATFORM_SERVICE_TOKEN_SECRET=${random_password.service_token_secret.result}

PLATFORM_AUTH_COOKIE_SECURE=${var.https_enabled}
EOT

  # ---------------------------------------------------------------- cloud-init

  # 製品ごとの backend/.env（配備する製品だけ。/u01/aipoc/props/<製品>.backend.env）。
  backend_envs = {
    rag    = local.rag_backend_env
    nl2sql = local.nl2sql_backend_env
    agent  = local.agent_backend_env
  }

  cloud_init_user_data = base64gzip(templatefile("${path.module}/cloud_init/bootstrap.template.yaml", {
    adb_name            = local.effective_adb_name
    adb_ocid            = local.effective_adb_ocid
    application_git_ref = var.application_git_ref
    application_git_url = var.application_git_url
    assign_public_ip    = tostring(!local.compute_subnet_prohibits_public_ip)
    backend_envs        = { for product in local.selected_products : product => base64gzip(local.backend_envs[product]) }
    compartment_ocid    = var.compartment_ocid
    db_dsn              = local.effective_oracle_dsn
    https_enabled       = tostring(var.https_enabled)
    platform_env        = base64gzip(local.platform_env)
    products            = join(" ", local.selected_products)
    rag_services        = var.deploy_rag ? join(" ", local.rag_services) : ""
    region              = var.region
    wallet_content      = data.external.wallet_files.result.wallet_content
    wallet_dir_host     = local.wallet_dir_host
  }))
}
