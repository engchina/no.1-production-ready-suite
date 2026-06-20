"""Office→PDF 前処理マイクロサービスの変換実装。

LibreOffice headless(`soffice`)で Office 文書を PDF へ変換する。LibreOffice はこの
サービス image にのみ含め、他 parser / backend に非干渉。未導入・変換失敗のときは
passthrough(変換せず原本を使う)へ縮退する。
"""

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile

from rag_parser_core.preprocess import ConvertOutcome
from rag_parser_core.source import SourceProfile

_OFFICE_SUFFIX_BY_EXTENSION = {
    "docx": ".docx",
    "doc": ".doc",
    "pptx": ".pptx",
    "ppt": ".ppt",
    "xlsx": ".xlsx",
    "xls": ".xls",
    "odt": ".odt",
    "odp": ".odp",
    "ods": ".ods",
    "rtf": ".rtf",
}


def soffice_path() -> str | None:
    """利用可能な LibreOffice 実行ファイルを返す(未導入なら None)。"""
    return shutil.which("soffice") or shutil.which("libreoffice")


def convert(
    source_bytes: bytes,
    content_type: str,
    preprocess_profile: str,
    source_profile: SourceProfile | None,
) -> ConvertOutcome:
    """選択プリセットで変換する。office_to_pdf 以外・依存欠如・失敗は passthrough へ縮退する。"""
    if preprocess_profile != "office_to_pdf":
        return ConvertOutcome.passthrough(
            reason=f"preprocess_unsupported_profile:{preprocess_profile}"
        )
    return _office_to_pdf(source_bytes, source_profile)


def _office_suffix(source_profile: SourceProfile | None) -> str:
    extension = (source_profile.extension if source_profile is not None else None) or ""
    return _OFFICE_SUFFIX_BY_EXTENSION.get(extension.strip().lower().lstrip("."), ".docx")


def _office_to_pdf(source_bytes: bytes, source_profile: SourceProfile | None) -> ConvertOutcome:
    soffice = soffice_path()
    if soffice is None:
        return ConvertOutcome.passthrough(reason="libreoffice_unavailable")
    suffix = _office_suffix(source_profile)
    with tempfile.TemporaryDirectory() as tmp:
        in_path = os.path.join(tmp, f"input{suffix}")
        with open(in_path, "wb") as handle:
            handle.write(source_bytes)
        # soffice は同時実行で profile を奪い合うため、専用 HOME を渡して衝突を避ける。
        env = {**os.environ, "HOME": tmp}
        command = [
            soffice,
            "--headless",
            "--norestore",
            "--convert-to",
            "pdf",
            "--outdir",
            tmp,
            in_path,
        ]
        try:
            subprocess.run(
                command,
                check=True,
                capture_output=True,
                timeout=240,
                env=env,
            )
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired, OSError):
            return ConvertOutcome.passthrough(reason="office_to_pdf_failed")
        outputs = [name for name in os.listdir(tmp) if name.lower().endswith(".pdf")]
        if not outputs:
            return ConvertOutcome.passthrough(reason="office_to_pdf_no_output")
        with open(os.path.join(tmp, outputs[0]), "rb") as handle:
            pdf_bytes = handle.read()
    if not pdf_bytes:
        return ConvertOutcome.passthrough(reason="office_to_pdf_empty")
    return ConvertOutcome(
        converted=True,
        converter_name="libreoffice",
        converter_version="v1",
        derived_bytes=pdf_bytes,
        derived_content_type="application/pdf",
    )
