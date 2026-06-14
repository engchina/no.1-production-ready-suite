"""ダッシュボード API のテスト。"""

import asyncio

from app.clients.oracle import OracleClient
from app.main import app
from app.rag.chunking import chunk_text
from app.schemas.document import FileStatus
from app.schemas.extraction import StructuredExtraction
from tests.support import AsgiTestClient

client = AsgiTestClient(app)


def test_dashboard_summary_returns_zero_state() -> None:
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
    assert data["system"]["adapter"] == "local"
    assert data["system"]["checks"] == {"local_storage": "ok"}


def test_dashboard_summary_reflects_documents_and_indexed_chunks() -> None:
    """アップロード・索引済み状態を集計と最近の活動へ反映する。"""
    indexed_id = _upload(
        "policy-a.txt",
        "\n".join(
            [
                "# 経費申請",
                "社内規程 経費申請 承認フロー",
                "- 申請者が証憑を添付する",
                "- 上長が承認する",
                "| 項目 | 金額 |",
                "| 交通費 | 1000 |",
            ]
        ).encode(),
    )
    ingest_resp = client.post(f"/api/documents/{indexed_id}/ingest")
    assert ingest_resp.status_code == 200

    uploaded_id = _upload("manual-b.txt", "未取込のマニュアル".encode())

    response = client.get("/api/dashboard/summary")

    assert response.status_code == 200
    data = response.json()["data"]
    stats = data["stats"]
    assert stats["total_uploads"] == 2
    assert stats["uploads_this_month"] == 2
    assert stats["total_indexed"] == 1
    assert stats["indexed_this_month"] == 1
    assert stats["searchable_rows"] >= 1

    activities = data["recent_activities"]
    assert {activity["id"] for activity in activities} == {indexed_id, uploaded_id}
    indexed_activity = next(activity for activity in activities if activity["id"] == indexed_id)
    assert indexed_activity["type"] == "INDEXING"
    assert indexed_activity["status"] == "INDEXED"
    uploaded_activity = next(activity for activity in activities if activity["id"] == uploaded_id)
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
    assert quality["chunk_profile_counts"]["structure_v1"] >= 1
    assert quality["content_kind_counts"]["table"] >= 1
    assert quality["content_kind_counts"]["list"] >= 1


def test_dashboard_ingestion_quality_counts_raw_text_fallback_chunks() -> None:
    """raw_text fallback と text_v1 chunk も取込品質集計へ含める。"""
    oracle = OracleClient()
    document = asyncio.run(
        oracle.create_document(
            file_name="legacy.txt",
            object_storage_path="local://legacy.txt",
            content_type="text/plain",
            file_size_bytes=32,
            content_sha256="a" * 64,
        )
    )
    asyncio.run(
        oracle.save_extraction(
            document.id,
            StructuredExtraction(raw_text="# レガシー\n本文だけの旧抽出です。", confidence=0.8),
        )
    )
    chunks = chunk_text("本文だけの旧抽出です。", chunk_size=80, overlap=0)
    asyncio.run(oracle.save_chunks(document.id, chunks, [[0.0] * 1536 for _ in chunks]))
    asyncio.run(oracle.update_document_status(document.id, FileStatus.INDEXED))

    response = client.get("/api/dashboard/summary")

    assert response.status_code == 200
    quality = response.json()["data"]["ingestion_quality"]
    assert quality["document_count"] == 1
    assert quality["structured_document_count"] == 1
    assert quality["element_count"] >= 1
    assert quality["chunk_profile_counts"] == {"text_v1": len(chunks)}
    assert quality["content_kind_counts"] == {"text": len(chunks)}


def _upload(file_name: str, content: bytes) -> str:
    response = client.post(
        "/api/documents/upload",
        files={"file": (file_name, content, "text/plain")},
    )
    assert response.status_code == 200
    return str(response.json()["data"]["id"])
