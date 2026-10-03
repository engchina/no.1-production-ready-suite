"""platform/scripts/e2e_impact.py のテスト（#885）。

実行: python3 -m unittest discover -s platform/scripts/tests -p 'test_*.py'
"""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import e2e_impact as impact  # noqa: E402


def _map(files: dict, tests: int = 4, specs: dict | None = None, commit: str = "c0") -> dict:
    return {
        "version": impact.MAP_VERSION,
        "product": "rag",
        "commit": commit,
        "tests": tests,
        "specs": specs or {},
        "files": files,
    }


class DictionaryTest(unittest.TestCase):
    OLD = """export const ja = {
  "a.title": "タイトル",
  // コメント
  "a.long":
    "長い文言の前半" +
    "後半",
  "b.label": "ラベル {name}",
};

export function t(key: string) {
  return key;
}
"""

    def test_changed_keys_and_old_values(self) -> None:
        new = self.OLD.replace('"ラベル {name}"', '"新しいラベル {name}"').replace("後半", "後ろ")
        keys, old_values, structural = impact.dictionary_changes(self.OLD, new)
        self.assertEqual(keys, {"a.long", "b.label"})
        self.assertEqual(old_values["a.long"], "長い文言の前半後半")
        self.assertEqual(old_values["b.label"], "ラベル {name}")
        self.assertFalse(structural)

    def test_added_key_and_comment_only_change(self) -> None:
        new = self.OLD.replace("  // コメント\n", '  // 別のコメント\n  "c.new": "追加",\n')
        keys, old_values, structural = impact.dictionary_changes(self.OLD, new)
        self.assertEqual(keys, {"c.new"})
        self.assertEqual(old_values, {})
        self.assertFalse(structural)

    def test_function_change_is_structural(self) -> None:
        new = self.OLD.replace("return key;", "return key.trim();")
        keys, _, structural = impact.dictionary_changes(self.OLD, new)
        self.assertEqual(keys, set())
        self.assertTrue(structural)


class TranslateLinesTest(unittest.TestCase):
    def test_moves_lines_after_insertion(self) -> None:
        source = "a\nb\nc\nd\n"
        target = "x\ny\na\nb\nc\nd\n"
        self.assertEqual(impact.translate_lines({1, 4}, source, target), {3, 6})

    def test_changed_line_maps_near(self) -> None:
        source = "a\nb\nc\n"
        target = "a\nB\nc\n"
        self.assertEqual(impact.translate_lines({2}, source, target), {1})


class ImpactMapTest(unittest.TestCase):
    def setUp(self) -> None:
        files = {
            "rag/frontend/src/Page.tsx": {
                "loadedBy": ["s1", "s2", "s3", "s4"],
                "functions": {
                    # 読み込み時に全テストで実行される関数
                    "3:3": {"specs": ["s1", "s2", "s3", "s4"], "tests": 4},
                    # 画面の component（s1 だけ）と、その中の handler（s1 だけ）
                    "10:40": {"specs": ["s1"], "tests": 1},
                    "20:25": {"specs": ["s1"], "tests": 1},
                },
            },
            "rag/frontend/src/Shell.tsx": {
                "loadedBy": ["s1", "s2", "s3", "s4"],
                "functions": {"5:30": {"specs": ["s1", "s2", "s3", "s4"], "tests": 4}},
            },
        }
        self.map = impact.ImpactMap(_map(files))

    def test_line_in_function_selects_its_specs(self) -> None:
        specs, shared = self.map.specs_for_lines("rag/frontend/src/Page.tsx", {22})
        self.assertEqual(specs, {"s1"})
        self.assertFalse(shared)

    def test_line_in_global_function_selects_all_loaders(self) -> None:
        specs, shared = self.map.specs_for_lines("rag/frontend/src/Page.tsx", {3})
        self.assertEqual(specs, {"s1", "s2", "s3", "s4"})
        self.assertTrue(shared)

    def test_top_level_line_selects_local_specs(self) -> None:
        specs, _ = self.map.specs_for_lines("rag/frontend/src/Page.tsx", {1})
        self.assertEqual(specs, {"s1"})

    def test_top_level_of_shell_selects_all(self) -> None:
        specs, shared = self.map.specs_for_lines("rag/frontend/src/Shell.tsx", {1})
        self.assertEqual(specs, {"s1", "s2", "s3", "s4"})
        self.assertTrue(shared)


