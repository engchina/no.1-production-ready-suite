"""任意の Oracle AI Data Platform（#5917）の入力どうしの条件と、共通 .env・cloud-init への出し方。"""

from __future__ import annotations

import json
import os
import re
import subprocess
import tempfile
import unittest
from pathlib import Path
from typing import ClassVar

ROOT = Path(__file__).resolve().parents[3]
STACK = ROOT / "terraform/stack"

VALID = {
    "aidp_iam_policies_confirmed": True,
    "create_new_adb": True,
    "existing_adb_admin_password": "",
    "aidp_db_user_name": "ADB_AIDP_USER",
    "aidp_db_user_password": "Synthetic-Pass-12",
    "effective_oracle_user": "ADB_APP_USER",
}


class AidpPreconditionTests(unittest.TestCase):
    """aidp.tf の実際の precondition を組み込み resource に移し、OCI に接続せず plan する。"""

    cwd: ClassVar[Path]
    terraform: ClassVar[str]

    @classmethod
    def setUpClass(cls) -> None:
        scratch = ROOT / "tmp"
        scratch.mkdir(exist_ok=True)
        directory = tempfile.TemporaryDirectory(prefix="aidp-inputs-", dir=scratch)
        cls.addClassCleanup(directory.cleanup)
        cls.cwd = Path(directory.name)
        source = (STACK / "aidp.tf").read_text(encoding="utf-8")
        lifecycle = re.search(r"(?ms)^  lifecycle \{.*?^  \}", source)
        if lifecycle is None:
            raise AssertionError("aidp.tf の precondition がない")
        (cls.cwd / "main.tf").write_text(
            'variable "aidp_iam_policies_confirmed" { type = bool }\n'
            'variable "create_new_adb" { type = bool }\n'
            'variable "existing_adb_admin_password" { type = string }\n'
            'variable "aidp_db_user_name" { type = string }\n'
            'variable "aidp_db_user_password" { type = string }\n'
            'variable "effective_oracle_user" { type = string }\n'
            "locals {\n"
            "  create_new_adb        = var.create_new_adb\n"
            "  effective_oracle_user = var.effective_oracle_user\n"
            "  aidp_db_user          = upper(trimspace(var.aidp_db_user_name))\n"
            "}\n"
            'resource "terraform_data" "aidp" {\n' + lifecycle.group(0) + "\n}\n",
            encoding="utf-8",
        )
        cls.terraform = os.environ.get("TERRAFORM_BIN", "terraform")
        result = cls.cli("init", "-backend=false", "-input=false", "-no-color")
        if result.returncode:
            raise AssertionError(result.stdout)

    @classmethod
    def cli(cls, *args: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [cls.terraform, *args],
            cwd=cls.cwd,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            timeout=60,
            check=False,
        )

    def plan(self, **changes: object) -> subprocess.CompletedProcess[str]:
        values = {**VALID, **changes}
        (self.cwd / "terraform.tfvars.json").write_text(json.dumps(values), encoding="utf-8")
        return self.cli("plan", "-input=false", "-no-color", "-refresh=false")

    def assert_rejected(self, expected: str, **changes: object) -> None:
        result = self.plan(**changes)
        self.assertNotEqual(result.returncode, 0, result.stdout)
        # Terraform は長い message を折り返すため、空白をそろえて比べる。
        self.assertIn(expected, re.sub(r"[\s│]+", " ", result.stdout))

    def test_valid_inputs_plan(self) -> None:
        result = self.plan()
        self.assertEqual(result.returncode, 0, result.stdout)

    def test_iam_policies_must_be_confirmed(self) -> None:
        self.assert_rejected("aidp_iam_policies_confirmed を true にしてください", aidp_iam_policies_confirmed=False)

    def test_existing_adb_requires_admin_password(self) -> None:
        # 既存の ADB では、アプリ用の DB ユーザーを使わない構成でも ADMIN のパスワードが要る。
        self.assert_rejected("existing_adb_admin_password", create_new_adb=False)
        result = self.plan(create_new_adb=False, existing_adb_admin_password="Synthetic-Admin-1")
        self.assertEqual(result.returncode, 0, result.stdout)

    def test_db_user_password_is_required(self) -> None:
        self.assert_rejected("aidp_db_user_password", aidp_db_user_password="  ")

    def test_db_user_must_differ_from_the_product_user(self) -> None:
        for product_user in ("ADB_AIDP_USER", "adb_aidp_user ", "ADMIN"):
            with self.subTest(product_user=product_user):
                self.assert_rejected(
                    "3 製品の接続のユーザー",
                    effective_oracle_user=product_user,
                    aidp_db_user_name=product_user.strip(),
                )

    def test_password_must_not_contain_the_user_name(self) -> None:
        self.assert_rejected("aidp_db_user_name を含めないでください", aidp_db_user_password="Xadb_aidp_user-Pass1")


class AidpRenderingTests(unittest.TestCase):
    """deploy_aidp=false の配備の cloud-init を変えない（変えると既存の stack の Compute が作り直される）。"""

    def test_platform_env_lines_are_inside_the_deploy_aidp_block(self) -> None:
        source = (STACK / "locals.tf").read_text(encoding="utf-8")
        heredoc = re.search(r"(?ms)^  platform_env = <<-EOT\n(.*?)^EOT$", source)
        assert heredoc is not None
        body = heredoc.group(1)
        block = re.search(r"(?ms)^%\{if var\.deploy_aidp~\}\n.*?^%\{endif~\}\n", body)
        self.assertIsNotNone(block)
        assert block is not None
        # 条件の block は heredoc の末尾で、外すと AIDP の行が残らない。
        self.assertTrue(body.endswith(block.group(0)))
        self.assertNotIn("AIDP", body.replace(block.group(0), ""))

    def test_props_file_is_written_only_with_aidp(self) -> None:
        bootstrap = (STACK / "cloud_init/bootstrap.template.yaml").read_text(encoding="utf-8")
        outside = re.sub(r'(?ms)^%\{ if adb_aidp_user_env != "" ~\}\n.*?^%\{ endif ~\}\n', "", bootstrap, count=1)
        self.assertNotIn("adb_aidp_user", outside)
        self.assertIn("adb_aidp_user.env", bootstrap)

    def test_password_variable_is_sensitive_and_validated(self) -> None:
        variables = (STACK / "variables.tf").read_text(encoding="utf-8")
        block = re.search(r'(?ms)^variable "aidp_db_user_password" \{.*?^\}', variables)
        assert block is not None
        self.assertIn("sensitive   = true", block.group(0))
        self.assertIn("length(var.aidp_db_user_password) >= 12", block.group(0))


if __name__ == "__main__":
    unittest.main()
