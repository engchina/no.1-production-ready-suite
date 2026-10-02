"""業務 Agent の Run・Event・Artifact・Approval の保存と、組み込み Runtime の実行の記録。

永続 DB 導入前でも API / 権限 / 承認 / SSE を検証できるよう、
append-only event log をプロセス内 repository として実装する。
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
from collections.abc import Callable, Iterator, Sequence
from copy import deepcopy
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from pathlib import Path
from threading import Condition, Lock
from typing import Any, Protocol
from uuid import uuid4

from pydantic import BaseModel, Field, computed_field, field_validator, model_validator

from app.features.agent.config import runtime_config_store
from app.features.agent.tools import (
    ToolCall,
    ToolInvocationContext,
    ToolPolicy,
    ToolResult,
    mcp_base_tool_name,
    tool_registry,
)
from app.observability import record_runtime_event
from app.oracle_connection import connect_platform_oracle
from app.settings import get_settings

logger = logging.getLogger(__name__)
# Run・業務 Agent の Oracle のテーブル（作成はシステムテーブル。`app.system_schema`。#764）。
RUNTIME_CHECKPOINT_TABLE = "AGENT_RUNTIME_CHECKPOINTS"
RUNTIME_PROJECTION_PREFIX = "AGENT_RUNTIME"
JsonObject = dict[str, Any]
OracleConnectFactory = Callable[[], Any]

# 組み込み Runtime（#754）の Runtime ID と、承認待ちの Run に保存する SDK の状態の key。
BUILTIN_RUNTIME_ID = "builtin"
_BUILTIN_STATE_KEY = "_builtin_sdk_state"


def _now() -> datetime:
    return datetime.now(UTC)


class RunStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    WAITING_APPROVAL = "waiting_approval"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class StepStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    WAITING_APPROVAL = "waiting_approval"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class ApprovalStatus(StrEnum):
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"
    CANCELLED = "cancelled"


class RunEventType(StrEnum):
    RUN_CREATED = "run.created"
    RUN_REPLAYED = "run.replayed"
    RUN_STATUS_CHANGED = "run.status_changed"
    STEP_STARTED = "step.started"
    TOOL_APPROVAL_REQUIRED = "tool.approval_required"
    TOOL_COMPLETED = "tool.completed"
    TOOL_FAILED = "tool.failed"
    TOOL_GUARDRAIL_WARNING = "tool.guardrail_warning"
    SKILL_PLANNED = "skill.planned"
    APPROVAL_DECIDED = "approval.decided"
    ARTIFACT_CREATED = "artifact.created"
    RUN_COMPLETED = "run.completed"
    RUN_CANCELLED = "run.cancelled"
    RUNTIME_SUBMITTED = "runtime.submitted"
    RUNTIME_EVENT = "runtime.event"
    RUNTIME_FAILED = "runtime.failed"
    RUNTIME_DISPATCH_CLAIMED = "runtime.dispatch_claimed"
    # 旧エンジン（#756 で削除）が記録したイベント。保存済みの Run を読み込むためだけに残す。
    MEMORY_WRITTEN = "memory.written"
    PLANNER_COMPLETED = "planner.completed"


class ArtifactContentRef(BaseModel):
    backend: str
    uri: str
    content_type: str = "application/json"
    size_bytes: int | None = None
    sha256: str | None = None


class Artifact(BaseModel):
    id: str = Field(default_factory=lambda: f"artifact_{uuid4().hex}")
    name: str
    kind: str
    content: JsonObject
    content_ref: ArtifactContentRef | None = None
    created_at: datetime = Field(default_factory=_now)


class RunEvent(BaseModel):
    id: str = Field(default_factory=lambda: f"event_{uuid4().hex}")
    run_id: str
    type: RunEventType
    message: str
    payload: JsonObject = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=_now)


class RunStep(BaseModel):
    id: str = Field(default_factory=lambda: f"step_{uuid4().hex}")
    run_id: str
    kind: str = "tool"
    status: StepStatus = StepStatus.PENDING
    tool_call: ToolCall | None = None
    tool_result: ToolResult | None = None
    approval_id: str | None = None
    started_at: datetime | None = None
    completed_at: datetime | None = None


class ApprovalRequest(BaseModel):
    id: str = Field(default_factory=lambda: f"approval_{uuid4().hex}")
    run_id: str
    step_id: str
    tool_call: ToolCall
    status: ApprovalStatus = ApprovalStatus.PENDING
    reason: str
    decided_by: str | None = None
    decided_at: datetime | None = None
    created_at: datetime = Field(default_factory=_now)


# 版に残す業務 Agent の項目（下書き = AgentProfile の同名の項目。#770）。
AGENT_VERSIONED_FIELDS: tuple[str, ...] = (
    "name",
    "description",
    "instructions",
    "skill_ids",
    "model_id",
)


class AgentVersion(BaseModel):
    """公開した業務 Agent の版（#770）。利用者の Run は公開中の版の内容で実行する。"""

    version: int
    name: str
    description: str = ""
    instructions: str = ""
    skill_ids: list[str] = Field(default_factory=list)
    model_id: str = ""
    note: str = ""
    published_at: datetime = Field(default_factory=_now)
    published_by: str | None = None


class AgentProfile(BaseModel):
    id: str = Field(default_factory=lambda: f"agent_{uuid4().hex}")
    name: str
    description: str = ""
    instructions: str = ""
    skill_ids: list[str] = Field(default_factory=list)
    # 組み込み Runtime で使うモデル（OCI Enterprise AI の model_id）。
    # 空なら既定のテキストモデル（#754）。
    model_id: str = ""
    migration_required: bool = False
    # Deprecated read compatibility。新規 UI/API は skill_ids だけを書き込む。
    tool_names: list[str] = Field(default_factory=list)
    enabled: bool = True
    # 由来層: builtin(default)/ runtime(UI/API)/ plugin:<id>(plugin install)。
    source: str = "runtime"
    # 版（#770）。上の名前〜モデルは下書きで、公開すると版になる。利用者の Run は公開中の版で動く。
    # versioned=False は #770 より前の Agent（読み込み時に現在の内容を v1 として公開する）。
    versioned: bool = False
    versions: list[AgentVersion] = Field(default_factory=list)
    published_version: int | None = None
    created_at: datetime = Field(default_factory=_now)
    updated_at: datetime = Field(default_factory=_now)

    def published(self) -> AgentVersion | None:
        return next(
            (item for item in self.versions if item.version == self.published_version), None
        )

    @computed_field  # type: ignore[prop-decorator]
    @property
    def unpublished_changes(self) -> bool:
        """下書きに公開していない変更があるか（公開した版が無いときも True）。"""
        return self.has_unpublished_changes()

    def has_unpublished_changes(self) -> bool:
        published = self.published()
        if published is None:
            return True
        return any(
            getattr(self, field) != getattr(published, field) for field in AGENT_VERSIONED_FIELDS
        )


class AgentNotPublishedError(ValueError):
    """公開した版の無い業務 Agent を、下書きではない Run で実行しようとした（#770）。"""


class AgentPublishRequest(BaseModel):
    note: str = Field(default="", max_length=500)


class AgentProfilePatch(BaseModel):
    name: str | None = None
    description: str | None = None
    instructions: str | None = None
    skill_ids: list[str] | None = None
    model_id: str | None = None
    tool_names: list[str] | None = None
    enabled: bool | None = None


class RunCreateRequest(BaseModel):
    goal: str
    agent_id: str = "default"
    metadata: JsonObject = Field(default_factory=dict)
    # 公開前の下書きで実行する（管理者が試すとき。#770）。既定は公開中の版。
    draft: bool = False
    # 会話（スレッド）を続けるときの ID（#768）。省略すると新しい会話を始める。
    thread_id: str | None = Field(default=None, pattern=r"^thread_[0-9a-f]{32}$")

    @field_validator("goal")
    @classmethod
    def _require_goal(cls, value: str) -> str:
        # ゴールは画面と同じく必須（#540）。空白だけも未入力として扱う
        # Oracle の goal 列は NOT NULL。。
        if not value.strip():
            raise ValueError("ゴールを入力してください。")
        return value


class FeedbackRating(StrEnum):
    """回答の評価（RAG と同じ値。#774）。"""

    HELPFUL = "helpful"
    NOT_HELPFUL = "not_helpful"


class FeedbackReason(StrEnum):
    """役に立たなかった理由（RAG の回答の理由に、Agent のツールの使い方を足したもの。#774）。"""

    INCORRECT = "incorrect"
    INCOMPLETE = "incomplete"
    NOT_RELEVANT = "not_relevant"
    ANSWER_UNTRUSTED = "answer_untrusted"
    AMBIGUOUS_QUESTION = "ambiguous_question"
    WRONG_ACTION = "wrong_action"


FEEDBACK_COMMENT_MAX_CHARS = 1000


class RunFeedbackRequest(BaseModel):
    """チャットの回答への評価。役に立たなかったときは理由が必須（RAG と同じ。#774）。"""

    rating: FeedbackRating
    reason: FeedbackReason | None = None
    comment: str = Field(default="", max_length=FEEDBACK_COMMENT_MAX_CHARS)

    @model_validator(mode="after")
    def _reason_for_not_helpful(self) -> RunFeedbackRequest:
        if self.rating == FeedbackRating.NOT_HELPFUL:
            if self.reason is None:
                raise ValueError("役に立たなかった理由を選んでください。")
            self.comment = self.comment.strip()
        else:
            # 役に立った評価には理由・コメントを残さない（RAG と同じ）。
            self.reason = None
            self.comment = ""
        return self


class RunFeedback(BaseModel):
    """Run（チャットの 1 往復）の回答への評価。付け直すと上書きする（#774）。"""

    rating: FeedbackRating
    reason: FeedbackReason | None = None
    comment: str = ""
    user_uuid: str | None = None
    updated_at: datetime = Field(default_factory=_now)


class RunNotRatableError(ValueError):
    """回答の無い Run（完了していない Run）には評価を付けられない（#774）。"""


# 評価の Run（#776）で、承認が要るツールを実行しなかったときにモデルへ返す文。
EVALUATION_DRY_RUN_MESSAGE = (
    "品質評価の実行中のため、このツールは実行していません（承認が要る操作）。"
    "実行した場合の結果は分からないものとして、ここまでの情報で回答を完成させてください。"
)
# 評価の Run の印（`metadata`）。組み込み Runtime は承認が要るツールを実行せずに続ける。
EVALUATION_DRY_RUN_KEY = "evaluation_dry_run"


class RunUsage(BaseModel):
    """Run が使ったモデルの量（#772）。承認待ちからの再開を含めた累計（SDK の `Usage`）。"""

    model: str = ""
    requests: int = Field(default=0, ge=0)
    input_tokens: int = Field(default=0, ge=0)
    output_tokens: int = Field(default=0, ge=0)
    total_tokens: int = Field(default=0, ge=0)


class RunState(BaseModel):
    id: str
    goal: str
    agent_id: str
    runtime_id: str = "legacy-native"
    status: RunStatus
    steps: list[RunStep] = Field(default_factory=list)
    events: list[RunEvent] = Field(default_factory=list)
    approvals: list[ApprovalRequest] = Field(default_factory=list)
    artifacts: list[Artifact] = Field(default_factory=list)
    pending_tool_calls: list[ToolCall] = Field(default_factory=list)
    metadata: JsonObject = Field(default_factory=dict)
    # Run を作った利用者（共通認証の user_uuid。#233）。RAG / NL2SQL の MCP はこの利用者として呼ぶ。
    # この項目がない既存の Run は None。
    created_by_user_uuid: str | None = None
    # 会話（スレッド。#768）。チャットの 1 往復が 1 Run。#768 より前の Run は None。
    thread_id: str | None = None
    # 回答への評価（#774）。評価していない Run は None。
    feedback: RunFeedback | None = None
    # 管理者の評価（Agent 管理の権限。だれの回答にも付けられ、本人の評価とは別に残す。#774）。
    admin_review: RunFeedback | None = None
    # モデルの利用量（#772）。組み込み Runtime がモデルを呼ぶ前の Run・#772 より前の Run は None。
    usage: RunUsage | None = None
    created_at: datetime = Field(default_factory=_now)
    updated_at: datetime = Field(default_factory=_now)


