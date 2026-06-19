"""MinerU / Dots.OCR の remap 層を fake SDK module で決定論検証する。

GPU は CI 非搭載のため、実 OCR(GPU シーム `_run_mineru` / `_run_dots_ocr`)が呼ぶ SDK を
fake module へ差し替え、出力(markdown / 要素)が `StructuredExtraction` へ正しく
再マップされることだけを検証する。実 GPU 実行は手動 integration で確認する。
"""

from __future__ import annotations

import sys
import types

import pytest

from rag_parser_core.registry import _external_adapter_result
from rag_parser_core.source import SourceModality, SourceProfile


def _pdf_profile() -> SourceProfile:
    return SourceProfile(
        original_file_name="scan.pdf",
        sanitized_file_name="scan.pdf",
        content_type="application/pdf",
        file_size_bytes=16,
        content_sha256="0" * 64,
        modality=SourceModality.PDF,
        parser_profile="pdf",
    )


@pytest.mark.parametrize(
    ("backend", "module_name", "entry"),
    [
        ("mineru", "mineru", "parse_document"),
        ("dots_ocr", "dots_ocr", "parse"),
        ("glm_ocr", "glm_ocr", "parse"),
    ],
)
def test_ocr_engine_markdown_remaps_to_structured_extraction(
    monkeypatch: pytest.MonkeyPatch,
    backend: str,
    module_name: str,
    entry: str,
) -> None:
    fake = types.ModuleType(module_name)
    setattr(fake, entry, lambda _path: "# 請求書\n\n合計 1,200 円")
    monkeypatch.setitem(sys.modules, module_name, fake)

    result = _external_adapter_result(
        backend,
        source_bytes=b"%PDF-1.4 scanned",
        source_profile=_pdf_profile(),
        content_type="application/pdf",
    )

    assert result.parser_backend == backend
    assert result.fallback_used is False
    assert result.extraction is not None
    assert "請求書" in result.extraction.raw_text
    assert result.extraction.parser_artifacts["external_adapter"] == backend
    assert result.extraction.parser_artifacts["ocr_engine"] is True
    assert any(
        element.source_parser == f"{backend}_adapter" for element in result.extraction.elements
    )


def test_ocr_engine_without_entry_point_falls_back(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = types.ModuleType("mineru")  # エントリポイント無し
    monkeypatch.setitem(sys.modules, "mineru", fake)

    result = _external_adapter_result(
        "mineru",
        source_bytes=b"%PDF-1.4 scanned",
        source_profile=_pdf_profile(),
        content_type="application/pdf",
    )

    assert result.extraction is None
    assert result.fallback_used is True
    assert "mineru_adapter_failed" in result.warnings
