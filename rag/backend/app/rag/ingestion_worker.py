"""取込ジョブを専用に消費するキューワーカー。

API リクエスト処理（event loop）から取込実行を切り離すための仕組み。
in-process dispatcher（lifespan で起動）でも、別プロセス
（``python -m app.rag.ingestion_worker``）でも同じ ``IngestionQueueWorker``
を使う。in-process dispatcher は設定により job 本体を
``python -m app.rag.ingestion_job_runner <job_id>`` subprocess へ隔離する。
複数ワーカーで同時に動かしても
``claim_ingestion_job`` の row lock により同一ジョブの二重実行は起きない。

lease と heartbeat(#357):
- worker はプロセスごとに一意の ``worker_id`` を持ち、claim で job の lease を取る
  (``rag_ingestion_jobs.lease_owner`` / ``heartbeat_at``)。
- 実行中は ``heartbeat_interval`` ごとに自分の lease の RUNNING job の heartbeat を更新する。
  stale の回復は heartbeat が TTL を超えて途絶えた job だけを戻し、自分の lease の job は戻さない。
  job の長さの上限は job の timeout だけが持つ。
- 停止(SIGTERM / lifespan の終了)では新しい job を取らず、``shutdown_grace_seconds`` まで
  実行中の job を待つ。終わらなければ子を止め、自分の lease の job を QUEUED に戻す
  (attempt は増やさない)。
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import signal
import socket
import sys
import uuid
from collections.abc import Awaitable, Callable, Sequence
from datetime import UTC, datetime, timedelta

from app.clients.oracle import OracleClient, close_oracle_pool, oracle_error_log_fields
from app.config import Settings, get_settings
from app.logging_config import configure_logging
from app.schemas.document import FileStatus, IngestionJob, IngestionJobStatus

logger = logging.getLogger(__name__)

JobRunner = Callable[[str], Awaitable[None]]
QueuedJobFetcher = Callable[[int], Awaitable[Sequence[IngestionJob]]]
SchemaReadinessChecker = Callable[[], Awaitable[bool]]
LeaseHeartbeat = Callable[[], Awaitable[int]]
LeasedJobRequeuer = Callable[[], Awaitable[Sequence[IngestionJob]]]
Clock = Callable[[], datetime]

# lease_owner 列(VARCHAR2(128))に収める。
_WORKER_ID_HOST_MAX_CHARS = 80
# 子プロセスに SIGTERM を送ってから SIGKILL するまでの秒数。systemd の TimeoutStopSec は
# 停止の grace + これ + QUEUED に戻す DB の処理より長くする(init_script.sh。テストで照合する)。
CHILD_TERMINATE_TIMEOUT_SECONDS = 10.0

# enqueue 側（同一プロセス内）から即時起床させるための通知イベント。
# 別プロセスのワーカーには届かないが、その場合は poll interval で拾う。
_WAKEUP = asyncio.Event()


def request_ingestion_worker_wakeup() -> None:
    """in-process ワーカーへ「新しいジョブがある」と通知する。"""
    _WAKEUP.set()


def new_worker_id() -> str:
    """lease_owner に使う、worker プロセスごとに一意な識別子(host:pid:乱数)。"""
    host = (socket.gethostname() or "worker")[:_WORKER_ID_HOST_MAX_CHARS]
    return f"{host}:{os.getpid()}:{uuid.uuid4().hex[:12]}"


def _utc_now() -> datetime:
    return datetime.now(UTC)


async def _default_job_runner(job_id: str, *, lease_owner: str | None = None) -> None:
    # 循環 import を避けるため遅延 import する。
    from app.api.routes.documents import _run_ingestion_job

    await _run_ingestion_job(job_id, lease_owner=lease_owner)


async def _default_schema_ready() -> bool:
    from app.rag.system_schema_runtime import system_schema_runtime

    return await system_schema_runtime.is_ready()


class IngestionJobSubprocessError(RuntimeError):
    """subprocess runner が異常終了した。親 worker が job を失敗へ戻す。"""


def _job_runner_for_settings(settings: Settings, *, lease_owner: str | None = None) -> JobRunner:
    if settings.ingestion_queue_process_isolation_enabled:
        return lambda job_id: run_ingestion_job_subprocess(
            job_id,
            timeout_seconds=settings.ingestion_job_subprocess_timeout_seconds,
            lease_owner=lease_owner,
        )
    return lambda job_id: _default_job_runner(job_id, lease_owner=lease_owner)


async def _terminate_process(process: asyncio.subprocess.Process) -> None:
    if process.returncode is not None:
        return
    process.terminate()
    try:
        await asyncio.wait_for(process.wait(), timeout=CHILD_TERMINATE_TIMEOUT_SECONDS)
    except TimeoutError:
        process.kill()
        await process.wait()


async def run_ingestion_job_subprocess(
    job_id: str,
    *,
    timeout_seconds: float | None = None,
    lease_owner: str | None = None,
) -> None:
    """1 job を別 Python process で実行し、API event loop / CUDA 初期化と隔離する。

    ``lease_owner`` は子の claim が取る lease の持ち主(この worker)。heartbeat は親が打つ。
    """
    timeout = (
        timeout_seconds
        if timeout_seconds is not None
        else get_settings().rag_parser_service_timeout_seconds
    )
    command = [sys.executable, "-m", "app.rag.ingestion_job_runner", job_id]
    if lease_owner is not None:
        command += ["--lease-owner", lease_owner]
    process = await asyncio.create_subprocess_exec(*command)
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
        schema_ready: SchemaReadinessChecker | None = None,
        concurrency: int | None = None,
        poll_interval_seconds: float | None = None,
        worker_id: str | None = None,
        heartbeat: LeaseHeartbeat | None = None,
        requeue_leased: LeasedJobRequeuer | None = None,
        heartbeat_interval_seconds: float | None = None,
        shutdown_grace_seconds: float | None = None,
        clock: Clock | None = None,
    ) -> None:
        self._settings = settings
        self.worker_id = worker_id or new_worker_id()
        self._job_runner = job_runner or _job_runner_for_settings(
            settings, lease_owner=self.worker_id
        )
        self._fetch_queued = fetch_queued or self._default_fetch_queued
        self._recover_stale = recover_stale or self._default_recover_stale
        self._heartbeat = heartbeat or self._default_heartbeat
        self._requeue_leased = requeue_leased or self._default_requeue_leased
        self._schema_ready = schema_ready or _default_schema_ready
        self._clock = clock or _utc_now
        self._concurrency = max(1, concurrency or settings.ingestion_queue_worker_concurrency)
        self._poll_interval = (
            poll_interval_seconds or settings.ingestion_queue_poll_interval_seconds
        )
        self._heartbeat_interval = (
            heartbeat_interval_seconds or settings.ingestion_queue_heartbeat_interval_seconds
        )
        self._shutdown_grace = (
            shutdown_grace_seconds
            if shutdown_grace_seconds is not None
            else settings.ingestion_queue_shutdown_grace_seconds
        )
        self._recovery_interval = settings.ingestion_queue_recovery_interval_seconds
        self._last_recovery_at: float | None = None
        self._inflight: set[str] = set()
        self._tasks: set[asyncio.Task[None]] = set()
        self._last_schema_state: str | None = None
        self._recovery_initialized = False
        self._stop_event: asyncio.Event | None = None
        # 停止中に打ち切られた job がある(停止処理で自分の lease の job を QUEUED に戻す)。
        self._interrupted_on_stop = False

    async def run_forever(self, *, stop_event: asyncio.Event | None = None) -> None:
        """停止イベントが立つまでキューを消費し続ける。"""
        stop_event = stop_event or asyncio.Event()
        self._stop_event = stop_event
        logger.info(
            "ingestion_worker_started",
            extra={
                "worker_id": self.worker_id,
                "concurrency": self._concurrency,
                "poll_interval": self._poll_interval,
                "heartbeat_interval": self._heartbeat_interval,
                "shutdown_grace_seconds": self._shutdown_grace,
            },
        )
        heartbeat_task = asyncio.create_task(self._heartbeat_loop())
        try:
            while not stop_event.is_set():
                if not await self._schema_is_ready():
                    await self._wait_for_work(stop_event)
                    continue
                if not self._recovery_initialized:
                    await self._recover_stale_safely()
                    self._last_recovery_at = asyncio.get_running_loop().time()
                    self._recovery_initialized = True
                if stop_event.is_set():
                    break
                dispatched = await self._dispatch_available()
                if dispatched == 0:
                    # アイドル時に、クラッシュで固着した文書/ジョブを定期回復する。
                    await self._recover_stale_if_due()
                    await self._wait_for_work(stop_event)
        finally:
            # 停止後は新しい job を取らない(このループを抜けた時点で dispatch しない)。
            stop_event.set()
            await self._shutdown(heartbeat_task)
            logger.info("ingestion_worker_stopped", extra={"worker_id": self.worker_id})

    async def _schema_is_ready(self) -> bool:
        """schema 未作成/操作中は queue table へ触れず、状態変化だけを記録する。"""

        try:
            ready = await self._schema_ready()
        except asyncio.CancelledError:
            raise
        except Exception:
            state = "unavailable"
            if self._last_schema_state != state:
                logger.exception("ingestion_worker_schema_probe_failed")
                self._last_schema_state = state
            return False

        state = "ready" if ready else "setup_required"
        if state != self._last_schema_state:
            if ready:
                logger.info("ingestion_worker_schema_ready")
            else:
                logger.warning(
                    "ingestion_worker_schema_setup_required",
                    extra={
                        "advice": (
                            "システム設定 > データベースでシステムテーブルを作成・更新してください"
                        )
                    },
                )
            self._last_schema_state = state
        return ready

    async def _default_fetch_queued(self, limit: int) -> Sequence[IngestionJob]:
        # FIFO で、claim できない job(同じ文書の job が RUNNING)と自分が実行中の job を除く。
        # 除かないと、先頭の claim できない job が毎回 free 枠を埋め、後ろの job が流れない(#357)。
        return await OracleClient().list_dispatchable_ingestion_jobs(
            limit=limit,
            exclude_job_ids=sorted(self._inflight),
        )

    async def _default_recover_stale(self) -> Sequence[IngestionJob]:
        now = self._clock()
        return await OracleClient().recover_stale_ingestion_jobs(
            stale_before=now
            - timedelta(seconds=self._settings.ingestion_queue_stale_running_seconds),
            heartbeat_stale_before=now
            - timedelta(seconds=self._settings.ingestion_queue_lease_ttl_seconds),
            limit=self._settings.ingestion_queue_startup_drain_limit,
            exclude_lease_owner=self.worker_id,
        )

    async def _default_heartbeat(self) -> int:
        return await OracleClient().heartbeat_ingestion_jobs(
            lease_owner=self.worker_id,
            heartbeat_at=self._clock(),
        )

    async def _default_requeue_leased(self) -> Sequence[IngestionJob]:
        return await OracleClient().requeue_leased_ingestion_jobs(lease_owner=self.worker_id)

    async def _heartbeat_loop(self) -> None:
        """実行中の job がある間、heartbeat_interval ごとに自分の lease を延長する。"""
        while True:
            await asyncio.sleep(self._heartbeat_interval)
            if self._inflight:
                await self._heartbeat_safely()

    async def _heartbeat_safely(self) -> None:
        try:
            await self._heartbeat()
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            # 1 回の失敗では lease は切れない(TTL は間隔の 3 倍以上)。次の間隔で再試行する。
            logger.warning(
                "ingestion_worker_heartbeat_failed",
                extra={"worker_id": self.worker_id, **oracle_error_log_fields(exc)},
                exc_info=True,
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
        except Exception as exc:
            logger.exception(
                "ingestion_worker_stale_recovery_failed", extra=oracle_error_log_fields(exc)
            )
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
        except Exception as exc:
            logger.exception("ingestion_worker_fetch_failed", extra=oracle_error_log_fields(exc))
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
            if self._stop_event is not None and self._stop_event.is_set():
                # 停止中に子が止まった(端末の Ctrl+C で子にも signal が届いた等)。失敗にせず、
                # 停止処理が自分の lease の job として QUEUED に戻す(#357)。
                logger.warning(
                    "ingestion_worker_job_interrupted",
                    extra={"job_id": job_id, "worker_id": self.worker_id},
                )
                self._interrupted_on_stop = True
                return
            await _mark_running_job_failed(job_id, error=exc)
            logger.exception(
                "ingestion_worker_job_failed",
                extra={"job_id": job_id, **oracle_error_log_fields(exc)},
            )
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

    async def _shutdown(self, heartbeat_task: asyncio.Task[None]) -> None:
        """実行中の job を grace まで待ち、残りは止めて自分の lease の job を QUEUED に戻す。

        grace の間も heartbeat を打ち続け、他の worker に回復されないようにする。子プロセスは
        ``run_ingestion_job_subprocess`` の取り消しで SIGTERM(10 秒で SIGKILL)を送って止める。
        """
        pending = [task for task in self._tasks if not task.done()]
        had_inflight = bool(pending)
        try:
            if pending and self._shutdown_grace > 0:
                logger.info(
                    "ingestion_worker_draining",
                    extra={
                        "worker_id": self.worker_id,
                        "inflight": len(pending),
                        "grace_seconds": self._shutdown_grace,
                    },
                )
                _, still_running = await asyncio.wait(pending, timeout=self._shutdown_grace)
                pending = list(still_running)
            if pending:
                logger.warning(
                    "ingestion_worker_cancelling",
                    extra={"worker_id": self.worker_id, "inflight": len(pending)},
                )
                for task in pending:
                    task.cancel()
                await asyncio.gather(*pending, return_exceptions=True)
        finally:
            heartbeat_task.cancel()
            await asyncio.gather(heartbeat_task, return_exceptions=True)
            if had_inflight or self._interrupted_on_stop:
                await self._requeue_leased_safely()

    async def _requeue_leased_safely(self) -> None:
        """止めた job を QUEUED に戻す。失敗したら lease の TTL 後に他の worker が回復する。"""
        try:
            requeued = await self._requeue_leased()
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.exception(
                "ingestion_worker_requeue_leased_failed",
                extra={"worker_id": self.worker_id, **oracle_error_log_fields(exc)},
            )
            return
        if requeued:
            logger.warning(
                "ingestion_worker_requeued_leased_jobs",
                extra={
                    "worker_id": self.worker_id,
                    "job_ids": [job.id for job in requeued],
                },
            )


async def _mark_running_job_failed(job_id: str, *, error: Exception) -> None:
    """subprocess が落ちた時に RUNNING のまま放置しない。

    - RUNNING の job は FAILED にする(RUNNING のときだけ書く。#305)。レシピの job は
      レシピ行だけを ERROR にし、文書(全レシピの集約)の status は変えない。文書単位の job は
      文書を ERROR にする(子が落ちたので pipeline は書けていない)。
    - 取り消し済みの job は、取り消しを検知した側として文書・レシピの status を戻す。
    """
    try:
        # 循環 import を避けるため遅延 import する。
        from app.api.routes.documents import _fail_ingestion_job, _restore_statuses_after_cancel

        oracle = OracleClient()
        job = await oracle.get_ingestion_job(job_id)
        if job is None:
            return
        if job.status == IngestionJobStatus.CANCELLED:
            await _restore_statuses_after_cancel(oracle, job)
            return
        if job.status != IngestionJobStatus.RUNNING:
            return
        message = str(error)[:500] or "取込ジョブ実行プロセスが異常終了しました。"
        failed = await _fail_ingestion_job(oracle, job, message)
        if failed and job.recipe_id is None:
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
