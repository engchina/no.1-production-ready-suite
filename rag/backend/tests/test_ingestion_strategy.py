"""source profile と取込 pipeline の抽出 strategy 接続テスト。"""

import json
import zipfile
from datetime import UTC, datetime
from io import BytesIO
from typing import Any, cast

import pytest
from pypdf import PdfReader, PdfWriter

from app.clients.oci_enterprise_ai import (
    EnterpriseAiIncompleteResponseError,
    EnterpriseAiValidationError,
    OciEnterpriseAiClient,
)
from app.clients.oci_genai import OciGenAiClient
from app.config import Settings
from app.rag import ingestion as ingestion_module
from app.rag.graph_index import GraphIndex
from app.rag.ingestion import (
    EXTRACTION_ARTIFACT_SCHEMA_VERSION,
    IngestionCancelledError,
    IngestionPipeline,
    IngestionUserError,
)
from app.rag.ingestion_quality import build_ingestion_quality_report
from app.rag.parsers import ParserRegistryResult
from app.schemas.document import (
    DocumentDetail,
    FileStatus,
    IngestionSegment,
    SourceModality,
    SourceProfile,
)
from app.schemas.extraction import (
    DocumentElement,
    ExtractionAsset,
    ExtractionPage,
    ExtractionTable,
    ExtractionTableCell,
    StructuredExtraction,
)
from app.schemas.knowledge_base import KnowledgeBaseRef


class FakeOracle:
    """IngestionPipeline に必要な OracleClient subset。"""

    def __init__(self) -> None:
        self.saved_extraction: StructuredExtraction | None = None
        self.saved_chunk_count = 0
        self.atomic_index_save_count = 0
        self.saved_graph_index: GraphIndex | None = None
        self.graph_document_id: str | None = None
        self.statuses: list[FileStatus] = []
        self.segments: dict[str, IngestionSegment] = {}

    async def update_document_status(
        self,
        document_id: str,
        status: FileStatus,
        error_message: str | None = None,
    ) -> DocumentDetail:
        self.statuses.append(status)
        return DocumentDetail(
            id=document_id,
            file_name="layout.pdf",
            status=status,
            content_type="application/pdf",
            file_size_bytes=7,
            content_sha256="a" * 64,
            uploaded_at=datetime.now(UTC),
            indexed_at=datetime.now(UTC) if status == FileStatus.INDEXED else None,
            error_message=error_message,
        )

    async def save_extraction(
        self,
        document_id: str,
        extraction: StructuredExtraction,
    ) -> None:
        _ = document_id
        self.saved_extraction = extraction

    async def save_chunks(
        self,
        document_id: str,
        chunks: list[Any],
        vectors: list[list[float]],
    ) -> None:
        _ = document_id, vectors
        self.saved_chunk_count = len(chunks)

    async def save_index(
        self,
        document_id: str,
        extraction: StructuredExtraction,
        chunks: list[Any],
        vectors: list[list[float]],
    ) -> None:
        _ = document_id, vectors
        self.atomic_index_save_count += 1
        self.saved_extraction = extraction
        self.saved_chunk_count = len(chunks)

    async def list_document_knowledge_bases(
        self,
        document_id: str,
    ) -> list[KnowledgeBaseRef]:
        _ = document_id
        return [KnowledgeBaseRef(id="kb-1", name="社内規程")]

    async def replace_document_graph_index(
        self,
        document_id: str,
        graph_index: GraphIndex,
    ) -> None:
        self.graph_document_id = document_id
        self.saved_graph_index = graph_index

    async def replace_ingestion_segments(
        self,
        document_id: str,
        segments: list[IngestionSegment],
    ) -> list[IngestionSegment]:
        self.segments = {
            segment.segment_id: segment.model_copy(update={"document_id": document_id})
            for segment in segments
        }
        return list(self.segments.values())

    async def list_ingestion_segments(self, document_id: str) -> list[IngestionSegment]:
        return [segment for segment in self.segments.values() if segment.document_id == document_id]

    async def update_ingestion_segment(
        self,
        segment_id: str,
        **updates: object,
    ) -> IngestionSegment | None:
        segment = self.segments.get(segment_id)
        if segment is None:
            return None
        updated = segment.model_copy(
            update={key: value for key, value in updates.items() if value is not None}
        )
        self.segments[segment_id] = updated
        return updated


class CapturingVlm(OciEnterpriseAiClient):
    """VLM 呼び出し時の parser profile と prompt を記録する。"""

    parser_profile: str | None = None
    prompt = ""

    async def extract_with_vlm(
        self,
        image_bytes: bytes,
        prompt: str,
        *,
        mime_type: str = "application/octet-stream",
        parser_profile: str = "enterprise_ai_generic",
    ) -> dict[str, object]:
        _ = image_bytes, mime_type
        self.parser_profile = parser_profile
        self.prompt = prompt
        return {
            "raw_text": "PDF 規程本文です。\n| 項目 | 値 |\n| 承認 | 部門長 |",
            "document_type": "社内規程",
            "confidence": 0.92,
            "warnings": [],
            "elements": [
                {"kind": "text", "text": "PDF 規程本文です。", "order": 1, "page_number": 1},
                {
                    "kind": "table",
                    "text": "| 項目 | 値 |\n| 承認 | 部門長 |",
                    "order": 2,
                    "page_number": 1,
                },
            ],
        }


class SegmentCapturingVlm(OciEnterpriseAiClient):
    """PDF segment ごとの VLM 呼び出しを記録する。"""

    def __init__(self, *, fail_multi_page_once: bool = False) -> None:
        self.fail_multi_page_once = fail_multi_page_once
        self.failed_once = False
        self.page_counts: list[int] = []
        self.prompts: list[str] = []

    async def extract_with_vlm(
        self,
        image_bytes: bytes,
        prompt: str,
        *,
        mime_type: str = "application/octet-stream",
        parser_profile: str = "enterprise_ai_generic",
    ) -> dict[str, object]:
        _ = mime_type, parser_profile
        reader = PdfReader(BytesIO(image_bytes))
        page_count = len(reader.pages)
        self.page_counts.append(page_count)
        self.prompts.append(prompt)
        if self.fail_multi_page_once and page_count > 1 and not self.failed_once:
            self.failed_once = True
            raise EnterpriseAiIncompleteResponseError("max_output_tokens")
        call_index = len(self.page_counts)
        return {
            "raw_text": f"segment {call_index} の本文",
            "document_type": "社内規程",
            "confidence": 0.9,
            "warnings": [],
            "elements": [
                {
                    "kind": "text",
                    "text": f"segment {call_index} の本文",
                    "order": 1,
                    "page_number": 1,
                }
            ],
        }


class SegmentFailingVlm(OciEnterpriseAiClient):
    """指定回数目の PDF segment 抽出で失敗する VLM fake。"""

    def __init__(self, *, fail_on_call: int) -> None:
        self.fail_on_call = fail_on_call
        self.page_counts: list[int] = []

    async def extract_with_vlm(
        self,
        image_bytes: bytes,
        prompt: str,
        *,
        mime_type: str = "application/octet-stream",
        parser_profile: str = "enterprise_ai_generic",
    ) -> dict[str, object]:
        _ = prompt, mime_type, parser_profile
        reader = PdfReader(BytesIO(image_bytes))
        page_count = len(reader.pages)
        self.page_counts.append(page_count)
        call_index = len(self.page_counts)
        if call_index == self.fail_on_call:
            raise RuntimeError("segment failure")
        return {
            "raw_text": f"segment {call_index} の本文",
            "document_type": "社内規程",
            "confidence": 0.9,
            "warnings": [],
            "elements": [
                {
                    "kind": "text",
                    "text": f"segment {call_index} の本文",
                    "order": 1,
                    "page_number": 1,
                }
            ],
        }


