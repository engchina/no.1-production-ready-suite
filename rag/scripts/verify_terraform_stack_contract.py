#!/usr/bin/env python3
"""RAG の OCI Resource Manager stack zip の入力契約と配備契約を検証する。

stdlib だけで動かす（CI と release workflow で追加依存なしに実行するため）。
"""

from __future__ import annotations

import argparse
import re
import stat
import sys
import zipfile
from pathlib import Path

RAG_DIR = Path(__file__).resolve().parents[1]
PRIVATE_ACCESS = "プライベート・エンドポイント・アクセスのみ"
ALLOWED_ACCESS = "許可されたIPおよびVCN限定のセキュア・アクセス"
EVERYWHERE_ACCESS = "すべての場所からのセキュア・アクセス"
# RAG と共有 platform は monorepo（rag/ と platform/）から1回の clone で取得する。
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
]
SECRET_VARIABLES = [
    "app_login_password",
    "adb_password",
    "existing_oracle_password",
    "existing_oracle_wallet_password",
]

# stack が常に build・起動する compose の service（CPU だけ）と、任意で足せる service。
BASE_COMPOSE_SERVICES = [
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
OPTIONAL_COMPOSE_SERVICES = [
    "parser-docling",
    "parser-marker",
    "parser-oci-genai-vision",
    "parser-oci-document-understanding",
]

# backend/.env（compose の env_file）に必ず書く値。値が入力由来のものは single quote で囲む。
REQUIRED_BACKEND_ENV_LINES = [
    "AUTH_MODE=production\n",
    "AUTH_USERNAME='${var.app_login_user}'\n",
    "AUTH_PASSWORD='${var.app_login_password}'\n",
    "AUTH_SESSION_SECRET=\n",
    "AUTH_COOKIE_SECURE=${var.app_auth_cookie_secure}\n",
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
]
# docker-compose.yml の environment が正本の key。env_file に書くと compose の値に隠れて誤解を招く。
COMPOSE_OWNED_ENV_KEYS = {
    "ENVIRONMENT",
    "LOCAL_STORAGE_DIR",
    "MODEL_SETTINGS_FILE",
    "OCI_CONFIG_FILE",
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


def _compose_services_local(locals_source: str) -> str:
    match = re.search(r"(?ms)^  compose_services = concat\(\n(.*?)^  \)$", locals_source)
    if match is None:
        raise AssertionError("locals.tf compose_services concat() not found")
    return match.group(1)


def _settings_fields() -> set[str]:
    source = (RAG_DIR / "backend" / "app" / "config.py").read_text(encoding="utf-8")
    body = source.split("\nclass Settings(", 1)[1]
    return set(re.findall(r"(?m)^    ([a-z][a-z0-9_]*): ", body))


def _docker_compose_services() -> dict[str, str]:
    """rag/docker-compose.yml の service 名 → 定義（次の service までの本文）。"""
    source = (RAG_DIR / "docker-compose.yml").read_text(encoding="utf-8")
    services = source.split("\nservices:\n", 1)[1].split("\nvolumes:\n", 1)[0]
    blocks = re.split(r"(?m)^  ([a-z0-9-]+):\n", services)
    return {blocks[index]: blocks[index + 1] for index in range(1, len(blocks) - 1, 2)}


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
            "Production Ready RAG",
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
    _require_all("\n".join(hidden), HIDDEN_VARIABLES, context="hidden Resource Manager variables")
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
        ["app_login_user", "app_login_password", "app_auth_cookie_secure"],
        context="Application Access group",
    )
    _require_all(
        _schema_variable(schema, "app_login_password"),
        ["type: password", "required: true", "visible: true", "confirmation: true"],
        context="login password schema",
    )
    _require_all(
        "\n".join(groups.get("Document Parsers", [])),
        ["enable_parser_docling", "enable_parser_marker", "enable_oci_cloud_parsers"],
        context="Document Parsers group",
    )
    for name, default in {
        "enable_parser_docling": "true",
        "enable_parser_marker": "false",
        "enable_oci_cloud_parsers": "false",
    }.items():
        _require_all(
            _schema_variable(schema, name),
            ["type: boolean", f"default: {default}"],
            context=f"{name} schema",
        )
        _require_all(
            _terraform_variable(variables, name),
            ["type        = bool", f"default     = {default}"],
            context=f"{name} Terraform default",
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
        _schema_variable(schema, "adb_name"),
        ['default: "RAGADB"'],
        context="adb_name schema",
    )
    _require_all(
        _schema_variable(schema, "adb_workload"),
        ['- "OLTP"', 'default: "OLTP"'],
        context="adb_workload schema",
    )
    _require_all(
        _schema_variable(schema, "adb_db_version"),
        ['default: "26ai"'],
        context="adb_db_version schema",
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
        ['default     = "RAGADB"'],
        context="adb_name",
    )
    _require_all(
        _terraform_variable(variables, "adb_db_version"),
        ['default     = "26ai"'],
        context="adb_db_version",
    )
    for name in SECRET_VARIABLES:
        _require_all(
            _terraform_variable(variables, name),
            ["sensitive   = true"],
            context=f"{name} sensitive flag",
        )
    # compose の env_file は single quote の中を文字どおりに渡すため、入力に single quote を許さない。
    for name in ("adb_password", "existing_oracle_password", "existing_oracle_user", "existing_oracle_dsn"):
        _require_all(
            _terraform_variable(variables, name),
            [f"!can(regex(\"[\\r\\n']\", var.{name}))"],
            context=f"{name} single quote validation",
        )
    _require_all(
        _terraform_variable(variables, "app_login_password"),
        ["!can(regex(\"[\\r\\n\\\"'\\\\\\\\]\", var.app_login_password))"],
        context="app_login_password quote validation",
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
            'trimspace(var.app_login_password) != ""',
        ],
        context="Compute preconditions",
    )

    backend_env = _backend_env(locals_source)
    _require_all(backend_env, REQUIRED_BACKEND_ENV_LINES, context="backend/.env")
    settings_fields = _settings_fields()
    env_keys = re.findall(r"(?m)^([A-Z][A-Z0-9_]*)=", backend_env)
    unknown = sorted(key for key in env_keys if key.lower() not in settings_fields)
    if unknown:
        raise AssertionError(f"backend/.env keys are not RAG Settings fields: {unknown}")
    compose_owned = sorted(set(env_keys) & COMPOSE_OWNED_ENV_KEYS)
    if compose_owned:
        raise AssertionError(f"backend/.env must not set keys owned by docker-compose.yml: {compose_owned}")
    duplicated = sorted({key for key in env_keys if env_keys.count(key) > 1})
    if duplicated:
        raise AssertionError(f"backend/.env keys are duplicated: {duplicated}")
    # 入力由来の文字列（${var.*} / ${local.effective_*}）は single quote で囲む（数値・bool・OCID は除く）。
    for line in backend_env.splitlines():
        key, _, value = line.partition("=")
        if re.search(r"\$\{(var\.app_login_|local\.effective_oracle_(user|password|dsn|wallet))", value):
            if not (value.startswith("'") and value.endswith("'")):
                raise AssertionError(f"backend/.env value must be single-quoted: {key}")

    _verify_compose_services(locals_source)

    # terraform fmt が "=" の位置を揃えるため、空白を正規化して比較する。
    _require_all(
        re.sub(r"[ \t]+", " ", locals_source),
        [
            f'wallet_dir_host = "{WALLET_DIR}"',
            'app_repo_dir = "no.1-production-ready-suite/rag"',
            'compose_services = join(" ", local.compose_services)',
            "backend_env = base64gzip(local.backend_env)",
            "application_git_ref = var.application_git_ref",
            "application_git_url = var.application_git_url",
        ],
        context="cloud-init rendering",
    )


