"""前処理(Preprocess)サービス app factory と HTTP 契約(ConvertResponse 往復)の検証。

実変換依存(LibreOffice/PyMuPDF)に依らず、converter を決定論スタブへ差し替えて
`POST /convert` が base64 派生 bytes を JSON で往復できることを確認する。
"""

from __future__ import annotations

from fastapi.testclient import TestClient

from rag_parser_core.preprocess import (
    ConvertHealth,
    ConvertOutcome,
    ConvertResponse,
    SourceDerivation,
    normalize_preprocess_profile,
    supported_profiles_from,
)
from rag_parser_core.preprocess_service import create_preprocess_app
from rag_parser_core.source import SourceProfile


def test_convert_outcome_roundtrip_through_response() -> None:
    outcome = ConvertOutcome(
        converted=True,
        converter_name="pdf_rasterize",
        converter_version="1.24",
        derived_bytes=b"%PDF-1.7 page-image",
        derived_content_type="application/pdf",
        page_map={"1": 1, "2": 2},
        warnings=("rasterized",),
    )
    response = ConvertResponse.from_outcome(outcome)
    assert response.converted is True
    assert response.derived_bytes() == b"%PDF-1.7 page-image"
    assert response.page_map == {"1": 1, "2": 2}
    assert response.warnings == ["rasterized"]


def test_passthrough_outcome_carries_no_payload() -> None:
    response = ConvertResponse.from_outcome(ConvertOutcome.passthrough(reason="unsupported"))
    assert response.converted is False
    assert response.derived_content_base64 is None
    assert response.derived_bytes() is None
    assert response.warnings == ["unsupported"]


def test_source_derivation_serializes_to_json_dict() -> None:
    derivation = SourceDerivation(
        derivation_id="d1",
        preprocess_profile="office_to_pdf",
        converted=True,
        converter_name="libreoffice",
        derived_object_path="artifacts/canonical/doc/trace/canonical.pdf",
        page_map={"1": 1},
    )
    payload = derivation.model_dump(mode="json")
    assert payload["derivation_id"] == "d1"
    assert payload["page_map"] == {"1": 1}
    assert SourceDerivation.model_validate(payload).converted is True


def test_normalize_and_supported_profiles() -> None:
    assert normalize_preprocess_profile("AUTO") == "auto"
    assert normalize_preprocess_profile("???") == "passthrough"
    assert supported_profiles_from(["passthrough", "passthrough", "auto"]) == [
        "passthrough",
        "auto",
    ]


def test_convert_endpoint_roundtrip_with_stub_converter() -> None:
    def _converter(
        source_bytes: bytes,
        content_type: str,
        preprocess_profile: str,
        source_profile: SourceProfile | None,
    ) -> ConvertOutcome:
        assert preprocess_profile == "office_to_pdf"
        return ConvertOutcome(
            converted=True,
            converter_name="stub",
            converter_version="v1",
            derived_bytes=b"%PDF stub " + source_bytes,
            derived_content_type="application/pdf",
        )

    def _health() -> ConvertHealth:
        return ConvertHealth(status="ok", supported_profiles=["office_to_pdf"])

    app = create_preprocess_app(converter=_converter, health_probe=_health)
    client = TestClient(app)

    health = client.get("/health")
    assert health.status_code == 200
    assert health.json()["status"] == "ok"

    response = client.post(
        "/convert",
        files={"file": ("a.docx", b"DOCX", "application/octet-stream")},
        data={"content_type": "application/vnd.ms-word", "preprocess_profile": "office_to_pdf"},
    )
    assert response.status_code == 200
    parsed = ConvertResponse.model_validate(response.json())
    assert parsed.converted is True
    assert parsed.derived_bytes() == b"%PDF stub DOCX"
