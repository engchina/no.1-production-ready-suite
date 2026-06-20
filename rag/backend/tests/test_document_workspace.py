"""文書プレビュー（原本配信）と抽出本文表示用 API のテスト。"""

from datetime import UTC, datetime
from uuid import uuid4

import anyio
import pytest

from app.api.routes import documents as documents_route
from app.clients.object_storage import ObjectStorageClient
from app.main import app
from app.schemas.document import (
    DocumentChunkView,
    DocumentDetail,
    DocumentExtractionExportFormat,
    DocumentSummary,
    FileStatus,
    IngestionJob,
    IngestionJobStatus,
    IngestionSegment,
)
from app.schemas.extraction import ExtractionTable, ExtractionTableCell
from app.schemas.knowledge_base import KnowledgeBaseRef
from tests.support import AsgiTestClient

client = AsgiTestClient(app)


def test_structured_table_html_preserves_formula_cell_metadata() -> None:
    """review HTML は table cell formula lineage を data 属性として安全に出す。"""
    table = ExtractionTable(
        table_id="tbl-1",
        cells=[
            ExtractionTableCell(
                row=1,
                col=1,
                text="<INDEXED>",
                metadata={
                    "formula_cell_ref": "B2",
                    "equation_format": "excel_formula",
                    "formula": 'IF(A2="状態","<INDEXED>","ERROR")',
                    "formula_value": "<INDEXED>",
                },
            )
        ],
    )

    html = documents_route._structured_table_html(table, "")

    assert 'data-formula-ref="B2"' in html
    assert 'data-formula-format="excel_formula"' in html
    assert 'data-formula-value="&lt;INDEXED&gt;"' in html
    assert "&lt;INDEXED&gt;" in html
    assert "<INDEXED>" not in html


class FakeWorkspaceOracle:
    """文書 workspace API テスト用のインメモリ Oracle fake。"""

    def __init__(self) -> None:
        self.documents: dict[str, DocumentDetail] = {}
        self.chunks: dict[str, list[DocumentChunkView]] = {}
        self.ingestion_jobs: dict[str, IngestionJob] = {}
        self.ingestion_segments: dict[str, list[IngestionSegment]] = {}
        self.knowledge_base_assignments: set[tuple[str, str]] = set()

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
        knowledge_base_ids: list[str] | None = None,
    ) -> DocumentDetail:
        document_id = uuid4().hex
        knowledge_bases = [
            KnowledgeBaseRef(id=knowledge_base_id, name=f"KB {knowledge_base_id}")
            for knowledge_base_id in knowledge_base_ids or ["kb-default"]
        ]
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
            knowledge_bases=knowledge_bases,
        )
        self.documents[document_id] = detail
        for knowledge_base in knowledge_bases:
            self.knowledge_base_assignments.add((knowledge_base.id, document_id))
        return detail

    async def assign_documents_to_knowledge_base(
        self,
        knowledge_base_id: str,
        document_ids: list[str],
    ) -> object:
        for document_id in document_ids:
            detail = self.documents[document_id]
            if not any(ref.id == knowledge_base_id for ref in detail.knowledge_bases):
                detail = detail.model_copy(
                    update={
                        "knowledge_bases": [
                            *detail.knowledge_bases,
                            KnowledgeBaseRef(
                                id=knowledge_base_id,
                                name=f"KB {knowledge_base_id}",
                            ),
                        ]
                    }
                )
                self.documents[document_id] = detail
            self.knowledge_base_assignments.add((knowledge_base_id, document_id))
        return object()

    async def list_document_chunks(self, document_id: str) -> list[DocumentChunkView]:
        return list(self.chunks.get(document_id, []))

    async def get_owning_knowledge_base(self, document_id: str) -> None:
        # この fake は KB 別の取込上書きを使わない(グローバル設定で取込する)。
        return None

    async def list_ingestion_segments(self, document_id: str) -> list[IngestionSegment]:
        return list(self.ingestion_segments.get(document_id, []))

    async def get_document(self, document_id: str) -> DocumentDetail | None:
        return self.documents.get(document_id)

    async def delete_document(self, document_id: str) -> bool:
        if document_id not in self.documents:
            return False
        del self.documents[document_id]
        self.ingestion_segments.pop(document_id, None)
        self.ingestion_jobs = {
            job_id: job
            for job_id, job in self.ingestion_jobs.items()
            if job.document_id != document_id
        }
        for duplicate_id, detail in list(self.documents.items()):
            if detail.duplicate_of_document_id == document_id:
                self.documents[duplicate_id] = detail.model_copy(
                    update={"duplicate_of_document_id": None}
                )
        return True

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

    async def create_ingestion_job(self, job: IngestionJob) -> IngestionJob:
        self.ingestion_jobs[job.id] = job
        return job

    async def get_ingestion_job(self, job_id: str) -> IngestionJob | None:
        return self.ingestion_jobs.get(job_id)

    async def list_ingestion_jobs(
        self,
        *,
        status: IngestionJobStatus | None = None,
        limit: int | None = None,
        offset: int = 0,
    ) -> list[IngestionJob]:
        jobs = [
            job for job in self.ingestion_jobs.values() if status is None or job.status == status
        ]
        sorted_jobs = sorted(
            jobs,
            key=lambda job: job.queued_at,
            reverse=True,
        )
        return sorted_jobs[offset : offset + limit] if limit is not None else sorted_jobs[offset:]

    async def list_document_ingestion_jobs(
        self,
        document_id: str,
        *,
        status: IngestionJobStatus | None = None,
    ) -> list[IngestionJob]:
        return [
            job
            for job in self.ingestion_jobs.values()
            if job.document_id == document_id and (status is None or job.status == status)
        ]

    async def count_ingestion_jobs(
        self,
        *,
        status: IngestionJobStatus | None = None,
    ) -> int:
        return len(
            [job for job in self.ingestion_jobs.values() if status is None or job.status == status]
        )

    async def recover_stale_ingestion_jobs(
        self,
        *,
        stale_before: datetime,
        limit: int,
    ) -> list[IngestionJob]:
        stale_jobs = [
            job
            for job in self.ingestion_jobs.values()
            if job.status == IngestionJobStatus.RUNNING
            and (job.started_at or job.queued_at) < stale_before
        ][:limit]
        for job in stale_jobs:
            status = (
                IngestionJobStatus.FAILED
                if job.attempt_count >= job.max_attempts
                else IngestionJobStatus.QUEUED
            )
            self.ingestion_jobs[job.id] = job.model_copy(
                update={
                    "status": status,
                    "started_at": None if status == IngestionJobStatus.QUEUED else job.started_at,
                    "finished_at": (
                        datetime.now(UTC) if status == IngestionJobStatus.FAILED else None
                    ),
                }
            )
        return stale_jobs

    async def claim_ingestion_job(
        self,
        job_id: str,
        *,
        started_at: datetime,
    ) -> IngestionJob | None:
        job = self.ingestion_jobs.get(job_id)
        if job is None or job.status != IngestionJobStatus.QUEUED:
            return None
        claimed = job.model_copy(
            update={
                "status": IngestionJobStatus.RUNNING,
                "attempt_count": job.attempt_count + 1,
                "started_at": started_at,
                "error_message": None,
                "finished_at": None,
            }
        )
        self.ingestion_jobs[job_id] = claimed
        return claimed

    async def update_ingestion_job(
        self,
        job_id: str,
        **updates: object,
    ) -> IngestionJob | None:
        job = self.ingestion_jobs.get(job_id)
        if job is None:
            return None
        updated = job.model_copy(
            update={key: value for key, value in updates.items() if value is not None}
        )
        self.ingestion_jobs[job_id] = updated
        return updated


class FakeWorkspaceIngestionPipeline:
    """取込 API テストで外部 AI/embedding を呼ばずに抽出結果を保存する fake。"""

    def __init__(self, *, oracle: FakeWorkspaceOracle, settings: object | None = None) -> None:
        self._oracle = oracle
        self._settings = settings

    async def ingest(
        self,
        document_id: str,
        image_bytes: bytes,
        prompt: str,
        *,
        content_type: str = "application/octet-stream",
        source_profile: object | None = None,
        cancel_checker: object | None = None,
    ) -> DocumentDetail:
        _ = prompt, content_type, source_profile, cancel_checker
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
def fake_document_dependencies(monkeypatch: pytest.MonkeyPatch) -> FakeWorkspaceOracle:
    fake_oracle = FakeWorkspaceOracle()
    monkeypatch.setattr(documents_route, "OracleClient", lambda: fake_oracle)
    monkeypatch.setattr(documents_route, "IngestionPipeline", FakeWorkspaceIngestionPipeline)
    return fake_oracle


