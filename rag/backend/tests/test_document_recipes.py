"""1文書1〜3レシピの境界・工程状態・検索対象契約。"""

from datetime import UTC, datetime
from typing import Any

import pytest
from fastapi import HTTPException

from app.api.routes import documents as documents_route
from app.api.routes.documents import (
    _apply_recipe_review_text_edits,
    _enqueue_failed_segment_retry_job_for_document,
    _recipe_steps,
)
from app.clients.oracle import (
    oracle_chunk_set_schema_sql,
    oracle_document_recipe_schema_sql,
    oracle_ingestion_job_schema_sql,
)
from app.schemas.document import (
    DocumentDetail,
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
