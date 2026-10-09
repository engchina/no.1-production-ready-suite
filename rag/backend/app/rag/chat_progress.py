"""チャットの処理の段階（3 製品共通の契約 `pr_backend_core.chat_progress`。#1146 / #1359）。

回答の生成の内部の工程（``SearchStageProgress``。`history_rewrite` / `field_filter` / 回答フローの
``answer_step:<工程名>`` / `answer_guardrail`）を、利用者向けの粗い段階
（質問の整理 → 文書の検索 → 並べ替え → 回答の作成 → 回答の確認）へまとめ、段階のイベントとして
記録する（記録・番号・状態の進み方・終端は共通の `ChatProgressRecorder` が持つ）。

対象（`target_id`）は作成中の ASSISTANT のメッセージの id。記録したイベントの一覧は、
そのメッセージの `progress_json` に保存し、回答の配信（SSE の `chat_progress`）と、段階の
polling / SSE の endpoint（`GET .../messages/{回答の id}/progress[/stream]`）で配る。

段階の名前（利用者向けの文言）は記録に入れない。画面が段階の id から i18n で付ける
（`frontend/src/lib/chat-progress.ts`）。補足は値（`params`）だけを入れる: 補正検索の回数
（`{"attempt": n}`）・回答の根拠の件数（`{"citations": n}`）。
"""

from __future__ import annotations

import logging
import re
from collections.abc import Callable
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

REWRITE_QUERY = "rewrite_query"
RETRIEVE = "retrieve"
RERANK = "rerank"
GENERATE_ANSWER = "generate_answer"
CHECK_GUARDRAIL = "check_guardrail"

# 段階の id（並びは処理の順。最初に待機中として出し、画面の並びを決める）。名前は画面の i18n。
CHAT_PROGRESS_STEP_IDS: tuple[str, ...] = (
    REWRITE_QUERY,
    RETRIEVE,
    RERANK,
    GENERATE_ANSWER,
    CHECK_GUARDRAIL,
)

# パイプラインの工程（`app/rag/pipeline.py` の `_observe_stage`）→ 段階。
# `answer`（回答フロー全体）は中の工程を `answer_step:` で送るので、段階にしない。
_PIPELINE_STAGE_STEPS: dict[str, str] = {
    "history_rewrite": REWRITE_QUERY,
    "field_filter": REWRITE_QUERY,
    "retrieval": RETRIEVE,
    "answer_guardrail": CHECK_GUARDRAIL,
}

# 回答フロー（`rag_engine.generation.answering` の `_execution_step`）の工程名の先頭 → 段階。
# 「回答生成フロー」は検索と回答の生成を包む工程なので、段階にしない。
_ANSWER_STEP_PREFIXES: tuple[tuple[str, str], ...] = (
    ("質問の理解", REWRITE_QUERY),
    ("用語・ルールの確認", REWRITE_QUERY),
    ("検索の準備確認", REWRITE_QUERY),
    ("質問拡張戦略", REWRITE_QUERY),
    ("検索文と検索語の確定", REWRITE_QUERY),
    ("文書検索", RETRIEVE),
    ("根拠確認", RETRIEVE),
    ("文書の選択", RETRIEVE),
    ("補正検索の準備", RETRIEVE),
    ("文脈の追加", RETRIEVE),
    ("画面の選択", RETRIEVE),
    ("検索語の再確定", RETRIEVE),
    ("Rerank", RERANK),
    ("回答に使う画像の確認", GENERATE_ANSWER),
    ("回答文の生成", GENERATE_ANSWER),
    ("是正", GENERATE_ANSWER),
)

# 「文書検索（2回目）」のような補正検索の回数。
_RETRIEVAL_ATTEMPT = re.compile(r"^文書検索（(\d+)回目）$")


def chat_progress_step_id(stage: str, *, rerank_enabled: bool = True) -> str | None:
    """内部の工程の名前を段階の id にする。段階にしない工程（全体を包む工程・未知の工程）は None。

    rerank を無効にした設定でも、回答フローは「Rerank」の工程で業務の絞り込みなどを行うので、
    そのときは検索の一部として扱う（並べ替えの段階は `skipped`）。
    """
    if not stage.startswith(ANSWER_STEP_STAGE_PREFIX):
        return _PIPELINE_STAGE_STEPS.get(stage)
    name = stage.removeprefix(ANSWER_STEP_STAGE_PREFIX).strip()
    for prefix, step_id in _ANSWER_STEP_PREFIXES:
        if name.startswith(prefix):
            if step_id == RERANK and not rerank_enabled:
                return RETRIEVE
            return step_id
    return None


type ChatProgressEventItem = ChatProgressStepEvent | ChatProgressTerminalEvent


