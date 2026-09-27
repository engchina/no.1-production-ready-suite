locals {
  instance_access_ips = {
    for product, instance in oci_core_instance.product :
    product => local.compute_subnet_prohibits_public_ip ? instance.private_ip : instance.public_ip
  }
  application_urls = {
    for product, ip in local.instance_access_ips :
    product => var.application_port == 80 ? "http://${ip}" : "http://${ip}:${var.application_port}"
  }
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

# 配備しなかった製品の output は null（Resource Manager では表示されない）。

output "rag_application_url" {
  description = "Production Ready RAG URL (log in with system_admin and app_admin_login_user_password)."
  value       = lookup(local.application_urls, "rag", null)
}

output "rag_ssh_to_instance" {
  description = "SSH command for the RAG Compute instance."
  value       = contains(keys(local.instance_access_ips), "rag") ? "ssh -o ServerAliveInterval=10 ubuntu@${local.instance_access_ips["rag"]}" : null
}

output "rag_compose_services" {
  description = "docker compose services built and started on the RAG Compute instance."
  value       = var.deploy_rag ? join(", ", local.rag_compose_services) : null
}

output "nl2sql_application_url" {
  description = "Production Ready NL2SQL URL (log in with system_admin)."
  value       = lookup(local.application_urls, "nl2sql", null)
}

output "nl2sql_ssh_to_instance" {
  description = "SSH command for the NL2SQL Compute instance."
  value       = contains(keys(local.instance_access_ips), "nl2sql") ? "ssh -o ServerAliveInterval=10 ubuntu@${local.instance_access_ips["nl2sql"]}" : null
}

output "agent_application_url" {
  description = "Production Ready Agent Control Plane URL (log in with system_admin and app_admin_login_user_password)."
  value       = lookup(local.application_urls, "agent", null)
}

output "agent_ssh_to_instance" {
  description = "SSH command for the Agent Control Plane Compute instance."
  value       = contains(keys(local.instance_access_ips), "agent") ? "ssh -o ServerAliveInterval=10 ubuntu@${local.instance_access_ips["agent"]}" : null
}
