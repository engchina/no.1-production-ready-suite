# Production Ready NL2SQL Terraform Stack

This directory contains the OCI Resource Manager stack for Production Ready
NL2SQL. The stack provisions:

- Oracle Autonomous Database 26ai
- A generated ADB wallet
- One OCI Compute instance
- A cloud-init bootstrap that clones the repositories and runs the application
  directly on Compute with Nginx and systemd

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
port is `80`. The public entrypoint is `http://<compute-ip>/`; browser API
requests use the same origin under `/api/...`.

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
`/var/log/cloud-init-custom.log` and `/var/log/nl2sql-init.log` until the
application services are ready.
The bootstrap starts:

- Nginx on the configured application port, default `80`
- `production-ready-nl2sql-backend` on private upstream `127.0.0.1:8000`

Nginx serves `frontend/dist` at `/` and reverse proxies `/api/` to the backend.
Only TCP `80` needs to be opened publicly for the application. If ADB uses a
private endpoint, the selected network must still allow Compute to reach ADB on
TCP `1522`.

Worker systemd units are installed but not started by the bootstrap, so the
application can boot even when the database is not reachable yet or tables do
not exist. After database connectivity and schema initialization are ready,
operators can enable them with:

```bash
sudo systemctl enable --now production-ready-nl2sql-schema-refresh-worker
sudo systemctl enable --now production-ready-nl2sql-quality-evaluation-worker
sudo systemctl enable --now production-ready-nl2sql-ontology-worker
```

The first application login is created from:

- username: `ADMIN`
- password: the ADB password supplied to Resource Manager

The application marks this bootstrap administrator for forced password change.
If the auth/RBAC tables do not exist yet, the first login attempt applies the
idempotent security migrations and then creates the bootstrap administrator.

For direct HTTP access, the default is `app_environment=local`,
`DEBUG=false`, and `app_auth_cookie_secure=false`. When serving through HTTPS,
set `app_environment=production` and `app_auth_cookie_secure=true`.

## Troubleshooting

On the Compute instance, inspect:

```bash
sudo tail -f /var/log/cloud-init-custom.log
sudo tail -f /var/log/nl2sql-init.log
cd /u01/aipoc/no.1-production-ready-nl2sql
sudo systemctl status production-ready-nl2sql-backend
sudo journalctl -u production-ready-nl2sql-backend -f
sudo journalctl -u production-ready-nl2sql-schema-refresh-worker -f
sudo journalctl -u production-ready-nl2sql-quality-evaluation-worker -f
sudo journalctl -u production-ready-nl2sql-ontology-worker -f
sudo nginx -t
sudo tail -f /var/log/nginx/production-ready-nl2sql-error.log
curl -i http://127.0.0.1:8000/api/health
curl -i http://127.0.0.1/api/health
curl -i http://127.0.0.1/health
```

The cloud-init bootstrap:

1. Installs Nginx, Node.js 22, uv, and build dependencies.
2. Clones the NL2SQL and shared platform repositories.
3. Extracts the ADB wallet to `/u01/aipoc/wallet`.
4. Writes the runtime `backend/.env`.
5. Installs backend dependencies with `uv sync --locked --no-dev --python 3.12`.
6. Builds the shared UI package and `frontend/dist`.
7. Starts the backend service with systemd.
8. Configures Nginx to serve the SPA and same-origin `/api/` path.

Backend startup is intentionally independent of database reachability and table
existence. If the database is not ready yet, the application still starts; run
schema initialization from the System Settings UI or, for operators who prefer
CLI recovery after DB connectivity is available:

```bash
cd /u01/aipoc/no.1-production-ready-nl2sql/backend
sudo -u ubuntu /usr/local/bin/uv run python -m app.cli.nl2sql_system_schema --initialize
sudo -u ubuntu /usr/local/bin/uv run python -m app.cli.app_security_migrate --apply
sudo systemctl restart production-ready-nl2sql-backend
```

## Stack Boundaries

This stack preserves the project architecture:

- LLM/VLM: OCI Enterprise AI
- Embedding/rerank: OCI Generative AI
- Vector search and application state: Oracle 26ai

Do not replace these with another LLM provider, external rerank provider, or
external vector database in this stack.
