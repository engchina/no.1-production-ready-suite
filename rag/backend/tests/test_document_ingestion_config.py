"""文書の取込設定スナップショット / ドリフト endpoint のテスト。

3 層モデル: effective レシピは global 既定(「検索・回答設定」)から解決する。
owning KB overlay や per-KB build config グルーピングは持たない(文書単位の単一レシピ)。
ドリフトは「取込時に刻まれた観測値 vs 現在の global 既定」で判定する。
"""

from collections.abc import Callable
from datetime import UTC, datetime

import pytest

from app.api.routes import documents as documents_route
from app.config import get_settings
from app.main import app
from app.schemas.document import (
    DocumentChunkView,
    DocumentDetail,
    DocumentProcessingConfig,
    FileStatus,
)
from tests.support import AsgiTestClient

client = AsgiTestClient(app)


class FakeIngestionConfigOracle:
    """ingestion-config endpoint 用の最小 fake(文書 + 取込済み chunk のみ)。"""

    def __init__(self) -> None:
        self.documents: dict[str, DocumentDetail] = {}
        self.chunks: dict[str, list[DocumentChunkView]] = {}
        self.processing_configs: dict[str, DocumentProcessingConfig] = {}
        self.active_job_statuses: set[str] = set()
        self.serving_recipe_snapshot: dict[str, object] | None = None

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
            content_sha256="a" * 64,
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

    async def get_document(self, document_id: str) -> DocumentDetail | None:
        return self.documents.get(document_id)

    async def list_document_chunks(self, document_id: str) -> list[DocumentChunkView]:
        return list(self.chunks.get(document_id, []))

    async def get_document_processing_config(self, document_id: str) -> DocumentProcessingConfig:
        return self.processing_configs.get(document_id, DocumentProcessingConfig())

    async def update_document_processing_config(
        self, document_id: str, config: DocumentProcessingConfig
    ) -> DocumentProcessingConfig:
        self.processing_configs[document_id] = config
        return config

    async def get_document_serving_chunk_set_id(self, document_id: str) -> str | None:
        _ = document_id
        return "cs-serving" if self.serving_recipe_snapshot is not None else None

    async def get_chunk_set(self, chunk_set_id: str) -> dict[str, object] | None:
        if chunk_set_id != "cs-serving" or self.serving_recipe_snapshot is None:
            return None
        return {"chunk_set_id": chunk_set_id, "recipe_subset": self.serving_recipe_snapshot}

    async def list_document_ingestion_jobs(
        self, document_id: str, **kwargs: object
    ) -> list[object]:
        _ = document_id
        status = kwargs.get("status")
        return [object()] if getattr(status, "value", status) in self.active_job_statuses else []


@pytest.fixture
def fake_oracle(monkeypatch: pytest.MonkeyPatch) -> FakeIngestionConfigOracle:
    fake = FakeIngestionConfigOracle()
    monkeypatch.setattr(documents_route, "OracleClient", lambda: fake)
    return fake


@pytest.fixture
def set_global_recipe(
    monkeypatch: pytest.MonkeyPatch,
) -> Callable[..., None]:
    """global 既定レシピ(get_settings シングルトン)を一時的に上書きする。"""
    settings = get_settings()

    def _apply(*, chunking_strategy: str | None = None, parser: str | None = None) -> None:
        if chunking_strategy is not None:
            monkeypatch.setattr(settings, "rag_chunking_strategy", chunking_strategy)
        if parser is not None:
            monkeypatch.setattr(settings, "rag_parser_adapter_backend", parser)

    return _apply


def test_ingestion_config_uses_global_recipe(
    fake_oracle: FakeIngestionConfigOracle,
) -> None:
    """effective は global 既定。取込済みが既定と一致すればドリフトしない。"""
    fake_oracle.add_document("doc-1", status=FileStatus.INDEXED, chunk_strategy="structure_aware")

    resp = client.get("/api/documents/doc-1/ingestion-config")

    assert resp.status_code == 200
    data = resp.json()["data"]
    assert "owning_knowledge_base" not in data
    assert "build_configurations" not in data
    assert data["effective_chunking_strategy"] == "structure_aware"
    assert data["observed_chunking_strategy"] == "structure_aware"
    assert data["config_drift"] is False


def test_ingestion_config_reports_no_drift_when_matching(
    fake_oracle: FakeIngestionConfigOracle,
    set_global_recipe: Callable[..., None],
) -> None:
    """global 既定と取込済みチャンクが一致すればドリフトしない。"""
    set_global_recipe(chunking_strategy="page_level", parser="docling")
    fake_oracle.add_document(
        "doc-1", status=FileStatus.INDEXED, chunk_strategy="page_level", source_parser="docling"
    )

    data = client.get("/api/documents/doc-1/ingestion-config").json()["data"]

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
    set_global_recipe: Callable[..., None],
    backend: str,
    profile: str,
) -> None:
    """外部 parser の runtime profile 名は同じ backend として扱う(ドリフトしない)。"""
    set_global_recipe(parser=backend)
    fake_oracle.add_document(
        "doc-1",
        status=FileStatus.INDEXED,
        chunk_strategy="structure_aware",
        source_parser=profile,
    )

    data = client.get("/api/documents/doc-1/ingestion-config").json()["data"]

    assert data["observed_parser_backend"] == profile
    assert data["parser_drift"] is False
    assert data["config_drift"] is False