def _verify_compose_services(locals_source: str) -> None:
    services_local = _compose_services_local(locals_source)
    listed = re.findall(r'"([a-z0-9-]+)"', services_local)
    base_block = services_local.split("],", 1)[0]
    base = re.findall(r'"([a-z0-9-]+)"', base_block)
    if base != BASE_COMPOSE_SERVICES:
        raise AssertionError(f"base compose services changed: {base}")
    optional = [name for name in listed if name not in base]
    if sorted(optional) != sorted(OPTIONAL_COMPOSE_SERVICES):
        raise AssertionError(f"optional compose services changed: {optional}")
    _require_all(
        services_local,
        [
            'var.enable_parser_docling ? ["parser-docling"] : []',
            'var.enable_parser_marker ? ["parser-marker"] : []',
            "var.enable_oci_cloud_parsers ? [",
        ],
        context="optional compose services",
    )

    compose = _docker_compose_services()
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


def _verify_bootstrap_and_init(bootstrap: str, init_source: str) -> None:
    _require_all(
        bootstrap,
        [
            'APP_ROOT="/u01/aipoc"',
            'SUITE_REPO_DIR="$${APP_ROOT}/no.1-production-ready-suite"',
            'APP_REPO_DIR="$${SUITE_REPO_DIR}/rag"',
            'clone_or_update_repo "${application_git_url}" "${application_git_ref}" "$${SUITE_REPO_DIR}"',
            'bash "$${init_script}"',
            'path: "/u01/aipoc/props/compose_services.txt"',
            "${compose_services}",
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
    _require_in_order(
        bootstrap,
        ["configure_firewall\n", "clone_or_update_repo ", "run_application_init\n"],
        context="bootstrap order (firewall rules are saved before Docker is installed)",
    )
    _require_all(
        init_source,
        [
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
        context="RAG init_script.sh",
    )
    allowed_block = re.search(r"(?ms)^ALLOWED_COMPOSE_SERVICES=\(\n(.*?)^\)$", init_source)
    if allowed_block is None:
        raise AssertionError("init_script.sh ALLOWED_COMPOSE_SERVICES not found")
    allowed = allowed_block.group(1).split()
    if sorted(allowed) != sorted(BASE_COMPOSE_SERVICES + OPTIONAL_COMPOSE_SERVICES):
        raise AssertionError(f"init_script.sh allowed compose services differ from the stack: {allowed}")
    _require_in_order(
        init_source,
        [
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
        context="init_script.sh main order",
    )
    if "auth_basic" in init_source:
        raise AssertionError("RAG uses the backend login; Nginx must not add Basic authentication")


def _verify_boundaries(sources: dict[str, str]) -> None:
    combined = "\n".join(sources.values())
    for name, source in sources.items():
        if re.search(r"nl2sql", source, re.IGNORECASE):
            raise AssertionError(f"{name} still references NL2SQL")
        if re.search(r"production-ready-agent|AGENT_[A-Z_]+=|AGENTADB|agent_control_plane", source):
            raise AssertionError(f"{name} still references the Agent stack")
        if "platform_git_" in source or "no.1-production-ready-platform" in source:
            raise AssertionError(f"{name} must not clone the shared platform separately")
    if re.search(r"(?i)\b(CREATE|ALTER|DROP)\s+(TABLE|INDEX|USER)\b|DBMS_CLOUD", combined):
        raise AssertionError("ADB DDL must stay in the application, not in the stack")
    if re.search(r"--profile[ =]gpu|parser-asr|nvidia", combined):
        raise AssertionError("GPU services must not be part of the stack")
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
    init_source = (RAG_DIR / "init_script.sh").read_text(encoding="utf-8")

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
    print("RAG Terraform stack contract verified.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
