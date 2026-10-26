"""依存の向き（#3240）: suite → platform の公開の package だけ。

製品の module・DB・保存ファイルを読まない。

- suite の src は標準 library・httpx・pydantic・platform の `pr_evaluation_scoring`
  /`pr_enterprise_ai`（OCI Enterprise AI の呼び方）・自分だけを import する（製品の `app.*`・
  `rag_*`・`nl2sql`・Agent の package を import しない）。
- suite の src は製品の DB の driver（`oracledb`）を import せず、製品のディレクトリの path を書か
  ない。
- platform の採点の部品は、標準 library と自分だけを import する（製品・suite を import しない）。
- 製品と platform の package は suite に依存しない。
"""

from __future__ import annotations

import ast
import re
import sys
from pathlib import Path

import pytest

SUITE_SRC = Path(__file__).resolve().parents[1] / "src" / "pr_suite_evaluation"
REPO = Path(__file__).resolve().parents[3]
PLATFORM_SCORING_SRC = REPO / "platform" / "packages" / "evaluation_scoring" / "src"
SUITE_ALLOWED = {
    "httpx",
    "pydantic",
    "pr_evaluation_scoring",
    "pr_enterprise_ai",
    "pr_suite_evaluation",
}
SCORING_ALLOWED = {"pr_evaluation_scoring"}
# 製品のディレクトリの path（実行時に製品の保存ファイル・設定を読まない）。
PRODUCT_PATH = re.compile(r"""["'](?:\.\./)*(?:rag|nl2sql|agent)/(?:backend|frontend|packages)""")


def _imports(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            names.add(node.module.split(".")[0])
    return names


def _sources(root: Path) -> list[Path]:
    paths = sorted(root.rglob("*.py"))
    assert paths
    return paths


@pytest.mark.parametrize(
    ("root", "allowed"),
    [(SUITE_SRC, SUITE_ALLOWED), (PLATFORM_SCORING_SRC, SCORING_ALLOWED)],
    ids=["suite", "platform-scoring"],
)
def test_imports_stay_within_the_allowed_packages(root: Path, allowed: set[str]) -> None:
    for path in _sources(root):
        outside = {
            name
            for name in _imports(path)
            if name not in allowed and name not in sys.stdlib_module_names and name != "__future__"
        }
        assert not outside, (path.relative_to(REPO), sorted(outside))


def test_suite_does_not_read_product_files() -> None:
    for path in _sources(SUITE_SRC):
        text = path.read_text(encoding="utf-8")
        assert not PRODUCT_PATH.search(text), path.relative_to(REPO)
        assert "oracledb" not in _imports(path), path.relative_to(REPO)


def test_products_and_platform_do_not_depend_on_the_suite() -> None:
    """製品と platform の package は suite に依存しない（venv に無いので import もできない）。"""
    manifests = [
        *sorted((REPO / "platform" / "packages").glob("*/pyproject.toml")),
        *(REPO / product / "backend" / "pyproject.toml" for product in ("rag", "nl2sql", "agent")),
    ]
    for path in manifests:
        assert "production-ready-suite-evaluation" not in path.read_text(encoding="utf-8"), path


def test_suite_does_not_install_fastapi() -> None:
    """suite の venv に FastAPI を入れない（OCI Enterprise AI の呼び方は標準 library だけの
    `pr_enterprise_ai` から使う。#3418）。"""
    root = Path(__file__).resolve().parents[1]
    assert "production-ready-system-settings-backend" not in (root / "pyproject.toml").read_text(
        encoding="utf-8"
    )
    assert 'name = "fastapi"' not in (root / "uv.lock").read_text(encoding="utf-8")
