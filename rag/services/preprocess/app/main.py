"""前処理(Preprocess)マイクロサービス。

共有 contract(rag_parser_core)の app factory を使い、この image に導入された
LibreOffice / PyMuPDF で原本を canonical な中間物へ変換して `ConvertResponse` を返す。
変換依存は本サービス単独で upgrade でき、他 parser / backend に非干渉。
"""

from rag_parser_core.preprocess import (
    PREPROCESS_PROFILES,
    ConvertHealth,
    supported_profiles_from,
)
from rag_parser_core.preprocess_service import create_preprocess_app

from app.converters import convert, pymupdf_available, soffice_path


def _health() -> ConvertHealth:
    """LibreOffice / PyMuPDF の導入状況を返す(readiness 表示の値ソース)。"""
    office_ready = soffice_path() is not None
    pdf_ready = pymupdf_available()
    supported: list[str] = ["passthrough", "text_normalize"]
    if office_ready:
        supported.append("office_to_pdf")
    if pdf_ready:
        supported.append("pdf_to_page_images")
    status = "ok" if (office_ready or pdf_ready) else "degraded"
    return ConvertHealth(
        status=status,
        backend="preprocess",
        package_name="libreoffice+pymupdf",
        package_version=None,
        supported_profiles=supported_profiles_from(supported or list(PREPROCESS_PROFILES)),
    )


app = create_preprocess_app(converter=convert, health_probe=_health, title="preprocess")
