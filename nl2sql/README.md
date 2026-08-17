# Production Ready NL2SQL

Production Ready NL2SQL is a production-oriented NL2SQL reference implementation
for deploying an Oracle 26ai-backed application with OCI Enterprise AI and OCI
Generative AI.

## Deploy to OCI

Click the button below to open OCI Resource Manager with the Osaka region
(`ap-osaka-1`) selected by default.

[![Deploy to Oracle Cloud](https://oci-resourcemanager-plugin.plugins.oci.oraclecloud.com/latest/deploy-to-oracle-cloud.svg)](https://cloud.oracle.com/resourcemanager/stacks/create?region=ap-osaka-1&zipUrl=https://github.com/engchina/no.1-production-ready-nl2sql/releases/latest/download/production-ready-nl2sql-terraform-stack.zip)

The button downloads the latest Terraform Resource Manager stack release asset:

`production-ready-nl2sql-terraform-stack.zip`

The Compute deployment serves the frontend through Nginx on HTTP port `80` and
proxies API calls through the same origin at `/api/...`.

At least one GitHub Release must publish that asset before the one-click deploy
URL can create a stack. For manual packaging, upload steps, required variables,
and troubleshooting, see [terraform/README.md](terraform/README.md).

## Stack Boundaries

This deployment keeps the approved architecture:

- LLM/VLM: OCI Enterprise AI
- Embedding/rerank: OCI Generative AI
- Vector search and application state: Oracle 26ai
