# Production Ready NL2SQL Terraform Stack

This directory contains the OCI Resource Manager stack for Production Ready
NL2SQL. The stack provisions:

- Oracle Autonomous AI Database 26ai, or connection settings for an existing ADB
- A generated ADB wallet for the selected/new ADB
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
- Application administrator password. The username is fixed to `system_admin`
  and is case-sensitive.
- Deep Data Security DATA USER password. The stack enables
  `ORACLE_DEEPSEC_ENABLED=true`, keeps `ORACLE_DEEPSEC_DATA_USER` fixed as
  `DEEPSEC_DATA_USER`, and writes this password to
  `ORACLE_DEEPSEC_DATA_USER_PASSWORD` in `backend/.env`.
- Oracle driver mode is intentionally fixed to Thin + mTLS:
  `ORACLE_DRIVER_MODE=thin` and `ORACLE_CLIENT_LIB_DIR=`. The cloud-init script
  does not install Oracle Instant Client because Deep Data Security is supported
  only by python-oracledb Thin mode in this stack.
- Autonomous AI Database mode:
  - `新規 Autonomous AI Database の作成`: provide the new ADB sizing, network,
    license, and password fields. New ADBs default to Thin-compatible Wallet
    mTLS (`Require mutual TLS (mTLS)=true`) with the ADB access-control list
    enabled. In VCN ACL mode, leaving the override fields blank allows the
    selected Compute subnet by writing `VCN_OCID;SUBNET_CIDR` to
    `whitelisted_ips`.
    If `Require mutual TLS (mTLS)` is set to `false`, the bootstrap writes
    `ORACLE_CONNECTION_SECURITY=walletless_tls`; use that only with an ADB
    connection string that supports one-way TLS and an ACL that permits the
    application host.
    For public endpoint ACL deployments that leave the VCN ACL overrides blank,
    ensure the Compute subnet has a valid OCI private path to ADB; otherwise use
    CIDR ACL mode and enter the Compute/NAT public egress IP or CIDR. For
    private endpoint deployments, select an ADB subnet reachable from the
    application Compute subnet in the same VCN routing/security path.
  - `既存の Autonomous AI Database を選択`: provide the existing ADB OCID plus the
    values written to `ORACLE_USER` and `ORACLE_PASSWORD`. `ORACLE_DSN` can be
    left blank; the stack uses the selected ADB `db_name` with `_high`, for
    example `NL2SQLADB` becomes `nl2sqladb_high`. The wallet password can be
    supplied separately, or left blank to reuse `ORACLE_PASSWORD`. This stack
    reads the selected ADB and generates a wallet, but does not modify its
    network access, mTLS, or access-control list settings.
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

The configured `SYSTEM_ADMIN` login comes from the application administrator
values supplied in Resource Manager:

- `APP_ADMIN_LOGIN_USER_ID=system_admin`
- `APP_ADMIN_LOGIN_USER_PASSWORD`

Deep Data Security is enabled by default in Terraform deployments:

- `ORACLE_DEEPSEC_ENABLED=true`
- `ORACLE_DEEPSEC_DATA_USER=DEEPSEC_DATA_USER`
- `ORACLE_DEEPSEC_DATA_USER_PASSWORD`

After deployment, open `システム設定 > Deep Data Security`, apply the V001 steps in
order, then run the Data Grant verification.

Oracle Select AI is configured through `backend/.env` only; the application
System Settings pages cannot write this value. The stack therefore writes a
fixed credential name:

- `NL2SQL_SELECT_AI_CREDENTIAL_NAME=OCI_CRED`

To use Select AI, create a matching `OCI_CRED` credential on the ADB with
`DBMS_CLOUD.CREATE_CREDENTIAL`. The backend never creates the credential itself.
When the credential name is left empty or the credential does not exist, Select
AI reports a readiness warning and the engine stays unavailable. To use a
different name, edit `backend/.env` on the instance and restart
`production-ready-nl2sql-backend`.

This configured administrator is independent from the database connection user,
does not read from `NL2SQL_APP_USERS`, and does not require the auth/RBAC tables
to exist. Application-local users are checked from `NL2SQL_APP_USERS`. The
configured administrator password can be changed from the application password
change screen; the backend writes the new value back to `backend/.env`.

When `adb_deployment_mode` selects an existing ADB (`既存の Autonomous AI
Database を選択`, or legacy `USE_EXISTING`), the stack does not create any ADB
resource. It generates a wallet from the selected existing ADB OCID and writes the
database values into `backend/.env`:

- `ORACLE_USER`
- `ORACLE_PASSWORD`
- `ORACLE_DSN` (`existing_oracle_dsn`, or `<selected ADB db_name lowercased>_high`
  when left blank)
- `ORACLE_CONNECTION_SECURITY` (`wallet_mtls` when mTLS is required,
  otherwise `walletless_tls`)
- `ORACLE_WALLET_PASSWORD`
- `ORACLE_ADB_OCID`
- `ORACLE_ADB_REGION`

The Resource Manager form hides the application environment and auth cookie
security inputs. Direct HTTP deployments keep the internal defaults
`app_environment=local`, `DEBUG=false`, and `app_auth_cookie_secure=false`.
If you override these Terraform variables outside the form for HTTPS, use
`app_environment=production` with `app_auth_cookie_secure=true`.

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

1. Installs Nginx, Node.js 24, uv, and build dependencies.
2. Clones the NL2SQL and shared platform repositories.
3. Extracts the ADB wallet to `/u01/aipoc/wallet`.
4. Writes the runtime `backend/.env`.
5. Installs backend dependencies with `uv sync --locked --no-dev --python 3.12`.
6. Builds the shared UI package and `frontend/dist`.
7. Starts the backend service with systemd.
8. Configures Nginx to serve the SPA and same-origin `/api/` path.

Node.js installation uses the NodeSource apt repository first. If apt candidate
inspection, installation, or post-install validation fails, the init script
falls back to the official Node.js `latest-v24.x` Linux tarball with
`SHASUMS256.txt` verification. Override `NODEJS_OFFICIAL_RELEASE_BASE_URL` or
`NODEJS_OFFICIAL_INSTALL_DIR` only for controlled mirrors or recovery testing;
`NODEJS_OFFICIAL_BIN_DIR` is also available when the symlink target must be
isolated.

Backend startup is intentionally independent of database reachability and table
existence. If the database is not ready yet, the application still starts; run
schema initialization from the System Settings UI or, for operators who prefer
CLI recovery after DB connectivity is available:

```bash
cd /u01/aipoc/no.1-production-ready-nl2sql/backend
sudo -u ubuntu /usr/local/bin/uv run python -m app.cli.nl2sql_system_schema --initialize
sudo -u ubuntu /usr/local/bin/uv run python -m app.cli.app_security_migrate --apply --skip-bootstrap
sudo systemctl restart production-ready-nl2sql-backend
```

## Stack Boundaries

This stack preserves the project architecture:

- LLM/VLM: OCI Enterprise AI
- Embedding/rerank: OCI Generative AI
- Vector search and application state: Oracle 26ai

Do not replace these with another LLM provider, external rerank provider, or
external vector database in this stack.