class ThreadNotFoundError(LookupError):
    """会話が無い・別の利用者や別の Agent の会話（#768）。"""


class ThreadSummary(BaseModel):
    thread_id: str
    agent_id: str
    title: str
    run_count: int
    last_status: RunStatus
    created_at: datetime
    updated_at: datetime


class ThreadsData(BaseModel):
    threads: list[ThreadSummary] = Field(default_factory=list)


class ThreadData(BaseModel):
    thread_id: str
    agent_id: str
    runs: list[RunState] = Field(default_factory=list)


def run_answer_text(run: RunState) -> str | None:
    """組み込み Runtime の最終回答（`kind="answer"` の成果物）。"""
    for artifact in reversed(run.artifacts):
        if artifact.kind == "answer" and isinstance(artifact.content, dict):
            text = artifact.content.get("text")
            if isinstance(text, str):
                return text
    return None


class ApprovalDecisionRequest(BaseModel):
    approved: bool
    decided_by: str = "user"
    comment: str | None = None


class RunsData(BaseModel):
    runs: list[RunState]


class AgentsData(BaseModel):
    agents: list[AgentProfile]


class ArtifactsData(BaseModel):
    artifacts: list[Artifact]


class AgentRuntimeSnapshot(BaseModel):
    version: str = "agent-control-plane.snapshot.v2"
    exported_at: datetime = Field(default_factory=_now)
    runs: list[RunState] = Field(default_factory=list)
    agents: list[AgentProfile] = Field(default_factory=list)
    # 旧 Memory（#756 で削除）。読み込んでも使わない。
    memory: list[JsonObject] = Field(default_factory=list)
    # 旧版（#754 より前）の外部 Runtime と Binding。読み込んでも使わない。
    control_plane_state: JsonObject = Field(default_factory=dict)


class AgentRuntimeSnapshotSummary(BaseModel):
    runs: int = 0
    agents: int = 0
    events: int = 0
    steps: int = 0
    approvals: int = 0
    artifacts: int = 0
    pending_tool_calls: int = 0


class AgentRuntimeSnapshotValidation(BaseModel):
    valid: bool
    errors: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    summary: AgentRuntimeSnapshotSummary


class RuntimeToolCallAuditRecord(BaseModel):
    run_id: str
    run_goal: str
    run_status: str
    agent_id: str
    step_id: str
    tool_name: str
    status: str
    approval_id: str | None = None
    approval_status: str | None = None
    policy_decision: str | None = None
    permission_level: str | None = None
    side_effects: bool | None = None
    started_at: str | None = None
    completed_at: str | None = None
    duration_ms: int | None = None
    success: bool | None = None
    error: str | None = None
    error_code: str | None = None
    guardrail_warnings: list[str] = Field(default_factory=list)
    trace_id: str | None = None
    artifact_ids: list[str] = Field(default_factory=list)
    audit_metadata: JsonObject = Field(default_factory=dict)
    run_created_at: str
    run_updated_at: str


class RuntimeToolCallAuditData(BaseModel):
    total: int
    offset: int
    limit: int
    records: list[RuntimeToolCallAuditRecord]


class AgentRuntimeRepositoryContract(Protocol):
    def claim_control_plane_run(self, worker_id: str, *, lease_seconds: int) -> RunState | None: ...
    def persist_control_plane_state(self) -> None: ...
    def create_builtin_run(
        self, request: RunCreateRequest, *, created_by_user_uuid: str | None = None
    ) -> RunState: ...
    def begin_builtin_run(self, run_id: str) -> tuple[RunState, AgentProfile] | None: ...
    def begin_builtin_resume(
        self, run_id: str
    ) -> tuple[RunState, AgentProfile, str, dict[str, bool]] | None: ...
    def start_builtin_tool_step(
        self, run_id: str, call: ToolCall
    ) -> tuple[str, ToolInvocationContext]: ...
    def finish_builtin_tool_step(self, run_id: str, step_id: str, result: ToolResult) -> None: ...
    def request_builtin_approvals(
        self, run_id: str, calls: Sequence[ToolCall], *, state: str
    ) -> RunState: ...
    def complete_builtin_run(self, run_id: str, answer: str) -> RunState: ...
    def fail_builtin_run(self, run_id: str, *, code: str, detail: str) -> RunState: ...
    def note_builtin_warning(self, run_id: str, message: str) -> None: ...
    def set_run_feedback(
        self, run_id: str, feedback: RunFeedback, *, admin: bool = False
    ) -> RunState: ...
    def thread_history(self, run_id: str, *, limit: int) -> list[tuple[str, str]]: ...
    def list_threads(
        self, *, user_uuid: str | None, agent_id: str | None = None
    ) -> list[ThreadSummary]: ...
    def get_thread(self, thread_id: str, *, user_uuid: str | None) -> ThreadData: ...
    def record_builtin_dry_run_steps(self, run_id: str, calls: Sequence[ToolCall]) -> None: ...
    def record_builtin_usage(self, run_id: str, usage: RunUsage) -> None: ...
    def list_runs(self) -> list[RunState]: ...
    def get_run(self, run_id: str) -> RunState: ...
    def list_artifacts(self, run_id: str) -> list[Artifact]: ...
    def get_artifact(self, run_id: str, artifact_id: str) -> Artifact: ...
    def iter_events(
        self,
        run_id: str,
        *,
        after_event_id: str | None = None,
        follow: bool = False,
        idle_timeout_seconds: float = 15.0,
    ) -> Iterator[RunEvent | None]: ...
    def cancel_run(self, run_id: str) -> RunState: ...
    def decide_approval(self, approval_id: str, request: ApprovalDecisionRequest) -> RunState: ...
    def list_agents(self) -> list[AgentProfile]: ...
    def create_agent(self, agent: AgentProfile) -> AgentProfile: ...
    def patch_agent(self, agent_id: str, patch: AgentProfilePatch) -> AgentProfile: ...
    def publish_agent(
        self, agent_id: str, *, note: str = "", published_by: str | None = None
    ) -> AgentProfile: ...
    def restore_agent_version(
        self, agent_id: str, version: int, *, published_by: str | None = None
    ) -> AgentProfile: ...
    def delete_agent(self, agent_id: str) -> None: ...
    def set_plugin_agents(self, source: str, agents: list[AgentProfile]) -> None: ...
    def remove_agents_by_source(self, source: str) -> None: ...
    def export_snapshot(self) -> AgentRuntimeSnapshot: ...
    def validate_snapshot(
        self, snapshot: AgentRuntimeSnapshot
    ) -> AgentRuntimeSnapshotValidation: ...
    def replace_snapshot(self, snapshot: AgentRuntimeSnapshot) -> AgentRuntimeSnapshot: ...


