"""service 系 parser backend(enterprise_ai_vlm / oci_document_understanding)の契約テスト。

- registry: 明示選択時に core が sentinel(extraction=None)を返すこと
- ingestion routing: enterprise_ai_vlm は直接 VLM、DU は None で安全縮退・成功時は remap
- OCI Document Understanding client: 注入 fake SDK で非同期 job フローと remap を検証
"""

from __future__ import annotations

import json
from typing import Any

import pytest
from rag_parser_core.registry import (
    SERVICE_ADAPTER_BACKENDS,
    ParserRegistryResult,
    parse_with_registry,
)

from app.clients.oci_document_understanding import (
    OciDocumentUnderstandingClient,
    document_understanding_result_to_payload,
)
from app.config import Settings
from app.rag.ingestion import IngestionPipeline
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
        parser_profile="pdf",
    )


@pytest.mark.parametrize("backend", sorted(SERVICE_ADAPTER_BACKENDS))
def test_parse_with_registry_service_backend_returns_sentinel(backend: str) -> None:
    """service backend 明示時は core で実行せず sentinel を返し、ローカル抽出しない。"""
    result = parse_with_registry(
        b"%PDF-1.7 fake",
        source_profile=_pdf_profile(),
        content_type="application/pdf",
        adapter_backend=backend,
    )
    assert result.extraction is None
    assert result.parser_backend == backend
    assert result.fallback_used is False
    assert result.template == "enterprise_ai_fallback"


def _du_result_json() -> dict[str, Any]:
    return {
        "documentMetadata": {"pageCount": 1},
        "detectedDocumentTypes": [{"documentType": "INVOICE"}],
        "pages": [
            {
                "pageNumber": 1,
                "dimensions": {"width": 800, "height": 1000, "unit": "PIXEL"},
                "lines": [{"text": "請求書"}, {"text": "合計 1,200 円"}],
                "words": [{"text": "請求書", "confidence": 0.9}],
                "tables": [
                    {
                        "rowCount": 2,
                        "columnCount": 2,
                        "headerRows": [
                            {
                                "cells": [
                                    {"text": "品目", "rowIndex": 0, "columnIndex": 0},
                                    {"text": "金額", "rowIndex": 0, "columnIndex": 1},
                                ]
                            }
                        ],
                        "bodyRows": [
                            {
                                "cells": [
                                    {"text": "りんご", "rowIndex": 1, "columnIndex": 0},
                                    {"text": "1,200", "rowIndex": 1, "columnIndex": 1},
                                ]
                            }
                        ],
                    }
                ],
            }
        ],
    }


def test_document_understanding_remap_to_structured_extraction() -> None:
    """DU 結果 JSON が raw_text / pages / tables を持つ StructuredExtraction へ写る。"""
    payload = document_understanding_result_to_payload(_du_result_json())
    extraction = StructuredExtraction.model_validate(payload)
    assert "請求書" in extraction.raw_text
    assert "合計 1,200 円" in extraction.raw_text
    assert extraction.document_type == "INVOICE"
    assert extraction.confidence == pytest.approx(0.9)
    assert len(extraction.tables) == 1
    cells = {(cell.row, cell.col): cell.text for cell in extraction.tables[0].cells}
    assert cells[(0, 0)] == "品目"
    assert cells[(1, 1)] == "1,200"
    assert extraction.pages and extraction.pages[0].page_number == 1


# --- OCI Document Understanding client(注入 fake SDK)---
class _FakeResponse:
    def __init__(self, data: object) -> None:
        self.data = data


class _FakeJob:
    def __init__(self, job_id: str, state: str) -> None:
        self.id = job_id
        self.lifecycle_state = state
        self.lifecycle_details = ""


