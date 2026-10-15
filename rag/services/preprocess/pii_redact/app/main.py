"""PII マスク(pii_redact)前処理マイクロサービス。

共有 contract(rag_parser_core)の app factory を使い、原本テキストの PII を Presidio +
日本語 NER でマスクして `ConvertResponse` を返す。ローカル OSS のみで完結し外部 SaaS は
呼ばない。重い NLP 依存(Presidio / spaCy)はこのサービスに隔離し、他 parser / backend に非干渉。
"""

from importlib import metadata

from rag_parser_core.preprocess import ConvertHealth, supported_profiles_from
from rag_parser_core.preprocess_service import create_preprocess_app

from app.converters import convert


def _health() -> ConvertHealth:
    """Presidio が import できれば ready、無ければ degraded。"""
    try:
        import presidio_analyzer  # noqa: F401

        status = "ok"
        # presidio_analyzer は __version__ を持たないため、配布物の metadata から読む。
        version: str | None = metadata.version("presidio-analyzer")
    except Exception:  # noqa: BLE001 - 依存欠如は degraded として可視化する
        status = "degraded"
        version = None
    return ConvertHealth(
        status=status,
        backend="preprocess-pii-redact",
        package_name="presidio-analyzer",
        package_version=version,
        supported_profiles=supported_profiles_from(["pii_redact"]),
    )


app = create_preprocess_app(converter=convert, health_probe=_health, title="preprocess-pii-redact")
