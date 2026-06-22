"""parser サービス共通 app factory と HTTP 契約(ParseResponse 往復)の検証。

実 parser 依存(docling 等)に依らず、`run_external_adapter` を決定論スタブへ差し替えて
`POST /parse` が `StructuredExtraction` を JSON で往復できることを確認する。
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from rag_parser_core import service as service_module
from rag_parser_core.extraction import DocumentElement, StructuredExtraction
from rag_parser_core.registry import ParserRegistryResult
from rag_parser_core.result import ParseResponse
from rag_parser_core.service import create_parse_app
from rag_parser_core.source import SourceModality, SourceProfile


def _stub_extraction() -> StructuredExtraction:
    return StructuredExtraction(
        raw_text="見出し\n本文",
        document_type="ドキュメント",
        confidence=1.0,
        elements=[
            DocumentElement(
                kind="title",
                text="見出し",
                order=0,
                element_id="el-0",
                content_kind="text",
                source_parser="docling_adapter",
                page_number=1,
            )
        ],
    )


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch) -> TestClient:
    captured: dict[str, object] = {}

    def fake_run(
        backend: str,
        source_bytes: bytes,
        source_profile: SourceProfile | None,
        content_type: str,
    ) -> ParserRegistryResult:
        captured["backend"] = backend
        captured["bytes"] = source_bytes
        captured["content_type"] = content_type
        captured["profile"] = source_profile
        return ParserRegistryResult(
            extraction=_stub_extraction(),
            parser_backend=backend,
            parser_version=f"{backend}_adapter_v1",
            template="structure_aware",
        )

    monkeypatch.setattr(service_module, "run_external_adapter", fake_run)
    # 実在する配布名(pydantic)で version 検出経路を検証する。
    app = create_parse_app(
        backend="docling",
        import_name="pydantic",
        distribution_names=("pydantic",),
    )
    test_client = TestClient(app)
    test_client.captured = captured  # type: ignore[attr-defined]
    return test_client


def test_health_reports_backend_and_version(client: TestClient) -> None:
    response = client.get("/health")
    assert response.status_code == 200
    payload = response.json()
    assert payload["backend"] == "docling"
    assert payload["status"] == "ok"
    assert payload["package_version"] is not None


def test_health_reports_ok_when_runtime_health_is_ready() -> None:
    app = create_parse_app(
        backend="dots_ocr",
        import_name="pydantic",
        distribution_names=("pydantic",),
        runtime_health=lambda: True,
    )
    payload = TestClient(app).get("/health").json()

    assert payload["status"] == "ok"
    assert payload["backend"] == "dots_ocr"
    assert payload["package_version"] is not None


def test_health_reports_degraded_when_runtime_health_fails() -> None:
    def fail_runtime() -> bool:
        raise ConnectionError("vllm refused")

    app = create_parse_app(
        backend="glm_ocr",
        import_name="pydantic",
        distribution_names=("pydantic",),
        runtime_health=fail_runtime,
    )
    payload = TestClient(app).get("/health").json()

    assert payload["status"] == "degraded"
    assert payload["backend"] == "glm_ocr"
    assert payload["package_version"] is not None


def test_parse_roundtrips_structured_extraction(client: TestClient) -> None:
    profile = SourceProfile(
        original_file_name="a.pdf",
        sanitized_file_name="a.pdf",
        content_type="application/pdf",
        file_size_bytes=3,
        content_sha256="0" * 64,
        modality=SourceModality.PDF,
        parser_profile="pdf",
    )
    response = client.post(
        "/parse",
        files={"file": ("a.pdf", b"abc", "application/pdf")},
        data={
            "content_type": "application/pdf",
            "source_profile": profile.model_dump_json(),
        },
    )
    assert response.status_code == 200
    parsed = ParseResponse.model_validate(response.json())
    assert parsed.parser_backend == "docling"
    assert parsed.template == "structure_aware"
    assert parsed.extraction is not None
    assert parsed.extraction.elements[0].text == "見出し"
    # source_profile が JSON 経由で adapter まで届くこと
    assert client.captured["content_type"] == "application/pdf"  # type: ignore[attr-defined]
    assert client.captured["bytes"] == b"abc"  # type: ignore[attr-defined]
    forwarded = client.captured["profile"]  # type: ignore[attr-defined]
    assert isinstance(forwarded, SourceProfile)
    assert forwarded.modality == SourceModality.PDF


def test_parse_result_to_registry_result_preserves_fallback() -> None:
    response = ParseResponse(
        extraction=None,
        parser_backend="marker",
        parser_version="marker_adapter_v1",
        fallback_used=True,
        template="marker_fallback",
        warnings=["marker_adapter_failed"],
    )
    result = response.to_result()
    assert result.extraction is None
    assert result.fallback_used is True
    assert result.warnings == ("marker_adapter_failed",)
    # 往復で等価
    assert ParseResponse.from_result(result).model_dump() == response.model_dump()
