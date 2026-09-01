#!/usr/bin/env python3
"""OCI Resource Manager stack の ADB 入力契約を検証する。"""

from __future__ import annotations

import argparse
import re
import sys
import zipfile
from pathlib import Path

PRIVATE_ACCESS = "プライベート・エンドポイント・アクセスのみ"
ALLOWED_ACCESS = "許可されたIPおよびVCN限定のセキュア・アクセス"
EVERYWHERE_ACCESS = "すべての場所からのセキュア・アクセス"
IP_OR_CIDR = "IPアドレスまたはCIDRブロック"
APPLICATION_GIT_URL = "https://github.com/engchina/no.1-production-ready-nl2sql.git"
PLATFORM_GIT_URL = "https://github.com/engchina/no.1-production-ready-platform.git"
CHICAGO_COMPUTE_IMAGE = (
    "ocid1.image.oc1.us-chicago-1.aaaaaaaal25tbfrlwhh27tzgiatqr3oq5y3qzz7wgpezjouvjk2cdfdr4mnq"
)


def _schema_variable(source: str, name: str) -> str:
    match = re.search(
        rf"(?ms)^  {re.escape(name)}:\n(.*?)(?=^  [a-zA-Z0-9_]+:\n|\Z)",
        source,
    )
    if match is None:
        raise AssertionError(f"schema variable not found: {name}")
    return match.group(0)


def _terraform_variable(source: str, name: str) -> str:
    match = re.search(
        rf'(?ms)^variable "{re.escape(name)}" \{{.*?^\}}',
        source,
    )
    if match is None:
        raise AssertionError(f"Terraform variable not found: {name}")
    return match.group(0)


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


