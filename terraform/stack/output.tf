locals {
  # 利用者が開く IP（公開 IP があれば公開 IP、private subnet なら private IP）。
  instance_access_ip = (
    local.compute_subnet_prohibits_public_ip
    ? oci_core_instance.suite.private_ip
    : oci_core_instance.suite.public_ip
  )
  # 公開の port（#1316）。既定（HTTPS 443・HTTP 80）でなければ URL に port を付ける。
  public_port          = var.https_enabled ? var.https_port : var.http_port
  default_public_port  = var.https_enabled ? 443 : 80
  application_base_url = "${var.https_enabled ? "https" : "http"}://${local.instance_access_ip}${local.public_port == local.default_public_port ? "" : ":${local.public_port}"}"
}

output "autonomous_database_ocid" {
  description = "Autonomous AI Database OCID shared by every deployed product."
  value       = local.effective_adb_ocid
}

output "autonomous_database_high_connection_string" {
  description = "Autonomous AI Database HIGH connection string."
  value = local.create_new_adb ? lookup(
    oci_database_autonomous_database.generated_database_autonomous_database[0].connection_strings[0].all_connection_strings,
    "HIGH",
    "unavailable",
  ) : local.effective_oracle_dsn
}

output "application_url" {
  description = "Root URL of the Compute instance. / redirects to the Agent Control Plane (or to the first selected product when the Agent is not deployed)."
  value       = "${local.application_base_url}/"
}

# 配備しなかった製品の output は null（Resource Manager では表示されない）。

output "rag_application_url" {
  description = "Production Ready RAG URL (log in with system_admin and app_admin_login_user_password)."
  value       = var.deploy_rag ? "${local.application_base_url}/rag/" : null
}

output "rag_services" {
  description = "Preprocessing and parser services installed as systemd units (production-ready-rag-<service>.service) on the Compute instance."
  value       = var.deploy_rag ? join(", ", local.rag_services) : null
}

output "nl2sql_application_url" {
  description = "Production Ready NL2SQL URL (log in with system_admin)."
  value       = var.deploy_nl2sql ? "${local.application_base_url}/nl2sql/" : null
}

output "agent_application_url" {
  description = "Production Ready Agent Control Plane URL (log in with system_admin and app_admin_login_user_password)."
  value       = var.deploy_agent ? "${local.application_base_url}/agent/" : null
}

# Oracle AI Data Platform（#5917）。配備しないときは null（Resource Manager では表示されない）。
output "aidp_url" {
  description = "Oracle AI Data Platform Workbench URL. Sign in with an OCI IAM user that has the use ai-data-platforms permission."
  value       = var.deploy_aidp ? local.aidp_url : null
}

# Oracle Analytics Cloud（#5933）。配備しないときは null。
output "oac_url" {
  description = "Oracle Analytics Cloud URL. Sign in with a user of the identity domain (the administrator is oac_admin_user)."
  value       = var.deploy_oac ? oci_analytics_analytics_instance.suite[0].service_url : null
}

output "ca_certificate_url" {
  description = "Private root CA certificate of the HTTPS server certificate. Import it as a trusted root CA on client PCs to remove the browser warning (also available from System settings > Appearance and certificate)."
  value       = var.https_enabled ? "${local.application_base_url}/platform/ca.crt" : null
}

output "ssh_to_instance" {
  description = "SSH command for the Compute instance."
  value       = "ssh -o ServerAliveInterval=10 opc@${local.instance_access_ip}"
}

# bastion の Compute（#2670）。作らないときは null（Resource Manager では表示されない）。
locals {
  bastion_access_ip = var.bastion_enabled ? (
    local.bastion_subnet_prohibits_public_ip
    ? oci_core_instance.bastion[0].private_ip
    : oci_core_instance.bastion[0].public_ip
  ) : null
}

output "bastion_vnc_address" {
  description = "VNC address of the bastion Compute instance (TigerVNC display :1). Log in with the bastion VNC password."
  value       = var.bastion_enabled ? "${local.bastion_access_ip}:5901" : null
}

output "ssh_to_bastion" {
  description = "SSH command for the bastion Compute instance."
  value       = var.bastion_enabled ? "ssh -o ServerAliveInterval=10 opc@${local.bastion_access_ip}" : null
}

# Oracle AI Data Platform の接続の情報（#5917）。パスワードは出さない。
output "aidp_ocid" {
  description = "Oracle AI Data Platform Workbench instance OCID."
  value       = var.deploy_aidp ? local.aidp_ocid : null
}

output "aidp_alias_key" {
  description = "Alias key (short form of the OCID) of the Oracle AI Data Platform Workbench instance."
  value       = var.deploy_aidp ? oci_ai_data_platform_ai_data_platform.suite[0].alias_key : null
}

output "aidp_default_workspace_name" {
  description = "Default workspace created with the Oracle AI Data Platform Workbench instance."
  value       = var.deploy_aidp ? var.aidp_default_workspace_name : null
}

output "aidp_api_endpoint" {
  description = "Base URL of the Oracle AI Data Platform Workbench REST API (workspaces, catalogs, agents) of this instance."
  value       = var.deploy_aidp ? local.aidp_api_endpoint : null
}

output "aidp_web_socket_endpoint" {
  description = "WebSocket endpoint of the Oracle AI Data Platform Workbench instance."
  value       = var.deploy_aidp ? oci_ai_data_platform_ai_data_platform.suite[0].web_socket_endpoint : null
}

output "aidp_external_catalog_info" {
  description = "Values to register the Autonomous AI Database as an External Catalog in Oracle AI Data Platform (enter the password of the Oracle AI Data Platform database user yourself)."
  value = var.deploy_aidp ? join(", ", [
    "Autonomous AI Database OCID: ${local.effective_adb_ocid}",
    "Database name: ${local.effective_adb_name}",
    "Service: ${local.effective_oracle_dsn}",
    "User name: ${local.aidp_db_user}",
  ]) : null
}

output "oac_ocid" {
  description = "Oracle Analytics Cloud instance OCID."
  value       = var.deploy_oac ? oci_analytics_analytics_instance.suite[0].id : null
}
