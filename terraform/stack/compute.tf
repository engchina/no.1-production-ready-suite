locals {
  # Oracle Linux 9 の Arm platform image。公式 image 一覧のリージョン別 OCID を固定する。
  always_free_arm_images = {
    ap-tokyo-1 = "ocid1.image.oc1.ap-tokyo-1.aaaaaaaa6bmmxt3iileo7a3wtrjmgtzh2fjdeby57qhbgkvq7qegav56jzlq"
    ap-osaka-1 = "ocid1.image.oc1.ap-osaka-1.aaaaaaaajdu542grchkh2np7zshy3yyvp4jy5yik3ixwlhf3heziits5vfhq"
  }
  effective_instance_shape = var.compute_always_free_enabled ? "VM.Standard.A1.Flex" : var.instance_shape
  effective_instance_image = var.compute_always_free_enabled ? local.always_free_arm_images[var.region] : var.instance_image_source_id
}

# 選んだ製品（RAG / NL2SQL / Agent）をすべて 1 台の Compute に入れる（#1316）。ADB と Wallet は adb.tf の1つを共有する。
# - 3製品とも Docker を使わず、uv の venv + systemd + Nginx に直接配備する（#286 / #356）。
# - 配備は platform/deploy/suite-init.sh が、各製品の <製品>/init_script.sh を順に呼んで行う。backend・worker・
#   RAG の前処理 / parser は製品ごとに独立した unit と port（127.0.0.1）で動き、Nginx の site だけを 1 つにする
#   （/rag/ /nl2sql/ /agent/、/ → /agent/、HTTPS は既定 on で 443）。
# 以前の製品ごとの Compute（resource の名前 product / agent）は、この resource に置き換わる（apply で作り直す）。

# 3製品のサービス間 token の署名鍵（共通 .env の PLATFORM_SERVICE_TOKEN_SECRET。#233）。
# Agent が RAG / NL2SQL の MCP を Run の利用者として呼ぶときに使う。
# 値は Terraform の state に残るため、state は他の secret と同じく機密として扱う。
resource "random_password" "service_token_secret" {
  length  = 48
  special = false
}

resource "oci_core_instance" "suite" {
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
  display_name = var.instance_display_name
  instance_options {
    are_legacy_imds_endpoints_disabled = "false"
  }
  metadata = {
    "user_data"           = local.cloud_init_user_data
    "ssh_authorized_keys" = var.ssh_authorized_keys
  }
  dynamic "platform_config" {
    for_each = var.compute_always_free_enabled ? [] : [1]
    content {
      is_symmetric_multi_threading_enabled = true
      type                                 = "AMD_VM"
    }
  }
  shape = local.effective_instance_shape
  shape_config {
    baseline_ocpu_utilization = var.compute_always_free_enabled ? null : "BASELINE_1_1"
    memory_in_gbs             = var.compute_always_free_enabled ? var.compute_always_free_memory_in_gbs : var.instance_flex_shape_memory
    ocpus                     = var.compute_always_free_enabled ? var.compute_always_free_ocpus : var.instance_flex_shape_ocpus
  }
  source_details {
    boot_volume_size_in_gbs = var.compute_always_free_enabled ? var.compute_always_free_boot_volume_size_in_gbs : var.instance_boot_volume_size
    boot_volume_vpus_per_gb = var.compute_always_free_enabled ? 10 : var.instance_boot_volume_vpus
    source_id               = local.effective_instance_image
    source_type             = "image"
  }

  # 製品固有の入力は、その製品を配備するときだけ必須にする。
  lifecycle {
    precondition {
      condition     = !var.compute_always_free_enabled || !var.bastion_enabled
      error_message = "Always Free Compute では bastion を無効にしてください（bastion の Chrome は x86 専用）。"
    }
    precondition {
      condition     = trimspace(var.app_admin_login_user_password) != ""
      error_message = "app_admin_login_user_password must be configured when deploy_rag, deploy_nl2sql, or deploy_agent is true. Every product requires a login (system_admin and the users created in User Management)."
    }
    precondition {
      condition     = length(local.public_ports_reserved) == 0
      error_message = "http_port / https_port must not be SSH, a well-known service port, or a port the suite uses on the Compute instance (22, 25, 53, 111, 1521, 1522, 3000, 3001, 3002, 3306, 5432, 6379, 9090, 8000, 8010, 8020, 18000-18099)."
    }
    precondition {
      condition     = var.platform_environment == "local" || var.https_enabled
      error_message = "https_enabled must be true when platform_environment is staging or production (all products require Secure cookies)."
    }
    precondition {
      condition     = !var.deploy_nl2sql || !var.nl2sql_oracle_deepsec_enabled || trimspace(var.nl2sql_oracle_deepsec_data_user_password) != ""
      error_message = "nl2sql_oracle_deepsec_data_user_password must be configured when nl2sql_oracle_deepsec_enabled=true."
    }
  }
}