def test_ingestion_config_detects_drift(
    fake_oracle: FakeIngestionConfigOracle,
) -> None:
    """global 既定の戦略が取込済みチャンクと異なるとドリフトを立てる。"""
    # global 既定 = structure_aware。観測値を page_level にしてずらす。
    fake_oracle.add_document("doc-1", status=FileStatus.INDEXED, chunk_strategy="page_level")

    data = client.get("/api/documents/doc-1/ingestion-config").json()["data"]

    assert data["effective_chunking_strategy"] == "structure_aware"
    assert data["observed_chunking_strategy"] == "page_level"
    assert data["chunking_drift"] is True
    assert data["parser_drift"] is False
    assert data["config_drift"] is True


def test_ingestion_config_detects_parser_drift(
    fake_oracle: FakeIngestionConfigOracle,
    set_global_recipe: Callable[..., None],
) -> None:
    """global の parser が変わったら、chunking が同じでも再取込対象として扱う。"""
    set_global_recipe(parser="mineru")
    fake_oracle.add_document(
        "doc-1",
        status=FileStatus.INDEXED,
        chunk_strategy="structure_aware",
        source_parser="enterprise_ai_pdf_layout",
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
    """未取込(UPLOADED)では観測値が無く、ドリフト判定もしない。effective は global 既定。"""
    fake_oracle.add_document("doc-1", status=FileStatus.UPLOADED)

    data = client.get("/api/documents/doc-1/ingestion-config").json()["data"]

    assert data["is_indexed"] is False
    assert data["observed_chunking_strategy"] is None
    assert data["config_drift"] is False
    assert data["effective_chunking_strategy"] == "structure_aware"


def test_ingestion_config_returns_404_for_missing_document(
    fake_oracle: FakeIngestionConfigOracle,
) -> None:
    resp = client.get("/api/documents/missing/ingestion-config")
    assert resp.status_code == 404


def test_ingestion_config_update_persists_document_override(
    fake_oracle: FakeIngestionConfigOracle,
) -> None:
    fake_oracle.add_document("doc-1", status=FileStatus.UPLOADED)

    resp = client.put(
        "/api/documents/doc-1/ingestion-config",
        json={"parser_adapter_backend": "mineru", "chunking_strategy": "page_level"},
    )

    assert resp.status_code == 200
    data = resp.json()["data"]
    assert data["processing_config"]["parser_adapter_backend"] == "mineru"
    assert data["effective_processing_config"]["chunking_strategy"] == "page_level"
    assert data["effective_processing_config"]["parser_mineru_enabled"] is True
    assert data["effective_parser_adapter_backend"] == "mineru"


def test_ingestion_config_update_empty_restores_global_inheritance(
    fake_oracle: FakeIngestionConfigOracle,
) -> None:
    fake_oracle.add_document("doc-1", status=FileStatus.UPLOADED)
    fake_oracle.processing_configs["doc-1"] = DocumentProcessingConfig(
        parser_adapter_backend="mineru"
    )

    resp = client.put("/api/documents/doc-1/ingestion-config", json={})

    assert resp.status_code == 200
    assert all(value is None for value in resp.json()["data"]["processing_config"].values())


def test_ingestion_config_update_rejects_intermediate_status(
    fake_oracle: FakeIngestionConfigOracle,
) -> None:
    fake_oracle.add_document("doc-1", status=FileStatus.REVIEW)

    resp = client.put(
        "/api/documents/doc-1/ingestion-config",
        json={"chunking_strategy": "page_level"},
    )

    assert resp.status_code == 409


def test_ingestion_config_update_rejects_invalid_chunk_parameters(
    fake_oracle: FakeIngestionConfigOracle,
) -> None:
    fake_oracle.add_document("doc-1", status=FileStatus.UPLOADED)

    resp = client.put(
        "/api/documents/doc-1/ingestion-config",
        json={"chunk_size": 300, "chunk_overlap": 300},
    )

    assert resp.status_code == 422


def test_ingestion_config_update_rejects_active_job(
    fake_oracle: FakeIngestionConfigOracle,
) -> None:
    fake_oracle.add_document("doc-1", status=FileStatus.UPLOADED)
    fake_oracle.active_job_statuses.add("QUEUED")

    resp = client.put(
        "/api/documents/doc-1/ingestion-config",
        json={"chunking_strategy": "page_level"},
    )

    assert resp.status_code == 409


def test_ingestion_config_detects_derived_setting_drift_from_serving_snapshot(
    fake_oracle: FakeIngestionConfigOracle,
) -> None:
    fake_oracle.add_document(
        "doc-1",
        status=FileStatus.INDEXED,
        chunk_strategy="structure_aware",
        source_parser="local",
    )
    _, observed = documents_route._merge_document_processing_config(DocumentProcessingConfig())
    fake_oracle.serving_recipe_snapshot = {
        "processing_config": {},
        "effective_processing_config": observed.model_dump(mode="json"),
    }
    fake_oracle.processing_configs["doc-1"] = DocumentProcessingConfig(graph_profile="entities")

    data = client.get("/api/documents/doc-1/ingestion-config").json()["data"]

    assert data["drift_fields"] == ["graph_profile"]
    assert data["config_drift"] is True
