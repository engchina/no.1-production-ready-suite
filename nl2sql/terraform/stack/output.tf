locals {
  instance_access_ip = local.compute_subnet_prohibits_public_ip ? oci_core_instance.generated_oci_core_instance.private_ip : oci_core_instance.generated_oci_core_instance.public_ip
}

output "autonomous_database_ocid" {
  description = "Autonomous AI Database OCID."
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

output "ssh_to_instance" {
  description = "Convenient command to SSH to the instance using its public or private access IP."
  value       = "ssh -o ServerAliveInterval=10 ubuntu@${local.instance_access_ip}"
}

output "application_url" {
  description = "Production Ready NL2SQL application URL."
  value       = var.application_port == 80 ? "http://${local.instance_access_ip}" : "http://${local.instance_access_ip}:${var.application_port}"
}
