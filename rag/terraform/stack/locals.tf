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

  # rag/backend/.env（docker compose の env_file）。compose の env_file は `$` を展開するため、
  # 入力値は single quote で囲んで文字どおりに渡す（入力の validation で single quote を禁止している）。
  # ENVIRONMENT / LOCAL_STORAGE_DIR / MODEL_SETTINGS_FILE / OCI_CONFIG_FILE と service URL は
  # docker-compose.yml の environment が正本のため、ここには書かない。
  # AUTH_SESSION_SECRET / AUDIT_CONTEXT_HASH_SALT は instance 上で生成する（state に残さない）。
  # RAG は python-oracledb Thin mode + Wallet(mTLS) で接続する（ORACLE_CLIENT_LIB_DIR は空）。
  # ADB の DDL は Terraform に持たない。init_script.sh がアプリの system schema CLI で適用する。
  backend_env = <<-EOT
APP_VERSION=0.1.0
LOG_LEVEL=INFO

AUTH_MODE=production
AUTH_USERNAME='${var.app_login_user}'
AUTH_PASSWORD='${var.app_login_password}'
AUTH_SESSION_SECRET=
AUTH_COOKIE_SECURE=${var.app_auth_cookie_secure}
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
    region              = var.region
    wallet_content      = data.external.wallet_files.result.wallet_content
    wallet_dir_host     = local.wallet_dir_host
  })

  cloud_init_user_data = base64gzip(local.cloud_init_rendered)
}
