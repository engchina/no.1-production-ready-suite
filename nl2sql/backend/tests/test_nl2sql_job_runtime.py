"""NL2SQL 非同期 job の複数 worker / 再起動耐性テスト。

gunicorn の複数 worker では、job を実行するプロセスと `GET /jobs/{id}` /
`POST /jobs/{id}/cancel` を受けるプロセスが異なる。1 つの repository を共有する
2 つの service を「別 worker」に見立てて、状態収束・キャンセル伝搬・孤児 job の
正規化を検証する。
"""

from __future__ import annotations

import threading
import time
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from app.features.nl2sql.incremental_store import MemoryIncrementalNl2SqlRepository
from app.features.nl2sql.models import (
    JobCreateRequest,
    JobStatus,
    JobStepStatus,
    Nl2SqlEngine,
    Nl2SqlProfile,
    SchemaCatalog,
    SchemaColumn,
    SchemaTable,
)
from app.features.nl2sql.service import (
    JOB_CANCELLED_ERROR_CODE,
    JOB_INTERRUPTED_ERROR_CODE,
    Nl2SqlService,
    StoredJob,
    _new_job_steps,
)
from app.features.nl2sql.store import MemoryNl2SqlStore

_PROFILE_ID = "orders-profile"
_GENERATED = '{"sql":"SELECT ID FROM APP.ORDERS","explanation":"注文 ID を取得します。"}'


class _FakeEnterpriseAiClient:
    def __init__(self, text: str) -> None:
        self.text = text

    def is_configured(self) -> bool:
        return True

    def model_id(self) -> str:
        return "enterprise-nl2sql-model"

    def generate(
        self,
        *,
        prompt: str,
        context: str,
        system_prompt: str,
        timeout_seconds: float | None = None,
        max_output_tokens: int | None = None,
    ) -> str:
        del prompt, context, system_prompt, timeout_seconds, max_output_tokens
        return self.text


class _BlockingEnterpriseAiClient(_FakeEnterpriseAiClient):
    """generate_sql 段階で止まり、テスト側が release するまで戻らない。"""

    def __init__(self, text: str) -> None:
        super().__init__(text)
        self.entered = threading.Event()
        self.release = threading.Event()

    def generate(
        self,
        *,
        prompt: str,
        context: str,
        system_prompt: str,
        timeout_seconds: float | None = None,
        max_output_tokens: int | None = None,
    ) -> str:
        self.entered.set()
        del timeout_seconds, max_output_tokens
        assert self.release.wait(timeout=10), "テストが release しないまま timeout"
        return super().generate(prompt=prompt, context=context, system_prompt=system_prompt)


def _repository() -> MemoryIncrementalNl2SqlRepository:
    repository = MemoryIncrementalNl2SqlRepository(seed_default=False)
    catalog = SchemaCatalog(
        refreshed_at="2026-09-01T00:00:00+00:00",
        schema_fingerprint="job-runtime-v1",
        current_owner="APP",
        tables=[
            SchemaTable(
                owner="APP",
                table_name="ORDERS",
                logical_name="注文",
                columns=[
                    SchemaColumn(
                        column_name="ID", logical_name="ID", data_type="NUMBER", nullable=False
                    ),
                    SchemaColumn(
                        column_name="ORDER_NAME", logical_name="注文名", data_type="VARCHAR2"
                    ),
                    SchemaColumn(column_name="AMOUNT", logical_name="金額", data_type="NUMBER"),
                    SchemaColumn(column_name="CREATED_AT", logical_name="作成日", data_type="DATE"),
                ],
            )
        ],
    )
    manifest = {
        (table.owner.upper(), table.table_name.upper()): catalog.refreshed_at
        for table in catalog.tables
    }
    repository.apply_schema_refresh(
        catalog=catalog,
        manifest=manifest,
        changed_keys=set(manifest),
        deleted_keys=set(),
    )
    repository.save_profile(
        Nl2SqlProfile(id=_PROFILE_ID, name="注文管理", allowed_tables=["APP.ORDERS"]),
        expected_etag=None,
    )
    return repository