class AggregateTest(unittest.TestCase):
    def _record(self, spec: str, test_id: str, retry: int, executed: dict) -> dict:
        return {
            "spec": spec,
            "testId": test_id,
            "title": test_id,
            "project": "desktop",
            "retry": retry,
            "durationMs": 1000,
            "executed": executed,
            "loaded": sorted(executed),
        }

    def test_aggregate_keeps_last_retry_and_merges_shards(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            shard1 = Path(tmp, "1")
            shard2 = Path(tmp, "2")
            shard1.mkdir()
            shard2.mkdir()
            records = [
                (shard1, self._record("a.spec.ts", "t1", 0, {"x.tsx": ["1:9", "2:3"]})),
                (shard1, self._record("a.spec.ts", "t1", 1, {"x.tsx": ["1:9"]})),
                (shard2, self._record("b.spec.ts", "t2", 0, {"x.tsx": ["1:9", "5:6"]})),
            ]
            for index, (directory, record) in enumerate(records):
                Path(directory, f"{index}.json").write_text(json.dumps(record))
            maps = [
                impact.aggregate(shard1, "rag", "c0"),
                impact.aggregate(shard2, "rag", "c0"),
            ]
        merged = impact.merge_maps(maps)
        assert merged is not None
        self.assertEqual(merged["tests"], 2)
        self.assertEqual(merged["specs"]["a.spec.ts"]["tests"], 1)
        functions = impact.ImpactMap(merged).functions("x.tsx")
        by_key = {(fn.start, fn.end): fn for fn in functions}
        self.assertTrue(by_key[(1, 9)].is_global)
        self.assertNotIn((2, 3), by_key)
        self.assertEqual(by_key[(5, 6)].specs, {"b.spec.ts"})

    def test_merge_ignores_other_commits(self) -> None:
        a = _map({}, tests=1, commit="c1")
        b = _map({}, tests=2, commit="c2")
        merged = impact.merge_maps([a, b])
        assert merged is not None
        self.assertEqual(merged["tests"], 1)


class SelectIntegrationTest(unittest.TestCase):
    """一時的な git repo で select を通しで確かめる。"""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self._git("init", "-q", "-b", "main")
        self._git("config", "user.email", "test@example.com")
        self._git("config", "user.name", "test")
        files = {
            "rag/frontend/package.json": "{}\n",
            "rag/frontend/src/Page.tsx": "import x from './x';\n\nexport function Page() {\n"
            "  const label = t('page.title');\n  return label;\n}\n",
            "rag/frontend/src/Other.tsx": (
                'export function Other() {\n  return t("other.title");\n}\n'
            ),
            "rag/frontend/src/lib/i18n.ts": 'export const ja = {\n  "page.title": "ページ",\n'
            '  "other.title": "ほかの画面",\n};\n',
            "rag/frontend/e2e/fixtures/test.ts": "export const test = 1;\n",
            "rag/frontend/e2e/fixtures/helper.ts": "export const helper = 1;\n",
            "rag/frontend/e2e/page.spec.ts": "import { test } from './fixtures/test';\n",
            "rag/frontend/e2e/other.spec.ts": "import { helper } from './fixtures/helper';\n"
            "// ほかの画面\n",
            "rag/frontend/e2e/node.spec.ts": "import { test } from './fixtures/test';\n",
        }
        for path, text in files.items():
            self._write(path, text)
        self._git("add", "-A")
        self._git("commit", "-q", "-m", "base")
        self.base = self._git("rev-parse", "HEAD").strip()
        self._git("checkout", "-q", "-b", "feature")
        page = "rag/frontend/e2e/page.spec.ts"
        other = "rag/frontend/e2e/other.spec.ts"
        node = "rag/frontend/e2e/node.spec.ts"
        self.map = impact.ImpactMap(
            {
                **_map(
                    {
                        "rag/frontend/src/Page.tsx": {
                            "loadedBy": [page, other],
                            "functions": {"3:6": {"specs": [page], "tests": 1}},
                        },
                        "rag/frontend/src/Other.tsx": {
                            "loadedBy": [page, other],
                            "functions": {"1:3": {"specs": [other], "tests": 1}},
                        },
                    },
                    tests=2,
                    specs={page: {"durationMs": 1000, "tests": 1}, other: {"durationMs": 1000}},
                    commit=self.base,
                ),
            }
        )
        self.node = node
        self.page = page
        self.other = other
        self._patch = mock.patch.object(impact, "ROOT", self.root)
        self._patch.start()

    def tearDown(self) -> None:
        self._patch.stop()
        self._tmp.cleanup()

    def _git(self, *args: str) -> str:
        return subprocess.run(
            ["git", *args], cwd=self.root, capture_output=True, text=True, check=True
        ).stdout

    def _write(self, path: str, text: str) -> None:
        target = self.root / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")

    def _commit_and_select(self) -> impact.Selection:
        self._git("add", "-A")
        self._git("commit", "-q", "-m", "change")
        return impact.select(impact.PRODUCTS["rag"], self.map, self.base)

    def test_changed_function_selects_spec_and_unrecorded_spec(self) -> None:
        self._write(
            "rag/frontend/src/Page.tsx",
            (self.root / "rag/frontend/src/Page.tsx")
            .read_text()
            .replace("return label;", "return label + 1;"),
        )
        selection = self._commit_and_select()
        self.assertEqual(selection.full, [])
        # 記録の無い spec（node.spec.ts）は常に選ぶ。
        self.assertEqual(set(selection.specs), {self.page, self.node})

    def test_i18n_key_selects_referencing_screen_and_old_text_spec(self) -> None:
        text = (self.root / "rag/frontend/src/lib/i18n.ts").read_text()
        self._write("rag/frontend/src/lib/i18n.ts", text.replace("ほかの画面", "別の画面"))
        selection = self._commit_and_select()
        self.assertIn(self.other, selection.specs)
        self.assertNotIn(self.page, selection.specs)

    def test_helper_change_selects_dependent_spec(self) -> None:
        self._write("rag/frontend/e2e/fixtures/helper.ts", "export const helper = 2;\n")
        selection = self._commit_and_select()
        self.assertIn(self.other, selection.specs)
        self.assertNotIn(self.page, selection.specs)

    def test_package_json_runs_everything(self) -> None:
        self._write("rag/frontend/package.json", '{"name": "x"}\n')
        selection = self._commit_and_select()
        self.assertTrue(selection.full)

    def test_without_map_runs_everything(self) -> None:
        self._write("rag/frontend/src/Other.tsx", "export function Other() {\n  return 1;\n}\n")
        self._git("add", "-A")
        self._git("commit", "-q", "-m", "change")
        selection = impact.select(impact.PRODUCTS["rag"], None, self.base)
        self.assertTrue(selection.full)

    def test_backend_change_selects_nothing(self) -> None:
        self._write("rag/backend/app.py", "x = 1\n")
        selection = self._commit_and_select()
        self.assertEqual(selection.specs, {})
        self.assertEqual(selection.full, [])

    def test_check_imports_rejects_direct_test_import(self) -> None:
        self._write(
            "rag/frontend/e2e/bad.spec.ts",
            'import { expect, test } from "@playwright/test";\n',
        )
        self._write(
            "rag/frontend/e2e/ok.spec.ts",
            'import type { Page } from "@playwright/test";\n'
            'import { expect } from "@playwright/test";\n',
        )
        errors = impact.check_imports([impact.PRODUCTS["rag"]])
        self.assertEqual(len(errors), 1)
        self.assertIn("bad.spec.ts", errors[0])
        self.assertIn("./fixtures/test", errors[0])


if __name__ == "__main__":
    unittest.main()
