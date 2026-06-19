"""MinerU / Dots.OCR を parser adapter 候補として整備した登録のテスト(PoweRAG 由来)。

実 package は未導入のため、登録口・readiness・routing・安全な fallback を検証する。
実 OCR は OCI Enterprise AI VLM へ再マップする前提で、半状態(導入を掲げて実際は parse 不可)を
避けることを確認する。
"""

from __future__ import annotations

from rag_parser_core.registry import EXTERNAL_ADAPTER_PACKAGES, _external_adapter_result

from app.config import Settings
from app.rag.parser_adapter_readiness import (
    ADAPTER_ORDER,
    ADAPTER_PACKAGES,
    parser_adapter_runtime_settings,
)
from app.rag.parser_adapter_routing import adapter_order_for_source_kind


def test_mineru_and_dots_ocr_are_registered_candidates() -> None:
    assert "mineru" in ADAPTER_PACKAGES
    assert "dots_ocr" in ADAPTER_PACKAGES
    assert "glm_ocr" in ADAPTER_PACKAGES
    assert ADAPTER_ORDER[-3:] == ("mineru", "dots_ocr", "glm_ocr")
    assert "mineru" in EXTERNAL_ADAPTER_PACKAGES
    assert "dots_ocr" in EXTERNAL_ADAPTER_PACKAGES
    assert "glm_ocr" in EXTERNAL_ADAPTER_PACKAGES


def test_routing_adds_ocr_engines_for_pdf_and_image() -> None:
    pdf_order = adapter_order_for_source_kind("pdf")
    assert "mineru" in pdf_order
    assert pdf_order[-1] == "glm_ocr"
    image_order = adapter_order_for_source_kind("image")
    assert "dots_ocr" in image_order
    assert "mineru" in image_order
    assert image_order[-1] == "glm_ocr"


def test_readiness_reports_missing_when_package_absent() -> None:
    snapshot = parser_adapter_runtime_settings(
        Settings.model_construct(
            rag_parser_adapter_backend="auto",
            rag_parser_docling_enabled=False,
            rag_parser_marker_enabled=False,
            rag_parser_unstructured_enabled=False,
            rag_parser_mineru_enabled=True,
            rag_parser_dots_ocr_enabled=False,
        )
    )
    status = {adapter.backend: adapter for adapter in snapshot.adapters}
    # 有効化したが未導入 → missing。flag off かつ未導入 → disabled。
    assert status["mineru"].status == "missing"
    assert status["mineru"].install_package.startswith("mineru")
    assert status["dots_ocr"].status == "disabled"


def test_uninstalled_adapter_falls_back_safely() -> None:
    # 未導入 backend を呼んでも例外なく package_missing fallback を返す(半状態を避ける)。
    result = _external_adapter_result(
        "mineru",
        source_bytes=b"%PDF-1.4 dummy",
        source_profile=None,
        content_type="application/pdf",
    )
    assert result.extraction is None
    assert result.fallback_used is True
    assert "mineru_adapter_package_missing" in result.warnings