def _worker(
    repository: MemoryIncrementalNl2SqlRepository,
    client: _FakeEnterpriseAiClient | None = None,
) -> Nl2SqlService:
    """1 つの repository を共有する「別 worker プロセス」相当の service。"""

    service = Nl2SqlService(store=MemoryNl2SqlStore())
    service._incremental_repository = repository  # noqa: SLF001 - white-box contract test
    service._refresh_job_repository = repository  # noqa: SLF001
    service._persistence_ready = True  # noqa: SLF001
    service._persistence_writable = True  # noqa: SLF001
    service._cache_token_poll_seconds = 0.0  # noqa: SLF001
    service._catalog = repository.load_catalog()  # noqa: SLF001
    service._enterprise_ai_client = client or _FakeEnterpriseAiClient(_GENERATED)  # noqa: SLF001
    return service


def _request() -> JobCreateRequest:
    return JobCreateRequest(
        question="注文一覧を確認したい",
        engine=Nl2SqlEngine.ENTERPRISE_AI_DIRECT,
        profile_id=_PROFILE_ID,
    )


def _wait_terminal(service: Nl2SqlService, job_id: str) -> Any:
    job = None
    for _ in range(200):
        job = service.get_job(job_id)
        if job is not None and job.status in {JobStatus.DONE, JobStatus.ERROR}:
            return job
        time.sleep(0.01)
    raise AssertionError(f"job が terminal に達しない: {job}")


def test_observer_worker_rereads_in_flight_job_until_owner_finishes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repository = _repository()
    owner = _worker(repository)
    observer = _worker(repository)
    # owner 側の実行を止め、pending のまま永続化された状態を作る。
    monkeypatch.setattr(owner, "_run_job_safely", lambda _job_id: None)
    created = owner.start_job(_request(), actor_user_uuid="user-1", actor_is_system_admin=True)

    first = observer.get_job(created.job_id)
    assert first is not None
    assert first.status == JobStatus.PENDING

    with owner._lock:  # noqa: SLF001
        stored = owner._jobs[created.job_id]  # noqa: SLF001
        stored.status = JobStatus.DONE
        stored.finished_at = datetime.now(UTC).isoformat()
    owner._persist_job(created.job_id)  # noqa: SLF001

    # 旧実装は observer 側 cache を再読込せず、永遠に pending を返していた。
    second = observer.get_job(created.job_id)
    assert second is not None
    assert second.status == JobStatus.DONE


