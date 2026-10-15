"""実行中の取込 job の、抽出の中の段（解析・Vision）の進捗を job の行へ書く（#1999）。

通しジョブ内で実行する抽出は、解析サービス（Docling）が原本全体を 1 回で解析し、その後に
図・表の説明（Vision）を行う。抽出セグメントは解析の後に作るチェックポイントで、段の途中の
進み具合を持たない。そのため、worker は段の進捗をこの記録に集め、一定の間隔で job の行
（``rag_ingestion_jobs.progress_json``）へ書き、画面は job の応答の ``progress`` を出す。

- 記録は job の実行（``_run_ingestion_job``）の間だけ ``track_ingestion_progress`` が
  context に置く。``asyncio.to_thread`` の thread も context を引き継ぐため、解析・Vision の段は
  引数を足さずに ``active_ingestion_progress()`` で取れる。job の外（同期の取込・評価）では
  None で、何も書かない。
- 更新は thread から呼べる（lock で守る）。書き込みは event loop の task が間隔ごとにまとめて行い、
  1 件ごとに DB へ書かない。書き込みの失敗は警告に留め、取込を止めない。
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import threading
from collections.abc import AsyncIterator, Awaitable, Callable, Mapping
from contextvars import ContextVar
from datetime import UTC, datetime

from app.schemas.document import (
    IngestionJobProgress,
    IngestionParseProgress,
    IngestionVisionProgress,
)

logger = logging.getLogger(__name__)

# 進捗を job の行へ書く間隔（秒）。画面の polling（数秒）と同じ程度にする。
DEFAULT_FLUSH_INTERVAL_SECONDS = 2.0

ProgressWriter = Callable[[IngestionJobProgress | None], Awaitable[object]]

_ACTIVE: ContextVar[IngestionProgressReporter | None] = ContextVar(
    "rag_ingestion_progress", default=None
)


def active_ingestion_progress() -> IngestionProgressReporter | None:
    """実行中の job の進捗の記録（job の外では None）。"""
    return _ACTIVE.get()


class IngestionProgressReporter:
    """段の進捗を集め、変わったときだけ ``writer`` で書く。"""

    def __init__(self, writer: ProgressWriter) -> None:
        self._writer = writer
        self._lock = threading.Lock()
        self._parse: IngestionParseProgress | None = None
        self._vision: IngestionVisionProgress | None = None
        self._stage: str | None = None
        self._updated_at: datetime | None = None
        self._dirty = False

    def update_parse(self, parse: IngestionParseProgress) -> None:
        """解析の段の進捗を置き換える（Vision の段に進んだ後は段を戻さない）。"""
        with self._lock:
            if parse == self._parse:
                return
            self._parse = parse
            if self._stage != "vision":
                self._stage = "parse"
            self._touch()

    def finish_parse(self, *, engine: str | None, page_count: int | None) -> None:
        """解析サービスの応答が届いた（解析の段の完了）。ページ数は応答の値で補う。"""
        with self._lock:
            current = self._parse or IngestionParseProgress()
            total = current.total if current.total is not None else page_count
            completed = current.completed
            if total is not None and current.failed == 0:
                completed = total
            self._parse = current.model_copy(
                update={
                    "engine": current.engine or engine,
                    "completed": completed,
                    "total": total,
                    "finished": True,
                }
            )
            if self._stage != "vision":
                self._stage = "parse"
            self._touch()

    def update_vision(self, vision: IngestionVisionProgress) -> None:
        """図・表の説明の段の進捗を置き換える。"""
        with self._lock:
            if vision == self._vision and self._stage == "vision":
                return
            self._vision = vision
            self._stage = "vision"
            self._touch()

    def finish_vision(self) -> None:
        """図・表の説明の段の完了（対象が無かったときも完了にする）。"""
        with self._lock:
            current = self._vision or IngestionVisionProgress(total_final=True)
            self._vision = current.model_copy(
                update={"finished": True, "running": 0, "current_page": None, "current_kind": None}
            )
            self._stage = "vision"
            self._touch()

    def snapshot(self) -> IngestionJobProgress | None:
        """今の進捗（まだ段が始まっていなければ None）。"""
        with self._lock:
            return self._snapshot_locked()

    async def flush(self) -> None:
        """変わった進捗を書く。失敗は警告に留める（次の間隔で書き直す）。"""
        with self._lock:
            if not self._dirty:
                return
            progress = self._snapshot_locked()
            self._dirty = False
        try:
            await self._writer(progress)
        except Exception:  # noqa: BLE001 - 進捗は補助。取込を止めない。
            with self._lock:
                self._dirty = True
            logger.warning("ingestion_progress_write_failed", exc_info=True)

    def _snapshot_locked(self) -> IngestionJobProgress | None:
        if self._stage is None or self._updated_at is None:
            return None
        return IngestionJobProgress.model_validate(
            {
                "stage": self._stage,
                "parse": self._parse,
                "vision": self._vision,
                "updated_at": self._updated_at,
            }
        )

    def _touch(self) -> None:
        self._updated_at = datetime.now(UTC)
        self._dirty = True


@contextlib.asynccontextmanager
async def track_ingestion_progress(
    writer: ProgressWriter,
    *,
    interval_seconds: float = DEFAULT_FLUSH_INTERVAL_SECONDS,
) -> AsyncIterator[IngestionProgressReporter]:
    """job の実行の間、進捗の記録を context に置き、間隔ごとに書く。

    始めに前の試行の進捗を消す（同じ job の再試行で古い段を出さない）。終わったら書く task を
    止める（job の完了・失敗の後は進捗を出さないため、最後の書き込みはしない）。
    """
    reporter = IngestionProgressReporter(writer)
    try:
        await writer(None)
    except Exception:  # noqa: BLE001 - 進捗は補助。取込を止めない。
        logger.warning("ingestion_progress_reset_failed", exc_info=True)

    async def flush_periodically() -> None:
        while True:
            await asyncio.sleep(interval_seconds)
            await reporter.flush()

    token = _ACTIVE.set(reporter)
    task = asyncio.create_task(flush_periodically())
    try:
        yield reporter
    finally:
        _ACTIVE.reset(token)
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task


def parse_progress_from_service(payload: Mapping[str, object]) -> IngestionParseProgress | None:
    """解析サービスの進捗（Docling の ``progress.json``）を解析の段の進捗にする。"""
    total = _non_negative_int(payload.get("total_pages"))
    completed = _non_negative_int(payload.get("completed_pages"))
    failed = _non_negative_int(payload.get("failed_pages"))
    if total is None and completed is None:
        return None
    engine = payload.get("engine")
    stage = payload.get("stage")
    return IngestionParseProgress(
        engine=str(engine) if isinstance(engine, str) and engine else None,
        completed=completed or 0,
        failed=failed or 0,
        total=total,
        finished=stage in {"docling_completed", "docling_failed"},
    )


def vision_progress_from_ledger(payload: Mapping[str, object]) -> IngestionVisionProgress:
    """Vision の段の記録（``VisionCheckpoints`` の ``progress.json``）を件数の進捗にする。"""
    raw_counts = payload.get("counts")
    counts: Mapping[str, object] = raw_counts if isinstance(raw_counts, Mapping) else {}
    raw_current = payload.get("current")
    current: Mapping[str, object] = raw_current if isinstance(raw_current, Mapping) else {}
    page = _non_negative_int(current.get("page"))
    kind = str(current.get("kind") or "").casefold()
    phase = payload.get("phase")
    return IngestionVisionProgress(
        phase=str(phase) if isinstance(phase, str) and phase else None,
        succeeded=_non_negative_int(counts.get("succeeded")) or 0,
        failed=_non_negative_int(counts.get("failed")) or 0,
        skipped=_non_negative_int(counts.get("skipped")) or 0,
        running=_non_negative_int(counts.get("running")) or 0,
        pending=_non_negative_int(counts.get("pending")) or 0,
        total_known=_non_negative_int(payload.get("total_known")) or 0,
        total_final=bool(payload.get("total_final")),
        current_page=page if page else None,
        current_kind="table" if kind == "table" else "picture" if kind == "picture" else None,
        finished=str(phase or "").startswith("completed"),
    )


def _non_negative_int(value: object) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    return value if value >= 0 else None
