"""ドキュメント関連スキーマ。"""

from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, Field
from rag_parser_core.source import SourceModality, SourcePreviewKind, SourceProfile

from app.schemas.common import JsonValue
from app.schemas.knowledge_base import KnowledgeBaseRef

__all__ = [
    "SourceModality",
    "SourcePreviewKind",
    "SourceProfile",
]


class FileStatus(StrEnum):
    """ファイル処理状態。

    RAG はアップロード後に 2 段階で取込む。前段(INGESTING)で parse/抽出を行い、
    人手のプレビュー確認待ち(REVIEW)で停止する。承認後に後段(INDEXING)で
    チャンク→埋め込み→索引を行い、成功した文書を検索対象の INDEXED として扱う。
    REVIEW / INDEXING は検索対象に含めず、INDEXED のみを検索可能とする。
    """

    UPLOADED = "UPLOADED"
    INGESTING = "INGESTING"
    REVIEW = "REVIEW"
    INDEXING = "INDEXING"
    INDEXED = "INDEXED"
    ERROR = "ERROR"


class BatchUploadFailedItem(BaseModel):
    """batch upload で個別に失敗したファイル。"""

    file_name: str
    status_code: int
    message: str
    source_profile: SourceProfile | None = None


class IngestionJobStatus(StrEnum):
    """取込 job 状態。"""

    QUEUED = "QUEUED"
    RUNNING = "RUNNING"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
    SKIPPED = "SKIPPED"
    CANCELLED = "CANCELLED"


class IngestionJobPhase(StrEnum):
    """取込 job の処理フェーズ。

    EXTRACT は parse/抽出を行い REVIEW(プレビュー確認待ち)で停止する前段、
    INDEX は承認後にチャンク→埋め込み→索引を行う後段。
    """

    EXTRACT = "EXTRACT"
    INDEX = "INDEX"


class IngestionJob(BaseModel):
    """キュー投入された取込 job。"""

    id: str
    document_id: str
    status: IngestionJobStatus
    phase: IngestionJobPhase = IngestionJobPhase.EXTRACT
    parser_profile: str
    quality_warnings: list[str] = Field(default_factory=list)
    skip_reason: str | None = None
    error_message: str | None = None
    attempt_count: int = Field(default=0, ge=0)
    max_attempts: int = Field(default=3, ge=1)
    queued_at: datetime
    started_at: datetime | None = None
    finished_at: datetime | None = None


class DocumentSummary(BaseModel):
    """一覧表示用のドキュメント要約。"""

    id: str
    file_name: str
    status: FileStatus
    category_name: str | None = None
    content_type: str | None = None
    file_size_bytes: int | None = None
    content_sha256: str | None = None
    duplicate_of_document_id: str | None = None
    uploaded_at: datetime
    indexed_at: datetime | None = None
    knowledge_bases: list[KnowledgeBaseRef] = Field(default_factory=list)
    source_profile: SourceProfile | None = None


class DocumentDetail(DocumentSummary):
    """詳細表示用。VLM/LLM の抽出本文とメタデータを含む。"""

    object_storage_path: str | None = None
    extraction: dict[str, object] = Field(default_factory=dict)
    error_message: str | None = None


class DocumentIngestionConfigData(BaseModel):
    """文書の取込設定スナップショットと owning KB の現行設定のドリフト状況。

    Parser/Chunking は取込時にしか効かないため、owning KB の現行設定と、
    実際に取込時へ刻まれた chunk metadata を比較し、再取込が必要かを示す。
    """

    document_id: str
    is_indexed: bool = False
    owning_knowledge_base: KnowledgeBaseRef | None = None
    # owning KB の現行設定を重ねた「これから取り込むなら」の有効値。
    effective_chunking_strategy: str
    effective_parser_adapter_backend: str
    # 実際に取込時へ刻まれた値(INDEXED 済みのときのみ観測できる)。
    observed_chunking_strategy: str | None = None
    observed_parser_backend: str | None = None
    config_drift: bool = False


class UploadResult(BaseModel):
    """アップロード結果。"""

    id: str
    file_name: str
    status: FileStatus
    file_size_bytes: int
    content_sha256: str
    duplicate_of_document_id: str | None = None
    knowledge_bases: list[KnowledgeBaseRef] = Field(default_factory=list)
    source_profile: SourceProfile
    ingestion_started: bool = False
    ingestion_job: IngestionJob | None = None


class BatchUploadResult(BaseModel):
    """複数ファイル upload の結果。"""

    items: list[UploadResult] = Field(default_factory=list)
    failed_items: list[BatchUploadFailedItem] = Field(default_factory=list)
    total_count: int = 0
    uploaded_count: int = 0
    failed_count: int = 0
    queued_count: int = 0
    skipped_count: int = 0


