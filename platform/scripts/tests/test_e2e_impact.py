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


API_TS = """export interface Config {
  /** 説明 */
  a?: string;
}

export const LIMIT = 10;

export enum Mode {
  A = "a",
}

// 読み込み
export function load() {
  return LIMIT;
}
"""


class NonRuntimeLinesTest(unittest.TestCase):
    def _marked(self, text: str) -> list[str]:
        lines = text.splitlines()
        return [lines[n - 1] for n in sorted(impact.non_runtime_lines(text))]

    def test_interface_type_and_comments_are_static(self) -> None:
        text = (
            'import type { A } from "./a";\n'  # 1
            'export type { B, C } from "./b";\n'  # 2
            "/**\n"  # 3
            " * 説明\n"  # 4
            " */\n"  # 5
            'export interface Props extends Omit<A, "x"> {\n'  # 6
            "  // 欄\n"  # 7
            "  onChange: (value: { a: string }) => void;\n"  # 8
            "}\n"  # 9
            "\n"  # 10
            "export type Union =\n"  # 11
            '  | "a"\n'  # 12
            "  | { b: `x-${string}` };\n"  # 13
            "declare global {\n"  # 14
            "  interface Window { x: number }\n"  # 15
            "}\n"  # 16
        )
        self.assertEqual(impact.non_runtime_lines(text), set(range(1, 17)))

    def test_runtime_code_is_not_static(self) -> None:
        text = (
            "export const LIMIT = 10;\n"  # 1
            "export enum Mode {\n"  # 2
            '  A = "a",\n'  # 3
            "}\n"  # 4
            "const enum Flag { On }\n"  # 5
            "export function f(): number {\n"  # 6
            "  type Local = string;\n"  # 7（関数の中は見ない）
            "  return 1; // コメント\n"  # 8
            "}\n"  # 9
            'import { type A, b } from "./a";\n'  # 10
            "export class K implements A {}\n"  # 11
        )
        self.assertEqual(impact.non_runtime_lines(text), set())

    def test_type_without_semicolon_ends_before_next_statement(self) -> None:
        text = 'export type A = "a" | "b"\nexport const b = 1;\n'
        self.assertEqual(self._marked(text), ['export type A = "a" | "b"'])

    def test_template_literal_lines_are_runtime(self) -> None:
        text = "const sql = `\n\n// 例\ninterface X {}\n`;\n"
        self.assertEqual(impact.non_runtime_lines(text), set())

    def test_code_after_declaration_on_same_line_is_runtime(self) -> None:
        text = "interface A {\n  a: string;\n} export const x = 1;\n/* a */ const y = 2;\n"
        self.assertEqual(self._marked(text), ["interface A {", "  a: string;"])


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


class PlanShardsTest(unittest.TestCase):
    def test_lpt_balances_by_duration(self) -> None:
        durations = {"a": 700, "b": 500, "c": 400, "d": 300, "e": 200, "f": 100}
        shards = impact.plan_shards(durations, 3)
        totals = sorted(shard.estimated_ms for shard in shards)
        self.assertEqual(totals, [700, 700, 800])
        self.assertEqual(sorted(s for shard in shards for s in shard.specs), sorted(durations))

    def test_no_empty_shards(self) -> None:
        shards = impact.plan_shards({"a": 1, "b": 2}, 4)
        self.assertEqual(len(shards), 2)
        self.assertEqual(impact.plan_shards({}, 3), [])

    def test_unknown_spec_uses_median(self) -> None:
        recorded = impact.ImpactMap(
            _map(
                {},
                specs={
                    "a": {"durationMs": 100},
                    "b": {"durationMs": 300},
                    "c": {"durationMs": 900},
                },
            )
        )
        self.assertEqual(impact.spec_durations(["a", "new"], recorded), {"a": 100, "new": 300})
        self.assertEqual(impact.spec_durations(["x"], None), {"x": impact.UNKNOWN_SPEC_MS})

    def test_matrix_lists_specs_per_shard(self) -> None:
        product = impact.PRODUCTS["rag"]
        selection = impact.Selection(product)
        long = {f"rag/frontend/e2e/s{i}.spec.ts": {"durationMs": 4 * 60 * 1000} for i in range(3)}
        selection.add(long, "変更")
        matrix, shards = impact.shard_matrix(
            product, selection, impact.ImpactMap(_map({}, specs=long))
        )
        self.assertEqual(len(matrix), 2)
        self.assertEqual([entry["shard"] for entry in matrix], [1, 2])
        self.assertEqual({entry["playwright_shard"] for entry in matrix}, {"1/1"})
        listed = " ".join(entry["specs"] for entry in matrix).split()
        self.assertEqual(sorted(listed), [f"e2e/s{i}.spec.ts" for i in range(3)])
        self.assertEqual(len(shards), 2)

    def test_fully_parallel_product_shards_by_test(self) -> None:
        product = impact.PRODUCTS["nl2sql"]
        selection = impact.Selection(product)
        selection.need_full("package.json")
        matrix, shards = impact.shard_matrix(product, selection, None)
        self.assertEqual(len(matrix), product.max_shards)
        self.assertEqual(
            matrix[0], {"shard": 1, "specs": "", "playwright_shard": f"1/{product.max_shards}"}
        )
        self.assertEqual(shards, [])


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
            "rag/frontend/src/lib/api.ts": API_TS,
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
                        "rag/frontend/src/lib/api.ts": {
                            "loadedBy": [page, other],
                            "functions": {"14:16": {"specs": [page], "tests": 1}},
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

    def _edit_api(self, old: str, new: str) -> impact.Selection:
        text = (self.root / "rag/frontend/src/lib/api.ts").read_text()
        self.assertIn(old, text)
        self._write("rag/frontend/src/lib/api.ts", text.replace(old, new))
        return self._commit_and_select()

    def test_interface_only_change_selects_nothing(self) -> None:
        selection = self._edit_api(
            "  a?: string;\n", "  a?: string;\n  /** 追加 */\n  b?: number;\n"
        )
        self.assertEqual(selection.specs, {})
        self.assertEqual(selection.full, [])
        self.assertTrue(any("型の宣言・コメントだけ" in note for note in selection.notes))

    def test_comment_only_change_selects_nothing(self) -> None:
        selection = self._edit_api("// 読み込み\n", "// 一覧を読み込む\n")
        self.assertEqual(selection.specs, {})

    def test_const_change_selects_specs(self) -> None:
        selection = self._edit_api("LIMIT = 10", "LIMIT = 20")
        self.assertIn(self.page, selection.specs)

    def test_enum_change_selects_specs(self) -> None:
        selection = self._edit_api('  A = "a",\n', '  A = "a",\n  B = "b",\n')
        self.assertIn(self.page, selection.specs)

    def test_function_body_change_selects_specs(self) -> None:
        selection = self._edit_api("return LIMIT;", "return LIMIT + 1;")
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
