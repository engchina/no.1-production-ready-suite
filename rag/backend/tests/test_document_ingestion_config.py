"""文書の取込設定スナップショット / ドリフト endpoint のテスト。"""

from datetime import UTC, datetime

import pytest

from app.api.routes import documents as documents_route
from app.main import app
from app.rag.kb_adapter_config import KnowledgeBaseAdapterConfig
from app.schemas.document import DocumentChunkView, DocumentDetail, FileStatus
from app.schemas.knowledge_base import KnowledgeBaseDetail, KnowledgeBaseStatus
from tests.support import AsgiTestClient

client = AsgiTestClient(app)


class FakeIngestionConfigOracle:
    """ingestion-config endpoint 用の最小 fake。"""

    def __init__(self) -> None:
        self.documents: dict[str, DocumentDetail] = {}
        self.chunks: dict[str, list[DocumentChunkView]] = {}
        self.owning: dict[str, KnowledgeBaseDetail] = {}

    def add_document(
        self,
        document_id: str,
        *,
        status: FileStatus,
        chunk_strategy: str | None = None,
        source_parser: str | None = None,
    ) -> None:
        self.documents[document_id] = DocumentDetail(
            id=document_id,
            file_name=f"{document_id}.pdf",
            status=status,
            object_storage_path=f"staging/{document_id}.pdf",
            uploaded_at=datetime(2026, 1, 1, tzinfo=UTC),
            indexed_at=datetime(2026, 1, 2, tzinfo=UTC) if status == FileStatus.INDEXED else None,
        )
        if chunk_strategy is not None:
            self.chunks[document_id] = [
                DocumentChunkView(
                    document_id=document_id,
                    chunk_id=f"{document_id}-c0",
                    chunk_index=0,
                    text="本文",
                    source_parser=source_parser,
                    metadata={"chunk_strategy": chunk_strategy},
                )
            ]

    def set_owning(self, document_id: str, config: KnowledgeBaseAdapterConfig) -> None:
        self.owning[document_id] = KnowledgeBaseDetail(
            id=f"kb-{document_id}",
            name=f"KB {document_id}",
            status=KnowledgeBaseStatus.ACTIVE,
            adapter_config=config,
            created_at=datetime(2026, 1, 1, tzinfo=UTC),
            updated_at=datetime(2026, 1, 1, tzinfo=UTC),
        )

    async def get_document(self, document_id: str) -> DocumentDetail | None:
        return self.documents.get(document_id)

    async def get_owning_knowledge_base(self, document_id: str) -> KnowledgeBaseDetail | None:
        return self.owning.get(document_id)

    async def list_document_chunks(self, document_id: str) -> list[DocumentChunkView]:
        return list(self.chunks.get(document_id, []))


@pytest.fixture
def fake_oracle(monkeypatch: pytest.MonkeyPatch) -> FakeIngestionConfigOracle:
    fake = FakeIngestionConfigOracle()
    monkeypatch.setattr(documents_route, "OracleClient", lambda: fake)
    return fake


def _config(**ingestion: object) -> KnowledgeBaseAdapterConfig:
    return KnowledgeBaseAdapterConfig.model_validate({"ingestion": ingestion})


def test_ingestion_config_without_owning_kb_uses_global(
    fake_oracle: FakeIngestionConfigOracle,
) -> None:
    """所属 KB が無ければグローバル既定が effective として返り、ドリフトは立たない。"""
    fake_oracle.add_document("doc-1", status=FileStatus.INDEXED, chunk_strategy="structure_aware")

    resp = client.get("/api/documents/doc-1/ingestion-config")

    assert resp.status_code == 200
    data = resp.json()["data"]
    assert data["owning_knowledge_base"] is None
    assert data["effective_chunking_strategy"] == "structure_aware"
    assert data["observed_chunking_strategy"] == "structure_aware"
    assert data["config_drift"] is False


def test_ingestion_config_reports_no_drift_when_matching(
    fake_oracle: FakeIngestionConfigOracle,
) -> None:
    """owning KB の戦略と取込済みチャンクが一致すればドリフトしない。"""
    fake_oracle.add_document(
        "doc-1", status=FileStatus.INDEXED, chunk_strategy="page_level", source_parser="docling"
    )
    fake_oracle.set_owning(
        "doc-1",
        _config(
            chunking_strategy="page_level",
            parser_adapter_backend="docling",
        ),
    )

    data = client.get("/api/documents/doc-1/ingestion-config").json()["data"]

    assert data["owning_knowledge_base"]["id"] == "kb-doc-1"
    assert data["effective_chunking_strategy"] == "page_level"
    assert data["observed_chunking_strategy"] == "page_level"
    assert data["observed_parser_backend"] == "docling"
    assert data["config_drift"] is False


