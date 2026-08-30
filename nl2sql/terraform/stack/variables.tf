variable "region" {
  description = "OCI region used by Resource Manager and runtime clients."
  type        = string
  default     = "ap-osaka-1"
}

variable "availability_domain" {
  description = "Availability domain for the Compute instance."
  type        = string
  default     = ""
}

variable "compartment_ocid" {
  description = "OCI compartment OCID for the NL2SQL deployment."
  type        = string
  default     = ""
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
    condition     = !can(regex("[\r\n]", var.existing_oracle_user))
    error_message = "existing_oracle_user must not contain line breaks."
  }
}

variable "existing_oracle_password" {
  description = "Oracle database password for an existing Autonomous AI Database."
  type        = string
  sensitive   = true
  default     = ""

  validation {
    condition     = !can(regex("[\r\n]", var.existing_oracle_password))
    error_message = "existing_oracle_password must not contain line breaks."
  }
}

variable "existing_oracle_dsn" {
  description = "Optional Oracle DSN for an existing Autonomous AI Database. Leave blank to use lower(db_name)_high from the selected ADB."
  type        = string
  default     = ""

  validation {
    condition     = !can(regex("[\r\n]", var.existing_oracle_dsn))
    error_message = "existing_oracle_dsn must not contain line breaks."
  }
}

variable "existing_oracle_wallet_password" {
  description = "Wallet password for an existing Autonomous AI Database. Leave blank to reuse existing_oracle_password."
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
  default     = "NL2SQLADB"

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
    condition     = !can(regex("[\r\n]", var.adb_password))
    error_message = "adb_password must not contain line breaks."
  }
}

variable "adb_workload" {
  description = "Autonomous AI Database workload type."
  type        = string
  default     = "OLTP"

  validation {
    condition     = contains(["OLTP", "DW", "AJD", "APEX", "LH"], var.adb_workload)
    error_message = "adb_workload must be one of OLTP, DW, AJD, APEX, or LH."
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
  default     = "SECURE_ACCESS_FROM_ALLOWED_IPS_AND_VCNS"

  validation {
    condition = contains([
      "PUBLIC_ENDPOINT",
      "SECURE_ACCESS_FROM_ALLOWED_IPS_AND_VCNS",
      "PRIVATE_ENDPOINT_ONLY"
    ], var.adb_network_access_type)
    error_message = "adb_network_access_type must be PUBLIC_ENDPOINT, SECURE_ACCESS_FROM_ALLOWED_IPS_AND_VCNS, or PRIVATE_ENDPOINT_ONLY."
  }
}

variable "adb_use_private_subnet" {
  description = "Compatibility switch for older tfvars. Prefer adb_network_access_type."
  type        = bool
  default     = false
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
    condition     = contains(["VCN", "CIDR_BLOCK"], var.adb_acl_notation_type)
    error_message = "adb_acl_notation_type must be VCN or CIDR_BLOCK."
  }
}

variable "adb_acl_vcn_id" {
  description = "VCN OCID allowed to access Autonomous AI Database when secure ACL mode is selected."
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
  description = "Compute instance display name."
  type        = string
  default     = "NL2SQL_INSTANCE"
}

variable "instance_shape" {
  description = "Compute instance shape."
  type        = string
  default     = "VM.Standard.E5.Flex"

  validation {
    condition     = contains(["VM.Standard.E4.Flex", "VM.Standard.E5.Flex"], var.instance_shape)
    error_message = "instance_shape must be VM.Standard.E4.Flex or VM.Standard.E5.Flex."
  }
}

variable "instance_flex_shape_ocpus" {
  description = "Compute instance OCPUs."
  type        = number
  default     = 2

  validation {
    condition     = var.instance_flex_shape_ocpus > 0
    error_message = "instance_flex_shape_ocpus must be greater than 0."
  }
}

variable "instance_flex_shape_memory" {
  description = "Compute instance memory in GB."
  type        = number
  default     = 16

  validation {
    condition     = var.instance_flex_shape_memory > 0
    error_message = "instance_flex_shape_memory must be greater than 0."
  }
}

variable "instance_boot_volume_size" {
  description = "Compute boot volume size in GB."
  type        = number
  default     = 100

  validation {
    condition     = var.instance_boot_volume_size >= 50 && var.instance_boot_volume_size <= 32768
    error_message = "instance_boot_volume_size must be between 50 and 32768 GB."
  }
}

