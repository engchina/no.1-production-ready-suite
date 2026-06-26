"""例ファイルで 7 つの前処理サービスを実依存込みで自動検証する。

各サービスは独立 venv(いずれも `app.converters`)で名前衝突するため 1 プロセスで
まとめて import できない。よって本ランナーは各サービスディレクトリで `uv run` を
subprocess 起動し、それぞれの venv 内で `convert()` を実行して結果を検証する。

実行:
    python examples/test-preprocess/test_examples.py      # 直接(全件まとめ)
    cd examples/test-preprocess && uv run pytest           # pytest として(要 uv)

各サービスは実バイナリ依存(LibreOffice / PyMuPDF / openpyxl / OpenCV / Presidio)。
依存欠如・変換失敗時は passthrough(converted=False)へ縮退し、本テストは fail する。
url_to_markdown は実ネットを避けるため fetcher/extractor を注入する。
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

EX = Path(__file__).resolve().parent
SERVICES = EX.parent.parent / "services" / "preprocess"

# サービス snippet は venv 内で実行され、検証して "PASS:<detail>" を最終行に出す。
# 失敗時は AssertionError(非ゼロ終了)で落ちる。
_OFFICE = """
import os, app.converters as c
from rag_parser_core.source import SourceProfile, SourceModality
d = open(os.environ["EX"] + "/document.docx", "rb").read()
sp = SourceProfile(original_file_name="document.docx", sanitized_file_name="document.docx",
    extension=".docx",
    content_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    file_size_bytes=len(d), content_sha256="0"*64, modality=SourceModality.OFFICE,
    parser_profile="unstructured")
o = c.convert(d, sp.content_type, "office_to_pdf", sp)
assert o.converted and o.derived_bytes[:5] == b"%PDF-", o.warnings
print("PASS:pdf", len(o.derived_bytes))
"""

_PDF = """
import os, app.converters as c
d = open(os.environ["EX"] + "/pages.pdf", "rb").read()
o = c.convert(d, "application/pdf", "pdf_to_page_images", None)
assert o.converted and o.derived_bytes[:5] == b"%PDF-" and o.page_map, o.warnings
print("PASS:pages", o.page_map)
"""

_CSV = """
import os, json, app.converters as c
d = open(os.environ["EX"] + "/table.csv", "rb").read()
o = c.convert(d, "text/csv", "csv_to_json", None)
assert o.converted, o.warnings
data = json.loads(o.derived_bytes)
assert data["row_count"] == 4 and "name" in data["columns"], data
print("PASS:rows", data["row_count"])
"""

_XLSX = """
import os, json, app.converters as c
d = open(os.environ["EX"] + "/table.xlsx", "rb").read()
o = c.convert(d, "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
              "excel_to_json", None)
assert o.converted, o.warnings
data = json.loads(o.derived_bytes)
assert len(data["sheets"]) == 2, data
print("PASS:sheets", len(data["sheets"]))
"""

_URL = """
import os, app.converters as c
d = open(os.environ["EX"] + "/urls.txt", "rb").read()
assert c._parse_urls(d), "no urls parsed"
o = c.convert(d, "text/plain", "url_to_markdown", None,
              fetcher=lambda u, **k: "<html><body><h1>" + u + "</h1><p>本文</p></body></html>",
              url_guard=lambda u: True)
assert o.converted and b"## Source:" in o.derived_bytes, o.warnings
print("PASS:md", len(o.derived_bytes))
"""

_IMG = """
import os, app.converters as c
d = open(os.environ["EX"] + "/photo.png", "rb").read()
o = c.convert(d, "image/png", "image_enhance", None)
assert o.converted and o.derived_bytes[:4] == b"\\x89PNG", o.warnings
print("PASS:png", len(d), "->", len(o.derived_bytes))
"""

_PII = """
import os, app.converters as c
d = open(os.environ["EX"] + "/pii.txt", "rb").read()
o = c.convert(d, "text/plain", "pii_redact", None)
kinds = {w.split(":")[1].split("=")[0] for w in o.warnings if w.startswith("pii_redacted:")}
assert o.converted and {"EMAIL_ADDRESS", "PHONE_NUMBER"} <= kinds, o.warnings
print("PASS:pii", sorted(kinds))
"""

CASES = {
    "office_to_pdf": _OFFICE,
    "pdf_to_page_images": _PDF,
    "csv_to_json": _CSV,
    "excel_to_json": _XLSX,
    "url_to_markdown": _URL,
    "image_enhance": _IMG,
    "pii_redact": _PII,
}


def _run(service: str, snippet: str) -> str:
    proc = subprocess.run(
        ["uv", "run", "python", "-c", snippet],
        cwd=SERVICES / service,
        env={**__import__("os").environ, "EX": str(EX)},
        capture_output=True,
        text=True,
        timeout=600,
    )
    if proc.returncode != 0:
        raise AssertionError(f"{service} failed:\n{proc.stdout}\n{proc.stderr}")
    return proc.stdout.strip().splitlines()[-1]


# pytest が個別ケースとして拾えるようにパラメタライズ(uv 環境なら)
try:
    import pytest

    @pytest.mark.parametrize("service", list(CASES))
    def test_preprocess_example(service: str) -> None:
        assert _run(service, CASES[service]).startswith("PASS:")
except ImportError:  # pytest 無しでも __main__ で回せる
    pass


if __name__ == "__main__":
    failures = 0
    for service, snippet in CASES.items():
        try:
            detail = _run(service, snippet)
            print(f"  OK  {service:20s} {detail}")
        except AssertionError as exc:
            failures += 1
            print(f"  FAIL {service:20s}\n{exc}")
    print(f"\n{len(CASES) - failures}/{len(CASES)} passed")
    sys.exit(1 if failures else 0)
