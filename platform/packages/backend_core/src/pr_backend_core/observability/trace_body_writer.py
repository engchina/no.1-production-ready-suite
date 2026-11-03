"""回答の実行をDB書込で待たせない、全文本文専用の有界writer。"""

from __future__ import annotations

import logging
import threading
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass

from .trace_bodies import BodyFailure, BodyOwner, BodyReference, PreparedBody

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class BodyWriteJob:
    body: PreparedBody
    owners: tuple[BodyOwner, ...]

    def __post_init__(self) -> None:
        if not self.owners:
            raise ValueError("trace body requires a persisted record owner")


@dataclass(frozen=True, slots=True)
class BodySubmission:
    reference: BodyReference
    accepted: bool
    failure_code: BodyFailure | None = None


class TraceBodyWriter:
    """件数とUTF-8本文量で待機中・書込中の両方を数える。

    persistは製品の正本の存活を取引内で確認し、所属と全文を保存する責務を持つ。
    保存例外は回答へ送出せず、failureへ技術の理由だけを通知する。投入に失敗した
    ときの理由は戻り値に残し、主処理から同期DB書込を呼ばない。
    """

    def __init__(
        self,
        persist: Callable[[BodyWriteJob], None],
        failure: Callable[[BodyWriteJob, BodyFailure], None],
        *,
        max_jobs: int,
        max_bytes: int,
    ) -> None:
        if max_jobs < 1 or max_bytes < 1:
            raise ValueError("trace body writer capacity must be positive")
        self._persist = persist
        self._failure = failure
        self._max_jobs = max_jobs
        self._max_bytes = max_bytes
        self._condition = threading.Condition()
        self._queue: deque[BodyWriteJob] = deque()
        self._jobs = 0
        self._bytes = 0
        self._accepting = False
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        with self._condition:
            if self._thread is not None and self._thread.is_alive():
                return
            self._accepting = True
            self._thread = threading.Thread(target=self._run, name="trace-body-writer", daemon=True)
            self._thread.start()

    def submit(self, job: BodyWriteJob) -> BodySubmission:
        with self._condition:
            if not self._accepting:
                return BodySubmission(job.body.reference, False, "writer_stopped")
            size = job.body.reference.byte_count
            if self._jobs >= self._max_jobs or self._bytes + size > self._max_bytes:
                return BodySubmission(job.body.reference, False, "queue_full")
            self._jobs += 1
            self._bytes += size
            self._queue.append(job)
            self._condition.notify_all()
            return BodySubmission(job.body.reference, True)

    @property
    def pending(self) -> tuple[int, int]:
        with self._condition:
            return self._jobs, self._bytes

    def stop(self, *, timeout: float = 5.0) -> bool:
        """新規投入を止め、受付済み本文はworkerで排出する。時間内終了かを返す。"""
        if timeout < 0:
            raise ValueError("trace body writer stop timeout must not be negative")
        with self._condition:
            self._accepting = False
            self._condition.notify_all()
            thread = self._thread
        if thread is not None:
            thread.join(timeout)
        return thread is None or not thread.is_alive()

    def _run(self) -> None:
        while True:
            with self._condition:
                self._condition.wait_for(lambda: bool(self._queue) or not self._accepting)
                if not self._queue:
                    return
                job = self._queue.popleft()
            try:
                self._persist(job)
            except Exception:
                # 本文・secretを含み得る例外文をlogへ展開しない。
                logger.warning("trace_body_write_failed")
                try:
                    self._failure(job, "write_failed")
                except Exception:
                    logger.warning("trace_body_failure_record_failed")
            finally:
                with self._condition:
                    self._jobs -= 1
                    self._bytes -= job.body.reference.byte_count
                    self._condition.notify_all()
