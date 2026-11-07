"""同じ worker が取込 job を claim し直した後の古い試行の書き込みを、実 Oracle で止める(#4187)。

lease の持ち主(``lease_owner``)は worker ごとの値で、同じ worker が claim し直すと変わらない。
claim のたびに増える試行の回数(``attempt_count``)も条件にし、古い試行の完了・失敗・再キュー・進捗の
書き込みを 0 行の更新にする(fencing token)。SQL の形は tests/test_oracle_adapter.py、route の
後始末は tests/test_ingestion_lease.py が確かめる。未到達なら oracle_db fixture が skip
(CI では失敗)し、作成行は文書の削除で後始末する。
"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

import pytest

from app.clients.oracle import (
    IngestionJobFence,
    OracleClient,
    OracleConnectionProtocol,
    _execute_count,
)
from app.schemas.document import (
    IngestionJob,
    IngestionJobPhase,
    IngestionJobProgress,
    IngestionJobStatus,
)


async def _running_job_reclaimed_by_same_worker(
    client: OracleClient, document_id: str, lease_owner: str
) -> tuple[IngestionJobFence, IngestionJobFence]:
    """job を claim し、stale の回復と同じく QUEUED へ戻し、同じ worker が claim し直す。

    戻すのは対象の job の行だけにする(``recover_stale_ingestion_jobs`` は並列の他のテストの job も
    戻しうるため、回復の書き込みと同じ列だけを直接更新する)。
    """
    job = await client.create_ingestion_job(
        IngestionJob(
            id=f"job_attempt_fence_{uuid4().hex[:16]}",
            document_id=document_id,
            status=IngestionJobStatus.QUEUED,
            phase=IngestionJobPhase.PREPROCESS,
            parser_profile="local_text_structure",
            queued_at=datetime.now(UTC),
        )
    )
    first = await client.claim_ingestion_job(
        job.id, started_at=datetime.now(UTC), lease_owner=lease_owner
    )
    assert first is not None

    def recover(connection: OracleConnectionProtocol) -> int:
        return _execute_count(
            connection,
            """
            UPDATE rag_ingestion_jobs
            SET status = 'QUEUED', started_at = NULL, lease_owner = NULL, heartbeat_at = NULL
            WHERE job_id = :job_id AND status = 'RUNNING'
            """,
            {"job_id": job.id},
        )

    assert await client._run_transaction(recover) == 1
    second = await client.claim_ingestion_job(
        job.id, started_at=datetime.now(UTC), lease_owner=lease_owner
    )
    assert second is not None
    assert second.attempt_count == first.attempt_count + 1
    return (
        IngestionJobFence(
            job_id=job.id, attempt_count=first.attempt_count, lease_owner=lease_owner
        ),
        IngestionJobFence(
            job_id=job.id, attempt_count=second.attempt_count, lease_owner=lease_owner
        ),
    )


@pytest.mark.usefixtures("oracle_db")
@pytest.mark.parametrize(
    "to_status",
    [IngestionJobStatus.SUCCEEDED, IngestionJobStatus.FAILED, IngestionJobStatus.QUEUED],
    ids=["succeeded", "failed", "requeued"],
)
async def test_old_attempt_of_same_worker_cannot_write_job(
    to_status: IngestionJobStatus,
) -> None:
    """古い試行の完了・失敗・再キューと進捗は書かれず、新しい試行は書ける。"""
    client = OracleClient()
    suffix = uuid4().hex[:12]
    document = await client.create_document(
        file_name=f"attempt-fence-{suffix}.txt",
        object_storage_path=f"oci://bucket/attempt-fence-{suffix}.txt",
        content_type="text/plain",
    )
    lease_owner = f"synthetic-worker-{suffix}"
    try:
        old, new = await _running_job_reclaimed_by_same_worker(client, document.id, lease_owner)

        lease = await client.get_ingestion_job_lease(old.job_id)
        assert lease is not None
        assert lease.lease_owner == lease_owner
        assert lease.attempt_count == new.attempt_count

        stale = await client.transition_ingestion_job(
            old.job_id,
            from_statuses=(IngestionJobStatus.RUNNING,),
            to_status=to_status,
            finished_at=datetime.now(UTC),
            fence=old,
        )
        assert stale is None
        progress = IngestionJobProgress(stage="parse", updated_at=datetime.now(UTC))
        assert not await client.update_ingestion_job_progress(old.job_id, progress, fence=old)

        current = await client.get_ingestion_job(old.job_id)
        assert current is not None
        assert current.status == IngestionJobStatus.RUNNING
        assert current.attempt_count == new.attempt_count
        assert current.finished_at is None
        assert current.progress is None

        # 新しい試行は影響を受けない。
        assert await client.update_ingestion_job_progress(old.job_id, progress, fence=new)
        finished = await client.transition_ingestion_job(
            old.job_id,
            from_statuses=(IngestionJobStatus.RUNNING,),
            to_status=to_status,
            finished_at=datetime.now(UTC),
            fence=new,
        )
        assert finished is not None
        assert finished.status == to_status
    finally:
        # 途中で失敗しても RUNNING の job を残さない(RUNNING の job があると文書を消せない)。
        job_ids = [job.id for job in await client.list_document_ingestion_jobs(document.id)]
        for job_id in job_ids:
            await client.transition_ingestion_job(
                job_id,
                from_statuses=(IngestionJobStatus.QUEUED, IngestionJobStatus.RUNNING),
                to_status=IngestionJobStatus.CANCELLED,
            )
        await client.delete_document(document.id)