def _upload(file_name: str, body: bytes, content_type: str) -> str:
    resp = client.post(
        "/api/documents/upload",
        files={"file": (file_name, body, content_type)},
    )
    assert resp.status_code == 200
    return str(resp.json()["data"]["id"])


def test_document_upload_returns_assigned_knowledge_bases() -> None:
    """upload 時に指定した KB 所属はレスポンスへ返す。"""
    resp = client.post(
        "/api/documents/upload",
        data={"knowledge_base_ids": "kb-1,kb-2"},
        files={"file": ("policy.txt", b"sample", "text/plain")},
    )

    assert resp.status_code == 200
    assert resp.json()["data"]["knowledge_bases"] == [
        {"id": "kb-1", "name": "KB kb-1"},
        {"id": "kb-2", "name": "KB kb-2"},
    ]


def test_document_upload_returns_source_profile() -> None:
    """upload レスポンスは原本品質と処理方針を source profile として返す。"""
    resp = client.post(
        "/api/documents/upload",
        files={"file": ("policy.txt", "本文".encode(), "text/plain")},
    )

    assert resp.status_code == 200
    profile = resp.json()["data"]["source_profile"]
    assert profile["original_file_name"] == "policy.txt"
    assert profile["sanitized_file_name"] == "policy.txt"
    assert profile["extension"] == ".txt"
    assert profile["content_type"] == "text/plain"
    assert profile["modality"] == "text"
    assert profile["parser_profile"] == "local_text_structure"
    assert profile["text_charset"] == "utf-8"
    assert profile["quality_status"] == "ready"
    assert profile["quality_warnings"] == []


def test_document_upload_accepts_tsv_as_local_text_table_source() -> None:
    """TSV upload は unknown 扱いにせず local table parser へ渡す。"""
    resp = client.post(
        "/api/documents/upload",
        files={
            "file": (
                "metrics.tsv",
                b"name\tamount\nalpha\t1200\n",
                "text/tab-separated-values",
            )
        },
    )

    assert resp.status_code == 200
    profile = resp.json()["data"]["source_profile"]
    assert profile["extension"] == ".tsv"
    assert profile["content_type"] == "text/tab-separated-values"
    assert profile["modality"] == "text"
    assert profile["parser_profile"] == "local_text_structure"
    assert profile["parser_backend"] == "local_partition"
    assert profile["preview_kind"] == "text"
    assert profile["unsupported_reason"] is None


def test_document_upload_auto_ingestion_starts_background_pipeline() -> None:
    """auto 指定時は upload 後に取込 job をキュー投入する。"""
    resp = client.post(
        "/api/documents/upload",
        data={"ingestion_mode": "auto"},
        files={
            "file": (
                "policy.txt",
                "社内規程: 経費申請\n部門長が承認します。".encode(),
                "text/plain",
            )
        },
    )

    assert resp.status_code == 200
    body = resp.json()["data"]
    assert body["ingestion_started"] is True
    assert body["ingestion_job"]["status"] == "QUEUED"
    assert body["ingestion_job"]["parser_profile"] == "local_text_structure"

    detail = client.get(f"/api/documents/{body['id']}")
    assert detail.status_code == 200
    assert detail.json()["data"]["status"] == "UPLOADED"

    job_detail = client.get(f"/api/documents/ingestion-jobs/{body['ingestion_job']['id']}")
    assert job_detail.status_code == 200
    assert job_detail.json()["data"]["status"] == "QUEUED"

    anyio.run(documents_route._run_ingestion_job, body["ingestion_job"]["id"])
    detail = client.get(f"/api/documents/{body['id']}")
    assert detail.status_code == 200
    indexed = detail.json()["data"]
    assert indexed["status"] == "INDEXED"
    assert "部門長が承認" in indexed["extraction"]["raw_text"]

    job_detail = client.get(f"/api/documents/ingestion-jobs/{body['ingestion_job']['id']}")
    assert job_detail.status_code == 200
    assert job_detail.json()["data"]["status"] == "SUCCEEDED"
    assert job_detail.json()["data"]["attempt_count"] == 1


def test_document_upload_auto_ingestion_skips_unsupported_audio() -> None:
    """未対応 audio は auto 指定でも取込 pipeline を開始せず skipped にする。"""
    resp = client.post(
        "/api/documents/upload",
        data={"ingestion_mode": "auto"},
        files={"file": ("voice.mp3", b"ID3", "audio/mpeg")},
    )

    assert resp.status_code == 200
    body = resp.json()["data"]
    assert body["ingestion_started"] is False
    assert body["source_profile"]["modality"] == "audio"
    assert body["source_profile"]["parser_profile"] == "unsupported_audio"
    assert body["source_profile"]["unsupported_reason"] == "audio_transcription_not_configured"
    assert "unsupported_audio" in body["source_profile"]["quality_warnings"]
    assert body["ingestion_job"]["status"] == "SKIPPED"
    assert body["ingestion_job"]["skip_reason"] == "audio_transcription_not_configured"

    detail = client.get(f"/api/documents/{body['id']}")
    assert detail.status_code == 200
    assert detail.json()["data"]["status"] == "UPLOADED"


def test_document_upload_auto_ingestion_skips_common_audio_mime_variant() -> None:
    """M4A などの一般的な音声 MIME も 415 ではなく skipped reason を返す。"""
    resp = client.post(
        "/api/documents/upload",
        data={"ingestion_mode": "auto"},
        files={"file": ("meeting.m4a", b"m4a bytes", "audio/mp4")},
    )

    assert resp.status_code == 200
    body = resp.json()["data"]
    assert body["ingestion_started"] is False
    assert body["source_profile"]["modality"] == "audio"
    assert body["source_profile"]["parser_profile"] == "unsupported_audio"
    assert body["source_profile"]["unsupported_reason"] == "audio_transcription_not_configured"
    assert "unsupported_audio" in body["source_profile"]["quality_warnings"]
    assert body["ingestion_job"]["status"] == "SKIPPED"
    assert body["ingestion_job"]["skip_reason"] == "audio_transcription_not_configured"


def test_document_upload_rejects_unknown_octet_stream_before_storage() -> None:
    """application/octet-stream でも拡張子から未知の binary は保存前に 415 にする。"""
    resp = client.post(
        "/api/documents/upload",
        data={"ingestion_mode": "auto"},
        files={"file": ("payload.bin", b"\x00\x01\x02", "application/octet-stream")},
    )

    assert resp.status_code == 415
    assert resp.json()["error_messages"] == ["対応していないファイル形式です。"]


def test_document_upload_accepts_recognized_octet_stream_for_explicit_skip() -> None:
    """MIME が欠落した M4A は audio と判定し、明示的な skipped reason を返す。"""
    resp = client.post(
        "/api/documents/upload",
        data={"ingestion_mode": "auto"},
        files={"file": ("meeting.m4a", b"m4a bytes", "application/octet-stream")},
    )

    assert resp.status_code == 200
    body = resp.json()["data"]
    assert body["source_profile"]["modality"] == "audio"
    assert body["source_profile"]["parser_profile"] == "unsupported_audio"
    assert body["ingestion_job"]["status"] == "SKIPPED"
    assert body["ingestion_job"]["skip_reason"] == "audio_transcription_not_configured"


