"""source profile と取込 pipeline の抽出 strategy 接続テスト。"""

import json
from datetime import UTC, datetime
from io import BytesIO
from typing import Any, cast

import pytest
from pypdf import PdfReader
from rag_parser_core.preprocess import ConvertOutcome
from rag_parser_core.result import ParserRegistryResult

from app.clients.oci_enterprise_ai import (
    EnterpriseAiIncompleteResponseError,
    OciEnterpriseAiClient,
)
from app.clients.oci_genai import OciGenAiClient
from app.config import Settings
from app.rag import ingestion as ingestion_module
from app.rag.graph_index import GraphIndex
from app.rag.ingestion import (
    IngestionCancelledError,
    IngestionPipeline,
    IngestionUserError,
)
from app.rag.ingestion_quality import build_ingestion_quality_report
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
        self.error_messages: list[str | None] = []
        self.segments: dict[str, IngestionSegment] = {}

    async def update_document_status(
        self,
        document_id: str,
        status: FileStatus,
        error_message: str | None = None,
    ) -> DocumentDetail:
        self.statuses.append(status)
        self.error_messages.append(error_message)
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
        chunk_set_id: str | None = None,
    ) -> None:
        _ = document_id, vectors, chunk_set_id
        self.atomic_index_save_count += 1
        self.saved_extraction = extraction
        self.saved_chunk_count = len(chunks)

    async def upsert_document_extraction_artifact(self, **kwargs: object) -> None:
        _ = kwargs

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


