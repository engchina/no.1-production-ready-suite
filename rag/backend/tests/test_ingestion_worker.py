"""取込キューワーカーのテスト。"""

import asyncio
import sys
from collections.abc import Iterator, Sequence
from datetime import UTC, datetime

import pytest

from app.config import get_settings
from app.rag import ingestion_worker
from app.rag.ingestion_worker import IngestionJobSubprocessError, IngestionQueueWorker
from app.schemas.document import FileStatus, IngestionJob, IngestionJobStatus


def _job(job_id: str) -> IngestionJob:
    return IngestionJob(
        id=job_id,
        document_id=f"doc-{job_id}",
        status=IngestionJobStatus.QUEUED,
        parser_profile="local_text_structure",
        queued_at=datetime.now(UTC),
    )


@pytest.fixture(autouse=True)
def _reset_wakeup() -> Iterator[None]:
    ingestion_worker._WAKEUP.clear()
    yield
    ingestion_worker._WAKEUP.clear()


async def test_worker_runs_all_queued_jobs() -> None:
    """QUEUED ジョブを取り出して全件 job_runner に渡す。"""
    queued = [_job(f"j{i}") for i in range(3)]
    executed: list[str] = []
    stop = asyncio.Event()

    async def fetch(limit: int) -> Sequence[IngestionJob]:
        batch = queued[:limit]
        del queued[:limit]
        return batch

    async def runner(job_id: str) -> None:
        executed.append(job_id)
        if len(executed) == 3:
            stop.set()

    async def recover() -> Sequence[IngestionJob]:
        return []

    worker = IngestionQueueWorker(
        settings=get_settings(),
        job_runner=runner,
        fetch_queued=fetch,
        recover_stale=recover,
        concurrency=2,
        poll_interval_seconds=0.05,
    )
    await asyncio.wait_for(worker.run_forever(stop_event=stop), timeout=5)

    assert sorted(executed) == ["j0", "j1", "j2"]


async def test_worker_respects_concurrency_limit() -> None:
    """同時実行数は concurrency を超えない。"""
    queued = [_job(f"j{i}") for i in range(5)]
    stop = asyncio.Event()
    current = 0
    observed_max = 0
    completed = 0

    async def fetch(limit: int) -> Sequence[IngestionJob]:
        batch = queued[:limit]
        del queued[:limit]
        return batch

    async def runner(job_id: str) -> None:
        nonlocal current, observed_max, completed
        current += 1
        observed_max = max(observed_max, current)
        await asyncio.sleep(0.05)
        current -= 1
        completed += 1
        if completed == 5:
            stop.set()

    async def recover() -> Sequence[IngestionJob]:
        return []

    worker = IngestionQueueWorker(
        settings=get_settings(),
        job_runner=runner,
        fetch_queued=fetch,
        recover_stale=recover,
        concurrency=2,
        poll_interval_seconds=0.05,
    )
    await asyncio.wait_for(worker.run_forever(stop_event=stop), timeout=5)

    assert completed == 5
    assert observed_max == 2


async def test_worker_does_not_redispatch_inflight_job() -> None:
    """同じ QUEUED ジョブが続けて返っても二重起動しない。"""
    job = _job("dup")
    starts: list[str] = []
    release = asyncio.Event()
    stop = asyncio.Event()

    async def fetch(limit: int) -> Sequence[IngestionJob]:
        # claim 前の窓で同じジョブが見え続ける状況を模す。
        return [job]

    async def runner(job_id: str) -> None:
        starts.append(job_id)
        await release.wait()

    async def recover() -> Sequence[IngestionJob]:
        return []

    worker = IngestionQueueWorker(
        settings=get_settings(),
        job_runner=runner,
        fetch_queued=fetch,
        recover_stale=recover,
        concurrency=2,
        poll_interval_seconds=0.01,
    )
    task = asyncio.create_task(worker.run_forever(stop_event=stop))
    await asyncio.sleep(0.1)
    release.set()
    stop.set()
    ingestion_worker.request_ingestion_worker_wakeup()
    await asyncio.wait_for(task, timeout=5)

    # 実行中（in-flight）の間は同一 id を再ディスパッチしない。
    assert starts.count("dup") == 1


