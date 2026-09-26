#!/usr/bin/env python3
"""統合 OCI Resource Manager stack zip の入力契約と配備契約を検証する（#217）。

1つの ADB を全製品で共有し、選んだ製品（RAG / NL2SQL / Agent）ごとに Compute を1台作る stack の、
Resource Manager フォーム・Terraform・cloud-init と、各製品の init_script.sh / Settings との整合を確かめる。
stdlib だけで動かす（CI と release workflow で追加依存なしに実行するため）。
"""

from __future__ import annotations

import argparse
import re
import stat
import sys
import zipfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
PRODUCTS = ("rag", "nl2sql", "agent")
PRIVATE_ACCESS = "プライベート・エンドポイント・アクセスのみ"
ALLOWED_ACCESS = "許可されたIPおよびVCN限定のセキュア・アクセス"
EVERYWHERE_ACCESS = "すべての場所からのセキュア・アクセス"
IP_OR_CIDR = "IPアドレスまたはCIDRブロック"
# 製品と共有 platform は monorepo から1回の clone で取得する。
APPLICATION_GIT_URL = "https://github.com/engchina/no.1-production-ready-suite.git"
WALLET_DIR = "/u01/aipoc/wallet"

REQUIRED_ENTRIES = {
    "schema.yaml",
    "adb.tf",
    "compute.tf",
    "datasources.tf",
    "locals.tf",
    "output.tf",
    "provider.tf",
    "variables.tf",
    "versions.tf",
    "extract_wallet.sh",
    "cloud_init/bootstrap.template.yaml",
}
FORBIDDEN_NAMES = {".terraform.lock.hcl", "wallet_full.zip", "wallet_small.zip"}

HIDDEN_VARIABLES = [
    "application_git_url",
    "application_git_ref",
    "existing_oracle_wallet_password",
    "adb_is_mtls_connection_required",
    "nl2sql_app_environment",
    "nl2sql_app_auth_cookie_secure",
    "nl2sql_app_admin_login_user_id",
    "agent_runtime_repository_backend",
]
SECRET_VARIABLES = [
    "adb_password",
    "existing_oracle_password",
    "existing_oracle_wallet_password",
    "rag_app_login_password",
    "nl2sql_app_admin_login_user_password",
    "nl2sql_oracle_deepsec_data_user_password",
    "agent_app_basic_auth_password",
    "agent_control_plane_mcp_token_secret",
]
# 製品ごとの入力は、その製品を選んだときだけフォームに出す（group の visible と、group 内の変数の接頭辞）。
PRODUCT_GROUPS = {
    "RAG": "rag",
    "NL2SQL": "nl2sql",
    "NL2SQL Deep Data Security": "nl2sql",
    "Agent Control Plane": "agent",
}
# 製品を選ばないとフォームから消えるため、Resource Manager では任意入力にして Terraform の precondition で必須にする。
PRODUCT_PASSWORDS = {
    "rag": "rag_app_login_password",
    "nl2sql": "nl2sql_app_admin_login_user_password",
    "agent": "agent_app_basic_auth_password",
}

# RAG の Compute が常に build・起動する compose の service（CPU だけ）と、任意で足せる service。
RAG_BASE_COMPOSE_SERVICES = [
    "backend",
    "ingestion-worker",
    "preprocess-office-to-pdf",
    "preprocess-pdf-to-page-images",
    "preprocess-csv-to-json",
    "preprocess-excel-to-json",
    "preprocess-url-to-markdown",
    "preprocess-image-enhance",
    "preprocess-pii-redact",
    "parser-unstructured",
    "pipeline-generation",
    "pipeline-retrieval",
]
RAG_OPTIONAL_COMPOSE_SERVICES = [
    "parser-docling",
    "parser-marker",
    "parser-oci-genai-vision",
    "parser-oci-document-understanding",
]
# docker-compose.yml の environment が正本の key。RAG の env_file に書くと compose の値に隠れて誤解を招く。
RAG_COMPOSE_OWNED_ENV_KEYS = {"ENVIRONMENT", "LOCAL_STORAGE_DIR", "MODEL_SETTINGS_FILE", "OCI_CONFIG_FILE"}

