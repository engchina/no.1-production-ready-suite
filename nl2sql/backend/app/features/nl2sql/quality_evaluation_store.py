"""品質評価 job/result の memory / Oracle repository。"""

from __future__ import annotations

import json
import threading
from collections.abc import Callable
from contextlib import AbstractContextManager
from datetime import UTC, datetime, timedelta
from typing import Any, Protocol

from .quality_evaluation_models import (
    QualityEvaluationJobRecord,
    QualityEvaluationResult,
    QualityEvaluationStatus,
)


def _canonical_json(value: dict[str, Any]) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True)


def _read_lob(value: Any) -> str:
    if value is None:
        return ""
    reader = getattr(value, "read", None)
    return str(reader() if callable(reader) else value)


class QualityEvaluationRepository(Protocol):
    mode: str

    def save_job(self, job: QualityEvaluationJobRecord) -> QualityEvaluationJobRecord: ...

    def get_job(self, job_id: str) -> QualityEvaluationJobRecord | None: ...

    def list_jobs(
        self, *, offset: int, limit: int
    ) -> tuple[list[QualityEvaluationJobRecord], int]: ...

    def claim_job(
        self, *, worker_id: str, lease_seconds: float, job_id: str | None = None
    ) -> QualityEvaluationJobRecord | None: ...

    def save_result(self, result: QualityEvaluationResult) -> bool: ...

    def has_result(self, *, job_id: str, case_no: int, engine: str, repetition_no: int) -> bool: ...

    def list_results(
        self, *, job_id: str, offset: int, limit: int
    ) -> tuple[list[QualityEvaluationResult], int]: ...

    def all_results(self, job_id: str) -> list[QualityEvaluationResult]: ...


class MemoryQualityEvaluationRepository:
    mode = "memory"

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._jobs: dict[str, QualityEvaluationJobRecord] = {}
        self._results: dict[tuple[str, int, str, int], QualityEvaluationResult] = {}

    def save_job(self, job: QualityEvaluationJobRecord) -> QualityEvaluationJobRecord:
        with self._lock:
            self._jobs[job.job_id] = job.model_copy(deep=True)
            return job.model_copy(deep=True)

    def get_job(self, job_id: str) -> QualityEvaluationJobRecord | None:
        with self._lock:
            job = self._jobs.get(job_id)
            return job.model_copy(deep=True) if job else None

    def list_jobs(
        self, *, offset: int, limit: int
    ) -> tuple[list[QualityEvaluationJobRecord], int]:
        with self._lock:
            jobs = sorted(
                self._jobs.values(), key=lambda item: (item.created_at, item.job_id), reverse=True
            )
            return [item.model_copy(deep=True) for item in jobs[offset : offset + limit]], len(jobs)

    def claim_job(
        self, *, worker_id: str, lease_seconds: float, job_id: str | None = None
    ) -> QualityEvaluationJobRecord | None:
        now = datetime.now(UTC)
        with self._lock:
            candidates = sorted(
                self._jobs.values(), key=lambda item: (item.created_at, item.job_id)
            )
            for current in candidates:
                if job_id and current.job_id != job_id:
                    continue
                expired = not current.lease_expires_at or datetime.fromisoformat(
                    current.lease_expires_at
                ) <= now
                if current.status != QualityEvaluationStatus.PENDING and not (
                    current.status == QualityEvaluationStatus.RUNNING and expired
                ):
                    continue
                claimed = current.model_copy(
                    update={
                        "status": QualityEvaluationStatus.RUNNING,
                        "started_at": current.started_at or now.isoformat(),
                        "worker_id": worker_id,
                        "heartbeat_at": now.isoformat(),
                        "lease_expires_at": (
                            now + timedelta(seconds=max(30.0, lease_seconds))
                        ).isoformat(),
                        "attempt_no": current.attempt_no + 1,
                        "updated_at": now.isoformat(),
                    },
                    deep=True,
                )
                self._jobs[claimed.job_id] = claimed
                return claimed.model_copy(deep=True)
        return None

    def save_result(self, result: QualityEvaluationResult) -> bool:
        key = (result.job_id, result.case_no, result.engine.value, result.repetition_no)
        with self._lock:
            if key in self._results:
                return False
            self._results[key] = result.model_copy(deep=True)
            return True

    def has_result(
        self, *, job_id: str, case_no: int, engine: str, repetition_no: int
    ) -> bool:
        with self._lock:
            return (job_id, case_no, engine, repetition_no) in self._results

    def list_results(
        self, *, job_id: str, offset: int, limit: int
    ) -> tuple[list[QualityEvaluationResult], int]:
        with self._lock:
            results = sorted(
                (item for key, item in self._results.items() if key[0] == job_id),
                key=lambda item: (item.case_no, item.engine.value, item.repetition_no),
            )
            return [item.model_copy(deep=True) for item in results[offset : offset + limit]], len(
                results
            )

    def all_results(self, job_id: str) -> list[QualityEvaluationResult]:
        return self.list_results(job_id=job_id, offset=0, limit=1_000_000)[0]