def test_document_upload_auto_ingestion_skips_unsupported_outlook_msg() -> None:
    """Outlook MSG は未対応 email として auto 取込を開始しない。"""
    resp = client.post(
        "/api/documents/upload",
        data={"ingestion_mode": "auto"},
        files={
            "file": (
                "approval.msg",
                b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1outlook msg",
                "application/vnd.ms-outlook",
            )
        },
    )

    assert resp.status_code == 200
    body = resp.json()["data"]
    assert body["ingestion_started"] is False
    assert body["source_profile"]["modality"] == "email"
    assert body["source_profile"]["parser_profile"] == "unsupported_outlook_msg"
    assert body["source_profile"]["parser_backend"] == "unsupported"
    assert body["source_profile"]["preview_kind"] == "unsupported"
    assert body["source_profile"]["unsupported_reason"] == "outlook_msg_not_supported"
    assert "unsupported_outlook_msg" in body["source_profile"]["quality_warnings"]
    assert body["ingestion_job"]["status"] == "SKIPPED"
    assert body["ingestion_job"]["skip_reason"] == "outlook_msg_not_supported"

    detail = client.get(f"/api/documents/{body['id']}")
    assert detail.status_code == 200
    assert detail.json()["data"]["status"] == "UPLOADED"


def test_document_upload_auto_ingestion_skips_unsupported_tiff_image() -> None:
    """TIFF 画像は auto 指定でも VLM に送らず skipped にする。"""
    resp = client.post(
        "/api/documents/upload",
        data={"ingestion_mode": "auto"},
        files={"file": ("scan.tiff", b"II*\x00tiff", "image/tiff")},
    )

    assert resp.status_code == 200
    body = resp.json()["data"]
    assert body["ingestion_started"] is False
    assert body["source_profile"]["modality"] == "image"
    assert body["source_profile"]["parser_profile"] == "unsupported_tiff_image"
    assert body["source_profile"]["parser_backend"] == "unsupported"
    assert body["source_profile"]["preview_kind"] == "unsupported"
    assert body["source_profile"]["unsupported_reason"] == "tiff_image_not_supported"
    assert "unsupported_tiff_image" in body["source_profile"]["quality_warnings"]
    assert body["ingestion_job"]["status"] == "SKIPPED"
    assert body["ingestion_job"]["skip_reason"] == "tiff_image_not_supported"

    detail = client.get(f"/api/documents/{body['id']}")
    assert detail.status_code == 200
    assert detail.json()["data"]["status"] == "UPLOADED"


def test_document_upload_auto_ingestion_skips_unsupported_legacy_office() -> None:
    """旧バイナリ Office は auto 指定でも VLM に送らず skipped にする。"""
    resp = client.post(
        "/api/documents/upload",
        data={"ingestion_mode": "auto"},
        files={"file": ("legacy.doc", b"legacy office", "application/msword")},
    )

    assert resp.status_code == 200
    body = resp.json()["data"]
    assert body["ingestion_started"] is False
    assert body["source_profile"]["modality"] == "office"
    assert body["source_profile"]["parser_profile"] == "unsupported_legacy_office_binary"
    assert body["source_profile"]["parser_backend"] == "unsupported"
    assert body["source_profile"]["preview_kind"] == "unsupported"
    assert body["source_profile"]["unsupported_reason"] == "legacy_office_binary_not_supported"
    assert "unsupported_legacy_office_binary" in body["source_profile"]["quality_warnings"]
    assert body["ingestion_job"]["status"] == "SKIPPED"
    assert body["ingestion_job"]["skip_reason"] == "legacy_office_binary_not_supported"

    detail = client.get(f"/api/documents/{body['id']}")
    assert detail.status_code == 200
    assert detail.json()["data"]["status"] == "UPLOADED"


def test_document_upload_auto_ingestion_skips_duplicate_source() -> None:
    """重複原本は auto 指定でも重複索引を作らず、手動確認に残す。"""
    body = "同じ本文".encode()
    original = client.post(
        "/api/documents/upload",
        files={"file": ("original.txt", body, "text/plain")},
    )
    assert original.status_code == 200

    duplicate = client.post(
        "/api/documents/upload",
        data={"ingestion_mode": "auto"},
        files={"file": ("duplicate.txt", body, "text/plain")},
    )

    assert duplicate.status_code == 200
    payload = duplicate.json()["data"]
    assert payload["ingestion_started"] is False
    assert payload["ingestion_job"]["status"] == "SKIPPED"
    assert payload["ingestion_job"]["skip_reason"] == "duplicate_content"
    assert payload["duplicate_of_document_id"] == original.json()["data"]["id"]
    assert "duplicate_content" in payload["source_profile"]["quality_warnings"]

    detail = client.get(f"/api/documents/{payload['id']}")
    assert detail.status_code == 200
    assert detail.json()["data"]["status"] == "UPLOADED"


def test_batch_upload_auto_queues_ingestion_jobs() -> None:
    """batch-upload は複数ファイルを保存し、それぞれの取込 job を作る。"""
    resp = client.post(
        "/api/documents/batch-upload",
        data={"ingestion_mode": "auto", "knowledge_base_ids": "kb-1"},
        files=[
            ("files", ("policy-a.txt", "A 規程".encode(), "text/plain")),
            ("files", ("policy-b.txt", "B 規程".encode(), "text/plain")),
        ],
    )

    assert resp.status_code == 200
    data = resp.json()["data"]
    assert data["total_count"] == 2
    assert data["uploaded_count"] == 2
    assert data["queued_count"] == 2
    assert data["skipped_count"] == 0
    assert [item["file_name"] for item in data["items"]] == ["policy-a.txt", "policy-b.txt"]
    assert all(item["ingestion_started"] is True for item in data["items"])
    assert all(item["ingestion_job"]["status"] == "QUEUED" for item in data["items"])

    jobs = client.get("/api/documents/ingestion-jobs")
    assert jobs.status_code == 200
    listed = jobs.json()["data"]["items"]
    assert len(listed) == 2
    assert {job["status"] for job in listed} == {"QUEUED"}
    assert {job["parser_profile"] for job in listed} == {"local_text_structure"}

    for item in data["items"]:
        detail = client.get(f"/api/documents/{item['id']}")
        assert detail.status_code == 200
        assert detail.json()["data"]["status"] == "UPLOADED"

    for item in data["items"]:
        anyio.run(documents_route._run_ingestion_job, item["ingestion_job"]["id"])
        detail = client.get(f"/api/documents/{item['id']}")
        assert detail.status_code == 200
        assert detail.json()["data"]["status"] == "INDEXED"


def test_batch_upload_failed_item_includes_source_profile() -> None:
    """batch-upload の失敗 item は判定できた source profile を返す。"""
    resp = client.post(
        "/api/documents/batch-upload",
        files=[
            ("files", ("policy-ok.txt", b"OK", "text/plain")),
            ("files", ("policy.exe", b"MZ", "application/x-msdownload")),
        ],
    )

    assert resp.status_code == 200
    data = resp.json()["data"]
    assert data["total_count"] == 2
    assert data["uploaded_count"] == 1
    assert data["failed_count"] == 1
    assert data["items"][0]["file_name"] == "policy-ok.txt"
    failed = data["failed_items"][0]
    assert failed["file_name"] == "policy.exe"
    assert failed["status_code"] == 415
    assert failed["source_profile"]["modality"] == "unknown"
    assert failed["source_profile"]["parser_profile"] == "enterprise_ai_generic"
    assert failed["source_profile"]["preview_kind"] == "unsupported"
    assert failed["source_profile"]["unsupported_reason"] == "unknown_file_type"
    assert "unknown_modality" in failed["source_profile"]["quality_warnings"]


def test_document_ingestion_job_endpoint_queues_existing_document() -> None:
    """保存済み文書は文書単位で取込 job へ投入できる。"""
    document_id = _upload(
        "manual-policy.txt",
        "手動キュー投入対象".encode(),
        "text/plain",
    )

    resp = client.post(f"/api/documents/{document_id}/ingestion-jobs")

    assert resp.status_code == 200
    job = resp.json()["data"]
    assert job["document_id"] == document_id
    assert job["status"] == "QUEUED"
    assert job["parser_profile"] == "local_text_structure"

    detail = client.get(f"/api/documents/{document_id}")
    assert detail.status_code == 200
    assert detail.json()["data"]["status"] == "UPLOADED"

    job_detail = client.get(f"/api/documents/ingestion-jobs/{job['id']}")
    assert job_detail.status_code == 200
    assert job_detail.json()["data"]["status"] == "QUEUED"

    anyio.run(documents_route._run_ingestion_job, job["id"])
    detail = client.get(f"/api/documents/{document_id}")
    assert detail.status_code == 200
    assert detail.json()["data"]["status"] == "INDEXED"

    job_detail = client.get(f"/api/documents/ingestion-jobs/{job['id']}")
    assert job_detail.status_code == 200
    assert job_detail.json()["data"]["status"] == "SUCCEEDED"