# 製品ごとの backend/.env に必ず書く値。
REQUIRED_BACKEND_ENV_LINES = {
    # RAG の env_file は compose が `$` を展開するため、入力由来の値を single quote で囲む。
    "rag": [
        "AUTH_MODE=production\n",
        "AUTH_USERNAME='${var.rag_app_login_user}'\n",
        "AUTH_PASSWORD='${var.rag_app_login_password}'\n",
        "AUTH_SESSION_SECRET=\n",
        "AUTH_COOKIE_SECURE=${var.rag_app_auth_cookie_secure}\n",
        "AUDIT_CONTEXT_HASH_SALT=\n",
        "OCI_COMPARTMENT_ID=${var.compartment_ocid}\n",
        "ORACLE_USER='${local.effective_oracle_user}'\n",
        "ORACLE_PASSWORD='${local.effective_oracle_password}'\n",
        "ORACLE_DSN='${local.effective_oracle_dsn}'\n",
        "ORACLE_CLIENT_LIB_DIR=\n",
        "ORACLE_WALLET_DIR=${local.wallet_dir_host}\n",
        "ORACLE_WALLET_PASSWORD='${local.effective_oracle_wallet_password}'\n",
        "ORACLE_ADB_OCID=${local.effective_adb_ocid}\n",
        "RAG_PARSER_ADAPTER_BACKEND=unstructured\n",
        "RAG_SERVICE_CONTROL_ENABLED=false\n",
    ],
    "nl2sql": [
        "APP_ADMIN_LOGIN_USER_ID=${var.nl2sql_app_admin_login_user_id}\n",
        "APP_ADMIN_LOGIN_USER_PASSWORD=${var.nl2sql_app_admin_login_user_password}\n",
        "APP_AUTH_COOKIE_SECURE=${var.nl2sql_app_auth_cookie_secure}\n",
        "OCI_COMPARTMENT_ID=${var.compartment_ocid}\n",
        "ORACLE_DRIVER_MODE=thin\n",
        "ORACLE_CONNECTION_SECURITY=${local.nl2sql_oracle_connection_security}\n",
        "ORACLE_CLIENT_LIB_DIR=\n",
        "ORACLE_WALLET_DIR=${local.wallet_dir_host}\n",
        "ORACLE_WALLET_PASSWORD=${local.effective_oracle_wallet_password}\n",
        "ORACLE_DEEPSEC_ENABLED=${var.nl2sql_oracle_deepsec_enabled}\n",
        "ORACLE_DEEPSEC_DATA_USER=DEEPSEC_DATA_USER\n",
        'ORACLE_DEEPSEC_DATA_USER_PASSWORD=${var.nl2sql_oracle_deepsec_enabled ? '
        'var.nl2sql_oracle_deepsec_data_user_password : ""}\n',
        "ORACLE_ADB_OCID=${local.effective_adb_ocid}\n",
        "NL2SQL_SELECT_AI_CREDENTIAL_NAME=OCI_CRED\n",
    ],
    "agent": [
        "ORACLE_CLIENT_LIB_DIR=\n",
        "ORACLE_WALLET_DIR=${local.wallet_dir_host}\n",
        "ORACLE_WALLET_PASSWORD=${local.effective_oracle_wallet_password}\n",
        "ORACLE_ADB_OCID=${local.effective_adb_ocid}\n",
        "OCI_COMPARTMENT_ID=${var.compartment_ocid}\n",
        "AGENT_RUNTIME_REPOSITORY_BACKEND=${var.agent_runtime_repository_backend}\n",
        "AGENT_RUNTIME_DISPATCH_MODE=in_process\n",
        "AGENT_RUNTIME_ORACLE_DSN=${local.effective_oracle_dsn}\n",
        "AGENT_RUNTIME_ORACLE_USER=${local.effective_oracle_user}\n",
        "AGENT_RUNTIME_ORACLE_PASSWORD=${local.effective_oracle_password}\n",
        "AGENT_RUNTIME_ORACLE_WALLET_DIR=${local.wallet_dir_host}\n",
        "AGENT_RUNTIME_ORACLE_WALLET_PASSWORD=${local.effective_oracle_wallet_password}\n",
        "AGENT_RUNTIME_ORACLE_CREATE_SCHEMA=true\n",
        "AGENT_RUNTIME_SERVICE_CONTROL_ENABLED=false\n",
        "AGENT_CONTROL_PLANE_PUBLIC_BASE_URL=${trimspace(var.agent_control_plane_public_base_url)}\n",
        "AGENT_CONTROL_PLANE_MCP_TOKEN_SECRET=${var.agent_control_plane_mcp_token_secret}\n",
    ],
}
SETTINGS_FILES = {
    "rag": REPO_ROOT / "rag" / "backend" / "app" / "config.py",
    "nl2sql": REPO_ROOT / "nl2sql" / "backend" / "app" / "settings.py",
    "agent": REPO_ROOT / "agent" / "backend" / "app" / "settings.py",
}
# pr_backend_core.BaseServiceSettings の共通 field（各製品の Settings には書かれていない）。
BASE_SETTINGS_FIELDS = {"app_version", "log_level", "environment", "cors_origins"}

