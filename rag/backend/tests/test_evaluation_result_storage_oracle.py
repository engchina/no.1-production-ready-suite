"""品質評価の大きな結果を実 Oracle の JSON 型の列へ保存するテスト（#4103・#4107）。

未到達なら oracle_db fixture が skip する（CI の実 Oracle の job は skip を失敗にする）。
内容はすべて合成の値。
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime
from io import BytesIO
from typing import Any
from uuid import uuid4

import pytest
from openpyxl import load_workbook  # type: ignore[import-untyped]

from app.api.routes.evaluation import _save_evaluation_artifact
from app.clients.oracle import OracleClient, _json_dumps
from app.rag.evaluation import EvaluationProgressCallback
from app.rag.evaluation_jobs import (
    EvaluationJobOutcome,
    EvaluationJobRecord,
    EvaluationJobService,
    OracleEvaluationJobStore,
)
from app.rag.evaluation_results_excel import evaluation_results_xlsx
from app.schemas.evaluation import EvaluationResultExportRequest
from tests import _oracle_test_db

_MIB = 1024 * 1024
_CASES = 45


def _large_result(*, cases: int = _CASES, case_bytes: int = _MIB) -> dict[str, Any]:
    """実際の大きな結果と同じ形（候補 ID の配列と日本語の文。1 ケース約 1MiB）。"""
    ids = [f"{index:032x}:{index:08x}" for index in range(case_bytes // 88)]
    note = "合成の診断" * (case_bytes // 30)
    return {
        "case_count": cases,
        "answer_correctness": 0.5,
        "case_results": [
            {
                "case_id": f"case-{index}",
                "trace_id": f"trace-{index}",
                "answer_correctness": 0.5,
                "elapsed_ms": 1.0,
                "diagnostics": {
                    "answer": {"retrieval_stages": [{"selected_ids": ids}], "note": note},
                },
            }
            for index in range(cases)
        ],
    }


@pytest.fixture
def job_ids(oracle_db: None) -> Iterator[list[str]]:
    """テストで作った job と artifact の行を消す。"""
    created: list[str] = []
    yield created
    with _oracle_test_db._schema_connection() as connection:
        cursor = connection.cursor()
        for job_id in created:
            cursor.execute("DELETE FROM rag_evaluation_jobs WHERE job_id = :id", {"id": job_id})
            cursor.execute(
                "DELETE FROM rag_evaluation_runs WHERE evaluation_run_id = :id", {"id": job_id}
            )
        connection.commit()


async def _create_job(store: OracleEvaluationJobStore, job_id: str, lease_owner: str) -> None:
    await store.create(
        EvaluationJobRecord(
            job_id=job_id,
            kind="run",
            status="RUNNING",
            total_cases=_CASES,
            time_limit_seconds=3600,
            created_at=datetime.now(UTC),
            lease_owner=lease_owner,
        )
    )


async def _column_stats(job_id: str) -> dict[str, object]:
    row = await OracleClient()._fetch_one(
        """
        SELECT status,
               JSON_VALUE(result_json, '$.answer_diagnostics_omitted') AS omitted
        FROM rag_evaluation_jobs
        WHERE job_id = :job_id
        """,
        {"job_id": job_id},
    )
    assert row is not None
    return dict(row)


async def test_raw_large_result_exceeds_json_column_limit(job_ids: list[str]) -> None:
    """列の上限（ORA-40604）を、この修正の前提として確かめる。行は RUNNING のまま残る。"""
    job_id, lease_owner = f"pytest-{uuid4().hex}", "pytest-worker"
    job_ids.append(job_id)
    client = OracleClient()
    await _create_job(OracleEvaluationJobStore(client), job_id, lease_owner)
    result = _large_result()
    assert len(_json_dumps(result).encode("utf-8")) > 40 * _MIB

    with pytest.raises(Exception, match="ORA-40604"):
        await client.finish_evaluation_job(
            job_id,
            lease_owner=lease_owner,
            status="SUCCEEDED",
            result=result,
            error_message=None,
            evaluation_run_id=None,
        )

    assert (await _column_stats(job_id))["status"] == "RUNNING"


async def test_large_result_succeeds_and_exports_excel(job_ids: list[str]) -> None:
    """45 ケース約 45MiB の結果でも SUCCEEDED になり、得点を読めて Excel を作れる。"""
    job_id = f"pytest-{uuid4().hex}"
    job_ids.append(job_id)
    service = EvaluationJobService(
        OracleEvaluationJobStore(OracleClient()),
        heartbeat_seconds=0.5,
        worker_id=f"pytest-{uuid4().hex[:12]}",
        finish_retry_seconds=(),
    )

    async def executor(progress: EvaluationProgressCallback) -> EvaluationJobOutcome:
        await progress(completed_cases=_CASES, current_case_id=None)
        return EvaluationJobOutcome(result=_large_result())

    await service.submit(
        kind="run", total_cases=_CASES, time_limit_seconds=3600, executor=executor, job_id=job_id
    )
    await service.wait_for(job_id)

    job = await service.get(job_id)
    assert job.status == "SUCCEEDED", job.error_message
    assert job.run_result is not None
    assert job.run_result.answer_diagnostics_omitted is True
    assert len(job.run_result.case_results) == _CASES
    assert job.run_result.case_results[-1].answer_correctness == 0.5
    assert job.run_result.case_results[-1].diagnostics.answer is None

    content = evaluation_results_xlsx(job.run_result, EvaluationResultExportRequest())
    workbook = load_workbook(BytesIO(content))
    assert workbook.worksheets


async def test_large_artifact_is_saved(job_ids: list[str]) -> None:
    """評価の artifact（`rag_evaluation_runs`）も、同じ大きさで保存できる。"""
    run_id = await _save_evaluation_artifact(
        request_summary={"kind": "run"},
        result_summary=_large_result(),
        knowledge_base_ids=[],
        passed=False,
    )
    assert run_id is not None
    job_ids.append(run_id)

    row = await OracleClient()._fetch_one(
        """
        SELECT JSON_VALUE(result_json, '$.answer_diagnostics_omitted') AS omitted
        FROM rag_evaluation_runs
        WHERE evaluation_run_id = :run_id
        """,
        {"run_id": run_id},
    )
    assert row is not None
    assert row["omitted"] == "true"
