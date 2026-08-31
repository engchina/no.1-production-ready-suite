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

    _require_all(
        schema,
        [
            'version: "20260831.1"',
            '- title: "ネットワーク・アクセス"',
            "- adb_private_endpoint_vcn_compartment_id",
            "- adb_private_endpoint_vcn_id",
            "- adb_private_endpoint_subnet_compartment_id",
            "- adb_acl_vcn_compartment_id",
            "- adb_acl_subnet_compartment_id",
        ],
        context="Resource Manager schema",
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
        ["required: true", 'title: "ADBのコンパートメント"'],
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

    workload_schema = _schema_variable(schema, "adb_workload")
    _require_all(workload_schema, ['default: "LH"'], context="adb_workload schema")

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

    workload_variable = _terraform_variable(variables, "adb_workload")
    _require_all(workload_variable, ['default     = "LH"'], context="adb_workload")
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

    _require_all(
        adb,
        [
            f'var.adb_network_access_type == "{PRIVATE_ACCESS}"',
            f'var.adb_network_access_type == "{ALLOWED_ACCESS}"',
            f'var.adb_network_access_type == "{EVERYWHERE_ACCESS}"',
            "is_access_control_enabled                      = local.adb_secure_acl_enabled",
            "subnet_id                                      = local.adb_private_endpoint_enabled ? var.adb_subnet_id : null",
            "whitelisted_ips                                = local.adb_secure_acl_enabled ? local.adb_whitelisted_ips : null",
            "compartment_id                                 = var.adb_compartment_ocid",
            "self.compartment_id == trimspace(var.adb_compartment_ocid)",
            "選択した既存のAutonomous AI Databaseは、ADBのコンパートメントに属している必要があります。",
        ],
        context="ADB network mapping",
    )
    if "compartment_id                                 = var.compartment_ocid" in adb:
        raise AssertionError("new ADB still uses the deployment compartment")
    _require_all(
        compute,
        ["compartment_id      = var.compartment_ocid"],
        context="Compute deployment compartment",
    )
    _require_all(
        locals_source,
        ["OCI_COMPARTMENT_ID=${var.compartment_ocid}"],
        context="runtime deployment compartment",
    )
    _require_all(
        compute,
        [
            "プライベート・エンドポイントを作成する場合は、VCNのコンパートメントを選択してください。",
            "プライベート・エンドポイントを作成する場合は、仮想クラウド・ネットワークを選択してください。",
            "プライベート・エンドポイントを作成する場合は、サブネットのコンパートメントを選択してください。",
            "プライベート・エンドポイントを作成する場合は、サブネットを選択してください。",
            "data.oci_core_subnet.adb_private_endpoint_subnet[0].vcn_id",
            IP_OR_CIDR,
        ],
        context="ADB network preconditions",
    )

    forbidden = [
        "PUBLIC_ENDPOINT",
        "SECURE_ACCESS_FROM_ALLOWED_IPS_AND_VCNS",
        "PRIVATE_ENDPOINT_ONLY",
        "CIDR_BLOCK",
        "adb_use_private_subnet",
    ]
    stack_contract = "\n".join([schema, variables, adb, compute, locals_source])
    found = [value for value in forbidden if value in stack_contract]
    if found:
        raise AssertionError(f"legacy ADB network inputs remain: {found}")


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