# 各製品の init_script.sh が守る配備の契約。
INIT_SCRIPT_CONTRACTS = {
    "rag": [
        'WALLET_DIR="${APP_ROOT}/wallet"',
        'COMPOSE_PROJECT_NAME="production-ready-rag"',
        'BACKEND_HOST="127.0.0.1"',
        'BACKEND_PORT="8000"',
        "REQUIRED_COMPOSE_SERVICES=(backend ingestion-worker parser-unstructured)",
        "docker-compose-plugin",
        "ports: !override",
        '- "${BACKEND_HOST}:${BACKEND_PORT}:8000"',
        "- ${WALLET_DIR}:${WALLET_DIR}",
        'find "${WALLET_DIR}" -type f -exec chmod 0600 {} \\;',
        "python -m app.rag.system_schema_cli initialize",
        "openssl rand -hex 32",
        "ExecStart=${COMPOSE_WRAPPER} up -d --no-build ${COMPOSE_SERVICES[*]}",
        "proxy_pass http://${BACKEND_HOST}:${BACKEND_PORT};",
        "proxy_buffering off;",
        "/api/health",
    ],
    "nl2sql": [
        'WALLET_DIR="${APP_ROOT}/wallet"',
        'chown "root:${APP_GROUP}" "${APP_ROOT}"',
        'chmod 0775 "${APP_ROOT}"',
        'install -d -m 0700 -o "${APP_USER}" -g "${APP_GROUP}" "${WALLET_DIR}"',
        'find "${WALLET_DIR}" -type f -exec chmod 0600 {} \\;',
        "app.cli.nl2sql_system_schema --initialize",
        "app.cli.app_security_migrate --apply --skip-bootstrap",
        'if [ "${DATABASE_INITIALIZATION_READY}" = "true" ]; then',
        "production-ready-nl2sql-schema-refresh-worker.service",
        "production-ready-nl2sql-quality-evaluation-worker.service",
        "production-ready-nl2sql-ontology-worker.service",
    ],
    "agent": [
        'WALLET_DIR="${APP_ROOT}/wallet"',
        'BACKEND_PORT="8020"',
        'BACKEND_WORKERS="1"',
        'install -d -m 0700 -o "${APP_USER}" -g "${APP_GROUP}" "${WALLET_DIR}"',
        'find "${WALLET_DIR}" -type f -exec chmod 0600 {} \\;',
        "import app.features.agent.runtime",
        "openssl passwd -6 -stdin",
        "auth_basic_user_file ${NGINX_HTPASSWD_PATH};",
        "location /api/mcp/ {\n        auth_basic off;",
        "location = /health {\n        auth_basic off;",
        "uv sync --locked --no-dev --python 3.12",
        '"${PROPS_DIR}/basic_auth_user.txt"',
        '"${PROPS_DIR}/basic_auth_password"',
    ],
}
INIT_SCRIPT_ORDER = {
    "rag": [
        "  install_system_packages\n",
        "  install_docker\n",
        "  load_compose_services\n",
        "  prepare_filesystem\n",
        "  install_runtime_env\n",
        "  write_compose_override\n",
        "  build_images\n",
        "  prepare_container_permissions\n",
        "  initialize_database_schema\n",
        "  build_frontend\n",
        "  configure_systemd\n",
        "  configure_nginx\n",
        "  wait_for_backend\n",
    ],
    "nl2sql": [
        "app.cli.nl2sql_system_schema --initialize",
        "app.cli.app_security_migrate --apply --skip-bootstrap",
    ],
    "agent": [
        "  install_runtime_env\n",
        "  install_backend\n",
        "  initialize_database_schema\n",
        "  build_frontend\n",
        "  configure_systemd\n",
        "  configure_basic_auth\n",
        "  configure_nginx\n",
        "  wait_for_backend\n",
    ],
}


def _require_all(source: str, expected: list[str], *, context: str) -> None:
    missing = [value for value in expected if value not in source]
    if missing:
        raise AssertionError(f"{context} is missing: {missing}")


def _require_in_order(source: str, expected: list[str], *, context: str) -> None:
    cursor = 0
    for value in expected:
        position = source.find(value, cursor)
        if position < 0:
            raise AssertionError(f"{context} is missing or out of order: {value}")
        cursor = position + len(value)


def _schema_variable(source: str, name: str) -> str:
    match = re.search(
        rf"(?ms)^  {re.escape(name)}:\n(.*?)(?=^  [a-zA-Z0-9_]+:\n|^outputs:|\Z)",
        source,
    )
    if match is None:
        raise AssertionError(f"schema variable not found: {name}")
    return match.group(0)


def _terraform_variable(source: str, name: str) -> str:
    match = re.search(rf'(?ms)^variable "{re.escape(name)}" \{{.*?^\}}', source)
    if match is None:
        raise AssertionError(f"Terraform variable not found: {name}")
    return match.group(0)


def _schema_section(schema: str, name: str) -> str:
    match = re.search(rf"(?ms)^{name}:\n(.*?)(?=^[a-zA-Z]\w*:|\Z)", schema)
    if match is None:
        raise AssertionError(f"schema section not found: {name}")
    return match.group(1)


