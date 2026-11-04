"""正本の保存前に得た全文を有界で保持し、保存取引からwriterへ渡す。"""

from __future__ import annotations

import threading
from collections.abc import Iterable
from dataclasses import dataclass, field

from .trace_bodies import BodyOwner, BodyReference, PreparedBody
from .trace_body_writer import BodySubmission, BodyWriteJob, TraceBodyWriter


class TraceBodyBuffer:
    """複数実行の未保存本文の量をprocess全体で制限する。本文は外部APIから読まない。"""

    def __init__(self, *, max_bodies: int, max_bytes: int) -> None:
        if max_bodies < 1 or max_bytes < 1:
            raise ValueError("trace body buffer capacity must be positive")
        self._max_bodies = max_bodies
        self._max_bytes = max_bytes
        self._lock = threading.Lock()
        self._bodies: dict[str, tuple[PreparedBody, BodyCaptureSession]] = {}
        self._bytes = 0

    def session(self) -> BodyCaptureSession:
        return BodyCaptureSession(self)

    @property
    def pending(self) -> tuple[int, int]:
        with self._lock:
            return len(self._bodies), self._bytes

    def handoff_registered(
        self,
        references: Iterable[BodyReference],
        *,
        owner: BodyOwner,
        writer: TraceBodyWriter,
        retain_until_commit: bool = False,
    ) -> tuple[BodySubmission, ...]:
        """正本保存→所属attachの取引内で呼ぶ。未取得の旧参照から本文を作らない。

        workerは所属を新規作成せず、commit後に実際の所属があることを確認して書く。
        取引のrollback時はworker側で所属なしを確認し、本文の復活を防ぐ必要がある。
        受付失敗は戻り値から同じ保存取引内でmanifestに記録する。
        """
        submissions: list[BodySubmission] = []
        with self._lock:
            for reference in references:
                entry = self._bodies.get(reference.body_id)
                if entry is None:
                    continue
                body, session = entry
                if body.reference != reference:
                    raise ValueError("buffered trace body reference mismatch")
                result = writer.submit(BodyWriteJob(body, (owner,)))
                if not retain_until_commit:
                    self._bodies.pop(reference.body_id)
                    session._ids.discard(reference.body_id)
                    self._bytes -= body.reference.byte_count
                submissions.append(result)
        return tuple(submissions)

    def confirm_registered(self, references: Iterable[BodyReference]) -> None:
        """正本取引のcommit成功後だけ解放する。rollback後の再試行に原文を残す。"""
        with self._lock:
            for reference in references:
                entry = self._bodies.get(reference.body_id)
                if entry is None:
                    continue
                body, session = entry
                if body.reference != reference:
                    raise ValueError("buffered trace body reference mismatch")
                self._bodies.pop(reference.body_id)
                session._ids.discard(reference.body_id)
                self._bytes -= body.reference.byte_count


@dataclass(slots=True)
class BodyCaptureSession:
    buffer: TraceBodyBuffer
    _ids: set[str] = field(default_factory=set)
    _closed: bool = False

    def capture(self, body: PreparedBody) -> BodySubmission:
        with self.buffer._lock:
            if self._closed:
                return BodySubmission(body.reference, False, "writer_stopped")
            before = self.buffer._bodies.get(body.reference.body_id)
            if before is not None:
                if before[1] is not self or before[0] != body:
                    return BodySubmission(body.reference, False, "capture_failed")
                return BodySubmission(body.reference, True)
            if (
                len(self.buffer._bodies) >= self.buffer._max_bodies
                or self.buffer._bytes + body.reference.byte_count > self.buffer._max_bytes
            ):
                return BodySubmission(body.reference, False, "queue_full")
            self.buffer._bodies[body.reference.body_id] = (body, self)
            self.buffer._bytes += body.reference.byte_count
            self._ids.add(body.reference.body_id)
            return BodySubmission(body.reference, True)

    def close(self) -> None:
        """保存しなかった生成の本文を解放する。writerへ移した本文の所属は変更しない。"""
        with self.buffer._lock:
            self._closed = True
            for body_id in self._ids:
                entry = self.buffer._bodies.get(body_id)
                if entry is not None and entry[1] is self:
                    self.buffer._bodies.pop(body_id)
                    self.buffer._bytes -= entry[0].reference.byte_count
            self._ids.clear()