class _FakeDocumentClient:
    def __init__(self, states: list[str]) -> None:
        self._states = states
        self._poll = 0
        self.created: list[object] = []

    def create_processor_job(self, create_processor_job_details: object) -> _FakeResponse:
        self.created.append(create_processor_job_details)
        return _FakeResponse(_FakeJob("job-1", self._states[0]))

    def get_processor_job(self, processor_job_id: str) -> _FakeResponse:
        state = self._states[min(self._poll, len(self._states) - 1)]
        self._poll += 1
        return _FakeResponse(_FakeJob(processor_job_id, state))


class _FakeObjectEntry:
    def __init__(self, name: str) -> None:
        self.name = name


class _FakeStorageClient:
    def __init__(self, output_objects: dict[str, bytes]) -> None:
        self._output_objects = output_objects
        self.put_calls: list[tuple[str, str, str]] = []

    def put_object(
        self,
        namespace_name: str,
        bucket_name: str,
        object_name: str,
        put_object_body: bytes,
        **kwargs: Any,
    ) -> _FakeResponse:
        self.put_calls.append((namespace_name, bucket_name, object_name))
        return _FakeResponse(None)

    def list_objects(self, namespace_name: str, bucket_name: str, **kwargs: Any) -> _FakeResponse:
        prefix = str(kwargs.get("prefix", ""))
        names = [name for name in self._output_objects if name.startswith(prefix)]
        return _FakeResponse(type("L", (), {"objects": [_FakeObjectEntry(n) for n in names]})())

    def get_object(
        self, namespace_name: str, bucket_name: str, object_name: str, **kwargs: Any
    ) -> _FakeResponse:
        return _FakeResponse(type("D", (), {"content": self._output_objects[object_name]})())


def _du_settings() -> Settings:
    return Settings.model_construct(
        oci_compartment_id="ocid1.compartment.oc1..test",
        oci_region="ap-osaka-1",
        oci_config_file="~/.oci/config",
        oci_config_profile="DEFAULT",
        object_storage_region="ap-osaka-1",
        oci_document_understanding_namespace="ns",
        oci_document_understanding_input_bucket="in-bucket",
        oci_document_understanding_output_bucket="out-bucket",
        oci_document_understanding_input_prefix="document-understanding/input",
        oci_document_understanding_output_prefix="document-understanding/output",
        oci_document_understanding_language="JPN",
        oci_document_understanding_features=["DOCUMENT_TEXT_EXTRACTION", "TABLE_EXTRACTION"],
        oci_document_understanding_poll_interval_seconds=0.01,
        oci_document_understanding_timeout_seconds=5.0,
    )


async def test_du_client_analyze_runs_async_job_and_remaps() -> None:
    """put → create → poll(SUCCEEDED) → 結果取得 → remap の一連が動く。"""
    output_key = "document-understanding/output/job-1/ns_in-bucket_doc.json"
    document_client = _FakeDocumentClient(["ACCEPTED", "IN_PROGRESS", "SUCCEEDED"])
    storage_client = _FakeStorageClient({output_key: json.dumps(_du_result_json()).encode("utf-8")})
    client = OciDocumentUnderstandingClient(
        _du_settings(),
        document_client=document_client,
        object_storage_client=storage_client,
    )
    payload = await client.analyze(b"%PDF fake", content_type="application/pdf", document_id="doc")
    assert payload is not None
    extraction = StructuredExtraction.model_validate(payload)
    assert "請求書" in extraction.raw_text
    assert document_client.created  # job が作成された
    assert storage_client.put_calls  # 入力が put された


async def test_du_client_analyze_returns_none_when_unconfigured() -> None:
    """compartment/namespace/bucket 未設定なら None を返し安全に縮退する。"""
    client = OciDocumentUnderstandingClient(
        Settings.model_construct(
            oci_compartment_id="",
            oci_document_understanding_namespace="",
            oci_document_understanding_input_bucket="",
            object_storage_namespace="",
            object_storage_bucket="",
        )
    )
    assert client.is_configured() is False
    payload = await client.analyze(b"x", content_type="application/pdf", document_id="doc")
    assert payload is None