def _schema_groups(schema: str) -> dict[str, tuple[str, list[str]]]:
    """group title → (visible の記述, 変数名)。"""
    groups: dict[str, tuple[str, list[str]]] = {}
    section = _schema_section(schema, "variableGroups")
    for block in re.split(r"(?m)^  - title: ", section)[1:]:
        title = block.splitlines()[0].strip().strip('"')
        visible = block.split("    visible:", 1)[1].split("    variables:", 1)[0].strip()
        members = re.findall(r"(?m)^      - ([a-z0-9_]+)$", block)
        groups[title] = (visible, members)
    return groups


def _heredoc(locals_source: str, name: str) -> str:
    match = re.search(rf"(?ms)^  {name} = <<-EOT\n(.*?)^EOT$", locals_source)
    if match is None:
        raise AssertionError(f"locals.tf {name} heredoc not found")
    return match.group(1)


def _settings_fields(product: str) -> set[str]:
    source = SETTINGS_FILES[product].read_text(encoding="utf-8")
    body = source.split("\nclass Settings(", 1)[1]
    return set(re.findall(r"(?m)^    ([a-z][a-z0-9_]*): ", body)) | BASE_SETTINGS_FIELDS


def _verify_package_entries(archive: zipfile.ZipFile) -> None:
    names = set(archive.namelist())
    missing = REQUIRED_ENTRIES - names
    if missing:
        raise AssertionError(f"missing package entries: {sorted(missing)}")
    forbidden = {
        name
        for name in names
        if name in FORBIDDEN_NAMES
        or name.startswith(".terraform/")
        or name.endswith(".tfstate")
        or ".tfstate." in name
        or name.endswith((".tfvars", ".tfvars.json"))
        or (name.startswith("wallet") and name.endswith(".zip"))
    }
    if forbidden:
        raise AssertionError(f"forbidden package entries: {sorted(forbidden)}")
    mode = archive.getinfo("extract_wallet.sh").external_attr >> 16
    if not mode & stat.S_IXUSR:
        raise AssertionError("extract_wallet.sh is not executable in package")


