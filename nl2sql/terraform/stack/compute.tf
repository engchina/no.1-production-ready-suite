resource "oci_core_instance" "generated_oci_core_instance" {
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
  platform_config {
    is_symmetric_multi_threading_enabled = "true"
    type                                 = "AMD_VM"
  }
  shape = var.instance_shape
  shape_config {
    baseline_ocpu_utilization = "BASELINE_1_1"
    memory_in_gbs             = var.instance_flex_shape_memory
    ocpus                     = var.instance_flex_shape_ocpus
  }
  source_details {
    boot_volume_size_in_gbs = var.instance_boot_volume_size
    boot_volume_vpus_per_gb = var.instance_boot_volume_vpus
    source_id               = var.instance_image_source_id
    source_type             = "image"
  }

  lifecycle {
    precondition {
      condition     = local.create_new_adb ? trimspace(var.adb_password) != "" : true
      error_message = "adb_password must be configured."
    }
    precondition {
      condition = (
        local.create_new_adb && local.adb_private_endpoint_enabled
      ) ? trimspace(var.adb_private_endpoint_vcn_compartment_id) != "" : true
      error_message = "プライベート・エンドポイントを作成する場合は、VCNのコンパートメントを選択してください。"
    }
    precondition {
      condition = (
        local.create_new_adb && local.adb_private_endpoint_enabled
      ) ? trimspace(var.adb_private_endpoint_vcn_id) != "" : true
      error_message = "プライベート・エンドポイントを作成する場合は、仮想クラウド・ネットワークを選択してください。"
    }
    precondition {
      condition = (
        local.create_new_adb && local.adb_private_endpoint_enabled
      ) ? trimspace(var.adb_private_endpoint_subnet_compartment_id) != "" : true
      error_message = "プライベート・エンドポイントを作成する場合は、サブネットのコンパートメントを選択してください。"
    }
    precondition {
      condition = (
        local.create_new_adb && local.adb_private_endpoint_enabled
      ) ? trimspace(var.adb_subnet_id) != "" : true
      error_message = "プライベート・エンドポイントを作成する場合は、サブネットを選択してください。"
    }
    precondition {
      condition = (
        local.create_new_adb
        && local.adb_private_endpoint_enabled
        && trimspace(var.adb_subnet_id) != ""
        && trimspace(var.adb_private_endpoint_vcn_id) != ""
      ) ? data.oci_core_subnet.adb_private_endpoint_subnet[0].vcn_id == trimspace(var.adb_private_endpoint_vcn_id) : true
      error_message = "選択したサブネットは、プライベート・エンドポイント用VCNに属している必要があります。"
    }
    precondition {
      condition = (
        local.create_new_adb
        && local.adb_secure_acl_enabled
        && var.adb_acl_notation_type == "VCN"
      ) ? trimspace(var.adb_acl_vcn_compartment_id) != "" : true
      error_message = "VCNアクセス制御を使用する場合は、VCNのコンパートメントを選択してください。"
    }
    precondition {
      condition = (
        local.create_new_adb
        && local.adb_secure_acl_enabled
        && var.adb_acl_notation_type == "VCN"
      ) ? trimspace(local.effective_adb_acl_vcn_id) != "" : true
      error_message = "VCNアクセス制御を使用する場合は、許可する仮想クラウド・ネットワークを選択してください。"
    }
    precondition {
      condition = (
        local.create_new_adb
        && local.adb_secure_acl_enabled
        && var.adb_acl_notation_type == "VCN"
        && trimspace(var.adb_acl_subnet_id) != ""
      ) ? trimspace(var.adb_acl_subnet_compartment_id) != "" : true
      error_message = "許可するサブネットを指定する場合は、サブネットのコンパートメントを選択してください。"
    }
    precondition {
      condition = (
        local.create_new_adb
        && local.adb_secure_acl_enabled
        && var.adb_acl_notation_type == "IPアドレスまたはCIDRブロック"
      ) ? length(local.adb_acl_cidr_entries) > 0 : true
      error_message = "IPアドレスまたはCIDRブロックによるアクセス制御を使用する場合は、許可する値を入力してください。"
    }
    precondition {
      condition = local.create_new_adb || (
        trimspace(var.existing_adb_ocid) != ""
        && trimspace(var.existing_oracle_user) != ""
        && trimspace(var.existing_oracle_password) != ""
      )
      error_message = "existing_adb_ocid, existing_oracle_user, and existing_oracle_password must be configured when adb_deployment_mode selects an existing Autonomous AI Database."
    }
    precondition {
      condition     = var.app_environment == "local" || var.app_auth_cookie_secure
      error_message = "app_auth_cookie_secure must be true when app_environment is staging or production."
    }
    precondition {
      condition     = !var.oracle_deepsec_enabled || trimspace(var.oracle_deepsec_data_user_password) != ""
      error_message = "oracle_deepsec_data_user_password must be configured when oracle_deepsec_enabled=true."
    }
  }
}
