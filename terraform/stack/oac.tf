# Oracle Analytics Cloud（OAC。任意の配備。#5933）。
# - deploy_oac=true のときだけ 1 つ作る（既定 false）。置き場所は ADB のコンパートメント（AIDP と同じ）。
# - 認証は Identity Domain（oac_identity_domain_ocid）と、その domain の利用者の名前（oac_admin_user）で指定する。
#   IDCS の access token（idcs_access_token）は使わない（期限が短く、stack の入力に向かない）。
# - 公開の endpoint で作る。ADB との接続・dataset・workbook は stack で作らない（OAC の画面で作る）。
#   手順は terraform/README.md「Oracle Analytics Cloud」。
# - Compute の cloud-init には何も書かない。ただし今は、stack を再 apply すると入力に関わらず Compute が作り直される
#   （#5945）ため、最初の配備で決める。
resource "oci_analytics_analytics_instance" "suite" {
  count = var.deploy_oac ? 1 : 0

  compartment_id = var.adb_compartment_ocid
  name           = var.oac_name
  feature_set    = var.oac_feature_set
  license_type   = var.oac_license_type
  domain_id      = trimspace(var.oac_identity_domain_ocid)
  admin_user     = trimspace(var.oac_admin_user)

  capacity {
    capacity_type  = "OLPU_COUNT"
    capacity_value = var.oac_capacity_ocpus
  }

  lifecycle {
    precondition {
      condition     = trimspace(var.oac_identity_domain_ocid) != ""
      error_message = "deploy_oac=true のときは、Identity Domain の OCID（oac_identity_domain_ocid）が必要です。"
    }
    precondition {
      condition     = trimspace(var.oac_admin_user) != ""
      error_message = "deploy_oac=true のときは、Oracle Analytics Cloud の管理者にする利用者の名前（oac_admin_user）が必要です。"
    }
  }
}
