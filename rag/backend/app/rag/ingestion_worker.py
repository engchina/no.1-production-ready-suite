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
  job の長さは、進捗の停滞(``ingestion_job_stall_timeout_seconds``)と全体の上限
  (``ingestion_job_subprocess_timeout_seconds``)で切る(#2003。``run_ingestion_job_subprocess``)。
- heartbeat の時刻と TTL の判定は DB の時計(``SYSTIMESTAMP``)で行い、worker を別の host で
  動かしても時計のずれに左右されない(#359)。worker の時計(``clock``)は、heartbeat の無い
  lease 導入前の行の判定(開始からの経過時間)にだけ使う。
- 完了・失敗・再キューは自分の lease の job のときだけ書く。heartbeat が途絶えて別の worker が
  claim し直した後の古い実行の結果は捨てる(#359)。
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
from collections.abc import Awaitable, Callable, Collection, Sequence
from datetime import UTC, datetime, timedelta

from pr_backend_core.observability.request_context import bind_log_context

from app.clients.oracle import OracleClient, close_oracle_pool, oracle_error_log_fields
from app.config import Settings, get_settings
from app.logging_config import configure_logging
from app.schemas.document import (
    INGESTION_HEAVY_PHASES,
    FileStatus,
    IngestionJob,
    IngestionJobPhase,
    IngestionJobProgress,
    IngestionJobStatus,
)

logger = logging.getLogger(__name__)

JobRunner = Callable[[str], Awaitable[None]]
# (取る件数の上限, 除く段)。除く段は重い段の上限に達したときの PREPROCESS・EXTRACT(#2027)。
QueuedJobFetcher = Callable[[int, Collection[IngestionJobPhase]], Awaitable[Sequence[IngestionJob]]]
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


class IngestionJobStalledError(IngestionJobSubprocessError):
    """job の進捗が停滞の時間（``ingestion_job_stall_timeout_seconds``）進まなかった(#2003)。"""


class IngestionJobTimeoutError(IngestionJobSubprocessError):
    """job が全体の上限（``ingestion_job_subprocess_timeout_seconds``）を超えた(#2003)。"""


# 親が子の job の進捗を読む。job の行(phase と ``progress_json``)を返す。
JobProgressReader = Callable[[str], Awaitable[IngestionJob | None]]
MonotonicClock = Callable[[], float]


def _job_runner_for_settings(settings: Settings, *, lease_owner: str | None = None) -> JobRunner:
    if settings.ingestion_queue_process_isolation_enabled:
        return lambda job_id: run_ingestion_job_subprocess(
            job_id,
            timeout_seconds=settings.ingestion_job_subprocess_timeout_seconds,
            stall_timeout_seconds=settings.ingestion_job_stall_timeout_seconds,
            progress_poll_interval_seconds=settings.ingestion_queue_heartbeat_interval_seconds,
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


async def _default_read_job_progress(job_id: str) -> IngestionJob | None:
    return await OracleClient().get_ingestion_job(job_id)


def _progress_is_running(progress: IngestionJobProgress | None) -> bool:
    """進捗を出す段(解析のページ・図・表の説明)の途中か。

    進捗を出すのは抽出の中の 2 つの段だけ(#1999)。段の前(ファイル準備・ダウンロード)と、
    抽出の後(分割・埋め込み・索引)は進捗を出さないため、停滞を判定しない(それぞれの呼び出しの
    timeout と、全体の上限に従う)。
    """
    if progress is None:
        return False
    if progress.stage == "vision":
        return progress.vision is not None and not progress.vision.finished
    return progress.parse is None or not progress.parse.finished


def describe_ingestion_progress(progress: IngestionJobProgress | None) -> str:
    """失敗の文言に出す、最後に進んだ段と件数。"""
    if progress is None:
        return "進捗の記録なし（解析の前、または進捗を出さない段）"
    if progress.stage == "vision":
        vision = progress.vision
        if vision is None:
            return "図・表の説明（件数は未確定）"
        if vision.finished:
            return "図・表の説明の完了の後（分割・索引など）"
        done = vision.succeeded + vision.failed + vision.skipped
        suffix = "" if vision.total_final else "以上"
        return f"図・表の説明 {done} / {vision.total_known} 件{suffix}"
    parse = progress.parse
    if parse is None:
        return "解析（ページ数は未確定）"
    if parse.finished:
        return "解析の完了の後"
    if len(parse.segments) > 1:
        # PDF の段ごとの解析(#2001)。完了した段のページと、解析中の段の中のページを出す。
        done = sum(s.page_end - s.page_start + 1 for s in parse.segments if s.status == "SUCCEEDED")
        total = sum(s.page_end - s.page_start + 1 for s in parse.segments)
        running = [s for s in parse.segments if s.status == "RUNNING"]
        current = "・".join(
            f"p.{s.page_start}-{s.page_end} の {s.completed} / "
            f"{s.page_end - s.page_start + 1} ページ"
            for s in running
        )
        return f"解析 完了 {done} / {total} ページ" + (f"（解析中: {current}）" if current else "")
    if parse.total is None:
        return f"解析 {parse.completed} ページ（総ページ数は未確定）"
    return f"解析 {parse.completed} / {parse.total} ページ"


def _format_seconds(seconds: float) -> str:
    if seconds >= 3600 and seconds % 3600 == 0:
        return f"{seconds:g} 秒（{seconds / 3600:g} 時間）"
    if seconds >= 60 and seconds % 60 == 0:
        return f"{seconds:g} 秒（{seconds / 60:g} 分）"
    return f"{seconds:g} 秒"


class _ProgressWatch:
    """子の job の進捗(phase と ``progress_json``)が最後に変わった時刻を覚える。"""

    def __init__(self, *, started_at: float) -> None:
        self._fingerprint: object = None
        self.progress: IngestionJobProgress | None = None
        self.changed_at = started_at

    def observe(self, job: IngestionJob | None, now: float) -> None:
        if job is None:
            # 読めなかった(DB の一時的な失敗)。進んだとも止まったとも決めない。
            return
        progress = job.progress
        fingerprint = (
            job.phase,
            progress.model_dump(mode="json") if progress is not None else None,
        )
        if fingerprint != self._fingerprint:
            self._fingerprint = fingerprint
            self.progress = progress
            self.changed_at = now

    def stalled_for(self, now: float) -> float | None:
        """進捗を出す段の途中で進んでいない秒数(段の外なら None)。"""
        if not _progress_is_running(self.progress):
            return None
        return now - self.changed_at


async def _read_job_progress_safely(
    read_job: JobProgressReader, job_id: str
) -> IngestionJob | None:
    try:
        return await read_job(job_id)
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        logger.warning(
            "ingestion_job_progress_read_failed",
            extra={"job_id": job_id, **oracle_error_log_fields(exc)},
            exc_info=True,
        )
        return None


async def run_ingestion_job_subprocess(
    job_id: str,
    *,
    timeout_seconds: float | None = None,
    stall_timeout_seconds: float | None = None,
    progress_poll_interval_seconds: float | None = None,
    lease_owner: str | None = None,
    read_job: JobProgressReader | None = None,
    clock: MonotonicClock | None = None,
) -> None:
    """1 job を別 Python process で実行し、API event loop / CUDA 初期化と隔離する。

    ``lease_owner`` は子の claim が取る lease の持ち主(この worker)。heartbeat は親が打つ。

    止め方は 2 つ(#2003):

    - 停滞: 子が job の行へ書く進捗(#1999 の ``progress_json``。解析のページ・図・表の説明の
      件数と更新時刻)を ``progress_poll_interval_seconds`` ごとに読み、進捗を出す段の途中で
      ``stall_timeout_seconds`` 変わらなければ止める。進んでいる job は止めない。
    - 全体の上限: ``timeout_seconds`` を超えたら止める(保険。1 文書 1,000 図を見込んだ長さ)。

    ``read_job`` と ``clock`` はテストで差し替える(既定は job の行の読み出しと event loop の時計)。
    """
    settings = get_settings()
    timeout = (
        timeout_seconds
        if timeout_seconds is not None
        else settings.ingestion_job_subprocess_timeout_seconds
    )
    stall_timeout = (
        stall_timeout_seconds
        if stall_timeout_seconds is not None
        else settings.ingestion_job_stall_timeout_seconds
    )
    poll_interval = (
        progress_poll_interval_seconds
        if progress_poll_interval_seconds is not None
        else settings.ingestion_queue_heartbeat_interval_seconds
    )
    reader = read_job or _default_read_job_progress
    now = clock or asyncio.get_running_loop().time
    command = [sys.executable, "-m", "app.rag.ingestion_job_runner", job_id]
    if lease_owner is not None:
        command += ["--lease-owner", lease_owner]
    process = await asyncio.create_subprocess_exec(*command)
    started_at = now()
    deadline = started_at + timeout
    watch = _ProgressWatch(started_at=started_at)
    waiter = asyncio.ensure_future(process.wait())
    try:
        while True:
            remaining = deadline - now()
            if remaining <= 0:
                await _terminate_process(process)
                raise IngestionJobTimeoutError(
                    f"取込が全体の上限（{_format_seconds(timeout)}）を超えたため止めました。"
                    f"最後に進んだ段: {describe_ingestion_progress(watch.progress)}。"
                    "さらに時間のかかる文書は RAG_INGESTION_JOB_SUBPROCESS_TIMEOUT_SECONDS を"
                    "長くしてください。"
                )
            done, _ = await asyncio.wait({waiter}, timeout=min(poll_interval, remaining))
            if done:
                return_code = waiter.result()
                break
            watch.observe(await _read_job_progress_safely(reader, job_id), now())
            stalled = watch.stalled_for(now())
            if stalled is not None and stalled >= stall_timeout:
                await _terminate_process(process)
                logger.warning(
                    "ingestion_job_stalled",
                    extra={
                        "job_id": job_id,
                        "stall_timeout_seconds": stall_timeout,
                        "stalled_seconds": round(stalled, 1),
                    },
                )
                raise IngestionJobStalledError(
                    f"取込の進捗が {_format_seconds(stall_timeout)}進まなかったため止めました。"
                    f"最後に進んだ段: {describe_ingestion_progress(watch.progress)}。"
                    "1 件の処理に時間がかかる場合は RAG_INGESTION_JOB_STALL_TIMEOUT_SECONDS を"
                    "長くしてください。"
                )
    except asyncio.CancelledError:
        await _terminate_process(process)
        raise
    finally:
        if not waiter.done():
            waiter.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await waiter
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
        heavy_phase_concurrency: int | None = None,
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
        # 全体の枠と重い段(ファイル準備・抽出)の上限。auto は vCPU から決める(#2027)。
        self._concurrency_plan = settings.ingestion_queue_concurrency()
        self._concurrency = max(1, concurrency or self._concurrency_plan.total)
        self._heavy_phase_concurrency = min(
            self._concurrency,
            max(1, heavy_phase_concurrency or self._concurrency_plan.heavy_phase),
        )
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
        # 実行中の重い段の job(重い段の上限を数える)。
        self._inflight_heavy: set[str] = set()
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
                "heavy_phase_concurrency": self._heavy_phase_concurrency,
                "vcpu": self._concurrency_plan.vcpu,
                "worker_concurrency_auto": self._concurrency_plan.total_auto,
                "heavy_phase_concurrency_auto": self._concurrency_plan.heavy_phase_auto,
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

    async def _default_fetch_queued(
        self, limit: int, exclude_phases: Collection[IngestionJobPhase] = ()
    ) -> Sequence[IngestionJob]:
        # 取る順番(始めた文書の続きの段を先に。#2027)で、claim できない job(同じ文書の job が
        # RUNNING)と自分が実行中の job を除く。除かないと、先頭の claim できない job が毎回
        # free 枠を埋め、後ろの job が流れない(#357)。枠が埋まった段も同じ理由で除く。
        return await OracleClient().list_dispatchable_ingestion_jobs(
            limit=limit,
            exclude_job_ids=sorted(self._inflight),
            exclude_phases=exclude_phases,
        )

    async def _default_recover_stale(self) -> Sequence[IngestionJob]:
        # heartbeat のある job は DB の時計で TTL を判定する(#359)。worker の時計は、heartbeat の
        # 無い lease 導入前の行(開始からの経過時間)にだけ使う。
        return await OracleClient().recover_stale_ingestion_jobs(
            stale_before=self._clock()
            - timedelta(seconds=self._settings.ingestion_queue_stale_running_seconds),
            lease_ttl_seconds=self._settings.ingestion_queue_lease_ttl_seconds,
            limit=self._settings.ingestion_queue_startup_drain_limit,
            exclude_lease_owner=self.worker_id,
        )

    async def _default_heartbeat(self) -> int:
        return await OracleClient().heartbeat_ingestion_jobs(lease_owner=self.worker_id)

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
        """空きスロット分だけ QUEUED ジョブを取り出して実行タスクを起動する。

        重い段(ファイル準備・抽出)は ``heavy_phase_concurrency`` までにし、残りの枠を軽い段
        (Chunk 作成・索引)に空ける(段ごとの枠。#2027)。重い段の上限に達していれば重い段を
        除いて取る。上限の手前なら取った順に重い段を上限まで起動し、超える分は飛ばす(1 件でも
        起動すれば run_forever がすぐ次を呼ぶので、次の回で重い段を除いて軽い段を取る)。
        """
        free = self._concurrency - len(self._inflight)
        if free <= 0:
            return 0
        exclude_phases: Collection[IngestionJobPhase] = (
            INGESTION_HEAVY_PHASES
            if len(self._inflight_heavy) >= self._heavy_phase_concurrency
            else ()
        )
        try:
            jobs = await self._fetch_queued(free, exclude_phases)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.exception("ingestion_worker_fetch_failed", extra=oracle_error_log_fields(exc))
            return 0
        dispatched = 0
        for job in jobs:
            if job.id in self._inflight:
                continue
            if job.phase in INGESTION_HEAVY_PHASES:
                if len(self._inflight_heavy) >= self._heavy_phase_concurrency:
                    continue
                self._inflight_heavy.add(job.id)
            self._inflight.add(job.id)
            task = asyncio.create_task(self._run_job(job.id))
            self._tasks.add(task)
            task.add_done_callback(self._tasks.discard)
            dispatched += 1
            if len(self._inflight) >= self._concurrency:
                break
        return dispatched

    async def _run_job(self, job_id: str) -> None:
        with bind_log_context(job_id=job_id, worker_id=self.worker_id):
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
                await _mark_running_job_failed(job_id, error=exc, lease_owner=self.worker_id)
                logger.exception(
                    "ingestion_worker_job_failed",
                    extra={"job_id": job_id, **oracle_error_log_fields(exc)},
                )
            finally:
                self._inflight.discard(job_id)
                self._inflight_heavy.discard(job_id)
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


async def _mark_running_job_failed(
    job_id: str, *, error: Exception, lease_owner: str | None = None
) -> None:
    """subprocess が落ちた時に RUNNING のまま放置しない。

    - RUNNING の job は FAILED にする(RUNNING のときだけ書く。#305)。レシピの job は
      レシピ行だけを ERROR にし、文書(全レシピの集約)の status は変えない。文書単位の job は
      文書を ERROR にする(子が落ちたので pipeline は書けていない)。
    - 取り消し済みの job は、取り消しを検知した側として文書・レシピの status を戻す。
    - ``lease_owner``(この worker)を渡すと、自分の lease の job のときだけ書く。heartbeat が
      途絶えて別の worker が claim し直した job は、FAILED にも取り消しの後始末にもしない(#359)。
    """
    try:
        # 循環 import を避けるため遅延 import する。
        from app.api.routes.documents import _fail_ingestion_job, _restore_statuses_after_cancel

        oracle = OracleClient()
        job = await oracle.get_ingestion_job(job_id)
        if job is None:
            return
        if job.status == IngestionJobStatus.CANCELLED:
            await _restore_statuses_after_cancel(oracle, job, lease_owner=lease_owner)
            return
        if job.status != IngestionJobStatus.RUNNING:
            return
        message = str(error)[:500] or "取込ジョブ実行プロセスが異常終了しました。"
        failed = await _fail_ingestion_job(oracle, job, message, lease_owner=lease_owner)
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
    configure_logging(settings.log_level, component="ingestion_worker")
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