def verify(package_path: Path) -> None:
    with zipfile.ZipFile(package_path) as archive:
        schema = archive.read("schema.yaml").decode()
        variables = archive.read("variables.tf").decode()
        adb = archive.read("adb.tf").decode()
        compute = archive.read("compute.tf").decode()
        locals_source = archive.read("locals.tf").decode()
        bootstrap = archive.read("cloud_init/bootstrap.template.yaml").decode()

    init_source = (Path(__file__).resolve().parents[1] / "init_script.sh").read_text(
        encoding="utf-8"
    )

    _require_all(
        schema,
        [
            'version: "20260901.2"',
            '- title: "ネットワーク・アクセス"',
            "- adb_private_endpoint_vcn_compartment_id",
            "- adb_private_endpoint_vcn_id",
            "- adb_private_endpoint_subnet_compartment_id",
            "- adb_acl_vcn_compartment_id",
            "- adb_acl_subnet_compartment_id",
            "- oracle_deepsec_enabled",
            "- ssh_authorized_keys",
        ],
        context="Resource Manager schema",
    )
    _require_in_order(
        schema,
        [
            '- title: "ネットワーク・アクセス"',
            "- title: Deep Data Security",
            "- title: Compute",
        ],
        context="Resource Manager variable group order",
    )
    if not re.search(
        r"(?m)^  - title: Deep Data Security\n"
        r"    visible: true\n"
        r"    variables:\n"
        r"      - oracle_deepsec_enabled\n"
        r"      - oracle_deepsec_data_user_password\n\n"
        r"  - title: Compute$",
        schema,
    ):
        raise AssertionError(
            "Deep Data Security variable group must be immediately before Compute"
        )
    hidden_group = re.search(
        r"(?ms)^  - title: Hidden\n    visible: false\n    variables:\n(.*?)(?=^  - title: )",
        schema,
    )
    if hidden_group is None:
        raise AssertionError("hidden Resource Manager variable group not found")
    _require_all(
        hidden_group.group(0),
        [
            "- application_git_url",
            "- application_git_ref",
            "- platform_git_url",
            "- platform_git_ref",
            "- existing_oracle_wallet_password",
            "- adb_is_mtls_connection_required",
        ],
        context="hidden Resource Manager variables",
    )
    application_source_group = re.search(
        r"(?ms)^  - title: Application Source\n"
        r"    visible: true\n"
        r"    variables:\n"
        r"(.*?)(?=^  - title: )",
        schema,
    )
    if application_source_group is None:
        raise AssertionError("Application Source variable group not found")
    _require_all(
        application_source_group.group(0),
        ["- application_port"],
        context="visible Application Source variables",
    )
    visible_git_variables = [
        name
        for name in (
            "application_git_url",
            "application_git_ref",
            "platform_git_url",
            "platform_git_ref",
        )
        if f"- {name}" in application_source_group.group(0)
    ]
    if visible_git_variables:
        raise AssertionError(
            f"Git source variables remain in the visible group: {visible_git_variables}"
        )
    compute_group = re.search(
        r"(?ms)^  - title: Compute\n"
        r"    visible: true\n"
        r"    variables:\n"
        r"(.*?)(?=^  - title: |^variables:)",
        schema,
    )
    if compute_group is None:
        raise AssertionError("Compute variable group not found")
    _require_in_order(
        compute_group.group(0),
        [
            "- subnet_ai_subnet_id",
            "- ssh_authorized_keys",
        ],
        context="Compute SSH key field order",
    )

    git_source_defaults = {
        "application_git_url": APPLICATION_GIT_URL,
        "application_git_ref": "main",
        "platform_git_url": PLATFORM_GIT_URL,
        "platform_git_ref": "main",
    }
    for name, default in git_source_defaults.items():
        schema_block = _schema_variable(schema, name)
        _require_all(
            schema_block,
            ["required: true", "visible: false", f'default: "{default}"'],
            context=f"{name} hidden schema",
        )
        terraform_block = _terraform_variable(variables, name)
        _require_all(
            terraform_block,
            [f'default     = "{default}"'],
            context=f"{name} Terraform default",
        )
    _require_in_order(
        schema,
        [
            "- title: Autonomous AI Database",
            "- adb_compartment_ocid",
            "- adb_deployment_mode",
            "- title: Existing Autonomous AI Database",
        ],
        context="ADB selection field order",
    )

    adb_compartment_schema = _schema_variable(schema, "adb_compartment_ocid")
    _require_all(
        adb_compartment_schema,
        [
            "required: true",
            'title: "ADBのコンパートメント"',
            "default: compartment_ocid",
        ],
        context="adb_compartment_ocid schema",
    )
    deployment_mode_schema = _schema_variable(schema, "adb_deployment_mode")
    _require_all(
        deployment_mode_schema,
        [
            'title: "ADBの利用方法"',
            '- "新規 Autonomous AI Database の作成"',
            '- "既存の Autonomous AI Database を選択"',
            'default: "新規 Autonomous AI Database の作成"',
        ],
        context="adb_deployment_mode schema",
    )
    existing_adb_schema = _schema_variable(schema, "existing_adb_ocid")
    _require_all(
        existing_adb_schema,
        ["compartmentId: ${adb_compartment_ocid}"],
        context="existing ADB compartment dependency",
    )
    existing_wallet_schema = _schema_variable(schema, "existing_oracle_wallet_password")
    _require_all(
        existing_wallet_schema,
        [
            "visible: false",
            "Existing ADB wallet generation reuses the existing DB password.",
        ],
        context="existing wallet password hidden schema",
    )

    workload_schema = _schema_variable(schema, "adb_workload")
    _require_all(
        workload_schema,
        [
            '- "OLTP"',
            '- "AJD"',
            '- "APEX"',
            '- "LH"',
            'default: "LH"',
        ],
        context="adb_workload schema",
    )
    if '- "DW"' in workload_schema:
        raise AssertionError("Resource Manager workload options must not include DW")

    access_schema = _schema_variable(schema, "adb_network_access_type")
    _require_all(
        access_schema,
        [
            'title: "アクセス・タイプ"',
            f'- "{EVERYWHERE_ACCESS}"',
            f'- "{ALLOWED_ACCESS}"',
            f'- "{PRIVATE_ACCESS}"',
            f'default: "{PRIVATE_ACCESS}"',
        ],
        context="adb_network_access_type schema",
    )

    for name in (
        "adb_private_endpoint_vcn_compartment_id",
        "adb_private_endpoint_vcn_id",
        "adb_private_endpoint_subnet_compartment_id",
        "adb_subnet_id",
    ):
        block = _schema_variable(schema, name)
        _require_all(
            block,
            ["required: true", f'- "{PRIVATE_ACCESS}"'],
            context=f"{name} schema",
        )
    for name in (
        "adb_private_endpoint_vcn_compartment_id",
        "adb_private_endpoint_subnet_compartment_id",
        "adb_acl_vcn_compartment_id",
        "adb_acl_subnet_compartment_id",
    ):
        block = _schema_variable(schema, name)
        _require_all(
            block,
            ["default: compartment_ocid"],
            context=f"{name} current compartment default",
        )

    private_vcn_schema = _schema_variable(schema, "adb_private_endpoint_vcn_id")
    _require_all(
        private_vcn_schema,
        ["compartmentId: ${adb_private_endpoint_vcn_compartment_id}"],
        context="private endpoint VCN dependency",
    )
    private_subnet_schema = _schema_variable(schema, "adb_subnet_id")
    _require_all(
        private_subnet_schema,
        [
            "compartmentId: ${adb_private_endpoint_subnet_compartment_id}",
            "vcnId: ${adb_private_endpoint_vcn_id}",
        ],
        context="private endpoint subnet dependencies",
    )

    notation_schema = _schema_variable(schema, "adb_acl_notation_type")
    _require_all(
        notation_schema,
        [f'- "{ALLOWED_ACCESS}"', f'- "{IP_OR_CIDR}"'],
        context="ACL notation schema",
    )
    cidr_schema = _schema_variable(schema, "adb_acl_cidr_blocks")
    _require_all(
        cidr_schema,
        ["and:", f'- "{ALLOWED_ACCESS}"', f'- "{IP_OR_CIDR}"'],
        context="ACL IP/CIDR visibility",
    )
    acl_vcn_schema = _schema_variable(schema, "adb_acl_vcn_id")
    _require_all(
        acl_vcn_schema,
        ["and:", f'- "{ALLOWED_ACCESS}"', '- "VCN"'],
        context="ACL VCN visibility",
    )
    deepsec_enabled_schema = _schema_variable(schema, "oracle_deepsec_enabled")
    _require_all(
        deepsec_enabled_schema,
        [
            "type: boolean",
            "required: true",
            "visible: true",
            'title: "ORACLE_DEEPSEC_ENABLED"',
            "default: true",
        ],
        context="DeepSec enabled schema",
    )
    deepsec_password_schema = _schema_variable(schema, "oracle_deepsec_data_user_password")
    _require_all(
        deepsec_password_schema,
        [
            "type: password",
            "required: false",
            "visible:",
            "eq:",
            "- oracle_deepsec_enabled",
            "- true",
            'title: "DeepSec DATA USER password"',
        ],
        context="DeepSec password conditional schema",
    )
    mtls_schema = _schema_variable(schema, "adb_is_mtls_connection_required")
    _require_all(
        mtls_schema,
        [
            "type: boolean",
            "required: true",
            "visible: false",
            "default: true",
        ],
        context="mTLS hidden schema",
    )
    compute_image_schema = _schema_variable(schema, "instance_image_source_id")
    if (
        CHICAGO_COMPUTE_IMAGE in compute_image_schema
        or "us-chicago-1" in compute_image_schema
    ):
        raise AssertionError("Resource Manager Compute image options must not include Chicago")
    ssh_schema = _schema_variable(schema, "ssh_authorized_keys")
    _require_all(
        ssh_schema,
        [
            "type: oci:core:ssh:publickey",
            "required: true",
            "visible: true",
            'title: "SSHキーの追加"',
            "SSHキーペアを自動生成するか、既存の公開キーをアップロードまたは貼り付けて",
        ],
        context="SSH public key Resource Manager schema",
    )

    workload_variable = _terraform_variable(variables, "adb_workload")
    _require_all(
        workload_variable,
        [
            'default     = "LH"',
            'contains(["OLTP", "AJD", "APEX", "LH"], var.adb_workload)',
            "adb_workload must be one of OLTP, AJD, APEX, or LH.",
        ],
        context="adb_workload",
    )
    if '"DW"' in workload_variable:
        raise AssertionError("Terraform adb_workload validation must not accept DW")
    access_variable = _terraform_variable(variables, "adb_network_access_type")
    _require_all(
        access_variable,
        [
            f'default     = "{PRIVATE_ACCESS}"',
            f'"{EVERYWHERE_ACCESS}"',
            f'"{ALLOWED_ACCESS}"',
            f'"{PRIVATE_ACCESS}"',
        ],
        context="adb_network_access_type",
    )
    notation_variable = _terraform_variable(variables, "adb_acl_notation_type")
    _require_all(notation_variable, [f'"{IP_OR_CIDR}"'], context="adb_acl_notation_type")
    adb_compartment_variable = _terraform_variable(variables, "adb_compartment_ocid")
    _require_all(
        adb_compartment_variable,
        [
            'default     = ""',
            'regex("^ocid1\\\\.compartment\\\\."',
            "ADBのコンパートメントを選択し、有効なCompartment OCIDを指定してください。",
        ],
        context="adb_compartment_ocid",
    )

    if not re.search(
        r"effective_oracle_wallet_password\s*=\s*"
        r"local\.create_new_adb \? var\.adb_password : var\.existing_oracle_password",
        adb,
    ):
        raise AssertionError("ADB network mapping is missing effective wallet password mapping")
    _require_all(
        adb,
        [
            f'var.adb_network_access_type == "{PRIVATE_ACCESS}"',
            f'var.adb_network_access_type == "{ALLOWED_ACCESS}"',
            f'var.adb_network_access_type == "{EVERYWHERE_ACCESS}"',
            "subnet_id                                      = "
            "local.adb_private_endpoint_enabled ? var.adb_subnet_id : null",
            "whitelisted_ips                                = "
            "local.adb_secure_acl_enabled ? local.adb_whitelisted_ips : null",
            "compartment_id                                 = var.adb_compartment_ocid",
            "self.compartment_id == trimspace(var.adb_compartment_ocid)",
            "選択した既存のAutonomous AI Databaseは、"
            "ADBのコンパートメントに属している必要があります。",
        ],
        context="ADB network mapping",
    )
    if "is_access_control_enabled" in adb:
        raise AssertionError(
            "serverless Autonomous AI Database must not configure is_access_control_enabled",
        )
    if "trimspace(var.existing_oracle_wallet_password)" in adb:
        raise AssertionError("existing ADB wallet password must not override DB password")
    if "compartment_id                                 = var.compartment_ocid" in adb:
        raise AssertionError("new ADB still uses the deployment compartment")
    _require_all(
        compute,
        ["compartment_id      = var.compartment_ocid"],
        context="Compute deployment compartment",
    )
    _require_all(
        locals_source,
        [
            "OCI_COMPARTMENT_ID=${var.compartment_ocid}",
            "ORACLE_WALLET_DIR=${local.wallet_dir_host}",
            "ORACLE_DEEPSEC_ENABLED=${var.oracle_deepsec_enabled}",
            "ORACLE_DEEPSEC_DATA_USER_PASSWORD=${var.oracle_deepsec_enabled ? "
            'var.oracle_deepsec_data_user_password : ""}',
            'wallet_dir_host   = "/u01/aipoc/wallet"',
            "application_git_ref = var.application_git_ref",
            "application_git_url = var.application_git_url",
            "platform_git_ref    = var.platform_git_ref",
            "platform_git_url    = var.platform_git_url",
        ],
        context="runtime deployment compartment",
    )
    _require_all(
        bootstrap,
        [
            'APP_ROOT="/u01/aipoc"',
            'run_application_init',
            'bash "$${init_script}"',
            'clone_or_update_repo "${application_git_url}" "${application_git_ref}"',
            'clone_or_update_repo "${platform_git_url}" "${platform_git_ref}"',
            "Nginx listens on TCP port",
        ],
        context="direct Compute bootstrap",
    )
    _require_all(
        init_source,
        [
            'WALLET_DIR="${APP_ROOT}/wallet"',
            'chown "root:${APP_GROUP}" "${APP_ROOT}"',
            'chmod 0775 "${APP_ROOT}"',
            'install -d -m 0700 -o "${APP_USER}" -g "${APP_GROUP}" "${WALLET_DIR}"',
            'find "${WALLET_DIR}" -type f -exec chmod 0600 {} \\;',
            "app.cli.nl2sql_system_schema --initialize",
            "app.cli.app_security_migrate --apply --skip-bootstrap",
            'if [ "${DATABASE_INITIALIZATION_READY}" = "true" ]; then',
            "systemctl enable --now \\",
            "systemctl disable --now \\",
            "production-ready-nl2sql-schema-refresh-worker.service",
            "production-ready-nl2sql-quality-evaluation-worker.service",
            "production-ready-nl2sql-ontology-worker.service",
        ],
        context="application deployment initialization",
    )
    _require_in_order(
        init_source,
        [
            "app.cli.nl2sql_system_schema --initialize",
            "app.cli.app_security_migrate --apply --skip-bootstrap",
        ],
        context="database initialization order",
    )
    _require_all(
        compute,
        [
            "!var.oracle_deepsec_enabled || "
            'trimspace(var.oracle_deepsec_data_user_password) != ""',
            "oracle_deepsec_data_user_password must be configured when "
            "oracle_deepsec_enabled=true.",
            "プライベート・エンドポイントを作成する場合は、VCNのコンパートメントを選択してください。",
            "プライベート・エンドポイントを作成する場合は、仮想クラウド・ネットワークを選択してください。",
            "プライベート・エンドポイントを作成する場合は、サブネットのコンパートメントを選択してください。",
            "プライベート・エンドポイントを作成する場合は、サブネットを選択してください。",
            "data.oci_core_subnet.adb_private_endpoint_subnet[0].vcn_id",
            IP_OR_CIDR,
        ],
        context="ADB network preconditions",
    )
    deepsec_enabled_variable = _terraform_variable(variables, "oracle_deepsec_enabled")
    _require_all(
        deepsec_enabled_variable,
        [
            "type        = bool",
            "default     = true",
        ],
        context="oracle_deepsec_enabled variable",
    )
    deepsec_password_variable = _terraform_variable(
        variables, "oracle_deepsec_data_user_password"
    )
    _require_all(
        deepsec_password_variable,
        [
            'default     = ""',
            'trimspace(var.oracle_deepsec_data_user_password) == ""',
            "oracle_deepsec_data_user_password must be empty or 12-256 characters",
        ],
        context="oracle_deepsec_data_user_password variable",
    )

    forbidden = [
        "PUBLIC_ENDPOINT",
        "SECURE_ACCESS_FROM_ALLOWED_IPS_AND_VCNS",
        "PRIVATE_ENDPOINT_ONLY",
        "CIDR_BLOCK",
        "adb_use_private_subnet",
    ]
    stack_contract = "\n".join(
        [schema, variables, adb, compute, locals_source, bootstrap, init_source]
    )
    found = [value for value in forbidden if value in stack_contract]
    if found:
        raise AssertionError(f"legacy ADB network inputs remain: {found}")
    if re.search(r"(?im)^\s*(?:docker|docker-compose)\b", bootstrap + "\n" + init_source):
        raise AssertionError("direct Compute deployment unexpectedly requires Docker")


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "package",
        type=Path,
        help="Path to the packaged OCI Resource Manager stack ZIP.",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(sys.argv[1:] if argv is None else argv)
    verify(args.package)
    print("Terraform ADB stack contract verified.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
