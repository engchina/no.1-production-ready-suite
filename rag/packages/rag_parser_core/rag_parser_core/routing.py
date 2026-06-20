"""Parser adapter の source-aware routing 定義。"""

from __future__ import annotations

from typing import Literal

ParserAdapterRouteBackend = Literal[
    "docling", "marker", "unstructured", "mineru", "dots_ocr", "glm_ocr"
]
ParserAdapterSourceKind = Literal[
    "pdf",
    "image",
    "office",
    "html",
    "email",
    "audio",
    "text",
    "unknown",
]
AdapterOrderBySourceKind = dict[
    ParserAdapterSourceKind,
    tuple[ParserAdapterRouteBackend, ...],
]

SOURCE_ROUTE_KINDS: tuple[ParserAdapterSourceKind, ...] = (
    "pdf",
    "image",
    "office",
    "html",
    "email",
    "audio",
    "text",
    "unknown",
)

# MinerU/Dots.OCR/GLM-OCR は OCR が強みのため pdf/image の候補末尾に足す。
# 未導入時は readiness が missing として fallback するため、順序は導入後に効く。
ADAPTER_ORDER_BY_SOURCE_KIND: AdapterOrderBySourceKind = {
    "pdf": ("docling", "marker", "unstructured", "mineru", "glm_ocr"),
    "image": ("unstructured", "marker", "docling", "dots_ocr", "mineru", "glm_ocr"),
    "office": ("docling", "unstructured", "mineru"),
    "html": ("docling", "unstructured"),
    "email": ("unstructured",),
    "audio": (),
    "text": (),
    "unknown": ("unstructured", "docling"),
}


def normalize_source_kind(value: object) -> ParserAdapterSourceKind:
    """manifest modality / runtime source label を routing 用の低 cardinality へ寄せる。"""
    normalized = str(value or "").strip().casefold()
    if normalized in {"pdf"}:
        return "pdf"
    if normalized in {"image", "ocr", "scan", "scanned_image"}:
        return "image"
    if normalized in {"office", "docx", "pptx", "xlsx", "word", "powerpoint", "excel"}:
        return "office"
    if normalized in {"html", "xhtml", "web"}:
        return "html"
    if normalized in {"email", "eml", "message"}:
        return "email"
    if normalized in {"audio", "wav", "mp3", "m4a", "flac", "ogg", "aac"}:
        return "audio"
    if normalized in {"text", "markdown", "md", "csv", "tsv", "json", "jsonl", "ndjson"}:
        return "text"
    return "unknown"


def adapter_order_for_source_kind(
    source_kind: object,
) -> tuple[ParserAdapterRouteBackend, ...]:
    """source kind に対する外部 adapter 候補順を返す。"""
    return ADAPTER_ORDER_BY_SOURCE_KIND[normalize_source_kind(source_kind)]
