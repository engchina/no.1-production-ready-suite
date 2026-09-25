#!/usr/bin/env python3
"""Agent の OCI Resource Manager stack zip の入力契約と配備契約を検証する。

stdlib だけで動かす（CI と release workflow で追加依存なしに実行するため）。
"""

from __future__ import annotations

import argparse
import re
import stat
import sys
import zipfile
from pathlib import Path

AGENT_DIR = Path(__file__).resolve().parents[1]
PRIVATE_ACCESS = "プライベート・エンドポイント・アクセスのみ"
ALLOWED_ACCESS = "許可されたIPおよびVCN限定のセキュア・アクセス"
EVERYWHERE_ACCESS = "すべての場所からのセキュア・アクセス"
IP_OR_CIDR = "IPアドレスまたはCIDRブロック"
# Agent と共有 platform は monorepo（agent/ と platform/）から1回の clone で取得する。
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
    "agent_runtime_repository_backend",
]

# backend/.env に必ず書く値（Agent backend の Settings と init_script.sh の前提）。
REQUIRED_BACKEND_ENV_LINES = [
    "ORACLE_CLIENT_LIB_DIR=\n",
    "ORACLE_WALLET_DIR=${local.wallet_dir_host}",
    "ORACLE_WALLET_PASSWORD=${local.effective_oracle_wallet_password}",
    "ORACLE_ADB_OCID=${local.effective_adb_ocid}",
    "OCI_COMPARTMENT_ID=${var.compartment_ocid}",
    "AGENT_RUNTIME_REPOSITORY_BACKEND=${var.agent_runtime_repository_backend}",
    "AGENT_RUNTIME_DISPATCH_MODE=in_process",
    "AGENT_RUNTIME_ORACLE_DSN=${local.effective_oracle_dsn}",
    "AGENT_RUNTIME_ORACLE_USER=${local.effective_oracle_user}",
    "AGENT_RUNTIME_ORACLE_PASSWORD=${local.effective_oracle_password}",
    "AGENT_RUNTIME_ORACLE_WALLET_DIR=${local.wallet_dir_host}",
    "AGENT_RUNTIME_ORACLE_WALLET_PASSWORD=${local.effective_oracle_wallet_password}",
    "AGENT_RUNTIME_ORACLE_CREATE_SCHEMA=true",
    "AGENT_RUNTIME_SERVICE_CONTROL_ENABLED=false",
    "AGENT_CONTROL_PLANE_PUBLIC_BASE_URL=",
    "AGENT_CONTROL_PLANE_MCP_TOKEN_SECRET=${var.agent_control_plane_mcp_token_secret}",
]
# pr_backend_core.BaseServiceSettings の共通 field（agent/backend/app/settings.py には無い）。
BASE_SETTINGS_FIELDS = {"app_version", "log_level", "environment", "cors_origins"}


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


def _schema_groups(schema: str) -> dict[str, list[str]]:
    groups: dict[str, list[str]] = {}
    section = _schema_section(schema, "variableGroups")
    for block in re.split(r"(?m)^  - title: ", section)[1:]:
        title = block.splitlines()[0].strip().strip('"')
        members = re.findall(r"(?m)^      - ([a-z0-9_]+)$", block)
        groups[title] = members
    return groups


def _backend_env(locals_source: str) -> str:
    match = re.search(r"(?ms)backend_env = <<-EOT\n(.*?)^EOT$", locals_source)
    if match is None:
        raise AssertionError("locals.tf backend_env heredoc not found")
    return match.group(1)


