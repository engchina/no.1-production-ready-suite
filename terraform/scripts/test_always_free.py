#!/usr/bin/env python3
"""実際の Terraform 式・validation・precondition を OCI 接続なしで検証する。"""

from __future__ import annotations

import json
import os
import re
import subprocess
import tempfile
import unittest
from pathlib import Path

STACK = Path(__file__).resolve().parents[1] / "stack"
TERRAFORM = os.environ.get("TERRAFORM", "terraform")


class AlwaysFreeTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # 一時モジュールも repo の tmp に置く。OCI resource の式を terraform_data に移し、
        # provider の通信を除いて Terraform 自身で式と入力の制約を評価する。
        temp_root = STACK.parents[1] / "tmp" / "always-free-tests"
        temp_root.mkdir(parents=True, exist_ok=True)
        cls.temp = tempfile.TemporaryDirectory(dir=temp_root)
        cls.path = Path(cls.temp.name)
        compute = (STACK / "compute.tf").read_text()
        adb = (STACK / "adb.tf").read_text()
        source = (STACK / "variables.tf").read_text()
        source += compute[: compute.index("# 選んだ製品")]
        source += """
locals {
  create_new_adb = contains(["CREATE_NEW", "新規 Autonomous AI Database の作成"], var.adb_deployment_mode)
  adb_private_endpoint_enabled = local.create_new_adb && var.adb_network_access_type == "プライベート・エンドポイント・アクセスのみ"
}
resource "terraform_data" "settings" {
  input = {
"""
        fields = {
            "shape": (compute, "shape"),
            "image": (compute, "source_id"),
            "ocpus": (compute, "ocpus"),
            "memory": (compute, "memory_in_gbs"),
            "boot": (compute, "boot_volume_size_in_gbs"),
            "vpus": (compute, "boot_volume_vpus_per_gb"),
            "baseline": (compute, "baseline_ocpu_utilization"),
        }
        for name in (
            "is_free_tier",
            "compute_model",
            "compute_count",
            "data_storage_size_in_tbs",
            "backup_retention_period_in_days",
            "is_auto_scaling_enabled",
            "is_auto_scaling_for_storage_enabled",
            "license_model",
        ):
            fields[name] = (adb, name)
        for name, (text, attribute) in fields.items():
            expression = re.search(rf"(?m)^  +{attribute}\s*= (.+)$", text)[1]
            source += f"    {name} = {expression}\n"
        source += "  }\n  lifecycle {\n"
        for block in re.findall(
            r"    precondition \{.*?\n    \}", compute + adb, re.DOTALL
        ):
            if (
                "var.compute_always_free_enabled" in block
                or "var.adb_is_free_tier" in block
            ):
                source += block + "\n"
        source += "  }\n}\n"
        (cls.path / "main.tf").write_text(source)
        cls.run_tf("init", "-backend=false")

    @classmethod
    def tearDownClass(cls):
        cls.temp.cleanup()

    @classmethod
    def run_tf(cls, *args, success=True):
        result = subprocess.run(
            [TERRAFORM, f"-chdir={cls.path}", *args, "-no-color"],
            check=False,
            capture_output=True,
            text=True,
        )
        if (result.returncode == 0) != success:
            raise AssertionError(result.stdout + result.stderr)
        return result

    def plan(self, values, success=True):
        values = {"adb_compartment_ocid": "ocid1.compartment.oc1..synthetic", **values}
        (self.path / "test.auto.tfvars.json").write_text(json.dumps(values))
        self.run_tf(
            "plan",
            "-input=false",
            "-refresh=false",
            "-out=test.tfplan",
            success=success,
        )
        if success:
            result = self.run_tf("show", "-json", "test.tfplan")
            return json.loads(result.stdout)["planned_values"]["root_module"][
                "resources"
            ][0]["values"]["input"]

    def test_paid_defaults(self):
        values = self.plan({})
        self.assertEqual(
            [values[k] for k in ("shape", "ocpus", "memory", "boot", "vpus")],
            ["VM.Standard.E5.Flex", 4, 32, 200, 10],
        )
        self.assertEqual(
            [
                values[k]
                for k in (
                    "is_free_tier",
                    "compute_model",
                    "compute_count",
                    "data_storage_size_in_tbs",
                    "backup_retention_period_in_days",
                )
            ],
            [False, "ECPU", 2, 1, 1],
        )

    def test_free_both_regions_and_sizes(self):
        for region in ("ap-osaka-1", "ap-tokyo-1"):
            for ocpus, memory, boot in ((2, 12, 200), (1, 6, 50)):
                with self.subTest(region=region, ocpus=ocpus):
                    values = self.plan(
                        {
                            "region": region,
                            "compute_always_free_enabled": True,
                            "adb_is_free_tier": True,
                            "adb_network_access_type": "すべての場所からのセキュア・アクセス",
                            "compute_always_free_ocpus": ocpus,
                            "compute_always_free_memory_in_gbs": memory,
                            "compute_always_free_boot_volume_size_in_gbs": boot,
                            "adb_is_auto_scaling_enabled": True,
                            "adb_is_auto_scaling_for_storage_enabled": True,
                            "license_model": "BRING_YOUR_OWN_LICENSE",
                        }
                    )
                    self.assertEqual(
                        [
                            values[k]
                            for k in ("shape", "ocpus", "memory", "boot", "vpus")
                        ],
                        ["VM.Standard.A1.Flex", ocpus, memory, boot, 10],
                    )
                    self.assertIn(region, values["image"])
                    self.assertEqual(
                        [
                            values[k]
                            for k in (
                                "compute_model",
                                "compute_count",
                                "data_storage_size_in_tbs",
                                "backup_retention_period_in_days",
                                "baseline",
                            )
                        ],
                        [None] * 5,
                    )
                    self.assertEqual(
                        [
                            values[k]
                            for k in (
                                "is_free_tier",
                                "is_auto_scaling_enabled",
                                "is_auto_scaling_for_storage_enabled",
                                "license_model",
                            )
                        ],
                        [True, False, False, "LICENSE_INCLUDED"],
                    )

    def test_independent_switches(self):
        compute = self.plan({"compute_always_free_enabled": True})
        self.assertEqual(compute["shape"], "VM.Standard.A1.Flex")
        self.assertEqual(compute["compute_count"], 2)
        self.assertFalse(compute["is_free_tier"])
        adb = self.plan(
            {
                "adb_is_free_tier": True,
                "adb_workload": "OLTP",
                "adb_network_access_type": "許可されたIPおよびVCN限定のセキュア・アクセス",
            }
        )
        self.assertEqual(adb["shape"], "VM.Standard.E5.Flex")
        self.assertTrue(adb["is_free_tier"])
        self.assertIsNone(adb["compute_count"])

    def test_rejected_inputs_and_combinations(self):
        cases = [{"compute_always_free_ocpus": n} for n in (0, 1.5, 3)]
        cases += [{"compute_always_free_memory_in_gbs": n} for n in (0, 1.5, 13)]
        cases += [
            {"compute_always_free_boot_volume_size_in_gbs": n} for n in (49, 50.5, 201)
        ]
        cases += [
            {"compute_always_free_enabled": True, "bastion_enabled": True},
            {"adb_is_free_tier": True},
        ]
        for extra in (
            {"adb_deployment_mode": "USE_EXISTING"},
            {"adb_is_elastic_pool_enabled": True},
            {"adb_workload": "AJD"},
            {"adb_workload": "APEX"},
            {"adb_db_version": "23ai"},
        ):
            cases.append(
                {
                    "adb_is_free_tier": True,
                    "adb_network_access_type": "すべての場所からのセキュア・アクセス",
                    **extra,
                }
            )
        for case in cases:
            with self.subTest(case=case):
                self.plan(case, success=False)


if __name__ == "__main__":
    unittest.main()
