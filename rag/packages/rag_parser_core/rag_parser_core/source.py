"""原本(アップロードファイル)の種別・品質メタデータ。

parser サービスと backend が共有する最小の source 契約。document.py から切り出し、
backend 側は本モジュールから re-export して既存 import を維持する。
"""

from enum import StrEnum

from pydantic import BaseModel, Field


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