def _settings_fields() -> set[str]:
    source = (AGENT_DIR / "backend" / "app" / "settings.py").read_text(encoding="utf-8")
    body = source.split("class Settings(", 1)[1]
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
        [
            "schemaVersion: 1.1.0",
            'version: "20260926.1"',
            "Production Ready Agent Control Plane",
        ],
        context="Resource Manager schema header",
    )
    terraform_names = set(re.findall(r'(?m)^variable "([a-z0-9_]+)" \{', variables))
    schema_names = set(re.findall(r"(?m)^  ([a-z0-9_]+):\n", _schema_section(schema, "variables")))
    groups = _schema_groups(schema)
    grouped = [name for members in groups.values() for name in members]
    if terraform_names - schema_names:
        raise AssertionError(
            f"Terraform variables missing from schema: {sorted(terraform_names - schema_names)}"
        )
    if schema_names - terraform_names:
        raise AssertionError(
            f"schema variables missing from Terraform: {sorted(schema_names - terraform_names)}"
        )
    duplicated = sorted({name for name in grouped if grouped.count(name) > 1})
    if duplicated:
        raise AssertionError(f"variables listed in multiple groups: {duplicated}")
    ungrouped = sorted((terraform_names - {"region"}) - set(grouped))
    if ungrouped:
        raise AssertionError(f"variables not listed in any variable group: {ungrouped}")

    hidden = groups.get("Hidden")
    if hidden is None or not re.search(r"(?m)^  - title: Hidden\n    visible: false\n", schema):
        raise AssertionError("hidden Resource Manager variable group not found")
    _require_all(
        "\n".join(hidden), HIDDEN_VARIABLES, context="hidden Resource Manager variables"
    )
    for name in HIDDEN_VARIABLES:
        _require_all(_schema_variable(schema, name), ["visible: false"], context=f"{name} schema")

    for name, default in {
        "application_git_url": APPLICATION_GIT_URL,
        "application_git_ref": "main",
    }.items():
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

    _require_all(
        "\n".join(groups.get("Application Access", [])),
        ["app_basic_auth_user", "app_basic_auth_password"],
        context="Application Access group",
    )
    _require_all(
        _schema_variable(schema, "app_basic_auth_password"),
        ["type: password", "required: true", "visible: true", "confirmation: true"],
        context="Basic authentication password schema",
    )
    _require_all(
        _schema_variable(schema, "agent_control_plane_mcp_token_secret"),
        ["type: password", "required: false"],
        context="Binding MCP token master secret schema",
    )
    _require_in_order(
        schema,
        [
            "- title: Autonomous AI Database",
            "- adb_compartment_ocid",
            "- adb_deployment_mode",
            "- title: Existing Autonomous AI Database",
            '- title: "ネットワーク・アクセス"',
            "- title: Compute",
            "- subnet_ai_subnet_id",
            "- ssh_authorized_keys",
        ],
        context="Resource Manager variable group order",
    )
    _require_all(
        _schema_variable(schema, "adb_compartment_ocid"),
        ["required: true", "default: compartment_ocid"],
        context="adb_compartment_ocid schema",
    )
    _require_all(
        _schema_variable(schema, "existing_adb_ocid"),
        ["compartmentId: ${adb_compartment_ocid}"],
        context="existing ADB compartment dependency",
    )
    _require_all(
        _schema_variable(schema, "adb_network_access_type"),
        [
            f'- "{EVERYWHERE_ACCESS}"',
            f'- "{ALLOWED_ACCESS}"',
            f'- "{PRIVATE_ACCESS}"',
            f'default: "{PRIVATE_ACCESS}"',
        ],
        context="adb_network_access_type schema",
    )
    _require_all(
        _schema_variable(schema, "adb_workload"),
        ['- "OLTP"', 'default: "OLTP"'],
        context="adb_workload schema",
    )
    _require_all(
        _schema_variable(schema, "adb_is_mtls_connection_required"),
        ["type: boolean", "visible: false", "default: true"],
        context="mTLS hidden schema",
    )
    _require_all(
        _schema_variable(schema, "ssh_authorized_keys"),
        ["type: oci:core:ssh:publickey", "required: true"],
        context="SSH public key schema",
    )
    if "us-chicago-1" in _schema_variable(schema, "instance_image_source_id"):
        raise AssertionError("Resource Manager Compute image options must not include Chicago")


def _verify_terraform(variables: str, adb: str, compute: str, locals_source: str) -> None:
    _require_all(
        _terraform_variable(variables, "adb_workload"),
        ['default     = "OLTP"', 'contains(["OLTP", "AJD", "APEX", "LH"], var.adb_workload)'],
        context="adb_workload",
    )
    _require_all(
        _terraform_variable(variables, "adb_name"),
        ['default     = "AGENTADB"'],
        context="adb_name",
    )
    _require_all(
        _terraform_variable(variables, "agent_runtime_repository_backend"),
        ['default     = "oracle_checkpoint"', '"oracle_normalized"'],
        context="agent_runtime_repository_backend",
    )
    for name in (
        "app_basic_auth_password",
        "agent_control_plane_mcp_token_secret",
        "adb_password",
        "existing_oracle_password",
    ):
        _require_all(
            _terraform_variable(variables, name),
            ["sensitive   = true"],
            context=f"{name} sensitive flag",
        )
    _require_all(
        adb,
        [
            f'var.adb_network_access_type == "{PRIVATE_ACCESS}"',
            f'var.adb_network_access_type == "{ALLOWED_ACCESS}"',
            f'var.adb_network_access_type == "{EVERYWHERE_ACCESS}"',
            "compartment_id                                 = var.adb_compartment_ocid",
            "self.compartment_id == trimspace(var.adb_compartment_ocid)",
            'resource "oci_database_autonomous_database_wallet"',
        ],
        context="ADB network mapping",
    )
    _require_all(
        compute,
        [
            "compartment_id      = var.compartment_ocid",
            'trimspace(var.app_basic_auth_password) != ""',
        ],
        context="Compute preconditions",
    )

    backend_env = _backend_env(locals_source)
    _require_all(backend_env, REQUIRED_BACKEND_ENV_LINES, context="backend/.env")
    # terraform fmt が "=" の位置を揃えるため、空白を正規化して比較する。
    _require_all(
        re.sub(r"[ \t]+", " ", locals_source),
        [
            f'wallet_dir_host = "{WALLET_DIR}"',
            'app_repo_dir = "no.1-production-ready-suite/agent"',
            "basic_auth_password = base64gzip(var.app_basic_auth_password)",
            "application_git_ref = var.application_git_ref",
            "application_git_url = var.application_git_url",
        ],
        context="cloud-init rendering",
    )
    settings_fields = _settings_fields()
    env_keys = re.findall(r"(?m)^([A-Z][A-Z0-9_]*)=", backend_env)
    unknown = sorted(key for key in env_keys if key.lower() not in settings_fields)
    if unknown:
        raise AssertionError(f"backend/.env keys are not Agent Settings fields: {unknown}")


