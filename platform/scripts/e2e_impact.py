#!/usr/bin/env python3
"""e2e の影響分析: PR の差分に影響を受ける Playwright の spec を選ぶ（#885）。

nightly（`.github/workflows/e2e-nightly.yml`）は 3 製品の e2e を `E2E_IMPACT_DIR` 付きで
全件実行し、テストごとに「実行された関数の元の行の範囲」を記録する
（`platform/scripts/e2e-impact/fixture.ts`）。`aggregate` はそれを shard ごとに 1 つの JSON に
まとめ、artifact（`e2e-impact-<製品>-<shard>`）にする。

PR の CI（`.github/workflows/ci.yml` の `e2e-impact`）は `select` で、base との差分の行を
実行した spec を選び、選んだ spec だけを shard に分けて実行する。選び方:

- 製品の frontend の source・共有 UI（`platform/packages/{ui,system-settings}/src`）の変更した行
  - その行を含む関数を実行した spec。すべてのテストで実行される関数（画面の枠・読み込み時の
    処理）なら、そのファイルを読み込んだ spec すべて
  - 関数の外の行（import・定数・型）は、そのファイルの関数を実行した spec
- i18n の辞書（`src/lib/*i18n*.ts`）は、変更したキーを参照する source の行に置き換える。
  古い文言をそのまま書いている spec も選ぶ
- 変更・追加した spec と、変更した e2e の helper / fixture を使う spec
- coverage の記録が無い spec（新しい spec・ページを使わない spec）は常に選ぶ
- 判定できないもの（記録が無い、package.json / lock / vite / playwright の設定、CSS、
  記録の仕組み自体）は全件

ローカルで同じ選択を見るには（nightly の記録を `gh` で取得する）:

    python3 platform/scripts/e2e_impact.py select --product rag --base origin/main --download

`check-imports` は、spec が `@playwright/test` から `test` を直接 import していないこと
（記録の fixture を通ること）を確かめる。
"""

from __future__ import annotations

import argparse
import difflib
import json
import math
import os
import re
import subprocess
import sys
import tempfile
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
MAP_VERSION = 1
# PR の 1 shard の目安（nightly の所要時間で見積もる）。
SHARD_TARGET_MS = 6 * 60 * 1000
UNKNOWN_SPEC_MS = 60 * 1000


@dataclass(frozen=True)
class Product:
    name: str
    frontend: str
    test_dir: str
    # 記録の fixture を足した `test` を export するファイル（frontend からの相対パス）。
    test_module: str
    max_shards: int
    nightly_shards: int

    @property
    def test_root(self) -> str:
        return f"{self.frontend}/{self.test_dir}"


PRODUCTS = {
    "rag": Product("rag", "rag/frontend", "e2e", "e2e/fixtures/test.ts", 4, 2),
    "nl2sql": Product("nl2sql", "nl2sql/frontend", "tests/e2e", "tests/e2e/_helpers/test.ts", 8, 8),
    "agent": Product("agent", "agent/frontend", "e2e", "e2e/fixtures/test.ts", 3, 1),
}
PLATFORM_FRONTEND = ("platform/packages/ui/", "platform/packages/system-settings/")
IMPACT_TOOLING = ("platform/scripts/e2e-impact/",)
CODE_SUFFIXES = (".ts", ".tsx", ".js", ".jsx", ".mjs", ".json")
I18N_DICT = re.compile(r"/src/lib/[^/]*i18n[^/]*\.ts$")
UNIT_TEST = re.compile(r"(\.test\.[cm]?[jt]sx?$|/__tests__/)")
FULL_RUN_FILES = re.compile(
    r"^(package\.json|package-lock\.json|index\.html|vite\.config\.[cm]?[jt]s|"
    r"playwright\.config\.[cm]?[jt]s|tsconfig[^/]*\.json)$"
)


# ---------------------------------------------------------------- git


def git(*args: str, check: bool = True) -> str:
    result = subprocess.run(["git", *args], cwd=ROOT, capture_output=True, text=True, check=False)
    if check and result.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)}: {result.stderr.strip()}")
    return result.stdout


def git_show(ref: str, path: str) -> str | None:
    result = subprocess.run(
        ["git", "show", f"{ref}:{path}"], cwd=ROOT, capture_output=True, text=True, check=False
    )
    return result.stdout if result.returncode == 0 else None


