output "app_name" {
  value = local.app_name
}

output "backend_image" {
  value = var.backend_image
}

output "frontend_image" {
  value = var.frontend_image
}

output "required_runtime_env" {
  value = local.required_runtime_env
}
