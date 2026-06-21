provider "oci" {
  region = var.region
}

locals {
  app_name = "production-ready-nl2sql"

  required_runtime_env = {
    NL2SQL_RUNTIME_MODE               = "oracle"
    NL2SQL_PERSISTENCE_MODE           = "oracle"
    NL2SQL_FEEDBACK_EMBEDDING_ENABLED = "true"
    OCI_GENAI_EMBED_MODEL_ID          = "cohere.embed-v4.0"
    OCI_ENTERPRISE_AI_LLM_PATH        = "/responses"
    NL2SQL_SELECT_AI_PROVIDER         = "oci"
    NL2SQL_FEEDBACK_VECTOR_TABLE      = "NL2SQL_FEEDBACK_VECTORS"
    NL2SQL_FEEDBACK_VECTOR_INDEX      = "NL2SQL_FEEDBACK_VEC_IDX"
  }
}

# This stack intentionally keeps OCI resources explicit for the operator.
# Use Resource Manager variables for image URIs and inject secrets through OCI Vault,
# Container Instances, or OKE workload identity rather than Terraform literals.