def has_commit(ref: str) -> bool:
    result = subprocess.run(
        ["git", "cat-file", "-e", f"{ref}^{{commit}}"], cwd=ROOT, capture_output=True, check=False
    )
    return result.returncode == 0


@dataclass(frozen=True)
class Change:
    status: str  # A / M / D / R
    old: str | None
    new: str | None

    @property
    def paths(self) -> list[str]:
        return [p for p in (self.old, self.new) if p]


def changed_files(base: str, head: str) -> list[Change]:
    out = git("diff", "--name-status", "-M", f"{base}...{head}")
    changes: list[Change] = []
    for line in out.splitlines():
        parts = line.split("\t")
        status = parts[0][0]
        if status == "R":
            changes.append(Change("R", parts[1], parts[2]))
        elif status == "D":
            changes.append(Change("D", parts[1], None))
        elif status == "A":
            changes.append(Change("A", None, parts[1]))
        else:
            changes.append(Change("M", parts[1], parts[1]))
    return changes


HUNK = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@")


def changed_old_lines(base: str, head: str, old: str, new: str) -> set[int]:
    """差分の base 側の行番号（追加だけの位置は前後の行）。"""
    out = git("diff", "-U0", f"{base}...{head}", "--", old, new)
    lines: set[int] = set()
    for line in out.splitlines():
        match = HUNK.match(line)
        if not match:
            continue
        start = int(match.group(1))
        count = int(match.group(2) or "1")
        if count == 0:
            lines.update({max(start, 1), start + 1})
        else:
            lines.update(range(start, start + count))
    return lines


def translate_lines(lines: Iterable[int], source: str, target: str) -> set[int]:
    """`source` の行番号を、`target` の対応する行番号に移す（変わった行は前後の行）。"""
    a = source.splitlines()
    b = target.splitlines()
    mapping: dict[int, int] = {}
    for tag, i1, i2, j1, _j2 in difflib.SequenceMatcher(None, a, b, autojunk=False).get_opcodes():
        if tag == "equal":
            for offset in range(i2 - i1):
                mapping[i1 + offset + 1] = j1 + offset + 1
        else:
            for line in range(i1 + 1, i2 + 1):
                mapping[line] = max(j1, 1)
    result: set[int] = set()
    for line in lines:
        if line in mapping:
            result.add(mapping[line])
        elif mapping:
            result.add(max(1, min(line, len(b))))
    return result


# ---------------------------------------------------------------- 記録の集計（nightly）


def aggregate(records_dir: Path, product: str, commit: str) -> dict:
    """テストごとの記録を、shard の集計にする（同じテストの retry は最後のものだけ）。"""
    latest: dict[str, dict] = {}
    for path in sorted(records_dir.glob("*.json")):
        record = json.loads(path.read_text(encoding="utf-8"))
        current = latest.get(record["testId"])
        if current is None or record["retry"] >= current["retry"]:
            latest[record["testId"]] = record
    specs: dict[str, dict] = {}
    files: dict[str, dict] = {}
    tests = 0
    for record in latest.values():
        spec = record["spec"]
        entry = specs.setdefault(spec, {"durationMs": 0, "tests": 0})
        entry["durationMs"] += int(record["durationMs"])
        entry["tests"] += 1
        if not record["loaded"]:
            continue
        tests += 1
        for loaded in record["loaded"]:
            info = files.setdefault(loaded, {"loadedBy": set(), "functions": {}})
            info["loadedBy"].add(spec)
        for path, ranges in record["executed"].items():
            info = files.setdefault(path, {"loadedBy": set(), "functions": {}})
            info["loadedBy"].add(spec)
            for key in ranges:
                fn = info["functions"].setdefault(key, {"specs": set(), "tests": 0})
                fn["specs"].add(spec)
                fn["tests"] += 1
    return {
        "version": MAP_VERSION,
        "product": product,
        "commit": commit,
        "tests": tests,
        "specs": specs,
        "files": {
            path: {
                "loadedBy": sorted(info["loadedBy"]),
                "functions": {
                    key: {"specs": sorted(fn["specs"]), "tests": fn["tests"]}
                    for key, fn in sorted(info["functions"].items())
                },
            }
            for path, info in sorted(files.items())
        },
    }


