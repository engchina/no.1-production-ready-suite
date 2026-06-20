"""ParserServiceClient(parser マイクロサービス HTTP クライアント)のテスト。

成功時は ParseResponse を ParserRegistryResult へ戻し、接続失敗/未設定/不正応答時は
warning 付き fallback(extraction=None)へ縮退することを検証する。
"""

from __future__ import annotations

import httpx
import pytest
from rag_parser_core.extraction import DocumentElement, StructuredExtraction
from rag_parser_core.registry import ParserRegistryResult
from rag_parser_core.result import ParseResponse

from app.clients import parser_service as parser_service_module
from app.clients.parser_service import ParserServiceClient
from app.config import Settings
from app.schemas.document import SourceModality, SourceProfile


def _profile() -> SourceProfile:
    return SourceProfile(
        original_file_name="a.pdf",
        sanitized_file_name="a.pdf",
        content_type="application/pdf",
        file_size_bytes=3,
        content_sha256="0" * 64,
        modality=SourceModality.PDF,
        parser_profile="pdf",
    )


def _install_transport(
    monkeypatch: pytest.MonkeyPatch,
    handler: httpx.MockTransport,
) -> None:
    """ParserServiceClient が内部で作る httpx.Client に MockTransport を注入する。"""
    original = httpx.Client

    def factory(*args: object, **kwargs: object) -> httpx.Client:
        kwargs.pop("timeout", None)
        return original(transport=handler)

    monkeypatch.setattr(parser_service_module.httpx, "Client", factory)


def test_runner_returns_extraction_on_success(monkeypatch: pytest.MonkeyPatch) -> None:
    extraction = StructuredExtraction(
        raw_text="本文",
        document_type="PDF",
        confidence=1.0,
        elements=[
            DocumentElement(
                kind="text",
                text="本文",
                order=0,
                element_id="el-0",
                content_kind="text",
                source_parser="docling_adapter",
                page_number=1,
            )
        ],
    )
    response = ParseResponse(
        extraction=extraction,
        parser_backend="docling",
        parser_version="docling:2.103.0",
        template="structure_aware",
    )

    def handle(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/parse"
        return httpx.Response(200, json=response.model_dump(mode="json"))

    _install_transport(monkeypatch, httpx.MockTransport(handle))
    client = ParserServiceClient(
        Settings(rag_parser_docling_service_url="http://parser-docling:8000")
    )
    result = client.runner("docling", b"abc", _profile(), "application/pdf")

    assert isinstance(result, ParserRegistryResult)
    assert result.fallback_used is False
    assert result.extraction is not None
    assert result.extraction.elements[0].text == "本文"


def test_runner_falls_back_when_service_unreachable(monkeypatch: pytest.MonkeyPatch) -> None:
    def handle(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused", request=request)

    _install_transport(monkeypatch, httpx.MockTransport(handle))
    client = ParserServiceClient(
        Settings(rag_parser_marker_service_url="http://parser-marker:8000")
    )
    result = client.runner("marker", b"abc", _profile(), "application/pdf")

    assert result.extraction is None
    assert result.fallback_used is True
    assert "marker_adapter_service_unreachable" in result.warnings


def test_runner_falls_back_on_5xx(monkeypatch: pytest.MonkeyPatch) -> None:
    def handle(request: httpx.Request) -> httpx.Response:
        return httpx.Response(503, text="unavailable")

    _install_transport(monkeypatch, httpx.MockTransport(handle))
    client = ParserServiceClient(Settings())
    result = client.runner("unstructured", b"abc", _profile(), "application/pdf")

    assert result.extraction is None
    assert result.fallback_used is True
    assert "unstructured_adapter_service_unreachable" in result.warnings


def test_runner_falls_back_when_url_unconfigured() -> None:
    client = ParserServiceClient(Settings(rag_parser_docling_service_url=""))
    result = client.runner("docling", b"abc", _profile(), "application/pdf")

    assert result.extraction is None
    assert result.fallback_used is True
    assert "docling_adapter_service_unconfigured" in result.warnings
