variable "compartment_ocid" {
  description = "OCI compartment OCID for NL2SQL deployment resources."
  type        = string
}

variable "region" {
  description = "OCI region."
  type        = string
}

variable "backend_image" {
  description = "Container image URI for the FastAPI backend."
  type        = string
  default     = ""
}

variable "frontend_image" {
  description = "Container image URI for the React frontend."
  type        = string
  default     = ""
}

variable "oracle_dsn" {
  description = "Oracle ADB service alias or DSN. Store secrets outside Terraform state."
  type        = string
  default     = ""
}
