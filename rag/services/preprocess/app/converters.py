"""前処理マイクロサービスの変換実装。

重い変換依存(LibreOffice / PyMuPDF)はこのサービス image にのみ含める。導入が無い
環境では各 converter が passthrough(変換せず)へ縮退し、backend は原本のまま parse する。

- ``office_to_pdf``: LibreOffice headless(`soffice`)で Office→PDF。
- ``pdf_to_page_images``: PyMuPDF で各ページをラスタライズし、画像のみの PDF を再構成する
  (engchina/No.1-PdfParser-Free 風。スキャン/複雑 PDF を VLM OCR 経路へ確実に載せる)。
  単一 artifact 契約(`ConvertResponse`)を保つため、複数画像ではなく画像 PDF を返す。
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


def pymupdf_available() -> bool:
    """PyMuPDF(fitz)が import できるか。"""
    try:
        import fitz  # noqa: F401
    except Exception:
        return False
    return True


def convert(
    source_bytes: bytes,
    content_type: str,
    preprocess_profile: str,
    source_profile: SourceProfile | None,
) -> ConvertOutcome:
    """選択プリセットで変換する。未対応・依存欠如・失敗は passthrough へ縮退する。"""
    if preprocess_profile == "office_to_pdf":
        return _office_to_pdf(source_bytes, source_profile)
    if preprocess_profile == "pdf_to_page_images":
        return _pdf_to_image_pdf(source_bytes)
    # text_normalize は backend in-process で処理済み。auto は backend で具体化済み。
    return ConvertOutcome.passthrough(reason=f"preprocess_unsupported_profile:{preprocess_profile}")


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


def _pdf_to_image_pdf(source_bytes: bytes) -> ConvertOutcome:
    try:
        import fitz  # PyMuPDF
    except Exception:
        return ConvertOutcome.passthrough(reason="pymupdf_unavailable")
    try:
        source = fitz.open(stream=source_bytes, filetype="pdf")
    except Exception:
        return ConvertOutcome.passthrough(reason="pdf_open_failed")
    output = fitz.open()
    page_map: dict[str, int] = {}
    matrix = fitz.Matrix(2.0, 2.0)  # ~144 dpi
    try:
        for index, page in enumerate(source, start=1):
            pixmap = page.get_pixmap(matrix=matrix)
            new_page = output.new_page(width=pixmap.width, height=pixmap.height)
            new_page.insert_image(fitz.Rect(0, 0, pixmap.width, pixmap.height), pixmap=pixmap)
            page_map[str(index)] = index
        derived = output.tobytes()
    except Exception:
        return ConvertOutcome.passthrough(reason="pdf_rasterize_failed")
    finally:
        source.close()
        output.close()
    if not page_map or not derived:
        return ConvertOutcome.passthrough(reason="pdf_rasterize_empty")
    return ConvertOutcome(
        converted=True,
        converter_name="pdf_rasterize",
        converter_version=str(getattr(fitz, "VersionBind", "v1")),
        derived_bytes=derived,
        derived_content_type="application/pdf",
        page_map=page_map,
    )
