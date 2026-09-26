"""NL2SQL の Terraform stack の release zip について、資格情報まわりの追加の契約を確かめる。

共通の release workflow（.github/workflows/terraform-release.yml）が、製品ごとの
scripts/verify_terraform_release_extras.py があれば実行する（#94）。
"""
from pathlib import Path
import re
import sys
import zipfile

package = Path(sys.argv[1] if len(sys.argv) > 1 else "dist/production-ready-nl2sql-terraform-stack.zip")
with zipfile.ZipFile(package) as archive:
    locals_tf = archive.read("locals.tf").decode()
    schema_yaml = archive.read("schema.yaml").decode()
    variables_tf = archive.read("variables.tf").decode()


def heredoc_body(name: str) -> str:
    """locals.tf の `<name> = <<-EOT` heredoc の本文。"""
    match = re.search(rf"(?ms)^  {name} = <<-EOT\n(.*?)^EOT$", locals_tf)
    if not match:
        raise SystemExit(f"{name} heredoc is missing from locals.tf")
    return match.group(1)


# 製品固有の設定は backend_env（backend/.env）、3製品共通の設定は platform_env（platform/.env。#211）。
backend_env = heredoc_body("backend_env")
platform_env = heredoc_body("platform_env")
if (
    "PLATFORM_ADMIN_LOGIN_USER_ID=${var.app_admin_login_user_id}" not in platform_env
    or "PLATFORM_ADMIN_LOGIN_USER_PASSWORD=${var.app_admin_login_user_password}"
    not in platform_env
):
    raise SystemExit("PLATFORM_ADMIN credentials are missing from platform.env")
deepsec_schema_group = re.search(
    r"- title: Deep Data Security\s+visible: true\s+variables:\s+"
    r"- oracle_deepsec_enabled\s+"
    r"- oracle_deepsec_data_user_password",
    schema_yaml,
)
if not deepsec_schema_group:
    raise SystemExit("DeepSec toggle/password are missing from the visible Resource Manager group")
deepsec_enabled_schema_block = re.search(
    r"(?ms)^  oracle_deepsec_enabled:\n(?:(?:    .*)\n)+",
    schema_yaml,
)
deepsec_enabled_schema_checks = [
    "type: boolean",
    "required: true",
    "visible: true",
    "default: true",
]
if not deepsec_enabled_schema_block or any(
    check not in deepsec_enabled_schema_block.group(0)
    for check in deepsec_enabled_schema_checks
):
    raise SystemExit("DeepSec enabled Resource Manager form field is incomplete")
deepsec_schema_block = re.search(
    r"(?ms)^  oracle_deepsec_data_user_password:\n(?:(?:    .*)\n)+",
    schema_yaml,
)
deepsec_schema_checks = [
    "type: password",
    "required: false",
    "- oracle_deepsec_enabled",
    "- true",
    "NL2SQL_ORACLE_DEEPSEC_DATA_USER_PASSWORD",
    "confirmation: true",
    "pattern: '^[^\"\\r\\n]{12,256}$'",
]
if not deepsec_schema_block or any(
    check not in deepsec_schema_block.group(0)
    for check in deepsec_schema_checks
):
    raise SystemExit("DeepSec password Resource Manager form field is incomplete")
deepsec_variable_block = re.search(
    r'(?ms)^variable "oracle_deepsec_data_user_password" \{.*?^}',
    variables_tf,
)
deepsec_variable_checks = [
    "type        = string",
    "sensitive   = true",
    "default     = \"\"",
    "trimspace(var.oracle_deepsec_data_user_password) == \"\"",
    "length(var.oracle_deepsec_data_user_password) >= 12",
    "length(var.oracle_deepsec_data_user_password) <= 256",
    "!can(regex(\"[\\r\\n]\", var.oracle_deepsec_data_user_password))",
    "!can(regex(\"\\\"\", var.oracle_deepsec_data_user_password))",
]
if (
    not deepsec_variable_block
    or any(
        check not in deepsec_variable_block.group(0)
        for check in deepsec_variable_checks
    )
):
    raise SystemExit("DeepSec password Terraform variable validation is incomplete")
if (
    "NL2SQL_ORACLE_DEEPSEC_ENABLED=${var.oracle_deepsec_enabled}" not in backend_env
    or "NL2SQL_ORACLE_DEEPSEC_DATA_USER_PASSWORD=${var.oracle_deepsec_enabled ? "
    "var.oracle_deepsec_data_user_password : \"\"}" not in backend_env
):
    raise SystemExit("DeepSec toggle/password are missing from backend.env")
ssh_schema_block = re.search(
    r"(?ms)^  ssh_authorized_keys:\n(?:(?:    .*)\n)+",
    schema_yaml,
)
ssh_schema_checks = [
    "type: oci:core:ssh:publickey",
    "required: true",
    "visible: true",
    "title: \"SSHキーの追加\"",
]
if not ssh_schema_block or any(
    check not in ssh_schema_block.group(0)
    for check in ssh_schema_checks
):
    raise SystemExit("SSH public key Resource Manager form field is incomplete")
if "first SYSTEM_ADMIN web login" in schema_yaml:
    raise SystemExit("ADB password is still described as a web login password")
print("Terraform package credential checks verified.")
