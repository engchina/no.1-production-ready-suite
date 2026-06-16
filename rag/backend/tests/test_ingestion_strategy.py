"""source profile と取込 pipeline の抽出 strategy 接続テスト。"""

from datetime import UTC, datetime
from io import BytesIO
from typing import Any, cast

from pypdf import PdfReader, PdfWriter

from app.clients.oci_enterprise_ai import EnterpriseAiIncompleteResponseError, OciEnterpriseAiClient
from app.clients.oci_genai import OciGenAiClient
from app.config import Settings
from app.rag.graph_index import GraphIndex
from app.rag.ingestion import IngestionPipeline
from app.schemas.document import DocumentDetail, FileStatus, SourceModality, SourceProfile
from app.schemas.extraction import StructuredExtraction
from app.schemas.knowledge_base import KnowledgeBaseRef


class FakeOracle:
    """IngestionPipeline に必要な OracleClient subset。"""

    def __init__(self) -> None:
        self.saved_extraction: StructuredExtraction | None = None
        self.saved_chunk_count = 0
        self.saved_graph_index: GraphIndex | None = None
        self.graph_document_id: str | None = None
        self.statuses: list[FileStatus] = []

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


async def test_ingestion_pipeline_writes_graph_index_when_enabled() -> None:
    """RAG_GRAPH_ENABLED 時は取込結果から GraphRAG-lite index を保存する。"""
    oracle = FakeOracle()
    settings = Settings.model_construct(
        rag_graph_enabled=True,
        rag_chunk_size=800,
        rag_chunk_overlap=120,
        rag_max_chunks_per_document=512,
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
        rag_max_chunks_per_document=512,
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
        rag_max_chunks_per_document=512,
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


def _blank_pdf(*, page_count: int) -> bytes:
    """テスト用の空白 PDF bytes を作る。"""
    writer = PdfWriter()
    for _ in range(page_count):
        writer.add_blank_page(width=72, height=72)
    output = BytesIO()
    writer.write(output)
    return output.getvalue()


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
