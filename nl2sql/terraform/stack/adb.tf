data "oci_core_subnet" "adb_acl_subnet" {
  count = (
    local.create_new_adb
    && var.adb_network_access_type == "SECURE_ACCESS_FROM_ALLOWED_IPS_AND_VCNS"
    && var.adb_acl_notation_type == "VCN"
    && trimspace(local.effective_adb_acl_subnet_id) != ""
  ) ? 1 : 0
  subnet_id = local.effective_adb_acl_subnet_id
}

data "oci_database_autonomous_database" "selected_existing_adb" {
  count = local.use_existing_adb && trimspace(var.existing_adb_ocid) != "" ? 1 : 0

  autonomous_database_id = var.existing_adb_ocid
}

locals {
  adb_deployment_mode_normalized = trimspace(var.adb_deployment_mode)
  adb_deployment_mode_create_values = [
    "CREATE_NEW",
    "新規 Autonomous Database の作成",
    "新規 Autonomous AI Database の作成"
  ]
  adb_deployment_mode_existing_values = [
    "USE_EXISTING",
    "既存の Autonomous Database を選択",
    "既存の Autonomous AI Database を選択"
  ]
  create_new_adb   = contains(local.adb_deployment_mode_create_values, local.adb_deployment_mode_normalized)
  use_existing_adb = contains(local.adb_deployment_mode_existing_values, local.adb_deployment_mode_normalized)
  adb_display_name = trimspace(var.adb_display_name) != "" ? var.adb_display_name : var.adb_name
  adb_private_endpoint_enabled = (
    local.create_new_adb
    && (var.adb_network_access_type == "PRIVATE_ENDPOINT_ONLY" || var.adb_use_private_subnet)
  )
  adb_secure_acl_enabled = local.create_new_adb && var.adb_network_access_type == "SECURE_ACCESS_FROM_ALLOWED_IPS_AND_VCNS"
  effective_adb_acl_vcn_id = (
    trimspace(var.adb_acl_vcn_id) != "" ? trimspace(var.adb_acl_vcn_id) : trimspace(var.vcn_ai_vcn_id)
  )
  effective_adb_acl_subnet_id = (
    trimspace(var.adb_acl_subnet_id) != "" ? trimspace(var.adb_acl_subnet_id) : trimspace(var.subnet_ai_subnet_id)
  )

  adb_acl_cidr_entries = local.adb_secure_acl_enabled && var.adb_acl_notation_type == "CIDR_BLOCK" && trimspace(var.adb_acl_cidr_blocks) != "" ? [
    for cidr in split(",", var.adb_acl_cidr_blocks) : trimspace(cidr)
    if trimspace(cidr) != ""
  ] : []

  adb_acl_vcn_entries = local.adb_secure_acl_enabled && var.adb_acl_notation_type == "VCN" && trimspace(local.effective_adb_acl_vcn_id) != "" ? [
    trimspace(local.effective_adb_acl_subnet_id) != "" ? "${local.effective_adb_acl_vcn_id};${data.oci_core_subnet.adb_acl_subnet[0].cidr_block}" : local.effective_adb_acl_vcn_id
  ] : []

  adb_whitelisted_ips = local.adb_secure_acl_enabled ? concat(local.adb_acl_vcn_entries, local.adb_acl_cidr_entries) : null

  existing_adb_db_name = try(data.oci_database_autonomous_database.selected_existing_adb[0].db_name, "")
  effective_existing_oracle_dsn = trimspace(var.existing_oracle_dsn) != "" ? trimspace(var.existing_oracle_dsn) : (
    trimspace(local.existing_adb_db_name) != "" ? "${lower(local.existing_adb_db_name)}_high" : ""
  )

  effective_adb_ocid        = local.create_new_adb ? oci_database_autonomous_database.generated_database_autonomous_database[0].id : var.existing_adb_ocid
  effective_adb_name        = local.create_new_adb ? var.adb_name : local.existing_adb_db_name
  effective_oracle_user     = local.create_new_adb ? "ADMIN" : var.existing_oracle_user
  effective_oracle_password = local.create_new_adb ? var.adb_password : var.existing_oracle_password
  effective_oracle_dsn      = local.create_new_adb ? "${lower(var.adb_name)}_high" : local.effective_existing_oracle_dsn
  effective_oracle_wallet_password = local.create_new_adb ? var.adb_password : (
    trimspace(var.existing_oracle_wallet_password) != "" ? var.existing_oracle_wallet_password : var.existing_oracle_password
  )
}

resource "oci_database_autonomous_database" "generated_database_autonomous_database" {
  count                                          = local.create_new_adb ? 1 : 0
  admin_password                                 = var.adb_password
  autonomous_maintenance_schedule_type           = "REGULAR"
  backup_retention_period_in_days                = var.adb_backup_retention_period_in_days
  character_set                                  = "AL32UTF8"
  compartment_id                                 = var.compartment_ocid
  compute_count                                  = var.adb_compute_count
  compute_model                                  = var.adb_compute_model
  data_storage_size_in_tbs                       = var.adb_data_storage_size_in_tbs
  db_name                                        = var.adb_name
  db_version                                     = var.adb_db_version
  db_workload                                    = var.adb_workload
  display_name                                   = local.adb_display_name
  is_auto_scaling_enabled                        = var.adb_is_auto_scaling_enabled
  is_auto_scaling_for_storage_enabled            = var.adb_is_auto_scaling_for_storage_enabled
  is_dedicated                                   = "false"
  is_mtls_connection_required                    = var.adb_is_mtls_connection_required
  is_preview_version_with_service_terms_accepted = "false"
  license_model                                  = var.license_model
  ncharacter_set                                 = "AL16UTF16"
  subnet_id                                      = local.adb_private_endpoint_enabled ? var.adb_subnet_id : null
  whitelisted_ips                                = local.adb_secure_acl_enabled ? local.adb_whitelisted_ips : null

  dynamic "resource_pool_summary" {
    for_each = var.adb_is_elastic_pool_enabled ? [1] : []

    content {
      pool_size                = var.adb_resource_pool_size
      pool_storage_size_in_tbs = var.adb_resource_pool_storage_size_in_tbs
    }
  }
}

resource "oci_database_autonomous_database_wallet" "generated_autonomous_database_wallet" {
  autonomous_database_id = local.effective_adb_ocid
  password               = local.effective_oracle_wallet_password
  base64_encode_content  = "true"
  generate_type          = "SINGLE"

  lifecycle {
    precondition {
      condition     = trimspace(local.effective_adb_ocid) != ""
      error_message = "An Autonomous AI Database OCID is required to generate the wallet."
    }
    precondition {
      condition     = trimspace(local.effective_oracle_wallet_password) != ""
      error_message = "A wallet password is required to generate the Autonomous AI Database wallet."
    }
  }
}

# Save the generated wallet ZIP as a local binary file.
resource "local_file" "wallet_zip" {
  content_base64 = oci_database_autonomous_database_wallet.generated_autonomous_database_wallet.content
  filename       = "${path.module}/wallet_full.zip"
}

# Shrink the wallet ZIP before injecting it into cloud-init.
data "external" "wallet_files" {
  depends_on = [local_file.wallet_zip]
  program    = ["bash", "${path.module}/extract_wallet.sh"]
}