class ChatProgressTracker:
    """1 モデル分の回答（作成中の ASSISTANT のメッセージ）の段階を、内部の工程の進捗から記録する。

    実行中の段階は 1 つだけにする（別の段階の工程が始まったら、前の実行中の段階は終わったと
    する。`start(..., exclusive=True)`）。工程は入れ子になる（例: 文書検索の中の Rerank）ので、
    内側の工程が始まった時点で外側の段階は終える。
    段階は先へだけ進める（#1358）。回答フローは前の段階の工程へ戻ることがある（Rerank の後の
    文書の選択・根拠確認、補正検索（CRAG）の 2 回目の文書検索）。戻るたびに完了した段階を実行中に
    戻すと、画面の「N ステップ完了」の一覧から段階が消えて、また現れる。前の段階の工程は今の段階の
    続きとして扱い、補正検索の回数だけを検索の段階の値（`attempt`）に出す。

    記録したイベントは `sink`（`attach` で後から付けられる）に 1 件ずつ渡す。保存は呼び出し元が
    `dump()` で行う。スレッドから同時に呼ばない（イベントループの中で呼ぶ）。
    """

    def __init__(
        self,
        message_id: str,
        *,
        rerank_enabled: bool = True,
        model_id: str = "",
        events: list[ChatProgressEventItem] | None = None,
        sink: ChatProgressSink | None = None,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
        timer: Callable[[], float] = perf_counter,
    ) -> None:
        self._rerank_enabled = rerank_enabled
        self._model_id = model_id
        self._timer = timer
        self._trace_id: str | None = None
        self._sink = sink
        # 段階が実行中になった時刻（ログの所要時間）。
        self._running_since: dict[str, float] = {}
        # 最後に始めた段階（戻りの判定）。
        self._last_index: int | None = None
        self._recorder = ChatProgressRecorder(
            message_id, events=events or (), sink=self._on_event, clock=clock
        )
        self._recorder.declare(*CHAT_PROGRESS_STEP_IDS)
        if not rerank_enabled:
            self._recorder.finish(RERANK, "skipped")

    @property
    def message_id(self) -> str:
        return self._recorder.target_id

    @property
    def model_id(self) -> str:
        return self._model_id

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
        step_id = chat_progress_step_id(progress.stage, rerank_enabled=self._rerank_enabled)
        if step_id is None or progress.outcome != "started":
            # 段階は次の段階が始まるまで（または回答ができるまで）実行中のままにする。工程の
            # 終わりごとに完了へ変えると、同じ段階の工程が続くたびに表示が完了と実行中を行き来する。
            # 工程の中の失敗は回答フローが続けることがある（補正・是正）ので、ここでは失敗にしない。
            # 回答の生成そのものの失敗（時間切れを含む）は `fail()` で反映する。
            return False
        before = self._recorder.last_seq
        attempt = _retrieval_attempt(progress.stage)
        index = CHAT_PROGRESS_STEP_IDS.index(step_id)
        if self._last_index is not None and index < self._last_index:
            # 前の段階の工程（戻り）。完了した段階を実行中に戻さない（#1358）。
            # 補正検索の回数だけ出す。
            if attempt is not None:
                self._recorder.update(step_id, params=self._params(step_id, attempt=attempt))
            return self._recorder.last_seq != before
        self._last_index = index
        if attempt is not None:
            self._recorder.start(
                step_id, exclusive=True, params=self._params(step_id, attempt=attempt)
            )
        else:
            self._recorder.start(step_id, exclusive=True)
        return self._recorder.last_seq != before

    def finish(self, *, citation_count: int) -> None:
        """回答ができた。根拠の件数を検索の段階に付けてから、終端（完了）にする。

        実行中の段階は完了、始まらなかった段階はスキップになる（`complete("done")`）。
        """
        retrieve = self._recorder.step(RETRIEVE)
        if retrieve is not None and retrieve.status in {"running", "done"}:
            self._recorder.update(RETRIEVE, params=self._params(RETRIEVE, citations=citation_count))
        self._recorder.complete("done")

    def fail(self) -> None:
        """回答の生成が失敗した（時間切れを含む）。実行中の段階を失敗にして終端（失敗）にする。

        実行中の段階が無いときは、最後に動いた段階の次の段階（何も始まっていなければ先頭）を
        失敗にする（どこで止まったかを示す）。
        """
        if self._recorder.terminal is not None:
            return
        steps = {step.step_id: step for step in self._recorder.steps()}
        if not any(step.status == "running" for step in steps.values()):
            start = self._last_index + 1 if self._last_index is not None else 0
            target = next(
                (
                    step_id
                    for step_id in CHAT_PROGRESS_STEP_IDS[start:]
                    if step_id in steps and steps[step_id].status == "pending"
                ),
                None,
            )
            if target is None and self._last_index is not None:
                target = CHAT_PROGRESS_STEP_IDS[self._last_index]
            if target is not None:
                self._recorder.finish(target, "failed")
        self._recorder.complete("failed")

    def close(self, status: ChatProgressTerminalStatus) -> None:
        """作成を止めた（`cancelled`）・中断した（`failed`）。終端を記録する（終端の後は何もしない）。"""
        self._recorder.complete(status)

    def _params(
        self, step_id: str, *, attempt: int | None = None, citations: int | None = None
    ) -> dict[str, str | int | float | bool]:
        step = self._recorder.step(step_id)
        params: dict[str, str | int | float | bool] = dict(step.params or {}) if step else {}
        if attempt is not None:
            params["attempt"] = attempt
        if citations is not None:
            params["citations"] = citations
        return params

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
        return {
            "event": "chat_progress_step",
            "request_id": request_id_var.get(),
            "trace_id": self._trace_id,
            "model_id": self._model_id,
            "message_id": self._recorder.target_id,
            "step_id": step_id,
        }


def _retrieval_attempt(stage: str) -> int | None:
    """補正検索（2 回目以降の文書検索）の回数。"""
    match = _RETRIEVAL_ATTEMPT.match(stage.removeprefix(ANSWER_STEP_STAGE_PREFIX).strip())
    if match is None or int(match.group(1)) < 2:
        return None
    return int(match.group(1))
