"""GPU OCR parser adapter 候補として整備した登録のテスト(PoweRAG 由来)。

実 package は未導入のため、登録口・readiness・routing・安全な fallback を検証する。
実 OCR は OCI Enterprise AI VLM へ再マップする前提で、半状態(導入を掲げて実際は parse 不可)を
避けることを確認する。
"""

from __future__ import annotations

from pytest import MonkeyPatch
from rag_parser_core import registry as parser_registry
from rag_parser_core.registry import (
    EXTERNAL_ADAPTER_PACKAGES,
    ParserRegistryResult,
    _external_adapter_result,
    parse_with_registry,
)

from app.config import Settings
from app.rag.parser_adapter_readiness import (
    ADAPTER_ORDER,
    ADAPTER_PACKAGES,
    parser_adapter_runtime_settings,
)
from app.rag.parser_adapter_routing import adapter_order_for_source_kind
from app.schemas.document import SourceModality, SourceProfile
from app.schemas.extraction import StructuredExtraction


def _pdf_profile() -> SourceProfile:
    return SourceProfile(
        original_file_name="scan.pdf",
        sanitized_file_name="scan.pdf",
        content_type="application/pdf",
        file_size_bytes=16,
        content_sha256="0" * 64,
        modality=SourceModality.PDF,
        parser_profile="enterprise_ai_pdf_layout",
    )


def test_gpu_ocr_engines_are_registered_candidates() -> None:
    assert "unlimited_ocr" in ADAPTER_PACKAGES
    assert "mineru" in ADAPTER_PACKAGES
    assert "dots_ocr" in ADAPTER_PACKAGES
    assert "glm_ocr" in ADAPTER_PACKAGES
    assert ADAPTER_ORDER[-4:] == ("unlimited_ocr", "mineru", "dots_ocr", "glm_ocr")
    assert "unlimited_ocr" in EXTERNAL_ADAPTER_PACKAGES
    assert "mineru" in EXTERNAL_ADAPTER_PACKAGES
    assert "dots_ocr" in EXTERNAL_ADAPTER_PACKAGES
    assert "glm_ocr" in EXTERNAL_ADAPTER_PACKAGES


def test_routing_adds_ocr_engines_for_pdf_and_image() -> None:
    pdf_order = adapter_order_for_source_kind("pdf")
    assert "unlimited_ocr" in pdf_order
    assert "mineru" in pdf_order
    assert pdf_order.index("unlimited_ocr") < pdf_order.index("mineru")
    assert pdf_order[-1] == "glm_ocr"
    image_order = adapter_order_for_source_kind("image")
    assert "dots_ocr" in image_order
    assert "unlimited_ocr" in image_order
    assert "mineru" in image_order
    assert image_order.index("unlimited_ocr") < image_order.index("mineru")
    assert image_order[-1] == "glm_ocr"


def test_readiness_reports_missing_when_package_absent() -> None:
    snapshot = parser_adapter_runtime_settings(
        Settings.model_construct(
            rag_parser_adapter_backend="mineru",
            rag_parser_docling_enabled=False,
            rag_parser_marker_enabled=False,
            rag_parser_unstructured_enabled=False,
            rag_parser_unlimited_ocr_enabled=False,
            rag_parser_mineru_enabled=True,
            rag_parser_dots_ocr_enabled=False,
        )
    )
    status = {adapter.backend: adapter for adapter in snapshot.adapters}
    # 有効化したが未導入 → missing。flag off かつ未導入 → disabled。
    assert status["unlimited_ocr"].status == "disabled"
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


def test_unlimited_ocr_missing_sglang_falls_back_safely(
    monkeypatch: MonkeyPatch,
) -> None:
    """Unlimited-OCR 実行依存が無ければ package_missing fallback を返す。"""
    monkeypatch.setattr(parser_registry, "_module_available", lambda _name: False)

    result = _external_adapter_result(
        "unlimited_ocr",
        source_bytes=b"%PDF-1.4 dummy",
        source_profile=None,
        content_type="application/pdf",
    )

    assert result.extraction is None
    assert result.fallback_used is True
    assert "unlimited_ocr_adapter_package_missing" in result.warnings


def test_parse_with_registry_routes_enabled_mineru_pdf_to_runner() -> None:
    """MinerU が選択・有効化されていれば PDF を外部 runner へ渡す。"""
    calls: list[str] = []

    def runner(
        backend: str,
        source_bytes: bytes,
        source_profile: SourceProfile | None,
        content_type: str,
    ) -> ParserRegistryResult:
        calls.append(backend)
        assert source_bytes == b"%PDF-1.4 scanned"
        assert source_profile is not None
        assert content_type == "application/pdf"
        return ParserRegistryResult(
            extraction=StructuredExtraction(raw_text="MinerU OCR 本文"),
            parser_backend=backend,
            parser_version="mineru:test",
            template="structure_aware",
        )

    result = parse_with_registry(
        b"%PDF-1.4 scanned",
        source_profile=_pdf_profile(),
        content_type="application/pdf",
        adapter_backend="mineru",
        mineru_enabled=True,
        external_adapter_runner=runner,
    )

    assert calls == ["mineru"]
    assert result.parser_backend == "mineru"
    assert result.extraction is not None
    assert result.extraction.raw_text == "MinerU OCR 本文"
    assert "mineru_adapter_feature_flag_disabled" not in result.warnings


def test_parse_with_registry_routes_enabled_unlimited_ocr_pdf_to_runner() -> None:
    """Unlimited-OCR が選択・有効化されていれば PDF を外部 runner へ渡す。"""
    calls: list[str] = []

    def runner(
        backend: str,
        source_bytes: bytes,
        source_profile: SourceProfile | None,
        content_type: str,
    ) -> ParserRegistryResult:
        calls.append(backend)
        assert source_bytes == b"%PDF-1.4 scanned"
        assert source_profile is not None
        assert content_type == "application/pdf"
        return ParserRegistryResult(
            extraction=StructuredExtraction(raw_text="Unlimited-OCR 本文"),
            parser_backend=backend,
            parser_version="unlimited_ocr:test",
            template="structure_aware",
        )

    result = parse_with_registry(
        b"%PDF-1.4 scanned",
        source_profile=_pdf_profile(),
        content_type="application/pdf",
        adapter_backend="unlimited_ocr",
        unlimited_ocr_enabled=True,
        external_adapter_runner=runner,
    )

    assert calls == ["unlimited_ocr"]
    assert result.parser_backend == "unlimited_ocr"
    assert result.extraction is not None
    assert result.extraction.raw_text == "Unlimited-OCR 本文"
    assert "unlimited_ocr_adapter_feature_flag_disabled" not in result.warnings