def merge_maps(maps: list[dict]) -> dict | None:
    """shard の集計をまとめる。製品・commit がそろわないものは捨てる。"""
    maps = [m for m in maps if m.get("version") == MAP_VERSION]
    if not maps:
        return None
    commit = maps[0]["commit"]
    maps = [m for m in maps if m["commit"] == commit]
    merged: dict = {
        "version": MAP_VERSION,
        "product": maps[0]["product"],
        "commit": commit,
        "shards": len(maps),
        "tests": 0,
        "specs": {},
        "files": {},
    }
    for m in maps:
        merged["tests"] += m["tests"]
        for spec, entry in m["specs"].items():
            target = merged["specs"].setdefault(spec, {"durationMs": 0, "tests": 0})
            target["durationMs"] += entry["durationMs"]
            target["tests"] += entry["tests"]
        for path, info in m["files"].items():
            target = merged["files"].setdefault(path, {"loadedBy": set(), "functions": {}})
            target["loadedBy"].update(info["loadedBy"])
            for key, fn in info["functions"].items():
                tfn = target["functions"].setdefault(key, {"specs": set(), "tests": 0})
                tfn["specs"].update(fn["specs"])
                tfn["tests"] += fn["tests"]
    return merged


# ---------------------------------------------------------------- 選択


@dataclass
class Selection:
    product: Product
    specs: dict[str, list[str]] = field(default_factory=dict)  # spec（repo 相対）→ 理由
    full: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    def add(self, specs: Iterable[str], reason: str) -> None:
        for spec in specs:
            reasons = self.specs.setdefault(spec, [])
            if reason not in reasons and len(reasons) < 5:
                reasons.append(reason)

    def need_full(self, reason: str) -> None:
        if reason not in self.full:
            self.full.append(reason)


@dataclass(frozen=True)
class Function:
    start: int
    end: int
    specs: frozenset[str]
    is_global: bool


class ImpactMap:
    def __init__(self, data: dict):
        self.commit: str = data["commit"]
        self.tests: int = data["tests"]
        self.specs: dict[str, dict] = data["specs"]
        self.shards: int = data.get("shards", 1)
        self._files: dict[str, dict] = data["files"]

    def loaded_by(self, path: str) -> set[str]:
        info = self._files.get(path)
        return set(info["loadedBy"]) if info else set()

    def knows(self, path: str) -> bool:
        return path in self._files

    def functions(self, path: str) -> list[Function]:
        info = self._files.get(path)
        if not info:
            return []
        result = []
        for key, fn in info["functions"].items():
            start, end = (int(v) for v in key.split(":"))
            result.append(
                Function(
                    start, end, frozenset(fn["specs"]), self.tests > 0 and fn["tests"] >= self.tests
                )
            )
        return result

    def specs_for_lines(self, path: str, lines: Iterable[int]) -> tuple[set[str], bool]:
        """行を実行した spec と、画面の枠など全テストで実行される処理に当たったか。"""
        functions = self.functions(path)
        loaded_by = self.loaded_by(path)
        local = (
            set().union(*(fn.specs for fn in functions if not fn.is_global)) if functions else set()
        )
        selected: set[str] = set()
        shared = False
        for line in lines:
            containing = [fn for fn in functions if fn.start <= line <= fn.end]
            if containing:
                inner = min(containing, key=lambda fn: (fn.end - fn.start, -fn.start))
                if inner.is_global:
                    selected |= loaded_by
                    shared = True
                else:
                    selected |= inner.specs
            elif local:
                selected |= local
            else:
                selected |= loaded_by
                shared = shared or bool(loaded_by)
        return selected, shared


def relative_to_frontend(product: Product, spec: str) -> str:
    return spec[len(product.frontend) + 1 :]


def list_specs(product: Product) -> list[str]:
    root = ROOT / product.test_root
    return sorted(
        str(path.relative_to(ROOT)).replace(os.sep, "/") for path in root.rglob("*.spec.ts")
    )


def test_dependents(product: Product, changed: str) -> set[str]:
    """変更した e2e の helper / fixture を（推移的に）使う spec。名前（拡張子なし）で探す。"""
    root = ROOT / product.test_root
    files = {
        str(p.relative_to(ROOT)).replace(os.sep, "/"): p.read_text(
            encoding="utf-8", errors="ignore"
        )
        for p in root.rglob("*")
        if p.is_file() and p.suffix in (".ts", ".mjs", ".js", ".json")
    }
    found: set[str] = set()
    queue = [changed]
    while queue:
        current = queue.pop()
        name = Path(current).name
        stem = re.sub(r"\.(ts|mjs|js|json)$", "", name)
        pattern = re.compile(rf"[/\"']{re.escape(stem)}(\.(ts|mjs|js|json))?[\"']")
        for path, text in files.items():
            if path not in found and path != current and pattern.search(text):
                found.add(path)
                queue.append(path)
    return {path for path in found if path.endswith(".spec.ts")}