def test_dispatch_uses_worker_wakeup(monkeypatch: pytest.MonkeyPatch) -> None:
    """HTTP 入口では取込を実行せず、ワーカーへ起床通知のみ行う。"""
    from app.api.routes import documents

    dedicated = get_settings().model_copy(update={"ingestion_queue_dedicated_worker_enabled": True})
    monkeypatch.setattr(documents, "get_settings", lambda: dedicated)
    ingestion_worker._WAKEUP.clear()

    documents._dispatch_ingestion_job("job-1", force=False)

    assert ingestion_worker._WAKEUP.is_set()


def test_dispatch_still_only_wakes_worker_when_dedicated_mode_is_disabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """legacy 設定でも FastAPI BackgroundTasks へ戻さない。"""
    from app.api.routes import documents

    inline = get_settings().model_copy(update={"ingestion_queue_dedicated_worker_enabled": False})
    monkeypatch.setattr(documents, "get_settings", lambda: inline)
    ingestion_worker._WAKEUP.clear()

    documents._dispatch_ingestion_job("job-2", force=True)

    assert ingestion_worker._WAKEUP.is_set()


class _FakeProcess:
    def __init__(self, return_code: int) -> None:
        self.returncode: int | None = None
        self._return_code = return_code
        self.terminated = False
        self.killed = False

    async def wait(self) -> int:
        self.returncode = self._return_code
        return self._return_code

    def terminate(self) -> None:
        self.terminated = True

    def kill(self) -> None:
        self.killed = True


class _HangingProcess(_FakeProcess):
    def __init__(self) -> None:
        super().__init__(-15)
        self._done = asyncio.Event()

    async def wait(self) -> int:
        await self._done.wait()
        self.returncode = self._return_code
        return self._return_code

    def terminate(self) -> None:
        super().terminate()
        self._done.set()

    def kill(self) -> None:
        super().kill()
        self._return_code = -9
        self._done.set()


async def test_subprocess_runner_invokes_single_job_runner(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """process isolation は job id を単発 runner へ渡す。"""
    captured: list[tuple[str, ...]] = []

    async def fake_create_subprocess_exec(*cmd: str) -> _FakeProcess:
        captured.append(cmd)
        return _FakeProcess(0)

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_create_subprocess_exec)

    await ingestion_worker.run_ingestion_job_subprocess("job-subprocess")

    assert captured == [
        (
            sys.executable,
            "-m",
            "app.rag.ingestion_job_runner",
            "job-subprocess",
        )
    ]


async def test_subprocess_runner_raises_without_finishing_job_on_nonzero_exit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """child process 異常終了時は worker 側で成功扱いしない。"""

    async def fake_create_subprocess_exec(*cmd: str) -> _FakeProcess:
        _ = cmd
        return _FakeProcess(7)

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_create_subprocess_exec)

    with pytest.raises(IngestionJobSubprocessError):
        await ingestion_worker.run_ingestion_job_subprocess("job-failed-child")


async def test_subprocess_runner_times_out_and_terminates_child(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """parser 子プロセスが固着したら job runner を待ち続けない。"""
    process = _HangingProcess()

    async def fake_create_subprocess_exec(*cmd: str) -> _HangingProcess:
        _ = cmd
        return process

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_create_subprocess_exec)

    with pytest.raises(IngestionJobSubprocessError, match="timed out"):
        await ingestion_worker.run_ingestion_job_subprocess(
            "job-timeout",
            timeout_seconds=0.01,
        )

    assert process.terminated is True


