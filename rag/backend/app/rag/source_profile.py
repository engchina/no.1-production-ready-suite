"""アップロード原本の source profile 作成。"""

import mimetypes
from pathlib import PurePath

from charset_normalizer import from_bytes

from app.schemas.document import SourceModality, SourceProfile

LARGE_FILE_WARNING_BYTES = 50 * 1024 * 1024

TEXT_MEDIA_TYPES = {
    "application/json",
    "application/xml",
    "application/csv",
    "application/x-ndjson",
}

OFFICE_EXTENSIONS = {
    ".doc",
    ".docx",
    ".ppt",
    ".pptx",
    ".xls",
    ".xlsx",
}

WHATWG_LABELS = {
    "cp932": "shift_jis",
    "ms932": "shift_jis",
    "shift-jis": "shift_jis",
    "sjis": "shift_jis",
    "euc-jp": "euc-jp",
    "eucjp": "euc-jp",
    "euc-jis-2004": "euc-jp",
    "euc-jisx0213": "euc-jp",
    "cp936": "gbk",
    "gbk": "gbk",
    "gb2312": "gbk",
    "cp949": "euc-kr",
    "euc-kr": "euc-kr",
    "cp950": "big5",
    "big5hkscs": "big5",
}


def build_source_profile(
    *,
    original_file_name: str,
    sanitized_file_name: str,
    content_type: str | None,
    file_size_bytes: int | None,
    content_sha256: str | None,
    duplicate_of_document_id: str | None = None,
    data: bytes | None = None,
) -> SourceProfile:
    """原本メタデータから source profile を作る。"""
    normalized_content_type = _normalized_content_type(content_type)
    inferred_content_type = _inferred_content_type(sanitized_file_name)
    extension = PurePath(sanitized_file_name).suffix.lower() or None
    modality = _source_modality(normalized_content_type, extension)
    warnings = _quality_warnings(
        content_type=normalized_content_type,
        inferred_content_type=inferred_content_type,
        file_size_bytes=file_size_bytes or 0,
        modality=modality,
        duplicate_of_document_id=duplicate_of_document_id,
    )
    text_charset = (
        _detect_text_charset(data)
        if data is not None and _is_text_media_type(normalized_content_type)
        else None
    )
    return SourceProfile(
        original_file_name=original_file_name or sanitized_file_name,
        sanitized_file_name=sanitized_file_name,
        extension=extension,
        content_type=normalized_content_type,
        inferred_content_type=inferred_content_type,
        file_size_bytes=file_size_bytes or 0,
        content_sha256=content_sha256 or "",
        modality=modality,
        parser_profile=_parser_profile(modality),
        text_charset=text_charset,
        duplicate_of_document_id=duplicate_of_document_id,
        quality_status="warning" if warnings else "ready",
        quality_warnings=warnings,
    )


def _normalized_content_type(content_type: str | None) -> str:
    if not content_type:
        return "application/octet-stream"
    return content_type.split(";", maxsplit=1)[0].strip().lower() or "application/octet-stream"


def _inferred_content_type(file_name: str) -> str | None:
    media_type, _ = mimetypes.guess_type(file_name)
    return media_type.lower() if media_type else None


def _source_modality(content_type: str, extension: str | None) -> SourceModality:
    if content_type == "application/pdf" or extension == ".pdf":
        return SourceModality.PDF
    if content_type.startswith("image/"):
        return SourceModality.IMAGE
    if _is_text_media_type(content_type) or extension in {".txt", ".md", ".markdown", ".csv"}:
        return SourceModality.TEXT
    if extension in OFFICE_EXTENSIONS or content_type in {
        "application/msword",
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        "application/vnd.ms-powerpoint",
        "application/vnd.openxmlformats-officedocument.presentationml.presentation",
        "application/vnd.ms-excel",
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    }:
        return SourceModality.OFFICE
    return SourceModality.UNKNOWN


def _parser_profile(modality: SourceModality) -> str:
    if modality == SourceModality.PDF:
        return "enterprise_ai_pdf_layout"
    if modality == SourceModality.IMAGE:
        return "enterprise_ai_image_ocr"
    if modality == SourceModality.TEXT:
        return "enterprise_ai_text_structure"
    if modality == SourceModality.OFFICE:
        return "enterprise_ai_office_structure"
    return "enterprise_ai_generic"


def _quality_warnings(
    *,
    content_type: str,
    inferred_content_type: str | None,
    file_size_bytes: int,
    modality: SourceModality,
    duplicate_of_document_id: str | None,
) -> list[str]:
    warnings: list[str] = []
    if duplicate_of_document_id:
        warnings.append("duplicate_content")
    if content_type == "application/octet-stream":
        warnings.append("content_type_missing")
    elif inferred_content_type and not _content_types_compatible(
        content_type,
        inferred_content_type,
    ):
        warnings.append("content_type_extension_mismatch")
    if file_size_bytes >= LARGE_FILE_WARNING_BYTES:
        warnings.append("large_file")
    if modality == SourceModality.UNKNOWN:
        warnings.append("unknown_modality")
    return warnings


def _content_types_compatible(actual: str, inferred: str) -> bool:
    if actual == inferred:
        return True
    if actual.startswith("text/") and inferred in TEXT_MEDIA_TYPES:
        return True
    return actual.startswith("image/") and inferred.startswith("image/")


def _is_text_media_type(media_type: str) -> bool:
    return media_type.startswith("text/") or media_type in TEXT_MEDIA_TYPES


def _detect_text_charset(data: bytes) -> str:
    if not data:
        return "utf-8"
    try:
        data.decode("utf-8")
        return "utf-8"
    except UnicodeDecodeError:
        pass
    match = from_bytes(data).best()
    if match is None or not match.encoding:
        return "utf-8"
    label = match.encoding.replace("_", "-").lower()
    return WHATWG_LABELS.get(label, label)
