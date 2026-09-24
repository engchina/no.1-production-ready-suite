"""原本(アップロードファイル)の種別・品質メタデータ。

parser サービスと backend が共有する最小の source 契約。document.py から切り出し、
backend 側は本モジュールから re-export して既存 import を維持する。
"""

from enum import StrEnum
from pathlib import PurePath

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


def template_for_source_profile(source_profile: SourceProfile | None) -> str:
    """chunk metadata 用の既定 template 名(SourceProfile だけで決まる軽量関数)。"""
    if source_profile is None:
        return "enterprise_ai_fallback"
    extension = source_profile.extension or ""
    if source_profile.modality == SourceModality.PDF:
        return "pdf_layout"
    if source_profile.modality == SourceModality.IMAGE:
        return "ocr_page"
    if source_profile.modality == SourceModality.HTML:
        return "html_semantic"
    if source_profile.modality == SourceModality.EMAIL:
        return "email_thread"
    if extension == ".docx":
        return "office_document"
    if extension == ".pptx":
        return "office_slide"
    if extension == ".xlsx":
        return "office_sheet"
    if source_profile.modality == SourceModality.TEXT:
        name = PurePath(source_profile.sanitized_file_name).suffix.lower()
        return "markdown_by_heading" if name in {".md", ".markdown"} else "text_blocks"
    return "enterprise_ai_fallback"
