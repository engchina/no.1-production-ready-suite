"""文書プレビュー（原本配信）と抽出本文表示用 API のテスト。"""

from app.clients.oracle import reset_local_store
from app.main import app
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
    """原本配信は保存した bytes と保存済み content-type を返す。"""
    body = "社内規程 経費申請 承認フロー".encode()
    document_id = _upload("policy.txt", body, "text/plain")

    resp = client.get(f"/api/documents/{document_id}/content")

    assert resp.status_code == 200
    assert resp.content == body
    assert resp.headers["content-type"].startswith("text/plain")
    assert resp.headers["x-content-type-options"] == "nosniff"
    assert "filename*=UTF-8''policy.txt" in resp.headers["content-disposition"]


def test_document_content_prefers_stored_content_type_without_extension() -> None:
    """拡張子がなくても upload 時に保存した content-type で配信する。"""
    body = "拡張子なしテキスト".encode()
    document_id = _upload("policy", body, "text/plain")

    resp = client.get(f"/api/documents/{document_id}/content")

    assert resp.status_code == 200
    assert resp.content == body
    assert resp.headers["content-type"].startswith("text/plain")
    assert "filename*=UTF-8''policy" in resp.headers["content-disposition"]


def test_document_content_returns_404_for_unknown_document() -> None:
    """存在しないドキュメントの原本配信は 404。"""
    resp = client.get("/api/documents/unknown/content")

    assert resp.status_code == 404
    assert resp.json()["error_messages"] == ["ドキュメントが見つかりません。"]


def test_document_detail_returns_extraction_after_ingest() -> None:
    """取込後の詳細 API は抽出本文とメタデータを返す。"""
    document_id = _upload(
        "policy.txt",
        "社内規程: 経費申請\n部門長が承認します。".encode(),
        "text/plain",
    )
    assert client.post(f"/api/documents/{document_id}/ingest").status_code == 200

    detail = client.get(f"/api/documents/{document_id}")
    assert detail.status_code == 200
    extraction = detail.json()["data"]["extraction"]
    assert extraction["document_type"] == "社内規程"
    assert "部門長が承認" in extraction["raw_text"]
    assert "fields" not in extraction


def test_fields_edit_endpoint_is_not_available() -> None:
    """帳票向けの抽出フィールド編集 endpoint は提供しない。"""
    document_id = _upload("policy.txt", b"sample", "text/plain")

    resp = client.patch(
        f"/api/documents/{document_id}/fields",
        json={"fields": {"document_number": "DOC-001"}},
    )

    assert resp.status_code == 404