def test_document_ingestion_job_endpoint_skips_duplicate_document() -> None:
    """重複文書の手動 job 投入も重複索引を作らず skipped にする。"""
    body = "重複本文".encode()
    original = client.post(
        "/api/documents/upload",
        files={"file": ("original.txt", body, "text/plain")},
    )
    assert original.status_code == 200
    duplicate = client.post(
        "/api/documents/upload",
        files={"file": ("duplicate.txt", body, "text/plain")},
    )
    assert duplicate.status_code == 200
    document_id = duplicate.json()["data"]["id"]

    resp = client.post(f"/api/documents/{document_id}/ingestion-jobs")

    assert resp.status_code == 200
    job = resp.json()["data"]
    assert job["status"] == "SKIPPED"
    assert job["skip_reason"] == "duplicate_content"
    assert "duplicate_content" in job["quality_warnings"]

    detail = client.get(f"/api/documents/{document_id}")
    assert detail.status_code == 200
    assert detail.json()["data"]["status"] == "UPLOADED"


def test_document_ingestion_job_endpoint_skips_indexed_without_force() -> None:
    """既に索引済みの文書は force なしなら再取込せず job 履歴だけ残す。"""
    document_id = _upload(
        "indexed-policy.txt",
        "索引済み本文".encode(),
        "text/plain",
    )
    first = client.post(f"/api/documents/{document_id}/ingestion-jobs")
    assert first.status_code == 200
    anyio.run(documents_route._run_ingestion_job, first.json()["data"]["id"])

    second = client.post(f"/api/documents/{document_id}/ingestion-jobs")

    assert second.status_code == 200
    job = second.json()["data"]
    assert job["status"] == "SKIPPED"
    assert job["skip_reason"] == "already_indexed"


def test_document_ingestion_job_endpoint_skips_unsupported_audio() -> None:
    """未対応 audio の手動 job 投入も skipped 履歴だけ残す。"""
    document_id = _upload("voice.mp3", b"ID3", "audio/mpeg")

    resp = client.post(f"/api/documents/{document_id}/ingestion-jobs")

    assert resp.status_code == 200
    job = resp.json()["data"]
    assert job["status"] == "SKIPPED"
    assert job["parser_profile"] == "unsupported_audio"
    assert job["skip_reason"] == "audio_transcription_not_configured"
    assert "unsupported_audio" in job["quality_warnings"]

    detail = client.get(f"/api/documents/{document_id}")
    assert detail.status_code == 200
    assert detail.json()["data"]["status"] == "UPLOADED"


def test_document_ingestion_job_endpoint_skips_unsupported_outlook_msg() -> None:
    """未対応 Outlook MSG の手動 job 投入も skipped 履歴だけ残す。"""
    document_id = _upload(
        "approval.msg",
        b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1outlook msg",
        "application/octet-stream",
    )

    resp = client.post(f"/api/documents/{document_id}/ingestion-jobs")

    assert resp.status_code == 200
    job = resp.json()["data"]
    assert job["status"] == "SKIPPED"
    assert job["parser_profile"] == "unsupported_outlook_msg"
    assert job["skip_reason"] == "outlook_msg_not_supported"
    assert "unsupported_outlook_msg" in job["quality_warnings"]

    detail = client.get(f"/api/documents/{document_id}")
    assert detail.status_code == 200
    assert detail.json()["data"]["status"] == "UPLOADED"


def test_document_ingestion_job_endpoint_skips_unsupported_tiff_image() -> None:
    """TIFF 画像の手動 job 投入も skipped 履歴だけ残す。"""
    document_id = _upload("scan.tiff", b"II*\x00tiff", "image/tiff")

    resp = client.post(f"/api/documents/{document_id}/ingestion-jobs")

    assert resp.status_code == 200
    job = resp.json()["data"]
    assert job["status"] == "SKIPPED"
    assert job["parser_profile"] == "unsupported_tiff_image"
    assert job["skip_reason"] == "tiff_image_not_supported"
    assert "unsupported_tiff_image" in job["quality_warnings"]

    detail = client.get(f"/api/documents/{document_id}")
    assert detail.status_code == 200
    assert detail.json()["data"]["status"] == "UPLOADED"


def test_document_ingestion_job_endpoint_skips_unsupported_legacy_office() -> None:
    """旧バイナリ Office の手動 job 投入も skipped 履歴だけ残す。"""
    document_id = _upload("legacy.doc", b"legacy office", "application/msword")

    resp = client.post(f"/api/documents/{document_id}/ingestion-jobs")

    assert resp.status_code == 200
    job = resp.json()["data"]
    assert job["status"] == "SKIPPED"
    assert job["parser_profile"] == "unsupported_legacy_office_binary"
    assert job["skip_reason"] == "legacy_office_binary_not_supported"
    assert "unsupported_legacy_office_binary" in job["quality_warnings"]

    detail = client.get(f"/api/documents/{document_id}")
    assert detail.status_code == 200
    assert detail.json()["data"]["status"] == "UPLOADED"


def test_drain_queued_ingestion_jobs_runs_persisted_jobs(
    fake_document_dependencies: FakeWorkspaceOracle,
) -> None:
    """永続化済み QUEUED job は drain API からワーカーへ通知できる。"""
    document_id = _upload(
        "queued-policy.txt",
        "再起動後の queued job".encode(),
        "text/plain",
    )
    queued_job = IngestionJob(
        id="job-queued",
        document_id=document_id,
        status=IngestionJobStatus.QUEUED,
        parser_profile="local_text_structure",
        queued_at=datetime.now(UTC),
    )
    fake_document_dependencies.ingestion_jobs[queued_job.id] = queued_job

    resp = client.post("/api/documents/ingestion-jobs/drain")

    assert resp.status_code == 200
    jobs = resp.json()["data"]
    assert [job["id"] for job in jobs] == ["job-queued"]
    assert jobs[0]["status"] == "QUEUED"

    detail = client.get(f"/api/documents/{document_id}")
    assert detail.status_code == 200
    assert detail.json()["data"]["status"] == "UPLOADED"

    job_detail = client.get("/api/documents/ingestion-jobs/job-queued")
    assert job_detail.status_code == 200
    assert job_detail.json()["data"]["status"] == "QUEUED"

    anyio.run(documents_route._run_ingestion_job, "job-queued")
    detail = client.get(f"/api/documents/{document_id}")
    assert detail.status_code == 200
    assert detail.json()["data"]["status"] == "INDEXED"
    job_detail = client.get("/api/documents/ingestion-jobs/job-queued")
    assert job_detail.status_code == 200
    assert job_detail.json()["data"]["status"] == "SUCCEEDED"
    assert job_detail.json()["data"]["attempt_count"] == 1


def test_retry_ingestion_job_creates_new_job_for_failed_document(
    fake_document_dependencies: FakeWorkspaceOracle,
) -> None:
    """失敗済み job の retry は新しい job として対象文書を再投入する。"""
    document_id = _upload(
        "failed-policy.txt",
        "retry 対象本文".encode(),
        "text/plain",
    )
    failed_job = IngestionJob(
        id="job-failed",
        document_id=document_id,
        status=IngestionJobStatus.FAILED,
        parser_profile="local_text_structure",
        error_message="前回失敗",
        queued_at=datetime.now(UTC),
        finished_at=datetime.now(UTC),
    )
    fake_document_dependencies.ingestion_jobs[failed_job.id] = failed_job

    resp = client.post("/api/documents/ingestion-jobs/job-failed/retry")

    assert resp.status_code == 200
    retry_job = resp.json()["data"]
    assert retry_job["id"] != "job-failed"
    assert retry_job["document_id"] == document_id
    assert retry_job["status"] == "QUEUED"

    job_detail = client.get(f"/api/documents/ingestion-jobs/{retry_job['id']}")
    assert job_detail.status_code == 200
    assert job_detail.json()["data"]["status"] == "QUEUED"

    anyio.run(documents_route._run_ingestion_job, retry_job["id"])
    job_detail = client.get(f"/api/documents/ingestion-jobs/{retry_job['id']}")
    assert job_detail.status_code == 200
    assert job_detail.json()["data"]["status"] == "SUCCEEDED"

    original_job = client.get("/api/documents/ingestion-jobs/job-failed")
    assert original_job.status_code == 200
    assert original_job.json()["data"]["status"] == "FAILED"