def test_owner_worker_keeps_local_state_authoritative_for_its_own_job(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repository = _repository()
    owner = _worker(repository)
    monkeypatch.setattr(owner, "_run_job_safely", lambda _job_id: None)
    created = owner.start_job(_request(), actor_user_uuid="user-1", actor_is_system_admin=True)

    # 別 worker が DB 上の snapshot を書き換えても、実行中プロセスは自分の状態を返す。
    document = repository.get_document("jobs", created.job_id)
    assert document is not None
    repository.put_document("jobs", created.job_id, {**document, "status": "error"})

    job = owner.get_job(created.job_id)
    assert job is not None
    assert job.status == JobStatus.PENDING


def test_cancel_from_other_worker_reaches_owner_at_stage_boundary() -> None:
    repository = _repository()
    blocking = _BlockingEnterpriseAiClient(_GENERATED)
    owner = _worker(repository, blocking)
    observer = _worker(repository)
    created = owner.start_job(_request(), actor_user_uuid="user-1", actor_is_system_admin=True)
    assert blocking.entered.wait(timeout=10)

    # 旧実装: observer は job を知らず None(404)、知っていても別プロセスの複製に
    # フラグを立てるだけで owner は止まらなかった。
    accepted = observer.request_job_cancel(created.job_id)
    assert accepted is not None
    assert accepted.status == JobStatus.RUNNING
    assert repository.get_document("job_cancel_requests", created.job_id) is not None

    blocking.release.set()
    finished = _wait_terminal(owner, created.job_id)
    assert finished.status == JobStatus.ERROR
    assert finished.error_code == JOB_CANCELLED_ERROR_CODE
    assert "キャンセル" in (finished.error_message or "")

    # error_code も snapshot 経由で他 worker に見える。
    seen = observer.get_job(created.job_id)
    assert seen is not None
    assert seen.status == JobStatus.ERROR
    assert seen.error_code == JOB_CANCELLED_ERROR_CODE


def test_cancel_of_unknown_job_returns_none() -> None:
    service = _worker(_repository())

    assert service.request_job_cancel("missing-job") is None


def test_cancel_of_terminal_job_is_noop_without_cancel_document() -> None:
    repository = _repository()
    owner = _worker(repository)
    created = owner.start_job(_request(), actor_user_uuid="user-1", actor_is_system_admin=True)
    finished = _wait_terminal(owner, created.job_id)
    assert finished.status == JobStatus.DONE

    result = owner.request_job_cancel(created.job_id)
    assert result is not None
    assert result.status == JobStatus.DONE
    assert repository.get_document("job_cancel_requests", created.job_id) is None


def _put_in_flight_snapshot(
    service: Nl2SqlService,
    repository: MemoryIncrementalNl2SqlRepository,
    *,
    job_id: str,
    updated_at: str,
) -> None:
    steps = _new_job_steps()
    steps[0] = steps[0].model_copy(update={"status": JobStepStatus.DONE, "elapsed_ms": 5})
    steps[1] = steps[1].model_copy(update={"status": JobStepStatus.RUNNING})
    stored = StoredJob(
        job_id=job_id,
        request=_request(),
        actor_user_uuid="user-1",
        actor_is_system_admin=True,
        status=JobStatus.RUNNING,
        created_at=updated_at,
        started_at=updated_at,
        steps=steps,
    )
    snapshot = {**service._job_to_snapshot(stored), "updated_at": updated_at}  # noqa: SLF001
    repository.put_document("jobs", job_id, snapshot, status="running")


def test_stale_in_flight_snapshot_is_closed_as_interrupted_and_persisted() -> None:
    repository = _repository()
    service = _worker(repository)
    two_hours_ago = (datetime.now(UTC) - timedelta(hours=2)).isoformat()
    _put_in_flight_snapshot(service, repository, job_id="job-orphan", updated_at=two_hours_ago)

    job = service.get_job("job-orphan")

    assert job is not None
    assert job.status == JobStatus.ERROR
    assert job.error_code == JOB_INTERRUPTED_ERROR_CODE
    assert "再起動" in (job.error_message or "")
    assert job.finished_at
    assert [step.status for step in job.steps][:2] == [JobStepStatus.DONE, JobStepStatus.ERROR]
    persisted = repository.get_document("jobs", "job-orphan")
    assert persisted is not None
    assert persisted["status"] == "error"
    assert persisted["error_code"] == JOB_INTERRUPTED_ERROR_CODE


def test_fresh_in_flight_snapshot_from_other_worker_stays_running() -> None:
    repository = _repository()
    service = _worker(repository)
    now = datetime.now(UTC).isoformat()
    _put_in_flight_snapshot(service, repository, job_id="job-live", updated_at=now)

    job = service.get_job("job-live")

    assert job is not None
    assert job.status == JobStatus.RUNNING
    persisted = repository.get_document("jobs", "job-live")
    assert persisted is not None
    assert persisted["status"] == "running"


def test_stale_threshold_is_configurable(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.settings import get_settings

    repository = _repository()
    service = _worker(repository)
    monkeypatch.setattr(get_settings(), "nl2sql_job_stale_after_seconds", 60.0)
    three_minutes_ago = (datetime.now(UTC) - timedelta(minutes=3)).isoformat()
    _put_in_flight_snapshot(
        service, repository, job_id="job-short-ttl", updated_at=three_minutes_ago
    )

    job = service.get_job("job-short-ttl")

    assert job is not None
    assert job.status == JobStatus.ERROR
    assert job.error_code == JOB_INTERRUPTED_ERROR_CODE


def test_run_job_safely_tolerates_missing_job_record() -> None:
    service = _worker(_repository())

    # 旧実装は self._jobs[job_id] の KeyError で worker スレッドごと落ちていた。
    service._run_job_safely("missing-job")  # noqa: SLF001
