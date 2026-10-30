"""bastion の任意入力と主 Compute からの継承の契約。"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from typing import ClassVar

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from verify_stack_contract import _schema_groups, _schema_variable, _terraform_variable

ROOT = Path(__file__).resolve().parents[3]
OVERRIDES = ("bastion_shape", "bastion_image_source_id", "bastion_ssh_authorized_keys")


class BastionFormTests(unittest.TestCase):
    def test_optional_inputs_are_visible_only_with_bastion(self) -> None:
        schema = (ROOT / "terraform/stack/schema.yaml").read_text()
        source = (ROOT / "terraform/stack/variables.tf").read_text()
        visible, members = _schema_groups(schema)["Bastion Compute"]
        self.assertEqual(visible, "bastion_enabled")
        for name in OVERRIDES:
            with self.subTest(field=name):
                self.assertIn(name, members)
                block = _schema_variable(schema, name)
                self.assertIn("visible: bastion_enabled", block)
                self.assertIn("required: false", block)
                self.assertIn('default: ""', block)
                self.assertIn('default     = ""', _terraform_variable(source, name))


class BastionInheritanceTests(unittest.TestCase):
    MAIN: ClassVar[dict[str, str]] = {
        "instance_shape": "VM.Standard.E5.Flex",
        "instance_image_source_id": "synthetic-main-image",
        "ssh_authorized_keys": "synthetic-main-public-key",
    }
    EXPECTED: ClassVar[dict[str, str]] = {
        "shape": MAIN["instance_shape"],
        "image": MAIN["instance_image_source_id"],
        "ssh_keys": MAIN["ssh_authorized_keys"],
    }

    @classmethod
    def setUpClass(cls) -> None:
        scratch = ROOT / "tmp"
        scratch.mkdir(exist_ok=True)
        cls.directory = tempfile.TemporaryDirectory(
            prefix="bastion-inputs-", dir=scratch
        )
        cls.addClassCleanup(cls.directory.cleanup)
        cls.cwd = Path(cls.directory.name)
        variables = (ROOT / "terraform/stack/variables.tf").read_text()
        bastion = (ROOT / "terraform/stack/bastion.tf").read_text()
        local_block = re.search(r"(?ms)^locals \{.*?^\}", bastion)
        if local_block is None:
            raise AssertionError("bastion の継承用 locals がない")
        names = (*cls.MAIN, *OVERRIDES, "bastion_subnet_id", "subnet_ai_subnet_id")
        # 配布する変数と locals をそのまま使い、provider・resource を含めない。
        (cls.cwd / "main.tf").write_text(
            "\n".join(_terraform_variable(variables, name) for name in names)
            + "\n"
            + local_block.group(0)
            + """
output "bastion" {
  value = {
    shape = local.bastion_shape
    image = local.bastion_image_source_id
    ssh_keys = local.bastion_ssh_authorized_keys
  }
}
output "main" {
  value = {
    shape = var.instance_shape
    image = var.instance_image_source_id
    ssh_keys = var.ssh_authorized_keys
  }
}
"""
        )
        cls.terraform = os.environ.get("TERRAFORM_BIN", "terraform")
        initialized = cls.run_cli("init", "-backend=false", "-input=false", "-no-color")
        if initialized.returncode != 0:
            raise AssertionError(initialized.stdout)

    @classmethod
    def run_cli(cls, *args: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [cls.terraform, *args],
            cwd=cls.cwd,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            check=False,
            timeout=30,
        )

    def plan(self, overrides: dict[str, str]) -> subprocess.CompletedProcess[str]:
        (self.cwd / "inputs.tfvars.json").write_text(json.dumps(self.MAIN | overrides))
        return self.run_cli(
            "plan",
            "-input=false",
            "-lock=false",
            "-no-color",
            "-var-file=inputs.tfvars.json",
            "-out=plan.bin",
        )

    def assert_resolved(
        self, overrides: dict[str, str], expected: dict[str, str]
    ) -> None:
        plan = self.plan(overrides)
        self.assertEqual(plan.returncode, 0, plan.stdout)
        shown = self.run_cli("show", "-json", "plan.bin")
        self.assertEqual(shown.returncode, 0, shown.stdout)
        outputs = json.loads(shown.stdout)["planned_values"]["outputs"]
        self.assertEqual(outputs["bastion"]["value"], expected)
        self.assertEqual(outputs["main"]["value"], self.EXPECTED)

    def test_omitted_inputs_preserve_existing_configuration(self) -> None:
        self.assert_resolved({}, self.EXPECTED)

    def test_empty_and_whitespace_inputs_inherit(self) -> None:
        for blank in ("", " \t\n"):
            with self.subTest(blank=repr(blank)):
                self.assert_resolved(dict.fromkeys(OVERRIDES, blank), self.EXPECTED)

    def test_independent_values_do_not_change_main_compute(self) -> None:
        self.assert_resolved(
            {
                "bastion_shape": "VM.Standard.E4.Flex",
                "bastion_image_source_id": "synthetic-bastion-image",
                "bastion_ssh_authorized_keys": "synthetic-public-key-A\nsynthetic-public-key-B\n",
            },
            {
                "shape": "VM.Standard.E4.Flex",
                "image": "synthetic-bastion-image",
                "ssh_keys": "synthetic-public-key-A\nsynthetic-public-key-B\n",
            },
        )

    def test_each_input_can_be_overridden_individually(self) -> None:
        for field, key, value in (
            ("bastion_shape", "shape", "VM.Standard.E4.Flex"),
            ("bastion_image_source_id", "image", "synthetic-bastion-image"),
            ("bastion_ssh_authorized_keys", "ssh_keys", "synthetic-bastion-public-key"),
        ):
            with self.subTest(field=field):
                self.assert_resolved({field: value}, self.EXPECTED | {key: value})

    def test_unsupported_shape_is_rejected(self) -> None:
        plan = self.plan({"bastion_shape": "UNSUPPORTED"})
        self.assertNotEqual(plan.returncode, 0, plan.stdout)
        self.assertIn("Invalid value for variable", plan.stdout)


if __name__ == "__main__":
    unittest.main()
