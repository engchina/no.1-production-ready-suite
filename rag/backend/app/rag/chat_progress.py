"""チャットの処理の段階（3 製品共通の契約 `pr_backend_core.chat_progress`。#1146 / #1359 / #2096）。

回答の生成の内部の工程（``SearchStageProgress``）を、そのまま 1 工程 = 1 段階として記録する（#2096。
「処理の経過」に回答の記録（`execution_steps`）と同じ細かい工程を出す）。

- 回答フローの工程（``answer_step:<工程名>``。`rag_engine.generation.answering` の
  `_execution_step`）は種類 ``answer_step`` の段階にし、値（`params`）に工程名（``name``）と
  入れ子の深さ（``depth``）を入れる。
- 回答フローの外の工程（会話を踏まえた質問の書き換え `history_rewrite`・質問から条件を読み取る
  `field_filter`・回答の安全チェック `answer_guardrail`）は、工程の名前を種類にした段階にする。
- 全体を包む工程（`answer` / `retrieval`）は、中の工程を段階にするので出さない。
- 回答ができたら（`finish`）、回答の記録の工程の LLM の呼び出し回数（``llm_calls``）と結果の説明
  （``note``。「該当なし」など）を、同じ工程の段階の値に足してから終端にする。

対象（`target_id`）は作成中の ASSISTANT のメッセージの id。記録したイベントの一覧は、
そのメッセージの `progress_json` に保存し、回答の配信（SSE の `chat_progress`）と、段階の
polling / SSE の endpoint（`GET .../messages/{回答の id}/progress[/stream]`）で配る。

段階の名前（利用者向けの文言）は記録に入れない。画面が種類と工程名から i18n で付ける
（`frontend/src/lib/chat-progress.ts`）。
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Mapping
from datetime import UTC, datetime
from time import perf_counter
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

from app.rag.answer_engine import ANSWER_STEP_STAGE_PREFIX
from app.rag.pipeline import SearchStageProgress

logger = logging.getLogger(__name__)

# 回答フローの工程の段階の種類（値の ``name`` が工程名、``depth`` が入れ子の深さ）。
ANSWER_STEP_KIND = "answer_step"

# 回答フローの外の工程のうち、段階にするもの（工程の名前を種類にする）。
PIPELINE_STEP_KINDS: frozenset[str] = frozenset(
    {"history_rewrite", "field_filter", "answer_guardrail"}
)

# 全体を包む工程（中の工程を段階にするので、包む工程は段階にしない）。
_WRAPPER_STAGES: frozenset[str] = frozenset({"answer", "retrieval"})

# 回答の記録の工程の状態のうち、結果の説明ではないもの（完了・例外で中断）。
_PLAIN_STEP_STATUSES: frozenset[str] = frozenset({"", "complete", "エラーで中断"})


def chat_progress_step_kind(stage: str) -> str | None:
    """内部の工程の名前を段階の種類にする。段階にしない工程（全体を包む工程など）は None。"""
    if stage.startswith(ANSWER_STEP_STAGE_PREFIX):
        return ANSWER_STEP_KIND if stage.removeprefix(ANSWER_STEP_STAGE_PREFIX).strip() else None
    if stage in PIPELINE_STEP_KINDS:
        return stage
    return None


type ChatProgressEventItem = ChatProgressStepEvent | ChatProgressTerminalEvent


class ChatProgressTracker:
    """1 モデル分の回答（作成中の ASSISTANT のメッセージ）の段階を、内部の工程の進捗から記録する。

    工程が始まったら段階を実行中にし、終わったら完了（失敗した工程は失敗）にする。工程は入れ子になる
    （回答フローの中の文書の検索など）ので、実行中の段階は同時に複数あってよい（画面は最後に始まった
    段階を今の段階にする）。段階の id は記録の順の連番（``s1`` / ``s2`` …）で、同じ工程名の 2 回目
    （補正検索など）も別の段階にする。工程の中断（`cancelled`。時間切れ）では段階を終えず、続く
    `fail()` / `close()` の終端で失敗・停止にする。

    記録したイベントは `sink`（`attach` で後から付けられる）に 1 件ずつ渡す。保存は呼び出し元が
    `dump()` で行う。スレッドから同時に呼ばない（イベントループの中で呼ぶ）。
    """

    def __init__(
        self,
        message_id: str,
        *,
        model_id: str = "",
        events: list[ChatProgressEventItem] | None = None,
        sink: ChatProgressSink | None = None,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
        timer: Callable[[], float] = perf_counter,
    ) -> None:
        self._model_id = model_id
        self._timer = timer
        self._trace_id: str | None = None
        self._sink = sink
        # 段階が実行中になった時刻（ログの所要時間）。
        self._running_since: dict[str, float] = {}
        # 終わっていない工程（工程の名前・段階の id。入れ子の順）。
        self._open: list[tuple[str, str]] = []
        self._recorder = ChatProgressRecorder(
            message_id, events=events or (), sink=self._on_event, clock=clock
        )
        # 保存済みのイベントから作り直したときは、続きの番号で段階の id を付ける。
        self._next_index = len(self._recorder.steps()) + 1

    @property
    def message_id(self) -> str:
        return self._recorder.target_id

    @property
    def model_id(self) -> str:
        return self._model_id

    @property
    def trace_id(self) -> str | None:
        """段階の進捗から分かった回答の trace_id（失敗の「詳細」に出す。#2023）。"""
        return self._trace_id

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
        """保存の JSON（`progress_json`）。"""
        return dump_chat_progress_events(self._recorder.events)

    def attach(self, sink: ChatProgressSink) -> None:
        """記録の送り先を付ける（これより後に記録したイベントを渡す）。"""
        self._sink = sink

    def observe(self, progress: SearchStageProgress) -> bool:
        """内部の工程の開始・終了を段階へ反映する。イベントを記録したら True。"""
        self._trace_id = progress.trace_id or self._trace_id
        kind = chat_progress_step_kind(progress.stage)
        if kind is None or self._recorder.terminal is not None:
            return False
        before = self._recorder.last_seq
        if progress.outcome == "started":
            step_id = f"s{self._next_index}"
            self._next_index += 1
            params: dict[str, str | int | float | bool] = {}
            if kind == ANSWER_STEP_KIND:
                params["name"] = progress.stage.removeprefix(ANSWER_STEP_STAGE_PREFIX).strip()
                params["depth"] = sum(
                    1 for stage, _ in self._open if stage.startswith(ANSWER_STEP_STAGE_PREFIX)
                )
            self._recorder.start(step_id, kind=kind, params=params or None)
            self._open.append((progress.stage, step_id))
            return self._recorder.last_seq != before
        index = next(
            (i for i in range(len(self._open) - 1, -1, -1) if self._open[i][0] == progress.stage),
            None,
        )
        if index is None:
            return False
        _, step_id = self._open.pop(index)
        if progress.outcome == "success":
            self._recorder.finish(step_id, "done")
        elif progress.outcome == "error":
            self._recorder.finish(step_id, "failed")
        # cancelled（時間切れなどの中断）は終端（fail / close）で失敗・停止にする。
        return self._recorder.last_seq != before

    def finish(self, *, execution_steps: object = None) -> None:
        """回答ができた。回答の記録の工程の値を段階に足してから、終端（完了）にする。

        `execution_steps` は回答の記録（`diagnostics.answer.execution_steps`）。工程名の順に段階と
        対応させ、LLM の呼び出し回数（``llm_calls``）と結果の説明（``note``）を値に足す。実行中の
        段階は完了になる（`complete("done")`）。
        """
        records: list[Mapping[str, Any]] = (
            [step for step in execution_steps if isinstance(step, Mapping)]
            if isinstance(execution_steps, list | tuple)
            else []
        )
        cursor = 0
        for step in self._recorder.steps():
            if step.kind != ANSWER_STEP_KIND or not step.params:
                continue
            name = step.params.get("name")
            match = next(
                (i for i in range(cursor, len(records)) if records[i].get("name") == name), None
            )
            if match is None:
                continue
            cursor = match + 1
            record = records[match]
            params = dict(step.params)
            llm_calls = record.get("llm_calls")
            if isinstance(llm_calls, int) and not isinstance(llm_calls, bool) and llm_calls > 0:
                params["llm_calls"] = llm_calls
            note = str(record.get("status") or "").strip()
            if note not in _PLAIN_STEP_STATUSES:
                params["note"] = note
            if params != step.params:
                self._recorder.update(step.step_id, params=params)
        self._recorder.complete("done")

    def fail(self) -> None:
        """回答の生成が失敗した（時間切れを含む）。実行中の段階を失敗にして終端（失敗）にする。"""
        self._recorder.complete("failed")

    def close(self, status: ChatProgressTerminalStatus) -> None:
        """作成を止めた（`cancelled`）・中断した（`failed`）。終端を記録する（終端の後は何もしない）。"""
        self._recorder.complete(status)

    def _on_event(self, event: ChatProgressEventItem) -> None:
        if isinstance(event, ChatProgressStepEvent):
            self._log(event)
        if self._sink is not None:
            self._sink(event)

    def _log(self, event: ChatProgressStepEvent) -> None:
        if event.status == "running":
            if event.step_id in self._running_since:
                return
            self._running_since[event.step_id] = self._timer()
            logger.info(
                "チャットの段階を開始しました",
                extra={**self._log_fields(event.step_id), "status": "running"},
            )
            return
        if event.status == "pending":
            return
        since = self._running_since.pop(event.step_id, None)
        if since is None and event.status != "failed":
            # 始まらなかった段階のスキップ（rerank 無効・回答の完了）は記録しない。
            return
        logger.log(
            logging.WARNING if event.status == "failed" else logging.INFO,
            "チャットの段階が終わりました",
            extra={
                **self._log_fields(event.step_id),
                "status": event.status,
                "duration_ms": (
                    round((self._timer() - since) * 1000, 3) if since is not None else None
                ),
            },
        )

    def _log_fields(self, step_id: str) -> dict[str, object]:
        step = self._recorder.step(step_id)
        return {
            "event": "chat_progress_step",
            "request_id": request_id_var.get(),
            "trace_id": self._trace_id,
            "model_id": self._model_id,
            "message_id": self._recorder.target_id,
            "step_id": step_id,
            "step_kind": step.kind if step is not None else None,
            "step_name": (step.params or {}).get("name") if step is not None else None,
        }