def _verify_schema(schema: str, variables: str) -> None:
    _require_all(
        schema,
        ["schemaVersion: 1.1.0", "Production Ready Suite"],
        context="Resource Manager schema header",
    )
    terraform_names = set(re.findall(r'(?m)^variable "([a-z0-9_]+)" \{', variables))
    schema_names = set(re.findall(r"(?m)^  ([a-z0-9_]+):\n", _schema_section(schema, "variables")))
    if terraform_names - schema_names:
        raise AssertionError(f"Terraform variables missing from schema: {sorted(terraform_names - schema_names)}")
    if schema_names - terraform_names:
        raise AssertionError(f"schema variables missing from Terraform: {sorted(schema_names - terraform_names)}")

    groups = _schema_groups(schema)
    grouped = [name for _, members in groups.values() for name in members]
    duplicated = sorted({name for name in grouped if grouped.count(name) > 1})
    if duplicated:
        raise AssertionError(f"variables listed in multiple groups: {duplicated}")
    ungrouped = sorted((terraform_names - {"region"}) - set(grouped))
    if ungrouped:
        raise AssertionError(f"variables not listed in any variable group: {ungrouped}")

    hidden_visible, hidden = groups.get("Hidden", ("", []))
    if hidden_visible != "false":
        raise AssertionError("hidden Resource Manager variable group not found")
    _require_all("\n".join(hidden), HIDDEN_VARIABLES, context="hidden Resource Manager variables")
    for name in HIDDEN_VARIABLES:
        _require_all(_schema_variable(schema, name), ["visible: false"], context=f"{name} schema")
    for name, default in {"application_git_url": APPLICATION_GIT_URL, "application_git_ref": "main"}.items():
        _require_all(
            _schema_variable(schema, name),
            ["required: true", f'default: "{default}"'],
            context=f"{name} hidden schema",
        )
        _require_all(
            _terraform_variable(variables, name),
            [f'default     = "{default}"'],
            context=f"{name} Terraform default",
        )

    # 配備する製品の選択（複数選択可、既定はすべて配備）。
    _, selection = groups.get("配備する製品", ("", []))
    if selection != [f"deploy_{product}" for product in PRODUCTS]:
        raise AssertionError(f"product selection group must list deploy_* in order: {selection}")
    for product in PRODUCTS:
        _require_all(
            _schema_variable(schema, f"deploy_{product}"),
            ["type: boolean", "required: true", "visible: true", "default: true"],
            context=f"deploy_{product} schema",
        )
        _require_all(
            _terraform_variable(variables, f"deploy_{product}"),
            ["type        = bool", "default     = true"],
            context=f"deploy_{product} Terraform default",
        )
    for title, product in PRODUCT_GROUPS.items():
        visible, members = groups.get(title, ("", []))
        if visible != f"deploy_{product}":
            raise AssertionError(f"{title} group must be visible only when deploy_{product} is selected")
        foreign = [name for name in members if not name.startswith(f"{product}_")]
        if foreign:
            raise AssertionError(f"{title} group must hold only {product}_* variables: {foreign}")
    for product, name in PRODUCT_PASSWORDS.items():
        _require_all(
            _schema_variable(schema, name),
            ["type: password", "required: false", f"visible: deploy_{product}", "confirmation: true", "pattern: '^$|"],
            context=f"{name} schema",
        )
    _require_all(
        _schema_variable(schema, "nl2sql_oracle_deepsec_data_user_password"),
        ["- deploy_nl2sql", "- nl2sql_oracle_deepsec_enabled", "confirmation: true"],
        context="DeepSec password conditional schema",
    )
    compute_visible, compute = groups.get("Compute", ("", []))
    if compute_visible != "true" or any(name.startswith(PRODUCTS) for name in compute):
        raise AssertionError(f"shared Compute group must hold only shared inputs: {compute}")

    _require_in_order(
        schema,
        [
            "- title: \"配備する製品\"",
            "- title: Autonomous AI Database",
            "- adb_compartment_ocid",
            "- adb_deployment_mode",
            "- title: Existing Autonomous AI Database",
            '- title: "ネットワーク・アクセス"',
            "- title: RAG",
            "- title: NL2SQL",
            '- title: "NL2SQL Deep Data Security"',
            "- title: Agent Control Plane",
            "- title: Compute",
            "- subnet_ai_subnet_id",
            "- ssh_authorized_keys",
        ],
        context="Resource Manager variable group order",
    )
    _require_all(
        _schema_variable(schema, "adb_compartment_ocid"),
        ["required: true", 'title: "ADBのコンパートメント"', "default: compartment_ocid"],
        context="adb_compartment_ocid schema",
    )
    _require_all(
        _schema_variable(schema, "adb_deployment_mode"),
        [
            'title: "ADBの利用方法"',
            '- "新規 Autonomous AI Database の作成"',
            '- "既存の Autonomous AI Database を選択"',
            'default: "新規 Autonomous AI Database の作成"',
        ],
        context="adb_deployment_mode schema",
    )
    _require_all(
        _schema_variable(schema, "existing_adb_ocid"),
        ["compartmentId: ${adb_compartment_ocid}"],
        context="existing ADB compartment dependency",
    )
    _require_all(
        _schema_variable(schema, "adb_network_access_type"),
        [f'- "{EVERYWHERE_ACCESS}"', f'- "{ALLOWED_ACCESS}"', f'- "{PRIVATE_ACCESS}"', f'default: "{PRIVATE_ACCESS}"'],
        context="adb_network_access_type schema",
    )
    for name in (
        "adb_private_endpoint_vcn_compartment_id",
        "adb_private_endpoint_subnet_compartment_id",
        "adb_acl_vcn_compartment_id",
        "adb_acl_subnet_compartment_id",
    ):
        _require_all(_schema_variable(schema, name), ["default: compartment_ocid"], context=f"{name} schema")
    _require_all(
        _schema_variable(schema, "adb_subnet_id"),
        ["compartmentId: ${adb_private_endpoint_subnet_compartment_id}", "vcnId: ${adb_private_endpoint_vcn_id}"],
        context="private endpoint subnet dependencies",
    )
    _require_all(
        _schema_variable(schema, "adb_acl_cidr_blocks"),
        ["and:", f'- "{ALLOWED_ACCESS}"', f'- "{IP_OR_CIDR}"'],
        context="ACL IP/CIDR visibility",
    )
    workload = _schema_variable(schema, "adb_workload")
    _require_all(
        workload,
        ['- "OLTP"', '- "AJD"', '- "APEX"', '- "LH"', 'default: "OLTP"'],
        context="adb_workload schema",
    )
    if '- "DW"' in workload:
        raise AssertionError("Resource Manager workload options must not include DW")
    _require_all(
        _schema_variable(schema, "adb_is_mtls_connection_required"),
        ["type: boolean", "visible: false", "default: true"],
        context="mTLS hidden schema",
    )
    _require_all(
        _schema_variable(schema, "ssh_authorized_keys"),
        ["type: oci:core:ssh:publickey", "required: true", "visible: true", 'title: "SSHキーの追加"'],
        context="SSH public key schema",
    )
    if "us-chicago-1" in _schema_variable(schema, "instance_image_source_id"):
        raise AssertionError("Resource Manager Compute image options must not include Chicago")

    outputs = _schema_section(schema, "outputs")
    for product in PRODUCTS:
        block = re.search(rf"(?ms)^  {product}_application_url:\n.*?(?=^  [a-z]|\Z)", outputs)
        if block is None or f"visible: deploy_{product}" not in block.group(0):
            raise AssertionError(f"{product}_application_url output must be shown only for deploy_{product}")