class AgentRuntimeRepository:
    def __init__(self, snapshot_path: str | Path | None = None) -> None:
        self._lock = Lock()
        self._condition = Condition(self._lock)
        self._runs: dict[str, RunState] = {}
        self._approvals: dict[str, ApprovalRequest] = {}
        default_agent = _ensure_versioned(_default_agent())
        self._agents: dict[str, AgentProfile] = {default_agent.id: default_agent}
        self._snapshot_path = Path(snapshot_path) if snapshot_path else None
        if self._snapshot_path is not None and self._snapshot_path.exists():
            self._load_snapshot_from_disk()

    def list_runs(self) -> list[RunState]:
        with self._lock:
            return [run.model_copy(deep=True) for run in self._sorted_runs_locked()]

    def get_run(self, run_id: str) -> RunState:
        with self._lock:
            run = self._runs.get(run_id)
            if run is None:
                raise KeyError(run_id)
            return run.model_copy(deep=True)

    def claim_control_plane_run(self, worker_id: str, *, lease_seconds: int) -> RunState | None:
        with self._lock:
            run = self._claim_control_plane_run_locked(worker_id, lease_seconds=lease_seconds)
            if run is None:
                return None
            self._persist_locked()
            return run.model_copy(deep=True)

    def _claim_control_plane_run_locked(
        self, worker_id: str, *, lease_seconds: int
    ) -> RunState | None:
        now = _now()
        for run in reversed(self._sorted_runs_locked()):
            # 組み込み Runtime の queued の Run（新規・承認の決定後の再開。#754）だけを claim する。
            if run.runtime_id != BUILTIN_RUNTIME_ID or run.status != RunStatus.QUEUED:
                continue
            lease = run.metadata.get("_runtime_dispatch_lease")
            if isinstance(lease, dict):
                expires_at = lease.get("expires_at")
                if isinstance(expires_at, str):
                    try:
                        if datetime.fromisoformat(expires_at) > now:
                            continue
                    except ValueError:
                        pass
            expires = now + timedelta(seconds=max(1, lease_seconds))
            run.metadata["_runtime_dispatch_lease"] = {
                "worker_id": worker_id,
                "claimed_at": now.isoformat(),
                "expires_at": expires.isoformat(),
            }
            run.updated_at = now
            self._append_event(
                run,
                RunEventType.RUNTIME_DISPATCH_CLAIMED,
                "runtime-dispatcher が Run を claim しました。",
                {"worker_id": worker_id, "lease_expires_at": expires.isoformat()},
            )
            return run
        return None

    def persist_control_plane_state(self) -> None:
        with self._lock:
            self._persist_locked()

    def events_for_run(self, run_id: str) -> list[RunEvent]:
        return self.get_run(run_id).events

    def list_artifacts(self, run_id: str) -> list[Artifact]:
        with self._lock:
            run = self._require_run(run_id)
            return [artifact.model_copy(deep=True) for artifact in run.artifacts]

    def get_artifact(self, run_id: str, artifact_id: str) -> Artifact:
        with self._lock:
            run = self._require_run(run_id)
            for artifact in run.artifacts:
                if artifact.id == artifact_id:
                    return _hydrate_artifact_content(artifact)
            raise KeyError(artifact_id)

    def export_snapshot(self) -> AgentRuntimeSnapshot:
        with self._lock:
            return self._export_snapshot_locked()

    def validate_snapshot(self, snapshot: AgentRuntimeSnapshot) -> AgentRuntimeSnapshotValidation:
        return _validate_snapshot(snapshot)

    def replace_snapshot(self, snapshot: AgentRuntimeSnapshot) -> AgentRuntimeSnapshot:
        validation = _validate_snapshot(snapshot)
        if not validation.valid:
            raise ValueError("; ".join(validation.errors))
        with self._condition:
            self._replace_state_locked(snapshot)
            self._persist_locked()
            self._condition.notify_all()
            return self._export_snapshot_locked()

    def iter_events(
        self,
        run_id: str,
        *,
        after_event_id: str | None = None,
        follow: bool = False,
        idle_timeout_seconds: float = 15.0,
    ) -> Iterator[RunEvent | None]:
        with self._condition:
            run = self._runs.get(run_id)
            if run is None:
                raise KeyError(run_id)
            next_index = _event_index_after(run.events, after_event_id)

        while True:
            with self._condition:
                run = self._require_run(run_id)
                if next_index < len(run.events):
                    event = run.events[next_index].model_copy(deep=True)
                    next_index += 1
                else:
                    if not follow or _is_terminal(run.status):
                        return
                    self._condition.wait(timeout=idle_timeout_seconds)
                    if next_index >= len(run.events):
                        event = None
                    else:
                        continue
            yield event

    def cancel_run(self, run_id: str) -> RunState:
        with self._lock:
            run = self._require_run(run_id)
            cancelled_approval_ids: list[str] = []
            run.status = RunStatus.CANCELLED
            run.updated_at = _now()
            run.pending_tool_calls.clear()
            for step in run.steps:
                if step.status in {
                    StepStatus.PENDING,
                    StepStatus.RUNNING,
                    StepStatus.WAITING_APPROVAL,
                }:
                    step.status = StepStatus.CANCELLED
                    step.completed_at = _now()
            for approval in run.approvals:
                if approval.status == ApprovalStatus.PENDING:
                    approval.status = ApprovalStatus.CANCELLED
                    approval.decided_by = "system"
                    approval.decided_at = _now()
                    cancelled_approval_ids.append(approval.id)
            self._append_event(
                run,
                RunEventType.RUN_CANCELLED,
                "実行をキャンセルしました。",
                {"cancelled_approval_ids": cancelled_approval_ids},
            )
            self._persist_locked()
            return run.model_copy(deep=True)

    def decide_approval(self, approval_id: str, request: ApprovalDecisionRequest) -> RunState:
        with self._lock:
            approval = self._approvals.get(approval_id)
            if approval is None:
                raise KeyError(approval_id)
            run = self._require_run(approval.run_id)
            step = self._require_step(run, approval.step_id)
            if _is_terminal(run.status):
                if approval.status == ApprovalStatus.PENDING:
                    approval.status = ApprovalStatus.CANCELLED
                    approval.decided_by = request.decided_by
                    approval.decided_at = _now()
                    self._append_event(
                        run,
                        RunEventType.APPROVAL_DECIDED,
                        "終了済み実行のためツール承認を無効化しました。",
                        {
                            "approval_id": approval.id,
                            "approved": False,
                            "comment": request.comment,
                            "tool_name": step.tool_call.name if step.tool_call else step.kind,
                            "run_status": run.status.value,
                        },
                    )
                    self._persist_locked()
                return run.model_copy(deep=True)
            if approval.status != ApprovalStatus.PENDING:
                return run.model_copy(deep=True)

            approval.status = (
                ApprovalStatus.APPROVED if request.approved else ApprovalStatus.REJECTED
            )
            approval.decided_by = request.decided_by
            approval.decided_at = _now()
            self._append_event(
                run,
                RunEventType.APPROVAL_DECIDED,
                "ツール実行を承認しました。" if request.approved else "ツール実行を拒否しました。",
                {
                    "approval_id": approval.id,
                    "approved": request.approved,
                    "comment": request.comment,
                    "tool_name": step.tool_call.name if step.tool_call else step.kind,
                },
            )
            if run.runtime_id == BUILTIN_RUNTIME_ID:
                # 組み込み Runtime（#754）は SDK が承認済みのツールを再開時に実行する。
                # ここでは決定だけを記録し、承認待ちが残らなければ再開を待つ状態（queued）に
                # する（再開は router が起動する）。
                if request.approved:
                    step.status = StepStatus.PENDING
                else:
                    step.status = StepStatus.CANCELLED
                    step.completed_at = _now()
                if _pending_approval_count(run) == 0:
                    run.status = RunStatus.QUEUED
                    run.updated_at = _now()
                self._persist_locked()
                return run.model_copy(deep=True)
            # 旧エンジンの Run（#756 で実行を削除）は再開しない。決定だけを記録し、step を閉じる。
            step.status = StepStatus.CANCELLED
            step.completed_at = _now()
            self._persist_locked()
            return run.model_copy(deep=True)

    # ---- 組み込み Runtime（#754。実行は builtin_runtime、記録はここ） ----

    def create_builtin_run(
        self, request: RunCreateRequest, *, created_by_user_uuid: str | None = None
    ) -> RunState:
        """組み込み Runtime の Run を投入する（実行は builtin_runtime.execute_run）。"""
        with self._lock:
            agent = self._agents.get(request.agent_id)
            if agent is None:
                raise KeyError(request.agent_id)
            if not agent.enabled:
                raise ValueError("agent disabled")
            if not request.draft and agent.published() is None:
                raise AgentNotPublishedError(request.agent_id)
            # 使う版（#770）。下書きで試すときは "draft"。
            agent_version: int | str = (
                "draft" if request.draft else agent.published_version or "draft"
            )
            if request.thread_id is not None:
                # 続ける会話は、同じ利用者・同じ Agent のものだけ（他人の会話へ書き込ませない）。
                self._require_thread_locked(
                    request.thread_id, user_uuid=created_by_user_uuid, agent_id=request.agent_id
                )
            run = RunState(
                id=f"run_{uuid4().hex}",
                goal=request.goal,
                agent_id=request.agent_id,
                runtime_id=BUILTIN_RUNTIME_ID,
                created_by_user_uuid=created_by_user_uuid,
                thread_id=request.thread_id or f"thread_{uuid4().hex}",
                status=RunStatus.QUEUED,
                metadata={**request.metadata, "agent_version": agent_version},
            )
            self._runs[run.id] = run
            self._append_event(
                run,
                RunEventType.RUN_CREATED,
                "実行を作成しました。",
                {"agent_id": request.agent_id, "runtime_id": BUILTIN_RUNTIME_ID},
            )
            self._persist_locked()
            return run.model_copy(deep=True)

    def _thread_runs_locked(self, thread_id: str) -> list[RunState]:
        return sorted(
            (run for run in self._runs.values() if run.thread_id == thread_id),
            key=lambda run: run.created_at,
        )

    def _require_thread_locked(
        self, thread_id: str, *, user_uuid: str | None, agent_id: str | None = None
    ) -> list[RunState]:
        runs = self._thread_runs_locked(thread_id)
        if (
            not runs
            or runs[0].created_by_user_uuid != user_uuid
            or (agent_id is not None and runs[0].agent_id != agent_id)
        ):
            raise ThreadNotFoundError(thread_id)
        return runs

    def thread_history(self, run_id: str, *, limit: int) -> list[tuple[str, str]]:
        """同じ会話の前の往復（質問と回答。完了したものだけ、古い順、最大 `limit` 往復）。"""
        with self._lock:
            run = self._require_run(run_id)
            if run.thread_id is None:
                return []
            turns = [
                (previous.goal, answer)
                for previous in self._thread_runs_locked(run.thread_id)
                if previous.id != run.id
                and previous.created_at <= run.created_at
                and previous.status == RunStatus.COMPLETED
                and (answer := run_answer_text(previous)) is not None
            ]
            return turns[-limit:] if limit > 0 else []

    def list_threads(
        self, *, user_uuid: str | None, agent_id: str | None = None
    ) -> list[ThreadSummary]:
        """利用者の会話の一覧（新しい順）。会話は作った利用者だけが見る（チャットは個人の作業）。"""
        with self._lock:
            grouped: dict[str, list[RunState]] = {}
            for run in self._runs.values():
                if run.thread_id is None or run.created_by_user_uuid != user_uuid:
                    continue
                if agent_id is not None and run.agent_id != agent_id:
                    continue
                grouped.setdefault(run.thread_id, []).append(run)
            summaries = []
            for thread_id, runs in grouped.items():
                runs.sort(key=lambda item: item.created_at)
                summaries.append(
                    ThreadSummary(
                        thread_id=thread_id,
                        agent_id=runs[0].agent_id,
                        title=runs[0].goal.strip().splitlines()[0][:80],
                        run_count=len(runs),
                        last_status=runs[-1].status,
                        created_at=runs[0].created_at,
                        updated_at=max(run.updated_at for run in runs),
                    )
                )
            return sorted(summaries, key=lambda item: item.updated_at, reverse=True)

    def get_thread(self, thread_id: str, *, user_uuid: str | None) -> ThreadData:
        with self._lock:
            runs = self._require_thread_locked(thread_id, user_uuid=user_uuid)
            return ThreadData(
                thread_id=thread_id,
                agent_id=runs[0].agent_id,
                runs=[run.model_copy(deep=True) for run in runs],
            )

    def begin_builtin_run(self, run_id: str) -> tuple[RunState, AgentProfile] | None:
        """実行を始める（取消済み・終了済みなら None）。"""
        with self._lock:
            run = self._require_run(run_id)
            agent = self._agents.get(run.agent_id)
            if _is_terminal(run.status) or run.status == RunStatus.WAITING_APPROVAL:
                return None
            if agent is None:
                self._fail_builtin_locked(
                    run, "runtime.agent_not_found", "業務 Agent が見つかりません。"
                )
                return None
            self._set_builtin_running_locked(run, "実行を開始しました。")
            return run.model_copy(deep=True), _agent_for_run(agent, run)

    def begin_builtin_resume(
        self, run_id: str
    ) -> tuple[RunState, AgentProfile, str, dict[str, bool]] | None:
        """承認の決定を反映して再開する（承認待ちが残る・状態が無いときは None）。"""
        with self._lock:
            run = self._require_run(run_id)
            agent = self._agents.get(run.agent_id)
            state_text = run.metadata.get(_BUILTIN_STATE_KEY)
            if _is_terminal(run.status) or _pending_approval_count(run) > 0:
                return None
            if agent is None or not isinstance(state_text, str):
                self._fail_builtin_locked(
                    run, "runtime.resume_state_missing", "再開に必要な実行の状態がありません。"
                )
                return None
            decisions = {
                approval.tool_call.trace_id or "": approval.status == ApprovalStatus.APPROVED
                for approval in run.approvals
                if approval.tool_call.trace_id
                and approval.status in {ApprovalStatus.APPROVED, ApprovalStatus.REJECTED}
            }
            run.metadata.pop(_BUILTIN_STATE_KEY, None)
            self._set_builtin_running_locked(run, "承認の決定を反映して実行を再開しました。")
            return run.model_copy(deep=True), _agent_for_run(agent, run), state_text, decisions

    def start_builtin_tool_step(
        self, run_id: str, call: ToolCall
    ) -> tuple[str, ToolInvocationContext]:
        """ツールの step を始める（承認済みの step があれば、それを実行中にする）。"""
        with self._lock:
            run = self._require_run(run_id)
            step = next(
                (
                    item
                    for item in run.steps
                    if call.trace_id
                    and item.tool_call is not None
                    and item.tool_call.trace_id == call.trace_id
                    and item.status in {StepStatus.PENDING, StepStatus.WAITING_APPROVAL}
                ),
                None,
            )
            if step is None:
                step = RunStep(run_id=run_id, tool_call=call)
                run.steps.append(step)
            step.status = StepStatus.RUNNING
            step.started_at = _now()
            self._append_event(
                run,
                RunEventType.STEP_STARTED,
                f"ツール {call.name} を開始しました。",
                {"step_id": step.id, "tool_name": call.name},
            )
            context = self._tool_invocation_context_locked(run, call, approval_id=step.approval_id)
            self._persist_locked()
            return step.id, context

    def finish_builtin_tool_step(self, run_id: str, step_id: str, result: ToolResult) -> None:
        """ツールの結果を step・イベント・成果物に記録する。

        失敗しても Run は続ける（結果を受け取ったモデルが判断する）。
        """
        with self._lock:
            run = self._require_run(run_id)
            step = self._require_step(run, step_id)
            step.tool_result = result
            step.completed_at = _now()
            call_name = step.tool_call.name if step.tool_call else step.kind
            if result.success:
                step.status = StepStatus.COMPLETED
                self._record_tool_success_artifacts(run, step, result)
                self._append_event(
                    run,
                    RunEventType.TOOL_COMPLETED,
                    f"ツール {call_name} が完了しました。",
                    {
                        "step_id": step.id,
                        "tool_name": call_name,
                        "output": result.output,
                        "duration_ms": result.duration_ms,
                        "guardrail_warnings": result.guardrail_warnings,
                        "audit_metadata": result.audit_metadata,
                    },
                )
                self._append_guardrail_events(run, step, result)
            else:
                step.status = StepStatus.FAILED
                self._append_event(
                    run,
                    RunEventType.TOOL_FAILED,
                    f"ツール {call_name} が失敗しました。",
                    {
                        "step_id": step.id,
                        "tool_name": call_name,
                        "error": result.error,
                        "error_code": result.error_code,
                        "error_details": result.error_details,
                        "duration_ms": result.duration_ms,
                        "audit_metadata": result.audit_metadata,
                    },
                )
            run.updated_at = _now()
            self._persist_locked()

    def request_builtin_approvals(
        self, run_id: str, calls: Sequence[ToolCall], *, state: str
    ) -> RunState:
        """承認が必要なツールで中断した Run を承認待ちにし、再開に使う状態を保存する。"""
        with self._lock:
            run = self._require_run(run_id)
            if _is_terminal(run.status):
                return run.model_copy(deep=True)
            for call in calls:
                step = RunStep(
                    run_id=run_id,
                    status=StepStatus.WAITING_APPROVAL,
                    tool_call=call,
                    started_at=_now(),
                )
                approval = ApprovalRequest(
                    run_id=run_id,
                    step_id=step.id,
                    tool_call=call,
                    reason=f"{call.name} は承認が必要です。",
                )
                step.approval_id = approval.id
                run.steps.append(step)
                run.approvals.append(approval)
                self._approvals[approval.id] = approval
                self._append_event(
                    run,
                    RunEventType.TOOL_APPROVAL_REQUIRED,
                    f"ツール {call.name} は承認待ちです。",
                    {
                        "approval_id": approval.id,
                        "step_id": step.id,
                        "tool_name": call.name,
                        "policy_decision": "ask",
                    },
                )
            run.metadata[_BUILTIN_STATE_KEY] = state
            run.status = RunStatus.WAITING_APPROVAL
            run.updated_at = _now()
            self._persist_locked()
            return run.model_copy(deep=True)

    def complete_builtin_run(self, run_id: str, answer: str) -> RunState:
        """モデルの最終回答を成果物に残して完了にする。"""
        with self._lock:
            run = self._require_run(run_id)
            if _is_terminal(run.status):
                return run.model_copy(deep=True)
            artifact = Artifact(name="回答", kind="answer", content={"text": answer})
            run.artifacts.append(artifact)
            self._append_event(
                run,
                RunEventType.ARTIFACT_CREATED,
                "回答を保存しました。",
                {"artifact_id": artifact.id, "kind": artifact.kind},
            )
            run.status = RunStatus.COMPLETED
            run.updated_at = _now()
            self._append_event(run, RunEventType.RUN_COMPLETED, "実行を完了しました。")
            self._persist_locked()
            return run.model_copy(deep=True)

    def fail_builtin_run(self, run_id: str, *, code: str, detail: str) -> RunState:
        with self._lock:
            run = self._require_run(run_id)
            if not _is_terminal(run.status):
                self._fail_builtin_locked(run, code, detail)
            return run.model_copy(deep=True)

    def set_run_feedback(
        self, run_id: str, feedback: RunFeedback, *, admin: bool = False
    ) -> RunState:
        """回答への評価を保存する（上書き。#774）。完了していない Run は RunNotRatableError。

        `admin` は管理者の評価（本人の評価とは別の項目）。
        """
        with self._lock:
            run = self._require_run(run_id)
            if run.status != RunStatus.COMPLETED or run_answer_text(run) is None:
                raise RunNotRatableError(run_id)
            if admin:
                run.admin_review = feedback
            else:
                run.feedback = feedback
            self._persist_locked()
            return run.model_copy(deep=True)

    def record_builtin_dry_run_steps(self, run_id: str, calls: Sequence[ToolCall]) -> None:
        """評価の Run で実行しなかった（承認が要る）ツールの呼び出しを step に残す（#776）。

        モデルがどのツールをどの引数で呼ぼうとしたかを、品質評価のツールの選択の判定に使う。
        """
        now = _now()
        with self._lock:
            run = self._require_run(run_id)
            for call in calls:
                run.steps.append(
                    RunStep(
                        run_id=run.id,
                        status=StepStatus.CANCELLED,
                        tool_call=call,
                        tool_result=ToolResult(
                            name=call.name,
                            success=False,
                            error=EVALUATION_DRY_RUN_MESSAGE,
                            error_code="evaluation.dry_run",
                            started_at=now,
                            completed_at=now,
                        ),
                        started_at=now,
                        completed_at=now,
                    )
                )
                self._append_event(
                    run,
                    RunEventType.RUNTIME_EVENT,
                    f"評価中のため、承認が要るツール {call.name} を実行しませんでした。",
                    {"tool_name": call.name, "evaluation_dry_run": True},
                )
            run.updated_at = now
            self._persist_locked()

    def record_builtin_usage(self, run_id: str, usage: RunUsage) -> None:
        """モデルの利用量（再開を含めた累計）を Run に記録する（#772）。"""
        with self._lock:
            run = self._runs.get(run_id)
            if run is None:
                return
            run.usage = usage
            self._persist_locked()

    def note_builtin_warning(self, run_id: str, message: str) -> None:
        """実行は続けるが利用者に伝えたいこと（取得できなかった MCP 接続など）をイベントに残す。"""
        with self._lock:
            run = self._require_run(run_id)
            self._append_event(run, RunEventType.RUNTIME_EVENT, message, {"severity": "warning"})
            self._persist_locked()

    def _set_builtin_running_locked(self, run: RunState, message: str) -> None:
        # 実行を始めたら dispatcher の lease は要らない（running は claim しない）。
        # 残すと、承認の決定で queued に戻った Run を lease の期限まで claim できない。
        run.metadata.pop("_runtime_dispatch_lease", None)
        run.status = RunStatus.RUNNING
        run.updated_at = _now()
        self._append_event(
            run,
            RunEventType.RUN_STATUS_CHANGED,
            message,
            {"status": RunStatus.RUNNING.value, "runtime_id": BUILTIN_RUNTIME_ID},
        )
        self._persist_locked()

    def _fail_builtin_locked(self, run: RunState, code: str, detail: str) -> None:
        run.status = RunStatus.FAILED
        run.updated_at = _now()
        run.metadata.pop(_BUILTIN_STATE_KEY, None)
        self._append_event(
            run,
            RunEventType.RUNTIME_FAILED,
            detail,
            {"error_code": code, "runtime_id": BUILTIN_RUNTIME_ID},
        )
        self._persist_locked()

    def list_agents(self) -> list[AgentProfile]:
        with self._lock:
            return [agent.model_copy(deep=True) for agent in self._sorted_agents_locked()]

    def create_agent(self, agent: AgentProfile) -> AgentProfile:
        with self._lock:
            if agent.id in self._agents:
                raise ValueError("agent already exists")
            self._validate_agent_skills(agent.skill_ids)
            self._validate_agent_tools(agent.tool_names)
            now = _now()
            agent.created_at = now
            agent.updated_at = now
            self._agents[agent.id] = _ensure_versioned(agent)
            self._persist_locked()
            return self._agents[agent.id].model_copy(deep=True)

    def publish_agent(
        self, agent_id: str, *, note: str = "", published_by: str | None = None
    ) -> AgentProfile:
        """下書きを新しい版として公開する（#770）。"""
        with self._lock:
            agent = self._agents.get(agent_id)
            if agent is None:
                raise KeyError(agent_id)
            self._validate_agent_skills(agent.skill_ids)
            version = max((item.version for item in agent.versions), default=0) + 1
            agent.versions.append(
                AgentVersion(
                    version=version,
                    note=note.strip(),
                    published_by=published_by,
                    **{field: getattr(agent, field) for field in AGENT_VERSIONED_FIELDS},
                )
            )
            agent.published_version = version
            agent.versioned = True
            agent.updated_at = _now()
            self._persist_locked()
            return agent.model_copy(deep=True)

    def restore_agent_version(
        self, agent_id: str, version: int, *, published_by: str | None = None
    ) -> AgentProfile:
        """前の版を公開し直し、下書きもその内容にする（ロールバック。#770）。"""
        with self._lock:
            agent = self._agents.get(agent_id)
            if agent is None:
                raise KeyError(agent_id)
            target = next((item for item in agent.versions if item.version == version), None)
            if target is None:
                raise KeyError(f"{agent_id}@{version}")
            for field in AGENT_VERSIONED_FIELDS:
                setattr(agent, field, deepcopy(getattr(target, field)))
            agent.published_version = version
            agent.updated_at = _now()
            self._persist_locked()
            return agent.model_copy(deep=True)

    def patch_agent(self, agent_id: str, patch: AgentProfilePatch) -> AgentProfile:
        with self._lock:
            agent = self._agents.get(agent_id)
            if agent is None:
                raise KeyError(agent_id)
            data = patch.model_dump(exclude_unset=True)
            tool_names = data.get("tool_names")
            if isinstance(tool_names, list):
                self._validate_agent_tools(tool_names)
            skill_ids = data.get("skill_ids")
            if isinstance(skill_ids, list):
                self._validate_agent_skills(skill_ids)
                data["migration_required"] = False
            for key, value in data.items():
                setattr(agent, key, value)
            agent.updated_at = _now()
            self._persist_locked()
            return agent.model_copy(deep=True)

    def delete_agent(self, agent_id: str) -> None:
        with self._lock:
            if agent_id == "default":
                raise ValueError("default agent cannot be removed")
            if agent_id not in self._agents:
                raise KeyError(agent_id)
            del self._agents[agent_id]
            self._persist_locked()

    def set_plugin_agents(self, source: str, agents: list[AgentProfile]) -> None:
        """指定 source(例: plugin:<id>)の agent を一括置換する。default は保護。"""
        with self._lock:
            for agent_id in [
                aid
                for aid, agent in self._agents.items()
                if agent.source == source and aid != "default"
            ]:
                del self._agents[agent_id]
            now = _now()
            for agent in agents:
                if agent.id == "default":
                    continue
                self._validate_agent_skills(agent.skill_ids)
                self._validate_agent_tools(agent.tool_names)
                self._agents[agent.id] = agent.model_copy(
                    deep=True, update={"source": source, "created_at": now, "updated_at": now}
                )
            self._persist_locked()

    def remove_agents_by_source(self, source: str) -> None:
        with self._lock:
            for agent_id in [
                aid
                for aid, agent in self._agents.items()
                if agent.source == source and aid != "default"
            ]:
                del self._agents[agent_id]
            self._persist_locked()

    def _sorted_runs_locked(self) -> list[RunState]:
        return sorted(self._runs.values(), key=lambda run: run.created_at, reverse=True)

    def _sorted_agents_locked(self) -> list[AgentProfile]:
        return sorted(self._agents.values(), key=lambda agent: agent.created_at)

    def _export_snapshot_locked(self) -> AgentRuntimeSnapshot:
        return AgentRuntimeSnapshot(
            runs=[run.model_copy(deep=True) for run in self._sorted_runs_locked()],
            agents=[agent.model_copy(deep=True) for agent in self._sorted_agents_locked()],
        )

    def _replace_state_locked(self, snapshot: AgentRuntimeSnapshot) -> None:
        self._runs = {run.id: run.model_copy(deep=True) for run in snapshot.runs}
        self._agents = {
            agent.id: _ensure_versioned(_migrate_legacy_agent(agent)) for agent in snapshot.agents
        }
        if "default" not in self._agents:
            self._agents["default"] = _ensure_versioned(_default_agent())
        self._approvals = {
            approval.id: approval for run in self._runs.values() for approval in run.approvals
        }

    def _load_snapshot_from_disk(self) -> None:
        if self._snapshot_path is None:
            return
        try:
            snapshot = AgentRuntimeSnapshot.model_validate_json(
                self._snapshot_path.read_text(encoding="utf-8")
            )
        except ValueError as exc:
            raise RuntimeError(f"agent runtime snapshot is invalid: {self._snapshot_path}") from exc
        with self._lock:
            self._replace_state_locked(snapshot)

    def _persist_locked(self) -> None:
        if self._snapshot_path is None:
            return
        self._snapshot_path.parent.mkdir(parents=True, exist_ok=True)
        temp_path = self._snapshot_path.with_name(f".{self._snapshot_path.name}.tmp")
        temp_path.write_text(
            self._export_snapshot_locked().model_dump_json(indent=2),
            encoding="utf-8",
        )
        os.replace(temp_path, self._snapshot_path)

    def _record_tool_success_artifacts(
        self,
        run: RunState,
        step: RunStep,
        result: ToolResult,
    ) -> None:
        if step.tool_call is None or result.output is None:
            return
        # MCP 接続のツール（`<接続>__<ツール>`。#757）は、ツールの部分で成果物の種類を決める。
        artifact_kind_by_tool = {
            "rag_search": "rag_evidence",
            "nl2sql_query": "structured_table",
            "nl2sql_get_job": "structured_table",
        }
        kind = artifact_kind_by_tool.get(mcp_base_tool_name(step.tool_call.name))
        if kind is None:
            return
        artifact = Artifact(
            name=f"{step.tool_call.name}:{step.id}",
            kind=kind,
            content=result.output,
        )
        artifact = _maybe_externalize_artifact_content(run.id, artifact)
        run.artifacts.append(artifact)
        if artifact.content_ref is not None:
            result.output = artifact.content
        self._append_event(
            run,
            RunEventType.ARTIFACT_CREATED,
            "ツール結果 artifact を保存しました。",
            {"artifact_id": artifact.id, "kind": artifact.kind, "step_id": step.id},
        )

    def _tool_invocation_context_locked(
        self,
        run: RunState,
        call: ToolCall,
        *,
        approval_id: str | None = None,
    ) -> ToolInvocationContext:
        # 承認後の再実行もこの context を使うため、承認者ではなく Run の利用者として呼ぶ（#233）。
        return ToolInvocationContext(
            approval_id=approval_id,
            trace_id=call.trace_id,
            agent_id=run.agent_id,
            run_id=run.id,
            user_uuid=run.created_by_user_uuid,
        )

    def _append_guardrail_events(
        self,
        run: RunState,
        step: RunStep,
        result: ToolResult,
    ) -> None:
        if not result.guardrail_warnings:
            return
        self._append_event(
            run,
            RunEventType.TOOL_GUARDRAIL_WARNING,
            "ツール出力の安全検査で警告を検出しました。",
            {
                "step_id": step.id,
                "tool_name": step.tool_call.name if step.tool_call else step.kind,
                "warnings": result.guardrail_warnings,
            },
        )

    def _append_event(
        self,
        run: RunState,
        event_type: RunEventType,
        message: str,
        payload: JsonObject | None = None,
    ) -> None:
        event = RunEvent(
            run_id=run.id,
            type=event_type,
            message=message,
            payload=payload or {},
        )
        run.events.append(event)
        record_runtime_event(event.type.value, {"run_id": run.id, **event.payload})
        self._condition.notify_all()

    def _require_run(self, run_id: str) -> RunState:
        run = self._runs.get(run_id)
        if run is None:
            raise KeyError(run_id)
        return run

    @staticmethod
    def _validate_agent_tools(tool_names: list[str]) -> None:
        registered_tools = set(tool_registry.names())
        unknown_tools = sorted({name for name in tool_names if name not in registered_tools})
        if unknown_tools:
            raise ValueError(f"unknown tool: {', '.join(unknown_tools)}")

    @staticmethod
    def _validate_agent_skills(skill_ids: list[str]) -> None:
        from app.features.agent.skills import skill_registry

        unknown = sorted(
            {skill_id for skill_id in skill_ids if skill_registry.get(skill_id) is None}
        )
        if unknown:
            raise ValueError(f"unknown skill: {', '.join(unknown)}")

    @staticmethod
    def _require_step(run: RunState, step_id: str) -> RunStep:
        for step in run.steps:
            if step.id == step_id:
                return step
        raise KeyError(step_id)