def _verify_bootstrap_and_init(bootstrap: str, init_source: str) -> None:
    _require_all(
        bootstrap,
        [
            'APP_ROOT="/u01/aipoc"',
            'SUITE_REPO_DIR="$${APP_ROOT}/no.1-production-ready-suite"',
            'APP_REPO_DIR="$${SUITE_REPO_DIR}/agent"',
            'clone_or_update_repo "${application_git_url}" "${application_git_ref}" "$${SUITE_REPO_DIR}"',
            'bash "$${init_script}"',
            'path: "/u01/aipoc/props/basic_auth_password"',
            "Nginx listens on TCP port",
        ],
        context="direct Compute bootstrap",
    )
    if not re.search(
        r'(?ms)path: "/u01/aipoc/props/basic_auth_password"\n'
        r'    permissions: "0600"\n    owner: "root:root"\n    encoding: "gzip\+base64"',
        bootstrap,
    ):
        raise AssertionError("basic_auth_password must be written root-only (0600) via gzip+base64")
    _require_all(
        init_source,
        [
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
        ],
        context="Agent init_script.sh",
    )
    _require_in_order(
        init_source,
        [
            "  install_runtime_env\n",
            "  install_backend\n",
            "  initialize_database_schema\n",
            "  build_frontend\n",
            "  configure_systemd\n",
            "  configure_basic_auth\n",
            "  configure_nginx\n",
            "  wait_for_backend\n",
        ],
        context="init_script.sh main order",
    )


def _verify_boundaries(sources: dict[str, str]) -> None:
    combined = "\n".join(sources.values())
    for name, source in sources.items():
        if re.search(r"nl2sql", source, re.IGNORECASE):
            raise AssertionError(f"{name} still references NL2SQL")
        if "platform_git_" in source or "no.1-production-ready-platform" in source:
            raise AssertionError(f"{name} must not clone the shared platform separately")
    if re.search(r"(?i)\b(CREATE|ALTER|DROP)\s+(TABLE|INDEX|USER)\b|DBMS_CLOUD", combined):
        raise AssertionError("ADB DDL must stay in the application, not in the stack")
    if re.search(r"(?im)^\s*(?:docker|docker-compose)\b", combined):
        raise AssertionError("direct Compute deployment unexpectedly requires Docker")
    for legacy in ("PUBLIC_ENDPOINT", "PRIVATE_ENDPOINT_ONLY", "CIDR_BLOCK", "adb_use_private_subnet"):
        if legacy in combined:
            raise AssertionError(f"legacy ADB network input remains: {legacy}")


def verify(package_path: Path) -> None:
    with zipfile.ZipFile(package_path) as archive:
        _verify_package_entries(archive)
        schema = archive.read("schema.yaml").decode()
        variables = archive.read("variables.tf").decode()
        adb = archive.read("adb.tf").decode()
        compute = archive.read("compute.tf").decode()
        locals_source = archive.read("locals.tf").decode()
        bootstrap = archive.read("cloud_init/bootstrap.template.yaml").decode()
    init_source = (AGENT_DIR / "init_script.sh").read_text(encoding="utf-8")

    _verify_schema(schema, variables)
    _verify_terraform(variables, adb, compute, locals_source)
    _verify_bootstrap_and_init(bootstrap, init_source)
    _verify_boundaries(
        {
            "schema.yaml": schema,
            "variables.tf": variables,
            "adb.tf": adb,
            "compute.tf": compute,
            "locals.tf": locals_source,
            "bootstrap": bootstrap,
            "init_script.sh": init_source,
        }
    )


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("package", type=Path, help="Path to the packaged OCI Resource Manager stack ZIP.")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(sys.argv[1:] if argv is None else argv)
    verify(args.package)
    print("Agent Terraform stack contract verified.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