def _verify_terraform(variables: str, adb: str, compute: str, locals_source: str, outputs: str) -> None:
    _require_all(
        _terraform_variable(variables, "adb_workload"),
        ['default     = "OLTP"', 'contains(["OLTP", "AJD", "APEX", "LH"], var.adb_workload)'],
        context="adb_workload",
    )
    _require_all(_terraform_variable(variables, "adb_db_version"), ['default     = "26ai"'], context="adb_db_version")
    for name in SECRET_VARIABLES:
        _require_all(_terraform_variable(variables, name), ["sensitive   = true"], context=f"{name} sensitive flag")
    # DB の接続情報は全製品の backend/.env に入り、RAG の env_file は single quote で囲むため、全製品で single quote を許さない。
    for name in ("adb_password", "existing_oracle_password", "existing_oracle_user", "existing_oracle_dsn"):
        _require_all(
            _terraform_variable(variables, name),
            [f"!can(regex(\"[\\r\\n']\", var.{name}))"],
            context=f"{name} single quote validation",
        )
    _require_all(
        _terraform_variable(variables, "rag_app_login_password"),
        ["!can(regex(\"[\\r\\n\\\"'\\\\\\\\]\", var.rag_app_login_password))"],
        context="rag_app_login_password quote validation",
    )

    _require_all(
        adb,
        [
            f'var.adb_network_access_type == "{PRIVATE_ACCESS}"',
            f'var.adb_network_access_type == "{ALLOWED_ACCESS}"',
            f'var.adb_network_access_type == "{EVERYWHERE_ACCESS}"',
            "subnet_id                                      = local.adb_private_endpoint_enabled ? var.adb_subnet_id : null",
            "whitelisted_ips                                = local.adb_secure_acl_enabled ? local.adb_whitelisted_ips : null",
            "compartment_id                                 = var.adb_compartment_ocid",
            "self.compartment_id == trimspace(var.adb_compartment_ocid)",
            'resource "oci_database_autonomous_database_wallet"',
            # ADB の入力と製品の選択は、常に1つ作る Wallet の precondition で1回だけ検証する。
            "condition     = length(local.selected_products) > 0",
            "プライベート・エンドポイントを作成する場合は、サブネットを選択してください。",
            "data.oci_core_subnet.adb_private_endpoint_subnet[0].vcn_id",
            "existing_adb_ocid, existing_oracle_user, and existing_oracle_password must be configured",
        ],
        context="ADB and product selection",
    )
    if "is_access_control_enabled" in adb:
        raise AssertionError("serverless Autonomous AI Database must not configure is_access_control_enabled")
    if not re.search(
        r"effective_oracle_wallet_password\s*=\s*local\.create_new_adb \? var\.adb_password : var\.existing_oracle_password",
        adb,
    ):
        raise AssertionError("wallet password must reuse the ADB / existing DB password")
    if len(re.findall(r'(?m)^resource "oci_database_autonomous_database" ', adb)) != 1:
        raise AssertionError("the stack must create at most one Autonomous AI Database")

    _require_all(
        compute,
        [
            'resource "oci_core_instance" "product" {',
            "for_each = local.selected_products",
            "compartment_id      = var.compartment_ocid",
            'user_data"           = local.cloud_init_user_data[each.key]',
            'each.key != "rag" || trimspace(var.rag_app_login_password) != ""',
            'each.key != "nl2sql" || trimspace(var.nl2sql_app_admin_login_user_password) != ""',
            'each.key != "nl2sql" || !var.nl2sql_oracle_deepsec_enabled',
            'each.key != "agent" || trimspace(var.agent_app_basic_auth_password) != ""',
        ],
        context="Compute per product",
    )
    if len(re.findall(r'(?m)^resource "oci_core_instance" ', compute)) != 1:
        raise AssertionError("Compute instances must be created by the single for_each resource")

    normalized = re.sub(r"[ \t]+", " ", locals_source)
    _require_all(
        normalized,
        [
            f'wallet_dir_host = "{WALLET_DIR}"',
            "rag = var.deploy_rag",
            "nl2sql = var.deploy_nl2sql",
            "agent = var.deploy_agent",
            "selected_products = toset([for product, enabled in local.product_enabled : product if enabled])",
            "for product in local.selected_products : product =>",
            "product = product",
            "backend_env = base64gzip(local.backend_envs[product])",
            'compose_services = product == "rag" ? join(" ", local.rag_compose_services) : ""',
            'basic_auth_password = product == "agent" ? base64gzip(var.agent_app_basic_auth_password) : ""',
            "application_git_ref = var.application_git_ref",
            "application_git_url = var.application_git_url",
        ],
        context="product selection and cloud-init rendering",
    )
    for product in PRODUCTS:
        _require_all(outputs, [f'output "{product}_application_url"'], context="outputs")

    for product in PRODUCTS:
        env = _heredoc(locals_source, f"{product}_backend_env")
        _require_all(env, REQUIRED_BACKEND_ENV_LINES[product], context=f"{product} backend/.env")
        keys = re.findall(r"(?m)^([A-Z][A-Z0-9_]*)=", env)
        fields = _settings_fields(product)
        unknown = sorted(key for key in keys if key.lower() not in fields)
        if unknown:
            raise AssertionError(f"{product} backend/.env keys are not Settings fields: {unknown}")
        duplicated = sorted({key for key in keys if keys.count(key) > 1})
        if duplicated:
            raise AssertionError(f"{product} backend/.env keys are duplicated: {duplicated}")
        # 他製品の入力が紛れ込んでいないこと。
        foreign = re.findall(rf"var\.((?!{product}_)(?:rag|nl2sql|agent)_[a-z0-9_]+)", env)
        if foreign:
            raise AssertionError(f"{product} backend/.env uses another product's inputs: {sorted(set(foreign))}")

    rag_env = _heredoc(locals_source, "rag_backend_env")
    keys = re.findall(r"(?m)^([A-Z][A-Z0-9_]*)=", rag_env)
    compose_owned = sorted(set(keys) & RAG_COMPOSE_OWNED_ENV_KEYS)
    if compose_owned:
        raise AssertionError(f"RAG backend/.env must not set keys owned by docker-compose.yml: {compose_owned}")
    for line in rag_env.splitlines():
        key, _, value = line.partition("=")
        if re.search(r"\$\{(var\.rag_app_login_|local\.effective_oracle_(user|password|dsn|wallet))", value):
            if not (value.startswith("'") and value.endswith("'")):
                raise AssertionError(f"RAG backend/.env value must be single-quoted: {key}")

    _verify_rag_compose_services(locals_source)