def test_retry_ingestion_job_rejects_running_job(
    fake_document_dependencies: FakeWorkspaceOracle,
) -> None:
    """実行中 job の retry は二重実行になるため拒否する。"""
    document_id = _upload(
        "running-policy.txt",
        "実行中本文".encode(),
        "text/plain",
    )
    running_job = IngestionJob(
        id="job-running",
        document_id=document_id,
        status=IngestionJobStatus.RUNNING,
        parser_profile="local_text_structure",
        queued_at=datetime.now(UTC),
        started_at=datetime.now(UTC),
    )
    fake_document_dependencies.ingestion_jobs[running_job.id] = running_job

    resp = client.post("/api/documents/ingestion-jobs/job-running/retry")

    assert resp.status_code == 409
    assert resp.json()["error_messages"] == ["この取込ジョブはまだ実行中です。"]


def test_retry_failed_ingestion_segments_creates_retry_job(
    fake_document_dependencies: FakeWorkspaceOracle,
) -> None:
    """FAILED checkpoint がある場合だけ segment retry job を投入する。"""
    document_id = _upload(
        "segment-failed.pdf",
        b"%PDF-1.4\nsegment retry target",
        "application/pdf",
    )
    fake_document_dependencies.ingestion_segments[document_id] = [
        IngestionSegment(
            segment_id=f"{document_id}:p1-2",
            document_id=document_id,
            status="SUCCEEDED",
            parser_backend="enterprise_ai",
            parser_profile="enterprise_ai_pdf_layout",
            page_start=1,
            page_end=2,
            artifact_path=f"oci://namespace/bucket/artifacts/extractions/{document_id}/p1-2.json",
        ),
        IngestionSegment(
            segment_id=f"{document_id}:p3-4",
            document_id=document_id,
            status="FAILED",
            parser_backend="enterprise_ai",
            parser_profile="enterprise_ai_pdf_layout",
            page_start=3,
            page_end=4,
            attempt_count=1,
            error_code="enterprise_ai_response_validation_error",
            error_message="前回の segment 抽出に失敗しました。",
        ),
    ]

    resp = client.post(f"/api/documents/{document_id}/ingestion-segments/retry")

    assert resp.status_code == 200
    retry_job = resp.json()["data"]
    assert retry_job["document_id"] == document_id
    assert retry_job["status"] == "QUEUED"
    job_detail = client.get(f"/api/documents/ingestion-jobs/{retry_job['id']}")
    assert job_detail.status_code == 200
    assert job_detail.json()["data"]["status"] == "QUEUED"

    anyio.run(documents_route._run_ingestion_job, retry_job["id"])
    job_detail = client.get(f"/api/documents/ingestion-jobs/{retry_job['id']}")
    assert job_detail.status_code == 200
    assert job_detail.json()["data"]["status"] == "SUCCEEDED"


def test_retry_failed_ingestion_segments_requires_failed_checkpoint(
    fake_document_dependencies: FakeWorkspaceOracle,
) -> None:
    """FAILED checkpoint がない文書は segment retry として受け付けない。"""
    document_id = _upload(
        "segment-ok.pdf",
        b"%PDF-1.4\nno failed segment",
        "application/pdf",
    )
    fake_document_dependencies.ingestion_segments[document_id] = [
        IngestionSegment(
            segment_id=f"{document_id}:p1-2",
            document_id=document_id,
            status="SUCCEEDED",
            parser_backend="enterprise_ai",
            parser_profile="enterprise_ai_pdf_layout",
            page_start=1,
            page_end=2,
            artifact_path=f"oci://namespace/bucket/artifacts/extractions/{document_id}/p1-2.json",
        )
    ]

    resp = client.post(f"/api/documents/{document_id}/ingestion-segments/retry")

    assert resp.status_code == 409
    assert resp.json()["error_messages"] == ["再試行対象の失敗 segment がありません。"]


def test_cancel_queued_ingestion_job(
    fake_document_dependencies: FakeWorkspaceOracle,
) -> None:
    """待機中 job は CANCELLED にでき、drain 対象から外れる。"""
    document_id = _upload(
        "cancel-queued-policy.txt",
        "cancel queued 本文".encode(),
        "text/plain",
    )
    queued_job = IngestionJob(
        id="job-cancel-queued",
        document_id=document_id,
        status=IngestionJobStatus.QUEUED,
        parser_profile="local_text_structure",
        queued_at=datetime.now(UTC),
    )
    fake_document_dependencies.ingestion_jobs[queued_job.id] = queued_job

    resp = client.post("/api/documents/ingestion-jobs/job-cancel-queued/cancel")

    assert resp.status_code == 200
    cancelled = resp.json()["data"]
    assert cancelled["status"] == "CANCELLED"
    assert cancelled["error_message"] == documents_route.INGESTION_JOB_CANCELLED_MESSAGE
    assert cancelled["finished_at"] is not None

    drain = client.post("/api/documents/ingestion-jobs/drain")
    assert drain.status_code == 200
    assert drain.json()["data"] == []


def test_cancel_running_ingestion_job(
    fake_document_dependencies: FakeWorkspaceOracle,
) -> None:
    """実行中 job は cancellation requested として CANCELLED にできる。"""
    document_id = _upload(
        "cancel-running-policy.txt",
        "cancel running 本文".encode(),
        "text/plain",
    )
    running_job = IngestionJob(
        id="job-cancel-running",
        document_id=document_id,
        status=IngestionJobStatus.RUNNING,
        parser_profile="local_text_structure",
        queued_at=datetime.now(UTC),
        started_at=datetime.now(UTC),
    )
    fake_document_dependencies.ingestion_jobs[running_job.id] = running_job
    fake_document_dependencies.documents[document_id] = fake_document_dependencies.documents[
        document_id
    ].model_copy(update={"status": FileStatus.INGESTING})

    resp = client.post("/api/documents/ingestion-jobs/job-cancel-running/cancel")

    assert resp.status_code == 200
    cancelled = resp.json()["data"]
    assert cancelled["status"] == "CANCELLED"
    assert cancelled["error_message"] == documents_route.INGESTION_JOB_CANCELLED_MESSAGE
    assert fake_document_dependencies.documents[document_id].status == FileStatus.UPLOADED


def test_cancel_ingestion_job_rejects_terminal_status(
    fake_document_dependencies: FakeWorkspaceOracle,
) -> None:
    """完了済み job の cancel は履歴破壊を避けて拒否する。"""
    document_id = _upload(
        "cancel-terminal-policy.txt",
        "cancel terminal 本文".encode(),
        "text/plain",
    )
    succeeded_job = IngestionJob(
        id="job-cancel-terminal",
        document_id=document_id,
        status=IngestionJobStatus.SUCCEEDED,
        parser_profile="local_text_structure",
        queued_at=datetime.now(UTC),
        finished_at=datetime.now(UTC),
    )
    fake_document_dependencies.ingestion_jobs[succeeded_job.id] = succeeded_job

    resp = client.post("/api/documents/ingestion-jobs/job-cancel-terminal/cancel")

    assert resp.status_code == 409
    assert resp.json()["error_messages"] == ["この取込ジョブはキャンセルできません。"]
    assert fake_document_dependencies.ingestion_jobs[succeeded_job.id].status == (
        IngestionJobStatus.SUCCEEDED
    )


