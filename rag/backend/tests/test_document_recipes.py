"""1文書1〜3レシピの境界・工程状態・検索対象契約。"""

from datetime import UTC, datetime
from typing import Any

import pytest
from fastapi import HTTPException

from app.api.routes import documents as documents_route
from app.api.routes.documents import (
    _apply_recipe_review_text_edits,
    _enqueue_failed_segment_retry_job_for_document,
    _materialize_experiment_candidate,
    _recipe_steps,
)
from app.clients.object_storage import ObjectStorageClient
from app.clients.oracle import (
    oracle_chunk_set_schema_sql,
    oracle_document_recipe_schema_sql,
    oracle_ingestion_job_schema_sql,
)
from app.config import Settings
from app.schemas.document import (
    DocumentDetail,
    DocumentPreprocessArtifact,
    DocumentProcessingConfig,
    DocumentRecipeStepStatus,
    DocumentReviewEditsRequest,
    FileStatus,
    IngestionJob,
    IngestionJobPhase,
    IngestionJobStatus,
    IngestionSegment,
)
from app.schemas.extraction import StructuredExtraction


def test_document_recipe_schema_enforces_one_slot_per_document_and_max_three() -> None:
    sql = oracle_document_recipe_schema_sql()
    assert "CHECK (slot_no BETWEEN 1 AND 3)" in sql
    assert "UNIQUE (document_id, slot_no)" in sql
    assert "config_revision" in sql and "NUMBER(10) DEFAULT 1 NOT NULL" in sql
    assert "materialized_revision" in sql


def test_chunk_set_schema_enforces_one_active_output_per_recipe() -> None:
    sql = oracle_chunk_set_schema_sql()
    assert "recipe_id       VARCHAR2(64)" in sql
    assert "is_active       NUMBER(1) DEFAULT 0 NOT NULL" in sql
    assert "CASE WHEN is_active = 1 THEN recipe_id END" in sql


def test_ingestion_job_schema_snapshots_recipe_revision() -> None:
    sql = oracle_ingestion_job_schema_sql()
    assert "recipe_id        VARCHAR2(64)" in sql
    assert "recipe_revision  NUMBER(10)" in sql
    assert "(recipe_id, status, queued_at DESC)" in sql


def test_recipe_steps_keep_failure_isolated_to_its_phase() -> None:
    now = datetime.now(UTC)
    failed = IngestionJob(
        id="job-1",
        document_id="doc-1",
        recipe_id="recipe-2",
        recipe_revision=3,
        status=IngestionJobStatus.FAILED,
        phase=IngestionJobPhase.EXTRACT,
        parser_profile="docling",
        queued_at=now,
        error_message="抽出に失敗しました。",
    )
    steps = _recipe_steps(
        {
            "status": FileStatus.ERROR.value,
            "failed_phase": IngestionJobPhase.EXTRACT.value,
        },
        [failed],
    )
    by_phase = {step.phase: step for step in steps}
    assert by_phase[IngestionJobPhase.EXTRACT].status == DocumentRecipeStepStatus.FAILED
    assert by_phase[IngestionJobPhase.EXTRACT].error_message == "抽出に失敗しました。"
    assert by_phase[IngestionJobPhase.CHUNK].status == DocumentRecipeStepStatus.PENDING
    assert by_phase[IngestionJobPhase.INDEX].status == DocumentRecipeStepStatus.PENDING


def test_indexed_recipe_reports_all_four_steps_succeeded_without_jobs() -> None:
    steps = _recipe_steps({"status": FileStatus.INDEXED.value}, [])
    assert [step.status for step in steps] == [DocumentRecipeStepStatus.SUCCEEDED] * 4


