"""チャットの処理の段階（3 製品共通の `ChatProgressStep`。#1146）。

回答の生成の内部の工程（``SearchStageProgress``。`history_rewrite` / `field_filter` / 回答フローの
``answer_step:<工程名>`` / `answer_guardrail`）を、利用者向けの粗い段階
（質問の整理 → 文書の検索 → 並べ替え → 回答の作成 → 回答の確認）へまとめる。

段階の形は NL2SQL・Agent と同じ契約（platform の `ChatProgress` の `ChatProgressStep`）:
``{"id", "label", "status": "pending" | "running" | "done" | "failed" | "skipped",
"startedAt"?, "finishedAt"?, "detail"?}``。detail には件数・回数などの短い補足だけを入れ、
技術的な詳細（例外の種類・内部の工程名）は入れない。
"""

from __future__ import annotations

import logging
import re
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from time import perf_counter
from typing import Literal

from pr_backend_core.observability import request_id_var

from app.rag.answer_engine import ANSWER_STEP_STAGE_PREFIX
from app.rag.pipeline import SearchStageProgress

logger = logging.getLogger(__name__)

type ChatProgressStatus = Literal["pending", "running", "done", "failed", "skipped"]

REWRITE_QUERY = "rewrite_query"
RETRIEVE = "retrieve"
RERANK = "rerank"
GENERATE_ANSWER = "generate_answer"
CHECK_GUARDRAIL = "check_guardrail"

