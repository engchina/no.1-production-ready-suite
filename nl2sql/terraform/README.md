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

### One-click Deploy

Click the button below to open OCI Resource Manager with the Osaka region
(`ap-osaka-1`) selected by default.

[![Deploy to Oracle Cloud](https://oci-resourcemanager-plugin.plugins.oci.oraclecloud.com/latest/deploy-to-oracle-cloud.svg)](https://cloud.oracle.com/resourcemanager/stacks/create?region=ap-osaka-1&zipUrl=https://github.com/engchina/no.1-production-ready-nl2sql/releases/latest/download/production-ready-nl2sql-terraform-stack.zip)

The button uses the latest GitHub Release asset named
`production-ready-nl2sql-terraform-stack.zip`. Publish at least one release with
that asset before using the one-click deploy URL.

### Manual Package and Upload

From the repository root, build the OCI Resource Manager zip package:

```bash
python scripts/package_terraform_stack.py
```

The default output is
`dist/production-ready-nl2sql-terraform-stack.zip`. Upload that zip to OCI
Resource Manager and create a stack. Provide the required form values:

- OCI compartment, region, availability domain, VCN, and subnets
- ADB password
- Compute image, shape, subnet, and SSH public key

After apply completes, use the `application_url` output. The default application
port is `3001`.

AI runtime settings are intentionally not collected by the Resource Manager
stack. After the application starts, configure OCI authentication, OCI
Enterprise AI, OCI Generative AI, and Select AI from the application System
Settings pages.

This stack renders deployment secrets into Compute cloud-init so the instance
can create `backend/.env`. Treat the Resource Manager stack, job history, and
state as sensitive operational material.

## Release Asset

The release workflow publishes:

- `production-ready-nl2sql-terraform-stack.zip`
- `production-ready-nl2sql-terraform-stack.zip.sha256`

The README deploy button intentionally targets the latest-release download URL so
future releases can replace the asset without changing documentation.

## Runtime Notes

The bootstrap script writes `/u01/aipoc/no.1-production-ready-nl2sql/backend/.env`
on the instance and starts in the background from cloud-init, matching the
proven No.1-SQL-Assist Terraform bootstrap pattern. Track progress in
`/var/log/cloud-init-custom.log` until the application containers are ready.
The bootstrap starts:

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
