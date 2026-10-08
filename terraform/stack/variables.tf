variable "region" {
  description = "OCI region used by Resource Manager and runtime clients."
  type        = string
  default     = "ap-osaka-1"

  # Compute の image（schema.yaml の instance_image_source_id）を用意しているリージョンだけを許す（#660）。
  validation {
    condition     = contains(["ap-tokyo-1", "ap-osaka-1"], var.region)
    error_message = "region must be ap-tokyo-1 or ap-osaka-1. Create the stack in one of these regions."
  }
}

variable "availability_domain" {
  description = "Availability domain for the Compute instance."
  type        = string
  default     = ""
}

variable "compartment_ocid" {
  description = "OCI compartment OCID for the Compute instances of the selected products."
  type        = string
  default     = ""
}

variable "adb_compartment_ocid" {
  description = "OCI compartment OCID used to create or select the Autonomous AI Database."
  type        = string
  default     = ""

  validation {
    condition = (
      trimspace(var.adb_compartment_ocid) != ""
      && can(regex("^ocid1\\.compartment\\.", trimspace(var.adb_compartment_ocid)))
    )
    error_message = "ADBのコンパートメントを選択し、有効なCompartment OCIDを指定してください。"
  }
}

variable "vcn_ai_vcn_id" {
  description = "VCN OCID used by the Resource Manager form for subnet filtering."
  type        = string
  default     = ""
}

variable "adb_deployment_mode" {
  description = "Whether to create a new Autonomous AI Database or connect to an existing one."
  type        = string
  default     = "新規 Autonomous AI Database の作成"

  validation {
    condition = contains([
      "CREATE_NEW",
      "USE_EXISTING",
      "新規 Autonomous Database の作成",
      "既存の Autonomous Database を選択",
      "新規 Autonomous AI Database の作成",
      "既存の Autonomous AI Database を選択"
    ], var.adb_deployment_mode)
    error_message = "adb_deployment_mode must be 新規 Autonomous AI Database の作成, 既存の Autonomous AI Database を選択, CREATE_NEW, or USE_EXISTING."
  }
}

variable "existing_adb_ocid" {
  description = "Existing Autonomous AI Database OCID used when adb_deployment_mode selects an existing database."
  type        = string
  default     = ""

  validation {
    condition = (
      var.existing_adb_ocid == ""
      || can(regex("^ocid1\\.autonomousdatabase\\.", var.existing_adb_ocid))
    )
    error_message = "existing_adb_ocid must be an Autonomous AI Database OCID."
  }
}

variable "existing_oracle_user" {
  description = "Oracle database username for an existing Autonomous AI Database."
  type        = string
  default     = "ADMIN"

  validation {
    condition     = !can(regex("[\r\n']", var.existing_oracle_user))
    error_message = "existing_oracle_user must not contain single quotes or line breaks."
  }
}

variable "existing_oracle_password" {
  description = "Oracle database password for an existing Autonomous AI Database."
  type        = string
  sensitive   = true
  default     = ""

  validation {
    condition     = !can(regex("[\r\n']", var.existing_oracle_password))
    error_message = "existing_oracle_password must not contain single quotes or line breaks."
  }
}

variable "existing_oracle_dsn" {
  description = "Optional Oracle DSN for an existing Autonomous AI Database. Leave blank to use lower(db_name)_high from the selected ADB."
  type        = string
  default     = ""

  validation {
    condition     = !can(regex("[\r\n']", var.existing_oracle_dsn))
    error_message = "existing_oracle_dsn must not contain single quotes or line breaks."
  }
}

variable "existing_oracle_wallet_password" {
  description = "Deprecated compatibility input. Existing ADB wallet generation reuses existing_oracle_password."
  type        = string
  sensitive   = true
  default     = ""

  validation {
    condition     = !can(regex("[\r\n]", var.existing_oracle_wallet_password))
    error_message = "existing_oracle_wallet_password must not contain line breaks."
  }
}

variable "adb_display_name" {
  description = "Autonomous AI Database display name. Leave blank to use adb_name."
  type        = string
  default     = ""
}

