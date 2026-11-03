"""元の Excel のメタ情報と job の参照を実 Oracle で確かめるテスト（#4212）。

未到達なら oracle_db fixture が skip する（CI の実 Oracle の job は skip を失敗にする）。
内容はすべて合成の値。
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime
from uuid import uuid4

import pytest

from app.clients.evaluation_workbook_store import OracleEvaluationWorkbookStore, SourceWorkbookRow
from app.clients.oracle import OracleClient
from app.rag.evaluation_jobs import EvaluationJobRecord, OracleEvaluationJobStore
from tests import _oracle_test_db

_TENANT = "t" * 64
_OWNER = "o" * 64


@pytest.fixture
def created(oracle_db: None) -> Iterator[dict[str, list[str]]]:
    """テストで作った行を消す。"""
    ids: dict[str, list[str]] = {"jobs": [], "workbooks": [], "traces": []}
    yield ids
    with _oracle_test_db._schema_connection() as connection:
        cursor = connection.cursor()
        for job_id in ids["jobs"]:
            cursor.execute("DELETE FROM rag_evaluation_jobs WHERE job_id = :id", {"id": job_id})
        for workbook_id in ids["workbooks"]:
            cursor.execute(
                "DELETE FROM rag_evaluation_source_workbooks WHERE workbook_id = :id",
                {"id": workbook_id},
            )
        for trace_id in ids["traces"]:
            cursor.execute("DELETE FROM rag_answer_records WHERE trace_id = :id", {"id": trace_id})
        connection.commit()


async def _workbook(store: OracleEvaluationWorkbookStore, created: dict[str, list[str]]) -> str:
    workbook_id = uuid4().hex
    created["workbooks"].append(workbook_id)
    await store.create(
        SourceWorkbookRow(
            workbook_id=workbook_id,
            tenant_id_hash=_TENANT,
            user_id_hash=_OWNER,
            file_name="合成の評価 A.xlsx",
            content_sha256=uuid4().hex,
            byte_size=10,
            object_storage_path=f"local://evaluation-workbooks/{workbook_id}/source.xlsx",
        )
    )
    return workbook_id


async def _job(
    store: OracleEvaluationJobStore, created: dict[str, list[str]], workbook_id: str
) -> str:
    job_id = f"pytest-{uuid4().hex}"
    created["jobs"].append(job_id)
    await store.create(
        EvaluationJobRecord(
            job_id=job_id,
            kind="run",
            status="RUNNING",
            total_cases=1,
            time_limit_seconds=3600,
            created_at=datetime.now(UTC),
            tenant_id_hash=_TENANT,
            user_id_hash=_OWNER,
            lease_owner="pytest-worker",
            source_workbook_id=workbook_id,
        )
    )
    return job_id


async def test_workbook_row_is_read_only_by_its_owner(created: dict[str, list[str]]) -> None:
    store = OracleEvaluationWorkbookStore()
    workbook_id = await _workbook(store, created)

    row = await store.get(workbook_id, tenant_id_hash=_TENANT, user_id_hash=_OWNER)
    assert row is not None
    assert row.file_name == "合成の評価 A.xlsx"
    assert await store.get(workbook_id, tenant_id_hash=_TENANT, user_id_hash="x" * 64) is None
    assert (
        await store.find_by_content(
            tenant_id_hash=_TENANT, user_id_hash=_OWNER, content_sha256=row.content_sha256
        )
    ) is not None
    assert (
        await store.find_by_content(
            tenant_id_hash=_TENANT, user_id_hash="x" * 64, content_sha256=row.content_sha256
        )
    ) is None


async def test_job_keeps_the_workbook_and_unused_listing_excludes_it(
    created: dict[str, list[str]],
) -> None:
    store = OracleEvaluationWorkbookStore()
    jobs = OracleEvaluationJobStore()
    used = await _workbook(store, created)
    unused = await _workbook(store, created)
    job_id = await _job(jobs, created, used)

    record = await jobs.get(job_id, tenant_id_hash=_TENANT, user_id_hash=_OWNER)
    assert record is not None
    assert record.source_workbook_id == used

    listed = await store.list_unused(
        tenant_id_hash=_TENANT, user_id_hash=_OWNER, older_than_hours=0
    )
    ids = {row.workbook_id for row in listed}
    assert unused in ids
    assert used not in ids
    # job が使っている行は消せない。
    assert not await store.delete(used)
    assert await store.delete(unused)


async def test_deleting_the_job_releases_the_workbook(created: dict[str, list[str]]) -> None:
    store = OracleEvaluationWorkbookStore()
    jobs = OracleEvaluationJobStore()
    workbook_id = await _workbook(store, created)
    job_id = await _job(jobs, created, workbook_id)

    with _oracle_test_db._schema_connection() as connection:
        cursor = connection.cursor()
        cursor.execute("DELETE FROM rag_evaluation_jobs WHERE job_id = :id", {"id": job_id})
        connection.commit()

    assert await store.delete(workbook_id)


async def test_answers_are_read_by_trace_id_in_batches(created: dict[str, list[str]]) -> None:
    """保存されていない trace は結果に入らない（SQL の形を実 DB で確かめる）。"""
    assert await OracleClient().evaluation_answers_by_trace_ids([f"pytest-{uuid4().hex}"]) == {}