def _verify_rag_compose_services(locals_source: str) -> None:
    match = re.search(r"(?ms)^  rag_compose_services = concat\(\n(.*?)^  \)$", locals_source)
    if match is None:
        raise AssertionError("locals.tf rag_compose_services concat() not found")
    services_local = match.group(1)
    listed = re.findall(r'"([a-z0-9-]+)"', services_local)
    base = re.findall(r'"([a-z0-9-]+)"', services_local.split("],", 1)[0])
    if base != RAG_BASE_COMPOSE_SERVICES:
        raise AssertionError(f"RAG base compose services changed: {base}")
    optional = [name for name in listed if name not in base]
    if sorted(optional) != sorted(RAG_OPTIONAL_COMPOSE_SERVICES):
        raise AssertionError(f"RAG optional compose services changed: {optional}")
    _require_all(
        services_local,
        [
            'var.rag_enable_parser_docling ? ["parser-docling"] : []',
            'var.rag_enable_parser_marker ? ["parser-marker"] : []',
            "var.rag_enable_oci_cloud_parsers ? [",
        ],
        context="RAG optional compose services",
    )
    compose_source = (REPO_ROOT / "rag" / "docker-compose.yml").read_text(encoding="utf-8")
    services = compose_source.split("\nservices:\n", 1)[1].split("\nvolumes:\n", 1)[0]
    blocks = re.split(r"(?m)^  ([a-z0-9-]+):\n", services)
    compose = {blocks[index]: blocks[index + 1] for index in range(1, len(blocks) - 1, 2)}
    missing = sorted(name for name in listed if name not in compose)
    if missing:
        raise AssertionError(f"compose services not defined in rag/docker-compose.yml: {missing}")
    gpu = sorted(
        name
        for name in listed
        if re.search(r'profiles: \[[^\]]*"gpu"', compose[name]) or "nvidia" in compose[name]
    )
    if gpu:
        raise AssertionError(f"the stack must not start GPU services: {gpu}")


