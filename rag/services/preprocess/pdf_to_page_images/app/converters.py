"""PDF→ページ画像PDF 前処理マイクロサービスの変換実装。

PyMuPDF で各ページをラスタライズし、画像のみの PDF を再構成する
(engchina/No.1-PdfParser-Free 風。スキャン/複雑 PDF を VLM OCR 経路へ確実に載せる)。
単一 artifact 契約(`ConvertResponse`)を保つため、複数画像ではなく画像 PDF を返す。
PyMuPDF はこのサービス image にのみ含め、他 parser / backend に非干渉。
"""

from __future__ import annotations

from rag_parser_core.preprocess import ConvertOutcome
from rag_parser_core.source import SourceProfile


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
    """選択プリセットで変換する。対象外・依存欠如・失敗は passthrough へ縮退する。"""
    if preprocess_profile != "pdf_to_page_images":
        return ConvertOutcome.passthrough(
            reason=f"preprocess_unsupported_profile:{preprocess_profile}"
        )
    return _pdf_to_image_pdf(source_bytes)


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
