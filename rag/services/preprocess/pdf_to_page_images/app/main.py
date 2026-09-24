"""PDF→ページ画像PDF 前処理マイクロサービス。

共有 contract(rag_parser_core)の app factory を使い、この image に導入された PyMuPDF で
各ページをラスタライズした画像 PDF を返す。変換依存は本サービス単独で upgrade でき、
他 parser / backend に非干渉。
"""

from rag_parser_core.preprocess import ConvertHealth, supported_profiles_from
from rag_parser_core.preprocess_service import create_preprocess_app

from app.converters import convert, pymupdf_available


def _health() -> ConvertHealth:
    """PyMuPDF の導入状況を返す(readiness 表示の値ソース)。"""
    pdf_ready = pymupdf_available()
    return ConvertHealth(
        status="ok" if pdf_ready else "degraded",
        backend="preprocess-pdf-to-page-images",
        package_name="pymupdf",
        package_version=None,
        supported_profiles=supported_profiles_from(
            ["pdf_to_page_images"] if pdf_ready else ["passthrough"]
        ),
    )


app = create_preprocess_app(
    converter=convert, health_probe=_health, title="preprocess-pdf-to-page-images"
)