# ---- i18n の辞書

ENTRY = re.compile(r'^\s*"([A-Za-z0-9_.\-]+)"\s*:')
STRING = re.compile(r'"((?:[^"\\]|\\.)*)"')


def parse_dictionary(text: str) -> tuple[dict[str, str], dict[int, str]]:
    """`"key": value` の辞書を読む。キー → 値の文字列、行番号 → その行が属するキー。"""
    entries: dict[str, list[str]] = {}
    owner: dict[int, str] = {}
    current: str | None = None
    for number, line in enumerate(text.splitlines(), start=1):
        match = ENTRY.match(line)
        stripped = line.strip()
        if match:
            current = match.group(1)
            entries[current] = [line[match.end() :]]
            owner[number] = current
        elif (
            current and stripped and not stripped.startswith("//") and not stripped.startswith("}")
        ):
            entries[current].append(line)
            owner[number] = current
            if stripped.endswith(",") and not stripped.endswith("+,"):
                current = None
        else:
            current = None if not stripped or stripped.startswith("}") else current
        if match and line.rstrip().endswith(","):
            current = None
    return {key: "\n".join(parts).strip() for key, parts in entries.items()}, owner


def string_value(raw: str) -> str:
    return "".join(json.loads(f'"{part}"') for part in STRING.findall(raw))


def dictionary_changes(old: str, new: str) -> tuple[set[str], dict[str, str], bool]:
    """変わったキー、そのキーの古い文言、辞書の外（関数など）が変わったか。"""
    old_entries, old_owner = parse_dictionary(old)
    new_entries, new_owner = parse_dictionary(new)
    keys = {
        key
        for key in set(old_entries) | set(new_entries)
        if old_entries.get(key) != new_entries.get(key)
    }

    def outside(text: str, owner: dict[int, str]) -> list[str]:
        result = []
        for number, line in enumerate(text.splitlines(), start=1):
            stripped = line.strip()
            if number in owner or not stripped or stripped.startswith("//"):
                continue
            result.append(stripped)
        return result

    structural = outside(old, old_owner) != outside(new, new_owner)
    old_values = {key: string_value(old_entries[key]) for key in keys if key in old_entries}
    return keys, old_values, structural


def key_references(product: Product, key: str, dictionaries: set[str]) -> dict[str, set[int]]:
    """キーを参照する source の行（HEAD の行番号）。無ければ動的なキーとして接頭辞で探す。"""
    src = ROOT / product.frontend / "src"
    candidates = [re.compile(rf"[\"'`]{re.escape(key)}[\"'`]")]
    prefix = key.rsplit(".", 1)[0] if "." in key else None
    if prefix:
        candidates.append(re.compile(rf"[\"'`]{re.escape(prefix)}\.(\$\{{|[\"'`])"))
        candidates.append(re.compile(rf"[\"'`]{re.escape(prefix)}\."))
    for pattern in candidates:
        found: dict[str, set[int]] = {}
        for path in src.rglob("*"):
            if path.suffix not in (".ts", ".tsx") or UNIT_TEST.search(path.name):
                continue
            rel = str(path.relative_to(ROOT)).replace(os.sep, "/")
            if rel in dictionaries:
                continue
            for number, line in enumerate(
                path.read_text(encoding="utf-8", errors="ignore").splitlines(), start=1
            ):
                if pattern.search(line):
                    found.setdefault(rel, set()).add(number)
        if found:
            return found
    return {}


def specs_mentioning(product: Product, text: str) -> set[str]:
    if len(text) < 2:
        return set()
    fragments = [f for f in re.split(r"\{[^}]*\}", text) if len(f.strip()) >= 2]
    if not fragments:
        return set()
    needle = max(fragments, key=len).strip()
    result = set()
    for spec in list_specs(product):
        if needle in (ROOT / spec).read_text(encoding="utf-8", errors="ignore"):
            result.add(spec)
    return result


# ---- 本体


def is_relevant_source(product: Product, path: str) -> bool:
    return path.startswith(f"{product.frontend}/src/") or (
        path.startswith(PLATFORM_FRONTEND) and "/src/" in path
    )


