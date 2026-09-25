"""PDF→ページ画像PDF 変換の決定論・縮退検証。

PyMuPDF の有無で実ラスタライズの可否が変わるため、profile 振り分けと不正入力・空入力の
passthrough 縮退を検証する(いずれの環境でも converted=False で決定論)。
"""

from __future__ import annotations

from app.converters import convert


def test_non_profile_passes_through() -> None:
    outcome = convert(b"%PDF-1.4", "application/pdf", "csv_to_json", None)
    assert outcome.converted is False
    assert any("preprocess_unsupported_profile" in w for w in outcome.warnings)


def test_invalid_pdf_passes_through() -> None:
    outcome = convert(b"this is not a pdf", "application/pdf", "pdf_to_page_images", None)
    assert outcome.converted is False
    # PyMuPDF 有: pdf_open_failed / pdf_rasterize_empty、無: pymupdf_unavailable のいずれか。
    assert outcome.warnings


def test_empty_input_passes_through() -> None:
    outcome = convert(b"", "application/pdf", "pdf_to_page_images", None)
    assert outcome.converted is False


def test_passthrough_is_deterministic() -> None:
    source = b"not a pdf"
    first = convert(source, "application/pdf", "pdf_to_page_images", None)
    second = convert(source, "application/pdf", "pdf_to_page_images", None)
    assert first.converted is second.converted is False
    assert first.derived_bytes == second.derived_bytes
