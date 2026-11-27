"""Wallet を小さくする extract_wallet.sh（external data source）は、同じ入力から必ず同じ出力を返す（#5945）。

出力が実行のたびに変わるか、plan の時点で決まらないと、cloud-init の user_data が変わり、stack を apply するたびに
Compute が作り直される。
"""

from __future__ import annotations

import base64
import io
import json
import os
import subprocess
import tempfile
import time
import unittest
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
SCRIPT = ROOT / "terraform/stack/extract_wallet.sh"
KEPT = ["cwallet.sso", "ewallet.pem", "sqlnet.ora", "tnsnames.ora"]
DROPPED = ["README", "keystore.jks", "truststore.jks", "ojdbc.properties", "ewallet.p12"]


def _query() -> str:
    """合成の Wallet の ZIP（実物と同じファイルの名前。内容は合成）。"""
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for name in [*KEPT, *DROPPED]:
            archive.writestr(name, f"synthetic {name}\n" * 20)
    return json.dumps({"wallet_base64": base64.b64encode(buffer.getvalue()).decode()})


def _run(query: str, *, cwd: str, timezone: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["bash", str(SCRIPT)],
        input=query,
        capture_output=True,
        text=True,
        cwd=cwd,
        env={**os.environ, "TZ": timezone},
        check=False,
    )


class ExtractWalletTests(unittest.TestCase):
    def test_same_input_gives_same_output_and_leaves_no_files(self) -> None:
        query = _query()
        with tempfile.TemporaryDirectory() as first, tempfile.TemporaryDirectory() as second:
            one = _run(query, cwd=first, timezone="UTC")
            time.sleep(2.1)  # ZIP の時刻は 2 秒の単位。実行の時刻が出力に入らないことを確かめる。
            two = _run(query, cwd=second, timezone="Asia/Tokyo")
            self.assertEqual(one.returncode, 0, one.stderr)
            self.assertEqual(one.stdout, two.stdout)
            # 作業ディレクトリ（stack のディレクトリ）にファイルを残さない・頼らない。
            self.assertEqual(os.listdir(first), [])
        content = base64.b64decode(json.loads(one.stdout)["wallet_content"])
        with zipfile.ZipFile(io.BytesIO(content)) as archive:
            self.assertEqual(sorted(archive.namelist()), KEPT)

    def test_empty_input_fails(self) -> None:
        with tempfile.TemporaryDirectory() as cwd:
            self.assertNotEqual(_run("{}", cwd=cwd, timezone="UTC").returncode, 0)


if __name__ == "__main__":
    unittest.main()