async def test_du_client_analyze_returns_none_on_job_failure() -> None:
    """job が FAILED のときは None を返す。"""
    document_client = _FakeDocumentClient(["ACCEPTED", "FAILED"])
    storage_client = _FakeStorageClient({})
    client = OciDocumentUnderstandingClient(
        _du_settings(),
        document_client=document_client,
        object_storage_client=storage_client,
    )
    payload = await client.analyze(b"x", content_type="application/pdf", document_id="doc")
    assert payload is None


# --- ingestion routing ---
def _routing_pipeline() -> IngestionPipeline:
    return IngestionPipeline(
        settings=Settings.model_construct(
            rag_extraction_artifact_cache_enabled=True,
            rag_extraction_artifact_prefix="artifacts/extractions",
        )
    )


def _service_unreachable_result(backend: str) -> ParserRegistryResult:
    """parser microservice 未到達時の fallback(extraction=None)を表す。"""
    return ParserRegistryResult(
        extraction=None,
        parser_backend=backend,
        parser_version="service_unavailable",
        fallback_used=True,
        template=f"{backend}_fallback",
        warnings=(f"{backend}_adapter_service_unreachable",),
    )


def _service_success_result(backend: str, extraction: StructuredExtraction) -> ParserRegistryResult:
    """parser microservice が抽出を返した場合の結果。"""
    return ParserRegistryResult(
        extraction=extraction,
        parser_backend=backend,
        parser_version=backend,
        template=backend,
    )