def select(product: Product, impact: ImpactMap | None, base: str, head: str = "HEAD") -> Selection:
    selection = Selection(product)
    all_specs = set(list_specs(product))
    dictionaries: set[str] = {
        str(p.relative_to(ROOT)).replace(os.sep, "/")
        for p in (ROOT / product.frontend / "src" / "lib").glob("*i18n*.ts")
        if not UNIT_TEST.search(p.name)
    }

    def source_lines(path: str, lines: set[int], reason: str, ref: str) -> None:
        """`ref` の行番号の `lines` を、記録の commit の行番号に移して spec を選ぶ。"""
        assert impact is not None
        if not impact.knows(path):
            selection.notes.append(f"{path}: どの spec も実行していない（記録に無い）")
            return
        mapped = lines
        if ref != impact.commit:
            before = git_show(ref, path)
            recorded = git_show(impact.commit, path) if has_commit(impact.commit) else None
            if before is not None and recorded is not None:
                mapped = translate_lines(lines, before, recorded) if before != recorded else lines
            elif recorded is None:
                # 記録の commit が無い（取得できない）ときは、ファイルを実行した spec すべてにする。
                mapped = {fn.start for fn in impact.functions(path)} or lines
        specs, shared = impact.specs_for_lines(path, mapped)
        label = f"{reason}（全画面の共通の処理）" if shared else reason
        if not specs:
            selection.notes.append(f"{path}: 変更した行を実行した spec が無い")
        selection.add(specs, label)

    for change in changed_files(base, head):
        paths = change.paths
        primary = change.new or change.old or ""
        if primary.startswith(IMPACT_TOOLING) or any(
            p == "platform/scripts/e2e_impact.py" for p in paths
        ):
            if primary.startswith(IMPACT_TOOLING):
                selection.need_full(f"{primary}（記録の仕組み）")
            continue

        # e2e の spec / helper
        if any(p.startswith(product.test_root + "/") for p in paths):
            if change.new and change.new.endswith(".spec.ts") and change.new in all_specs:
                selection.add([change.new], "変更した spec")
                continue
            if change.new == f"{product.frontend}/{product.test_module}":
                selection.need_full(f"{change.new}（全 spec の fixture）")
                continue
            for path in paths:
                if not path.endswith(".spec.ts"):
                    selection.add(test_dependents(product, path), f"helper の変更: {path}")
            continue

        # 製品の frontend の設定・依存
        for path in paths:
            if path.startswith(product.frontend + "/") and not path.startswith(
                f"{product.frontend}/src/"
            ):
                rest = path[len(product.frontend) + 1 :]
                if FULL_RUN_FILES.match(rest) or rest.startswith("public/"):
                    selection.need_full(f"{path}（frontend の設定・依存）")
            elif path.startswith(PLATFORM_FRONTEND) and "/src/" not in path:
                rest = path.split("/", 3)[3] if path.count("/") >= 3 else path
                if FULL_RUN_FILES.match(rest):
                    selection.need_full(f"{path}（共有 UI の設定・依存）")
            elif path in ("platform/package.json", "platform/package-lock.json"):
                selection.need_full(f"{path}（共有 UI の依存）")

        sources = [p for p in paths if is_relevant_source(product, p)]
        if not sources:
            continue
        if any(p.endswith(".css") for p in sources):
            selection.need_full(f"{primary}（CSS）")
            continue
        if all(UNIT_TEST.search(p) or p.endswith(".d.ts") for p in sources):
            continue
        if not any(p.endswith(CODE_SUFFIXES) for p in sources):
            selection.need_full(f"{primary}（画像などの資産）")
            continue
        if impact is None:
            selection.need_full("nightly の記録（coverage map）が無い")
            continue

        if change.status == "A":
            selection.notes.append(f"{primary}: 新しいファイル（import する側の変更で選ぶ）")
            continue

        old = change.old
        assert old is not None
        if old in dictionaries and change.new:
            before = git_show(base, old) or ""
            after = (ROOT / change.new).read_text(encoding="utf-8")
            keys, old_values, structural = dictionary_changes(before, after)
            if structural:
                source_lines(
                    old, changed_old_lines(base, head, old, change.new), "i18n の処理の変更", base
                )
            for key in sorted(keys):
                refs = key_references(product, key, dictionaries)
                if not refs:
                    selection.notes.append(f"{old}: キー {key} を参照する source が無い")
                for ref_path, lines in refs.items():
                    source_lines(ref_path, lines, f"文言 {key}", head)
                if key in old_values:
                    selection.add(
                        specs_mentioning(product, old_values[key]), f"古い文言 {key} を書いた spec"
                    )
            continue

        if change.status == "D":
            lines = {fn.start for fn in impact.functions(old)} or {1}
        else:
            lines = changed_old_lines(base, head, old, change.new or old)
        source_lines(old, lines, f"変更: {old}", base)

    if impact is not None:
        unrecorded = sorted(spec for spec in all_specs if spec not in impact.specs)
        if unrecorded and selection.specs:
            selection.add(unrecorded, "coverage の記録が無い spec（常に実行）")
    selection.specs = {spec: r for spec, r in selection.specs.items() if spec in all_specs}
    return selection


