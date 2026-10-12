#!/usr/bin/env python3
"""製品の frontend のコードが読む frontend の外のファイルが、
CI の frontend の job を動かす filter に入っていることを確かめる（#1531）。

`.github/workflows/ci.yml` の `<製品> / Frontend`・`<製品> / E2E smoke` は、`changes` の
`<製品>_frontend`（`<製品>/frontend/**` と、frontend のテストが読む backend のファイル）と
共有 UI 等の変更でだけ動く。frontend のテストが新しく backend のファイルを読むのに
filter に足し忘れると、そのファイルの変更で frontend の job が動かず、ずれを見逃す。

検出するのは、frontend の `.ts` / `.tsx` / `.js` / `.mjs` / `.cjs` / `.json` の中の
`../` で始まる文字列（そのファイルのディレクトリからの相対パス）と、
`const NAME = new URL("../dir/", import.meta.url)` を基点にした `new URL("x", NAME)`。
frontend の中・repo の外・存在しないパス（`.env` 等）・ディレクトリは数えない。

実行: python3 platform/scripts/ci_frontend_paths.py（違反があれば終了コード 1）
"""

from __future__ import annotations

import os
import re
import sys
from collections.abc import Iterable
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
CI_WORKFLOW = ".github/workflows/ci.yml"
PRODUCTS = ("rag", "nl2sql", "agent")
# frontend の job を動かす filter（ci.yml の rag-frontend 等の `if` と同じ組み合わせ）。
SHARED_FILTERS = ("platform_frontend", "platform_other", "ci")
EXTRA_FILTERS = {"nl2sql": ("nl2sql_frontend_contract",)}
SUFFIXES = (".ts", ".tsx", ".js", ".mjs", ".cjs", ".json")
SKIP_DIRS = {"node_modules", "dist", "test-results", "playwright-report", "coverage"}
RELATIVE = re.compile(r"""["'`]((?:\.\./)+[^"'`\s$]*)["'`]""")
URL_BASE = re.compile(
    r"""(\w+)\s*=\s*new URL\(\s*["'`]((?:\.\./)+[^"'`\s$]*/)["'`]\s*,\s*import\.meta\.url\s*\)"""
)


def parse_filters(text: str) -> dict[str, list[str]]:
    """ci.yml の dorny/paths-filter の `filters: |` の中の、名前ごとの glob（`!` の除外を含む）。"""
    filters: dict[str, list[str]] = {}
    lines = text.splitlines()
    try:
        start = next(i for i, line in enumerate(lines) if line.strip() == "filters: |")
    except StopIteration as exc:
        raise ValueError(f"{CI_WORKFLOW} に `filters: |` がありません") from exc
    indent = len(lines[start]) - len(lines[start].lstrip()) + 2
    current: str | None = None
    for line in lines[start + 1 :]:
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        depth = len(line) - len(line.lstrip())
        if depth < indent:
            break
        if depth == indent and stripped.endswith(":"):
            current = stripped[:-1]
            filters[current] = []
        elif stripped.startswith("- ") and current is not None:
            filters[current].append(stripped[2:].strip().strip("\"'"))
    return filters


def glob_regex(pattern: str) -> re.Pattern[str]:
    out = ""
    i = 0
    while i < len(pattern):
        if pattern.startswith("**/", i):
            out += "(?:.*/)?"
            i += 3
        elif pattern.startswith("**", i):
            out += ".*"
            i += 2
        elif pattern[i] == "*":
            out += "[^/]*"
            i += 1
        else:
            out += re.escape(pattern[i])
            i += 1
    return re.compile(out + r"\Z")


def matches(path: str, patterns: Iterable[str]) -> bool:
    """1 つの filter の判定（`!` の除外は、その filter で含めたものから除く）。"""
    patterns = list(patterns)
    included = any(glob_regex(p).match(path) for p in patterns if not p.startswith("!"))
    excluded = any(glob_regex(p[1:]).match(path) for p in patterns if p.startswith("!"))
    return included and not excluded


def frontend_reads(product: str, root: Path = ROOT) -> dict[str, set[str]]:
    """frontend の外で読む、repo の中に存在するファイル → 読むファイル（repo 相対）。"""
    frontend = root / product / "frontend"
    reads: dict[str, set[str]] = {}
    for dirpath, dirnames, filenames in os.walk(frontend):
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS]
        for name in filenames:
            if not name.endswith(SUFFIXES):
                continue
            source = Path(dirpath) / name
            text = source.read_text(encoding="utf-8", errors="ignore")
            targets = [Path(dirpath) / m.group(1) for m in RELATIVE.finditer(text)]
            for base_name, base in URL_BASE.findall(text):
                call = re.compile(
                    rf"""new URL\(\s*["'`]([^"'`\s$]+)["'`]\s*,\s*{re.escape(base_name)}\s*\)"""
                )
                targets += [Path(dirpath) / base / m.group(1) for m in call.finditer(text)]
            for target in targets:
                resolved = Path(os.path.normpath(target))
                try:
                    rel = resolved.relative_to(root).as_posix()
                except ValueError:
                    continue
                if rel.startswith(f"{product}/frontend/") or not resolved.is_file():
                    continue
                reads.setdefault(rel, set()).add(source.relative_to(root).as_posix())
    return reads


def check(root: Path = ROOT) -> list[str]:
    filters = parse_filters((root / CI_WORKFLOW).read_text(encoding="utf-8"))
    errors = []
    for product in PRODUCTS:
        names = (f"{product}_frontend", *SHARED_FILTERS, *EXTRA_FILTERS.get(product, ()))
        missing = [n for n in names if n not in filters]
        if missing:
            errors.append(f"{CI_WORKFLOW} に filter {', '.join(missing)} がありません")
            continue
        for path, sources in sorted(frontend_reads(product, root).items()):
            if any(matches(path, filters[name]) for name in names):
                continue
            errors.append(
                f"{path}（{', '.join(sorted(sources))} が読む）が {CI_WORKFLOW} の "
                f"`{product}_frontend` の filter にありません。足さないと、このファイルの変更で "
                f"{product} の frontend・E2E の job が動きません（#1531）"
            )
    return errors


def main() -> int:
    errors = check()
    for error in errors:
        print(f"::error::{error}" if os.environ.get("GITHUB_ACTIONS") else error)
    if not errors:
        print("frontend が読む frontend の外のファイルは、すべて CI の filter に入っています。")
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
