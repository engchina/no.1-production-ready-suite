"""Docling Picture OCR テキストを検索しやすいラベル付き形式へ整形する。"""

from __future__ import annotations

from rag_engine.parsing.content_labels import content_labels

# 旧い解析の結果に残る OCR の欄の名前（保存済みのデータの形。読むときだけ使う）。
LEGACY_PICTURE_OCR_TEXT_LABELS = ("Docling OCR",)


def picture_ocr_text_label() -> str:
    """OCR の欄の名前（言語資源の content_labels.picture_ocr。#2181）。"""
    return content_labels().text("picture_ocr")


def format_docling_picture_ocr_text(text: str) -> str:
    """Picture OCR 原文を検索向けのラベル付き text に整形します。"""
    normalized = str(text or "").strip()
    if not normalized:
        return ""
    prefix = f"{picture_ocr_text_label()}:"
    if normalized.startswith(prefix):
        return normalized
    for legacy_label in LEGACY_PICTURE_OCR_TEXT_LABELS:
        legacy_prefix = f"{legacy_label}:"
        if normalized.startswith(legacy_prefix):
            normalized = normalized[len(legacy_prefix) :].strip()
            break
    return f"{prefix}\n{normalized}"