variable "adb_name" {
  description = "Autonomous AI Database database name."
  type        = string
  default     = "SUITEADB"

  validation {
    condition     = can(regex("^[A-Za-z][A-Za-z0-9]{0,13}$", var.adb_name))
    error_message = "adb_name must start with a letter and contain only letters and digits, up to 14 characters."
  }
}

variable "adb_password" {
  description = "Autonomous AI Database ADMIN password."
  type        = string
  sensitive   = true
  default     = ""

  validation {
    condition     = !can(regex("[\r\n']", var.adb_password))
    error_message = "adb_password must not contain single quotes or line breaks."
  }
}

variable "adb_workload" {
  description = "Autonomous AI Database workload type. RAG ingestion and Agent Runtime checkpoints write continuously, so OLTP is the default."
  type        = string
  default     = "OLTP"

  validation {
    condition     = contains(["OLTP", "AJD", "APEX", "LH"], var.adb_workload)
    error_message = "adb_workload must be one of OLTP, AJD, APEX, or LH."
  }
}

variable "adb_db_version" {
  description = "Autonomous AI Database version."
  type        = string
  default     = "26ai"

  validation {
    condition     = contains(["19c", "23ai", "26ai"], var.adb_db_version)
    error_message = "adb_db_version must be one of 19c, 23ai, or 26ai."
  }
}

variable "adb_compute_model" {
  description = "Autonomous AI Database compute model."
  type        = string
  default     = "ECPU"

  validation {
    condition     = contains(["ECPU", "OCPU"], var.adb_compute_model)
    error_message = "adb_compute_model must be ECPU or OCPU."
  }
}

variable "adb_compute_count" {
  description = "Autonomous AI Database compute count."
  type        = number
  default     = 2

  validation {
    condition     = var.adb_compute_count > 0
    error_message = "adb_compute_count must be greater than 0."
  }
}

variable "adb_is_auto_scaling_enabled" {
  description = "Enable Autonomous AI Database compute auto scaling."
  type        = bool
  default     = false
}

variable "adb_data_storage_size_in_tbs" {
  description = "Autonomous AI Database storage size in TB."
  type        = number
  default     = 1

  validation {
    condition     = var.adb_data_storage_size_in_tbs > 0
    error_message = "adb_data_storage_size_in_tbs must be greater than 0."
  }
}

variable "adb_is_auto_scaling_for_storage_enabled" {
  description = "Enable Autonomous AI Database storage auto scaling."
  type        = bool
  default     = false
}

variable "adb_is_elastic_pool_enabled" {
  description = "Enable Autonomous AI Database elastic pool configuration."
  type        = bool
  default     = false
}

variable "adb_resource_pool_size" {
  description = "Autonomous AI Database elastic pool size. Used when adb_is_elastic_pool_enabled is true."
  type        = number
  default     = 0

  validation {
    condition     = var.adb_resource_pool_size >= 0
    error_message = "adb_resource_pool_size must be 0 or greater."
  }
}

variable "adb_resource_pool_storage_size_in_tbs" {
  description = "Autonomous AI Database elastic pool storage size in TB. Used when adb_is_elastic_pool_enabled is true."
  type        = number
  default     = 0

  validation {
    condition     = var.adb_resource_pool_storage_size_in_tbs >= 0
    error_message = "adb_resource_pool_storage_size_in_tbs must be 0 or greater."
  }
}

variable "license_model" {
  description = "Autonomous AI Database license model."
  type        = string
  default     = "LICENSE_INCLUDED"

  validation {
    condition     = contains(["BRING_YOUR_OWN_LICENSE", "LICENSE_INCLUDED"], var.license_model)
    error_message = "license_model must be BRING_YOUR_OWN_LICENSE or LICENSE_INCLUDED."
  }
}

variable "adb_backup_retention_period_in_days" {
  description = "Autonomous AI Database automatic backup retention period in days."
  type        = number
  default     = 1

  validation {
    condition     = var.adb_backup_retention_period_in_days >= 1
    error_message = "adb_backup_retention_period_in_days must be greater than or equal to 1."
  }
}