class AgentRuntimeOracleCheckpointRepository(AgentRuntimeRepository):
    """Oracle に Runtime snapshot checkpoint を保存する repository。

    第一段階では既存の安全な in-process 状態機を再利用し、状態変更ごとに
    Oracle CLOB へ checkpoint を保存する。完全な event / step 正規化テーブルは
    この checkpoint を移行元にして後続で追加する。
    """

    def __init__(
        self,
        *,
        checkpoint_key: str = "default",
        connect_factory: OracleConnectFactory | None = None,
    ) -> None:
        # 接続は共通の PLATFORM_ORACLE_*、テーブルはシステムテーブルが作る（#764）。
        self._oracle_table_name = RUNTIME_CHECKPOINT_TABLE
        self._oracle_checkpoint_key = checkpoint_key
        self._oracle_connect_factory = connect_factory or connect_platform_oracle
        super().__init__(snapshot_path=None)
        self._load_snapshot_from_oracle()

    def _connect_oracle(self) -> Any:
        return self._oracle_connect_factory()

    def _load_snapshot_from_oracle(self) -> None:
        query = (
            f"SELECT snapshot_json FROM {self._oracle_table_name} "
            "WHERE checkpoint_key = :checkpoint_key"
        )
        try:
            with self._connect_oracle() as connection, connection.cursor() as cursor:
                cursor.execute(query, checkpoint_key=self._oracle_checkpoint_key)
                row = cursor.fetchone()
                # CLOB は接続を閉じる前に読む（閉じた後に読むと DPY-1001。#765）。
                snapshot_json = _oracle_lob_to_text(row[0]) if row is not None else None
        except Exception as exc:
            if not _is_oracle_table_missing_error(exc):
                raise
            # システムテーブルを作る前でも起動できるようにする（画面は作成へ案内する）。
            logger.warning(
                "agent_runtime_tables_missing",
                extra={"table": self._oracle_table_name},
            )
            return
        if snapshot_json is None:
            return
        try:
            snapshot = AgentRuntimeSnapshot.model_validate_json(snapshot_json)
        except ValueError as exc:
            raise RuntimeError("agent runtime Oracle checkpoint is invalid") from exc
        validation = _validate_snapshot(snapshot)
        if not validation.valid:
            raise RuntimeError(
                "agent runtime Oracle checkpoint validation failed: " + "; ".join(validation.errors)
            )
        with self._lock:
            self._replace_state_locked(snapshot)

    def _persist_locked(self) -> None:
        snapshot_json = self._export_snapshot_locked().model_dump_json()
        with self._connect_oracle() as connection, connection.cursor() as cursor:
            self._write_checkpoint_cursor(cursor, snapshot_json)
            connection.commit()

    def claim_control_plane_run(self, worker_id: str, *, lease_seconds: int) -> RunState | None:
        """Oracle row lock 下で最新 checkpoint を読み、Run lease を原子的に取得する。"""
        query = (
            f"SELECT snapshot_json FROM {self._oracle_table_name} "
            "WHERE checkpoint_key = :checkpoint_key FOR UPDATE"
        )
        with self._connect_oracle() as connection, connection.cursor() as cursor:
            cursor.execute(query, checkpoint_key=self._oracle_checkpoint_key)
            row = cursor.fetchone()
            if row is None:
                connection.commit()
                return None
            snapshot = AgentRuntimeSnapshot.model_validate_json(_oracle_lob_to_text(row[0]))
            with self._lock:
                self._replace_state_locked(snapshot)
                run = self._claim_control_plane_run_locked(worker_id, lease_seconds=lease_seconds)
                if run is None:
                    connection.commit()
                    return None
                snapshot_json = self._export_snapshot_locked().model_dump_json()
            self._write_checkpoint_cursor(cursor, snapshot_json)
            connection.commit()
            return run.model_copy(deep=True)

    def _write_checkpoint_cursor(self, cursor: Any, snapshot_json: str) -> None:
        statement = f"""
        MERGE INTO {self._oracle_table_name} target
        USING (
            SELECT
                :checkpoint_key AS checkpoint_key,
                :snapshot_json AS snapshot_json
            FROM dual
        ) source
        ON (target.checkpoint_key = source.checkpoint_key)
        WHEN MATCHED THEN UPDATE SET
            target.snapshot_json = source.snapshot_json,
            target.updated_at = SYSTIMESTAMP
        WHEN NOT MATCHED THEN INSERT (
            checkpoint_key,
            snapshot_json,
            updated_at
        ) VALUES (
            source.checkpoint_key,
            source.snapshot_json,
            SYSTIMESTAMP
        )
        """
        cursor.execute(
            statement,
            checkpoint_key=self._oracle_checkpoint_key,
            snapshot_json=snapshot_json,
        )


