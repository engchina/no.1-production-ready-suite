"""文書プレビュー（原本配信）と抽出本文表示用 API のテスト。"""

from datetime import UTC, datetime
from uuid import uuid4

import anyio
import pytest

from app.api.routes import documents as documents_route
from app.clients.object_storage import ObjectStorageClient
from app.main import app
from app.schemas.document import (
    DocumentDetail,
    DocumentSummary,
    FileStatus,
    IngestionJob,
    IngestionJobStatus,
)
from app.schemas.knowledge_base import KnowledgeBaseRef
from tests.support import AsgiTestClient

client = AsgiTestClient(app)


class FakeWorkspaceOracle:
    """文書 workspace API テスト用のインメモリ Oracle fake。"""

    def __init__(self) -> None:
        self.documents: dict[str, DocumentDetail] = {}
        self.ingestion_jobs: dict[str, IngestionJob] = {}

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
        return detail

    async def get_document(self, document_id: str) -> DocumentDetail | None:
        return self.documents.get(document_id)

    async def delete_document(self, document_id: str) -> bool:
        if document_id not in self.documents:
            return False
        del self.documents[document_id]
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

    def __init__(self, *, oracle: FakeWorkspaceOracle) -> None:
        self._oracle = oracle

    async def ingest(
        self,
        document_id: str,
        image_bytes: bytes,
        prompt: str,
        *,
        content_type: str = "application/octet-stream",
        source_profile: object | None = None,
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
    assert profile["parser_profile"] == "enterprise_ai_text_structure"
    assert profile["text_charset"] == "utf-8"
    assert profile["quality_status"] == "ready"
    assert profile["quality_warnings"] == []


def test_document_upload_auto_ingestion_starts_background_pipeline() -> None:
    """auto 指定時は upload 後に取込パイプラインを開始する。"""
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
    assert body["ingestion_job"]["parser_profile"] == "enterprise_ai_text_structure"

    detail = client.get(f"/api/documents/{body['id']}")
    assert detail.status_code == 200
    indexed = detail.json()["data"]
    assert indexed["status"] == "INDEXED"
    assert "部門長が承認" in indexed["extraction"]["raw_text"]

    job_detail = client.get(f"/api/documents/ingestion-jobs/{body['ingestion_job']['id']}")
    assert job_detail.status_code == 200
    assert job_detail.json()["data"]["status"] == "SUCCEEDED"
    assert job_detail.json()["data"]["attempt_count"] == 1


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
    assert {job["status"] for job in listed} == {"SUCCEEDED"}
    assert {job["parser_profile"] for job in listed} == {"enterprise_ai_text_structure"}

    for item in data["items"]:
        detail = client.get(f"/api/documents/{item['id']}")
        assert detail.status_code == 200
        assert detail.json()["data"]["status"] == "INDEXED"


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
    assert job["parser_profile"] == "enterprise_ai_text_structure"

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

    second = client.post(f"/api/documents/{document_id}/ingestion-jobs")

    assert second.status_code == 200
    job = second.json()["data"]
    assert job["status"] == "SKIPPED"
    assert job["skip_reason"] == "already_indexed"


def test_drain_queued_ingestion_jobs_runs_persisted_jobs(
    fake_document_dependencies: FakeWorkspaceOracle,
) -> None:
    """永続化済み QUEUED job は drain API から再実行できる。"""
    document_id = _upload(
        "queued-policy.txt",
        "再起動後の queued job".encode(),
        "text/plain",
    )
    queued_job = IngestionJob(
        id="job-queued",
        document_id=document_id,
        status=IngestionJobStatus.QUEUED,
        parser_profile="enterprise_ai_text_structure",
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
        parser_profile="enterprise_ai_text_structure",
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
        parser_profile="enterprise_ai_text_structure",
        queued_at=datetime.now(UTC),
        started_at=datetime.now(UTC),
    )
    fake_document_dependencies.ingestion_jobs[running_job.id] = running_job

    resp = client.post("/api/documents/ingestion-jobs/job-running/retry")

    assert resp.status_code == 409
    assert resp.json()["error_messages"] == ["この取込ジョブはまだ実行中です。"]


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
        parser_profile="enterprise_ai_text_structure",
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
        parser_profile="enterprise_ai_text_structure",
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
        parser_profile="enterprise_ai_text_structure",
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
        parser_profile="enterprise_ai_text_structure",
        queued_at=datetime.now(UTC),
    )
    fake_document_dependencies.ingestion_jobs[queued_job.id] = queued_job

    async def cancel_during_ingest(
        document_id: str,
        *,
        force: bool = False,
    ) -> DocumentDetail:
        _ = force
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
        parser_profile="enterprise_ai_text_structure",
        attempt_count=1,
        max_attempts=3,
        queued_at=old_started_at,
        started_at=old_started_at,
    )
    maxed_job = IngestionJob(
        id="job-maxed",
        document_id=maxed_document_id,
        status=IngestionJobStatus.RUNNING,
        parser_profile="enterprise_ai_text_structure",
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


def test_delete_document_blocks_active_ingestion_job(
    fake_document_dependencies: FakeWorkspaceOracle,
) -> None:
    """未完了の取込 job がある document は誤削除を止める。"""
    document_id = _upload("policy.txt", b"sample", "text/plain")
    fake_document_dependencies.ingestion_jobs["job-queued"] = IngestionJob(
        id="job-queued",
        document_id=document_id,
        status=IngestionJobStatus.QUEUED,
        parser_profile="enterprise_ai_generic",
        queued_at=datetime.now(UTC),
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

    jobs = client.get("/api/documents/ingestion-jobs", params={"status": "SUCCEEDED"})
    assert jobs.status_code == 200
    assert any(job["document_id"] == document_id for job in jobs.json()["data"]["items"])


def test_fields_edit_endpoint_is_not_available() -> None:
    """帳票向けの抽出フィールド編集 endpoint は提供しない。"""
    document_id = _upload("policy.txt", b"sample", "text/plain")

    resp = client.patch(
        f"/api/documents/{document_id}/fields",
        json={"fields": {"document_number": "DOC-001"}},
    )

    assert resp.status_code == 404
