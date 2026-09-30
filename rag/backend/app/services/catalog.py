"""サービスカタログ。

前処理(`services/preprocess/*`)と Parser(`services/parsers/*`)の各マイクロサービスを
1 つの静的レジストリに統合する。`service_id` は systemd の unit 名
(``production-ready-rag-<service_id>.service``)の元になり、起動/停止(systemd 制御)と
稼働状態プローブ(``systemctl show`` + /health)の双方の正本にする(#286)。

各サービスはサービスごとの uv の venv で動くネイティブのプロセスで、``127.0.0.1:<port>`` で
listen する(本番は ``rag/init_script.sh``、開発は ``rag/scripts/rag-services.sh`` が unit を作る)。

URL 設定名は既存実装(`parser_adapter_readiness._SERVICE_URL_FIELDS` /
`preprocess_strategy.PREPROCESS_SERVICE_URL_ATTRS`)と一致させる。
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal

from app.config import Settings

ServiceCategory = Literal[
    "preprocess",
    "parser",
    # pipeline 各ステージのプラグイン(マイクロサービス)化。順次追加する。
    "chunking",
    "vector_index",
    "guardrail",
    "evaluation",
    "graphrag",
]
# cpu/gpu はローカル ML 依存の重さで分ける。oci は OCI クラウドサービスを呼ぶ薄い
# プロキシ microservice(GPU 不要・OCI 認証はメイン設定を継承)。
ServiceProfile = Literal["cpu", "gpu", "oci"]
ServiceExecutionPolicy = Literal[
    "required_no_fallback",
    "in_process_when_disabled",
    "selected_adapter",
]

# systemd の unit 名の接頭辞。init_script.sh / scripts/rag-services.sh が作る unit と同じ。
SYSTEMD_UNIT_PREFIX = "production-ready-rag-"
# 画面から操作してよい unit 名の形(allowlist と併せて検証する。shell のメタ文字・パスを通さない)。
SYSTEMD_UNIT_NAME_RE = re.compile(r"^production-ready-rag-[a-z0-9]+(?:-[a-z0-9]+)*\.service$")


@dataclass(frozen=True)
class ServiceCatalogEntry:
    """1 マイクロサービスのメタデータ。

    - ``service_id``: サービスの識別子(allowlist の鍵)。systemd の unit 名の元になる。
    - ``url_field``: base URL を持つ Settings フィールド名(/health 問い合わせ・取込の委譲先)。
    - ``label_key``: フロントの i18n キー(表示名)。
    - ``working_dir``: リポジトリ root からのサービス実装の相対パス(uv の venv を置く場所)。
    - ``port``: ``127.0.0.1`` で listen するポート(本番・開発で共通)。URL 設定の既定値と一致させる。
    - ``execution_policy``: 停止時・未使用時の runtime 契約。UI/API で fallback 境界を明示する。
    - ``deployable``: UI/API から起動/停止を提供し、systemd の unit を持つか。
      ``execution_policy``(停止時の fallback 契約)とは別軸。False のステージは backend 内処理
      (``rag_pipeline_core``)で動作し、サービス化は将来対応(操作系を出さず /health も叩かない)。
    - ``model_cache_path``: モデル DL を行うサービスのキャッシュ親(実行ユーザーの ``~/.cache``)。
      HF・docling・mineru など tool 別キャッシュをまとめて含めるため ``huggingface`` ではなく
      ``.cache`` 親を指す。None のサービスはモデル DL なし。
    """

    service_id: str
    category: ServiceCategory
    profile: ServiceProfile
    url_field: str
    label_key: str
    working_dir: str
    port: int
    execution_policy: ServiceExecutionPolicy = "selected_adapter"
    deployable: bool = True
    model_cache_path: str | None = None

    @property
    def systemd_unit(self) -> str | None:
        """このサービスの systemd の unit 名。backend 内処理のステージ(deployable=False)は None。"""
        if not self.deployable:
            return None
        return f"{SYSTEMD_UNIT_PREFIX}{self.service_id}.service"

    @property
    def default_url(self) -> str:
        """URL 設定の既定値(ネイティブ配備で listen する ``127.0.0.1:<port>``)。"""
        return f"http://127.0.0.1:{self.port}"


# パイプライン順に並べる(前処理 → Parser CPU → Parser GPU)。
SERVICE_CATALOG: tuple[ServiceCatalogEntry, ...] = (
    ServiceCatalogEntry(
        service_id="preprocess-office-to-pdf",
        category="preprocess",
        profile="cpu",
        url_field="rag_preprocess_office_to_pdf_service_url",
        label_key="settings.services.item.preprocessOfficeToPdf",
        working_dir="services/preprocess/office_to_pdf",
        port=18010,
    ),
    ServiceCatalogEntry(
        service_id="preprocess-pdf-to-page-images",
        category="preprocess",
        profile="cpu",
        url_field="rag_preprocess_pdf_to_page_images_service_url",
        label_key="settings.services.item.preprocessPdfToPageImages",
        working_dir="services/preprocess/pdf_to_page_images",
        port=18011,
    ),
    ServiceCatalogEntry(
        service_id="preprocess-csv-to-json",
        category="preprocess",
        profile="cpu",
        url_field="rag_preprocess_csv_to_json_service_url",
        label_key="settings.services.item.preprocessCsvToJson",
        working_dir="services/preprocess/csv_to_json",
        port=18012,
    ),
    ServiceCatalogEntry(
        service_id="preprocess-excel-to-json",
        category="preprocess",
        profile="cpu",
        url_field="rag_preprocess_excel_to_json_service_url",
        label_key="settings.services.item.preprocessExcelToJson",
        working_dir="services/preprocess/excel_to_json",
        port=18013,
    ),
    ServiceCatalogEntry(
        service_id="preprocess-url-to-markdown",
        category="preprocess",
        profile="cpu",
        url_field="rag_preprocess_url_to_markdown_service_url",
        label_key="settings.services.item.preprocessUrlToMarkdown",
        working_dir="services/preprocess/url_to_markdown",
        port=18014,
    ),
    ServiceCatalogEntry(
        service_id="preprocess-image-enhance",
        category="preprocess",
        profile="cpu",
        url_field="rag_preprocess_image_enhance_service_url",
        label_key="settings.services.item.preprocessImageEnhance",
        working_dir="services/preprocess/image_enhance",
        port=18015,
    ),
    ServiceCatalogEntry(
        service_id="preprocess-pii-redact",
        category="preprocess",
        profile="cpu",
        url_field="rag_preprocess_pii_redact_service_url",
        label_key="settings.services.item.preprocessPiiRedact",
        working_dir="services/preprocess/pii_redact",
        port=18016,
    ),
    ServiceCatalogEntry(
        service_id="parser-docling",
        category="parser",
        profile="cpu",
        url_field="rag_parser_docling_service_url",
        label_key="settings.services.item.parserDocling",
        working_dir="services/parsers/docling",
        port=18020,
        model_cache_path="~/.cache",
    ),
    ServiceCatalogEntry(
        service_id="parser-unstructured",
        category="parser",
        profile="cpu",
        url_field="rag_parser_unstructured_service_url",
        label_key="settings.services.item.parserUnstructured",
        working_dir="services/parsers/unstructured",
        port=18022,
    ),
    ServiceCatalogEntry(
        service_id="parser-asr",
        category="parser",
        profile="gpu",
        url_field="rag_parser_asr_service_url",
        label_key="settings.services.item.parserAsr",
        working_dir="services/parsers/asr",
        port=18026,
        model_cache_path="~/.cache",
    ),
    # ---- parser マイクロサービス(OCI クラウド・OCI 認証はメイン設定を継承)----
    # OCI を呼ぶだけの軽量プロキシ。OCI 認証はメイン設定(共通 .env・backend/.env と、backend が書く
    # サービス実行用の env ファイル)を systemd の EnvironmentFile で継承する。
    ServiceCatalogEntry(
        service_id="parser-oci-genai-vision",
        category="parser",
        profile="oci",
        url_field="rag_parser_oci_genai_vision_service_url",
        label_key="settings.services.item.parserOciGenaiVision",
        working_dir="services/parsers/oci_genai_vision",
        port=18027,
    ),
    ServiceCatalogEntry(
        service_id="parser-oci-document-understanding",
        category="parser",
        profile="oci",
        url_field="rag_parser_oci_document_understanding_service_url",
        label_key="settings.services.item.parserOciDocumentUnderstanding",
        working_dir="services/parsers/oci_document_understanding",
        port=18028,
    ),
    # ---- pipeline ステージのプラグイン(マイクロサービス)----
    ServiceCatalogEntry(
        service_id="pipeline-chunking",
        category="chunking",
        profile="cpu",
        url_field="rag_chunking_service_url",
        label_key="settings.services.item.pipelineChunking",
        working_dir="services/pipeline/chunking",
        port=18030,
        execution_policy="in_process_when_disabled",
        deployable=False,
    ),
    ServiceCatalogEntry(
        service_id="pipeline-vector-index",
        category="vector_index",
        profile="cpu",
        url_field="rag_vector_index_service_url",
        label_key="settings.services.item.pipelineVectorIndex",
        working_dir="services/pipeline/vector_index",
        port=18031,
        execution_policy="in_process_when_disabled",
        deployable=False,
    ),
    ServiceCatalogEntry(
        service_id="pipeline-graphrag",
        category="graphrag",
        profile="cpu",
        url_field="rag_graph_service_url",
        label_key="settings.services.item.pipelineGraphrag",
        working_dir="services/pipeline/graphrag",
        port=18032,
        execution_policy="in_process_when_disabled",
        deployable=False,
    ),
    ServiceCatalogEntry(
        service_id="pipeline-guardrail",
        category="guardrail",
        profile="cpu",
        url_field="rag_guardrail_service_url",
        label_key="settings.services.item.pipelineGuardrail",
        working_dir="services/pipeline/guardrail",
        port=18034,
        execution_policy="in_process_when_disabled",
        deployable=False,
    ),
    ServiceCatalogEntry(
        service_id="pipeline-evaluation",
        category="evaluation",
        profile="cpu",
        url_field="rag_evaluation_service_url",
        label_key="settings.services.item.pipelineEvaluation",
        working_dir="services/pipeline/evaluation",
        port=18037,
        execution_policy="in_process_when_disabled",
        deployable=False,
    ),
)

_CATALOG_BY_ID: dict[str, ServiceCatalogEntry] = {
    entry.service_id: entry for entry in SERVICE_CATALOG
}
# 画面から操作してよい systemd の unit(カタログの deployable なサービスだけ)。
ALLOWED_SYSTEMD_UNITS: frozenset[str] = frozenset(
    unit for entry in SERVICE_CATALOG if (unit := entry.systemd_unit) is not None
)


def get_catalog_entry(service_id: str) -> ServiceCatalogEntry | None:
    """service_id に対応するカタログエントリを返す(allowlist 照合)。未知なら None。"""
    return _CATALOG_BY_ID.get(service_id)


def is_allowed_systemd_unit(unit: str) -> bool:
    """unit 名が allowlist にあり、決まった形であるかを返す(任意の unit・文字列を拒否する)。"""
    return unit in ALLOWED_SYSTEMD_UNITS and SYSTEMD_UNIT_NAME_RE.fullmatch(unit) is not None


def is_dev_mode(settings: Settings) -> bool:
    """local 環境が dev か判定する。

    ``RAG_ENVIRONMENT`` を流用し、``prod``/``production`` 以外は dev とみなす
    (readiness の production 判定と整合)。dev/prod とも systemd の unit で起動/停止する(#286)。
    dev は起動/停止を自動で有効にする。
    """
    return settings.environment.strip().lower() not in {"prod", "production"}


def resolve_service_base_url(settings: Settings, url_field: str) -> str:
    """設定 ``url_field`` のサービス base URL を解決する(末尾スラッシュ除去)。

    既定値はネイティブ配備の ``http://127.0.0.1:<port>``(#286)。設定値をそのまま使い、空欄(=未設定)は
    空文字を返す。以前の Docker Compose の service 名(例 ``http://parser-docling:8000``)の読み替えは
    #356 で削除した(``backend/.env`` の値を ``127.0.0.1:<port>`` に直す。rag/docs/deployment.md)。

    稼働プローブ(/health)と取込の HTTP 委譲(/parse・/convert)で **同じ解決**を使い、
    「画面では到達できるのに取込では失敗」という不整合を防ぐ。
    """
    return str(getattr(settings, url_field, "") or "").strip().rstrip("/")


def service_health_url(settings: Settings, entry: ServiceCatalogEntry) -> str:
    """エントリの /health base URL を返す(``resolve_service_base_url`` と同じ解決)。"""
    return resolve_service_base_url(settings, entry.url_field)
