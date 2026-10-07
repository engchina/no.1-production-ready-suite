"""業務支援の評価の合成の資料を作る（#1231）。

`rag/evaluation/business-support/sources/*.html` を LibreOffice（soffice）で PDF にし、
パラメータの一覧の Excel（xlsx）を openpyxl で作って `rag/evaluation/business-support/` に
置く。資料はすべて架空の「サンプル業務ポータル」の内容で、実在の顧客・製品の内容は入れない。

作った PDF / xlsx は commit する（評価の CLI はこのファイルをそのまま取り込む）。原稿を直したら、
リポジトリ直下から次で作り直す（PDF の生成には LibreOffice と日本語のフォントが要る）::

    uv run --project rag/backend python rag/scripts/generate_business_support_corpus.py
"""

from __future__ import annotations

import shutil
import subprocess  # nosec B404 - soffice を固定の引数で呼ぶだけ
import sys
import tempfile
from pathlib import Path

CORPUS_DIR = Path(__file__).resolve().parents[1] / "evaluation" / "business-support"
SOURCES_DIR = CORPUS_DIR / "sources"
PARAMETERS_FILE = "portal-parameters.xlsx"
PARAMETERS_NOTE = (
    "既定値は出荷時の値、設定例は設定の書き方の例で、どちらも各環境の現在の設定値ではありません。"
)

# パラメータの一覧（名前・既定値・設定例・説明）。
# 既定値と設定例は、現場で実際に設定されている値ではない。
PARAMETERS: list[tuple[str, str, str, str]] = [
    ("session_timeout_minutes", "30", "60", "操作が無いときにログインが切れるまでの分数"),
    ("max_upload_mb", "20", "100", "1 ファイルのアップロードの上限（MB）"),
    ("notification_retry", "3", "5", "通知の送信に失敗したときに再送する回数"),
    ("report_retention_days", "365", "730", "出力した集計表を保管する日数"),
]


def main() -> int:
    soffice = shutil.which("soffice") or shutil.which("libreoffice")
    if soffice is None:
        print("soffice（LibreOffice）が見つかりません。", file=sys.stderr)
        return 2
    sources = sorted(SOURCES_DIR.glob("*.html"))
    with tempfile.TemporaryDirectory() as work:
        # 利用者の LibreOffice の設定（profile）を使わず、毎回同じ結果にする。
        profile = Path(work) / "profile"
        subprocess.run(  # nosec B603 - 固定の実行ファイルと引数
            [
                soffice,
                f"-env:UserInstallation=file://{profile}",
                "--headless",
                "--convert-to",
                "pdf:writer_web_pdf_Export",
                "--outdir",
                str(CORPUS_DIR),
                *[str(source) for source in sources],
            ],
            check=True,
            capture_output=True,
            timeout=300,
        )
    _write_parameters(CORPUS_DIR / PARAMETERS_FILE)
    for path in [
        *(CORPUS_DIR / f"{source.stem}.pdf" for source in sources),
        CORPUS_DIR / PARAMETERS_FILE,
    ]:
        if not path.is_file():
            print(f"作れませんでした: {path.name}", file=sys.stderr)
            return 1
        print(f"wrote {path.relative_to(CORPUS_DIR.parents[1])}")
    return 0


def _write_parameters(path: Path) -> None:
    from openpyxl import Workbook

    workbook = Workbook()
    sheet = workbook.active
    assert sheet is not None
    sheet.title = "パラメータ一覧"
    sheet.append(["サンプル業務ポータルのパラメータの一覧（架空。評価用の合成資料）"])
    sheet.append([PARAMETERS_NOTE])
    sheet.append(["パラメータ名", "既定値", "設定例", "説明"])
    for row in PARAMETERS:
        sheet.append(list(row))
    for cell in sheet["B"][3:] + sheet["C"][3:]:
        cell.number_format = "@"
    # 作成時刻を固定し、作り直しても同じ内容にする。
    from datetime import datetime

    fixed = datetime(2026, 4, 1)
    workbook.properties.created = fixed
    workbook.properties.modified = fixed
    workbook.save(path)


if __name__ == "__main__":
    raise SystemExit(main())
