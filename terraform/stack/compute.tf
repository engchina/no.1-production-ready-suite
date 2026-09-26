# 選んだ製品ごとの Compute（1製品 1台）。ADB と Wallet は adb.tf の1つを全 Compute で共有する。
# - RAG: Docker Compose で backend / ingestion-worker / 前処理 / CPU parser を動かし、host の Nginx が frontend を配信する
#   （GPU の parser 用 Compute は含めない）。
# - NL2SQL / Agent: Nginx + systemd に直接配備する（Docker は使わない）。
# 各製品の配備手順は <製品>/init_script.sh が持ち、cloud-init はそれを呼ぶだけにする。
resource "oci_core_instance" "product" {
  for_each = local.selected_products

  depends_on = [
    data.external.wallet_files
  ]
  availability_config {
    is_live_migration_preferred = "false"
    recovery_action             = "STOP_INSTANCE"
  }
  availability_domain = var.availability_domain
  compartment_id      = var.compartment_ocid
  create_vnic_details {
    assign_ipv6ip             = "false"
    assign_private_dns_record = "true"
    assign_public_ip          = !local.compute_subnet_prohibits_public_ip
    subnet_id                 = var.subnet_ai_subnet_id
  }
  display_name = local.product_instances[each.key].display_name
  instance_options {
    are_legacy_imds_endpoints_disabled = "false"
  }
  metadata = {
    "user_data"           = local.cloud_init_user_data[each.key]
    "ssh_authorized_keys" = var.ssh_authorized_keys
  }
  platform_config {
    is_symmetric_multi_threading_enabled = "true"
    type                                 = "AMD_VM"
  }
  shape = var.instance_shape
  shape_config {
    baseline_ocpu_utilization = "BASELINE_1_1"
    memory_in_gbs             = local.product_instances[each.key].memory_in_gbs
    ocpus                     = local.product_instances[each.key].ocpus
  }
  source_details {
    boot_volume_size_in_gbs = local.product_instances[each.key].boot_volume_size
    boot_volume_vpus_per_gb = var.instance_boot_volume_vpus
    source_id               = var.instance_image_source_id
    source_type             = "image"
  }

  # 製品固有の入力は、その製品を配備するときだけ必須にする。
  lifecycle {
    precondition {
      condition     = each.key != "rag" || trimspace(var.rag_app_login_password) != ""
      error_message = "rag_app_login_password must be configured when deploy_rag is true. The RAG backend requires a login (AUTH_MODE=production) for the UI and API."
    }
    precondition {
      condition     = each.key != "nl2sql" || trimspace(var.nl2sql_app_admin_login_user_password) != ""
      error_message = "nl2sql_app_admin_login_user_password must be configured when deploy_nl2sql is true."
    }
    precondition {
      condition     = each.key != "nl2sql" || var.nl2sql_app_environment == "local" || var.nl2sql_app_auth_cookie_secure
      error_message = "nl2sql_app_auth_cookie_secure must be true when nl2sql_app_environment is staging or production."
    }
    precondition {
      condition     = each.key != "nl2sql" || !var.nl2sql_oracle_deepsec_enabled || trimspace(var.nl2sql_oracle_deepsec_data_user_password) != ""
      error_message = "nl2sql_oracle_deepsec_data_user_password must be configured when nl2sql_oracle_deepsec_enabled=true."
    }
    precondition {
      condition     = each.key != "agent" || trimspace(var.agent_app_basic_auth_password) != ""
      error_message = "agent_app_basic_auth_password must be configured when deploy_agent is true. Nginx protects the Agent Control Plane UI and API with HTTP Basic authentication."
    }
  }
}
