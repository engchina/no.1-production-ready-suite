"""文書プレビュー（原本配信）と抽出本文表示用 API のテスト。"""

from datetime import UTC, datetime
from uuid import uuid4

import pytest

from app.api.routes import documents as documents_route
from app.main import app
from app.schemas.document import DocumentDetail, DocumentSummary, FileStatus
from tests.support import AsgiTestClient

client = AsgiTestClient(app)


class FakeWorkspaceOracle:
    """文書 workspace API テスト用のインメモリ Oracle fake。"""

    def __init__(self) -> None:
        self.documents: dict[str, DocumentDetail] = {}

    async def find_document_by_content_hash(self, content_sha256: str) -> DocumentSummary | None:
        for detail in self.documents.values():
            if detail.content_sha256 == content_sha256:
                return DocumentSummary.model_validate(detail.model_dump())
        return None

    async def create_document(
        self,
        *,
        file_name: str,
        object_storage_path: str,
        content_type: str | None,
        file_size_bytes: int | None,
        content_sha256: str | None,
        duplicate_of_document_id: str | None,
    ) -> DocumentDetail:
        document_id = uuid4().hex
        detail = DocumentDetail(
            id=document_id,
            file_name=file_name,
            status=FileStatus.UPLOADED,
            object_storage_path=object_storage_path,
            content_type=content_type,
            file_size_bytes=file_size_bytes,
            content_sha256=content_sha256,
            duplicate_of_document_id=duplicate_of_document_id,
            uploaded_at=datetime.now(UTC),
        )
        self.documents[document_id] = detail
        return detail

    async def get_document(self, document_id: str) -> DocumentDetail | None:
        return self.documents.get(document_id)

    async def update_document_status(
        self,
        document_id: str,
        status: FileStatus,
        error_message: str | None = None,
    ) -> DocumentDetail:
        detail = self.documents[document_id]
        indexed_at = datetime.now(UTC) if status == FileStatus.INDEXED else detail.indexed_at
        updated = detail.model_copy(
            update={
                "status": status,
                "indexed_at": indexed_at,
                "error_message": error_message,
            }
        )
        self.documents[document_id] = updated
        return updated


class FakeWorkspaceIngestionPipeline:
    """取込 API テストで外部 AI/embedding を呼ばずに抽出結果を保存する fake。"""

    def __init__(self, *, oracle: FakeWorkspaceOracle) -> None:
        self._oracle = oracle

    async def ingest(
        self,
        document_id: str,
        image_bytes: bytes,
        prompt: str,
        *,
        content_type: str = "application/octet-stream",
    ) -> DocumentDetail:
        detail = await self._oracle.get_document(document_id)
        assert detail is not None
        raw_text = image_bytes.decode("utf-8", errors="replace")
        self._oracle.documents[document_id] = detail.model_copy(
            update={
                "extraction": {
                    "document_type": "社内規程",
                    "raw_text": raw_text,
                    "confidence": 0.9,
                }
            }
        )
        return await self._oracle.update_document_status(document_id, FileStatus.INDEXED)


@pytest.fixture(autouse=True)
def fake_document_dependencies(monkeypatch: pytest.MonkeyPatch) -> None:
    fake_oracle = FakeWorkspaceOracle()
    monkeypatch.setattr(documents_route, "OracleClient", lambda: fake_oracle)
    monkeypatch.setattr(documents_route, "IngestionPipeline", FakeWorkspaceIngestionPipeline)


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
