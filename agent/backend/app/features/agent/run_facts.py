"""Run の事実（1 Run = 1 行。#794）。

利用状況（#772）・フィードバック（#774）の集計の入力。Run の snapshot（`RunState`）から、集計に要る
項目（業務 Agent・利用者・状態・モデルの利用量・起点・本人と管理者の評価・質問と回答・日時）だけを
取り出す。Oracle の構成では `AGENT_RUN_FACTS` に MERGE して SQL で集計し（`run_facts_store`）、
memory / file の構成ではメモリの Run からその場で作って Python で集計する。
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime
from typing import Any

from pydantic import BaseModel

from app.features.agent.runtime import (
    RunEventType,
    RunFeedback,
    RunState,
    RunStatus,
    run_answer_text,
)

# 画面で選べる集計の期間（日）。90 日を超える期間は保存した事実（Oracle）で集計する。
REPORT_PERIOD_DAYS: tuple[int, ...] = (7, 30, 90, 180, 365)

# Run の起点（`metadata` から決める）。
RUN_SOURCE_EVALUATION = "evaluation"
RUN_SOURCE_AUTOMATION = "automation"
RUN_SOURCE_MCP = "mcp"
RUN_SOURCE_REPLAY = "replay"
RUN_SOURCE_USER = "user"

_TERMINAL_EVENTS = {
    RunEventType.RUN_COMPLETED,
    RunEventType.RUNTIME_FAILED,
    RunEventType.RUN_CANCELLED,
}
_TERMINAL_STATUSES = {RunStatus.COMPLETED, RunStatus.FAILED, RunStatus.CANCELLED}


class RunFact(BaseModel):
    """集計に使う Run の事実。日時はすべて timezone 付き（UTC で保存する）。"""

    run_id: str
    agent_id: str
    # 記録した時点の業務 Agent の名前（削除した Agent の名前を集計に残す）。
    agent_name: str = ""
    # 使った版（公開した版の番号か "draft"。#770）。
    agent_version: str = ""
    thread_id: str | None = None
    user_uuid: str | None = None
    status: str
    source: str = RUN_SOURCE_USER
    # モデルの利用量（#772）。記録していない Run は None。
    model: str | None = None
    requests: int | None = None
    input_tokens: int | None = None
    output_tokens: int | None = None
    total_tokens: int | None = None
    feedback: RunFeedback | None = None
    admin_review: RunFeedback | None = None
    question: str = ""
    answer: str | None = None
    created_at: datetime
    finished_at: datetime | None = None

    @property
    def has_usage(self) -> bool:
        return self.requests is not None

    @property
    def rated_at(self) -> datetime | None:
        """新しい方の評価の日時（本人・管理者のどちらも無ければ None）。"""
        times = [item.updated_at for item in (self.feedback, self.admin_review) if item is not None]
        return max(times) if times else None


def run_source(metadata: Mapping[str, Any]) -> str:
    """Run の起点（品質評価・自動実行・MCP・再実行・利用者の操作）。"""
    if metadata.get("evaluation_job_id"):
        return RUN_SOURCE_EVALUATION
    if metadata.get("automation_id"):
        return RUN_SOURCE_AUTOMATION
    if metadata.get("source") == RUN_SOURCE_MCP:
        return RUN_SOURCE_MCP
    if metadata.get("replayed_from_run_id"):
        return RUN_SOURCE_REPLAY
    return RUN_SOURCE_USER


def _finished_at(run: RunState) -> datetime | None:
    if run.status not in _TERMINAL_STATUSES:
        return None
    for event in reversed(run.events):
        if event.type in _TERMINAL_EVENTS:
            return event.created_at
    return run.updated_at


def fact_from_run(run: RunState, *, agent_name: str = "") -> RunFact:
    """Run の snapshot から事実を作る（Run は変えない）。"""
    usage = run.usage
    version = run.metadata.get("agent_version")
    return RunFact(
        run_id=run.id,
        agent_id=run.agent_id,
        agent_name=agent_name,
        agent_version="" if version is None else str(version),
        thread_id=run.thread_id,
        user_uuid=run.created_by_user_uuid,
        status=run.status.value,
        source=run_source(run.metadata),
        model=usage.model if usage is not None else None,
        requests=usage.requests if usage is not None else None,
        input_tokens=usage.input_tokens if usage is not None else None,
        output_tokens=usage.output_tokens if usage is not None else None,
        total_tokens=usage.total_tokens if usage is not None else None,
        feedback=run.feedback.model_copy() if run.feedback is not None else None,
        admin_review=run.admin_review.model_copy() if run.admin_review is not None else None,
        question=run.goal,
        answer=run_answer_text(run),
        created_at=run.created_at,
        finished_at=_finished_at(run),
    )


__all__ = [
    "REPORT_PERIOD_DAYS",
    "RUN_SOURCE_AUTOMATION",
    "RUN_SOURCE_EVALUATION",
    "RUN_SOURCE_MCP",
    "RUN_SOURCE_REPLAY",
    "RUN_SOURCE_USER",
    "RunFact",
    "fact_from_run",
    "run_source",
]