variable "adb_network_access_type" {
  description = "Autonomous AI Database network access mode."
  type        = string
  default     = "プライベート・エンドポイント・アクセスのみ"

  validation {
    condition = contains([
      "すべての場所からのセキュア・アクセス",
      "許可されたIPおよびVCN限定のセキュア・アクセス",
      "プライベート・エンドポイント・アクセスのみ"
    ], var.adb_network_access_type)
    error_message = "adb_network_access_typeにはAutonomous AI Database画面に準拠した日本語のアクセス・タイプを指定してください。"
  }
}

variable "adb_private_endpoint_vcn_compartment_id" {
  description = "Compartment OCID containing the VCN used by the Autonomous AI Database private endpoint."
  type        = string
  default     = ""
}

variable "adb_private_endpoint_vcn_id" {
  description = "VCN OCID used by the Autonomous AI Database private endpoint."
  type        = string
  default     = ""
}

variable "adb_private_endpoint_subnet_compartment_id" {
  description = "Compartment OCID containing the subnet used by the Autonomous AI Database private endpoint."
  type        = string
  default     = ""
}

variable "adb_subnet_id" {
  description = "Private subnet OCID for Autonomous AI Database private endpoint access."
  type        = string
  default     = ""
}

variable "adb_acl_notation_type" {
  description = "Access-control notation for secure access from allowed IPs and VCNs."
  type        = string
  default     = "VCN"

  validation {
    condition     = contains(["VCN", "IPアドレスまたはCIDRブロック"], var.adb_acl_notation_type)
    error_message = "adb_acl_notation_typeにはVCNまたはIPアドレスまたはCIDRブロックを指定してください。"
  }
}

variable "adb_acl_vcn_compartment_id" {
  description = "Compartment OCID containing the VCN allowed by the Autonomous AI Database access-control list."
  type        = string
  default     = ""
}

variable "adb_acl_vcn_id" {
  description = "VCN OCID allowed to access Autonomous AI Database when secure ACL mode is selected."
  type        = string
  default     = ""
}

variable "adb_acl_subnet_compartment_id" {
  description = "Compartment OCID containing the optional subnet allowed by the Autonomous AI Database access-control list."
  type        = string
  default     = ""
}

variable "adb_acl_subnet_id" {
  description = "Optional subnet OCID used to derive the CIDR entry for ADB VCN ACL mode."
  type        = string
  default     = ""
}

variable "adb_acl_cidr_blocks" {
  description = "Comma-separated CIDR blocks allowed to access Autonomous AI Database when CIDR ACL mode is selected."
  type        = string
  default     = ""
}

variable "adb_is_mtls_connection_required" {
  description = "Require mutual TLS (mTLS) connections for Autonomous AI Database."
  type        = bool
  default     = true
}

variable "instance_display_name" {
  description = "Display name of the Compute instance that runs every selected product."
  type        = string
  default     = "PRODUCTION_READY_SUITE"
}

variable "instance_shape" {
  description = "Shape of the Compute instance that runs every selected product."
  type        = string
  default     = "VM.Standard.E5.Flex"

  validation {
    condition     = contains(["VM.Standard.E4.Flex", "VM.Standard.E5.Flex"], var.instance_shape)
    error_message = "instance_shape must be VM.Standard.E4.Flex or VM.Standard.E5.Flex."
  }
}

# 選んだ製品は 1 台の Compute に入れる（#1316）。既定は以前の製品ごとの既定の合計
# （OCPU: RAG 4 + NL2SQL 2 + Agent 2、メモリ: 32 + 16 + 16 GB）。boot volume は OS・Node.js・uv の Python を
# 共有するため、合計（200 + 100 + 100 GB）より小さい 300 GB にする。
variable "instance_flex_shape_ocpus" {
  description = "OCPUs of the Compute instance. The default is the total of the previous per-product defaults (RAG 4, NL2SQL 2, Agent 2)."
  type        = number
  default     = 8

  validation {
    condition     = var.instance_flex_shape_ocpus > 0
    error_message = "instance_flex_shape_ocpus must be greater than 0."
  }
}

variable "instance_flex_shape_memory" {
  description = "Memory in GB of the Compute instance. The default is the total of the previous per-product defaults (RAG 32, NL2SQL 16, Agent 16)."
  type        = number
  default     = 64

  validation {
    condition     = var.instance_flex_shape_memory > 0
    error_message = "instance_flex_shape_memory must be greater than 0."
  }
}

