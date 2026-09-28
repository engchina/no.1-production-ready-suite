# 選んだ製品ごとの Compute（1製品 1台）。ADB と Wallet は adb.tf の1つを全 Compute で共有する。
# - 3製品とも Docker を使わず、uv の venv + systemd + Nginx に直接配備する（#286 / #356）。
# - RAG: backend / ingestion-worker / 前処理 / CPU parser をサービスごとの venv と systemd の unit で動かし、host の Nginx が
#   frontend を配信する（GPU の parser 用 Compute は含めない）。
# 各製品の配備手順は <製品>/init_script.sh が持ち、cloud-init はそれを呼ぶだけにする。
# RAG / NL2SQL は for_each の "product"、Agent は "agent" で作る。Agent の backend/.env が RAG / NL2SQL の
# private IP（MCP の URL。#233）を使うため、同じ resource にすると自分自身を参照してしまう。

# 3製品のサービス間 token の署名鍵（共通 .env の PLATFORM_SERVICE_TOKEN_SECRET。#233）。
# Agent が RAG / NL2SQL の MCP を Run の利用者として呼ぶときに使い、全 Compute に同じ値を配る。
# 値は Terraform の state に残るため、state は他の secret と同じく機密として扱う。
resource "random_password" "service_token_secret" {
  length  = 48
  special = false
}

resource "oci_core_instance" "product" {
  for_each = local.non_agent_products

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
      condition     = !contains(["rag", "nl2sql", "agent"], each.key) || trimspace(var.app_admin_login_user_password) != ""
      error_message = "app_admin_login_user_password must be configured when deploy_rag, deploy_nl2sql, or deploy_agent is true. Every product requires a login (system_admin and the users created in User Management)."
    }
    precondition {
      condition     = each.key != "nl2sql" || var.nl2sql_app_environment == "local" || var.nl2sql_app_auth_cookie_secure
      error_message = "nl2sql_app_auth_cookie_secure must be true when nl2sql_app_environment is staging or production."
    }
    precondition {
      condition     = each.key != "nl2sql" || !var.nl2sql_oracle_deepsec_enabled || trimspace(var.nl2sql_oracle_deepsec_data_user_password) != ""
      error_message = "nl2sql_oracle_deepsec_data_user_password must be configured when nl2sql_oracle_deepsec_enabled=true."
    }
  }
}

# 以前は Agent も oci_core_instance.product["agent"] だった。既存の Compute を作り直さないよう移す。
moved {
  from = oci_core_instance.product["agent"]
  to   = oci_core_instance.agent["agent"]
}

# Agent の Compute。設定は上の product と同じで、user_data だけが RAG / NL2SQL の Compute に依存する。
resource "oci_core_instance" "agent" {
  for_each = local.agent_products

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
    "user_data"           = local.agent_cloud_init_user_data
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

  lifecycle {
    precondition {
      condition     = trimspace(var.app_admin_login_user_password) != ""
      error_message = "app_admin_login_user_password must be configured when deploy_rag, deploy_nl2sql, or deploy_agent is true. Every product requires a login (system_admin and the users created in User Management)."
    }
  }
}
