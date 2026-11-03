"""品質評価のケースごとの結果を実 Oracle の表へ保存するテスト（#4208）。

未到達なら oracle_db fixture が skip する（CI の実 Oracle の job は skip を失敗にする）。
内容はすべて合成の値。
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime
from uuid import uuid4

import pytest

from app.rag.evaluation_jobs import (
    EVALUATION_JOB_STALE_MESSAGE,
    EvaluationJobRecord,
    EvaluationJobService,
    OracleEvaluationJobStore,
)
from app.rag.request_context import (
    AuditRequestContext,
    reset_audit_request_context,
    set_audit_request_context,
)
from app.schemas.evaluation import EvaluationCaseResult
from tests import _oracle_test_db

_LEASE = "pytest-worker"
_TENANT = "t" * 64
_OWNER = "o" * 64


@pytest.fixture
def job_ids(oracle_db: None) -> Iterator[list[str]]:
    """テストで作った job の行を消す（ケースの行は FK で一緒に消える）。"""
    created: list[str] = []
    yield created
    with _oracle_test_db._schema_connection() as connection:
        cursor = connection.cursor()
        for job_id in created:
            cursor.execute("DELETE FROM rag_evaluation_jobs WHERE job_id = :id", {"id": job_id})
        connection.commit()


async def _create_job(store: OracleEvaluationJobStore, job_ids: list[str]) -> str:
    job_id = f"pytest-{uuid4().hex}"
    job_ids.append(job_id)
    await store.create(
        EvaluationJobRecord(
            job_id=job_id,
            kind="run",
            status="RUNNING",
            total_cases=3,
            time_limit_seconds=3600,
            created_at=datetime.now(UTC),
            tenant_id_hash=_TENANT,
            user_id_hash=_OWNER,
            lease_owner=_LEASE,
        )
    )
    return job_id


async def _save(
    store: OracleEvaluationJobStore,
    job_id: str,
    *,
    case_index: int,
    pass_index: int = 0,
    lease_owner: str = _LEASE,
) -> bool:
    return await store.save_case_result(
        job_id,
        lease_owner=lease_owner,
        pass_index=pass_index,
        case_index=case_index,
        case_id=f"row-{case_index + 2}",
        search_answer_profile_id="profile-a",
        result={
            "case_id": f"row-{case_index + 2}",
            "trace_id": f"trace-{case_index}",
            "elapsed_ms": 1.0,
            "answer_correctness": 0.5,
        },
    )


async def test_case_results_round_trip_in_case_order(job_ids: list[str]) -> None:
    store = OracleEvaluationJobStore()
    job_id = await _create_job(store, job_ids)

    assert await _save(store, job_id, case_index=2)
    assert await _save(store, job_id, case_index=0)
    assert await _save(store, job_id, case_index=0, pass_index=1)

    rows = await store.list_case_results(job_id, tenant_id_hash=_TENANT, user_id_hash=_OWNER)
    assert [(row.pass_index, row.case_index) for row in rows] == [(0, 0), (0, 2), (1, 0)]
    assert rows[1].case_id == "row-4"
    assert rows[1].search_answer_profile_id == "profile-a"
    assert rows[1].result["answer_correctness"] == 0.5


async def test_case_results_are_not_written_without_the_lease(job_ids: list[str]) -> None:
    store = OracleEvaluationJobStore()
    job_id = await _create_job(store, job_ids)

    assert not await _save(store, job_id, case_index=0, lease_owner="another-worker")
    assert await store.list_case_results(job_id, tenant_id_hash=_TENANT, user_id_hash=_OWNER) == []


async def test_case_results_are_read_only_by_the_job_owner(job_ids: list[str]) -> None:
    store = OracleEvaluationJobStore()
    job_id = await _create_job(store, job_ids)
    assert await _save(store, job_id, case_index=0)

    assert (
        await store.list_case_results(job_id, tenant_id_hash=_TENANT, user_id_hash="x" * 64) == []
    )


async def test_stale_job_returns_saved_cases_as_partial_result(job_ids: list[str]) -> None:
    """止まったプロセスの job を別のプロセスが回収しても、保存した行を途中までの結果で返す。"""
    store = OracleEvaluationJobStore()
    job_id = await _create_job(store, job_ids)
    assert await _save(store, job_id, case_index=0)
    assert await _save(store, job_id, case_index=1)

    token = set_audit_request_context(
        AuditRequestContext(tenant_id_hash=_TENANT, user_id_hash=_OWNER)
    )
    try:
        job = await EvaluationJobService(store, worker_id="other", stale_seconds=0.0).get(job_id)
    finally:
        reset_audit_request_context(token)

    assert job.status == "FAILED"
    assert job.error_message == EVALUATION_JOB_STALE_MESSAGE
    assert job.partial_result is not None
    assert [case.case_id for case in job.partial_result.case_results] == ["row-2", "row-3"]
    assert isinstance(job.partial_result.case_results[0], EvaluationCaseResult)


async def test_deleting_the_job_deletes_its_case_results(job_ids: list[str]) -> None:
    store = OracleEvaluationJobStore()
    job_id = await _create_job(store, job_ids)
    assert await _save(store, job_id, case_index=0)

    with _oracle_test_db._schema_connection() as connection:
        cursor = connection.cursor()
        cursor.execute("DELETE FROM rag_evaluation_jobs WHERE job_id = :id", {"id": job_id})
        connection.commit()
        cursor.execute(
            "SELECT COUNT(*) FROM rag_evaluation_job_case_results WHERE job_id = :id",
            {"id": job_id},
        )
        count = cursor.fetchone()
    assert count is not None and count[0] == 0
