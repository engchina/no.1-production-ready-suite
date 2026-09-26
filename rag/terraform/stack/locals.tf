locals {
  compute_subnet_prohibits_public_ip = coalesce(
    data.oci_core_subnet.selected_compute_subnet.prohibit_public_ip_on_vnic,
    false,
  )

  app_name        = "production-ready-rag"
  app_repo_dir    = "no.1-production-ready-suite/rag"
  wallet_dir_host = "/u01/aipoc/wallet"

  # rag/docker-compose.yml のうち、この stack が build・起動する service。
  # CPU の parser だけを配備する（parser-unstructured は既定の解析方式のため常に起動）。
  # GPU の service（compose の gpu profile）はこの stack では扱わない。
  compose_services = concat(
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
    var.enable_parser_docling ? ["parser-docling"] : [],
    var.enable_parser_marker ? ["parser-marker"] : [],
    var.enable_oci_cloud_parsers ? ["parser-oci-genai-vision", "parser-oci-document-understanding"] : [],
  )

  # rag/backend/.env（docker compose の env_file）は RAG 固有の設定（RAG_*）だけを持つ。
  # 3製品共通の設定（PLATFORM_*）は platform_env（リポジトリの platform/.env）に分ける（#211）。
  # compose の env_file は `$` を展開するため、入力値は single quote で囲んで文字どおりに渡す
  # （入力の validation で single quote を禁止している）。python-dotenv も single quote の中を展開しない。
  # RAG_ENVIRONMENT / PLATFORM_LOCAL_STORAGE_DIR / PLATFORM_OCI_CONFIG_FILE / PLATFORM_ENV_FILE と
  # service URL は docker-compose.yml の environment が正本のため、ここには書かない。
  # RAG_AUTH_SESSION_SECRET / RAG_AUDIT_CONTEXT_HASH_SALT は instance 上で生成する（state に残さない）。
  # ADB の DDL は Terraform に持たない。init_script.sh がアプリの system schema CLI で適用する。
  backend_env = <<-EOT
RAG_APP_VERSION=0.1.0
RAG_LOG_LEVEL=INFO

RAG_AUTH_MODE=production
RAG_AUTH_USERNAME='${var.app_login_user}'
RAG_AUTH_PASSWORD='${var.app_login_password}'
RAG_AUTH_SESSION_SECRET=
RAG_AUTH_COOKIE_SECURE=${var.app_auth_cookie_secure}
RAG_AUDIT_CONTEXT_HASH_SALT=

RAG_PARSER_ADAPTER_BACKEND=unstructured
RAG_PARSER_UNSTRUCTURED_ENABLED=true
RAG_SERVICE_CONTROL_ENABLED=false
EOT

  # platform/.env（3製品共通の設定）。backend / ingestion-worker は PLATFORM_ENV_FILE で読み書きする。
  # RAG は python-oracledb Thin mode + Wallet(mTLS) で接続する（PLATFORM_ORACLE_CLIENT_LIB_DIR は空）。
  platform_env = <<-EOT
PLATFORM_OCI_REGION=${var.region}
PLATFORM_OCI_COMPARTMENT_ID=${var.compartment_ocid}

PLATFORM_ORACLE_USER='${local.effective_oracle_user}'
PLATFORM_ORACLE_PASSWORD='${local.effective_oracle_password}'
PLATFORM_ORACLE_DSN='${local.effective_oracle_dsn}'
PLATFORM_ORACLE_CLIENT_LIB_DIR=
PLATFORM_ORACLE_WALLET_DIR=${local.wallet_dir_host}
PLATFORM_ORACLE_WALLET_PASSWORD='${local.effective_oracle_wallet_password}'
PLATFORM_ORACLE_ADB_OCID=${local.effective_adb_ocid}
PLATFORM_ORACLE_ADB_REGION=${var.region}

PLATFORM_UPLOAD_STORAGE_BACKEND=local
PLATFORM_OBJECT_STORAGE_REGION=${var.region}
EOT

  cloud_init_rendered = templatefile("${path.module}/cloud_init/bootstrap.template.yaml", {
    adb_name            = local.effective_adb_name
    adb_ocid            = local.effective_adb_ocid
    application_git_ref = var.application_git_ref
    application_git_url = var.application_git_url
    application_port    = tostring(var.application_port)
    backend_env         = base64gzip(local.backend_env)
    compartment_ocid    = var.compartment_ocid
    compose_services    = join(" ", local.compose_services)
    db_dsn              = local.effective_oracle_dsn
    platform_env        = base64gzip(local.platform_env)
    region              = var.region
    wallet_content      = data.external.wallet_files.result.wallet_content
    wallet_dir_host     = local.wallet_dir_host
  })

  cloud_init_user_data = base64gzip(local.cloud_init_rendered)
}
