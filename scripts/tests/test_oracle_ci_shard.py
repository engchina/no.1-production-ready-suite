"""Oracle CI の分割がテストを欠落・重複させないことを確かめる。"""

from __future__ import annotations

import os
import subprocess
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / ".github/scripts"))
from oracle_ci_shard import pytest_collection_modifyitems, shard_for


class OracleShardTests(unittest.TestCase):
    def test_shards_cover_all_items_once_and_preserve_order(self):
        original = [
            SimpleNamespace(nodeid=f"tests/test_a.py::test_case[{i}]")
            for i in range(1000)
        ]
        for count in (1, 2, 3):
            shards = []
            for index in range(count):
                config = Mock()
                config.getoption.side_effect = {
                    "--oracle-shard-count": count,
                    "--oracle-shard-index": index,
                }.__getitem__
                items = original.copy()
                pytest_collection_modifyitems(config, items)
                expected = [
                    item for item in original if shard_for(item.nodeid, count) == index
                ]
                self.assertEqual(items, expected)
                self.assertTrue(items)
                config.hook.pytest_deselected.assert_called_once_with(
                    items=[
                        item
                        for item in original
                        if shard_for(item.nodeid, count) != index
                    ]
                )
                shards.extend(item.nodeid for item in items)
            self.assertCountEqual(shards, [item.nodeid for item in original])

    def test_assignment_is_stable_for_unicode_and_parameterized_nodeids(self):
        nodeid = "tests/test_a.py::test_case[合成の入力]"
        assignments = []
        for seed in ("1", "2"):
            result = subprocess.run(
                [
                    sys.executable,
                    "-c",
                    "from oracle_ci_shard import shard_for; "
                    + f"print(shard_for({nodeid!r}, 2))",
                ],
                env=os.environ | {"PYTHONHASHSEED": seed, "PYTHONPATH": sys.path[0]},
                text=True,
                capture_output=True,
                check=True,
                timeout=10,
            )
            assignments.append(result.stdout)
        self.assertEqual(assignments[0], assignments[1])
        self.assertEqual(shard_for(nodeid, 1), 0)
        with self.assertRaises(ValueError):
            shard_for(nodeid, 0)

    def test_invalid_configuration_is_rejected(self):
        # Suite / Scripts は標準ライブラリだけで動くため例外の型だけを差し替える。
        for count, index in ((0, 0), (2, -1), (2, 2)):
            with self.subTest(count=count, index=index):
                config = Mock()
                config.getoption.side_effect = {
                    "--oracle-shard-count": count,
                    "--oracle-shard-index": index,
                }.__getitem__
                with (
                    unittest.mock.patch.dict(
                        sys.modules, {"pytest": SimpleNamespace(UsageError=ValueError)}
                    ),
                    self.assertRaises(ValueError),
                ):
                    pytest_collection_modifyitems(config, [])


if __name__ == "__main__":
    unittest.main()