def _verify_bootstrap(bootstrap: str) -> None:
    _require_all(
        bootstrap,
        [
            'APP_ROOT="/u01/aipoc"',
            'SUITE_REPO_DIR="$${APP_ROOT}/no.1-production-ready-suite"',
            'APP_REPO_DIR="$${SUITE_REPO_DIR}/${product}"',
            'clone_or_update_repo "${application_git_url}" "${application_git_ref}" "$${SUITE_REPO_DIR}"',
            'bash "$${init_script}"',
            'path: "/u01/aipoc/bootstrap-${product}.sh"',
            "nohup bash /u01/aipoc/bootstrap-${product}.sh",
            "Nginx listens on TCP port",
        ],
        context="Compute bootstrap",
    )
    for secret_path in ("/u01/aipoc/props/backend.env", "/u01/aipoc/props/wallet.zip"):
        if not re.search(
            rf'(?ms)path: "{re.escape(secret_path)}"\n    permissions: "0600"\n    owner: "root:root"\n',
            bootstrap,
        ):
            raise AssertionError(f"{secret_path} must be written root-only (0600)")
    # 製品固有のファイルは、その製品の Compute にだけ書く。
    rag_block = re.search(r'(?ms)^%\{ if product == "rag" ~\}\n(.*?)^%\{ endif ~\}', bootstrap)
    if rag_block is None or 'path: "/u01/aipoc/props/compose_services.txt"' not in rag_block.group(1):
        raise AssertionError("compose_services.txt must be written only on the RAG Compute")
    agent_block = re.search(r'(?ms)^%\{ if product == "agent" ~\}\n(.*?)^%\{ endif ~\}', bootstrap)
    if agent_block is None or not re.search(
        r'(?ms)path: "/u01/aipoc/props/basic_auth_password"\n'
        r'    permissions: "0600"\n    owner: "root:root"\n    encoding: "gzip\+base64"',
        agent_block.group(1),
    ):
        raise AssertionError("basic_auth_password must be written root-only (0600) only on the Agent Compute")
    _require_in_order(
        bootstrap,
        ["configure_firewall\n", "clone_or_update_repo ", "run_application_init\n"],
        context="bootstrap order (firewall rules are saved before Docker is installed)",
    )


def _verify_init_scripts(init_sources: dict[str, str]) -> None:
    for product, source in init_sources.items():
        _require_all(source, INIT_SCRIPT_CONTRACTS[product], context=f"{product}/init_script.sh")
        _require_in_order(source, INIT_SCRIPT_ORDER[product], context=f"{product}/init_script.sh main order")
    rag = init_sources["rag"]
    allowed_block = re.search(r"(?ms)^ALLOWED_COMPOSE_SERVICES=\(\n(.*?)^\)$", rag)
    if allowed_block is None:
        raise AssertionError("rag/init_script.sh ALLOWED_COMPOSE_SERVICES not found")
    if sorted(allowed_block.group(1).split()) != sorted(RAG_BASE_COMPOSE_SERVICES + RAG_OPTIONAL_COMPOSE_SERVICES):
        raise AssertionError("rag/init_script.sh allowed compose services differ from the stack")
    if "auth_basic" in rag:
        raise AssertionError("RAG uses the backend login; Nginx must not add Basic authentication")
    for product in ("nl2sql", "agent"):
        if re.search(r"(?im)^\s*(?:docker|docker-compose)\b", init_sources[product]):
            raise AssertionError(f"{product} is deployed directly with systemd and must not require Docker")


def _verify_boundaries(sources: dict[str, str]) -> None:
    combined = "\n".join(sources.values())
    for name, source in sources.items():
        if "platform_git_" in source or "no.1-production-ready-platform" in source:
            raise AssertionError(f"{name} must not clone the shared platform separately")
    if re.search(r"(?i)\b(CREATE|ALTER|DROP)\s+(TABLE|INDEX|USER)\b|DBMS_CLOUD", combined):
        raise AssertionError("ADB DDL must stay in the application, not in the stack")
    if re.search(r"--profile[ =]gpu|parser-asr|nvidia", combined):
        raise AssertionError("GPU services must not be part of the stack")
    for legacy in (
        "PUBLIC_ENDPOINT",
        "SECURE_ACCESS_FROM_ALLOWED_IPS_AND_VCNS",
        "PRIVATE_ENDPOINT_ONLY",
        "CIDR_BLOCK",
        "adb_use_private_subnet",
    ):
        if legacy in combined:
            raise AssertionError(f"legacy ADB network input remains: {legacy}")


def verify(package_path: Path) -> None:
    with zipfile.ZipFile(package_path) as archive:
        _verify_package_entries(archive)
        sources = {
            name: archive.read(name).decode()
            for name in ("schema.yaml", "variables.tf", "adb.tf", "compute.tf", "locals.tf", "output.tf")
        }
        bootstrap = archive.read("cloud_init/bootstrap.template.yaml").decode()
    init_sources = {
        product: (REPO_ROOT / product / "init_script.sh").read_text(encoding="utf-8") for product in PRODUCTS
    }

    _verify_schema(sources["schema.yaml"], sources["variables.tf"])
    _verify_terraform(
        sources["variables.tf"],
        sources["adb.tf"],
        sources["compute.tf"],
        sources["locals.tf"],
        sources["output.tf"],
    )
    _verify_bootstrap(bootstrap)
    _verify_init_scripts(init_sources)
    _verify_boundaries({**sources, "bootstrap": bootstrap, **{f"{p}/init_script.sh": s for p, s in init_sources.items()}})


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("package", type=Path, help="Path to the packaged OCI Resource Manager stack ZIP.")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(sys.argv[1:] if argv is None else argv)
    verify(args.package)
    print("Suite Terraform stack contract verified.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
