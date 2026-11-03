"""3製品の正本保存取引と全文writerをつなぐ共通の仕組み。"""

from __future__ import annotations

import contextlib
import logging
from collections.abc import Callable, Iterator, Mapping
from contextlib import AbstractContextManager, contextmanager
from contextvars import ContextVar
from typing import Any

from ..config.tracing import TracingSettings
from .call_body_capture import call_body_capture
from .trace_bodies import BodyFailure, BodyOwner, mark_body_incomplete, replace_body_owner
from .trace_body_buffer import BodyCaptureSession, TraceBodyBuffer
from .trace_body_registry import (
    call_body_failures,
    call_body_references,
    fail_registered_body,
    write_registered_body,
)
from .trace_body_writer import BodyWriteJob, TraceBodyWriter

logger = logging.getLogger(__name__)


class TraceBodyPersistence:
    def __init__(
        self,
        connection_factory: Callable[[], AbstractContextManager[Any]],
        *,
        clob_type: Any,
        settings: TracingSettings,
    ) -> None:
        self._connection = connection_factory
        self._clob_type = clob_type
        self._part_characters = settings.trace_body_part_characters
        self._shutdown_seconds = settings.trace_shutdown_timeout_seconds
        self._session: ContextVar[BodyCaptureSession | None] = ContextVar(
            "trace_body_persistence_session", default=None
        )
        self.buffer = TraceBodyBuffer(
            max_bodies=settings.trace_body_buffer_size, max_bytes=settings.trace_body_buffer_bytes
        )
        self.writer = TraceBodyWriter(
            self._persist,
            self._fail,
            max_jobs=settings.trace_body_queue_size,
            max_bytes=settings.trace_body_queue_bytes,
        )

    def start(self) -> None:
        self.writer.start()

    def stop(self) -> bool:
        return self.writer.stop(timeout=self._shutdown_seconds)

    @contextmanager
    def capture(self) -> Iterator[None]:
        # 内部pipeline終了の後に外側が失敗記録を保存する場合も、本文を先に解放しない。
        if self._session.get() is not None:
            yield
            return
        session = self.buffer.session()
        token = self._session.set(session)
        try:
            with call_body_capture(session.capture, part_characters=self._part_characters):
                yield
        finally:
            self._session.reset(token)
            session.close()

    def register(
        self, cursor: Any, owner: BodyOwner, span_details: Mapping[str, Any]
    ) -> dict[str, BodyFailure]:
        """正本の保存SQLの後、commit前に呼ぶ。trace障害だけで正本の保存を取り消さない。"""
        try:
            references = call_body_references(span_details)
            captured_faults = call_body_failures(span_details)
        except Exception:
            logger.warning("trace_body_reference_capture_failed")
            return {}
        faults: dict[str, BodyFailure] = {}
        try:
            cursor.execute("SAVEPOINT trace_body_registration")
            replace_body_owner(cursor, owner, references)
            for reference in references:
                if reference.body_id in captured_faults:
                    reason = captured_faults[reference.body_id]
                    mark_body_incomplete(cursor, reference, reason)
                    faults[reference.body_id] = reason
            for submission in self.buffer.handoff_registered(
                references, owner=owner, writer=self.writer, retain_until_commit=True
            ):
                if not submission.accepted:
                    reason = submission.failure_code or "write_failed"
                    mark_body_incomplete(cursor, submission.reference, reason)
                    faults[submission.reference.body_id] = reason
        except Exception:
            with contextlib.suppress(Exception):
                cursor.execute("ROLLBACK TO SAVEPOINT trace_body_registration")
            logger.warning("trace_body_registration_failed")
            return {reference.body_id: "storage_unavailable" for reference in references}
        return faults

    def confirm_saved(self, span_details: Mapping[str, Any]) -> None:
        """正本保存取引が正常にcommitした後に呼ぶ。確認障害でも回答を失敗にしない。"""
        try:
            self.buffer.confirm_registered(call_body_references(span_details))
        except Exception:
            logger.warning("trace_body_commit_confirmation_failed")

    def _persist(self, job: BodyWriteJob) -> None:
        with self._connection() as connection:
            try:
                with connection.cursor() as cursor:
                    write_registered_body(cursor, job.body, clob_type=self._clob_type)
                connection.commit()
            except Exception:
                connection.rollback()
                raise

    def _fail(self, job: BodyWriteJob, reason: BodyFailure) -> None:
        with self._connection() as connection:
            try:
                with connection.cursor() as cursor:
                    fail_registered_body(cursor, job.body.reference, reason)
                connection.commit()
            except Exception:
                connection.rollback()
                raise
