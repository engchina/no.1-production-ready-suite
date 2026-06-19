"""Office→PDF 変換の決定論・縮退検証。

LibreOffice(soffice)は CI に無いことがあるため、実変換ではなく profile 振り分けと
依存欠如時の passthrough 縮退を検証する(soffice_path を monkeypatch して決定論化)。
"""

from __future__ import annotations

import app.converters as converters
from app.converters import _office_suffix, convert


def test_non_office_profile_passes_through() -> None:
    outcome = convert(b"\x00data", "application/octet-stream", "csv_to_json", None)
    assert outcome.converted is False
    assert any("preprocess_unsupported_profile" in w for w in outcome.warnings)


def test_default_suffix_is_docx() -> None:
    assert _office_suffix(None) == ".docx"


def test_convert_without_libreoffice_passes_through(monkeypatch) -> None:
    monkeypatch.setattr(converters, "soffice_path", lambda: None)
    outcome = convert(b"\x00doc", "application/vnd.openxmlformats", "office_to_pdf", None)
    assert outcome.converted is False
    assert "libreoffice_unavailable" in outcome.warnings


def test_passthrough_is_deterministic(monkeypatch) -> None:
    monkeypatch.setattr(converters, "soffice_path", lambda: None)
    source = b"\x00doc"
    first = convert(source, "application/octet-stream", "office_to_pdf", None)
    second = convert(source, "application/octet-stream", "office_to_pdf", None)
    assert first.converted is second.converted is False
    assert first.derived_bytes == second.derived_bytes