class InvalidStructuredExtractionVlm(OciEnterpriseAiClient):
    """schema 不整合の VLM 応答を返す fake。"""

    async def extract_with_vlm(
        self,
        image_bytes: bytes,
        prompt: str,
        *,
        mime_type: str = "application/octet-stream",
        parser_profile: str = "enterprise_ai_generic",
    ) -> dict[str, object]:
        _ = image_bytes, prompt, mime_type, parser_profile
        return {
            "raw_text": "schema 不整合の本文",
            "document_type": "社内規程",
            "confidence": 1.4,
            "warnings": [],
        }


class FailingIfCalledVlm(OciEnterpriseAiClient):
    """再実行時に cached extraction が使われることを検証する VLM fake。"""

    async def extract_with_vlm(
        self,
        image_bytes: bytes,
        prompt: str,
        *,
        mime_type: str = "application/octet-stream",
        parser_profile: str = "enterprise_ai_generic",
    ) -> dict[str, object]:
        _ = image_bytes, prompt, mime_type, parser_profile
        raise AssertionError("cached extraction artifact があれば VLM を再実行しない")


class FakeEmbeddingClient(OciGenAiClient):
    """チャンク数分の 1536 次元ベクトルを返す。"""

    async def embed(
        self,
        texts: list[str],
        *,
        input_type: str = "SEARCH_DOCUMENT",
    ) -> list[list[float]]:
        _ = input_type
        return [[1.0] + [0.0] * 1535 for _ in texts]


class FailingEmbeddingClient(OciGenAiClient):
    """抽出 artifact cache 後の後段失敗を作る embedding fake。"""

    async def embed(
        self,
        texts: list[str],
        *,
        input_type: str = "SEARCH_DOCUMENT",
    ) -> list[list[float]]:
        _ = texts, input_type
        raise RuntimeError("embedding failure")


class FakeObjectStorage:
    """抽出 artifact cache 用の Object Storage fake。"""

    def __init__(self) -> None:
        self.puts: list[tuple[str, bytes, str]] = []
        self.gets: list[str] = []
        self.objects: dict[str, bytes] = {}

    async def put(self, key: str, data: bytes, content_type: str) -> str:
        self.puts.append((key, data, content_type))
        path = f"oci://namespace/bucket/{key}"
        self.objects[path] = data
        return path

    async def get(self, path: str) -> bytes:
        self.gets.append(path)
        return self.objects[path]

    def seed_extraction(self, path: str, extraction: StructuredExtraction) -> None:
        self.objects[path] = extraction.model_dump_json().encode("utf-8")


class FailingObjectStorage(FakeObjectStorage):
    """full extraction artifact cache 失敗を作る Object Storage fake。"""

    async def put(self, key: str, data: bytes, content_type: str) -> str:
        self.puts.append((key, data, content_type))
        raise RuntimeError("object storage unavailable")


def test_ingestion_trace_structure_attributes_follow_quality_report_counts() -> None:
    """chunking trace は quality_report と同じ構造件数を記録する。"""
    extraction = StructuredExtraction(
        raw_text="本文\n表\n図",
        confidence=0.92,
        elements=[
            DocumentElement(kind="text", text="本文", page_number=1, confidence=0.6),
        ],
        pages=[
            ExtractionPage(page_number=1, element_ids=["el-0000"]),
            ExtractionPage(page_number=2),
            ExtractionPage(page_number=3, element_ids=["tbl-main"]),
            ExtractionPage(page_number=4, element_ids=["fig-1"]),
        ],
        tables=[
            ExtractionTable(
                table_id="tbl-main",
                element_id="tbl-main",
                page_number=3,
                cells=[ExtractionTableCell(row=0, col=0, text="金額", confidence=0.4)],
            )
        ],
        assets=[
            ExtractionAsset(asset_id="fig-1", kind="image", page_number=4, alt_text="構成図")
        ],
        parser_artifacts={
            "page_count": 5,
            "table_count": 2,
            "equation_count": 2,
            "asset_count": 3,
            "low_confidence_count": 4,
            "failed_segment_count": 1,
        },
    )
    quality = build_ingestion_quality_report(extraction)
    extraction = extraction.model_copy(update={"quality_report": quality})

    attributes = ingestion_module._extraction_structure_attributes(extraction)

    assert attributes == {
        "element_count": 1,
        "table_count": 2,
        "figure_count": 3,
        "formula_count": 2,
        "page_count": 5,
        "asset_count": 3,
        "low_confidence_count": 4,
        "failed_segment_count": 1,
    }


def test_raw_vlm_trace_structure_attributes_read_first_class_payload_fields() -> None:
    """VLM trace は raw payload の pages/tables/assets/artifacts も件数化する。"""
    attributes = ingestion_module._raw_extraction_structure_attributes(
        {
            "elements": [
                {
                    "kind": "text",
                    "text": "本文",
                    "page_number": 1,
                }
            ],
            "pages": [
                {"page_number": 1, "element_ids": ["el-1"]},
                {"page_number": 2, "element_ids": []},
                {"page_number": 3, "element_ids": ["tbl-main"]},
                {"page_number": 4, "element_ids": ["fig-1"]},
            ],
            "tables": [{"table_id": "tbl-main", "page_number": 3}],
            "assets": [{"asset_id": "fig-1", "kind": "image", "page_number": 4}],
            "parser_artifacts": {
                "page_count": "5",
                "adapter_table_count": 2,
                "equation_count": 2,
                "asset_count": 3,
            },
        }
    )

    assert attributes == {
        "element_count": 1,
        "table_count": 2,
        "figure_count": 3,
        "formula_count": 2,
        "page_count": 5,
        "asset_count": 3,
    }


async def test_ingestion_pipeline_applies_parser_profile_strategy() -> None:
    """source profile の parser profile を VLM prompt と品質レポートへ通す。"""
    oracle = FakeOracle()
    vlm = CapturingVlm()
    pipeline = IngestionPipeline(
        vlm=vlm,
        genai=FakeEmbeddingClient(),
        oracle=cast(Any, oracle),
    )
    source_profile = SourceProfile(
        original_file_name="layout.pdf",
        sanitized_file_name="layout.pdf",
        extension=".pdf",
        content_type="application/pdf",
        inferred_content_type="application/pdf",
        file_size_bytes=7,
        content_sha256="a" * 64,
        modality=SourceModality.PDF,
        parser_profile="enterprise_ai_pdf_layout",
    )

    detail = await pipeline.ingest(
        "doc-layout",
        b"pdfdata",
        "本文を抽出してください。",
        content_type="application/pdf",
        source_profile=source_profile,
    )

    assert detail.status == FileStatus.INDEXED
    assert oracle.statuses == [FileStatus.INGESTING, FileStatus.INDEXED]
    assert oracle.saved_chunk_count > 0
    assert vlm.parser_profile == "enterprise_ai_pdf_layout"
    assert "PDF レイアウト解析方針" in vlm.prompt
    assert oracle.saved_extraction is not None
    assert oracle.saved_extraction.quality_report is not None
    assert oracle.saved_extraction.quality_report.parser_profile == "enterprise_ai_pdf_layout"
    assert "table_structure_review" in oracle.saved_extraction.quality_report.quality_warnings


