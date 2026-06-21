# NL2SQL Terraform Stack

This directory is the Resource Manager entrypoint for the current FastAPI + React
NL2SQL application. It intentionally keeps secrets out of Terraform state.

Build images from the workspace root:

```bash
docker build -f no.1-production-ready-nl2sql/backend/Dockerfile -t <backend-image> .
docker build -f no.1-production-ready-nl2sql/frontend/Dockerfile -t <frontend-image> .
```

Upload the `terraform/stack` directory to OCI Resource Manager and provide:

- `compartment_ocid`
- `region`
- `backend_image`
- `frontend_image`
- Oracle / OCI secrets through Vault, OKE secrets, or Container Instances env vars

Keep the model policy unchanged:

- LLM/VLM: OCI Enterprise AI
- Embedding/rerank: OCI Generative AI
- Vector search/state: Oracle 26ai