variable "instance_boot_volume_vpus" {
  description = "Compute boot volume VPUs/GB."
  type        = number
  default     = 10

  validation {
    condition     = contains(concat([10, 20], range(30, 121)), var.instance_boot_volume_vpus)
    error_message = "instance_boot_volume_vpus must be 10, 20, or a value from 30 through 120."
  }
}

variable "instance_image_source_id" {
  description = "Ubuntu image OCID for the Compute instance."
  type        = string
  default     = "ocid1.image.oc1.ap-osaka-1.aaaaaaaa7sbmd5q54w466eojxqwqfvvp554awzjpt2behuwsiefrxnwomq5a"
}

variable "subnet_ai_subnet_id" {
  description = "Subnet OCID for the Compute instance."
  type        = string
  default     = ""
}

variable "ssh_authorized_keys" {
  description = "SSH public keys authorized for the ubuntu user."
  type        = string
  default     = ""
}

variable "application_port" {
  description = "TCP port exposed by host Nginx and the instance firewall."
  type        = number
  default     = 80

  validation {
    condition     = floor(var.application_port) == var.application_port && var.application_port >= 1 && var.application_port <= 65535
    error_message = "application_port must be an integer between 1 and 65535."
  }
}

variable "application_git_url" {
  description = "Git repository URL for Production Ready NL2SQL."
  type        = string
  default     = "https://github.com/engchina/no.1-production-ready-nl2sql.git"

  validation {
    condition     = trimspace(var.application_git_url) != ""
    error_message = "application_git_url must be a non-empty Git URL."
  }
}

variable "application_git_ref" {
  description = "Git branch or tag used to deploy Production Ready NL2SQL."
  type        = string
  default     = "main"

  validation {
    condition     = trimspace(var.application_git_ref) != ""
    error_message = "application_git_ref must be a non-empty Git ref."
  }
}

variable "platform_git_url" {
  description = "Git repository URL for the shared Production Ready platform packages."
  type        = string
  default     = "https://github.com/engchina/no.1-production-ready-platform.git"

  validation {
    condition     = trimspace(var.platform_git_url) != ""
    error_message = "platform_git_url must be a non-empty Git URL."
  }
}

variable "platform_git_ref" {
  description = "Git branch or tag used to deploy shared Production Ready platform packages."
  type        = string
  default     = "main"

  validation {
    condition     = trimspace(var.platform_git_ref) != ""
    error_message = "platform_git_ref must be a non-empty Git ref."
  }
}

variable "app_environment" {
  description = "Application ENVIRONMENT. Direct HTTP deployments use local with DEBUG=false; production requires app_auth_cookie_secure=true."
  type        = string
  default     = "local"

  validation {
    condition     = contains(["local", "staging", "production"], var.app_environment)
    error_message = "app_environment must be local, staging, or production."
  }
}

variable "app_auth_cookie_secure" {
  description = "Set true when the application is served through HTTPS."
  type        = bool
  default     = false
}

variable "app_admin_login_user_id" {
  description = "Fixed login user ID for the built-in SYSTEM_ADMIN configuration administrator."
  type        = string
  default     = "system_admin"

  validation {
    condition     = var.app_admin_login_user_id == "system_admin"
    error_message = "app_admin_login_user_id is fixed and must be exactly system_admin."
  }
}

variable "app_admin_login_user_password" {
  description = "Login password for the built-in SYSTEM_ADMIN configuration administrator."
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

variable "oracle_deepsec_data_user_password" {
  description = "Password for the shared DEEPSEC_DATA_USER Deep Data Security DATA USER."
  type        = string
  sensitive   = true

  validation {
    condition = (
      trimspace(var.oracle_deepsec_data_user_password) != ""
      && length(var.oracle_deepsec_data_user_password) >= 12
      && length(var.oracle_deepsec_data_user_password) <= 256
      && !can(regex("[\r\n]", var.oracle_deepsec_data_user_password))
      && !can(regex("\"", var.oracle_deepsec_data_user_password))
    )
    error_message = "oracle_deepsec_data_user_password must be 12-256 characters, not include double quotes, and not contain line breaks."
  }
}