async def test_recipe_segment_retry_creates_extract_job_for_same_recipe(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """recipe 指定 retry は同 recipe の FAILED segment と revision を使う。"""

    class FakeOracle:
        created: IngestionJob | None = None

        async def get_document(self, document_id: str) -> DocumentDetail:
            return DocumentDetail(
                id=document_id,
                file_name="policy.pdf",
                status=FileStatus.ERROR,
                object_storage_path="local://policy.pdf",
                content_sha256="a" * 64,
                uploaded_at=datetime.now(UTC),
            )

        async def get_document_recipe(self, document_id: str, recipe_id: str) -> dict[str, object]:
            return {
                "document_id": document_id,
                "recipe_id": recipe_id,
                "config_revision": 7,
                "processing_config": {},
                "preprocess_artifact": {
                    "derivation_id": "prepared-1",
                    "profile": "passthrough",
                    "file_name": "policy.pdf",
                    "object_storage_path": "local://prepared/policy.pdf",
                },
            }

        async def list_ingestion_segments(self, document_id: str) -> list[IngestionSegment]:
            return [
                IngestionSegment(
                    segment_id=f"{document_id}:recipe-2:p1-2",
                    document_id=document_id,
                    recipe_id="recipe-2",
                    status="FAILED",
                    parser_backend="enterprise_ai",
                    parser_profile="enterprise_ai_pdf_layout",
                ),
                IngestionSegment(
                    segment_id=f"{document_id}:recipe-1:p3-4",
                    document_id=document_id,
                    recipe_id="recipe-1",
                    status="FAILED",
                    parser_backend="enterprise_ai",
                    parser_profile="enterprise_ai_pdf_layout",
                ),
            ]

        async def create_ingestion_job(self, job: IngestionJob) -> IngestionJob:
            self.created = job
            return job

    fake = FakeOracle()
    monkeypatch.setattr(documents_route, "OracleClient", lambda: fake)
    monkeypatch.setattr(documents_route, "_dispatch_ingestion_job", lambda _job_id: None)

    job = await _enqueue_failed_segment_retry_job_for_document("doc-1", recipe_id="recipe-1")

    assert job.recipe_id == "recipe-1"
    assert job.recipe_revision == 7
    assert job.phase == IngestionJobPhase.EXTRACT
    assert fake.created == job


async def test_recipe_segment_retry_ignores_other_recipe_failures(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """別 recipe の FAILED segment だけでは再試行を受け付けない。"""

    class FakeOracle:
        async def get_document(self, document_id: str) -> DocumentDetail:
            return DocumentDetail(
                id=document_id,
                file_name="policy.pdf",
                status=FileStatus.ERROR,
                object_storage_path="local://policy.pdf",
                uploaded_at=datetime.now(UTC),
            )

        async def get_document_recipe(self, document_id: str, recipe_id: str) -> dict[str, object]:
            return {
                "document_id": document_id,
                "recipe_id": recipe_id,
                "preprocess_artifact": {
                    "derivation_id": "prepared-1",
                    "profile": "passthrough",
                    "file_name": "policy.pdf",
                    "object_storage_path": "local://prepared/policy.pdf",
                },
            }

        async def list_ingestion_segments(self, document_id: str) -> list[IngestionSegment]:
            return [
                IngestionSegment(
                    segment_id=f"{document_id}:recipe-2:p1-2",
                    document_id=document_id,
                    recipe_id="recipe-2",
                    status="FAILED",
                    parser_backend="enterprise_ai",
                    parser_profile="enterprise_ai_pdf_layout",
                )
            ]

    monkeypatch.setattr(documents_route, "OracleClient", FakeOracle)

    with pytest.raises(HTTPException) as exc_info:
        await _enqueue_failed_segment_retry_job_for_document("doc-1", recipe_id="recipe-1")

    assert exc_info.value.status_code == 409


async def test_recipe_review_edit_copies_shared_extraction_before_pointer_switch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """既存共有 artifact は変更せず recipe 固有 ID へ copy-on-write する。"""

    class FakeOracle:
        upsert: dict[str, Any] | None = None
        pointer: str | None = None

        async def get_document(self, document_id: str) -> DocumentDetail:
            return DocumentDetail(
                id=document_id,
                file_name="policy.pdf",
                status=FileStatus.REVIEW,
                content_sha256="a" * 64,
                uploaded_at=datetime.now(UTC),
            )

        async def get_document_recipe(self, document_id: str, recipe_id: str) -> dict[str, object]:
            return {
                "document_id": document_id,
                "recipe_id": recipe_id,
                "status": "REVIEW",
                "config_revision": 3,
                "processing_config": {},
                "active_extraction_recipe_id": "er_shared",
            }

        async def get_document_extraction_artifact(self, **_kwargs: object) -> dict[str, object]:
            return {
                "extraction_json": StructuredExtraction(raw_text="共有本文").to_document_payload(),
                "recipe_subset": {},
                "status": "materialized",
            }

        async def upsert_document_extraction_artifact(self, **kwargs: Any) -> None:
            self.upsert = kwargs

        async def update_document_recipe_status(
            self, *, active_extraction_recipe_id: str | None = None, **_kwargs: object
        ) -> None:
            self.pointer = active_extraction_recipe_id

    fake = FakeOracle()
    monkeypatch.setattr(documents_route, "OracleClient", lambda: fake)

    await _apply_recipe_review_text_edits("doc-1", "recipe-1", DocumentReviewEditsRequest())

    assert fake.upsert is not None
    assert fake.upsert["extraction_recipe_id"] != "er_shared"
    assert fake.pointer == fake.upsert["extraction_recipe_id"]


async def test_list_document_recipes_fetches_jobs_once_for_all_recipes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """複数レシピでも取込 job 一覧は1回だけ取得する(N+1 回避)。"""

    def _row(recipe_id: str) -> dict[str, object]:
        now = datetime.now(UTC)
        return {
            "document_id": "doc-1",
            "recipe_id": recipe_id,
            "slot_no": 1,
            "status": FileStatus.INDEXED.value,
            "processing_config": {},
            "config_revision": 1,
            "created_at": now,
            "updated_at": now,
        }

    class FakeOracle:
        job_list_calls = 0

        async def list_document_recipes(self, document_id: str) -> list[dict[str, object]]:
            return [_row("recipe-1"), _row("recipe-2"), _row("recipe-3")]

        async def list_document_ingestion_jobs(
            self, document_id: str, *, status: IngestionJobStatus | None = None
        ) -> list[IngestionJob]:
            self.job_list_calls += 1
            return []

    fake = FakeOracle()
    monkeypatch.setattr(documents_route, "OracleClient", lambda: fake)

    result = await documents_route.list_document_recipes("doc-1")

    assert result.data is not None
    assert len(result.data) == 3
    assert fake.job_list_calls == 1


class _FakeRecipeJobOracle:
    """EXTRACT job の materialize と job ライフサイクルを支える fake。

    ``create_ingestion_job`` に実 Oracle と同じレシピ行ロックのガード(同一レシピに
    QUEUED/RUNNING が居れば拒否)を再現し、自動進行の投入タイミングを検証できるようにする。
    """

    def __init__(self) -> None:
        self.recipe_status = FileStatus.INGESTING
        self.jobs: dict[str, IngestionJob] = {}
        self.config_revision = 1

    async def get_document(self, document_id: str) -> DocumentDetail:
        return DocumentDetail(
            id=document_id,
            file_name="policy.pdf",
            status=FileStatus.INGESTING,
            content_sha256="a" * 64,
            content_type="application/pdf",
            object_storage_path="local://policy.pdf",
            uploaded_at=datetime.now(UTC),
        )

    async def get_document_serving_chunk_set_id(self, document_id: str) -> str | None:
        return None

    async def get_document_processing_config(self, document_id: str) -> DocumentProcessingConfig:
        return DocumentProcessingConfig()

    async def update_document_recipe_status(
        self, *, recipe_id: str, status: FileStatus, **_kwargs: object
    ) -> None:
        self.recipe_status = status

    async def get_document_recipe(self, document_id: str, recipe_id: str) -> dict[str, object]:
        return {
            "document_id": document_id,
            "recipe_id": recipe_id,
            "status": self.recipe_status.value,
            "config_revision": self.config_revision,
            "processing_config": {},
            "preprocess_artifact": DocumentPreprocessArtifact(
                derivation_id="prepared-1",
                profile="passthrough",
                file_name="policy.pdf",
                object_storage_path="local://prepared/policy.pdf",
                content_type="application/pdf",
            ).model_dump(mode="json"),
        }

    async def claim_ingestion_job(
        self, job_id: str, *, started_at: datetime
    ) -> IngestionJob | None:
        job = self.jobs.get(job_id)
        if job is None or job.status != IngestionJobStatus.QUEUED:
            return None
        claimed = job.model_copy(
            update={
                "status": IngestionJobStatus.RUNNING,
                "attempt_count": job.attempt_count + 1,
                "started_at": started_at,
            }
        )
        self.jobs[job_id] = claimed
        return claimed

    async def get_ingestion_job(self, job_id: str) -> IngestionJob | None:
        return self.jobs.get(job_id)

    async def update_ingestion_job(self, job_id: str, **updates: object) -> IngestionJob | None:
        job = self.jobs.get(job_id)
        if job is None:
            return None
        updated = job.model_copy(
            update={key: value for key, value in updates.items() if value is not None}
        )
        self.jobs[job_id] = updated
        return updated

    async def create_ingestion_job(self, job: IngestionJob) -> IngestionJob:
        if job.recipe_id is not None and any(
            existing.recipe_id == job.recipe_id
            and existing.status in {IngestionJobStatus.QUEUED, IngestionJobStatus.RUNNING}
            for existing in self.jobs.values()
        ):
            raise ValueError("このレシピは処理中または待機中です。")
        self.jobs[job.id] = job
        return job


class _FakeRecipeJobPipeline:
    """extraction を行わず、ingest 実行でレシピを REVIEW へ遷移させる fake pipeline。"""

    def __init__(self, *, oracle: _FakeRecipeJobOracle, **_kwargs: object) -> None:
        self._oracle = oracle

    async def ingest(self, *args: object, **kwargs: object) -> None:
        _ = args, kwargs
        self._oracle.recipe_status = FileStatus.REVIEW


def _extract_job(job_id: str = "job-extract-1") -> IngestionJob:
    return IngestionJob(
        id=job_id,
        document_id="doc-1",
        recipe_id="recipe-1",
        recipe_revision=1,
        status=IngestionJobStatus.QUEUED,
        phase=IngestionJobPhase.EXTRACT,
        parser_profile="local_text_structure",
        queued_at=datetime.now(UTC),
        settings_overrides={"processing_config": {}},
    )


async def _decide_next_phase(
    monkeypatch: pytest.MonkeyPatch, *, auto_chunk_enabled: bool
) -> IngestionJobPhase | None:
    """EXTRACT 完了直後の materialize が返す「次フェーズ」の決定だけを取り出す。"""
    fake = _FakeRecipeJobOracle()
    await ObjectStorageClient().put("prepared/policy.pdf", b"prepared pdf bytes", "application/pdf")
    monkeypatch.setattr(documents_route, "IngestionPipeline", _FakeRecipeJobPipeline)
    monkeypatch.setattr(
        documents_route,
        "get_settings",
        lambda: Settings(rag_auto_chunk_after_extract_enabled=auto_chunk_enabled),
    )
    return await _materialize_experiment_candidate(fake, _extract_job())  # type: ignore[arg-type]


async def test_recipe_extract_returns_chunk_phase_when_auto_chunk_enabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """EXTRACT 完了で REVIEW かつ auto_chunk 有効なら次フェーズ CHUNK を返す(決定のみ)。"""
    next_phase = await _decide_next_phase(monkeypatch, auto_chunk_enabled=True)
    assert next_phase == IngestionJobPhase.CHUNK


async def test_recipe_extract_returns_none_when_auto_chunk_disabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """既定(auto_chunk 無効)では次フェーズを返さず REVIEW に留める。"""
    next_phase = await _decide_next_phase(monkeypatch, auto_chunk_enabled=False)
    assert next_phase is None


async def test_recipe_extract_job_enqueues_chunk_after_current_job_finishes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """auto_chunk 有効時、EXTRACT job 完了(SUCCEEDED)後に同一レシピの CHUNK job を投入する。

    投入が現在ジョブ RUNNING 中に走るとレシピ行ロックのガードで弾かれるため、この統合的な
    経路テストが本来の不具合(自動進行が「このレシピは処理中または待機中です」で失敗)を捕捉する。
    """
    fake = _FakeRecipeJobOracle()
    fake.jobs["job-extract-1"] = _extract_job()
    await ObjectStorageClient().put("prepared/policy.pdf", b"prepared pdf bytes", "application/pdf")
    monkeypatch.setattr(documents_route, "OracleClient", lambda: fake)
    monkeypatch.setattr(documents_route, "IngestionPipeline", _FakeRecipeJobPipeline)
    monkeypatch.setattr(
        documents_route,
        "get_settings",
        lambda: Settings(rag_auto_chunk_after_extract_enabled=True),
    )
    monkeypatch.setattr(documents_route, "_dispatch_ingestion_job", lambda *a, **k: None)

    await documents_route._run_ingestion_job("job-extract-1", propagate_errors=True)

    # 現在の EXTRACT job は完了済み。
    assert fake.jobs["job-extract-1"].status == IngestionJobStatus.SUCCEEDED
    # 完了後にガードを通過して CHUNK job が 1 件だけ投入される。
    chunk_jobs = [
        job
        for job in fake.jobs.values()
        if job.phase == IngestionJobPhase.CHUNK and job.recipe_id == "recipe-1"
    ]
    assert len(chunk_jobs) == 1
    assert chunk_jobs[0].status == IngestionJobStatus.QUEUED


async def test_recipe_extract_job_stays_in_review_when_auto_chunk_disabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """auto_chunk 無効時は EXTRACT job 完了後も CHUNK job を投入しない。"""
    fake = _FakeRecipeJobOracle()
    fake.jobs["job-extract-1"] = _extract_job()
    await ObjectStorageClient().put("prepared/policy.pdf", b"prepared pdf bytes", "application/pdf")
    monkeypatch.setattr(documents_route, "OracleClient", lambda: fake)
    monkeypatch.setattr(documents_route, "IngestionPipeline", _FakeRecipeJobPipeline)
    monkeypatch.setattr(
        documents_route,
        "get_settings",
        lambda: Settings(rag_auto_chunk_after_extract_enabled=False),
    )
    monkeypatch.setattr(documents_route, "_dispatch_ingestion_job", lambda *a, **k: None)

    await documents_route._run_ingestion_job("job-extract-1", propagate_errors=True)

    assert fake.jobs["job-extract-1"].status == IngestionJobStatus.SUCCEEDED
    assert fake.recipe_status == FileStatus.REVIEW
    chunk_jobs = [job for job in fake.jobs.values() if job.phase == IngestionJobPhase.CHUNK]
    assert chunk_jobs == []
