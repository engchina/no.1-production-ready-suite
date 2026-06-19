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
from urllib.parse import urlparse

from app.config import Settings

ServiceCategory = Literal["preprocess", "parser"]
ServiceProfile = Literal["cpu", "gpu"]
# dev(ホスト)での起動方式。軽量な前処理は uv プロセス、重い ML 依存の parser は
# (dev でも)docker compose で起動する。prod は常に docker。
DevRunner = Literal["uv", "docker"]


@dataclass(frozen=True)
class ServiceCatalogEntry:
    """1 マイクロサービスのメタデータ。

    - ``service_id``: docker compose の service 名(allowlist の鍵)。
    - ``url_field``: base URL を持つ Settings フィールド名(prod の /health 問い合わせ用)。
    - ``label_key``: フロントの i18n キー(表示名)。
    - ``working_dir``: リポジトリ root からの相対パス(dev の ``uv run --directory`` 起動先)。
    - ``dev_port``: dev で localhost に bind / 公開するポート(uv プロセス、または docker の公開先)。
    - ``dev_runner``: dev での起動方式(``uv``=ホストプロセス / ``docker``=コンテナ)。
    """

    service_id: str
    category: ServiceCategory
    profile: ServiceProfile
    url_field: str
    label_key: str
    working_dir: str
    dev_port: int
    dev_runner: DevRunner


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
        dev_runner="uv",
    ),
    ServiceCatalogEntry(
        service_id="preprocess-pdf-to-page-images",
        category="preprocess",
        profile="cpu",
        url_field="rag_preprocess_pdf_to_page_images_service_url",
        label_key="settings.services.item.preprocessPdfToPageImages",
        working_dir="services/preprocess/pdf_to_page_images",
        dev_port=8011,
        dev_runner="uv",
    ),
    ServiceCatalogEntry(
        service_id="preprocess-csv-to-json",
        category="preprocess",
        profile="cpu",
        url_field="rag_preprocess_csv_to_json_service_url",
        label_key="settings.services.item.preprocessCsvToJson",
        working_dir="services/preprocess/csv_to_json",
        dev_port=8012,
        dev_runner="uv",
    ),
    ServiceCatalogEntry(
        service_id="preprocess-excel-to-json",
        category="preprocess",
        profile="cpu",
        url_field="rag_preprocess_excel_to_json_service_url",
        label_key="settings.services.item.preprocessExcelToJson",
        working_dir="services/preprocess/excel_to_json",
        dev_port=8013,
        dev_runner="uv",
    ),
    ServiceCatalogEntry(
        service_id="preprocess-url-to-markdown",
        category="preprocess",
        profile="cpu",
        url_field="rag_preprocess_url_to_markdown_service_url",
        label_key="settings.services.item.preprocessUrlToMarkdown",
        working_dir="services/preprocess/url_to_markdown",
        dev_port=8014,
        dev_runner="uv",
    ),
    ServiceCatalogEntry(
        service_id="preprocess-image-enhance",
        category="preprocess",
        profile="cpu",
        url_field="rag_preprocess_image_enhance_service_url",
        label_key="settings.services.item.preprocessImageEnhance",
        working_dir="services/preprocess/image_enhance",
        dev_port=8015,
        dev_runner="uv",
    ),
    ServiceCatalogEntry(
        service_id="preprocess-pii-redact",
        category="preprocess",
        profile="cpu",
        url_field="rag_preprocess_pii_redact_service_url",
        label_key="settings.services.item.preprocessPiiRedact",
        working_dir="services/preprocess/pii_redact",
        dev_port=8016,
        dev_runner="uv",
    ),
    ServiceCatalogEntry(
        service_id="parser-docling",
        category="parser",
        profile="cpu",
        url_field="rag_parser_docling_service_url",
        label_key="settings.services.item.parserDocling",
        working_dir="services/parsers/docling",
        dev_port=8020,
        dev_runner="docker",
    ),
    ServiceCatalogEntry(
        service_id="parser-marker",
        category="parser",
        profile="cpu",
        url_field="rag_parser_marker_service_url",
        label_key="settings.services.item.parserMarker",
        working_dir="services/parsers/marker",
        dev_port=8021,
        dev_runner="docker",
    ),
    ServiceCatalogEntry(
        service_id="parser-unstructured",
        category="parser",
        profile="cpu",
        url_field="rag_parser_unstructured_service_url",
        label_key="settings.services.item.parserUnstructured",
        working_dir="services/parsers/unstructured",
        dev_port=8022,
        dev_runner="docker",
    ),
    ServiceCatalogEntry(
        service_id="parser-mineru",
        category="parser",
        profile="gpu",
        url_field="rag_parser_mineru_service_url",
        label_key="settings.services.item.parserMineru",
        working_dir="services/parsers/mineru",
        dev_port=8023,
        dev_runner="docker",
    ),
    ServiceCatalogEntry(
        service_id="parser-dots-ocr",
        category="parser",
        profile="gpu",
        url_field="rag_parser_dots_ocr_service_url",
        label_key="settings.services.item.parserDotsOcr",
        working_dir="services/parsers/dots_ocr",
        dev_port=8024,
        dev_runner="docker",
    ),
    ServiceCatalogEntry(
        service_id="parser-glm-ocr",
        category="parser",
        profile="gpu",
        url_field="rag_parser_glm_ocr_service_url",
        label_key="settings.services.item.parserGlmOcr",
        working_dir="services/parsers/glm_ocr",
        dev_port=8025,
        dev_runner="docker",
    ),
)