async def test_ingestion_pipeline_caches_extraction_artifact_and_segment_checkpoint() -> None:
    """抽出 artifact を保存し、segment checkpoint に artifact path を残す。"""
    oracle = FakeOracle()
    storage = FakeObjectStorage()
    pipeline = IngestionPipeline(
        vlm=CapturingVlm(),
        genai=FakeEmbeddingClient(),
        oracle=cast(Any, oracle),
        object_storage=cast(Any, storage),
    )

    detail = await pipeline.ingest(
        "doc-artifact",
        b"pdfdata",
        "本文を抽出してください。",
        content_type="application/pdf",
        source_profile=_pdf_source_profile(file_size_bytes=7),
    )

    assert detail.status == FileStatus.INDEXED
    assert storage.puts
    key, data, content_type = storage.puts[0]
    assert key.startswith("artifacts/extractions/doc-artifact/")
    assert content_type == "application/json"
    assert b"raw_text" in data
    payload = json.loads(data)
    parser_artifacts = payload["parser_artifacts"]
    trace_key = key.rsplit("/", 1)[-1].removesuffix(".json")
    assert parser_artifacts["extraction_artifact_schema_version"] == 1
    assert parser_artifacts["extraction_artifact_kind"] == "full"
    assert parser_artifacts["extraction_artifact_document_id"] == "doc-artifact"
    assert parser_artifacts["extraction_artifact_trace_id"] == trace_key
    assert oracle.saved_extraction is not None
    artifact_path = oracle.saved_extraction.parser_artifacts["extraction_artifact_path"]
    assert isinstance(artifact_path, str)
    assert artifact_path.startswith("oci://namespace/bucket/artifacts/extractions/doc-artifact/")
    assert (
        oracle.saved_extraction.parser_artifacts["extraction_artifact_schema_version"] == 1
    )
    assert oracle.saved_extraction.parser_artifacts["extraction_artifact_kind"] == "full"
    assert oracle.segments
    assert all(segment.status == "SUCCEEDED" for segment in oracle.segments.values())
    assert all(segment.artifact_path == artifact_path for segment in oracle.segments.values())


async def test_ingestion_pipeline_sanitizes_extraction_artifact_keys() -> None:
    """artifact cache key は prefix/document id を安全な Object Storage path に正規化する。"""
    oracle = FakeOracle()
    storage = FakeObjectStorage()
    pipeline = IngestionPipeline(
        vlm=CapturingVlm(),
        genai=FakeEmbeddingClient(),
        oracle=cast(Any, oracle),
        object_storage=cast(Any, storage),
        settings=Settings(
            rag_extraction_artifact_prefix=(
                "../unsafe//prefix with space\\nested/./../final"
            ),
        ),
    )

    await pipeline.ingest(
        "doc/with/slash:artifact?",
        b"pdfdata",
        "本文を抽出してください。",
        content_type="application/pdf",
        source_profile=_pdf_source_profile(file_size_bytes=7),
    )

    assert storage.puts
    key, _data, _content_type = storage.puts[0]
    assert key.startswith("unsafe/prefix_with_space/nested/final/doc_with_slash_artifact_/")
    assert "\\" not in key
    assert " " not in key
    assert "//" not in key
    assert ".." not in key.split("/")
    assert oracle.saved_extraction is not None
    artifact_path = oracle.saved_extraction.parser_artifacts["extraction_artifact_path"]
    assert isinstance(artifact_path, str)
    assert "/doc_with_slash_artifact_/" in artifact_path


async def test_ingestion_pipeline_reports_extraction_artifact_cache_failure() -> None:
    """artifact cache 失敗は保存 payload と品質レポートへ警告として残す。"""
    oracle = FakeOracle()
    storage = FailingObjectStorage()
    pipeline = IngestionPipeline(
        vlm=CapturingVlm(),
        genai=FakeEmbeddingClient(),
        oracle=cast(Any, oracle),
        object_storage=cast(Any, storage),
    )

    detail = await pipeline.ingest(
        "doc-artifact-cache-failure",
        b"pdfdata",
        "本文を抽出してください。",
        content_type="application/pdf",
        source_profile=_pdf_source_profile(file_size_bytes=7),
    )

    assert detail.status == FileStatus.INDEXED
    assert storage.puts
    assert oracle.saved_extraction is not None
    assert oracle.saved_extraction.parser_artifacts["extraction_artifact_cache_failed"] is True
    assert "extraction_artifact_cache_failed" in oracle.saved_extraction.warnings
    assert oracle.saved_extraction.quality_report is not None
    assert (
        "extraction_artifact_cache_failed"
        in oracle.saved_extraction.quality_report.quality_warnings
    )
    assert oracle.saved_extraction.quality_report.risk_level == "medium"
    assert all(segment.artifact_path is None for segment in oracle.segments.values())


async def test_ingestion_pipeline_reuses_full_extraction_artifact_after_embedding_failure() -> None:
    """VLM 成功後の後段失敗は full extraction artifact から復旧し VLM を再実行しない。"""
    oracle = FakeOracle()
    storage = FakeObjectStorage()
    first_pipeline = IngestionPipeline(
        vlm=CapturingVlm(),
        genai=FailingEmbeddingClient(),
        oracle=cast(Any, oracle),
        object_storage=cast(Any, storage),
    )

    with pytest.raises(RuntimeError, match="embedding failure"):
        await first_pipeline.ingest(
            "doc-full-artifact-retry",
            b"pdfdata",
            "本文を抽出してください。",
            content_type="application/pdf",
            source_profile=_pdf_source_profile(file_size_bytes=7),
        )

    assert oracle.statuses == [FileStatus.INGESTING, FileStatus.ERROR]
    assert oracle.saved_extraction is None
    assert oracle.saved_chunk_count == 0
    assert len(storage.puts) == 1
    [source_segment] = list(oracle.segments.values())
    assert source_segment.status == "SUCCEEDED"
    assert source_segment.artifact_path is not None

    second_pipeline = IngestionPipeline(
        vlm=FailingIfCalledVlm(),
        genai=FakeEmbeddingClient(),
        oracle=cast(Any, oracle),
        object_storage=cast(Any, storage),
    )

    detail = await second_pipeline.ingest(
        "doc-full-artifact-retry",
        b"pdfdata",
        "本文を抽出してください。",
        content_type="application/pdf",
        source_profile=_pdf_source_profile(file_size_bytes=7),
    )

    assert detail.status == FileStatus.INDEXED
    assert storage.gets == [source_segment.artifact_path]
    assert len(storage.puts) == 2
    assert oracle.saved_extraction is not None
    assert oracle.saved_extraction.parser_artifacts["extraction_artifact_reused"] is True
    assert "PDF 規程本文です。" in oracle.saved_extraction.raw_text
    assert oracle.saved_chunk_count > 0


@pytest.mark.parametrize(
    ("document_id", "source_bytes", "source_profile", "expected_ids"),
    [
        (
            "doc-slides",
            lambda: _pptx_bytes(["第一スライド", "第二スライド"]),
            lambda: _office_source_profile(
                file_name="slides.pptx",
                content_type=(
                    "application/vnd.openxmlformats-officedocument.presentationml.presentation"
                ),
            ),
            ["doc-slides:slide1", "doc-slides:slide2"],
        ),
        (
            "doc-sheets",
            lambda: _xlsx_bytes(["Sheet A", "Sheet B"]),
            lambda: _office_source_profile(
                file_name="book.xlsx",
                content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            ),
            ["doc-sheets:sheet1", "doc-sheets:sheet2"],
        ),
    ],
)
async def test_ingestion_pipeline_creates_openxml_office_segment_checkpoints(
    document_id: str,
    source_bytes: Any,
    source_profile: Any,
    expected_ids: list[str],
) -> None:
    """OpenXML Office は slide/sheet 単位の checkpoint を残す。"""
    oracle = FakeOracle()
    storage = FakeObjectStorage()
    pipeline = IngestionPipeline(
        vlm=CapturingVlm(),
        genai=FakeEmbeddingClient(),
        oracle=cast(Any, oracle),
        object_storage=cast(Any, storage),
    )

    detail = await pipeline.ingest(
        document_id,
        source_bytes(),
        "本文を抽出してください。",
        content_type=source_profile().content_type,
        source_profile=source_profile(),
    )

    assert detail.status == FileStatus.INDEXED
    assert list(oracle.segments) == expected_ids
    assert all(segment.status == "SUCCEEDED" for segment in oracle.segments.values())
    assert [segment.page_start for segment in oracle.segments.values()] == [1, 2]
    assert [segment.page_end for segment in oracle.segments.values()] == [1, 2]
    assert oracle.saved_extraction is not None
    full_artifact_path = oracle.saved_extraction.parser_artifacts["extraction_artifact_path"]
    assert all(segment.artifact_path for segment in oracle.segments.values())
    assert all(
        segment.artifact_path != full_artifact_path for segment in oracle.segments.values()
    )
    assert all("/segments/" in str(segment.artifact_path) for segment in oracle.segments.values())
    segment_puts = [
        (key, json.loads(data))
        for key, data, _content_type in storage.puts
        if "/segments/" in key
    ]
    assert len(segment_puts) == 2
    segment_artifacts = [payload["parser_artifacts"] for _key, payload in segment_puts]
    assert {artifact["extraction_artifact_segment_id"] for artifact in segment_artifacts} == set(
        expected_ids
    )
    assert all(
        artifact["extraction_artifact_schema_version"] == 1
        and artifact["extraction_artifact_kind"] == "segment"
        and artifact["extraction_artifact_document_id"] == document_id
        and artifact["extraction_artifact_page_start"] == artifact["extraction_artifact_page_end"]
        for artifact in segment_artifacts
    )