class AgentRuntimeOracleNormalizedRepository(AgentRuntimeOracleCheckpointRepository):
    """Oracle checkpoint と正規化 projection tables を同時に更新する repository。"""

    def __init__(
        self,
        *,
        checkpoint_key: str = "default",
        projection_retention_days: int = 0,
        projection_write_mode: str = "replace",
        connect_factory: OracleConnectFactory | None = None,
    ) -> None:
        self._oracle_projection_prefix = RUNTIME_PROJECTION_PREFIX
        self._oracle_projection_tables = _oracle_projection_tables(self._oracle_projection_prefix)
        self._oracle_projection_retention_days = max(0, projection_retention_days)
        self._oracle_projection_write_mode = projection_write_mode.strip().lower() or "replace"
        if self._oracle_projection_write_mode not in {"replace", "incremental"}:
            raise ValueError("projection_write_mode must be replace or incremental")
        super().__init__(checkpoint_key=checkpoint_key, connect_factory=connect_factory)

    def _persist_locked(self) -> None:
        snapshot = self._export_snapshot_locked()
        with self._connect_oracle() as connection, connection.cursor() as cursor:
            self._write_checkpoint_cursor(cursor, snapshot.model_dump_json())
            if self._oracle_projection_write_mode == "incremental":
                self._upsert_projection_cursor(cursor, snapshot)
            else:
                self._replace_projection_cursor(cursor, snapshot)
            self._apply_projection_retention_cursor(cursor)
            connection.commit()

    def list_tool_call_audit_projection(
        self,
        *,
        run_id: str | None = None,
        tool_name: str | None = None,
        status: str | None = None,
        approval_status: str | None = None,
        error_code: str | None = None,
        has_guardrail_warnings: bool | None = None,
        offset: int = 0,
        limit: int = 100,
    ) -> RuntimeToolCallAuditData:
        """正規化 projection tables からツール監査ログを読み出す。"""
        with self._connect_oracle() as connection, connection.cursor() as cursor:
            artifact_ids_by_step = self._projection_artifact_ids_by_step(cursor)
            total = self._projection_tool_call_count(
                cursor,
                run_id=run_id,
                tool_name=tool_name,
                status=status,
                approval_status=approval_status,
                error_code=error_code,
                has_guardrail_warnings=has_guardrail_warnings,
            )
            rows = self._projection_tool_call_rows(
                cursor,
                run_id=run_id,
                tool_name=tool_name,
                status=status,
                approval_status=approval_status,
                error_code=error_code,
                has_guardrail_warnings=has_guardrail_warnings,
                offset=offset,
                limit=limit,
            )
            # CLOB の列は接続を閉じる前に読む（閉じた後に読むと DPY-1001。#765）。
            records: list[RuntimeToolCallAuditRecord] = []
            for row in rows:
                record = _runtime_audit_record_from_projection_row(
                    row,
                    artifact_ids_by_step=artifact_ids_by_step,
                )
                if record is None:
                    continue
                records.append(record)

        return RuntimeToolCallAuditData(total=total, offset=offset, limit=limit, records=records)

    def _projection_tool_call_rows(
        self,
        cursor: Any,
        *,
        run_id: str | None,
        tool_name: str | None,
        status: str | None,
        approval_status: str | None,
        error_code: str | None,
        has_guardrail_warnings: bool | None,
        offset: int,
        limit: int,
    ) -> list[tuple[Any, ...]]:
        tables = self._oracle_projection_tables
        where_sql, params = _projection_tool_call_where(
            run_id=run_id,
            tool_name=tool_name,
            status=status,
            approval_status=approval_status,
            error_code=error_code,
            has_guardrail_warnings=has_guardrail_warnings,
        )
        params["offset"] = offset
        params["limit"] = limit
        pagination_sql = "OFFSET :offset ROWS FETCH NEXT :limit ROWS ONLY"
        cursor.execute(
            f"""
            SELECT
                r.run_id,
                r.goal,
                r.status,
                r.agent_id,
                r.metadata_json,
                r.created_at,
                r.updated_at,
                s.step_id,
                s.status,
                s.tool_name,
                s.approval_id,
                s.tool_call_json,
                s.tool_result_json,
                s.started_at,
                s.completed_at,
                a.status AS approval_status
            FROM {tables["runs"]} r
            JOIN {tables["steps"]} s ON s.run_id = r.run_id
            LEFT JOIN {tables["approvals"]} a ON a.approval_id = s.approval_id
            {where_sql}
            ORDER BY r.created_at DESC, s.started_at ASC, s.step_id ASC
            {pagination_sql}
            """,
            **params,
        )
        return list(cursor.fetchall())

    def _projection_tool_call_count(
        self,
        cursor: Any,
        *,
        run_id: str | None,
        tool_name: str | None,
        status: str | None,
        approval_status: str | None,
        error_code: str | None,
        has_guardrail_warnings: bool | None,
    ) -> int:
        tables = self._oracle_projection_tables
        where_sql, params = _projection_tool_call_where(
            run_id=run_id,
            tool_name=tool_name,
            status=status,
            approval_status=approval_status,
            error_code=error_code,
            has_guardrail_warnings=has_guardrail_warnings,
        )
        cursor.execute(
            f"""
            SELECT COUNT(*)
            FROM {tables["runs"]} r
            JOIN {tables["steps"]} s ON s.run_id = r.run_id
            LEFT JOIN {tables["approvals"]} a ON a.approval_id = s.approval_id
            {where_sql}
            """,
            **params,
        )
        row = cursor.fetchone()
        return int(row[0]) if row is not None else 0

    def _projection_artifact_ids_by_step(self, cursor: Any) -> dict[str, list[str]]:
        table = self._oracle_projection_tables["events"]
        cursor.execute(
            f"""
            SELECT
                payload_json
            FROM {table}
            WHERE event_type = :event_type
            """,
            event_type=RunEventType.ARTIFACT_CREATED.value,
        )
        artifact_ids_by_step: dict[str, list[str]] = {}
        for (payload_json,) in cursor.fetchall():
            payload = _json_load_object(payload_json)
            step_id = payload.get("step_id")
            artifact_id = payload.get("artifact_id")
            if isinstance(step_id, str) and isinstance(artifact_id, str):
                artifact_ids_by_step.setdefault(step_id, []).append(artifact_id)
        return artifact_ids_by_step

    def _replace_projection_cursor(
        self,
        cursor: Any,
        snapshot: AgentRuntimeSnapshot,
    ) -> None:
        for table in reversed(list(self._oracle_projection_tables.values())):
            cursor.execute(f"DELETE FROM {table}")
        self._insert_runs(cursor, snapshot.runs)

    def _upsert_projection_cursor(
        self,
        cursor: Any,
        snapshot: AgentRuntimeSnapshot,
    ) -> None:
        self._upsert_runs(cursor, snapshot.runs)

    def _apply_projection_retention_cursor(self, cursor: Any) -> None:
        if self._oracle_projection_retention_days <= 0:
            return
        tables = self._oracle_projection_tables
        params = {"retention_days": self._oracle_projection_retention_days}
        # Delete children first so a future FK-backed schema can reuse the same order.
        for table, column in (
            (tables["artifacts"], "created_at"),
            (tables["events"], "created_at"),
            (tables["approvals"], "created_at"),
            (tables["steps"], "completed_at"),
            (tables["runs"], "created_at"),
        ):
            cursor.execute(
                f"""
                DELETE FROM {table}
                WHERE {column} IS NOT NULL
                  AND {column} < SYSTIMESTAMP - NUMTODSINTERVAL(:retention_days, 'DAY')
                """,
                **params,
            )

    def _insert_runs(self, cursor: Any, runs: list[RunState]) -> None:
        tables = self._oracle_projection_tables
        for run in runs:
            cursor.execute(
                f"""
                INSERT INTO {tables["runs"]} (
                    run_id,
                    agent_id,
                    status,
                    goal,
                    metadata_json,
                    pending_tool_calls_json,
                    created_at,
                    updated_at
                ) VALUES (
                    :run_id,
                    :agent_id,
                    :status,
                    :goal,
                    :metadata_json,
                    :pending_tool_calls_json,
                    :created_at,
                    :updated_at
                )
                """,
                run_id=run.id,
                agent_id=run.agent_id,
                status=run.status.value,
                goal=run.goal,
                metadata_json=_json_dump(run.metadata),
                pending_tool_calls_json=_model_list_json(run.pending_tool_calls),
                created_at=run.created_at,
                updated_at=run.updated_at,
            )
            self._insert_events(cursor, run)
            self._insert_steps(cursor, run)
            self._insert_approvals(cursor, run)
            self._insert_artifacts(cursor, run)

    def _insert_events(self, cursor: Any, run: RunState) -> None:
        table = self._oracle_projection_tables["events"]
        for event in run.events:
            cursor.execute(
                f"""
                INSERT INTO {table} (
                    event_id,
                    run_id,
                    event_type,
                    message,
                    payload_json,
                    created_at
                ) VALUES (
                    :event_id,
                    :run_id,
                    :event_type,
                    :message,
                    :payload_json,
                    :created_at
                )
                """,
                event_id=event.id,
                run_id=event.run_id,
                event_type=event.type.value,
                message=event.message,
                payload_json=_json_dump(event.payload),
                created_at=event.created_at,
            )

    def _insert_steps(self, cursor: Any, run: RunState) -> None:
        table = self._oracle_projection_tables["steps"]
        for step in run.steps:
            cursor.execute(
                f"""
                INSERT INTO {table} (
                    step_id,
                    run_id,
                    kind,
                    status,
                    tool_name,
                    approval_id,
                    tool_call_json,
                    tool_result_json,
                    started_at,
                    completed_at
                ) VALUES (
                    :step_id,
                    :run_id,
                    :kind,
                    :status,
                    :tool_name,
                    :approval_id,
                    :tool_call_json,
                    :tool_result_json,
                    :started_at,
                    :completed_at
                )
                """,
                step_id=step.id,
                run_id=step.run_id,
                kind=step.kind,
                status=step.status.value,
                tool_name=step.tool_call.name if step.tool_call else None,
                approval_id=step.approval_id,
                tool_call_json=step.tool_call.model_dump_json() if step.tool_call else None,
                tool_result_json=step.tool_result.model_dump_json() if step.tool_result else None,
                started_at=step.started_at,
                completed_at=step.completed_at,
            )

    def _insert_approvals(self, cursor: Any, run: RunState) -> None:
        table = self._oracle_projection_tables["approvals"]
        for approval in run.approvals:
            cursor.execute(
                f"""
                INSERT INTO {table} (
                    approval_id,
                    run_id,
                    step_id,
                    tool_name,
                    status,
                    reason,
                    decided_by,
                    decided_at,
                    created_at,
                    tool_call_json
                ) VALUES (
                    :approval_id,
                    :run_id,
                    :step_id,
                    :tool_name,
                    :status,
                    :reason,
                    :decided_by,
                    :decided_at,
                    :created_at,
                    :tool_call_json
                )
                """,
                approval_id=approval.id,
                run_id=approval.run_id,
                step_id=approval.step_id,
                tool_name=approval.tool_call.name,
                status=approval.status.value,
                reason=approval.reason,
                decided_by=approval.decided_by,
                decided_at=approval.decided_at,
                created_at=approval.created_at,
                tool_call_json=approval.tool_call.model_dump_json(),
            )

    def _insert_artifacts(self, cursor: Any, run: RunState) -> None:
        table = self._oracle_projection_tables["artifacts"]
        for artifact in run.artifacts:
            cursor.execute(
                f"""
                INSERT INTO {table} (
                    artifact_id,
                    run_id,
                    name,
                    kind,
                    content_json,
                    created_at
                ) VALUES (
                    :artifact_id,
                    :run_id,
                    :name,
                    :kind,
                    :content_json,
                    :created_at
                )
                """,
                artifact_id=artifact.id,
                run_id=run.id,
                name=artifact.name,
                kind=artifact.kind,
                content_json=_json_dump(artifact.content),
                created_at=artifact.created_at,
            )

    def _upsert_runs(self, cursor: Any, runs: list[RunState]) -> None:
        tables = self._oracle_projection_tables
        for run in runs:
            _merge_projection_row(
                cursor,
                table=tables["runs"],
                key_column="run_id",
                values={
                    "run_id": run.id,
                    "agent_id": run.agent_id,
                    "status": run.status.value,
                    "goal": run.goal,
                    "metadata_json": _json_dump(run.metadata),
                    "pending_tool_calls_json": _model_list_json(run.pending_tool_calls),
                    "created_at": run.created_at,
                    "updated_at": run.updated_at,
                },
            )
            self._upsert_events(cursor, run)
            self._upsert_steps(cursor, run)
            self._upsert_approvals(cursor, run)
            self._upsert_artifacts(cursor, run)

    def _upsert_events(self, cursor: Any, run: RunState) -> None:
        table = self._oracle_projection_tables["events"]
        for event in run.events:
            _merge_projection_row(
                cursor,
                table=table,
                key_column="event_id",
                values={
                    "event_id": event.id,
                    "run_id": event.run_id,
                    "event_type": event.type.value,
                    "message": event.message,
                    "payload_json": _json_dump(event.payload),
                    "created_at": event.created_at,
                },
            )

    def _upsert_steps(self, cursor: Any, run: RunState) -> None:
        table = self._oracle_projection_tables["steps"]
        for step in run.steps:
            _merge_projection_row(
                cursor,
                table=table,
                key_column="step_id",
                values={
                    "step_id": step.id,
                    "run_id": step.run_id,
                    "kind": step.kind,
                    "status": step.status.value,
                    "tool_name": step.tool_call.name if step.tool_call else None,
                    "approval_id": step.approval_id,
                    "tool_call_json": step.tool_call.model_dump_json() if step.tool_call else None,
                    "tool_result_json": (
                        step.tool_result.model_dump_json() if step.tool_result else None
                    ),
                    "started_at": step.started_at,
                    "completed_at": step.completed_at,
                },
            )

    def _upsert_approvals(self, cursor: Any, run: RunState) -> None:
        table = self._oracle_projection_tables["approvals"]
        for approval in run.approvals:
            _merge_projection_row(
                cursor,
                table=table,
                key_column="approval_id",
                values={
                    "approval_id": approval.id,
                    "run_id": approval.run_id,
                    "step_id": approval.step_id,
                    "tool_name": approval.tool_call.name,
                    "status": approval.status.value,
                    "reason": approval.reason,
                    "decided_by": approval.decided_by,
                    "decided_at": approval.decided_at,
                    "created_at": approval.created_at,
                    "tool_call_json": approval.tool_call.model_dump_json(),
                },
            )

    def _upsert_artifacts(self, cursor: Any, run: RunState) -> None:
        table = self._oracle_projection_tables["artifacts"]
        for artifact in run.artifacts:
            _merge_projection_row(
                cursor,
                table=table,
                key_column="artifact_id",
                values={
                    "artifact_id": artifact.id,
                    "run_id": run.id,
                    "name": artifact.name,
                    "kind": artifact.kind,
                    "content_json": _json_dump(artifact.content),
                    "created_at": artifact.created_at,
                },
            )


