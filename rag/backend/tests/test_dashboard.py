"""ダッシュボード API のテスト。"""

from datetime import UTC, datetime

import pytest

from app.api.routes import dashboard as dashboard_route
from app.main import app
from app.rag.chunking import chunk_text
from app.schemas.document import DocumentSummary, FileStatus
from app.schemas.extraction import StructuredExtraction
from tests.support import AsgiTestClient

client = AsgiTestClient(app)


class FakeDashboardOracle:
    """dashboard 集計に必要な OracleClient 契約だけを持つテスト用 fake。"""

    def __init__(
        self,
        *,
        documents: list[DocumentSummary] | None = None,
        searchable_rows: int = 0,
        extractions: list[dict[str, object]] | None = None,
        chunk_metadata: list[dict[str, str | int | float | bool | None]] | None = None,
    ) -> None:
        self.documents = documents or []
        self.searchable_rows = searchable_rows
        self.extractions = extractions or []
        self.chunk_metadata = chunk_metadata or []

    async def list_documents(self, *, limit: int | None = None) -> list[DocumentSummary]:
        return self.documents[:limit] if limit is not None else self.documents

    async def count_chunks(self) -> int:
        return self.searchable_rows

    async def list_document_extractions(self) -> list[dict[str, object]]:
        return self.extractions

    async def list_chunk_metadata(self) -> list[dict[str, str | int | float | bool | None]]:
        return self.chunk_metadata


@pytest.fixture
def dashboard_oracle(monkeypatch: pytest.MonkeyPatch) -> FakeDashboardOracle:
    """dashboard route の Oracle 依存をテスト用 fake に差し替える。"""
    fake = FakeDashboardOracle()
    monkeypatch.setattr(dashboard_route, "OracleClient", lambda settings: fake)
    monkeypatch.setattr(dashboard_route, "readiness_checks", lambda settings: {"oracle": "ok"})
    return fake


def test_dashboard_summary_returns_zero_state(dashboard_oracle: FakeDashboardOracle) -> None:
    """データがない状態でもダッシュボード契約を返す。"""
    response = client.get("/api/dashboard/summary")

    assert response.status_code == 200
    data = response.json()["data"]
    assert data["stats"] == {
        "total_uploads": 0,
        "uploads_this_month": 0,
        "total_indexed": 0,
        "indexed_this_month": 0,
        "searchable_rows": 0,
    }
    assert data["ingestion_quality"] == {
        "document_count": 0,
        "structured_document_count": 0,
        "element_count": 0,
        "table_count": 0,
        "list_count": 0,
        "page_count": 0,
        "chunk_profile_counts": {},
        "content_kind_counts": {},
    }
    assert data["recent_activities"] == []
    assert data["system"]["status"] == "online"
    assert set(data["system"]) == {"status", "version", "searchable_rows", "checks"}
    assert data["system"]["checks"] == {"oracle": "ok"}


def test_dashboard_summary_reflects_documents_and_indexed_chunks(
    dashboard_oracle: FakeDashboardOracle,
) -> None:
    """アップロード・索引済み状態を集計と最近の活動へ反映する。"""
    now = datetime.now(UTC)
    indexed_document = DocumentSummary(
        id="doc-indexed",
        file_name="policy-a.txt",
        status=FileStatus.INDEXED,
        content_type="text/plain",
        file_size_bytes=128,
        uploaded_at=now,
        indexed_at=now,
    )
    uploaded_document = DocumentSummary(
        id="doc-uploaded",
        file_name="manual-b.txt",
        status=FileStatus.UPLOADED,
        content_type="text/plain",
        file_size_bytes=64,
        uploaded_at=now,
    )
    extraction = StructuredExtraction(
        raw_text="\n".join(
            [
                "# 経費申請",
                "社内規程 経費申請 承認フロー",
                "- 申請者が証憑を添付する",
                "- 上長が承認する",
                "| 項目 | 金額 |",
                "| 交通費 | 1000 |",
            ]
        ),
        confidence=0.9,
    ).model_dump(mode="json")
    dashboard_oracle.documents = [indexed_document, uploaded_document]
    dashboard_oracle.searchable_rows = 3
    dashboard_oracle.extractions = [extraction, {}]
    dashboard_oracle.chunk_metadata = [
        {"chunk_profile": "structure_v1", "content_kind": "text"},
        {"chunk_profile": "structure_v1", "content_kind": "table"},
        {"chunk_profile": "structure_v1", "content_kind": "list"},
    ]

    response = client.get("/api/dashboard/summary")

    assert response.status_code == 200
    data = response.json()["data"]
    stats = data["stats"]
    assert stats["total_uploads"] == 2
    assert stats["uploads_this_month"] == 2
    assert stats["total_indexed"] == 1
    assert stats["indexed_this_month"] == 1
    assert stats["searchable_rows"] == 3

    activities = data["recent_activities"]
    assert {activity["id"] for activity in activities} == {"doc-indexed", "doc-uploaded"}
    indexed_activity = next(activity for activity in activities if activity["id"] == "doc-indexed")
    assert indexed_activity["type"] == "INDEXING"
    assert indexed_activity["status"] == "INDEXED"
    uploaded_activity = next(
        activity for activity in activities if activity["id"] == "doc-uploaded"
    )
    assert uploaded_activity["type"] == "UPLOAD"
    assert uploaded_activity["status"] == "UPLOADED"
    assert data["system"]["searchable_rows"] == stats["searchable_rows"]

    quality = data["ingestion_quality"]
    assert quality["document_count"] == 2
    assert quality["structured_document_count"] == 1
    assert quality["element_count"] >= 4
    assert quality["table_count"] >= 1
    assert quality["list_count"] >= 1
    assert quality["page_count"] >= 1
    assert quality["chunk_profile_counts"]["structure_v1"] == 3
    assert quality["content_kind_counts"]["table"] == 1
    assert quality["content_kind_counts"]["list"] == 1


def test_dashboard_ingestion_quality_counts_raw_text_fallback_chunks(
    dashboard_oracle: FakeDashboardOracle,
) -> None:
    """raw_text fallback と text_v1 chunk も取込品質集計へ含める。"""
    chunks = chunk_text("本文だけの旧抽出です。", chunk_size=80, overlap=0)
    dashboard_oracle.documents = [
        DocumentSummary(
            id="doc-legacy",
            file_name="legacy.txt",
            status=FileStatus.INDEXED,
            content_type="text/plain",
            file_size_bytes=32,
            uploaded_at=datetime.now(UTC),
            indexed_at=datetime.now(UTC),
        )
    ]
    dashboard_oracle.searchable_rows = len(chunks)
    dashboard_oracle.extractions = [
        StructuredExtraction(
            raw_text="# レガシー\n本文だけの旧抽出です。",
            confidence=0.8,
        ).model_dump(mode="json")
    ]
    dashboard_oracle.chunk_metadata = [
        {"chunk_profile": "text_v1", "content_kind": "text"} for _ in chunks
    ]

    response = client.get("/api/dashboard/summary")

    assert response.status_code == 200
    quality = response.json()["data"]["ingestion_quality"]
    assert quality["document_count"] == 1
    assert quality["structured_document_count"] == 1
    assert quality["element_count"] >= 1
    assert quality["chunk_profile_counts"] == {"text_v1": len(chunks)}
    assert quality["content_kind_counts"] == {"text": len(chunks)}