async def test_worker_marks_running_job_failed_when_runner_crashes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """子プロセス異常終了後に RUNNING job を放置しない。"""
    queued = [_job("crash")]
    running = queued[0].model_copy(update={"status": IngestionJobStatus.RUNNING})
    updates: dict[str, object] = {}
    stop = asyncio.Event()

    class FakeOracle:
        async def get_ingestion_job(self, job_id: str) -> IngestionJob:
            assert job_id == "crash"
            return running

        async def update_ingestion_job(self, job_id: str, **kwargs: object) -> IngestionJob:
            assert job_id == "crash"
            updates.update(kwargs)
            stop.set()
            return running.model_copy(update=kwargs)

        async def update_document_status(
            self,
            document_id: str,
            status: FileStatus,
            error_message: str | None = None,
        ) -> None:
            updates["document_id"] = document_id
            updates["document_status"] = status
            updates["document_error"] = error_message

    async def fetch(limit: int) -> Sequence[IngestionJob]:
        _ = limit
        batch = queued[:]
        queued.clear()
        return batch

    async def runner(job_id: str) -> None:
        assert job_id == "crash"
        raise IngestionJobSubprocessError("child died")

    async def recover() -> Sequence[IngestionJob]:
        return []

    monkeypatch.setattr(ingestion_worker, "OracleClient", FakeOracle)
    worker = IngestionQueueWorker(
        settings=get_settings(),
        job_runner=runner,
        fetch_queued=fetch,
        recover_stale=recover,
        concurrency=1,
        poll_interval_seconds=0.01,
    )
    await asyncio.wait_for(worker.run_forever(stop_event=stop), timeout=5)

    assert updates["status"] is IngestionJobStatus.FAILED
    assert updates["error_message"] == "child died"
    assert updates["document_status"] is FileStatus.ERROR


async def test_default_fetch_uses_fifo_order(monkeypatch: pytest.MonkeyPatch) -> None:
    """キュー消費は古い順(FIFO)で取り出し、滞留 job を starvation させない。"""
    captured: dict[str, object] = {}

    async def fake_list(self: object, **kwargs: object) -> Sequence[IngestionJob]:
        captured.update(kwargs)
        return []

    monkeypatch.setattr(
        "app.clients.oracle.OracleClient.list_ingestion_jobs",
        fake_list,
    )
    result = await ingestion_worker._default_fetch_queued(5)

    assert result == []
    assert captured["oldest_first"] is True
    assert captured["status"] is IngestionJobStatus.QUEUED
    assert captured["limit"] == 5


async def test_worker_recovers_stale_jobs_before_consuming() -> None:
    """起動時に stale RUNNING ジョブの回復を試みる。"""
    recovered_calls = 0
    stop = asyncio.Event()

    async def fetch(limit: int) -> Sequence[IngestionJob]:
        stop.set()
        return []

    async def runner(job_id: str) -> None:  # pragma: no cover - 呼ばれない
        raise AssertionError("no queued job expected")

    async def recover() -> Sequence[IngestionJob]:
        nonlocal recovered_calls
        recovered_calls += 1
        return [_job("stale")]

    worker = IngestionQueueWorker(
        settings=get_settings(),
        job_runner=runner,
        fetch_queued=fetch,
        recover_stale=recover,
        concurrency=2,
        poll_interval_seconds=0.01,
    )
    await asyncio.wait_for(worker.run_forever(stop_event=stop), timeout=5)

    assert recovered_calls == 1


async def test_worker_recovers_stale_jobs_periodically_when_idle() -> None:
    """アイドル中は recovery interval ごとに固着回復を再実行する。"""
    recovered_calls = 0
    fetch_calls = 0
    stop = asyncio.Event()

    async def fetch(limit: int) -> Sequence[IngestionJob]:
        nonlocal fetch_calls
        fetch_calls += 1
        if fetch_calls >= 3:
            stop.set()
        return []

    async def runner(job_id: str) -> None:  # pragma: no cover - 呼ばれない
        raise AssertionError("no queued job expected")

    async def recover() -> Sequence[IngestionJob]:
        nonlocal recovered_calls
        recovered_calls += 1
        return []

    settings = get_settings().model_copy(update={"ingestion_queue_recovery_interval_seconds": 0.0})
    worker = IngestionQueueWorker(
        settings=settings,
        job_runner=runner,
        fetch_queued=fetch,
        recover_stale=recover,
        concurrency=2,
        poll_interval_seconds=0.01,
    )
    await asyncio.wait_for(worker.run_forever(stop_event=stop), timeout=5)

    # 起動時 1 回 + アイドルサイクルでの定期回復で 2 回以上呼ばれる。
    assert recovered_calls >= 2
