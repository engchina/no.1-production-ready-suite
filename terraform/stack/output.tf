locals {
  # 利用者が開く IP（公開 IP があれば公開 IP、private subnet なら private IP）。
  instance_access_ip = (
    local.compute_subnet_prohibits_public_ip
    ? oci_core_instance.suite.private_ip
    : oci_core_instance.suite.public_ip
  )
  # HTTPS は 443、HTTP は 80（port は変数にしない。#1316）。
  application_base_url = "${var.https_enabled ? "https" : "http"}://${local.instance_access_ip}"
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

output "ca_certificate_url" {
  description = "Private root CA certificate of the HTTPS server certificate. Import it as a trusted root CA on client PCs to remove the browser warning (also available from System settings > Appearance and connection)."
  value       = var.https_enabled ? "${local.application_base_url}/platform/ca.crt" : null
}

output "ssh_to_instance" {
  description = "SSH command for the Compute instance."
  value       = "ssh -o ServerAliveInterval=10 ubuntu@${local.instance_access_ip}"
}
