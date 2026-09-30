"""Docling Picture OCR テキストを検索しやすいラベル付き形式へ整形する。"""

from __future__ import annotations


PICTURE_OCR_TEXT_LABEL = "OCR抽出テキスト"
LEGACY_PICTURE_OCR_TEXT_LABELS = ("Docling OCR",)


def format_docling_picture_ocr_text(text: str) -> str:
    """Picture OCR 原文を検索向けのラベル付き text に整形します。"""
    normalized = str(text or "").strip()
    if not normalized:
        return ""
    prefix = f"{PICTURE_OCR_TEXT_LABEL}:"
    if normalized.startswith(prefix):
        return normalized
    for legacy_label in LEGACY_PICTURE_OCR_TEXT_LABELS:
        legacy_prefix = f"{legacy_label}:"
        if normalized.startswith(legacy_prefix):
            normalized = normalized[len(legacy_prefix) :].strip()
            break
    return f"{prefix}\n{normalized}"
