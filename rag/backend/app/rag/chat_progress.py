"""回答の処理の経過（3 製品共通の契約 `pr_backend_core.chat_progress`。#1359 / #3226）。

処理の経過は trace（span の木）の投影にする（#3226）。回答の pipeline の
`operation_trace` の listener が知らせる span の開始・表示用のデータの変更・終了を、
そのまま処理の経過のイベント（`ChatProgressRecorder.record_span`。`step_id` は
span の id、親子は span の親子）として記録する。工程の名前は span の表示用のデータ
（種類と工程名。`app/rag/span_display.py`）から画面が付ける。

対象（`target_id`）は、チャットでは作成中の ASSISTANT のメッセージの id、RAG 検索では
実行の trace_id。記録したイベントの一覧は、チャットはそのメッセージの `progress_json`、
実行履歴は `stages_json.progress_events` に保存し、回答・検索の配信（SSE の
`chat_progress`）と、段階の polling / SSE の endpoint
（`GET .../messages/{回答の id}/progress[/stream]`）で配る。

段階の名前（利用者向けの文言）は記録に入れない。画面が種類と工程名から i18n で付ける
（`frontend/src/lib/chat-progress.ts`）。
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import threading
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

from pr_backend_core.chat_progress import (
    ChatProgressRecorder,
    ChatProgressSink,
    ChatProgressStepEvent,
    ChatProgressTerminalEvent,
    ChatProgressTerminalStatus,
    dump_chat_progress_events,
)
from pr_backend_core.observability import request_id_var
from pr_backend_core.observability.tracing import SpanListener, SpanUpdate

logger = logging.getLogger(__name__)

type ChatProgressEventItem = ChatProgressStepEvent | ChatProgressTerminalEvent


class ChatProgressTracker:
    """1 回の回答・検索の処理の経過を、span の知らせから記録する（#3226）。

    span の開始は実行中、終了は完了・失敗・スキップ（停止は `cancelled` の印）の段に
    なる。入れ子は span の親子で、実行の全体の span（root）も記録する（画面は行にしない）。

    記録したイベントは `sink`（`attach` で後から付けられる）に 1 件ずつ渡し、
    `on_change` を呼ぶ（保存の予約）。記録はイベントループの中で行う。回答の thread から
    届く span の知らせは `span_listener` がループへ渡す（届いた順を保つ）。
    """

    def __init__(
        self,
        target_id: str,
        *,
        model_id: str = "",
        events: list[ChatProgressEventItem] | None = None,
        sink: ChatProgressSink | None = None,
        on_change: Callable[[], None] | None = None,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self._model_id = model_id
        self._trace_id: str | None = None
        self._sink = sink
        self._on_change = on_change
        self._recorder = ChatProgressRecorder(
            target_id, events=events or (), sink=self._on_event, clock=clock
        )

    @property
    def message_id(self) -> str:
        """対象の id（チャットは作成中のメッセージの id）。"""
        return self._recorder.target_id

    @property
    def model_id(self) -> str:
        return self._model_id

    @property
    def trace_id(self) -> str | None:
        """回答の trace_id（失敗の「詳細」に出す。#2023）。"""
        return self._trace_id

    @trace_id.setter
    def trace_id(self, value: str | None) -> None:
        self._trace_id = value

    @property
    def events(self) -> list[ChatProgressEventItem]:
        """記録したイベントの全体（`seq` の順）。"""
        return self._recorder.events

    @property
    def last_seq(self) -> int:
        return self._recorder.last_seq

    @property
    def terminal(self) -> ChatProgressTerminalStatus | None:
        return self._recorder.terminal

    def dump(self) -> list[dict[str, Any]]:
        """保存の JSON（`progress_json` / `stages_json.progress_events`）。"""
        return dump_chat_progress_events(self._recorder.events)

    def attach(
        self, sink: ChatProgressSink | None = None, *, on_change: Callable[[], None] | None = None
    ) -> None:
        """記録の送り先・変更の知らせを付ける（これより後に記録したイベントを渡す）。"""
        if sink is not None:
            self._sink = sink
        if on_change is not None:
            self._on_change = on_change

    def record_span(self, update: SpanUpdate) -> bool:
        """span の知らせを段として記録する。イベントを記録したら True（終端の後は何もしない）。"""
        if self._recorder.terminal is not None:
            return False
        return self._recorder.record_span(update) is not None

    def span_listener(self) -> SpanListener:
        """`operation_trace(listener=...)` に渡す listener（回答の thread からも呼ばれる）。

        ループの thread から呼ばれたらその場で、ほかの thread からなら
        `call_soon_threadsafe` でループの中で記録する（ループへ渡した順に記録するので、
        span の開始・終了の順は保たれる）。イベントループの中で作る。
        """
        target_loop = asyncio.get_running_loop()
        loop_thread = threading.get_ident()

        def listen(update: SpanUpdate) -> None:
            if threading.get_ident() == loop_thread:
                self.record_span(update)
                return
            # 回答の thread の span。ループが閉じた後（取り消し）の知らせは捨てる。
            with contextlib.suppress(RuntimeError):
                target_loop.call_soon_threadsafe(self.record_span, update)

        return listen

    def finish(self) -> None:
        """回答・検索ができた。終端（完了）を記録する（実行中の段は完了になる）。"""
        self._recorder.complete("done")

    def fail(self) -> None:
        """回答の生成が失敗した（時間切れを含む）。実行中の段を失敗にして終端（失敗）にする。"""
        self._recorder.complete("failed")

    def close(self, status: ChatProgressTerminalStatus) -> None:
        """作成を止めた（`cancelled`）・中断した（`failed`）。終端を記録する（終端の後は何もしない）。"""
        self._recorder.complete(status)

    def _on_event(self, event: ChatProgressEventItem) -> None:
        if isinstance(event, ChatProgressStepEvent):
            self._log(event)
        if self._sink is not None:
            self._sink(event)
        if self._on_change is not None:
            self._on_change()

    def _log(self, event: ChatProgressStepEvent) -> None:
        # 工程の span（表示用のデータの種類がある）の終了だけをログに出す（技術の span・root
        # は出さない）。
        if event.status in ("running", "pending") or event.kind == event.step_id or event.root:
            return
        duration_ms = (
            round((event.finished_at - event.started_at).total_seconds() * 1000, 3)
            if event.started_at is not None and event.finished_at is not None
            else None
        )
        logger.log(
            logging.WARNING if event.status == "failed" else logging.INFO,
            "チャットの段階が終わりました",
            extra={
                "event": "chat_progress_step",
                "request_id": request_id_var.get(),
                "trace_id": self._trace_id,
                "model_id": self._model_id,
                "message_id": self._recorder.target_id,
                "step_id": event.step_id,
                "step_kind": event.kind,
                "step_name": (event.params or {}).get("name"),
                "status": event.status,
                "duration_ms": duration_ms,
            },
        )
