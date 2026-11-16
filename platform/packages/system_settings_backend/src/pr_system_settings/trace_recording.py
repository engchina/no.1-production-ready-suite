"""3 製品共通の軌跡の本文記録。未設定・読み出し失敗は無効。"""

from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from typing import Any, Protocol

from fastapi import APIRouter, params
from pr_backend_core.schemas import ApiResponse
from pydantic import BaseModel, ConfigDict

from .auth.errors import SecurityApiError

logger = logging.getLogger(__name__)
TRACE_RECORDING_TABLE = "PLATFORM_TRACE_RECORDING_SETTINGS"
CACHE_SECONDS = 5.0


class TraceRecordingSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")
    enabled: bool = False
    updated_at: datetime | None = None


class TraceRecordingUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    enabled: bool


class TraceRecordingStore(Protocol):
    def load(self) -> TraceRecordingSettings | None: ...
    def save(self, enabled: bool) -> TraceRecordingSettings: ...


class InMemoryTraceRecordingStore:
    def __init__(self) -> None:
        self.record = TraceRecordingSettings()

    def load(self) -> TraceRecordingSettings:
        return self.record

    def save(self, enabled: bool) -> TraceRecordingSettings:
        self.record = TraceRecordingSettings(enabled=enabled, updated_at=datetime.now(UTC))
        return self.record


class OracleTraceRecordingStore:
    def __init__(self, connection_factory: Any) -> None:
        self._connection_factory = connection_factory

    @contextmanager
    def _connection(self) -> Iterator[Any]:
        try:
            with self._connection_factory() as connection:
                yield connection
        except SecurityApiError:
            raise
        except Exception as exc:
            if "ORA-00942" in str(exc).upper():
                raise SecurityApiError(
                    409,
                    "共通のシステムテーブルを初期化してください。",
                    code="SECURITY_SCHEMA_MIGRATION_REQUIRED",
                ) from exc
            raise SecurityApiError(
                503,
                "軌跡の記録設定を読み書きできません。データベースの状態を確認してください。",
                code="TRACE_RECORDING_SETTINGS_UNAVAILABLE",
                retryable=True,
            ) from exc

    def load(self) -> TraceRecordingSettings | None:
        with self._connection() as conn, conn.cursor() as cursor:
            cursor.execute(
                "SELECT ENABLED, UPDATED_AT FROM PLATFORM_TRACE_RECORDING_SETTINGS WHERE ID = 1"
            )
            row = cursor.fetchone()
        if row is None:
            return None
        updated = row[1]
        if isinstance(updated, datetime) and updated.tzinfo is None:
            updated = updated.replace(tzinfo=UTC)
        return TraceRecordingSettings(enabled=bool(row[0]), updated_at=updated)

    def save(self, enabled: bool) -> TraceRecordingSettings:
        now = datetime.now(UTC)
        with self._connection() as conn, conn.cursor() as cursor:
            cursor.execute(
                """MERGE INTO PLATFORM_TRACE_RECORDING_SETTINGS target
                USING (SELECT 1 AS ID FROM DUAL) source ON (target.ID = source.ID)
                WHEN MATCHED THEN UPDATE SET ENABLED = :enabled, UPDATED_AT = :updated_at
                WHEN NOT MATCHED THEN INSERT (ID, ENABLED, UPDATED_AT)
                  VALUES (1, :enabled, :updated_at)""",
                {"enabled": int(enabled), "updated_at": now},
            )
            conn.commit()
        return TraceRecordingSettings(enabled=enabled, updated_at=now)


def _start_daemon_thread(refresh: Callable[[], None]) -> None:
    threading.Thread(target=refresh, name="trace-recording-refresh", daemon=True).start()


class TraceRecordingProvider:
    """「軌跡の記録」の値を短い cache で返す。

    `enabled()` はすべての要求の span の開始（event loop の上）で呼ばれるため、DB を待たない
    （#5140・#5181）。cache の値をすぐ返し、期限が切れたら背景の thread で 1 本だけ読み直す。
    読み直しの間は最後に読めた値、まだ一度も読めていない・読めなかったときは無効（False）。
    """

    def __init__(
        self,
        store: TraceRecordingStore,
        *,
        clock: Callable[[], float] = time.monotonic,
        run_in_background: Callable[[Callable[[], None]], None] = _start_daemon_thread,
    ) -> None:
        self.store = store
        self._clock = clock
        self._run_in_background = run_in_background
        self._lock = threading.Lock()
        self._cached: tuple[float, bool] | None = None
        self._refreshing = False
        # save() のたびに進める。保存より前に始めた読み直しの結果で、保存した値を戻さない。
        self._generation = 0

    def enabled(self) -> bool:
        with self._lock:
            cached = self._cached
            if cached is not None and self._clock() - cached[0] < CACHE_SECONDS:
                return cached[1]
            start = not self._refreshing
            self._refreshing = True
            generation = self._generation
        if start:
            try:
                self._run_in_background(lambda: self._refresh(generation))
            except Exception as exc:  # thread を作れない。次の呼び出しでやり直す
                logger.warning(
                    "trace_recording_settings_unavailable", extra={"error_type": type(exc).__name__}
                )
                with self._lock:
                    self._refreshing = False
        with self._lock:
            return self._cached[1] if self._cached is not None else False

    def _refresh(self, generation: int) -> None:
        try:
            value = self.load().enabled
        except Exception as exc:
            logger.warning(
                "trace_recording_settings_unavailable", extra={"error_type": type(exc).__name__}
            )
            value = False
        with self._lock:
            self._refreshing = False
            if generation == self._generation:
                self._cached = (self._clock(), value)

    def load(self) -> TraceRecordingSettings:
        return self.store.load() or TraceRecordingSettings()

    def save(self, enabled: bool) -> TraceRecordingSettings:
        saved = self.store.save(enabled)
        with self._lock:
            self._generation += 1
            self._cached = (self._clock(), saved.enabled)
        return saved


def build_trace_recording_router(
    *,
    provider: Callable[[], TraceRecordingProvider],
    write_dependencies: list[params.Depends] | None = None,
) -> APIRouter:
    router = APIRouter()

    @router.get("/trace-recording", response_model=ApiResponse[TraceRecordingSettings])
    def get_settings() -> ApiResponse[TraceRecordingSettings]:
        return ApiResponse(data=provider().load())

    @router.put(
        "/trace-recording",
        response_model=ApiResponse[TraceRecordingSettings],
        dependencies=write_dependencies or [],
    )
    def update_settings(payload: TraceRecordingUpdate) -> ApiResponse[TraceRecordingSettings]:
        return ApiResponse(data=provider().save(payload.enabled))

    return router
