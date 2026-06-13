"""文書プレビュー（原本配信）と抽出フィールド編集の API テスト。"""

import asyncio

from app.clients.oracle import OracleClient, reset_local_store
from app.main import app
from app.schemas.document import FileStatus
from tests.support import AsgiTestClient

client = AsgiTestClient(app)


def setup_function() -> None:
    reset_local_store()


def _upload(file_name: str, body: bytes, content_type: str) -> str:
    resp = client.post(
        "/api/documents/upload",
        files={"file": (file_name, body, content_type)},
    )
    assert resp.status_code == 200
    return str(resp.json()["data"]["id"])


def test_document_content_returns_original_bytes() -> None:
    """原本配信は保存した bytes と拡張子由来の content-type を返す。"""
    body = "請求書 クラウド利用料 120,000円".encode()
    document_id = _upload("invoice.txt", body, "text/plain")

    resp = client.get(f"/api/documents/{document_id}/content")

    assert resp.status_code == 200
    assert resp.content == body
    assert resp.headers["content-type"].startswith("text/plain")
    assert resp.headers["x-content-type-options"] == "nosniff"
    assert "filename*=UTF-8''invoice.txt" in resp.headers["content-disposition"]


def test_document_content_returns_404_for_unknown_document() -> None:
    """存在しないドキュメントの原本配信は 404。"""
    resp = client.get("/api/documents/unknown/content")

    assert resp.status_code == 404
    assert resp.json()["error_messages"] == ["ドキュメントが見つかりません。"]


def test_update_fields_persists_edits_on_analyzed_document() -> None:
    """分析済みドキュメントの抽出フィールドを編集して保存できる。"""
    document_id = _upload(
        "invoice.txt",
        "請求書番号: INV-001\n請求金額: 120,000".encode(),
        "text/plain",
    )
    assert client.post(f"/api/documents/{document_id}/analyze").status_code == 200

    resp = client.patch(
        f"/api/documents/{document_id}/fields",
        json={"fields": {"document_number": "INV-FIXED", "total_amount": 99000}},
    )

    assert resp.status_code == 200
    fields = resp.json()["data"]["extracted_fields"]["fields"]
    assert fields["document_number"] == "INV-FIXED"
    assert fields["total_amount"] == 99000

    detail = client.get(f"/api/documents/{document_id}")
    assert detail.json()["data"]["extracted_fields"]["fields"]["document_number"] == "INV-FIXED"


def test_update_fields_rejected_before_analysis() -> None:
    """未分析（UPLOADED）のドキュメントは編集できない。"""
    document_id = _upload("invoice.txt", b"sample", "text/plain")

    resp = client.patch(
        f"/api/documents/{document_id}/fields",
        json={"fields": {"document_number": "INV-001"}},
    )

    assert resp.status_code == 409
    assert resp.json()["error_messages"] == ["分析済みのドキュメントのみ編集できます。"]
    stored = asyncio.run(OracleClient().get_document(document_id))
    assert stored is not None
    assert stored.status == FileStatus.UPLOADED