def test_running_ingestion_job_does_not_overwrite_cancelled_status(
    fake_document_dependencies: FakeWorkspaceOracle,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """worker 終了時に、途中で cancel された job を SUCCEEDED で上書きしない。"""
    document_id = _upload(
        "cancel-race-policy.txt",
        "cancel race 本文".encode(),
        "text/plain",
    )
    queued_job = IngestionJob(
        id="job-cancel-race",
        document_id=document_id,
        status=IngestionJobStatus.QUEUED,
        parser_profile="local_text_structure",
        queued_at=datetime.now(UTC),
    )
    fake_document_dependencies.ingestion_jobs[queued_job.id] = queued_job

    async def cancel_during_ingest(
        document_id: str,
        *,
        force: bool = False,
        cancel_checker: object | None = None,
    ) -> DocumentDetail:
        _ = force, cancel_checker
        job = fake_document_dependencies.ingestion_jobs[queued_job.id]
        fake_document_dependencies.ingestion_jobs[queued_job.id] = job.model_copy(
            update={
                "status": IngestionJobStatus.CANCELLED,
                "error_message": documents_route.INGESTION_JOB_CANCELLED_MESSAGE,
                "finished_at": datetime.now(UTC),
            }
        )
        detail = await fake_document_dependencies.get_document(document_id)
        assert detail is not None
        return detail

    monkeypatch.setattr(documents_route, "_ingest_existing_document", cancel_during_ingest)

    anyio.run(documents_route._run_ingestion_job, queued_job.id)

    assert fake_document_dependencies.ingestion_jobs[queued_job.id].status == (
        IngestionJobStatus.CANCELLED
    )


def test_recover_and_drain_ingestion_jobs_recovers_stale_running_jobs(
    fake_document_dependencies: FakeWorkspaceOracle,
) -> None:
    """stale RUNNING job は再キューされ、上限到達 job は FAILED に残る。"""
    runnable_document_id = _upload(
        "stale-policy.txt",
        "stale job 本文".encode(),
        "text/plain",
    )
    maxed_document_id = _upload(
        "maxed-policy.txt",
        "maxed job 本文".encode(),
        "text/plain",
    )
    old_started_at = datetime(2026, 1, 1, tzinfo=UTC)
    stale_job = IngestionJob(
        id="job-stale",
        document_id=runnable_document_id,
        status=IngestionJobStatus.RUNNING,
        parser_profile="local_text_structure",
        attempt_count=1,
        max_attempts=3,
        queued_at=old_started_at,
        started_at=old_started_at,
    )
    maxed_job = IngestionJob(
        id="job-maxed",
        document_id=maxed_document_id,
        status=IngestionJobStatus.RUNNING,
        parser_profile="local_text_structure",
        attempt_count=3,
        max_attempts=3,
        queued_at=old_started_at,
        started_at=old_started_at,
    )
    fake_document_dependencies.ingestion_jobs[stale_job.id] = stale_job
    fake_document_dependencies.ingestion_jobs[maxed_job.id] = maxed_job

    async def run_recovery() -> list[IngestionJob]:
        return await documents_route.recover_and_drain_ingestion_jobs(
            limit=10,
            stale_running_seconds=1.0,
            concurrency=2,
        )

    drained = anyio.run(run_recovery)

    assert [job.id for job in drained] == ["job-stale"]
    assert fake_document_dependencies.ingestion_jobs["job-stale"].status == (
        IngestionJobStatus.SUCCEEDED
    )
    assert fake_document_dependencies.ingestion_jobs["job-stale"].attempt_count == 2
    assert fake_document_dependencies.ingestion_jobs["job-maxed"].status == (
        IngestionJobStatus.FAILED
    )


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


def test_document_content_sets_utf8_charset_for_text() -> None:
    """UTF-8 テキストは charset=utf-8 を付与して配信する。"""
    body = "社内規程 経費申請 承認フロー".encode()
    document_id = _upload("policy.txt", body, "text/plain")

    resp = client.get(f"/api/documents/{document_id}/content")

    assert resp.status_code == 200
    assert resp.content == body
    assert "charset=utf-8" in resp.headers["content-type"].lower()


# WHATWG (TextDecoder) が受理する代表的なラベル
_WHATWG_VALID_LABELS = {"utf-8", "shift_jis", "euc-jp", "gbk", "gb18030", "euc-kr", "big5"}


def test_document_content_detects_non_utf8_charset() -> None:
    """非 UTF-8 テキスト(Shift_JIS)は文字コードを検出して charset を付与する。"""
    plain = (
        "となりのトトロは宮崎駿監督の長編アニメーション映画である。"
        "昭和三十年代の日本の農村を舞台に、姉妹とトトロの交流を描いた作品。"
    )
    body = (plain * 8).encode("shift_jis")
    document_id = _upload("totoro.txt", body, "text/plain")

    resp = client.get(f"/api/documents/{document_id}/content")

    assert resp.status_code == 200
    # 原本 bytes は無改変で配信する
    assert resp.content == body
    content_type = resp.headers["content-type"].lower()
    assert "charset=" in content_type
    charset = content_type.split("charset=", 1)[1].split(";", 1)[0].strip()
    # 非 UTF-8 を検出し、ブラウザ TextDecoder 互換ラベルで配信する
    assert charset != "utf-8"
    assert charset in _WHATWG_VALID_LABELS
    # 配信した charset でデコードすると元のテキストへ戻る
    assert resp.content.decode(charset) == plain * 8


def test_document_content_returns_404_for_unknown_document() -> None:
    """存在しないドキュメントの原本配信は 404。"""
    resp = client.get("/api/documents/unknown/content")

    assert resp.status_code == 404
    assert resp.json()["error_messages"] == ["ドキュメントが見つかりません。"]


def test_ingestion_segments_fallback_uses_table_and_asset_pages(
    fake_document_dependencies: FakeWorkspaceOracle,
) -> None:
    """checkpoint 未永続化でも table/asset lineage から page range を返す。"""
    document_id = _upload("layout.pdf", b"%PDF-1.7\nsample", "application/pdf")
    artifact_path = f"artifacts/extractions/{document_id}/full.json"
    detail = fake_document_dependencies.documents[document_id]
    fake_document_dependencies.documents[document_id] = detail.model_copy(
        update={
            "status": FileStatus.INDEXED,
            "indexed_at": datetime.now(UTC),
            "extraction": {
                "raw_text": "売上表と説明図を含む PDF",
                "elements": [],
                "tables": [
                    {
                        "table_id": "tbl-1",
                        "page_number": 3,
                        "caption": "四半期売上",
                    }
                ],
                "assets": [
                    {
                        "asset_id": "fig-1",
                        "kind": "figure",
                        "page_number": 5,
                    }
                ],
                "quality_report": {
                    "parser_backend": "docling",
                    "parser_profile": "enterprise_ai_pdf_layout",
                },
                "parser_artifacts": {
                    "extraction_artifact_path": artifact_path,
                },
            },
        }
    )

    resp = client.get(f"/api/documents/{document_id}/ingestion-segments")

    assert resp.status_code == 200
    segments = resp.json()["data"]
    assert len(segments) == 1
    assert segments[0]["page_start"] == 3
    assert segments[0]["page_end"] == 5
    assert segments[0]["parser_backend"] == "docling"
    assert segments[0]["artifact_path"] == artifact_path


def test_delete_document_removes_record_and_original_file(
    fake_document_dependencies: FakeWorkspaceOracle,
) -> None:
    """document 削除 API は DB record とアップロード原本を削除する。"""
    body = b"sample policy"
    document_id = _upload("policy.txt", body, "text/plain")
    detail = fake_document_dependencies.documents[document_id]
    assert detail.object_storage_path is not None
    assert anyio.run(ObjectStorageClient().get, detail.object_storage_path) == body

    resp = client.delete(f"/api/documents/{document_id}")

    assert resp.status_code == 200
    data = resp.json()["data"]
    assert data["id"] == document_id
    assert data["file_name"] == "policy.txt"
    assert data["object_deleted"] is True
    assert document_id not in fake_document_dependencies.documents
    with pytest.raises(FileNotFoundError):
        anyio.run(ObjectStorageClient().get, detail.object_storage_path)
    assert client.get(f"/api/documents/{document_id}").status_code == 404


def test_delete_document_removes_extraction_artifacts(
    fake_document_dependencies: FakeWorkspaceOracle,
) -> None:
    """document 削除 API は抽出 artifact cache と segment artifact も best-effort で削除する。"""
    document_id = _upload("policy.txt", b"sample policy", "text/plain")
    full_artifact_path = anyio.run(
        ObjectStorageClient().put,
        f"artifacts/extractions/{document_id}/full.json",
        b'{"raw_text":"redacted"}',
        "application/json",
    )
    segment_artifact_path = anyio.run(
        ObjectStorageClient().put,
        f"artifacts/extractions/{document_id}/segments/p1.json",
        b'{"raw_text":"redacted segment"}',
        "application/json",
    )
    duplicate_segment_artifact_path = full_artifact_path
    original_path = fake_document_dependencies.documents[document_id].object_storage_path
    assert original_path is not None
    fake_document_dependencies.documents[document_id] = fake_document_dependencies.documents[
        document_id
    ].model_copy(
        update={
            "status": FileStatus.INDEXED,
            "extraction": {"parser_artifacts": {"extraction_artifact_path": full_artifact_path}},
        }
    )
    fake_document_dependencies.ingestion_segments[document_id] = [
        IngestionSegment(
            segment_id=f"{document_id}:p1",
            document_id=document_id,
            status="SUCCEEDED",
            artifact_path=segment_artifact_path,
        ),
        IngestionSegment(
            segment_id=f"{document_id}:full",
            document_id=document_id,
            status="SUCCEEDED",
            artifact_path=duplicate_segment_artifact_path,
        ),
        IngestionSegment(
            segment_id=f"{document_id}:source",
            document_id=document_id,
            status="SUCCEEDED",
            artifact_path=original_path,
        ),
    ]

    resp = client.delete(f"/api/documents/{document_id}")

    assert resp.status_code == 200
    data = resp.json()["data"]
    assert data["object_deleted"] is True
    assert data["artifact_deleted_count"] == 2
    assert data["artifact_delete_failed_count"] == 0
    with pytest.raises(FileNotFoundError):
        anyio.run(ObjectStorageClient().get, full_artifact_path)
    with pytest.raises(FileNotFoundError):
        anyio.run(ObjectStorageClient().get, segment_artifact_path)
    with pytest.raises(FileNotFoundError):
        anyio.run(ObjectStorageClient().get, original_path)


def test_delete_document_removes_queued_ingestion_state(
    fake_document_dependencies: FakeWorkspaceOracle,
) -> None:
    """待機中の投入 job / segment は document 削除と同時に消す。"""
    document_id = _upload("queued-policy.txt", b"queued sample", "text/plain")
    fake_document_dependencies.ingestion_jobs["job-queued-delete"] = IngestionJob(
        id="job-queued-delete",
        document_id=document_id,
        status=IngestionJobStatus.QUEUED,
        parser_profile="enterprise_ai_generic",
        queued_at=datetime.now(UTC),
    )
    fake_document_dependencies.ingestion_segments[document_id] = [
        IngestionSegment(
            segment_id=f"{document_id}:p1",
            document_id=document_id,
            status="QUEUED",
            page_start=1,
            page_end=1,
        )
    ]

    resp = client.delete(f"/api/documents/{document_id}")

    assert resp.status_code == 200
    assert document_id not in fake_document_dependencies.documents
    assert "job-queued-delete" not in fake_document_dependencies.ingestion_jobs
    assert document_id not in fake_document_dependencies.ingestion_segments


def test_delete_document_blocks_running_ingestion_job(
    fake_document_dependencies: FakeWorkspaceOracle,
) -> None:
    """実行中の取込 job がある document は worker 競合を避けて削除を止める。"""
    document_id = _upload("policy.txt", b"sample", "text/plain")
    fake_document_dependencies.ingestion_jobs["job-running"] = IngestionJob(
        id="job-running",
        document_id=document_id,
        status=IngestionJobStatus.RUNNING,
        parser_profile="enterprise_ai_generic",
        queued_at=datetime.now(UTC),
        started_at=datetime.now(UTC),
    )

    resp = client.delete(f"/api/documents/{document_id}")

    assert resp.status_code == 409
    assert "先にキャンセルしてください" in resp.json()["error_messages"][0]
    assert document_id in fake_document_dependencies.documents


def test_delete_document_clears_duplicate_references(
    fake_document_dependencies: FakeWorkspaceOracle,
) -> None:
    """重複元 document を削除すると、残る重複 document の参照を外す。"""
    original_id = _upload("original.txt", b"same body", "text/plain")
    duplicate_id = _upload("duplicate.txt", b"same body", "text/plain")
    assert (
        fake_document_dependencies.documents[duplicate_id].duplicate_of_document_id == original_id
    )

    resp = client.delete(f"/api/documents/{original_id}")

    assert resp.status_code == 200
    assert fake_document_dependencies.documents[duplicate_id].duplicate_of_document_id is None


def test_document_detail_returns_extraction_after_ingest() -> None:
    """取込 job 実行後の詳細 API は抽出本文とメタデータを返す。"""
    document_id = _upload(
        "policy.txt",
        "社内規程: 経費申請\n部門長が承認します。".encode(),
        "text/plain",
    )
    ingest = client.post(f"/api/documents/{document_id}/ingest")
    assert ingest.status_code == 200
    job = ingest.json()["data"]
    assert job["document_id"] == document_id
    assert job["status"] == "QUEUED"
    anyio.run(documents_route._run_ingestion_job, job["id"])

    detail = client.get(f"/api/documents/{document_id}")
    assert detail.status_code == 200
    extraction = detail.json()["data"]["extraction"]
    assert extraction["document_type"] == "社内規程"
    assert "部門長が承認" in extraction["raw_text"]
    assert "fields" not in extraction

    jobs = client.get("/api/documents/ingestion-jobs", params={"status": "SUCCEEDED"})
    assert jobs.status_code == 200
    assert any(job["document_id"] == document_id for job in jobs.json()["data"]["items"])


def test_document_extraction_export_returns_markdown_view(
    fake_document_dependencies: FakeWorkspaceOracle,
) -> None:
    """保存済み StructuredExtraction を Markdown で監査できる。"""
    document_id = _upload("manual.html", "<h1>検索運用</h1>".encode(), "text/html")
    detail = fake_document_dependencies.documents[document_id]
    fake_document_dependencies.documents[document_id] = detail.model_copy(
        update={
            "status": FileStatus.INDEXED,
            "extraction": {
                "raw_text": "検索運用\nインデックスを確認します。",
                "document_type": "運用マニュアル",
                "confidence": 0.91,
                "elements": [
                    {
                        "kind": "title",
                        "text": "検索運用",
                        "order": 0,
                        "element_id": "h1",
                        "page_number": 1,
                        "section_path": ["検索運用"],
                    },
                    {
                        "kind": "code",
                        "text": "ragctl reindex",
                        "order": 1,
                        "element_id": "code-1",
                        "page_number": 1,
                        "section_path": ["検索運用"],
                        "metadata": {"code_language": "bash"},
                    },
                    {
                        "kind": "table",
                        "text": "|項目|値|\n|-|-|\n|状態|INDEXED|",
                        "order": 2,
                        "element_id": "tbl-1",
                        "page_number": 2,
                        "section_path": ["検索運用", "確認表"],
                    },
                ],
                "assets": [
                    {
                        "asset_id": "fig-1",
                        "kind": "figure",
                        "page_number": 2,
                        "bbox": {"x": 10, "y": 20, "w": 30, "h": 40},
                        "alt_text": "検索フロー図",
                    }
                ],
                "quality_report": {
                    "parser_backend": "local_partition",
                    "parser_profile": "local_html_semantic",
                    "page_count": 2,
                    "page_coverage": 1.0,
                    "element_count": 3,
                },
            },
        }
    )

    resp = client.get(
        f"/api/documents/{document_id}/extraction-export",
        params={"format": DocumentExtractionExportFormat.MARKDOWN.value},
    )

    assert resp.status_code == 200
    data = resp.json()["data"]
    assert data["format"] == "markdown"
    assert data["content_type"].startswith("text/markdown")
    assert data["parser_backend"] == "local_partition"
    assert data["parser_profile"] == "local_html_semantic"
    assert data["page_count"] == 2
    assert data["element_count"] == 3
    assert data["asset_count"] == 1
    assert "<!-- page: 1 -->" in data["content"]
    assert "# 検索運用" in data["content"]
    assert "```bash\nragctl reindex\n```" in data["content"]
    assert "|状態|INDEXED|" in data["content"]
    assert "> Asset: figure `fig-1`" in data["content"]
    assert "> bbox: 10,20,40,60" in data["content"]
    assert "> alt: 検索フロー図" in data["content"]
    assert data["payload"] == {}


def test_document_extraction_export_returns_html_view(
    fake_document_dependencies: FakeWorkspaceOracle,
) -> None:
    """HTML export は保存済み extraction から escaped review HTML を返す。"""
    document_id = _upload("manual.html", b"<h1>html</h1>", "text/html")
    detail = fake_document_dependencies.documents[document_id]
    fake_document_dependencies.documents[document_id] = detail.model_copy(
        update={
            "status": FileStatus.INDEXED,
            "extraction": {
                "raw_text": "検索運用\n<script>alert(1)</script>",
                "document_type": "運用マニュアル",
                "elements": [
                    {
                        "kind": "title",
                        "text": "検索運用",
                        "order": 0,
                        "element_id": "title-1",
                        "page_number": 1,
                        "bbox": [0, 0, 100, 20],
                    },
                    {
                        "kind": "text",
                        "text": "<script>alert(1)</script>",
                        "order": 1,
                        "element_id": "txt-1",
                        "page_number": 1,
                        "content_kind": "text",
                    },
                    {
                        "kind": "code",
                        "text": "ragctl reindex",
                        "order": 2,
                        "element_id": "code-1",
                        "page_number": 1,
                        "metadata": {"code_language": "bash"},
                    },
                    {
                        "kind": "table",
                        "text": "|項目|値|\n|-|-|\n|状態|INDEXED|",
                        "order": 3,
                        "element_id": "tbl-1",
                        "page_number": 2,
                    },
                ],
                "tables": [
                    {
                        "table_id": "tbl-1",
                        "element_id": "tbl-1",
                        "page_number": 2,
                        "caption": "検索状態表",
                        "cells": [
                            {"row": 0, "col": 0, "text": "項目"},
                            {"row": 0, "col": 1, "text": "値"},
                            {
                                "row": 1,
                                "col": 0,
                                "text": "状態",
                                "bbox": {
                                    "left": 10,
                                    "top": 20,
                                    "right": 30,
                                    "bottom": 40,
                                },
                            },
                            {
                                "row": 1,
                                "col": 1,
                                "text": "<INDEXED>",
                                "metadata": {
                                    "formula_cell_ref": "B2",
                                    "equation_format": "excel_formula",
                                    "formula": 'IF(A2="状態","<INDEXED>","ERROR")',
                                    "formula_value": "<INDEXED>",
                                },
                            },
                        ],
                    }
                ],
                "assets": [
                    {
                        "asset_id": "fig-1",
                        "kind": "figure",
                        "object_path": "oci://namespace/bucket/internal/figure.png",
                        "page_number": 2,
                        "bbox": {"x": 5, "y": 10, "w": 25, "h": 35},
                        "alt_text": "<画像プレビュー>",
                    }
                ],
                "quality_report": {
                    "parser_backend": "local_partition",
                    "parser_profile": "local_html_semantic",
                    "page_count": 2,
                    "page_coverage": 1.0,
                    "element_count": 4,
                },
            },
        }
    )

    resp = client.get(
        f"/api/documents/{document_id}/extraction-export",
        params={"format": DocumentExtractionExportFormat.HTML.value},
    )

    assert resp.status_code == 200
    data = resp.json()["data"]
    assert data["format"] == "html"
    assert data["content_type"].startswith("text/html")
    assert data["payload"] == {}
    assert '<article data-document-type="運用マニュアル">' in data["content"]
    assert '<p class="page-marker" data-page="1">page 1</p>' in data["content"]
    assert '<h1 data-element-id="title-1"' in data["content"]
    assert 'data-bbox="0,0,100,20"' in data["content"]
    assert ">検索運用</h1>" in data["content"]
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in data["content"]
    assert "<script>alert(1)</script>" not in data["content"]
    assert '<code class="language-bash">ragctl reindex</code>' in data["content"]
    assert '<p class="table-caption" data-table-id="tbl-1">検索状態表</p>' in data["content"]
    assert '<table data-element-id="tbl-1"' in data["content"]
    assert 'class="table-block" data-table-id="tbl-1"' in data["content"]
    assert '<th data-table-id="tbl-1" data-row="0" data-col="0">項目</th>' in data["content"]
    assert 'data-bbox="10,20,30,40"' in data["content"]
    assert 'data-formula-ref="B2"' in data["content"]
    assert 'data-formula-format="excel_formula"' in data["content"]
    escaped_formula = (
        'data-formula="IF(A2=&quot;状態&quot;,&quot;&lt;INDEXED&gt;&quot;,&quot;ERROR&quot;)"'
    )
    assert escaped_formula in data["content"]
    assert 'data-formula-value="&lt;INDEXED&gt;"' in data["content"]
    assert 'class="asset-block"' in data["content"]
    assert 'data-asset-id="fig-1"' in data["content"]
    assert 'data-kind="figure"' in data["content"]
    assert 'data-page="2"' in data["content"]
    assert 'data-bbox="5,10,30,45"' in data["content"]
    assert "&lt;画像プレビュー&gt;" in data["content"]
    assert "oci://namespace/bucket/internal/figure.png" not in data["content"]
    assert "&lt;INDEXED&gt;" in data["content"]
    assert "<INDEXED>" not in data["content"]


def test_document_extraction_export_returns_json_payload(
    fake_document_dependencies: FakeWorkspaceOracle,
) -> None:
    """JSON export は保存済み extraction payload を機械可読に返す。"""
    document_id = _upload("policy.txt", b"policy", "text/plain")
    detail = fake_document_dependencies.documents[document_id]
    fake_document_dependencies.documents[document_id] = detail.model_copy(
        update={
            "status": FileStatus.INDEXED,
            "extraction": {
                "raw_text": "部門長が承認します。",
                "document_type": "社内規程",
                "confidence": 0.9,
                "elements": [
                    {
                        "kind": "text",
                        "text": "部門長が承認します。",
                        "order": 0,
                        "element_id": "el-1",
                    }
                ],
                "parser_artifacts": {"parser_backend": "local_partition"},
            },
        }
    )

    resp = client.get(
        f"/api/documents/{document_id}/extraction-export",
        params={"format": DocumentExtractionExportFormat.JSON.value},
    )

    assert resp.status_code == 200
    data = resp.json()["data"]
    assert data["format"] == "json"
    assert data["content_type"].startswith("application/json")
    assert data["payload"]["document_type"] == "社内規程"
    assert data["payload"]["elements"][0]["element_id"] == "el-1"
    assert '"document_type": "社内規程"' in data["content"]


def test_document_extraction_export_returns_chunk_view_without_embeddings(
    fake_document_dependencies: FakeWorkspaceOracle,
) -> None:
    """chunks export は embedding なしの可視化 metadata だけを返す。"""
    document_id = _upload("policy.txt", b"policy", "text/plain")
    detail = fake_document_dependencies.documents[document_id]
    fake_document_dependencies.documents[document_id] = detail.model_copy(
        update={
            "status": FileStatus.INDEXED,
            "extraction": {"raw_text": "承認条件", "document_type": "社内規程"},
        }
    )
    fake_document_dependencies.chunks[document_id] = [
        DocumentChunkView(
            document_id=document_id,
            chunk_id=f"{document_id}:0",
            chunk_index=0,
            text="承認条件: 部門長",
            page_start=1,
            page_end=1,
            section_path="経費申請 > 承認",
            content_kind="text",
            element_ids=["el-1"],
            metadata={"chunk_template": "markdown_by_heading"},
        )
    ]

    resp = client.get(
        f"/api/documents/{document_id}/extraction-export",
        params={"format": DocumentExtractionExportFormat.CHUNKS.value},
    )

    assert resp.status_code == 200
    data = resp.json()["data"]
    assert data["format"] == "chunks"
    assert data["content_type"].startswith("application/json")
    assert data["chunks"][0]["chunk_id"] == f"{document_id}:0"
    assert data["chunks"][0]["element_ids"] == ["el-1"]
    assert "embedding" not in data["content"].casefold()


def test_fields_edit_endpoint_is_not_available() -> None:
    """帳票向けの抽出フィールド編集 endpoint は提供しない。"""
    document_id = _upload("policy.txt", b"sample", "text/plain")

    resp = client.patch(
        f"/api/documents/{document_id}/fields",
        json={"fields": {"document_number": "DOC-001"}},
    )

    assert resp.status_code == 404