variable "instance_boot_volume_size" {
  description = "Boot volume size in GB of the Compute instance. The per-service Python environments and models of the RAG parsers are large."
  type        = number
  default     = 300

  validation {
    condition     = var.instance_boot_volume_size >= 50 && var.instance_boot_volume_size <= 32768
    error_message = "instance_boot_volume_size must be between 50 and 32768 GB."
  }
}

variable "instance_boot_volume_vpus" {
  description = "Boot volume VPUs/GB of the Compute instance."
  type        = number
  default     = 10

  validation {
    condition     = contains(concat([10, 20], range(30, 121)), var.instance_boot_volume_vpus)
    error_message = "instance_boot_volume_vpus must be 10, 20, or a value from 30 through 120."
  }
}

variable "instance_image_source_id" {
  description = "Ubuntu image OCID of the Compute instance."
  type        = string
  default     = "ocid1.image.oc1.ap-osaka-1.aaaaaaaa7sbmd5q54w466eojxqwqfvvp554awzjpt2behuwsiefrxnwomq5a"
}

variable "subnet_ai_subnet_id" {
  description = "Subnet OCID of the Compute instance."
  type        = string
  default     = ""
}

variable "ssh_authorized_keys" {
  description = "SSH public keys authorized for the ubuntu user."
  type        = string
  default     = ""
}

# HTTPS（#1316）。on なら Nginx が 443 で TLS 1.2 / 1.3 を受け、80 は 301 で https へ転送する。
# 証明書は Compute の中で作る自作の Root CA と、公開 IP・private IP の証明書。port と証明書の発行者は変数にしない
# （platform/deploy/suite-tls.sh の固定値）。on のとき共通 .env の PLATFORM_AUTH_COOKIE_SECURE を true にする。
variable "https_enabled" {
  description = "Serve the products over HTTPS on port 443 with a private root CA and an IP address certificate generated on the Compute instance (HTTP on port 80 redirects to HTTPS). When false, the products are served over HTTP on port 80."
  type        = bool
  default     = true
}

variable "application_git_url" {
  description = "Git repository URL for the Production Ready suite monorepo (rag/, nl2sql/, agent/, and platform/)."
  type        = string
  default     = "https://github.com/engchina/no.1-production-ready-suite.git"

  validation {
    condition     = trimspace(var.application_git_url) != ""
    error_message = "application_git_url must be a non-empty Git URL."
  }
}

variable "application_git_ref" {
  description = "Git branch or tag of the Production Ready suite deployed to the Compute instance."
  type        = string
  default     = "main"

  validation {
    condition     = trimspace(var.application_git_ref) != ""
    error_message = "application_git_ref must be a non-empty Git ref."
  }
}

# ---------------------------------------------------------------- 配備する製品
# 選んだ製品は 1 台の Compute に入れる（#1316）。最低1つは選ぶ（adb.tf の precondition で検証する）。
# ADB と Wallet は1つだけ作り、選んだ製品すべてで共有する。

variable "deploy_rag" {
  description = "Deploy Production Ready RAG (served under /rag/) on the shared Compute instance."
  type        = bool
  default     = true
}

variable "deploy_nl2sql" {
  description = "Deploy Production Ready NL2SQL (served under /nl2sql/) on the shared Compute instance."
  type        = bool
  default     = true
}

variable "deploy_agent" {
  description = "Deploy the Production Ready Agent Control Plane (served under /agent/; / redirects to it) on the shared Compute instance."
  type        = bool
  default     = true
}

# ---------------------------------------------------------------- RAG

# 文書解析（parser）は CPU のマイクロサービスだけを配備する。parser-docling（既定の解析エンジン。
# RAG_PARSER_ADAPTER_BACKEND=docling。PDF と画像）は常に配備し、ここでは任意の parser だけを選ぶ。
# 以前の Docling を配備するかを選ぶ入力は廃止した（Docling は常に配備する。#286）。
variable "rag_enable_parser_unstructured" {
  description = "Install the Unstructured parser (parser-unstructured) as a systemd service. Use it to ingest formats that the default Docling parser (PDF and images only) cannot parse, such as text, HTML, Office, and e-mail; select Unstructured in the document's processing recipe."
  type        = bool
  default     = false
}