def shard_count(product: Product, selection: Selection, impact: ImpactMap | None) -> int:
    if selection.full:
        return product.max_shards
    if not selection.specs:
        return 0
    durations = [entry["durationMs"] for entry in (impact.specs.values() if impact else [])]
    default = sorted(durations)[len(durations) // 2] if durations else UNKNOWN_SPEC_MS
    total = sum(
        impact.specs.get(spec, {}).get("durationMs", default) if impact else default
        for spec in selection.specs
    )
    return max(1, min(product.max_shards, math.ceil(total / SHARD_TARGET_MS)))


# ---------------------------------------------------------------- nightly の記録の取得


def download_maps(product: Product, dest: Path, repo: str | None = None) -> list[Path]:
    """最近の nightly（main）の run から、製品の記録の artifact を取得する。"""
    repo_args = ["--repo", repo] if repo else []
    out = subprocess.run(
        [
            "gh", "run", "list", "--workflow", "e2e-nightly.yml", "--branch", "main",
            "--status", "completed", "--limit", "10", "--json", "databaseId", *repo_args,
        ],
        cwd=ROOT, capture_output=True, text=True, check=False,
    )  # fmt: skip
    if out.returncode != 0:
        print(f"[e2e-impact] nightly の run を取得できない: {out.stderr.strip()}", file=sys.stderr)
        return []
    for run in json.loads(out.stdout or "[]"):
        target = dest / str(run["databaseId"])
        result = subprocess.run(
            [
                "gh", "run", "download", str(run["databaseId"]), "--pattern",
                f"e2e-impact-{product.name}-*", "--dir", str(target), *repo_args,
            ],
            cwd=ROOT, capture_output=True, text=True, check=False,
        )  # fmt: skip
        files = sorted(target.rglob("*.json")) if target.exists() else []
        if result.returncode == 0 and files:
            return files
    return []


def load_impact(paths: Iterable[Path], product: Product) -> ImpactMap | None:
    maps = []
    for path in paths:
        data = json.loads(path.read_text(encoding="utf-8"))
        if data.get("product") == product.name:
            maps.append(data)
    merged = merge_maps(maps)
    return ImpactMap(merged) if merged else None


# ---------------------------------------------------------------- import の検査

DIRECT_TEST_IMPORT = re.compile(
    r"import\s*\{([^}]*)\}\s*from\s*[\"']@playwright/test[\"']", re.MULTILINE
)


def check_imports(products: Iterable[Product]) -> list[str]:
    errors = []
    for product in products:
        root = ROOT / product.test_root
        allowed = ROOT / product.frontend / product.test_module
        for path in sorted(root.rglob("*.ts")):
            if path == allowed:
                continue
            for match in DIRECT_TEST_IMPORT.finditer(path.read_text(encoding="utf-8")):
                names = [n.strip() for n in match.group(1).split(",")]
                values = [n for n in names if n and not n.startswith("type ")]
                if any(re.match(r"^test(\s+as\s+\w+)?$", n) for n in values):
                    rel = path.relative_to(ROOT)
                    module = os.path.relpath(allowed.with_suffix(""), path.parent)
                    module = module if module.startswith(".") else f"./{module}"
                    errors.append(
                        f"{rel}: `test` は `@playwright/test` ではなく `{module}` から import する"
                        "（nightly の影響分析の記録を通すため。#885）"
                    )
    return errors


# ---------------------------------------------------------------- CLI


def write_output(path: str | None, values: dict[str, str]) -> None:
    if not path:
        return
    with open(path, "a", encoding="utf-8") as handle:
        for key, value in values.items():
            handle.write(f"{key}={value}\n")


def summary(selection: Selection, mode: str, shards: int, impact: ImpactMap | None) -> str:
    product = selection.product
    lines = [f"### e2e の影響分析: {product.name}", ""]
    if impact:
        lines.append(f"- 記録: nightly の commit `{impact.commit[:10]}`（{impact.shards} shard）")
    else:
        lines.append("- 記録: なし")
    if mode == "full":
        lines.append(f"- **全件**（{shards} shard）: " + "、".join(selection.full))
    elif mode == "none":
        lines.append("- 影響を受ける spec はありません")
    else:
        lines.append(f"- {len(selection.specs)} spec（{shards} shard）")
        for spec, reasons in sorted(selection.specs.items()):
            lines.append(f"  - `{relative_to_frontend(product, spec)}`: {' / '.join(reasons)}")
    for note in list(dict.fromkeys(selection.notes))[:30]:
        lines.append(f"- 注: {note}")
    return "\n".join(lines) + "\n"


def command_select(args: argparse.Namespace) -> int:
    product = PRODUCTS[args.product]
    paths: list[Path] = []
    if args.maps:
        paths = sorted(Path(args.maps).rglob("*.json"))
    elif args.download:
        paths = download_maps(product, Path(tempfile.mkdtemp(prefix="e2e-impact-")), args.repo)
    impact = load_impact(paths, product)
    selection = select(product, impact, args.base, args.head)
    shards = shard_count(product, selection, impact)
    mode = "full" if selection.full else ("selected" if selection.specs else "none")
    specs = [relative_to_frontend(product, spec) for spec in sorted(selection.specs)]
    text = summary(selection, mode, shards, impact)
    print(text)
    if args.step_summary:
        with open(args.step_summary, "a", encoding="utf-8") as handle:
            handle.write(text)
    write_output(
        args.github_output,
        {
            f"{product.name}_mode": mode,
            f"{product.name}_specs": " ".join(specs) if mode == "selected" else "",
            f"{product.name}_shards": json.dumps(list(range(1, shards + 1))),
            f"{product.name}_total": str(shards),
        },
    )
    return 0


def command_aggregate(args: argparse.Namespace) -> int:
    data = aggregate(Path(args.records), args.product, args.commit)
    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    Path(args.out).write_text(json.dumps(data, separators=(",", ":")), encoding="utf-8")
    print(f"[e2e-impact] {args.product}: {data['tests']} tests, {len(data['files'])} files")
    return 0


def command_check_imports(args: argparse.Namespace) -> int:
    products = [PRODUCTS[name] for name in (args.product or PRODUCTS)]
    errors = check_imports(products)
    for error in errors:
        print(f"::error::{error}" if os.environ.get("GITHUB_ACTIONS") else error)
    return 1 if errors else 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)

    p_select = sub.add_parser("select", help="差分に影響を受ける spec を選ぶ")
    p_select.add_argument("--product", required=True, choices=sorted(PRODUCTS))
    p_select.add_argument("--base", required=True, help="比べる base（例: origin/main）")
    p_select.add_argument("--head", default="HEAD")
    p_select.add_argument("--maps", help="記録（aggregate の JSON）のディレクトリ")
    p_select.add_argument("--download", action="store_true", help="nightly の記録を gh で取得する")
    p_select.add_argument("--repo", help="gh の --repo（省略時は現在の repo）")
    p_select.add_argument("--github-output", default=os.environ.get("GITHUB_OUTPUT"))
    p_select.add_argument("--step-summary", default=os.environ.get("GITHUB_STEP_SUMMARY"))
    p_select.set_defaults(func=command_select)

    p_aggregate = sub.add_parser("aggregate", help="テストごとの記録を shard の集計にする")
    p_aggregate.add_argument("--product", required=True, choices=sorted(PRODUCTS))
    p_aggregate.add_argument("--records", required=True)
    p_aggregate.add_argument("--out", required=True)
    p_aggregate.add_argument("--commit", required=True)
    p_aggregate.set_defaults(func=command_aggregate)

    p_check = sub.add_parser("check-imports", help="spec が記録の fixture を通ることを確かめる")
    p_check.add_argument("--product", action="append", choices=sorted(PRODUCTS))
    p_check.set_defaults(func=command_check_imports)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