class OracleQualityEvaluationRepository:
    mode = "oracle"

    def __init__(
        self, *, connection_factory: Callable[[], AbstractContextManager[Any]]
    ) -> None:
        self._connection_factory = connection_factory

    def save_job(self, job: QualityEvaluationJobRecord) -> QualityEvaluationJobRecord:
        payload = _canonical_json(job.model_dump(mode="json"))
        binds = {
            "job_id": job.job_id,
            "status": job.status.value,
            "profile_id": job.profile_id,
            "worker_id": job.worker_id or None,
            "heartbeat_at": datetime.fromisoformat(job.heartbeat_at) if job.heartbeat_at else None,
            "lease_expires_at": (
                datetime.fromisoformat(job.lease_expires_at) if job.lease_expires_at else None
            ),
            "attempt_no": job.attempt_no,
            "payload": payload,
        }
        with self._connection_factory() as connection, connection.cursor() as cursor:
            cursor.execute(
                "MERGE INTO NL2SQL_EVALUATION_JOBS t USING (SELECT :job_id JOB_ID FROM DUAL) s "
                "ON (t.JOB_ID = s.JOB_ID) WHEN MATCHED THEN UPDATE SET "
                "t.STATUS = :status, t.PROFILE_ID = :profile_id, t.WORKER_ID = :worker_id, "
                "t.HEARTBEAT_AT = :heartbeat_at, t.LEASE_EXPIRES_AT = :lease_expires_at, "
                "t.ATTEMPT_NO = :attempt_no, t.PAYLOAD_JSON = :payload, "
                "t.UPDATED_AT = SYSTIMESTAMP WHEN NOT MATCHED THEN INSERT "
                "(JOB_ID, STATUS, PROFILE_ID, WORKER_ID, HEARTBEAT_AT, LEASE_EXPIRES_AT, "
                "ATTEMPT_NO, PAYLOAD_JSON) VALUES (:job_id, :status, :profile_id, :worker_id, "
                ":heartbeat_at, :lease_expires_at, :attempt_no, :payload)",
                binds,
            )
            connection.commit()
        return job.model_copy(deep=True)

    def get_job(self, job_id: str) -> QualityEvaluationJobRecord | None:
        with self._connection_factory() as connection, connection.cursor() as cursor:
            cursor.execute(
                "SELECT PAYLOAD_JSON FROM NL2SQL_EVALUATION_JOBS WHERE JOB_ID = :job_id",
                {"job_id": job_id},
            )
            row = cursor.fetchone()
        raw = _read_lob(row[0]) if row else ""
        return QualityEvaluationJobRecord.model_validate_json(raw) if raw else None

    def list_jobs(
        self, *, offset: int, limit: int
    ) -> tuple[list[QualityEvaluationJobRecord], int]:
        with self._connection_factory() as connection, connection.cursor() as cursor:
            cursor.execute("SELECT COUNT(*) FROM NL2SQL_EVALUATION_JOBS")
            total = int(cursor.fetchone()[0])
            cursor.execute(
                "SELECT PAYLOAD_JSON FROM NL2SQL_EVALUATION_JOBS "
                "ORDER BY CREATED_AT DESC, JOB_ID DESC OFFSET :offset ROWS "
                "FETCH NEXT :limit ROWS ONLY",
                {"offset": offset, "limit": limit},
            )
            rows = cursor.fetchall()
        jobs = [
            QualityEvaluationJobRecord.model_validate_json(_read_lob(row[0]))
            for row in rows
        ]
        return jobs, total

    def claim_job(
        self, *, worker_id: str, lease_seconds: float, job_id: str | None = None
    ) -> QualityEvaluationJobRecord | None:
        now = datetime.now(UTC)
        lease = now + timedelta(seconds=max(30.0, lease_seconds))
        predicate = (
            "(STATUS = 'pending' OR (STATUS = 'running' AND "
            "(LEASE_EXPIRES_AT IS NULL OR LEASE_EXPIRES_AT <= SYSTIMESTAMP)))"
        )
        select_binds: dict[str, Any] = {}
        if job_id:
            predicate += " AND JOB_ID = :job_id"
            select_binds["job_id"] = job_id
        sql = (
            "SELECT JOB_ID, PAYLOAD_JSON FROM NL2SQL_EVALUATION_JOBS WHERE "
            + predicate
            + " ORDER BY CREATED_AT, JOB_ID FETCH FIRST 1 ROWS ONLY FOR UPDATE SKIP LOCKED"
        )
        with self._connection_factory() as connection, connection.cursor() as cursor:
            try:
                cursor.execute(sql, select_binds)
                row = cursor.fetchone()
                if row is None:
                    connection.commit()
                    return None
                current = QualityEvaluationJobRecord.model_validate_json(_read_lob(row[1]))
                claimed = current.model_copy(
                    update={
                        "status": QualityEvaluationStatus.RUNNING,
                        "started_at": current.started_at or now.isoformat(),
                        "worker_id": worker_id,
                        "heartbeat_at": now.isoformat(),
                        "lease_expires_at": lease.isoformat(),
                        "attempt_no": current.attempt_no + 1,
                        "updated_at": now.isoformat(),
                    },
                    deep=True,
                )
                cursor.execute(
                    "UPDATE NL2SQL_EVALUATION_JOBS SET STATUS = 'running', "
                    "WORKER_ID = :worker_id, HEARTBEAT_AT = :heartbeat_at, "
                    "LEASE_EXPIRES_AT = :lease_expires_at, ATTEMPT_NO = ATTEMPT_NO + 1, "
                    "PAYLOAD_JSON = :payload, UPDATED_AT = SYSTIMESTAMP WHERE JOB_ID = :job_id",
                    {
                        "worker_id": worker_id,
                        "heartbeat_at": now,
                        "lease_expires_at": lease,
                        "payload": _canonical_json(claimed.model_dump(mode="json")),
                        "job_id": str(row[0]),
                    },
                )
                connection.commit()
                return claimed
            except Exception:
                connection.rollback()
                raise

    def save_result(self, result: QualityEvaluationResult) -> bool:
        payload = _canonical_json(result.model_dump(mode="json"))
        with self._connection_factory() as connection, connection.cursor() as cursor:
            try:
                cursor.execute(
                    "INSERT INTO NL2SQL_EVALUATION_RESULTS "
                    "(RESULT_ID, JOB_ID, CASE_NO, ENGINE, REPETITION_NO, RESULT_STATUS, "
                    "VERDICT, PAYLOAD_JSON) VALUES (:result_id, :job_id, :case_no, :engine, "
                    ":repetition_no, :result_status, :verdict, :payload)",
                    {
                        "result_id": result.result_id,
                        "job_id": result.job_id,
                        "case_no": result.case_no,
                        "engine": result.engine.value,
                        "repetition_no": result.repetition_no,
                        "result_status": (
                            "error"
                            if result.generation_error or result.judge_error
                            else "completed"
                        ),
                        "verdict": result.verdict.value,
                        "payload": payload,
                    },
                )
                connection.commit()
                return True
            except Exception as exc:
                connection.rollback()
                if "ORA-00001" in str(exc).upper():
                    return False
                raise

    def has_result(
        self, *, job_id: str, case_no: int, engine: str, repetition_no: int
    ) -> bool:
        with self._connection_factory() as connection, connection.cursor() as cursor:
            cursor.execute(
                "SELECT 1 FROM NL2SQL_EVALUATION_RESULTS WHERE JOB_ID = :job_id "
                "AND CASE_NO = :case_no AND ENGINE = :engine "
                "AND REPETITION_NO = :repetition_no FETCH FIRST 1 ROWS ONLY",
                {
                    "job_id": job_id,
                    "case_no": case_no,
                    "engine": engine,
                    "repetition_no": repetition_no,
                },
            )
            return cursor.fetchone() is not None

    def list_results(
        self, *, job_id: str, offset: int, limit: int
    ) -> tuple[list[QualityEvaluationResult], int]:
        with self._connection_factory() as connection, connection.cursor() as cursor:
            cursor.execute(
                "SELECT COUNT(*) FROM NL2SQL_EVALUATION_RESULTS WHERE JOB_ID = :job_id",
                {"job_id": job_id},
            )
            total = int(cursor.fetchone()[0])
            cursor.execute(
                "SELECT PAYLOAD_JSON FROM NL2SQL_EVALUATION_RESULTS WHERE JOB_ID = :job_id "
                "ORDER BY CASE_NO, ENGINE, REPETITION_NO OFFSET :offset ROWS "
                "FETCH NEXT :limit ROWS ONLY",
                {"job_id": job_id, "offset": offset, "limit": limit},
            )
            rows = cursor.fetchall()
        results = [
            QualityEvaluationResult.model_validate_json(_read_lob(row[0])) for row in rows
        ]
        return results, total

    def all_results(self, job_id: str) -> list[QualityEvaluationResult]:
        with self._connection_factory() as connection, connection.cursor() as cursor:
            cursor.execute(
                "SELECT PAYLOAD_JSON FROM NL2SQL_EVALUATION_RESULTS WHERE JOB_ID = :job_id "
                "ORDER BY CASE_NO, ENGINE, REPETITION_NO",
                {"job_id": job_id},
            )
            rows = cursor.fetchall()
        return [QualityEvaluationResult.model_validate_json(_read_lob(row[0])) for row in rows]
