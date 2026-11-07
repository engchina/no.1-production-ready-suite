"""試行記録と進捗を同一CLOB・transactionで保存する実Oracle境界。"""

import asyncio
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

import pytest

from app.clients.oracle import IngestionJobFence, OracleClient
from app.rag.ingestion_attempt_receipts import AttemptReceipt
from app.schemas.document import IngestionJob, IngestionJobProgress, IngestionJobStatus


@pytest.mark.usefixtures("oracle_db")
async def test_receipt_survives_concurrent_progress_and_terminal_rejects_write() -> None:
    client = OracleClient()
    suffix = uuid4().hex
    document = await client.create_document(
        file_name=f"synthetic-{suffix}.pdf",
        object_storage_path=f"local://{suffix}",
        content_type="application/pdf",
    )
    try:
        recipe = await client.ensure_default_document_recipe(document.id)
        job = IngestionJob(
            id=f"job-{suffix}",
            document_id=document.id,
            recipe_id=str(recipe["recipe_id"]),
            recipe_revision=int(str(recipe.get("config_revision") or 1)),
            status=IngestionJobStatus.QUEUED,
            parser_profile="synthetic",
            queued_at=datetime.now(UTC),
        )
        await client.create_ingestion_job(job)
        claimed = await client.claim_ingestion_job(
            job.id, started_at=datetime.now(UTC), lease_owner="synthetic-worker"
        )
        assert claimed is not None
        # pathは参照値のみ。実artifactをこのDB試験で作成・読取しない。
        rows: list[dict[str, Any]] = [
            dict(
                segment_id=f"s-{i}",
                document_id=document.id,
                recipe_id=job.recipe_id,
                parser_backend="docling",
                parser_profile="layout",
                page_start=i + 1,
                page_end=i + 1,
                artifact_path="local://" + "x" * 100,
                parse_key="key-" + str(i),
            )
            for i in range(80)
        ]
        receipt = AttemptReceipt.model_validate(
            dict(
                version=1,
                job_id=job.id,
                attempt_count=claimed.attempt_count,
                document_id=document.id,
                recipe_id=job.recipe_id,
                recipe_revision=job.recipe_revision,
                accepted=dict(
                    version=1, document_id=document.id, recipe_id=job.recipe_id, segments=rows
                ),
                remaining=[],
            )
        )
        assert len(receipt.model_dump_json()) > 4000
        assert await client.save_ingestion_attempt_receipt(
            job.id, receipt, attempt_count=claimed.attempt_count, lease_owner="synthetic-worker"
        )
        fence = IngestionJobFence(
            job_id=job.id, attempt_count=claimed.attempt_count, lease_owner="synthetic-worker"
        )
        await asyncio.gather(
            client.update_ingestion_job_progress(job.id, None, fence=fence),
            client.update_ingestion_job_progress(
                job.id,
                IngestionJobProgress(stage="parse", updated_at=datetime.now(UTC)),
                fence=fence,
            ),
        )
        assert await client.read_ingestion_attempt_receipt(claimed) == receipt
        from app.clients.oracle import _execute_count

        def interrupted(connection: Any) -> None:
            assert (
                _execute_count(
                    connection,
                    "UPDATE rag_ingestion_jobs SET progress_json = NULL WHERE job_id = :job_id",
                    {"job_id": job.id},
                )
                == 1
            )
            raise ValueError("synthetic rollback")

        with pytest.raises(ValueError, match="synthetic rollback"):
            await client._run_transaction(interrupted)
        assert await client.read_ingestion_attempt_receipt(claimed) == receipt
        assert not await client.save_ingestion_attempt_receipt(
            job.id, receipt, attempt_count=claimed.attempt_count, lease_owner="other-worker"
        )
        assert not await client.save_ingestion_attempt_receipt(
            job.id,
            receipt.model_copy(update={"attempt_count": claimed.attempt_count + 1}),
            attempt_count=claimed.attempt_count + 1,
            lease_owner="synthetic-worker",
        )
        await client.transition_ingestion_job(
            job.id,
            from_statuses={IngestionJobStatus.RUNNING},
            to_status=IngestionJobStatus.FAILED,
            fence=fence,
        )
        assert not await client.save_ingestion_attempt_receipt(
            job.id, receipt, attempt_count=claimed.attempt_count, lease_owner="synthetic-worker"
        )
        assert await client.read_ingestion_attempt_receipt(claimed) == receipt
    finally:
        await client.delete_document(document.id)
