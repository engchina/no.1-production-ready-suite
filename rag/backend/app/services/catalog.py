"""サービスカタログ。

前処理(`services/preprocess/*`)と Parser(`services/parsers/*`)の各マイクロサービスを
1 つの静的レジストリに統合する。`service_id` は docker-compose.yml の service 名と一致させ、
起動/停止(compose 制御)と稼働状態プローブ(/health)の双方の正本にする。

URL 設定名は既存実装(`parser_adapter_readiness._SERVICE_URL_FIELDS` /
`preprocess_strategy.PREPROCESS_SERVICE_URL_ATTRS`)と一致させる。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from app.config import Settings

ServiceCategory = Literal["preprocess", "parser"]
ServiceProfile = Literal["cpu", "gpu"]


@dataclass(frozen=True)
class ServiceCatalogEntry:
    """1 マイクロサービスのメタデータ。

    - ``service_id``: docker compose の service 名(allowlist の鍵)。
    - ``url_field``: base URL を持つ Settings フィールド名(prod の /health 問い合わせ用)。
    - ``label_key``: フロントの i18n キー(表示名)。
    - ``working_dir``: リポジトリ root からの相対パス(dev の ``uv run --directory`` 起動先)。
    - ``dev_port``: dev(uv プロセス)起動時に bind する localhost ポート。
    """

    service_id: str
    category: ServiceCategory
    profile: ServiceProfile
    url_field: str
    label_key: str
    working_dir: str
    dev_port: int


# パイプライン順に並べる(前処理 → Parser CPU → Parser GPU)。
SERVICE_CATALOG: tuple[ServiceCatalogEntry, ...] = (
    ServiceCatalogEntry(
        service_id="preprocess-office-to-pdf",
        category="preprocess",
        profile="cpu",
        url_field="rag_preprocess_office_to_pdf_service_url",
        label_key="settings.services.item.preprocessOfficeToPdf",
        working_dir="services/preprocess/office_to_pdf",
        dev_port=8010,
    ),
    ServiceCatalogEntry(
        service_id="preprocess-pdf-to-page-images",
        category="preprocess",
        profile="cpu",
        url_field="rag_preprocess_pdf_to_page_images_service_url",
        label_key="settings.services.item.preprocessPdfToPageImages",
        working_dir="services/preprocess/pdf_to_page_images",
        dev_port=8011,
    ),
    ServiceCatalogEntry(
        service_id="preprocess-csv-to-json",
        category="preprocess",
        profile="cpu",
        url_field="rag_preprocess_csv_to_json_service_url",
        label_key="settings.services.item.preprocessCsvToJson",
        working_dir="services/preprocess/csv_to_json",
        dev_port=8012,
    ),
    ServiceCatalogEntry(
        service_id="preprocess-excel-to-json",
        category="preprocess",
        profile="cpu",
        url_field="rag_preprocess_excel_to_json_service_url",
        label_key="settings.services.item.preprocessExcelToJson",
        working_dir="services/preprocess/excel_to_json",
        dev_port=8013,
    ),
    ServiceCatalogEntry(
        service_id="parser-docling",
        category="parser",
        profile="cpu",
        url_field="rag_parser_docling_service_url",
        label_key="settings.services.item.parserDocling",
        working_dir="services/parsers/docling",
        dev_port=8020,
    ),
    ServiceCatalogEntry(
        service_id="parser-marker",
        category="parser",
        profile="cpu",
        url_field="rag_parser_marker_service_url",
        label_key="settings.services.item.parserMarker",
        working_dir="services/parsers/marker",
        dev_port=8021,
    ),
    ServiceCatalogEntry(
        service_id="parser-unstructured",
        category="parser",
        profile="cpu",
        url_field="rag_parser_unstructured_service_url",
        label_key="settings.services.item.parserUnstructured",
        working_dir="services/parsers/unstructured",
        dev_port=8022,
    ),
    ServiceCatalogEntry(
        service_id="parser-mineru",
        category="parser",
        profile="gpu",
        url_field="rag_parser_mineru_service_url",
        label_key="settings.services.item.parserMineru",
        working_dir="services/parsers/mineru",
        dev_port=8023,
    ),
    ServiceCatalogEntry(
        service_id="parser-dots-ocr",
        category="parser",
        profile="gpu",
        url_field="rag_parser_dots_ocr_service_url",
        label_key="settings.services.item.parserDotsOcr",
        working_dir="services/parsers/dots_ocr",
        dev_port=8024,
    ),
)

_CATALOG_BY_ID: dict[str, ServiceCatalogEntry] = {
    entry.service_id: entry for entry in SERVICE_CATALOG
}


def get_catalog_entry(service_id: str) -> ServiceCatalogEntry | None:
    """service_id に対応するカタログエントリを返す(allowlist 照合)。未知なら None。"""
    return _CATALOG_BY_ID.get(service_id)


def is_dev_mode(settings: Settings) -> bool:
    """local 環境が dev(uv プロセス起動)か判定する。

    ``ENVIRONMENT`` を流用し、``production``/``prod`` 以外は dev とみなす
    (readiness の production 判定と整合)。dev では各サービスをホスト上の
    ``uv`` プロセスとして起動/停止し、prod では docker compose を使う。
    """
    return settings.environment.strip().lower() not in {"production", "prod"}


def service_health_url(settings: Settings, entry: ServiceCatalogEntry) -> str:
    """エントリの base URL を返す(末尾スラッシュを除去)。未設定なら空文字。

    dev(uv プロセス)では docker 名は解決できないため、catalog の ``dev_port`` から
    ``http://127.0.0.1:<port>`` を組み立てて返す。prod は Settings の ``url_field``。
    """
    if is_dev_mode(settings):
        return f"http://127.0.0.1:{entry.dev_port}"
    return str(getattr(settings, entry.url_field, "") or "").strip().rstrip("/")
