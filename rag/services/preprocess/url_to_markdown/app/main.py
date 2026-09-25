"""URL→Markdown 前処理マイクロサービス。

共有 contract(rag_parser_core)の app factory を使い、URL リストを取得して boilerplate を
除去した Markdown へ再マップし `ConvertResponse` を返す。Web ページ取込(Firecrawl 相当)を
ローカル OSS(trafilatura)だけで実現し、外部 SaaS は呼ばない。他 parser / backend に非干渉。
"""

from rag_parser_core.preprocess import ConvertHealth, supported_profiles_from
from rag_parser_core.preprocess_service import create_preprocess_app

from app.converters import convert


def _health() -> ConvertHealth:
    """trafilatura が import できれば ready、無ければ degraded。"""
    try:
        import trafilatura  # noqa: F401

        status = "ok"
        version: str | None = getattr(__import__("trafilatura"), "__version__", None)
    except Exception:  # noqa: BLE001 - 依存欠如は degraded として可視化する
        status = "degraded"
        version = None
    return ConvertHealth(
        status=status,
        backend="preprocess-url-to-markdown",
        package_name="trafilatura",
        package_version=version,
        supported_profiles=supported_profiles_from(["url_to_markdown"]),
    )


app = create_preprocess_app(
    converter=convert, health_probe=_health, title="preprocess-url-to-markdown"
)