class DocumentChunkView(BaseModel):
    """UI で chunk/citation を可視化するための非 embedding chunk view。"""

    document_id: str
    chunk_id: str
    chunk_index: int = 0
    text: str
    page_start: int | None = None
    page_end: int | None = None
    bbox: list[float] | None = None
    section_path: str | None = None
    content_kind: str | None = None
    chunk_group_id: str | None = None
    source_parser: str | None = None
    element_ids: list[str] = Field(default_factory=list)
    metadata: dict[str, JsonValue] = Field(default_factory=dict)


class DocumentLayerStatusName(StrEnum):
    """文書 chunk_set の派生情報レイヤー状態。"""

    NOT_REQUESTED = "not_requested"
    PLANNED_ONLY = "planned_only"
    MATERIALIZED = "materialized"
    NEEDS_REINGEST = "needs_reingest"
    ERROR = "error"


class DocumentMaterializationLayerStatus(BaseModel):
    """chunk_set に紐づく派生情報レイヤーの現在状態。"""

    layer_id: str | None = None
    requested: bool = False
    status: DocumentLayerStatusName = DocumentLayerStatusName.NOT_REQUESTED
    reason: str | None = None


class DocumentChunkSetLayerStatuses(BaseModel):
    """chunk_set から派生する情報レイヤーの状態一覧。"""

    metadata: DocumentMaterializationLayerStatus = Field(
        default_factory=DocumentMaterializationLayerStatus
    )
    graph: DocumentMaterializationLayerStatus = Field(
        default_factory=DocumentMaterializationLayerStatus
    )
    navigation: DocumentMaterializationLayerStatus = Field(
        default_factory=DocumentMaterializationLayerStatus
    )


class DocumentChunkSet(BaseModel):
    """文書の chunk_set(variant = 1 レシピのチャンク集合)1 件分の状態・件数・所属/配信 KB。"""

    chunk_set_id: str
    extraction_recipe_id: str | None = None
    extraction_status: DocumentLayerStatusName = DocumentLayerStatusName.NOT_REQUESTED
    extraction_reason: str | None = None
    status: str
    chunk_count: int = 0
    vector_count: int = 0
    extraction_id: str | None = None
    parser: str | None = None
    preprocess: str | None = None
    knowledge_base_ids: list[str] = Field(default_factory=list)
    serving_knowledge_base_ids: list[str] = Field(default_factory=list)
    layer_statuses: DocumentChunkSetLayerStatuses = Field(
        default_factory=DocumentChunkSetLayerStatuses
    )


class DocumentExtractionExportFormat(StrEnum):
    """構造化抽出の監査用 export 形式。"""

    JSON = "json"
    MARKDOWN = "markdown"
    HTML = "html"
    CHUNKS = "chunks"


class DocumentExtractionExport(BaseModel):
    """Docling / Marker 風に extraction を非 embedding 形式で確認する view。"""

    document_id: str
    file_name: str
    format: DocumentExtractionExportFormat
    content_type: str
    content: str = ""
    payload: dict[str, object] = Field(default_factory=dict)
    chunks: list[DocumentChunkView] = Field(default_factory=list)
    parser_backend: str | None = None
    parser_profile: str | None = None
    page_count: int = 0
    element_count: int = 0
    table_count: int = 0
    asset_count: int = 0


class IngestionSegment(BaseModel):
    """文書取込 segment の checkpoint/status view。"""

    segment_id: str
    document_id: str
    status: str
    parser_backend: str = "enterprise_ai"
    parser_profile: str = "enterprise_ai_generic"
    page_start: int | None = None
    page_end: int | None = None
    attempt_count: int = Field(default=0, ge=0)
    artifact_path: str | None = None
    error_code: str | None = None
    error_message: str | None = None


class DocumentElementTextEdit(BaseModel):
    """REVIEW 中の人手修正: 要素 1 件のテキスト差し替え。"""

    element_id: str = Field(..., max_length=128)
    text: str = Field(default="", max_length=200000)


class DocumentTableCellTextEdit(BaseModel):
    """REVIEW 中の人手修正: 表セル 1 件のテキスト差し替え(table_id + row + col で同定)。"""

    table_id: str = Field(..., max_length=128)
    row: int = Field(..., ge=0)
    col: int = Field(..., ge=0)
    text: str = Field(default="", max_length=200000)


class DocumentApproveRequest(BaseModel):
    """承認(index)リクエスト。任意で REVIEW 中のテキスト修正を伴う。

    bbox・構造・section などはサーバ側の抽出結果を保持し、テキストのみ差し替える。
    """

    raw_text: str | None = Field(default=None, max_length=2000000)
    element_edits: list[DocumentElementTextEdit] = Field(default_factory=list, max_length=5000)
    table_cell_edits: list[DocumentTableCellTextEdit] = Field(
        default_factory=list, max_length=20000
    )


class DocumentDeleteResult(BaseModel):
    """ドキュメント削除結果。"""

    id: str
    file_name: str
    object_storage_path: str | None = None
    object_deleted: bool = False
    artifact_deleted_count: int = 0
    artifact_delete_failed_count: int = 0


class DocumentStats(BaseModel):
    """ドキュメント状態別の集計。"""

    total: int
    by_status: dict[FileStatus, int]
