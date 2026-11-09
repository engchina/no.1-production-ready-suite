"""Run の事実（1 Run = 1 行。#794）。

利用状況（#772）・フィードバック（#774）・確かめるべき実行（#1822）の集計の入力。Run の snapshot
（`RunState`）から、集計に要る項目（業務 Agent・利用者・状態・モデルの利用量・起点・本人と管理者の
評価・回答の対応と最終の検証の結果・ツールの失敗と承認の却下の件数・質問と回答・日時）だけを
取り出す。Oracle の構成では `AGENT_RUN_FACTS` に MERGE して SQL で集計し（`run_facts_store`）、
memory / file の構成ではメモリの Run からその場で作って Python で集計する。
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime
from typing import Any

from pydantic import BaseModel

from app.features.agent.answer_outcome import environment_tool_calls
from app.features.agent.runtime import (
    ApprovalStatus,
    RunEventType,
    RunFeedback,
    RunState,
    RunStatus,
    StepStatus,
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
    # 回答の対応（成果物 `answer` の `outcome.value`。#1305）。記録の無い Run は None。
    answer_outcome: str | None = None
    # 失敗したツールの呼び出しの数と、却下した承認の数（#1822）。
    tool_failures: int = 0
    approvals_rejected: int = 0
    # 業務データの道具（NL2SQL など RAG 以外の MCP 接続のツール）の成功した呼び出しの数（#1834）。
    # 記録していない古い事実は None（「根拠を使わずに答えた」にしない）。
    data_tool_calls: int | None = None
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


def run_finished_at(run: RunState) -> datetime | None:
    """終わった Run の終了日時（終わりの Event の時刻。実行中・待ちは None）。

    実行履歴の一覧と利用状況の集計で同じ値を使う。
    """
    if run.status not in _TERMINAL_STATUSES:
        return None
    for event in reversed(run.events):
        if event.type in _TERMINAL_EVENTS:
            return event.created_at
    return run.updated_at


def _latest_artifact_content(run: RunState, kind: str) -> dict[str, Any] | None:
    for artifact in reversed(run.artifacts):
        if artifact.kind == kind and isinstance(artifact.content, dict):
            return artifact.content
    return None


def run_answer_outcome(run: RunState) -> str | None:
    """回答の対応（`answer_outcome.answer_outcome` の `value`。#1305）。"""
    content = _latest_artifact_content(run, "answer")
    outcome = content.get("outcome") if content else None
    value = outcome.get("value") if isinstance(outcome, dict) else None
    return value if isinstance(value, str) and value else None


def run_tool_failures(run: RunState) -> int:
    """失敗した呼出しと、呼出し前に停止した必須 MCP 接続の取得失敗。"""
    discovery_failed = any(
        event.type == RunEventType.RUNTIME_FAILED
        and event.payload.get("error_code") == "runtime.mcp_unavailable"
        for event in run.events
    )
    return sum(1 for step in run.steps if step.status == StepStatus.FAILED) + int(discovery_failed)


def run_approvals_rejected(run: RunState) -> int:
    """却下した承認の数（終了で無効にした承認は数えない）。"""
    return sum(1 for approval in run.approvals if approval.status == ApprovalStatus.REJECTED)


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
        answer_outcome=run_answer_outcome(run),
        tool_failures=run_tool_failures(run),
        approvals_rejected=run_approvals_rejected(run),
        data_tool_calls=environment_tool_calls(run.steps),
        created_at=run.created_at,
        finished_at=run_finished_at(run),
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
    "run_answer_outcome",
    "run_approvals_rejected",
    "run_source",
    "run_tool_failures",
]
