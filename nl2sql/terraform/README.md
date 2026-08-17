# Production Ready NL2SQL Terraform Stack

This directory contains the OCI Resource Manager stack for Production Ready
NL2SQL. The stack provisions:

- Oracle Autonomous Database 26ai
- A generated ADB wallet
- One OCI Compute instance
- A cloud-init bootstrap that clones and runs the application with Docker Compose

The default application source is:

- `https://github.com/engchina/no.1-production-ready-nl2sql.git`, ref `main`
- `https://github.com/engchina/no.1-production-ready-platform.git`, ref `main`

## Deploy

Upload `terraform/stack` to OCI Resource Manager and create a stack. Provide the
required form values:

- OCI compartment, region, availability domain, VCN, and subnets
- ADB password
- Compute image, shape, subnet, and SSH public key
- OCI Enterprise AI endpoint, API key, and LLM/VLM model IDs
- OCI Generative AI endpoint and Cohere embedding/rerank model IDs

After apply completes, use the `application_url` output. The default application
port is `3001`.

The default `oci_auth_mode` is `instance_principal`. Configure an OCI dynamic
group and IAM policy that allow the Compute instance to call the required OCI
Generative AI, Database, and related runtime APIs before using live AI features.

This stack renders deployment secrets into Compute cloud-init so the instance
can create `backend/.env`. Treat the Resource Manager stack, job history, and
state as sensitive operational material.

## Runtime Notes

The bootstrap script writes `/u01/aipoc/no.1-production-ready-nl2sql/backend/.env`
on the instance and starts:

- `backend`
- `frontend`
- `schema-refresh-worker`
- `quality-evaluation-worker`

The `ontology-worker` service remains opt-in through the Docker Compose profile.

The first application login is created from:

- username: `ADMIN`
- password: the ADB password supplied to Resource Manager

The application marks this bootstrap administrator for forced password change.

For direct HTTP access, the default is `app_environment=local`,
`DEBUG=false`, and `app_auth_cookie_secure=false`. When serving through HTTPS,
set `app_environment=production` and `app_auth_cookie_secure=true`.

## Troubleshooting

On the Compute instance, inspect:

```bash
sudo tail -f /var/log/cloud-init-custom.log
cd /u01/aipoc/no.1-production-ready-nl2sql
sudo docker compose ps
sudo docker compose logs backend frontend
```

The cloud-init bootstrap:

1. Installs Docker Engine and Docker Compose plugin.
2. Clones the NL2SQL and shared platform repositories.
3. Extracts the ADB wallet to `/u01/aipoc/wallet`.
4. Builds Docker images without deployment secrets.
5. Writes the runtime `backend/.env`.
6. Initializes NL2SQL system tables.
7. Starts the application services.

## Stack Boundaries

This stack preserves the project architecture:

- LLM/VLM: OCI Enterprise AI
- Embedding/rerank: OCI Generative AI
- Vector search and application state: Oracle 26ai

Do not replace these with another LLM provider, external rerank provider, or
external vector database in this stack.
