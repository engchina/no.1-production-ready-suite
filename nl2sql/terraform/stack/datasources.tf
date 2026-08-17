data "oci_core_subnet" "selected_compute_subnet" {
  subnet_id = var.subnet_ai_subnet_id
}

data "template_file" "cloud_init_file" {
  template = file("./cloud_init/bootstrap.template.yaml")

  vars = {
    adb_name            = var.adb_name
    adb_ocid            = oci_database_autonomous_database.generated_database_autonomous_database.id
    application_git_ref = var.application_git_ref
    application_git_url = var.application_git_url
    application_port    = tostring(var.application_port)
    backend_env         = base64gzip(local.backend_env)
    compartment_ocid    = var.compartment_ocid
    db_dsn              = "${lower(var.adb_name)}_high"
    platform_git_ref    = var.platform_git_ref
    platform_git_url    = var.platform_git_url
    region              = var.region
    wallet_content      = data.external.wallet_files.result.wallet_content
    wallet_dir_host     = local.wallet_dir_host
  }
}

data "template_cloudinit_config" "cloud_init" {
  gzip          = true
  base64_encode = true

  part {
    filename     = "bootstrap.yaml"
    content_type = "text/cloud-config"
    content      = data.template_file.cloud_init_file.rendered
  }
}
