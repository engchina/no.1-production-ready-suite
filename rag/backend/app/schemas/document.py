"""ドキュメント関連スキーマ。"""

from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, Field

from app.schemas.knowledge_base import KnowledgeBaseRef


class FileStatus(StrEnum):
    """ファイル処理状態。

    RAG はアップロード後に取込(抽出→チャンク→埋め込み→索引)し、
    成功した文書を検索対象の INDEXED として扱う。
    """

    UPLOADED = "UPLOADED"
    INGESTING = "INGESTING"
    INDEXED = "INDEXED"
    ERROR = "ERROR"


class SourceModality(StrEnum):
    """アップロード原本の大まかな種類。"""

    PDF = "pdf"
    IMAGE = "image"
    TEXT = "text"
    HTML = "html"
    EMAIL = "email"
    OFFICE = "office"
    AUDIO = "audio"
    UNKNOWN = "unknown"


class SourcePreviewKind(StrEnum):
    """原本プレビューの既定表示種別。"""

    PDF = "pdf"
    IMAGE = "image"
    TEXT = "text"
    HTML = "html"
    EMAIL = "email"
    OFFICE = "office"
    UNSUPPORTED = "unsupported"


class SourceProfile(BaseModel):
    """アップロード原本の品質・処理方針メタデータ。"""

    original_file_name: str
    sanitized_file_name: str
    extension: str | None = None
    content_type: str
    inferred_content_type: str | None = None
    file_size_bytes: int
    content_sha256: str
    modality: SourceModality
    parser_profile: str
    parser_backend: str = "enterprise_ai"
    parser_version: str = "v1"
    preview_kind: SourcePreviewKind = SourcePreviewKind.UNSUPPORTED
    text_charset: str | None = None
    duplicate_of_document_id: str | None = None
    unsupported_reason: str | None = None
    quality_status: str = "ready"
    quality_warnings: list[str] = Field(default_factory=list)


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


class IngestionJob(BaseModel):
    """キュー投入された取込 job。"""

    id: str
    document_id: str
    status: IngestionJobStatus
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
    metadata: dict[str, str | int | float | bool | None] = Field(default_factory=dict)


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
