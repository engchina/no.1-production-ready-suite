# 任意の bastion の Compute（#2670）。bastion_enabled が true のときだけ作る。3 製品は入れない（compute.tf の
# oci_core_instance.suite が 1 台で持つ）。管理者が VNC の GUI（GNOME + Google Chrome）から、private subnet の
# Suite の Compute の画面や、プライベート・エンドポイントの ADB に届くための踏み台。
# - shape・image（Oracle Linux 9）・SSH キー・subnet は、未指定なら Suite の Compute の値を使う。
# - cloud_init/bastion.sh が GNOME・TigerVNC（:1 = TCP 5901）・Google Chrome を入れ、firewalld で 5901 を開ける。
# - VNC のパスワードは metadata の bastion_vnc_password_b64 で渡す（user_data には書かない）。値は state に残る。
# - 通信は subnet の security list（stack の外）で許可する（terraform/README.md「bastion の Compute」）。

locals {
  bastion_subnet_id           = trimspace(var.bastion_subnet_id) != "" ? var.bastion_subnet_id : var.subnet_ai_subnet_id
  bastion_shape               = trimspace(var.bastion_shape) != "" ? trimspace(var.bastion_shape) : var.instance_shape
  bastion_image_source_id     = trimspace(var.bastion_image_source_id) != "" ? trimspace(var.bastion_image_source_id) : var.instance_image_source_id
  bastion_ssh_authorized_keys = trimspace(var.bastion_ssh_authorized_keys) != "" ? var.bastion_ssh_authorized_keys : var.ssh_authorized_keys
}

data "oci_core_subnet" "bastion_subnet" {
  count     = var.bastion_enabled ? 1 : 0
  subnet_id = local.bastion_subnet_id
}

locals {
  bastion_subnet_prohibits_public_ip = var.bastion_enabled ? coalesce(
    data.oci_core_subnet.bastion_subnet[0].prohibit_public_ip_on_vnic,
    false,
  ) : true
}

resource "oci_core_instance" "bastion" {
  count = var.bastion_enabled ? 1 : 0

  availability_config {
    is_live_migration_preferred = "false"
    recovery_action             = "STOP_INSTANCE"
  }
  availability_domain = var.availability_domain
  compartment_id      = var.compartment_ocid
  create_vnic_details {
    assign_ipv6ip             = "false"
    assign_private_dns_record = "true"
    assign_public_ip          = !local.bastion_subnet_prohibits_public_ip
    subnet_id                 = local.bastion_subnet_id
  }
  display_name = var.bastion_display_name
  instance_options {
    are_legacy_imds_endpoints_disabled = "false"
  }
  metadata = {
    "user_data"                = base64encode(file("${path.module}/cloud_init/bastion.sh"))
    "ssh_authorized_keys"      = local.bastion_ssh_authorized_keys
    "bastion_vnc_password_b64" = base64encode(var.bastion_vnc_password)
  }
  platform_config {
    is_symmetric_multi_threading_enabled = "true"
    type                                 = "AMD_VM"
  }
  shape = local.bastion_shape
  shape_config {
    baseline_ocpu_utilization = "BASELINE_1_1"
    memory_in_gbs             = var.bastion_flex_shape_memory
    ocpus                     = var.bastion_flex_shape_ocpus
  }
  source_details {
    boot_volume_size_in_gbs = var.bastion_boot_volume_size
    boot_volume_vpus_per_gb = var.bastion_boot_volume_vpus
    source_id               = local.bastion_image_source_id
    source_type             = "image"
  }

  lifecycle {
    precondition {
      condition     = length(var.bastion_vnc_password) == 8
      error_message = "bastion_vnc_password must be configured (exactly 8 characters) when bastion_enabled is true."
    }
  }
}
