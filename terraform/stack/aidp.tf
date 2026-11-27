# Oracle AI Data Platform Workbench（AIDP。任意の配備。#5917）。
# - deploy_aidp=true のときだけ 1 つ作る（既定 false）。置き場所は ADB のコンパートメント。
# - AI 機能（Knowledge Base・Agent）の vector DB に、stack の ADB（新規 / 既存。Lakehouse）を関連付ける。関連付けには
#   ADMIN のパスワードが要る（AIDP が ADB に自分の schema を作る）。Vault は作らず vector_db_admin_cred で渡す
#   （値は state に残る。ADMIN のパスワードはもともと state と cloud-init にある）。
# - is_enable_ai_feature は後から変えると資源が作り直される（ForceNew）ため、既定で有効にする。
# - IAM policy は stack で作らない（tenancy の範囲の policy を含むため）。無いと作成が 20〜40 分後に失敗するので、
#   確認の入力（aidp_iam_policies_confirmed）で止める。要る policy は terraform/README.md「Oracle AI Data Platform」。
# - workspace・cluster・External Catalog・AIDP の Agent は Terraform の資源が無く、AIDP の画面で作る。
# - External Catalog が ADB に接続する DB ユーザー（aidp_db_user_name）は、Compute の suite-init.sh が ADMIN で作る
#   （platform/deploy/suite-db-user.py apply-aidp）。Terraform からは DB に接続しない。
locals {
  aidp_db_user = upper(trimspace(var.aidp_db_user_name))
  aidp_ocid    = var.deploy_aidp ? oci_ai_data_platform_ai_data_platform.suite[0].id : ""
  # 画面を開く URL。資源の属性に URL が無いため、OCID から組み立てる。
  aidp_url = var.deploy_aidp ? "https://aidp.oci.oraclecloud.com/?ocid=${local.aidp_ocid}" : ""
  # Workbench REST API（workspace・catalog・Agent など）の基点。
  aidp_api_endpoint = var.deploy_aidp ? "https://aidp.${var.region}.oci.oraclecloud.com/api/20260430/aiDataPlatforms/${local.aidp_ocid}" : ""
}

resource "oci_ai_data_platform_ai_data_platform" "suite" {
  count = var.deploy_aidp ? 1 : 0

  compartment_id         = var.adb_compartment_ocid
  display_name           = var.aidp_display_name
  default_workspace_name = var.aidp_default_workspace_name
  is_enable_ai_feature   = var.aidp_ai_feature_enabled
  vector_db_id           = var.aidp_ai_feature_enabled ? local.effective_adb_ocid : null
  vector_db_admin_cred   = var.aidp_ai_feature_enabled ? local.effective_adb_admin_password : null

  # 作成は 20〜40 分かかることがある（provider の既定は 20 分）。
  timeouts {
    create = "90m"
    update = "90m"
    delete = "60m"
  }

  lifecycle {
    precondition {
      condition     = var.aidp_iam_policies_confirmed
      error_message = "Oracle AI Data Platform の IAM policy を設定してから、aidp_iam_policies_confirmed を true にしてください（要る policy は terraform/README.md「Oracle AI Data Platform」。stack は policy を作りません）。"
    }
    precondition {
      condition     = local.create_new_adb || trimspace(var.existing_adb_admin_password) != ""
      error_message = "deploy_aidp=true のときは、既存の Autonomous AI Database の ADMIN のパスワード（existing_adb_admin_password）が必要です（AI 機能の関連付けと、AIDP 用の DB ユーザーの作成に使います）。"
    }
    precondition {
      condition     = trimspace(var.aidp_db_user_password) != ""
      error_message = "deploy_aidp=true のときは、AIDP 用の DB ユーザーのパスワード（aidp_db_user_password）が必要です。"
    }
    precondition {
      condition     = local.aidp_db_user != upper(trimspace(local.effective_oracle_user))
      error_message = "aidp_db_user_name は、3 製品の接続のユーザー（アプリ用の DB ユーザー・ADMIN・既存の DB のユーザー）と別の名前にしてください。"
    }
    precondition {
      condition     = !strcontains(lower(var.aidp_db_user_password), lower(local.aidp_db_user))
      error_message = "aidp_db_user_password に aidp_db_user_name を含めないでください（Autonomous AI Database のパスワードの規則）。"
    }
  }
}
