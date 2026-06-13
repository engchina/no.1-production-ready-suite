"""ドキュメント関連スキーマ。"""

from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, Field

from app.schemas.extraction import ScalarValue


class FileStatus(StrEnum):
    """ファイル処理状態（参照実装の状態モデルを踏襲）。"""

    UPLOADED = "UPLOADED"
    ANALYZING = "ANALYZING"
    ANALYZED = "ANALYZED"
    REGISTERED = "REGISTERED"
    ERROR = "ERROR"


class DocumentSummary(BaseModel):
    """一覧表示用のドキュメント要約。"""

    id: str
    file_name: str
    status: FileStatus
    category_name: str | None = None
    file_size_bytes: int | None = None
    content_sha256: str | None = None
    duplicate_of_document_id: str | None = None
    uploaded_at: datetime
    registered_at: datetime | None = None


class DocumentDetail(DocumentSummary):
    """詳細表示用。VLM 抽出結果を含む。"""

    object_storage_path: str | None = None
    extracted_fields: dict[str, object] = Field(default_factory=dict)
    error_message: str | None = None


class UploadResult(BaseModel):
    """アップロード結果。"""

    id: str
    file_name: str
    status: FileStatus
    file_size_bytes: int
    content_sha256: str
    duplicate_of_document_id: str | None = None


class DocumentStats(BaseModel):
    """ドキュメント状態別の集計。"""

    total: int
    by_status: dict[FileStatus, int]


class ExtractedFieldsUpdate(BaseModel):
    """抽出フィールドの編集リクエスト（分析後の修正）。"""

    fields: dict[str, ScalarValue] = Field(default_factory=dict)
    raw_text: str | None = None
