"""取込ジョブを専用に消費するキューワーカー。

API リクエスト処理（event loop）から取込実行を切り離すための仕組み。
in-process dispatcher（lifespan で起動）でも、別プロセス
（``python -m app.rag.ingestion_worker``）でも同じ ``IngestionQueueWorker``
を使う。in-process dispatcher は設定により job 本体を
``python -m app.rag.ingestion_job_runner <job_id>`` subprocess へ隔離する。
複数ワーカーで同時に動かしても
``claim_ingestion_job`` の row lock により同一ジョブの二重実行は起きない。
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import signal
import sys
from collections.abc import Awaitable, Callable, Sequence
from datetime import UTC, datetime, timedelta

from app.clients.oracle import OracleClient, close_oracle_pool
from app.config import Settings, get_settings
from app.logging_config import configure_logging
from app.schemas.document import FileStatus, IngestionJob, IngestionJobStatus

logger = logging.getLogger(__name__)

JobRunner = Callable[[str], Awaitable[None]]
QueuedJobFetcher = Callable[[int], Awaitable[Sequence[IngestionJob]]]

# enqueue 側（同一プロセス内）から即時起床させるための通知イベント。
# 別プロセスのワーカーには届かないが、その場合は poll interval で拾う。
_WAKEUP = asyncio.Event()


def request_ingestion_worker_wakeup() -> None:
    """in-process ワーカーへ「新しいジョブがある」と通知する。"""
    _WAKEUP.set()


async def _default_fetch_queued(limit: int) -> Sequence[IngestionJob]:
    return await OracleClient().list_ingestion_jobs(
        status=IngestionJobStatus.QUEUED,
        limit=limit,
        offset=0,
        oldest_first=True,  # FIFO: 滞留 job の starvation を避ける。
    )


async def _default_job_runner(job_id: str) -> None:
    # 循環 import を避けるため遅延 import する。
    from app.api.routes.documents import _run_ingestion_job

    await _run_ingestion_job(job_id)


class IngestionJobSubprocessError(RuntimeError):
    """subprocess runner が異常終了した。親 worker が job を失敗へ戻す。"""


def _job_runner_for_settings(settings: Settings) -> JobRunner:
    if settings.ingestion_queue_process_isolation_enabled:
        return lambda job_id: run_ingestion_job_subprocess(
            job_id,
            timeout_seconds=settings.ingestion_job_subprocess_timeout_seconds,
        )
    return _default_job_runner


async def _terminate_process(process: asyncio.subprocess.Process) -> None:
    if process.returncode is not None:
        return
    process.terminate()
    try:
        await asyncio.wait_for(process.wait(), timeout=10)
    except TimeoutError:
        process.kill()
        await process.wait()


async def run_ingestion_job_subprocess(
    job_id: str,
    *,
    timeout_seconds: float | None = None,
) -> None:
    """1 job を別 Python process で実行し、API event loop / CUDA 初期化と隔離する。"""
    timeout = (
        timeout_seconds
        if timeout_seconds is not None
        else get_settings().rag_parser_service_timeout_seconds
    )
    process = await asyncio.create_subprocess_exec(
        sys.executable,
        "-m",
        "app.rag.ingestion_job_runner",
        job_id,
    )
    try:
        return_code = await asyncio.wait_for(process.wait(), timeout=timeout)
    except TimeoutError as exc:
        await _terminate_process(process)
        raise IngestionJobSubprocessError(
            f"ingestion job subprocess timed out after {timeout:g}s"
        ) from exc
    except asyncio.CancelledError:
        await _terminate_process(process)
        raise
    if return_code != 0:
        raise IngestionJobSubprocessError(
            f"ingestion job subprocess exited with code {return_code}"
        )


class IngestionQueueWorker:
    """``ingestion_jobs`` キューをポーリングし、QUEUED ジョブを並行実行する。"""

    def __init__(
        self,
        *,
        settings: Settings,
        job_runner: JobRunner | None = None,
        fetch_queued: QueuedJobFetcher | None = None,
        recover_stale: Callable[[], Awaitable[Sequence[IngestionJob]]] | None = None,
        concurrency: int | None = None,
        poll_interval_seconds: float | None = None,
    ) -> None:
        self._settings = settings
        self._job_runner = job_runner or _job_runner_for_settings(settings)
        self._fetch_queued = fetch_queued or _default_fetch_queued
        self._recover_stale = recover_stale or self._default_recover_stale
        self._concurrency = max(1, concurrency or settings.ingestion_queue_worker_concurrency)
        self._poll_interval = (
            poll_interval_seconds or settings.ingestion_queue_poll_interval_seconds
        )
        self._recovery_interval = settings.ingestion_queue_recovery_interval_seconds
        self._last_recovery_at: float | None = None
        self._inflight: set[str] = set()
        self._tasks: set[asyncio.Task[None]] = set()

    async def run_forever(self, *, stop_event: asyncio.Event | None = None) -> None:
        """停止イベントが立つまでキューを消費し続ける。"""
        stop_event = stop_event or asyncio.Event()
        await self._recover_stale_safely()
        self._last_recovery_at = asyncio.get_running_loop().time()
        logger.info(
            "ingestion_worker_started",
            extra={"concurrency": self._concurrency, "poll_interval": self._poll_interval},
        )
        try:
            while not stop_event.is_set():
                dispatched = await self._dispatch_available()
                if dispatched == 0:
                    # アイドル時に、クラッシュで固着した文書/ジョブを定期回復する。
                    await self._recover_stale_if_due()
                    await self._wait_for_work(stop_event)
        finally:
            if self._settings.ingestion_queue_process_isolation_enabled:
                await self._cancel_inflight()
            else:
                await self._drain_inflight()
            logger.info("ingestion_worker_stopped")

    async def _default_recover_stale(self) -> Sequence[IngestionJob]:
        stale_before = datetime.now(UTC) - timedelta(
            seconds=self._settings.ingestion_queue_stale_running_seconds
        )
        return await OracleClient().recover_stale_ingestion_jobs(
            stale_before=stale_before,
            limit=self._settings.ingestion_queue_startup_drain_limit,
        )

    async def _recover_stale_if_due(self) -> None:
        """前回の回復から recovery interval を超えていれば再度回復する。"""
        now = asyncio.get_running_loop().time()
        if (
            self._last_recovery_at is not None
            and now - self._last_recovery_at < self._recovery_interval
        ):
            return
        self._last_recovery_at = now
        await self._recover_stale_safely()

    async def _recover_stale_safely(self) -> None:
        try:
            recovered = await self._recover_stale()
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("ingestion_worker_stale_recovery_failed")
            return
        if recovered:
            logger.info(
                "ingestion_worker_recovered_stale_jobs",
                extra={"job_count": len(recovered)},
            )

    async def _dispatch_available(self) -> int:
        """空きスロット分だけ QUEUED ジョブを取り出して実行タスクを起動する。"""
        free = self._concurrency - len(self._inflight)
        if free <= 0:
            return 0
        try:
            jobs = await self._fetch_queued(free)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("ingestion_worker_fetch_failed")
            return 0
        dispatched = 0
        for job in jobs:
            if job.id in self._inflight:
                continue
            self._inflight.add(job.id)
            task = asyncio.create_task(self._run_job(job.id))
            self._tasks.add(task)
            task.add_done_callback(self._tasks.discard)
            dispatched += 1
            if len(self._inflight) >= self._concurrency:
                break
        return dispatched

    async def _run_job(self, job_id: str) -> None:
        try:
            await self._job_runner(job_id)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            await _mark_running_job_failed(job_id, error=exc)
            logger.exception("ingestion_worker_job_failed", extra={"job_id": job_id})
        finally:
            self._inflight.discard(job_id)
            # スロットが空いたので次サイクルを即座に回す。
            _WAKEUP.set()

    async def _wait_for_work(self, stop_event: asyncio.Event) -> None:
        """新ジョブ通知・停止・poll interval のいずれかまで待つ。"""
        wakeup_waiter = asyncio.ensure_future(_WAKEUP.wait())
        stop_waiter = asyncio.ensure_future(stop_event.wait())
        try:
            await asyncio.wait(
                {wakeup_waiter, stop_waiter},
                timeout=self._poll_interval,
                return_when=asyncio.FIRST_COMPLETED,
            )
        finally:
            for waiter in (wakeup_waiter, stop_waiter):
                waiter.cancel()
            # キャンセル済み待機タスクを回収し、pending-destroy 警告を避ける。
            await asyncio.gather(wakeup_waiter, stop_waiter, return_exceptions=True)
            _WAKEUP.clear()

    async def _drain_inflight(self) -> None:
        """シャットダウン時に実行中タスクの完了を待つ。"""
        pending = list(self._tasks)
        if pending:
            logger.info("ingestion_worker_draining", extra={"inflight": len(pending)})
            await asyncio.gather(*pending, return_exceptions=True)

    async def _cancel_inflight(self) -> None:
        """API プロセス内 dispatcher 停止時は subprocess job を待ち続けず終了させる。"""
        pending = list(self._tasks)
        if pending:
            logger.info("ingestion_worker_cancelling", extra={"inflight": len(pending)})
            for task in pending:
                task.cancel()
            await asyncio.gather(*pending, return_exceptions=True)


async def _mark_running_job_failed(job_id: str, *, error: Exception) -> None:
    """subprocess が落ちた時に RUNNING のまま放置しない。"""
    try:
        oracle = OracleClient()
        job = await oracle.get_ingestion_job(job_id)
        if job is None or job.status != IngestionJobStatus.RUNNING:
            return
        message = str(error)[:500] or "取込ジョブ実行プロセスが異常終了しました。"
        await oracle.update_ingestion_job(
            job_id,
            status=IngestionJobStatus.FAILED,
            error_message=message,
            finished_at=datetime.now(UTC),
        )
        await oracle.update_document_status(job.document_id, FileStatus.ERROR, message)
    except Exception:
        logger.exception(
            "ingestion_worker_job_failure_mark_failed_failed",
            extra={"job_id": job_id},
        )


async def run_worker_process() -> None:
    """別プロセスのエントリポイント。SIGINT/SIGTERM で graceful に停止する。"""
    settings = get_settings()
    configure_logging(settings.log_level)
    stop_event = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        with contextlib.suppress(NotImplementedError):  # pragma: no cover - Windows 等
            loop.add_signal_handler(sig, stop_event.set)
    worker = IngestionQueueWorker(settings=settings)
    try:
        await worker.run_forever(stop_event=stop_event)
    finally:
        close_oracle_pool()


def main() -> None:
    asyncio.run(run_worker_process())


if __name__ == "__main__":
    main()