variable "rag_enable_oci_cloud_parsers" {
  description = "Install and start the OCI cloud parser proxies (parser-oci-genai-vision / parser-oci-document-understanding) as systemd services. They call OCI services and need no GPU."
  type        = bool
  default     = false
}

# GPU の parser（MinerU / Dots.OCR）はこの stack に含めない。
# RAG はこれらを外部 API として「文書解析」設定の API host で指定する。

# ---------------------------------------------------------------- NL2SQL

variable "nl2sql_app_environment" {
  description = "NL2SQL application environment (NL2SQL_ENVIRONMENT). HTTP deployments use local with NL2SQL_DEBUG=false; staging and production require https_enabled=true (PLATFORM_AUTH_COOKIE_SECURE=true)."
  type        = string
  default     = "local"

  validation {
    condition     = contains(["local", "staging", "production"], var.nl2sql_app_environment)
    error_message = "nl2sql_app_environment must be local, staging, or production."
  }
}

variable "app_admin_login_user_id" {
  description = "Fixed login user ID of the configuration administrator (SYSTEM_ADMIN) shared by RAG and NL2SQL (PLATFORM_ADMIN_LOGIN_USER_ID)."
  type        = string
  default     = "system_admin"

  validation {
    condition     = var.app_admin_login_user_id == "system_admin"
    error_message = "app_admin_login_user_id is fixed and must be exactly system_admin."
  }
}

variable "app_admin_login_user_password" {
  description = "Login password of the configuration administrator (system_admin) shared by RAG, NL2SQL, and the Agent Control Plane (PLATFORM_ADMIN_LOGIN_USER_PASSWORD). Required when any product is deployed."
  type        = string
  sensitive   = true
  default     = ""

  validation {
    condition = (
      var.app_admin_login_user_password == ""
      || (
        length(var.app_admin_login_user_password) >= 12
        && length(var.app_admin_login_user_password) <= 30
        && !can(regex("[\r\n]", var.app_admin_login_user_password))
        && !can(regex("\"", var.app_admin_login_user_password))
        && !can(regex("admin", var.app_admin_login_user_password))
        && can(regex("[0-9]", var.app_admin_login_user_password))
        && can(regex("[a-z]", var.app_admin_login_user_password))
        && can(regex("[A-Z]", var.app_admin_login_user_password))
      )
    )
    error_message = "app_admin_login_user_password must be 12-30 characters, include uppercase, lowercase, and digits, not include admin or double quotes, and not contain line breaks."
  }
}

variable "nl2sql_oracle_deepsec_enabled" {
  description = "Enable Deep Data Security in the NL2SQL backend/.env."
  type        = bool
  default     = true
}

variable "nl2sql_oracle_deepsec_data_user_password" {
  description = "Password for the shared DEEPSEC_DATA_USER Deep Data Security DATA USER."
  type        = string
  sensitive   = true
  default     = ""

  validation {
    condition = (
      trimspace(var.nl2sql_oracle_deepsec_data_user_password) == ""
      || (
        length(var.nl2sql_oracle_deepsec_data_user_password) >= 12
        && length(var.nl2sql_oracle_deepsec_data_user_password) <= 256
        && !can(regex("[\r\n]", var.nl2sql_oracle_deepsec_data_user_password))
        && !can(regex("\"", var.nl2sql_oracle_deepsec_data_user_password))
      )
    )
    error_message = "nl2sql_oracle_deepsec_data_user_password must be empty or 12-256 characters without double quotes or line breaks."
  }
}

# ---------------------------------------------------------------- Agent

variable "agent_runtime_repository_backend" {
  description = "Oracle repository for Agent runs, business agents and screen-edited definitions (AGENT_RUNTIME_REPOSITORY_BACKEND). The tables are created by the Agent system tables (init_script.sh runs agent_system_schema --initialize); the application does not run DDL."
  type        = string
  default     = "oracle_checkpoint"

  validation {
    condition     = contains(["oracle_checkpoint", "oracle_normalized"], var.agent_runtime_repository_backend)
    error_message = "agent_runtime_repository_backend must be oracle_checkpoint or oracle_normalized."
  }
}