def _event_index_after(events: list[RunEvent], event_id: str | None) -> int:
    if event_id is None:
        return 0
    for index, event in enumerate(events):
        if event.id == event_id:
            return index + 1
    return 0


def _is_terminal(status: RunStatus) -> bool:
    return status in {RunStatus.COMPLETED, RunStatus.FAILED, RunStatus.CANCELLED}


def _normalize_runtime_status(value: str) -> RunStatus | None:
    normalized = value.strip().lower().replace("-", "_")
    if normalized in {"queued", "pending", "created", "submitted"}:
        return RunStatus.QUEUED
    if normalized in {"running", "in_progress", "streaming", "active", "working"}:
        return RunStatus.RUNNING
    if normalized in {"completed", "complete", "succeeded", "success", "done"}:
        return RunStatus.COMPLETED
    if normalized in {"failed", "failure", "error", "errored"}:
        return RunStatus.FAILED
    if normalized in {"cancelled", "canceled", "stopped", "aborted"}:
        return RunStatus.CANCELLED
    if normalized in {"waiting_approval", "requires_action", "interrupted"}:
        return RunStatus.WAITING_APPROVAL
    return None


_RUNTIME_SECRET_KEY = re.compile(
    r"(?:authorization|cookie|password|passwd|secret|token|api[_-]?key|credential)",
    re.IGNORECASE,
)


