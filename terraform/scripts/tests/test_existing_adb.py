"""既存 ADB の取得失敗とコンパートメント不一致を区別する。"""

from __future__ import annotations

import json
import os
import re
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]


class ExistingAdbPlanTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        scratch = ROOT / "tmp"
        scratch.mkdir(exist_ok=True)
        cls.directory = tempfile.TemporaryDirectory(prefix="existing-adb-", dir=scratch)
        cls.addClassCleanup(cls.directory.cleanup)
        cls.cwd = Path(cls.directory.name)
        source = (ROOT / "terraform/stack/adb.tf").read_text()
        lifecycle = re.search(r"(?ms)^  lifecycle \{.*?^  \}", source)
        if lifecycle is None:
            raise AssertionError("既存 ADB の postcondition がない")
        # 実際の postcondition を組み込み resource に適用し、OCI に接続せず plan する。
        checks = lifecycle.group(0)
        for field in ("id", "compartment_id", "db_name"):
            checks = checks.replace(f"self.{field}", f"self.input.{field}")
        (cls.cwd / "main.tf").write_text(
            'variable "adb_compartment_ocid" { default = "synthetic-compartment" }\n'
            'variable "response" {\n'
            " type = object({id=string, compartment_id=string, db_name=string})\n}\n"
            'resource "terraform_data" "adb" {\n input = var.response\n'
            + checks
            + "\n}\n"
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
            timeout=30,
            check=False,
        )

    def plan(self, **changes: object) -> subprocess.CompletedProcess[str]:
        response = {
            "id": "synthetic-adb",
            "compartment_id": "synthetic-compartment",
            "db_name": "SYNTHETIC",
        }
        (self.cwd / "inputs.tfvars.json").write_text(
            json.dumps({"response": response | changes})
        )
        return self.cli(
            "plan", "-input=false", "-no-color", "-var-file=inputs.tfvars.json"
        )

    def test_matching_compartment_passes(self) -> None:
        result = self.plan()
        self.assertEqual(result.returncode, 0, result.stdout)

    def test_different_compartment_is_rejected(self) -> None:
        result = self.plan(compartment_id="synthetic-other-compartment")
        self.assertNotEqual(result.returncode, 0, result.stdout)
        self.assertIn("ADBのコンパートメントに属している必要", result.stdout)
        self.assertNotIn("情報を取得できませんでした", result.stdout)

    def test_missing_fields_are_rejected(self) -> None:
        for field in ("id", "compartment_id", "db_name"):
            for value in (None, "", "   "):
                with self.subTest(field=field, value=value):
                    result = self.plan(**{field: value})
                    self.assertNotEqual(result.returncode, 0, result.stdout)
                    self.assertIn("情報を取得できませんでした", result.stdout)
                    if field == "compartment_id":
                        self.assertNotIn(
                            "ADBのコンパートメントに属している必要", result.stdout
                        )


if __name__ == "__main__":
    unittest.main()
