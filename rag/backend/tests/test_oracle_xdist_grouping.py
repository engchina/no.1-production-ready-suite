"""共有 Oracle テストの直列化に必要な pytest / xdist の互換性。"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path


def test_loadgroup_keeps_shared_fixture_tests_on_one_worker(tmp_path: Path) -> None:
    (tmp_path / "pytest.ini").write_text(
        "[pytest]\naddopts = --dist loadgroup\nmarkers = xdist_group(name)\n"
    )
    (tmp_path / "conftest.py").write_text(
        "import pytest\n"
        "@pytest.fixture\ndef shared_database():\n    return 'synthetic'\n"
        "@pytest.hookimpl(tryfirst=True)\n"
        "def pytest_collection_modifyitems(items):\n"
        "    for item in items:\n"
        "        if 'shared_database' in item.fixturenames:\n"
        "            item.add_marker(pytest.mark.xdist_group('shared'))\n"
    )
    for name in ("a", "b"):
        (tmp_path / f"test_{name}.py").write_text(
            "from pathlib import Path\n"
            "def test_worker(shared_database, worker_id):\n"
            "    Path(__file__).with_suffix('.worker').write_text(worker_id)\n"
        )
    env = os.environ | {"PYTEST_ADDOPTS": ""}
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            "-c",
            str(tmp_path / "pytest.ini"),
            f"--confcutdir={tmp_path}",
            "-n",
            "2",
            "-q",
            str(tmp_path),
        ],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        timeout=45,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    workers = [(tmp_path / f"test_{name}.worker").read_text() for name in ("a", "b")]
    assert workers[0] == workers[1], f"共有 fixture のテストが別 worker に分散した: {workers}"
