terraform {
  required_version = ">= 1.8.0"

  required_providers {
    oci = {
      source  = "oracle/oci"
      version = "~> 6.0"
    }
  }
}

variable "compartment_id" {
  description = "OCI compartment OCID"
  type        = string
}

variable "region" {
  description = "OCI region"
  type        = string
  default     = "ap-osaka-1"
}

provider "oci" {
  region = var.region
}

# 実運用では network、OKE/Container Instances、Object Storage、Oracle 26ai、
# Vault、Logging/Monitoring を環境別 module として分割する。