async def test_service_backend_enterprise_ai_vlm_calls_vlm(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """microservice 未到達時、enterprise_ai_vlm 明示は in-process VLM(分割込み)へ縮退する。"""
    pipeline = _routing_pipeline()
    calls: dict[str, bool] = {}

    async def _fake_vlm(**kwargs: Any) -> dict[str, object]:
        calls["vlm"] = True
        return {"raw_text": "vlm extracted"}

    async def _fake_du(**kwargs: Any) -> dict[str, object] | None:
        calls["du"] = True
        return None

    monkeypatch.setattr(pipeline, "_extract_with_vlm", _fake_vlm)
    monkeypatch.setattr(pipeline, "_extract_with_document_understanding", _fake_du)
    monkeypatch.setattr(
        pipeline._parser_service,
        "run_service_backend",
        lambda *a, **k: _service_unreachable_result("oci_genai_vision"),
    )

    result = await pipeline._extract_with_service_backend(
        "enterprise_ai_vlm",
        trace_id="t",
        document_id="doc",
        source_bytes=b"x",
        prompt="p",
        content_type="text/plain",
        parser_profile="local_text_structure",
        checkpoint_segments=(),
        cancel_checker=None,
    )
    assert calls.get("vlm") is True
    assert "du" not in calls
    assert isinstance(result, StructuredExtraction)
    assert result.raw_text == "vlm extracted"


async def test_service_backend_vlm_uses_microservice_when_available(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """microservice が抽出を返したら in-process VLM(分割)を呼ばずにそれを使う。"""
    pipeline = _routing_pipeline()
    calls: dict[str, bool] = {}
    extraction = StructuredExtraction(raw_text="microservice vlm", document_type="ドキュメント")

    async def _fake_vlm(**kwargs: Any) -> dict[str, object]:
        calls["vlm"] = True
        return {"raw_text": "in-process"}

    monkeypatch.setattr(pipeline, "_extract_with_vlm", _fake_vlm)
    monkeypatch.setattr(
        pipeline._parser_service,
        "run_service_backend",
        lambda *a, **k: _service_success_result("oci_genai_vision", extraction),
    )

    result = await pipeline._extract_with_service_backend(
        "oci_genai_vision",
        trace_id="t",
        document_id="doc",
        source_bytes=b"x",
        prompt="p",
        content_type="application/pdf",
        parser_profile="enterprise_ai_pdf_layout",
        checkpoint_segments=(),
        cancel_checker=None,
    )
    assert isinstance(result, StructuredExtraction)
    assert result.raw_text == "microservice vlm"
    assert "vlm" not in calls  # in-process VLM は呼ばれない


async def test_service_backend_du_falls_back_when_none(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """microservice 未到達かつ in-process DU も None なら縮退用に None を返す(VLM は呼ばない)。"""
    pipeline = _routing_pipeline()
    calls: dict[str, bool] = {}

    async def _fake_vlm(**kwargs: Any) -> dict[str, object]:
        calls["vlm"] = True
        return {"raw_text": "vlm"}

    async def _fake_du(**kwargs: Any) -> dict[str, object] | None:
        calls["du"] = True
        return None

    monkeypatch.setattr(pipeline, "_extract_with_vlm", _fake_vlm)
    monkeypatch.setattr(pipeline, "_extract_with_document_understanding", _fake_du)
    # microservice は未到達(fallback)→ in-process DU へ縮退する経路を検証する。
    monkeypatch.setattr(
        pipeline._parser_service,
        "run_service_backend",
        lambda *a, **k: _service_unreachable_result("oci_document_understanding"),
    )

    result = await pipeline._extract_with_service_backend(
        "oci_document_understanding",
        trace_id="t",
        document_id="doc",
        source_bytes=b"x",
        prompt="p",
        content_type="application/pdf",
        parser_profile="enterprise_ai_pdf_layout",
        checkpoint_segments=(),
        cancel_checker=None,
    )
    assert calls.get("du") is True
    assert "vlm" not in calls
    assert result is None


async def test_service_backend_du_success_returns_extraction(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """microservice 未到達でも in-process DU 成功なら remap 済み StructuredExtraction を返す。"""
    pipeline = _routing_pipeline()

    async def _fake_du(**kwargs: Any) -> dict[str, object] | None:
        return document_understanding_result_to_payload(_du_result_json())

    monkeypatch.setattr(pipeline, "_extract_with_document_understanding", _fake_du)
    monkeypatch.setattr(
        pipeline._parser_service,
        "run_service_backend",
        lambda *a, **k: _service_unreachable_result("oci_document_understanding"),
    )

    result = await pipeline._extract_with_service_backend(
        "oci_document_understanding",
        trace_id="t",
        document_id="doc",
        source_bytes=b"x",
        prompt="p",
        content_type="application/pdf",
        parser_profile="enterprise_ai_pdf_layout",
        checkpoint_segments=(),
        cancel_checker=None,
    )
    assert isinstance(result, StructuredExtraction)
    assert "請求書" in result.raw_text


async def test_service_backend_du_uses_microservice_when_available(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """microservice が抽出を返したら in-process DU を呼ばずにそれを使う。"""
    pipeline = _routing_pipeline()
    calls: dict[str, bool] = {}
    extraction = StructuredExtraction.model_validate(
        document_understanding_result_to_payload(_du_result_json())
    )

    async def _fake_du(**kwargs: Any) -> dict[str, object] | None:
        calls["du"] = True
        return None

    monkeypatch.setattr(pipeline, "_extract_with_document_understanding", _fake_du)
    monkeypatch.setattr(
        pipeline._parser_service,
        "run_service_backend",
        lambda *a, **k: _service_success_result("oci_document_understanding", extraction),
    )

    result = await pipeline._extract_with_service_backend(
        "oci_document_understanding",
        trace_id="t",
        document_id="doc",
        source_bytes=b"x",
        prompt="p",
        content_type="application/pdf",
        parser_profile="enterprise_ai_pdf_layout",
        checkpoint_segments=(),
        cancel_checker=None,
    )
    assert isinstance(result, StructuredExtraction)
    assert "請求書" in result.raw_text
    assert "du" not in calls  # in-process DU は呼ばれない