def _canned_extraction() -> StructuredExtraction:
    """parser マイクロサービスが返す抽出の代替(pipeline テスト用)。"""
    return StructuredExtraction.model_validate(
        {
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
    )


@pytest.fixture(autouse=True)
def _stub_parser_service(monkeypatch: pytest.MonkeyPatch) -> None:
    """in-process 解析は撤去済み。pipeline テストは parser 委譲を canned 抽出で代替する。

    `IngestionPipeline._partition_source` は選択 backend を `ParserServiceClient.runner` へ
    HTTP 委譲する。本 fixture はその runner を決定論スタブへ差し替え、実サービス未起動でも
    pipeline(chunk/embedding/index/graph)を検証できるようにする。
    """

    def _runner(
        self: object,
        backend: str,
        source_bytes: bytes,
        source_profile: SourceProfile | None,
        content_type: str,
        *,
        fail_fast: bool = False,
    ) -> ParserRegistryResult:
        _ = self, source_bytes, source_profile, content_type, fail_fast
        return ParserRegistryResult(
            extraction=_canned_extraction(),
            parser_backend=backend,
            parser_version=f"{backend}_test",
            template="text_blocks",
        )

    monkeypatch.setattr(
        "app.clients.parser_service.ParserServiceClient.runner",
        _runner,
        raising=True,
    )


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
        assets=[ExtractionAsset(asset_id="fig-1", kind="image", page_number=4, alt_text="構成図")],
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


async def test_ingestion_pipeline_caches_extraction_artifact_and_segment_checkpoint() -> None:
    """抽出 artifact を保存し、segment checkpoint に artifact path を残す。"""
    oracle = FakeOracle()
    storage = FakeObjectStorage()
    pipeline = IngestionPipeline(
        vlm=CapturingVlm(),
        genai=FakeEmbeddingClient(),
        oracle=cast(Any, oracle),
        object_storage=cast(Any, storage),
        settings=Settings(
            rag_parser_adapter_backend="local",
            rag_review_gate_enabled=False,
            rag_auto_parse_after_preprocess_enabled=True,
        ),
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
    assert oracle.saved_extraction.parser_artifacts["extraction_artifact_schema_version"] == 1
    assert oracle.saved_extraction.parser_artifacts["extraction_artifact_kind"] == "full"
    assert oracle.segments
    assert all(segment.status == "SUCCEEDED" for segment in oracle.segments.values())
    assert all(segment.artifact_path == artifact_path for segment in oracle.segments.values())


async def test_ingestion_pipeline_stops_at_preprocessed_when_parse_gate_off() -> None:
    """ファイル準備ゲート off では PREPROCESSED で停止し、抽出/索引へ進めない。"""
    oracle = FakeOracle()
    storage = FakeObjectStorage()
    pipeline = IngestionPipeline(
        vlm=CapturingVlm(),
        genai=FakeEmbeddingClient(),
        oracle=cast(Any, oracle),
        object_storage=cast(Any, storage),
        settings=Settings(
            rag_parser_adapter_backend="local",
            rag_auto_parse_after_preprocess_enabled=False,
        ),
    )

    detail = await pipeline.ingest(
        "doc-gate",
        b"pdfdata",
        "本文を抽出してください。",
        content_type="application/pdf",
        source_profile=_pdf_source_profile(file_size_bytes=7),
    )

    assert detail.status == FileStatus.PREPROCESSED
    assert oracle.statuses[-1] == FileStatus.PREPROCESSED
    # parse 以降は走らない: 抽出も chunk も保存されない。
    assert oracle.saved_extraction is None
    assert oracle.saved_chunk_count == 0


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
            rag_parser_adapter_backend="local",
            rag_extraction_artifact_prefix=("../unsafe//prefix with space\\nested/./../final"),
            rag_auto_parse_after_preprocess_enabled=True,
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
        settings=Settings(
            rag_parser_adapter_backend="local",
            rag_review_gate_enabled=False,
            rag_auto_parse_after_preprocess_enabled=True,
        ),
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


class _StubConvertingPreprocess:
    """converting プロファイルで必ず converted=True を返す前処理スタブ(サービス不要)。"""

    def convert(
        self,
        source_bytes: bytes,
        *,
        content_type: str,
        source_profile: Any = None,
        profile: Any = None,
    ) -> ConvertOutcome:
        _ = source_bytes, content_type, source_profile, profile
        return ConvertOutcome(
            converted=True,
            converter_name="stub",
            converter_version="v1",
            derived_bytes=b"%PDF-1.7 derived",
            derived_content_type="application/pdf",
            page_map={"1": 1},
        )


async def test_preprocess_canonical_persist_failure_surfaces_as_error() -> None:
    """変換成功でも処理後ファイル保存に失敗したら silent な PREPROCESSED を作らず ERROR にする。"""
    oracle = FakeOracle()
    storage = FailingObjectStorage()
    pipeline = IngestionPipeline(
        vlm=CapturingVlm(),
        genai=FakeEmbeddingClient(),
        oracle=cast(Any, oracle),
        object_storage=cast(Any, storage),
        settings=Settings(
            rag_parser_adapter_backend="local",
            rag_preprocess_enabled=True,
            rag_preprocess_profile="pdf_to_page_images",
        ),
    )
    pipeline._preprocess = cast(Any, _StubConvertingPreprocess())

    with pytest.raises(IngestionUserError, match="処理後ファイルを保存できませんでした"):
        await pipeline.ingest(
            "doc-canonical-fail",
            b"pdfdata",
            "本文を抽出してください。",
            content_type="application/pdf",
            source_profile=_pdf_source_profile(file_size_bytes=7),
        )

    assert oracle.statuses[-1] == FileStatus.ERROR
    assert oracle.error_messages[-1] is not None
    assert "処理後ファイルを保存できませんでした" in oracle.error_messages[-1]
    # 処理後ファイルが無い壊れた PREPROCESSED 成功状態を作らない。
    assert FileStatus.PREPROCESSED not in oracle.statuses


async def test_canonical_artifact_persists_even_when_extraction_cache_disabled() -> None:
    """canonical(処理後ファイル)保存は抽出 JSON キャッシュフラグでは無効化されない(必須)。"""
    oracle = FakeOracle()
    storage = FakeObjectStorage()
    pipeline = IngestionPipeline(
        vlm=CapturingVlm(),
        genai=FakeEmbeddingClient(),
        oracle=cast(Any, oracle),
        object_storage=cast(Any, storage),
        settings=Settings(
            rag_parser_adapter_backend="local",
            rag_extraction_artifact_cache_enabled=False,
        ),
    )

    path = await pipeline._cache_canonical_artifact(
        document_id="doc-canonical",
        trace_id="trace-1",
        derived_bytes=b"%PDF-1.7 derived",
        content_type="application/pdf",
    )

    assert path is not None
    assert storage.puts
    assert "/canonical.pdf" in storage.puts[0][0]


class _NonPersistingOracle:
    """save_preprocess_artifact を受けるが永続化しない(読み戻すと artifact なし)スタブ。"""

    async def save_preprocess_artifact(self, document_id: str, artifact: Any) -> None:
        _ = document_id, artifact

    async def get_document(self, document_id: str) -> DocumentDetail:
        return DocumentDetail(
            id=document_id,
            file_name="x.pdf",
            status=FileStatus.PREPROCESSING,
            content_type="application/pdf",
            file_size_bytes=1,
            content_sha256="a" * 64,
            uploaded_at=datetime.now(UTC),
            indexed_at=None,
            preprocess_artifact=None,
        )


async def test_preprocess_artifact_save_not_persisted_surfaces_as_error() -> None:
    """ファイル準備 artifact の保存が永続化しなければ silent にせず ERROR 用例外を出す。"""
    from rag_parser_core.preprocess import SourceDerivation

    pipeline = IngestionPipeline(
        vlm=CapturingVlm(),
        genai=FakeEmbeddingClient(),
        oracle=cast(Any, _NonPersistingOracle()),
        settings=Settings(rag_parser_adapter_backend="local"),
    )
    derivation = SourceDerivation(
        derivation_id="d1",
        preprocess_profile="pdf_to_page_images",
        converted=True,
        derived_object_path="local://artifacts/canonical/doc-x/t/canonical.pdf",
        derived_content_type="application/pdf",
        source_content_type="application/pdf",
        source_sha256="a" * 64,
        derived_sha256="b" * 64,
    )
    with pytest.raises(IngestionUserError, match="処理後ファイル情報を保存できませんでした"):
        await pipeline._save_preprocess_artifact(
            document_id="doc-x",
            source_derivation=derivation,
            original_file_name="x.pdf",
            original_object_storage_path="local://uploaded/x.pdf",
            fallback_content_type="application/pdf",
        )


async def test_ingestion_pipeline_reuses_full_extraction_artifact_after_embedding_failure() -> None:
    """VLM 成功後の後段失敗は full extraction artifact から復旧し VLM を再実行しない。"""
    oracle = FakeOracle()
    storage = FakeObjectStorage()
    first_pipeline = IngestionPipeline(
        vlm=CapturingVlm(),
        genai=FailingEmbeddingClient(),
        oracle=cast(Any, oracle),
        object_storage=cast(Any, storage),
        settings=Settings(
            rag_parser_adapter_backend="local",
            rag_review_gate_enabled=False,
            rag_auto_parse_after_preprocess_enabled=True,
        ),
    )

    with pytest.raises(RuntimeError, match="embedding failure"):
        await first_pipeline.ingest(
            "doc-full-artifact-retry",
            b"pdfdata",
            "本文を抽出してください。",
            content_type="application/pdf",
            source_profile=_pdf_source_profile(file_size_bytes=7),
        )

    assert oracle.statuses == [
        FileStatus.PREPROCESSING,
        FileStatus.INGESTING,
        FileStatus.ERROR,
    ]
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
        settings=Settings(
            rag_parser_adapter_backend="local",
            rag_review_gate_enabled=False,
            rag_auto_parse_after_preprocess_enabled=True,
        ),
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


def test_ingestion_selected_external_parser_fallback_stops_ingestion() -> None:
    """明示選択 parser が fallback 結果なら Enterprise AI へ進めず止める。"""
    pipeline = object.__new__(IngestionPipeline)
    pipeline._settings = Settings(
        rag_parser_adapter_backend="mineru",
        rag_parser_mineru_enabled=True,
    )
    result = ParserRegistryResult(
        extraction=None,
        parser_backend="mineru",
        parser_version="service_unavailable",
        fallback_used=True,
        warnings=("mineru_adapter_failed",),
    )

    with pytest.raises(IngestionUserError, match="MinerU"):
        pipeline._raise_if_selected_parser_was_not_used(result)


async def test_ingestion_pipeline_cancel_after_extraction_does_not_save_index() -> None:
    """cancel 済み job は extraction/chunk/index 保存と INDEXED 遷移を止める。"""
    oracle = FakeOracle()
    pipeline = IngestionPipeline(
        vlm=CapturingVlm(),
        genai=FakeEmbeddingClient(),
        oracle=cast(Any, oracle),
        settings=Settings(
            rag_parser_adapter_backend="local",
            rag_review_gate_enabled=False,
            rag_auto_parse_after_preprocess_enabled=True,
        ),
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

    assert oracle.statuses == [FileStatus.PREPROCESSING, FileStatus.INGESTING]
    assert oracle.atomic_index_save_count == 0
    assert oracle.saved_extraction is None
    assert oracle.saved_chunk_count == 0


async def test_ingestion_pipeline_writes_graph_index_when_enabled() -> None:
    """RAG_GRAPH_ENABLED 時は取込結果から GraphRAG-lite index を保存する。"""
    oracle = FakeOracle()
    settings = Settings.model_construct(
        rag_parser_adapter_backend="local",
        rag_review_gate_enabled=False,
        rag_auto_parse_after_preprocess_enabled=True,
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


def test_checkpoint_parser_profile_prefers_external_adapter_lineage() -> None:
    """外部 parser 成功時の checkpoint 表示は Enterprise AI profile へ戻さない。"""
    extraction = StructuredExtraction.model_validate(
        {
            "raw_text": "Unstructured が抽出した本文",
            "document_type": "reference",
            "confidence": 0.9,
            "parser_artifacts": {
                "source_parser": "unstructured_adapter",
                "external_adapter": "unstructured",
            },
        }
    )
    result = ParserRegistryResult(
        extraction=extraction,
        parser_backend="unstructured",
        parser_version="unstructured:1.0.0",
    )

    assert (
        ingestion_module._checkpoint_parser_profile(
            result,
            fallback="enterprise_ai_pdf_layout",
        )
        == "unstructured_adapter"
    )