async def test_ingestion_pipeline_keeps_successful_external_adapter_for_office(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """外部 adapter が成功した Office は local segment parser で上書きしない。"""
    oracle = FakeOracle()
    storage = FakeObjectStorage()
    pipeline = IngestionPipeline(
        vlm=CapturingVlm(),
        genai=FakeEmbeddingClient(),
        oracle=cast(Any, oracle),
        object_storage=cast(Any, storage),
        settings=Settings(
            rag_parser_adapter_backend="docling",
            rag_parser_docling_enabled=True,
        ),
    )
    source_profile = _office_source_profile(
        file_name="slides.pptx",
        content_type="application/vnd.openxmlformats-officedocument.presentationml.presentation",
    )
    adapter_extraction = StructuredExtraction.model_validate(
        {
            "raw_text": "Docling adapter が抽出した Office 本文",
            "document_type": "プレゼンテーション",
            "confidence": 0.99,
            "elements": [
                {
                    "kind": "text",
                    "text": "Docling adapter が抽出した Office 本文",
                    "order": 1,
                    "page_number": 1,
                    "source_parser": "docling_adapter",
                }
            ],
            "parser_artifacts": {
                "source_parser": "docling_adapter",
                "external_adapter": "docling",
            },
        }
    )

    def fake_parse_with_registry(*_args: object, **_kwargs: object) -> ParserRegistryResult:
        return ParserRegistryResult(
            extraction=adapter_extraction,
            parser_backend="docling",
            parser_version="docling:1.0.0",
            template="office_slide",
        )

    def fail_local_office_parser(*_args: object, **_kwargs: object) -> object:
        raise AssertionError("external adapter の成功結果を local Office parser で上書きしない")

    monkeypatch.setattr(ingestion_module, "parse_with_registry", fake_parse_with_registry)
    monkeypatch.setattr(
        ingestion_module,
        "parse_openxml_office_segment_extractions",
        fail_local_office_parser,
    )

    detail = await pipeline.ingest(
        "doc-external-office",
        _pptx_bytes(["第一スライド", "第二スライド"]),
        "本文を抽出してください。",
        content_type=source_profile.content_type,
        source_profile=source_profile,
    )

    assert detail.status == FileStatus.INDEXED
    assert oracle.atomic_index_save_count == 1
    assert oracle.saved_extraction is not None
    assert oracle.saved_extraction.raw_text == "Docling adapter が抽出した Office 本文"
    assert oracle.saved_extraction.parser_artifacts["parser_backend"] == "docling"
    assert oracle.saved_extraction.quality_report is not None
    assert oracle.saved_extraction.quality_report.parser_backend == "docling"
    assert all(segment.parser_backend == "docling" for segment in oracle.segments.values())
    full_artifact_path = oracle.saved_extraction.parser_artifacts["extraction_artifact_path"]
    assert all(segment.artifact_path == full_artifact_path for segment in oracle.segments.values())
    assert [key for key, _data, _content_type in storage.puts if "/segments/" in key] == []


async def test_ingestion_pipeline_cancel_after_extraction_does_not_save_index() -> None:
    """cancel 済み job は extraction/chunk/index 保存と INDEXED 遷移を止める。"""
    oracle = FakeOracle()
    pipeline = IngestionPipeline(
        vlm=CapturingVlm(),
        genai=FakeEmbeddingClient(),
        oracle=cast(Any, oracle),
    )
    checks = 0

    async def cancel_after_extraction() -> bool:
        nonlocal checks
        checks += 1
        return checks >= 3

    with pytest.raises(IngestionCancelledError):
        await pipeline.ingest(
            "doc-cancel",
            b"pdfdata",
            "本文を抽出してください。",
            content_type="application/pdf",
            source_profile=_pdf_source_profile(file_size_bytes=7),
            cancel_checker=cancel_after_extraction,
        )

    assert oracle.statuses == [FileStatus.INGESTING]
    assert oracle.atomic_index_save_count == 0
    assert oracle.saved_extraction is None
    assert oracle.saved_chunk_count == 0


async def test_ingestion_pipeline_writes_graph_index_when_enabled() -> None:
    """RAG_GRAPH_ENABLED 時は取込結果から GraphRAG-lite index を保存する。"""
    oracle = FakeOracle()
    settings = Settings.model_construct(
        rag_graph_enabled=True,
        rag_chunk_size=800,
        rag_chunk_overlap=120,
        oci_genai_embedding_model="cohere.embed-v4.0",
        oci_enterprise_ai_models=[],
        oci_enterprise_ai_default_model="",
        oci_enterprise_ai_vlm_model="enterprise-vlm",
    )
    pipeline = IngestionPipeline(
        vlm=CapturingVlm(),
        genai=FakeEmbeddingClient(),
        oracle=cast(Any, oracle),
        settings=settings,
    )

    detail = await pipeline.ingest(
        "doc-graph",
        b"pdfdata",
        "本文を抽出してください。",
        content_type="application/pdf",
        source_profile=_pdf_source_profile(file_size_bytes=7),
    )

    assert detail.status == FileStatus.INDEXED
    assert oracle.graph_document_id == "doc-graph"
    assert oracle.saved_graph_index is not None
    assert {entity.knowledge_base_id for entity in oracle.saved_graph_index.entities} == {"kb-1"}
    assert oracle.saved_graph_index.relationships
    assert oracle.saved_graph_index.claims
    assert oracle.saved_graph_index.community_summaries
    assert oracle.saved_graph_index.entity_chunk_links


async def test_ingestion_pipeline_splits_pdf_into_page_segments() -> None:
    """多ページ PDF は小さな page segment に分割して VLM 抽出する。"""
    oracle = FakeOracle()
    vlm = SegmentCapturingVlm()
    settings = Settings.model_construct(
        rag_pdf_segmentation_enabled=True,
        rag_pdf_max_pages_per_segment=2,
        rag_pdf_max_segments=10,
        rag_chunk_size=800,
        rag_chunk_overlap=120,
        oci_genai_embedding_model="cohere.embed-v4.0",
        oci_enterprise_ai_models=[],
        oci_enterprise_ai_default_model="",
        oci_enterprise_ai_vlm_model="enterprise-vlm",
    )
    pipeline = IngestionPipeline(
        vlm=vlm,
        genai=FakeEmbeddingClient(),
        oracle=cast(Any, oracle),
        settings=settings,
    )

    detail = await pipeline.ingest(
        "doc-segmented",
        _blank_pdf(page_count=5),
        "本文を抽出してください。",
        content_type="application/pdf",
        source_profile=_pdf_source_profile(file_size_bytes=5),
    )

    assert detail.status == FileStatus.INDEXED
    assert vlm.page_counts == [2, 2, 1]
    assert all("元 PDF のページ番号" in prompt for prompt in vlm.prompts)
    assert oracle.saved_extraction is not None
    assert oracle.saved_extraction.raw_text.count("--- page") == 3
    assert [element.page_number for element in oracle.saved_extraction.elements] == [1, 3, 5]


async def test_ingestion_pipeline_retries_truncated_pdf_segment_per_page() -> None:
    """segment が max_output_tokens で途切れた場合は単ページへ分けて再試行する。"""
    oracle = FakeOracle()
    vlm = SegmentCapturingVlm(fail_multi_page_once=True)
    settings = Settings.model_construct(
        rag_pdf_segmentation_enabled=True,
        rag_pdf_max_pages_per_segment=2,
        rag_pdf_max_segments=10,
        rag_chunk_size=800,
        rag_chunk_overlap=120,
        oci_genai_embedding_model="cohere.embed-v4.0",
        oci_enterprise_ai_models=[],
        oci_enterprise_ai_default_model="",
        oci_enterprise_ai_vlm_model="enterprise-vlm",
    )
    pipeline = IngestionPipeline(
        vlm=vlm,
        genai=FakeEmbeddingClient(),
        oracle=cast(Any, oracle),
        settings=settings,
    )

    detail = await pipeline.ingest(
        "doc-page-retry",
        _blank_pdf(page_count=2),
        "本文を抽出してください。",
        content_type="application/pdf",
        source_profile=_pdf_source_profile(file_size_bytes=2),
    )

    assert detail.status == FileStatus.INDEXED
    assert vlm.page_counts == [2, 1, 1]
    assert oracle.saved_extraction is not None
    assert [element.page_number for element in oracle.saved_extraction.elements] == [1, 2]


async def test_ingestion_pipeline_retries_only_failed_checkpoint_segment() -> None:
    """既存 FAILED checkpoint があれば該当 page range だけ再抽出する。"""
    oracle = FakeOracle()
    oracle.segments["doc-failed-retry:p3-4"] = IngestionSegment(
        segment_id="doc-failed-retry:p3-4",
        document_id="doc-failed-retry",
        status="FAILED",
        parser_backend="enterprise_ai",
        parser_profile="enterprise_ai_pdf_layout",
        page_start=3,
        page_end=4,
        attempt_count=1,
        error_code="EnterpriseAiTimeoutError",
    )
    vlm = SegmentCapturingVlm()
    storage = FakeObjectStorage()
    settings = Settings.model_construct(
        rag_pdf_segmentation_enabled=True,
        rag_pdf_max_pages_per_segment=2,
        rag_pdf_max_segments=10,
        rag_chunk_size=800,
        rag_chunk_overlap=120,
        oci_genai_embedding_model="cohere.embed-v4.0",
        oci_enterprise_ai_models=[],
        oci_enterprise_ai_default_model="",
        oci_enterprise_ai_vlm_model="enterprise-vlm",
    )
    pipeline = IngestionPipeline(
        vlm=vlm,
        genai=FakeEmbeddingClient(),
        oracle=cast(Any, oracle),
        object_storage=cast(Any, storage),
        settings=settings,
    )

    detail = await pipeline.ingest(
        "doc-failed-retry",
        _blank_pdf(page_count=5),
        "本文を抽出してください。",
        content_type="application/pdf",
        source_profile=_pdf_source_profile(file_size_bytes=5),
    )

    assert detail.status == FileStatus.INDEXED
    assert vlm.page_counts == [2]
    assert oracle.saved_extraction is not None
    assert [element.page_number for element in oracle.saved_extraction.elements] == [3]
    retried = oracle.segments["doc-failed-retry:p3-4"]
    assert retried.status == "SUCCEEDED"
    assert retried.attempt_count == 2
    assert retried.artifact_path is not None


async def test_ingestion_pipeline_preserves_succeeded_segment_on_later_failure() -> None:
    """後続 segment 失敗時も成功済み checkpoint と artifact を FAILED に戻さない。"""
    oracle = FakeOracle()
    vlm = SegmentFailingVlm(fail_on_call=2)
    storage = FakeObjectStorage()
    settings = Settings.model_construct(
        rag_pdf_segmentation_enabled=True,
        rag_pdf_max_pages_per_segment=2,
        rag_pdf_max_segments=10,
        rag_chunk_size=800,
        rag_chunk_overlap=120,
        oci_genai_embedding_model="cohere.embed-v4.0",
        oci_enterprise_ai_models=[],
        oci_enterprise_ai_default_model="",
        oci_enterprise_ai_vlm_model="enterprise-vlm",
    )
    pipeline = IngestionPipeline(
        vlm=vlm,
        genai=FakeEmbeddingClient(),
        oracle=cast(Any, oracle),
        object_storage=cast(Any, storage),
        settings=settings,
    )

    with pytest.raises(RuntimeError, match="segment failure"):
        await pipeline.ingest(
            "doc-partial-failure",
            _blank_pdf(page_count=5),
            "本文を抽出してください。",
            content_type="application/pdf",
            source_profile=_pdf_source_profile(file_size_bytes=5),
        )

    assert vlm.page_counts == [2, 2]
    assert oracle.statuses == [FileStatus.INGESTING, FileStatus.ERROR]
    succeeded = oracle.segments["doc-partial-failure:p1-2"]
    failed = oracle.segments["doc-partial-failure:p3-4"]
    pending = oracle.segments["doc-partial-failure:p5-5"]
    assert succeeded.status == "SUCCEEDED"
    assert succeeded.artifact_path is not None
    assert failed.status == "FAILED"
    assert pending.status == "FAILED"
    assert len(storage.puts) == 1


async def test_ingestion_pipeline_records_schema_validation_detail_on_failed_segment() -> None:
    """VLM schema 不整合は segment checkpoint に原因が分かる形で保存する。"""
    oracle = FakeOracle()
    settings = Settings.model_construct(
        rag_pdf_segmentation_enabled=True,
        rag_pdf_max_pages_per_segment=1,
        rag_pdf_max_segments=10,
        rag_chunk_size=800,
        rag_chunk_overlap=120,
        oci_genai_embedding_model="cohere.embed-v4.0",
        oci_enterprise_ai_models=[],
        oci_enterprise_ai_default_model="",
        oci_enterprise_ai_vlm_model="enterprise-vlm",
    )
    pipeline = IngestionPipeline(
        vlm=InvalidStructuredExtractionVlm(),
        genai=FakeEmbeddingClient(),
        oracle=cast(Any, oracle),
        settings=settings,
    )

    with pytest.raises(EnterpriseAiValidationError, match="confidence"):
        await pipeline.ingest(
            "doc-validation",
            _blank_pdf(page_count=2),
            "本文を抽出してください。",
            content_type="application/pdf",
            source_profile=_pdf_source_profile(file_size_bytes=2),
        )

    failed = oracle.segments["doc-validation:p1-1"]
    pending = oracle.segments["doc-validation:p2-2"]
    assert failed.status == "FAILED"
    assert failed.error_code == "enterprise_ai_response_validation_error"
    assert failed.error_message is not None
    assert "confidence" in failed.error_message
    assert "失敗項目" in failed.error_message
    assert pending.status == "FAILED"
    assert pending.error_code == "enterprise_ai_response_validation_error"


async def test_ingestion_pipeline_merges_cached_succeeded_segments_on_failed_retry() -> None:
    """failed segment retry は成功済み artifact を読み戻し、ページ順に統合する。"""
    oracle = FakeOracle()
    storage = FakeObjectStorage()
    path_1 = "oci://namespace/bucket/artifacts/extractions/doc-cached/segments/p1-2.json"
    path_5 = "oci://namespace/bucket/artifacts/extractions/doc-cached/segments/p5-5.json"
    oracle.segments = {
        "doc-cached:p1-2": IngestionSegment(
            segment_id="doc-cached:p1-2",
            document_id="doc-cached",
            status="SUCCEEDED",
            parser_backend="enterprise_ai",
            parser_profile="enterprise_ai_pdf_layout",
            page_start=1,
            page_end=2,
            attempt_count=1,
            artifact_path=path_1,
        ),
        "doc-cached:p3-4": IngestionSegment(
            segment_id="doc-cached:p3-4",
            document_id="doc-cached",
            status="FAILED",
            parser_backend="enterprise_ai",
            parser_profile="enterprise_ai_pdf_layout",
            page_start=3,
            page_end=4,
            attempt_count=1,
            error_code="EnterpriseAiTimeoutError",
        ),
        "doc-cached:p5-5": IngestionSegment(
            segment_id="doc-cached:p5-5",
            document_id="doc-cached",
            status="SUCCEEDED",
            parser_backend="enterprise_ai",
            parser_profile="enterprise_ai_pdf_layout",
            page_start=5,
            page_end=5,
            attempt_count=1,
            artifact_path=path_5,
        ),
    }
    _seed_segment(storage, oracle, "doc-cached:p1-2", "cached 1")
    _seed_segment(storage, oracle, "doc-cached:p5-5", "cached 5")
    vlm = SegmentCapturingVlm()
    settings = Settings.model_construct(
        rag_pdf_segmentation_enabled=True,
        rag_pdf_max_pages_per_segment=2,
        rag_pdf_max_segments=10,
        rag_chunk_size=800,
        rag_chunk_overlap=120,
        oci_genai_embedding_model="cohere.embed-v4.0",
        oci_enterprise_ai_models=[],
        oci_enterprise_ai_default_model="",
        oci_enterprise_ai_vlm_model="enterprise-vlm",
    )
    pipeline = IngestionPipeline(
        vlm=vlm,
        genai=FakeEmbeddingClient(),
        oracle=cast(Any, oracle),
        object_storage=cast(Any, storage),
        settings=settings,
    )

    detail = await pipeline.ingest(
        "doc-cached",
        _blank_pdf(page_count=5),
        "本文を抽出してください。",
        content_type="application/pdf",
        source_profile=_pdf_source_profile(file_size_bytes=5),
    )

    assert detail.status == FileStatus.INDEXED
    assert vlm.page_counts == [2]
    assert storage.gets == [path_1, path_5]
    assert oracle.saved_extraction is not None
    assert [element.page_number for element in oracle.saved_extraction.elements] == [1, 3, 5]
    assert "cached 1" in oracle.saved_extraction.raw_text
    assert "cached 5" in oracle.saved_extraction.raw_text
    assert all(segment.status == "SUCCEEDED" for segment in oracle.segments.values())
    assert oracle.segments["doc-cached:p1-2"].artifact_path == path_1
    assert oracle.segments["doc-cached:p5-5"].artifact_path == path_5
    retried_artifact = oracle.segments["doc-cached:p3-4"].artifact_path
    assert retried_artifact is not None
    assert retried_artifact not in {path_1, path_5}
    assert "/segments/" in retried_artifact


async def test_ingestion_pipeline_reuses_all_succeeded_segment_artifacts() -> None:
    """後段失敗後の再実行は成功済み segment artifact だけで復旧する。"""
    oracle = FakeOracle()
    storage = FakeObjectStorage()
    paths = {
        "doc-reuse:p1-2": "oci://namespace/bucket/artifacts/extractions/doc-reuse/segments/p1-2.json",
        "doc-reuse:p3-4": "oci://namespace/bucket/artifacts/extractions/doc-reuse/segments/p3-4.json",
        "doc-reuse:p5-5": "oci://namespace/bucket/artifacts/extractions/doc-reuse/segments/p5-5.json",
    }
    oracle.segments = {
        segment_id: IngestionSegment(
            segment_id=segment_id,
            document_id="doc-reuse",
            status="SUCCEEDED",
            parser_backend="enterprise_ai",
            parser_profile="enterprise_ai_pdf_layout",
            page_start=page_start,
            page_end=page_end,
            attempt_count=1,
            artifact_path=path,
        )
        for segment_id, page_start, page_end, path in [
            ("doc-reuse:p1-2", 1, 2, paths["doc-reuse:p1-2"]),
            ("doc-reuse:p3-4", 3, 4, paths["doc-reuse:p3-4"]),
            ("doc-reuse:p5-5", 5, 5, paths["doc-reuse:p5-5"]),
        ]
    }
    _seed_segment(storage, oracle, "doc-reuse:p1-2", "cached 1")
    _seed_segment(storage, oracle, "doc-reuse:p3-4", "cached 3")
    _seed_segment(storage, oracle, "doc-reuse:p5-5", "cached 5")
    vlm = SegmentCapturingVlm()
    settings = Settings.model_construct(
        rag_pdf_segmentation_enabled=True,
        rag_pdf_max_pages_per_segment=2,
        rag_pdf_max_segments=10,
        rag_chunk_size=800,
        rag_chunk_overlap=120,
        oci_genai_embedding_model="cohere.embed-v4.0",
        oci_enterprise_ai_models=[],
        oci_enterprise_ai_default_model="",
        oci_enterprise_ai_vlm_model="enterprise-vlm",
    )
    pipeline = IngestionPipeline(
        vlm=vlm,
        genai=FakeEmbeddingClient(),
        oracle=cast(Any, oracle),
        object_storage=cast(Any, storage),
        settings=settings,
    )

    detail = await pipeline.ingest(
        "doc-reuse",
        _blank_pdf(page_count=5),
        "本文を抽出してください。",
        content_type="application/pdf",
        source_profile=_pdf_source_profile(file_size_bytes=5),
    )

    assert detail.status == FileStatus.INDEXED
    assert vlm.page_counts == []
    assert storage.gets == list(paths.values())
    assert oracle.saved_extraction is not None
    assert [element.page_number for element in oracle.saved_extraction.elements] == [1, 3, 5]
    assert "cached 1" in oracle.saved_extraction.raw_text
    assert "cached 3" in oracle.saved_extraction.raw_text
    assert "cached 5" in oracle.saved_extraction.raw_text
    assert all(
        oracle.segments[segment_id].artifact_path == artifact_path
        for segment_id, artifact_path in paths.items()
    )
    segment_puts = [key for key, _data, _content_type in storage.puts if "/segments/" in key]
    assert segment_puts == []


async def test_ingestion_pipeline_merges_segment_structure_with_unique_lineage() -> None:
    """segment artifact の pages/tables/assets と element id は文書 scope にそろえる。"""
    oracle = FakeOracle()
    storage = FakeObjectStorage()
    paths = {
        "doc-structure:p1-2": (
            "oci://namespace/bucket/artifacts/extractions/doc-structure/segments/p1-2.json"
        ),
        "doc-structure:p3-4": (
            "oci://namespace/bucket/artifacts/extractions/doc-structure/segments/p3-4.json"
        ),
    }
    oracle.segments = {
        segment_id: IngestionSegment(
            segment_id=segment_id,
            document_id="doc-structure",
            status="SUCCEEDED",
            parser_backend="enterprise_ai",
            parser_profile="enterprise_ai_pdf_layout",
            page_start=page_start,
            page_end=page_end,
            attempt_count=1,
            artifact_path=path,
        )
        for segment_id, page_start, page_end, path in [
            ("doc-structure:p1-2", 1, 2, paths["doc-structure:p1-2"]),
            ("doc-structure:p3-4", 3, 4, paths["doc-structure:p3-4"]),
        ]
    }
    _seed_segment(storage, oracle, "doc-structure:p1-2", "cached 1", include_structure=True)
    _seed_segment(storage, oracle, "doc-structure:p3-4", "cached 3", include_structure=True)
    pipeline = IngestionPipeline(
        vlm=SegmentCapturingVlm(),
        genai=FakeEmbeddingClient(),
        oracle=cast(Any, oracle),
        object_storage=cast(Any, storage),
        settings=Settings.model_construct(
            rag_pdf_segmentation_enabled=True,
            rag_pdf_max_pages_per_segment=2,
            rag_pdf_max_segments=10,
            rag_chunk_size=800,
            rag_chunk_overlap=120,
            oci_genai_embedding_model="cohere.embed-v4.0",
            oci_enterprise_ai_models=[],
            oci_enterprise_ai_default_model="",
            oci_enterprise_ai_vlm_model="enterprise-vlm",
        ),
    )

    await pipeline.ingest(
        "doc-structure",
        _blank_pdf(page_count=4),
        "本文を抽出してください。",
        content_type="application/pdf",
        source_profile=_pdf_source_profile(file_size_bytes=4),
    )

    assert oracle.saved_extraction is not None
    element_ids = [element.element_id for element in oracle.saved_extraction.elements]
    assert element_ids == ["p1-2-el-0000", "p3-4-el-0000"]
    assert [page.page_number for page in oracle.saved_extraction.pages] == [1, 3]
    assert [page.element_ids for page in oracle.saved_extraction.pages] == [
        ["p1-2-el-0000"],
        ["p3-4-el-0000"],
    ]
    assert [table.table_id for table in oracle.saved_extraction.tables] == [
        "p1-2-tbl-1",
        "p3-4-tbl-1",
    ]
    assert [table.element_id for table in oracle.saved_extraction.tables] == [
        "p1-2-el-0000",
        "p3-4-el-0000",
    ]
    assert [table.page_number for table in oracle.saved_extraction.tables] == [1, 3]
    assert [asset.asset_id for asset in oracle.saved_extraction.assets] == [
        "p1-2-asset-1",
        "p3-4-asset-1",
    ]
    assert [asset.page_number for asset in oracle.saved_extraction.assets] == [1, 3]
    assert (
        oracle.saved_extraction.parser_artifacts["segment_merge_warning"]
        == "pdf_segmented_extraction"
    )


async def test_ingestion_pipeline_reextracts_missing_succeeded_segment_artifact() -> None:
    """成功 checkpoint の artifact が消えていたら、その range だけ再抽出する。"""
    oracle = FakeOracle()
    storage = FakeObjectStorage()
    missing_path = "oci://namespace/bucket/artifacts/extractions/doc-cache-miss/segments/p1-2.json"
    cached_path = "oci://namespace/bucket/artifacts/extractions/doc-cache-miss/segments/p3-4.json"
    oracle.segments = {
        "doc-cache-miss:p1-2": IngestionSegment(
            segment_id="doc-cache-miss:p1-2",
            document_id="doc-cache-miss",
            status="SUCCEEDED",
            parser_backend="enterprise_ai",
            parser_profile="enterprise_ai_pdf_layout",
            page_start=1,
            page_end=2,
            attempt_count=1,
            artifact_path=missing_path,
        ),
        "doc-cache-miss:p3-4": IngestionSegment(
            segment_id="doc-cache-miss:p3-4",
            document_id="doc-cache-miss",
            status="SUCCEEDED",
            parser_backend="enterprise_ai",
            parser_profile="enterprise_ai_pdf_layout",
            page_start=3,
            page_end=4,
            attempt_count=1,
            artifact_path=cached_path,
        ),
    }
    _seed_segment(storage, oracle, "doc-cache-miss:p3-4", "cached 3")
    vlm = SegmentCapturingVlm()
    settings = Settings.model_construct(
        rag_pdf_segmentation_enabled=True,
        rag_pdf_max_pages_per_segment=2,
        rag_pdf_max_segments=10,
        rag_chunk_size=800,
        rag_chunk_overlap=120,
        oci_genai_embedding_model="cohere.embed-v4.0",
        oci_enterprise_ai_models=[],
        oci_enterprise_ai_default_model="",
        oci_enterprise_ai_vlm_model="enterprise-vlm",
    )
    pipeline = IngestionPipeline(
        vlm=vlm,
        genai=FakeEmbeddingClient(),
        oracle=cast(Any, oracle),
        object_storage=cast(Any, storage),
        settings=settings,
    )

    detail = await pipeline.ingest(
        "doc-cache-miss",
        _blank_pdf(page_count=4),
        "本文を抽出してください。",
        content_type="application/pdf",
        source_profile=_pdf_source_profile(file_size_bytes=4),
    )

    assert detail.status == FileStatus.INDEXED
    assert vlm.page_counts == [2]
    assert storage.gets == [missing_path, cached_path]
    assert oracle.saved_extraction is not None
    assert [element.page_number for element in oracle.saved_extraction.elements] == [1, 3]
    assert "segment 1 の本文" in oracle.saved_extraction.raw_text
    assert "cached 3" in oracle.saved_extraction.raw_text
    assert "segment_extraction_artifact_cache_miss" in oracle.saved_extraction.warnings
    assert (
        oracle.saved_extraction.parser_artifacts["segment_extraction_artifact_cache_miss_count"]
        == 1
    )
    assert oracle.saved_extraction.quality_report is not None
    assert (
        "segment_extraction_artifact_cache_miss"
        in oracle.saved_extraction.quality_report.quality_warnings
    )
    assert oracle.saved_extraction.quality_report.risk_level == "medium"
    assert oracle.segments["doc-cache-miss:p1-2"].attempt_count == 2
    assert oracle.segments["doc-cache-miss:p1-2"].artifact_path != missing_path
    assert oracle.segments["doc-cache-miss:p3-4"].attempt_count == 1
    assert oracle.segments["doc-cache-miss:p3-4"].artifact_path == cached_path


async def test_ingestion_pipeline_preserves_openxml_office_success_on_parse_failure() -> None:
    """壊れた sheet があっても成功 sheet artifact を残し、文書は未索引で止める。"""
    oracle = FakeOracle()
    storage = FakeObjectStorage()
    pipeline = IngestionPipeline(
        vlm=CapturingVlm(),
        genai=FakeEmbeddingClient(),
        oracle=cast(Any, oracle),
        object_storage=cast(Any, storage),
    )
    source_profile = _office_source_profile(
        file_name="broken.xlsx",
        content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )

    with pytest.raises(IngestionUserError, match="Office の一部 segment"):
        await pipeline.ingest(
            "doc-office-partial",
            _xlsx_bytes(["Sheet A", "Sheet B"], corrupt_sheets={2}),
            "本文を抽出してください。",
            content_type=source_profile.content_type,
            source_profile=source_profile,
        )

    assert oracle.statuses == [FileStatus.INGESTING, FileStatus.ERROR]
    assert oracle.saved_extraction is None
    assert oracle.saved_chunk_count == 0
    sheet1 = oracle.segments["doc-office-partial:sheet1"]
    sheet2 = oracle.segments["doc-office-partial:sheet2"]
    assert sheet1.status == "SUCCEEDED"
    assert sheet1.parser_backend == "local_partition"
    assert sheet1.artifact_path is not None
    assert sheet2.status == "FAILED"
    assert sheet2.parser_backend == "local_partition"
    assert sheet2.error_code == "office_segment_parse_failed"
    segment_puts = [key for key, _data, _content_type in storage.puts if "/segments/" in key]
    assert len(segment_puts) == 1


async def test_ingestion_pipeline_retries_failed_openxml_office_segment_from_cache() -> None:
    """Office failed segment retry は成功済み sheet artifact を再利用する。"""
    oracle = FakeOracle()
    storage = FakeObjectStorage()
    cached_path = "oci://namespace/bucket/artifacts/extractions/doc-office/segments/sheet1.json"
    oracle.segments = {
        "doc-office:sheet1": IngestionSegment(
            segment_id="doc-office:sheet1",
            document_id="doc-office",
            status="SUCCEEDED",
            parser_backend="local_partition",
            parser_profile="local_office_structure",
            page_start=1,
            page_end=1,
            attempt_count=1,
            artifact_path=cached_path,
        ),
        "doc-office:sheet2": IngestionSegment(
            segment_id="doc-office:sheet2",
            document_id="doc-office",
            status="FAILED",
            parser_backend="local_partition",
            parser_profile="local_office_structure",
            page_start=2,
            page_end=2,
            attempt_count=1,
            error_code="office_local_parse_failed",
        ),
    }
    _seed_segment(storage, oracle, "doc-office:sheet1", "cached sheet 1")
    pipeline = IngestionPipeline(
        vlm=CapturingVlm(),
        genai=FakeEmbeddingClient(),
        oracle=cast(Any, oracle),
        object_storage=cast(Any, storage),
    )
    source_profile = _office_source_profile(
        file_name="book.xlsx",
        content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )

    detail = await pipeline.ingest(
        "doc-office",
        _xlsx_bytes(["Sheet A", "Sheet B"]),
        "本文を抽出してください。",
        content_type=source_profile.content_type,
        source_profile=source_profile,
    )

    assert detail.status == FileStatus.INDEXED
    assert storage.gets == [cached_path]
    assert oracle.saved_extraction is not None
    assert "cached sheet 1" in oracle.saved_extraction.raw_text
    assert "Sheet B" in oracle.saved_extraction.raw_text
    segment_puts = [key for key, _data, _content_type in storage.puts if "/segments/" in key]
    assert len(segment_puts) == 1
    assert oracle.segments["doc-office:sheet2"].attempt_count == 2
    assert all(segment.status == "SUCCEEDED" for segment in oracle.segments.values())
    assert oracle.segments["doc-office:sheet1"].artifact_path == cached_path
    retried_artifact = oracle.segments["doc-office:sheet2"].artifact_path
    assert retried_artifact is not None
    assert retried_artifact != cached_path
    assert "/segments/" in retried_artifact


def _blank_pdf(*, page_count: int) -> bytes:
    """テスト用の空白 PDF bytes を作る。"""
    writer = PdfWriter()
    for _ in range(page_count):
        writer.add_blank_page(width=72, height=72)
    output = BytesIO()
    writer.write(output)
    return output.getvalue()


def _pptx_bytes(slides: list[str]) -> bytes:
    """テスト用の最小 OpenXML presentation bytes。"""
    return _zip_bytes(
        {
            f"ppt/slides/slide{index}.xml": f"<sld><txBody><t>{text}</t></txBody></sld>"
            for index, text in enumerate(slides, start=1)
        }
    )


def _xlsx_bytes(sheets: list[str], *, corrupt_sheets: set[int] | None = None) -> bytes:
    """テスト用の最小 OpenXML workbook bytes。"""
    corrupt_sheets = corrupt_sheets or set()
    files = {
        "xl/sharedStrings.xml": (
            "<sst>" + "".join(f"<si><t>{text}</t></si>" for text in sheets) + "</sst>"
        )
    }
    for index, _text in enumerate(sheets, start=1):
        if index in corrupt_sheets:
            files[f"xl/worksheets/sheet{index}.xml"] = "<worksheet><sheetData>"
        else:
            files[f"xl/worksheets/sheet{index}.xml"] = (
                '<worksheet><sheetData><row><c t="s"><v>'
                f"{index - 1}"
                "</v></c></row></sheetData></worksheet>"
            )
    return _zip_bytes(files)


def _zip_bytes(files: dict[str, str]) -> bytes:
    output = BytesIO()
    with zipfile.ZipFile(output, "w") as archive:
        for name, body in files.items():
            archive.writestr(name, body)
    return output.getvalue()


def _segment_extraction(
    text: str,
    *,
    page_number: int,
    include_structure: bool = False,
    document_id: str | None = None,
    segment_id: str | None = None,
    page_start: int | None = None,
    page_end: int | None = None,
) -> StructuredExtraction:
    """segment artifact 用の最小 StructuredExtraction。

    segment_id を渡すと、cache 再利用の identity 照合(``_cached_segment_artifact_matches``)が
    通るよう ``extraction_artifact_*`` を parser_artifacts に埋める(producer と同じキー)。
    """
    payload: dict[str, object] = {
        "raw_text": text,
        "document_type": "社内規程",
        "confidence": 0.9,
        "warnings": [],
        "elements": [
            {
                "kind": "text",
                "text": text,
                "order": 1,
                "element_id": "el-0000",
                "page_number": page_number,
            }
        ],
    }
    if include_structure:
        payload.update(
            {
                "pages": [
                    {
                        "page_number": page_number,
                        "label": f"page {page_number}",
                        "width": 100.0,
                        "height": 200.0,
                        "element_ids": ["el-0000"],
                    }
                ],
                "tables": [
                    {
                        "table_id": "tbl-1",
                        "element_id": "el-0000",
                        "page_number": page_number,
                        "cells": [{"row": 0, "col": 0, "text": text}],
                    }
                ],
                "assets": [
                    {
                        "asset_id": "asset-1",
                        "kind": "figure",
                        "page_number": page_number,
                        "alt_text": text,
                    }
                ],
            }
        )
    artifacts: dict[str, object] = {}
    if include_structure:
        artifacts["source_parser"] = "enterprise_ai_segment"
    if segment_id is not None:
        artifacts.update(
            {
                "extraction_artifact_schema_version": EXTRACTION_ARTIFACT_SCHEMA_VERSION,
                "extraction_artifact_kind": "segment",
                "extraction_artifact_document_id": document_id,
                "extraction_artifact_segment_id": segment_id,
                "extraction_artifact_page_start": page_start,
                "extraction_artifact_page_end": page_end,
            }
        )
    if artifacts:
        payload["parser_artifacts"] = artifacts
    return StructuredExtraction.model_validate(payload)


def _seed_segment(
    storage: "FakeObjectStorage",
    oracle: "FakeOracle",
    segment_id: str,
    text: str,
    *,
    include_structure: bool = False,
) -> None:
    """oracle.segments の identity を引いて、cache 再利用可能な segment artifact を seed する。"""
    seg = oracle.segments[segment_id]
    extraction = _segment_extraction(
        text,
        page_number=seg.page_start or 1,
        include_structure=include_structure,
        document_id=seg.document_id,
        segment_id=seg.segment_id,
        page_start=seg.page_start,
        page_end=seg.page_end,
    )
    assert seg.artifact_path is not None
    storage.seed_extraction(seg.artifact_path, extraction)


def _pdf_source_profile(*, file_size_bytes: int) -> SourceProfile:
    """PDF source profile のテスト fixture。"""
    return SourceProfile(
        original_file_name="layout.pdf",
        sanitized_file_name="layout.pdf",
        extension=".pdf",
        content_type="application/pdf",
        inferred_content_type="application/pdf",
        file_size_bytes=file_size_bytes,
        content_sha256="a" * 64,
        modality=SourceModality.PDF,
        parser_profile="enterprise_ai_pdf_layout",
    )


def _office_source_profile(*, file_name: str, content_type: str) -> SourceProfile:
    """OpenXML Office source profile のテスト fixture。"""
    return SourceProfile(
        original_file_name=file_name,
        sanitized_file_name=file_name,
        extension=f".{file_name.rsplit('.', 1)[-1]}",
        content_type=content_type,
        inferred_content_type=content_type,
        file_size_bytes=128,
        content_sha256="b" * 64,
        modality=SourceModality.OFFICE,
        parser_profile="local_office_structure",
        parser_backend="local_partition",
    )