def _runtime_event_metadata(payload: JsonObject) -> JsonObject:
    value = _safe_runtime_value(payload)
    return value if isinstance(value, dict) else {}


def _safe_runtime_value(value: object, *, depth: int = 0) -> object:
    if depth >= 5:
        return "[truncated]"
    if value is None or isinstance(value, bool | int | float):
        return value
    if isinstance(value, str):
        return value[:2000]
    if isinstance(value, list | tuple):
        return [_safe_runtime_value(item, depth=depth + 1) for item in value[:50]]
    if isinstance(value, dict):
        result: JsonObject = {}
        for raw_key, item in list(value.items())[:50]:
            key = str(raw_key)[:128]
            if _RUNTIME_SECRET_KEY.search(key):
                result[key] = "[REDACTED]"
            elif key.lower() in {"uri", "url", "href"} and isinstance(item, str):
                result[key] = item.split("?", 1)[0].split("#", 1)[0][:2000]
            else:
                result[key] = _safe_runtime_value(item, depth=depth + 1)
        return result
    return str(value)[:2000]


def _runtime_event_key(payload: JsonObject, cursor: str | None) -> str:
    for key in ("id", "event_id", "eventId", "cursor"):
        value = payload.get(key)
        if isinstance(value, str | int):
            return f"{key}:{value}"
    if cursor:
        return f"cursor:{cursor}"
    canonical = json.dumps(
        _runtime_event_metadata(payload),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return f"sha256:{hashlib.sha256(canonical.encode('utf-8')).hexdigest()}"


def _runtime_artifact(run_id: str, payload: JsonObject, index: int) -> Artifact:
    external_id = str(payload.get("id") or payload.get("artifact_id") or index)
    digest = hashlib.sha256(f"{run_id}:{external_id}".encode()).hexdigest()[:24]
    name = str(payload.get("name") or payload.get("title") or f"Runtime artifact {index + 1}")
    kind = str(payload.get("kind") or payload.get("type") or "runtime_artifact")
    return Artifact(
        id=f"artifact_runtime_{digest}",
        name=name[:256],
        kind=kind[:128],
        content=_runtime_event_metadata(payload),
    )


def builtin_resume_pending(run: RunState) -> bool:
    """承認がすべて決まり、保存した SDK の状態から再開を待つ組み込み Runtime の Run か。"""
    return (
        run.runtime_id == BUILTIN_RUNTIME_ID
        and run.status == RunStatus.QUEUED
        and isinstance(run.metadata.get(_BUILTIN_STATE_KEY), str)
    )


def _pending_approval_count(run: RunState) -> int:
    return sum(1 for approval in run.approvals if approval.status == ApprovalStatus.PENDING)


def _validate_oracle_identifier(identifier: str) -> str:
    normalized = identifier.strip().upper()
    if not re.fullmatch(r"[A-Z][A-Z0-9_$#]{0,127}", normalized):
        raise ValueError(f"invalid Oracle identifier: {identifier}")
    return normalized


def _oracle_projection_tables(prefix: str) -> dict[str, str]:
    suffixes = {
        "runs": "RUNS",
        "events": "EVENTS",
        "steps": "STEPS",
        "approvals": "APPROVALS",
        "artifacts": "ARTIFACTS",
    }
    return {
        key: _validate_oracle_identifier(f"{prefix}_{suffix}") for key, suffix in suffixes.items()
    }


def _merge_projection_row(
    cursor: Any,
    *,
    table: str,
    key_column: str,
    values: dict[str, Any],
) -> None:
    safe_table = _validate_oracle_identifier(table)
    safe_key = _validate_oracle_identifier(key_column)
    safe_columns = [_validate_oracle_identifier(column) for column in values]
    source_select = ", ".join(f":{column} AS {column}" for column in safe_columns)
    update_columns = [column for column in safe_columns if column != safe_key]
    update_sql = ", ".join(f"target.{column} = source.{column}" for column in update_columns)
    insert_columns = ", ".join(safe_columns)
    insert_values = ", ".join(f"source.{column}" for column in safe_columns)
    cursor.execute(
        f"""
        MERGE INTO {safe_table} target
        USING (SELECT {source_select} FROM dual) source
        ON (target.{safe_key} = source.{safe_key})
        WHEN MATCHED THEN UPDATE SET {update_sql}
        WHEN NOT MATCHED THEN INSERT ({insert_columns})
        VALUES ({insert_values})
        """,
        **values,
    )


def _projection_tool_call_where(
    *,
    run_id: str | None,
    tool_name: str | None,
    status: str | None,
    approval_status: str | None,
    error_code: str | None,
    has_guardrail_warnings: bool | None,
) -> tuple[str, JsonObject]:
    clauses: list[str] = []
    params: JsonObject = {}
    if run_id is not None:
        clauses.append("r.run_id = :run_id")
        params["run_id"] = run_id
    if tool_name is not None:
        clauses.append("s.tool_name = :tool_name")
        params["tool_name"] = tool_name
    if status is not None:
        clauses.append("s.status = :status")
        params["status"] = status
    if approval_status is not None:
        clauses.append("a.status = :approval_status")
        params["approval_status"] = approval_status
    if error_code is not None:
        clauses.append("JSON_VALUE(s.tool_result_json, '$.error_code') = :error_code")
        params["error_code"] = error_code
    if has_guardrail_warnings is True:
        clauses.append("JSON_EXISTS(s.tool_result_json, '$.guardrail_warnings[*]')")
    elif has_guardrail_warnings is False:
        clauses.append("NOT JSON_EXISTS(s.tool_result_json, '$.guardrail_warnings[*]')")
    where_sql = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    return where_sql, params


def _is_oracle_table_missing_error(exc: Exception) -> bool:
    text = str(exc)
    return "ORA-00942" in text or "table or view does not exist" in text


def _oracle_lob_to_text(value: object) -> str:
    read = getattr(value, "read", None)
    if callable(read):
        return str(read())
    return str(value)


def _json_dump(value: JsonObject | list[JsonObject]) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), default=str)


def _maybe_externalize_artifact_content(run_id: str, artifact: Artifact) -> Artifact:
    backend = get_settings().agent_artifact_storage_backend.strip().lower()
    if backend in {"", "inline"}:
        return artifact
    if artifact.kind != "command_output":
        return artifact
    if backend != "filesystem":
        return _artifact_with_storage_warning(
            artifact,
            f"unsupported artifact storage backend: {backend}",
        )
    try:
        content_ref = _write_filesystem_artifact_content(run_id, artifact)
    except OSError as exc:
        return _artifact_with_storage_warning(
            artifact,
            f"artifact storage write failed: {exc.__class__.__name__}",
        )
    return artifact.model_copy(
        update={
            "content": _artifact_summary_content(artifact.content, content_ref),
            "content_ref": content_ref,
        },
        deep=True,
    )


def _hydrate_artifact_content(artifact: Artifact) -> Artifact:
    hydrated = artifact.model_copy(deep=True)
    if hydrated.content_ref is None:
        return hydrated
    if hydrated.content_ref.backend != "filesystem":
        return hydrated
    try:
        hydrated.content = _read_filesystem_artifact_content(hydrated.content_ref)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        hydrated.content = _with_metadata(
            hydrated.content,
            {
                "artifact_storage_error": f"{exc.__class__.__name__}",
            },
        )
    return hydrated


def _write_filesystem_artifact_content(
    run_id: str,
    artifact: Artifact,
) -> ArtifactContentRef:
    payload = _json_dump(artifact.content).encode("utf-8")
    digest = hashlib.sha256(payload).hexdigest()
    root = _artifact_storage_root()
    relative_path = Path(_safe_artifact_path_part(run_id)) / (
        f"{_safe_artifact_path_part(artifact.id)}.json"
    )
    path = (root / relative_path).resolve()
    if not _path_is_within(path, root):
        raise OSError("artifact path is outside storage root")
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = path.with_name(f".{path.name}.tmp")
    temp_path.write_bytes(payload)
    os.replace(temp_path, path)
    return ArtifactContentRef(
        backend="filesystem",
        uri=relative_path.as_posix(),
        size_bytes=len(payload),
        sha256=digest,
    )


def _read_filesystem_artifact_content(content_ref: ArtifactContentRef) -> JsonObject:
    root = _artifact_storage_root()
    relative_path = Path(content_ref.uri)
    if relative_path.is_absolute():
        raise ValueError("artifact content ref must be relative")
    path = (root / relative_path).resolve()
    if not _path_is_within(path, root):
        raise ValueError("artifact content ref points outside storage root")
    payload = path.read_bytes()
    if content_ref.sha256 and hashlib.sha256(payload).hexdigest() != content_ref.sha256:
        raise ValueError("artifact content checksum mismatch")
    loaded = json.loads(payload.decode("utf-8"))
    if not isinstance(loaded, dict):
        raise ValueError("artifact content must be a JSON object")
    return loaded


def _artifact_storage_root() -> Path:
    return Path(get_settings().agent_artifact_storage_path).expanduser().resolve()


def _artifact_summary_content(content: JsonObject, content_ref: ArtifactContentRef) -> JsonObject:
    stdout = content.get("stdout")
    stderr = content.get("stderr")
    summary: JsonObject = {
        key: value for key, value in content.items() if key not in {"stdout", "stderr", "metadata"}
    }
    summary["stdout_bytes"] = _text_size_bytes(stdout)
    summary["stderr_bytes"] = _text_size_bytes(stderr)
    summary["metadata"] = _with_metadata(
        _metadata_object(content.get("metadata")),
        {
            "artifact_storage_backend": content_ref.backend,
            "artifact_storage_uri": content_ref.uri,
            "artifact_content_ref": content_ref.model_dump(mode="json"),
        },
    )
    return summary


def _artifact_with_storage_warning(artifact: Artifact, warning: str) -> Artifact:
    return artifact.model_copy(
        update={"content": _with_metadata(artifact.content, {"artifact_storage_warning": warning})},
        deep=True,
    )


def _with_metadata(content: JsonObject, metadata: JsonObject) -> JsonObject:
    current = dict(content)
    current["metadata"] = {
        **_metadata_object(current.get("metadata")),
        **metadata,
    }
    return current


def _metadata_object(value: object) -> JsonObject:
    return dict(value) if isinstance(value, dict) else {}


def _text_size_bytes(value: object) -> int:
    return len(value.encode("utf-8")) if isinstance(value, str) else 0


def _safe_artifact_path_part(value: str) -> str:
    safe = re.sub(r"[^A-Za-z0-9_.-]", "_", value).strip("._")
    return safe[:160] or "artifact"


def _path_is_within(path: Path, parent: Path) -> bool:
    try:
        path.relative_to(parent)
    except ValueError:
        return False
    return True


def _json_load_object(value: object | None) -> JsonObject:
    if value is None:
        return {}
    try:
        loaded = json.loads(_oracle_lob_to_text(value))
    except (TypeError, ValueError):
        return {}
    return loaded if isinstance(loaded, dict) else {}


def _json_load_string_list(value: object | None) -> list[str]:
    if value is None:
        return []
    try:
        loaded = json.loads(_oracle_lob_to_text(value))
    except (TypeError, ValueError):
        return []
    if not isinstance(loaded, list):
        return []
    return [item for item in loaded if isinstance(item, str)]


def _model_list_json(values: Sequence[BaseModel]) -> str:
    return json.dumps(
        [value.model_dump(mode="json") for value in values],
        ensure_ascii=False,
        separators=(",", ":"),
    )


