"""segment checkpoint / artifact cache の pipeline 契約テスト。"""

from __future__ import annotations

import asyncio
import hashlib
import json
from datetime import UTC, datetime
from typing import Any

import pytest

from app.clients.oci_enterprise_ai import OciEnterpriseAiClient
from app.clients.oci_genai import OciGenAiClient
from app.config import Settings
from app.rag import ingestion as ingestion_module
from app.rag.ingestion import IngestionPipeline
from app.rag.pdf_segments import PdfPageSegment
from app.schemas.document import (
    DocumentDetail,
    FileStatus,
    IngestionSegment,
    SourceModality,
    SourcePreviewKind,
    SourceProfile,
)
from app.schemas.extraction import DocumentElement, StructuredExtraction


async def test_failed_segment_retry_reuses_successful_artifacts_only(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """FAILED segment retry は成功済み artifact を温存し、失敗 range だけ再抽出する。"""
    document_id = "doc-segment-retry"
    cached_path = "local://artifacts/extractions/doc-segment-retry/p1-2.json"
    storage = _FakeObjectStorage(
        {
            cached_path: _extraction_payload(
                "page 1-2 cached text",
                page_number=1,
                element_id="cached-e1",
                source_parser="enterprise_ai_pdf_layout",
                parser_artifacts={
                    "extraction_artifact_schema_version": (
                        ingestion_module.EXTRACTION_ARTIFACT_SCHEMA_VERSION
                    ),
                    "extraction_artifact_kind": "segment",
                    "extraction_artifact_document_id": document_id,
                    "extraction_artifact_trace_id": "trace-previous",
                    "extraction_artifact_segment_id": f"{document_id}:p1-2",
                    "extraction_artifact_page_start": 1,
                    "extraction_artifact_page_end": 2,
                },
            )
        }
    )
    oracle = _FakeSegmentRetryOracle(
        document_id=document_id,
        segments=[
            IngestionSegment(
                segment_id=f"{document_id}:p1-2",
                document_id=document_id,
                status="SUCCEEDED",
                parser_backend="enterprise_ai",
                parser_profile="enterprise_ai_pdf_layout",
                page_start=1,
                page_end=2,
                artifact_path=cached_path,
            ),
            IngestionSegment(
                segment_id=f"{document_id}:p3-4",
                document_id=document_id,
                status="FAILED",
                parser_backend="enterprise_ai",
                parser_profile="enterprise_ai_pdf_layout",
                page_start=3,
                page_end=4,
                attempt_count=1,
                error_code="enterprise_ai_response_validation_error",
                error_message="前回の segment 抽出に失敗しました。",
            ),
        ],
    )
    vlm = _RetrySegmentVlm()
    monkeypatch.setattr(
        ingestion_module,
        "_pdf_segments_for_ingestion",
        lambda *args, **kwargs: [
            PdfPageSegment(index=0, page_start=1, page_end=2, content=b"segment-1-2"),
            PdfPageSegment(index=1, page_start=3, page_end=4, content=b"segment-3-4"),
        ],
    )
    monkeypatch.setattr(asyncio, "to_thread", _inline_to_thread)
    monkeypatch.setattr(ingestion_module, "record_rag_ingestion_audit", lambda **kwargs: None)

    pipeline = IngestionPipeline(
        vlm=vlm,
        genai=_EmbeddingClient(),
        oracle=oracle,  # type: ignore[arg-type]
        object_storage=storage,  # type: ignore[arg-type]
        settings=Settings.model_construct(
            rag_chunk_size=200,
            rag_chunk_overlap=20,
            rag_pdf_segmentation_enabled=True,
            rag_pdf_max_pages_per_segment=2,
            rag_pdf_max_segments=10,
            rag_segment_checkpoint_enabled=True,
            rag_extraction_artifact_cache_enabled=True,
            rag_extraction_artifact_prefix="artifacts/extractions",
            rag_graph_enabled=False,
            rag_parser_adapter_backend="local",
            rag_parser_docling_enabled=False,
            rag_parser_marker_enabled=False,
            rag_parser_unstructured_enabled=False,
            oci_enterprise_ai_vision_model_id="test-vision",
        ),
    )

    checkpoint_segments = await pipeline._prepare_segment_checkpoints(
        document_id=document_id,
        source_bytes=b"%PDF segmented retry",
        content_type="application/pdf",
        source_profile=_pdf_source_profile(),
        parser_backend="enterprise_ai",
        parser_profile="enterprise_ai_pdf_layout",
    )
    extracted = await pipeline._extract_with_vlm(
        trace_id="trace-segment-retry",
        document_id=document_id,
        source_bytes=b"%PDF segmented retry",
        prompt="PDF を抽出してください。",
        content_type="application/pdf",
        parser_profile="enterprise_ai_pdf_layout",
        checkpoint_segments=checkpoint_segments,
    )
    extraction = StructuredExtraction.model_validate(extracted)
    extraction, cached_full_artifact_path = await pipeline._cache_extraction_artifact(
        document_id=document_id,
        trace_id="trace-segment-retry",
        extraction=extraction,
    )
    await pipeline._mark_segments_succeeded(
        checkpoint_segments,
        artifact_path=cached_full_artifact_path,
    )

    assert vlm.calls == [b"segment-3-4"]
    assert "page 1-2 cached text" in extraction.raw_text
    assert "page 3-4 retried text" in extraction.raw_text

    segments = {segment.segment_id: segment for segment in oracle.segments}
    successful = segments[f"{document_id}:p1-2"]
    retried = segments[f"{document_id}:p3-4"]
    assert successful.status == "SUCCEEDED"
    assert successful.attempt_count == 0
    assert successful.artifact_path == cached_path
    assert retried.status == "SUCCEEDED"
    assert retried.attempt_count == 2
    assert retried.artifact_path is not None
    assert retried.artifact_path != cached_path

    metadata_full_artifact_path = extraction.parser_artifacts.get("extraction_artifact_path")
    assert isinstance(metadata_full_artifact_path, str)
    assert json.loads(storage.objects[metadata_full_artifact_path])["raw_text"]
    assert storage.put_keys
    assert all("segment-1-2" not in key for key in storage.put_keys)


async def test_cached_segment_artifact_identity_mismatch_is_cache_miss() -> None:
    """別 segment の artifact は checkpoint reuse せず、該当 range を再処理へ戻す。"""
    document_id = "doc-segment-identity"
    cached_path = "local://artifacts/extractions/doc-segment-identity/p1-2.json"
    storage = _FakeObjectStorage(
        {
            cached_path: _extraction_payload(
                "wrong segment cached text",
                page_number=1,
                element_id="wrong-e1",
                source_parser="enterprise_ai_pdf_layout",
                parser_artifacts={
                    "extraction_artifact_schema_version": (
                        ingestion_module.EXTRACTION_ARTIFACT_SCHEMA_VERSION
                    ),
                    "extraction_artifact_kind": "segment",
                    "extraction_artifact_document_id": document_id,
                    "extraction_artifact_trace_id": "trace-previous",
                    "extraction_artifact_segment_id": f"{document_id}:p9-10",
                    "extraction_artifact_page_start": 9,
                    "extraction_artifact_page_end": 10,
                },
            )
        }
    )
    pipeline = IngestionPipeline(
        object_storage=storage,  # type: ignore[arg-type]
        settings=Settings.model_construct(),
    )
    segment = IngestionSegment(
        segment_id=f"{document_id}:p1-2",
        document_id=document_id,
        status="SUCCEEDED",
        parser_backend="enterprise_ai",
        parser_profile="enterprise_ai_pdf_layout",
        page_start=1,
        page_end=2,
        artifact_path=cached_path,
    )

    result = await pipeline._load_cached_segment_extractions(
        [segment],
        target_segments=[],
        available_ranges={(1, 2)},
    )

    assert result.cached == []
    assert result.missing_ranges == {(1, 2)}


async def test_cached_full_artifact_document_mismatch_is_not_reused() -> None:
    """別 document の full artifact は全 segment 成功済みでも再利用しない。"""
    document_id = "doc-full-identity"
    cached_path = "local://artifacts/extractions/doc-full-identity/full.json"
    storage = _FakeObjectStorage(
        {
            cached_path: _extraction_payload(
                "wrong document full text",
                page_number=1,
                element_id="wrong-doc-e1",
                source_parser="enterprise_ai_pdf_layout",
                parser_artifacts={
                    "extraction_artifact_schema_version": (
                        ingestion_module.EXTRACTION_ARTIFACT_SCHEMA_VERSION
                    ),
                    "extraction_artifact_kind": "full",
                    "extraction_artifact_document_id": "another-document",
                    "extraction_artifact_trace_id": "trace-previous",
                },
            )
        }
    )
    pipeline = IngestionPipeline(
        object_storage=storage,  # type: ignore[arg-type]
        settings=Settings.model_construct(),
    )
    segment = IngestionSegment(
        segment_id=f"{document_id}:source",
        document_id=document_id,
        status="SUCCEEDED",
        parser_backend="enterprise_ai",
        parser_profile="enterprise_ai_pdf_layout",
        artifact_path=cached_path,
    )

    assert await pipeline._load_cached_full_extraction([segment]) is None


class _RetrySegmentVlm(OciEnterpriseAiClient):
    """失敗 segment だけが呼ばれたことを記録する VLM fake。"""

    def __init__(self) -> None:
        self.calls: list[bytes] = []

    async def extract_with_vlm(
        self,
        image_bytes: bytes,
        prompt: str,
        *,
        mime_type: str = "application/octet-stream",
        parser_profile: str = "enterprise_ai_generic",
    ) -> dict[str, object]:
        _ = prompt, mime_type, parser_profile
        self.calls.append(image_bytes)
        assert image_bytes == b"segment-3-4"
        return _extraction_payload(
            "page 3-4 retried text",
            page_number=1,
            element_id="retried-e1",
            source_parser="enterprise_ai_pdf_layout",
        )


class _EmbeddingClient(OciGenAiClient):
    """入力件数分の 1536 次元 embedding を返す fake。"""

    async def embed(
        self,
        texts: list[str],
        *,
        input_type: str = "SEARCH_DOCUMENT",
    ) -> list[list[float]]:
        _ = input_type
        return [[1.0] + [0.0] * 1535 for _ in texts]


class _FakeObjectStorage:
    """Object Storage artifact cache をメモリ上で再現する。"""

    def __init__(self, initial: dict[str, dict[str, object]]) -> None:
        self.objects = {
            key: json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode()
            for key, value in initial.items()
        }
        self.put_keys: list[str] = []

    async def put(self, key: str, data: bytes, content_type: str) -> str:
        assert content_type == "application/json"
        reference = f"local://{key}"
        self.objects[reference] = data
        self.put_keys.append(key)
        return reference

    async def get(self, key: str) -> bytes:
        return self.objects[key]


class _FakeSegmentRetryOracle:
    """IngestionPipeline が使う Oracle 操作だけを実装する fake。"""

    def __init__(
        self,
        *,
        document_id: str,
        segments: list[IngestionSegment],
    ) -> None:
        self.document = DocumentDetail(
            id=document_id,
            file_name="segmented.pdf",
            status=FileStatus.UPLOADED,
            content_type="application/pdf",
            file_size_bytes=20,
            content_sha256=hashlib.sha256(b"%PDF segmented retry").hexdigest(),
            uploaded_at=datetime.now(UTC),
            object_storage_path="local://uploaded/segmented.pdf",
            source_profile=_pdf_source_profile(),
        )
        self.segments = segments
        self.saved_extraction: StructuredExtraction | None = None
        self.saved_chunk_count = 0
        self.saved_vector_count = 0

    async def update_document_status(
        self,
        document_id: str,
        status: FileStatus,
        error_message: str | None = None,
    ) -> DocumentDetail:
        assert document_id == self.document.id
        self.document = self.document.model_copy(
            update={"status": status, "error_message": error_message}
        )
        return self.document

    async def replace_ingestion_segments(
        self,
        document_id: str,
        segments: list[IngestionSegment],
    ) -> list[IngestionSegment]:
        assert document_id == self.document.id
        self.segments = list(segments)
        return list(self.segments)

    async def list_ingestion_segments(self, document_id: str) -> list[IngestionSegment]:
        assert document_id == self.document.id
        return [segment.model_copy() for segment in self.segments]

    async def update_ingestion_segment(
        self,
        segment_id: str,
        *,
        status: str | None = None,
        attempt_count: int | None = None,
        artifact_path: str | None = None,
        error_code: str | None = None,
        error_message: str | None = None,
    ) -> IngestionSegment | None:
        for index, segment in enumerate(self.segments):
            if segment.segment_id != segment_id:
                continue
            updates: dict[str, Any] = {}
            if status is not None:
                updates["status"] = status
            if attempt_count is not None:
                updates["attempt_count"] = attempt_count
            if artifact_path is not None:
                updates["artifact_path"] = artifact_path
            if error_code is not None:
                updates["error_code"] = error_code
            if error_message is not None:
                updates["error_message"] = error_message
            self.segments[index] = segment.model_copy(update=updates)
            return self.segments[index]
        return None

    async def save_index(
        self,
        document_id: str,
        extraction: StructuredExtraction,
        chunks: list[Any],
        vectors: list[list[float]],
    ) -> None:
        assert document_id == self.document.id
        self.saved_extraction = extraction
        self.saved_chunk_count = len(chunks)
        self.saved_vector_count = len(vectors)


def _pdf_source_profile() -> SourceProfile:
    digest = hashlib.sha256(b"%PDF segmented retry").hexdigest()
    return SourceProfile(
        original_file_name="segmented.pdf",
        sanitized_file_name="segmented.pdf",
        extension=".pdf",
        content_type="application/pdf",
        inferred_content_type="application/pdf",
        file_size_bytes=20,
        content_sha256=digest,
        modality=SourceModality.PDF,
        parser_profile="enterprise_ai_pdf_layout",
        parser_backend="enterprise_ai",
        parser_version="enterprise_ai_v1",
        preview_kind=SourcePreviewKind.PDF,
        quality_warnings=[],
    )


async def _inline_to_thread(func: Any, /, *args: Any, **kwargs: Any) -> Any:
    """unit test では thread を残さず、to_thread 対象を同期実行する。"""
    return func(*args, **kwargs)


def _extraction_payload(
    text: str,
    *,
    page_number: int,
    element_id: str,
    source_parser: str,
    parser_artifacts: dict[str, object] | None = None,
) -> dict[str, object]:
    resolved_parser_artifacts = {
        "parser_backend": "enterprise_ai",
        "source_parser": source_parser,
        **(parser_artifacts or {}),
    }
    extraction = StructuredExtraction(
        raw_text=text,
        document_type="社内規程",
        confidence=0.92,
        elements=[
            DocumentElement(
                kind="text",
                text=text,
                order=1,
                element_id=element_id,
                page_number=page_number,
                source_parser=source_parser,
            )
        ],
        parser_artifacts=resolved_parser_artifacts,
    )
    return extraction.to_document_payload()