# 段階の id と利用者向けの文言（画面にそのまま出す。並びは処理の順）。
CHAT_PROGRESS_STEPS: tuple[tuple[str, str], ...] = (
    (REWRITE_QUERY, "質問を整理しています"),
    (RETRIEVE, "関係する文書を探しています"),
    (RERANK, "並べ替えています"),
    (GENERATE_ANSWER, "回答を作っています"),
    (CHECK_GUARDRAIL, "回答を確認しています"),
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


def _now_iso(now: datetime) -> str:
    return now.astimezone(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")


@dataclass
class _StepState:
    id: str
    label: str
    status: ChatProgressStatus = "pending"
    started_at: datetime | None = None
    finished_at: datetime | None = None
    detail: str | None = None
    # 今の「実行中」が始まった時刻（ログの所要時間）。
    running_since: float | None = None

    def to_payload(self) -> dict[str, str]:
        payload = {"id": self.id, "label": self.label, "status": self.status}
        if self.started_at is not None:
            payload["startedAt"] = _now_iso(self.started_at)
        if self.finished_at is not None:
            payload["finishedAt"] = _now_iso(self.finished_at)
        if self.detail:
            payload["detail"] = self.detail
        return payload


class ChatProgressTracker:
    """1 モデル分の回答の段階を、内部の工程の進捗から組み立てる。

    実行中の段階は 1 つだけにする（別の段階の工程が始まったら、前の実行中の段階は終わったと
    する）。工程は入れ子になる（例: 文書検索の中の Rerank）ので、内側の工程が始まった時点で
    外側の段階は終える。
    段階は先へだけ進める（#1358）。回答フローは前の段階の工程へ戻ることがある（Rerank の後の
    文書の選択・根拠確認、補正検索（CRAG）の 2 回目の文書検索）。戻るたびに完了した段階を実行中に
    戻すと、画面の「N ステップ完了」の一覧から段階が消えて、また現れる。前の段階の工程は今の段階の
    続きとして扱い、補正検索の回数だけを検索の段階の補足に出す。
    """

    def __init__(
        self,
        *,
        rerank_enabled: bool = True,
        model_id: str = "",
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
        timer: Callable[[], float] = perf_counter,
    ) -> None:
        self._rerank_enabled = rerank_enabled
        self._model_id = model_id
        self._clock = clock
        self._timer = timer
        self._trace_id: str | None = None
        self._steps = [
            _StepState(id=step_id, label=label) for step_id, label in CHAT_PROGRESS_STEPS
        ]
        self._by_id = {step.id: step for step in self._steps}
        self._last_touched: _StepState | None = None
        if not rerank_enabled:
            self._by_id[RERANK].status = "skipped"

    def snapshot(self) -> list[dict[str, str]]:
        """契約の形（`ChatProgressStep[]`）の今の段階の一覧。"""
        return [step.to_payload() for step in self._steps]

    def observe(self, progress: SearchStageProgress) -> bool:
        """内部の工程の開始・終了を段階へ反映する。段階の一覧が変わったら True。"""
        self._trace_id = progress.trace_id or self._trace_id
        step_id = chat_progress_step_id(progress.stage, rerank_enabled=self._rerank_enabled)
        if step_id is None:
            return False
        if progress.outcome != "started":
            # 段階は次の段階が始まるまで（または回答ができるまで）実行中のままにする。工程の
            # 終わりごとに完了へ変えると、同じ段階の工程が続くたびに表示が完了と実行中を行き来する。
            # 工程の中の失敗は回答フローが続けることがある（補正・是正）ので、ここでは失敗にしない。
            # 回答の生成そのものの失敗（時間切れを含む）は `fail()` で反映する。
            return False
        step = self._by_id[step_id]
        detail = _retrieval_attempt_detail(progress.stage)
        current = self._last_touched
        if current is not None and self._steps.index(step) < self._steps.index(current):
            # 前の段階の工程（戻り）。完了した段階を実行中に戻さない（#1358）。
            if detail and step.detail != detail:
                step.detail = detail
                return True
            return False
        self._last_touched = step
        changed = self._start(step)
        if detail and step.detail != detail:
            step.detail = detail
            changed = True
        return changed

    def finish(self, *, citation_count: int) -> None:
        """回答ができた。実行中の段階を終わらせ、始まらなかった段階を `skipped` にする。"""
        for step in self._steps:
            if step.status == "running":
                self._end(step, "done")
            elif step.status == "pending":
                step.status = "skipped"
        retrieve = self._by_id[RETRIEVE]
        if retrieve.status == "done":
            retrieve.detail = f"根拠 {citation_count} 件"

    def fail(self) -> None:
        """回答の生成が失敗した（時間切れを含む）。実行中の段階を `failed` にする。

        実行中の段階が無いときは、最後に動いた段階の次の段階（何も始まっていなければ先頭）を
        `failed` にする。
        """
        running = [step for step in self._steps if step.status == "running"]
        if running:
            for step in running:
                self._end(step, "failed")
            return
        start = self._steps.index(self._last_touched) + 1 if self._last_touched is not None else 0
        for step in self._steps[start:]:
            if step.status == "pending":
                self._end(step, "failed")
                return
        if self._last_touched is not None:
            self._end(self._last_touched, "failed")

    def _start(self, step: _StepState) -> bool:
        if step.status == "running":
            return False
        for other in self._steps:
            if other is not step and other.status == "running":
                self._end(other, "done")
        step.status = "running"
        step.started_at = step.started_at or self._clock()
        step.finished_at = None
        step.running_since = self._timer()
        logger.info(
            "チャットの段階を開始しました",
            extra={**self._log_fields(step), "status": "running"},
        )
        return True

    def _end(self, step: _StepState, status: ChatProgressStatus) -> None:
        step.status = status
        step.finished_at = self._clock()
        duration_ms = (
            round((self._timer() - step.running_since) * 1000, 3)
            if step.running_since is not None
            else None
        )
        step.running_since = None
        logger.log(
            logging.WARNING if status == "failed" else logging.INFO,
            "チャットの段階が終わりました",
            extra={**self._log_fields(step), "status": status, "duration_ms": duration_ms},
        )

    def _log_fields(self, step: _StepState) -> dict[str, object]:
        return {
            "event": "chat_progress_step",
            "request_id": request_id_var.get(),
            "trace_id": self._trace_id,
            "model_id": self._model_id,
            "step_id": step.id,
        }


def _retrieval_attempt_detail(stage: str) -> str | None:
    """補正検索（2 回目以降の文書検索）の回数の補足。"""
    match = _RETRIEVAL_ATTEMPT.match(stage.removeprefix(ANSWER_STEP_STAGE_PREFIX).strip())
    if match is None or int(match.group(1)) < 2:
        return None
    return f"{int(match.group(1))} 回目"