_CATALOG_BY_ID: dict[str, ServiceCatalogEntry] = {
    entry.service_id: entry for entry in SERVICE_CATALOG
}
_CATALOG_BY_URL_FIELD: dict[str, ServiceCatalogEntry] = {
    entry.url_field: entry for entry in SERVICE_CATALOG
}


def get_catalog_entry(service_id: str) -> ServiceCatalogEntry | None:
    """service_id に対応するカタログエントリを返す(allowlist 照合)。未知なら None。"""
    return _CATALOG_BY_ID.get(service_id)


def is_dev_mode(settings: Settings) -> bool:
    """local 環境が dev(uv プロセス起動)か判定する。

    ``ENVIRONMENT`` を流用し、``prod``/``production`` 以外は dev とみなす
    (readiness の production 判定と整合)。dev では各サービスをホスト上の
    ``uv`` プロセスとして起動/停止し、prod では docker compose を使う。
    """
    return settings.environment.strip().lower() not in {"prod", "production"}


def resolve_service_base_url(settings: Settings, url_field: str) -> str:
    """設定 ``url_field`` のサービス base URL を dev/prod に応じて解決する(末尾スラッシュ除去)。

    dev では docker compose の service 名(``parser-docling`` 等)をホストから解決できない。
    そこで **設定が docker 既定(host が compose service 名)のときだけ** catalog の
    ``dev_port`` から ``http://127.0.0.1:<port>`` に書き換える(uv はホストで bind、docker は
    ``docker-compose.dev.yml`` で同ポートを公開)。空欄(=未設定)や明示上書き(localhost 等)は
    そのまま尊重する。prod は常に設定値そのまま。

    稼働プローブ(/health)と取込の HTTP 委譲(/parse・/convert)で **同じ解決**を使い、
    dev で「画面では到達できるのに取込では docker 名で失敗」という不整合を防ぐ。
    """
    raw = str(getattr(settings, url_field, "") or "").strip().rstrip("/")
    entry = _CATALOG_BY_URL_FIELD.get(url_field)
    if entry is None or not is_dev_mode(settings) or not raw:
        return raw
    # docker 既定(host == compose service 名)のみ localhost:<dev_port> へ。明示上書きは尊重。
    host = urlparse(raw).hostname
    if host == entry.service_id:
        return f"http://127.0.0.1:{entry.dev_port}"
    return raw


def service_health_url(settings: Settings, entry: ServiceCatalogEntry) -> str:
    """エントリの /health base URL を返す(dev は 127.0.0.1:<dev_port>、prod は url_field)。"""
    return resolve_service_base_url(settings, entry.url_field)