def _runtime_audit_record_from_projection_row(
    row: tuple[Any, ...],
    *,
    artifact_ids_by_step: dict[str, list[str]],
) -> RuntimeToolCallAuditRecord | None:
    (
        run_id,
        run_goal,
        run_status,
        agent_id,
        metadata_json,
        run_created_at,
        run_updated_at,
        step_id,
        step_status,
        row_tool_name,
        approval_id,
        tool_call_json,
        tool_result_json,
        started_at,
        completed_at,
        row_approval_status,
    ) = row
    tool_call = _projection_tool_call(tool_call_json)
    result = _projection_tool_result(tool_result_json)
    tool_name = str(row_tool_name or (tool_call.name if tool_call is not None else "tool"))
    definition = tool_registry.get(tool_name)
    audit_metadata = result.audit_metadata if result is not None else {}
    permission_level = (
        definition.permission_level.value
        if definition is not None
        else _audit_text(audit_metadata, "permission_level")
    )
    side_effects = (
        definition.side_effects
        if definition is not None
        else _audit_bool(audit_metadata, "side_effects")
    )

    return RuntimeToolCallAuditRecord(
        run_id=str(run_id),
        run_goal=str(run_goal),
        run_status=str(run_status),
        agent_id=str(agent_id),
        step_id=str(step_id),
        tool_name=tool_name,
        status=str(step_status),
        approval_id=str(approval_id) if approval_id is not None else None,
        approval_status=str(row_approval_status) if row_approval_status is not None else None,
        policy_decision=result.policy_decision.value if result is not None else None,
        permission_level=permission_level,
        side_effects=side_effects,
        started_at=_datetime_text(started_at),
        completed_at=_datetime_text(completed_at),
        duration_ms=result.duration_ms if result is not None else None,
        success=result.success if result is not None else None,
        error=result.error if result is not None else None,
        error_code=result.error_code if result is not None else None,
        guardrail_warnings=result.guardrail_warnings if result is not None else [],
        trace_id=(
            tool_call.trace_id if tool_call is not None else _audit_text(audit_metadata, "trace_id")
        ),
        artifact_ids=artifact_ids_by_step.get(str(step_id), []),
        audit_metadata=audit_metadata,
        run_created_at=_datetime_text(run_created_at) or "",
        run_updated_at=_datetime_text(run_updated_at) or "",
    )


def _projection_tool_call(value: object | None) -> ToolCall | None:
    if value is None:
        return None
    try:
        return ToolCall.model_validate_json(_oracle_lob_to_text(value))
    except ValueError:
        return None


def _projection_tool_result(value: object | None) -> ToolResult | None:
    if value is None:
        return None
    try:
        return ToolResult.model_validate_json(_oracle_lob_to_text(value))
    except ValueError:
        return None


def _datetime_text(value: object | None) -> str | None:
    if value is None:
        return None
    isoformat = getattr(value, "isoformat", None)
    if callable(isoformat):
        return str(isoformat())
    return str(value)


def _audit_text(metadata: JsonObject, key: str) -> str | None:
    value = metadata.get(key)
    return value if isinstance(value, str) else None


def _audit_bool(metadata: JsonObject, key: str) -> bool | None:
    value = metadata.get(key)
    return value if isinstance(value, bool) else None


def _tool_call_signature(call: ToolCall) -> str:
    return json.dumps(
        {"name": call.name, "arguments": call.arguments},
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def _validate_snapshot(snapshot: AgentRuntimeSnapshot) -> AgentRuntimeSnapshotValidation:
    errors: list[str] = []
    warnings: list[str] = []
    # ツール名は検証しない（MCP 接続のツールは動的で、旧版のツール名も記録に残る。#757）。
    summary = AgentRuntimeSnapshotSummary(
        runs=len(snapshot.runs),
        agents=len(snapshot.agents),
        events=sum(len(run.events) for run in snapshot.runs),
        steps=sum(len(run.steps) for run in snapshot.runs),
        approvals=sum(len(run.approvals) for run in snapshot.runs),
        artifacts=sum(len(run.artifacts) for run in snapshot.runs),
        pending_tool_calls=sum(len(run.pending_tool_calls) for run in snapshot.runs),
    )

    if snapshot.version not in {
        "agent-runtime.snapshot.v1",
        "agent-control-plane.snapshot.v2",
    }:
        errors.append(f"unsupported snapshot version: {snapshot.version}")
    elif snapshot.version == "agent-runtime.snapshot.v1":
        warnings.append("legacy snapshot will be migrated to agent-control-plane.snapshot.v2")

    _append_duplicate_errors("run", [run.id for run in snapshot.runs], errors)
    _append_duplicate_errors("agent", [agent.id for agent in snapshot.agents], errors)
    event_ids = [event.id for run in snapshot.runs for event in run.events]
    artifact_ids = [artifact.id for run in snapshot.runs for artifact in run.artifacts]
    _append_duplicate_errors("event", event_ids, errors)
    _append_duplicate_errors("artifact", artifact_ids, errors)

    if not any(agent.id == "default" for agent in snapshot.agents):
        warnings.append("default agent is missing and will be recreated")

    for agent in snapshot.agents:
        _validate_agent_snapshot(agent, errors)

    for run in snapshot.runs:
        _validate_run_snapshot(run, errors, warnings)

    return AgentRuntimeSnapshotValidation(
        valid=not errors,
        errors=errors,
        warnings=warnings,
        summary=summary,
    )


def _append_duplicate_errors(label: str, values: list[str], errors: list[str]) -> None:
    seen: set[str] = set()
    duplicates: set[str] = set()
    for value in values:
        if value in seen:
            duplicates.add(value)
            continue
        seen.add(value)
    if duplicates:
        errors.append(f"duplicate {label} id: {', '.join(sorted(duplicates))}")


def _validate_agent_snapshot(
    agent: AgentProfile,
    errors: list[str],
) -> None:
    if not agent.id:
        errors.append("agent id must not be empty")


def _validate_run_snapshot(
    run: RunState,
    errors: list[str],
    warnings: list[str],
) -> None:
    if not run.id:
        errors.append("run id must not be empty")
    step_ids = [step.id for step in run.steps]
    approval_ids = [approval.id for approval in run.approvals]
    step_id_set = set(step_ids)
    approval_id_set = set(approval_ids)
    _append_duplicate_errors(f"step id in run {run.id}", step_ids, errors)
    _append_duplicate_errors(f"approval id in run {run.id}", approval_ids, errors)

    for event in run.events:
        if event.run_id != run.id:
            errors.append(f"event {event.id} belongs to {event.run_id}, expected {run.id}")
    for step in run.steps:
        _validate_step_snapshot(run, step, approval_id_set, errors)
    for approval in run.approvals:
        _validate_approval_snapshot(run, approval, step_id_set, errors)

    pending_approvals = [
        approval.id for approval in run.approvals if approval.status == ApprovalStatus.PENDING
    ]
    if _is_terminal(run.status) and pending_approvals:
        errors.append(
            f"terminal run {run.id} has pending approvals: {', '.join(pending_approvals)}"
        )
    if run.status == RunStatus.WAITING_APPROVAL and not pending_approvals:
        errors.append(f"run {run.id} is waiting_approval without pending approvals")
    if run.status == RunStatus.RUNNING:
        warnings.append(f"run {run.id} is running and will resume as imported state")


def _validate_step_snapshot(
    run: RunState,
    step: RunStep,
    approval_ids: set[str],
    errors: list[str],
) -> None:
    if step.run_id != run.id:
        errors.append(f"step {step.id} belongs to {step.run_id}, expected {run.id}")
    if step.approval_id is not None and step.approval_id not in approval_ids:
        errors.append(f"step {step.id} references missing approval {step.approval_id}")


def _validate_approval_snapshot(
    run: RunState,
    approval: ApprovalRequest,
    step_ids: set[str],
    errors: list[str],
) -> None:
    if approval.run_id != run.id:
        errors.append(f"approval {approval.id} belongs to {approval.run_id}, expected {run.id}")
    if approval.step_id not in step_ids:
        errors.append(f"approval {approval.id} references missing step {approval.step_id}")


def _default_agent() -> AgentProfile:
    return AgentProfile(
        id="default",
        name="汎用業務 Agent",
        description="RAG / NL2SQL の MCP 接続と承認フローを使う既定 Agent。",
        instructions="業務データは外部ツール経由で取得し、根拠と監査情報を残す。",
        skill_ids=[
            "business_rag_research",
            "structured_data_query",
            "rag_then_structured_data",
        ],
        tool_names=tool_registry.names(),
        source="builtin",
    )


_LEGACY_TOOL_SKILLS: dict[str, str] = {
    "external_rag_search": "business_rag_research",
    "external_nl2sql_query": "structured_data_query",
}
# 削除したツール（#756 / #757）。移行では無視する（Skill の推定にも、移行の失敗にも数えない）。
_RETIRED_LEGACY_TOOLS: frozenset[str] = frozenset(
    {
        "external_rag_chat",
        "external_rag_list_business_views",
        "external_nl2sql_get_job",
        "external_mcp_call",
        "external_mcp_list_tools",
        "sandbox_command_run",
        "agent_skill_run",
    }
)


def _ensure_versioned(agent: AgentProfile) -> AgentProfile:
    """#770 より前の Agent（版を持たない）は、現在の内容を v1 として公開する。"""
    if agent.versioned:
        return agent
    return agent.model_copy(
        update={
            "versioned": True,
            "versions": [
                AgentVersion(
                    version=1,
                    note="版の導入（#770）の前の内容",
                    published_at=agent.updated_at,
                    **{field: getattr(agent, field) for field in AGENT_VERSIONED_FIELDS},
                )
            ],
            "published_version": 1,
        }
    )


def _agent_for_run(agent: AgentProfile, run: RunState) -> AgentProfile:
    """Run が使う版の内容を重ねた Agent（下書きの Run・版の無い古い Run は現在の内容）。"""
    version = run.metadata.get("agent_version")
    target = next(
        (item for item in agent.versions if isinstance(version, int) and item.version == version),
        None,
    )
    if target is None:
        return agent.model_copy(deep=True)
    return agent.model_copy(
        deep=True, update={field: getattr(target, field) for field in AGENT_VERSIONED_FIELDS}
    )


def _migrate_legacy_agent(agent: AgentProfile) -> AgentProfile:
    if agent.skill_ids or not agent.tool_names:
        return agent.model_copy(deep=True)
    inferred = {
        skill_id
        for tool_name in agent.tool_names
        if (skill_id := _LEGACY_TOOL_SKILLS.get(tool_name)) is not None
    }
    unmapped = sorted(
        set(agent.tool_names).difference(_LEGACY_TOOL_SKILLS).difference(_RETIRED_LEGACY_TOOLS)
    )
    return agent.model_copy(
        deep=True,
        update={
            "skill_ids": sorted(inferred),
            "migration_required": bool(unmapped),
            "enabled": agent.enabled and not unmapped,
        },
    )


def _active_tool_policy() -> ToolPolicy:
    config = runtime_config_store.get_tool_policy()
    return ToolPolicy(
        default_mode=config.default_mode,
        allow=config.allow,
        ask=config.ask,
        deny=config.deny,
    )


def build_runtime_repository() -> AgentRuntimeRepositoryContract:
    settings = get_settings()
    backend = settings.agent_runtime_repository_backend.strip().lower()
    if backend in {"oracle", "oracle_checkpoint", "oracle_normalized"}:
        # 共通の PLATFORM_ORACLE_* で接続する（#764。旧 AGENT_RUNTIME_ORACLE_* は読まない）。
        if backend == "oracle_normalized":
            return AgentRuntimeOracleNormalizedRepository(
                projection_retention_days=settings.agent_runtime_projection_retention_days,
                projection_write_mode=settings.agent_runtime_projection_write_mode,
            )
        return AgentRuntimeOracleCheckpointRepository()
    if backend in {"memory", "in_memory", "file", "file_snapshot"}:
        snapshot_path = (
            settings.agent_runtime_snapshot_path
            if backend in {"file", "file_snapshot"} or settings.agent_runtime_snapshot_path
            else None
        )
        return AgentRuntimeRepository(snapshot_path=snapshot_path)
    raise RuntimeError(f"unsupported Agent Runtime repository backend: {backend}")


runtime_repository: AgentRuntimeRepositoryContract = build_runtime_repository()