@pytest.mark.parametrize(
    ("backend", "profile"),
    [
        ("docling", "docling_adapter"),
        ("marker", "marker_adapter"),
        ("unstructured", "unstructured_adapter"),
        ("unlimited_ocr", "unlimited_ocr_adapter"),
        ("mineru", "mineru_adapter"),
        ("dots_ocr", "dots_ocr_adapter"),
        ("glm_ocr", "glm_ocr_adapter"),
    ],
)
def test_ingestion_config_treats_external_adapter_profiles_as_matching(
    fake_oracle: FakeIngestionConfigOracle,
    backend: str,
    profile: str,
) -> None:
    """外部 parser の runtime profile 名は同じ backend として扱う。"""
    fake_oracle.add_document(
        "doc-1",
        status=FileStatus.INDEXED,
        chunk_strategy="structure_aware",
        source_parser=profile,
    )
    fake_oracle.set_owning(
        "doc-1",
        _config(
            chunking_strategy="structure_aware",
            parser_adapter_backend=backend,
        ),
    )

    data = client.get("/api/documents/doc-1/ingestion-config").json()["data"]

    assert data["observed_parser_backend"] == profile
    assert data["parser_drift"] is False
    assert data["config_drift"] is False


def test_ingestion_config_detects_drift(
    fake_oracle: FakeIngestionConfigOracle,
) -> None:
    """owning KB の現行戦略が取込済みチャンクと異なるとドリフトを立てる。"""
    fake_oracle.add_document("doc-1", status=FileStatus.INDEXED, chunk_strategy="structure_aware")
    fake_oracle.set_owning("doc-1", _config(chunking_strategy="page_level"))

    data = client.get("/api/documents/doc-1/ingestion-config").json()["data"]

    assert data["effective_chunking_strategy"] == "page_level"
    assert data["observed_chunking_strategy"] == "structure_aware"
    assert data["chunking_drift"] is True
    assert data["parser_drift"] is False
    assert data["config_drift"] is True


def test_ingestion_config_detects_parser_drift(
    fake_oracle: FakeIngestionConfigOracle,
) -> None:
    """owning KB の parser が変わったら、chunking が同じでも再取込対象として扱う。"""
    fake_oracle.add_document(
        "doc-1",
        status=FileStatus.INDEXED,
        chunk_strategy="structure_aware",
        source_parser="enterprise_ai_pdf_layout",
    )
    fake_oracle.set_owning(
        "doc-1",
        _config(
            chunking_strategy="structure_aware",
            parser_adapter_backend="mineru",
        ),
    )

    data = client.get("/api/documents/doc-1/ingestion-config").json()["data"]

    assert data["effective_parser_adapter_backend"] == "mineru"
    assert data["observed_parser_backend"] == "enterprise_ai_pdf_layout"
    assert data["chunking_drift"] is False
    assert data["parser_drift"] is True
    assert data["config_drift"] is True


def test_ingestion_config_no_drift_when_not_indexed(
    fake_oracle: FakeIngestionConfigOracle,
) -> None:
    """未取込(UPLOADED)では観測値が無く、ドリフト判定もしない。"""
    fake_oracle.add_document("doc-1", status=FileStatus.UPLOADED)
    fake_oracle.set_owning("doc-1", _config(chunking_strategy="page_level"))

    data = client.get("/api/documents/doc-1/ingestion-config").json()["data"]

    assert data["is_indexed"] is False
    assert data["observed_chunking_strategy"] is None
    assert data["config_drift"] is False
    # effective は「これから取り込むなら」の値として KB 設定を反映する。
    assert data["effective_chunking_strategy"] == "page_level"


def test_ingestion_config_falls_back_on_inconsistent_kb_config(
    fake_oracle: FakeIngestionConfigOracle,
) -> None:
    """KB 設定がグローバルと矛盾する場合はグローバルへ縮退し、500 にならない。"""
    fake_oracle.add_document("doc-1", status=FileStatus.INDEXED, chunk_strategy="structure_aware")
    fake_oracle.set_owning("doc-1", _config(chunk_size=200, chunk_overlap=500))

    resp = client.get("/api/documents/doc-1/ingestion-config")

    assert resp.status_code == 200
    data = resp.json()["data"]
    # 矛盾設定は無視され、グローバル既定の戦略になる。
    assert data["effective_chunking_strategy"] == "structure_aware"
    assert data["config_drift"] is False


def test_ingestion_config_returns_404_for_missing_document(
    fake_oracle: FakeIngestionConfigOracle,
) -> None:
    resp = client.get("/api/documents/missing/ingestion-config")
    assert resp.status_code == 404
