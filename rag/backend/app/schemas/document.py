"""ドキュメント関連スキーマ。"""

from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, Field


class FileStatus(StrEnum):
    """ファイル処理状態。

    RAG はアップロード後に取込(抽出→チャンク→埋め込み→索引)し、
    成功した文書を検索対象の INDEXED として扱う。
    """

    UPLOADED = "UPLOADED"
    INGESTING = "INGESTING"
    INDEXED = "INDEXED"
    ERROR = "ERROR"


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


class DocumentStats(BaseModel):
    """ドキュメント状態別の集計。"""

    total: int
    by_status: dict[FileStatus, int]
