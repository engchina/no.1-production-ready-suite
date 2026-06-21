"""Agent Runtime の in-memory 実装。

永続 DB 導入前でも API / 権限 / 承認 / SSE を検証できるよう、
append-only event log をプロセス内 repository として実装する。
"""

from __future__ import annotations

import hashlib
import json
import os
import re
from collections.abc import Callable, Iterator, Sequence
from datetime import UTC, datetime
from enum import StrEnum
from importlib import import_module
from pathlib import Path
from threading import Condition, Lock
from typing import Any, Protocol
from uuid import uuid4

from pydantic import BaseModel, Field, ValidationError

from app.features.agent.config import runtime_config_store
from app.features.agent.planner import PlannerDecision, PlannerMode, plan_next_step, plan_run_goal
from app.features.agent.tools import (
    ToolCall,
    ToolInvocationContext,
    ToolPolicy,
    ToolResult,
    tool_registry,
)
from app.observability import observe_memory_entries, record_runtime_event
from app.settings import get_settings

JsonObject = dict[str, Any]
OracleConnectFactory = Callable[[], Any]


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


class AgentProfile(BaseModel):
    id: str = Field(default_factory=lambda: f"agent_{uuid4().hex}")
    name: str
    description: str = ""
    instructions: str = ""
    tool_names: list[str] = Field(default_factory=list)
    command_allowed_prefixes: list[str] = Field(default_factory=list)
    enabled: bool = True
    created_at: datetime = Field(default_factory=_now)
    updated_at: datetime = Field(default_factory=_now)


class AgentProfilePatch(BaseModel):
    name: str | None = None
    description: str | None = None
    instructions: str | None = None
    tool_names: list[str] | None = None
    command_allowed_prefixes: list[str] | None = None
    enabled: bool | None = None


class MemoryKind(StrEnum):
    RUN_SUMMARY = "run_summary"
    USER_PREFERENCE = "user_preference"
    TOOL_LEARNING = "tool_learning"
    NOTE = "note"


class MemoryEntry(BaseModel):
    id: str = Field(default_factory=lambda: f"memory_{uuid4().hex}")
    kind: MemoryKind = MemoryKind.NOTE
    content: str
    metadata: JsonObject = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=_now)


class MemorySearchRequest(BaseModel):
    query: str = ""
    limit: int = Field(default=20, ge=1, le=100)
    kind: MemoryKind | None = None


class RunCreateRequest(BaseModel):
    goal: str
    agent_id: str = "default"
    tool_calls: list[ToolCall] = Field(default_factory=list)
    metadata: JsonObject = Field(default_factory=dict)
    planner_mode: PlannerMode = PlannerMode.AUTO


class RunState(BaseModel):
    id: str
    goal: str
    agent_id: str
    status: RunStatus
    steps: list[RunStep] = Field(default_factory=list)
    events: list[RunEvent] = Field(default_factory=list)
    approvals: list[ApprovalRequest] = Field(default_factory=list)
    artifacts: list[Artifact] = Field(default_factory=list)
    pending_tool_calls: list[ToolCall] = Field(default_factory=list)
    metadata: JsonObject = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=_now)
    updated_at: datetime = Field(default_factory=_now)


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


class MemoryData(BaseModel):
    entries: list[MemoryEntry]


class AgentRuntimeSnapshot(BaseModel):
    version: str = "agent-runtime.snapshot.v1"
    exported_at: datetime = Field(default_factory=_now)
    runs: list[RunState] = Field(default_factory=list)
    agents: list[AgentProfile] = Field(default_factory=list)
    memory: list[MemoryEntry] = Field(default_factory=list)


class AgentRuntimeSnapshotSummary(BaseModel):
    runs: int = 0
    agents: int = 0
    memory: int = 0
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
    def create_run(self, request: RunCreateRequest) -> RunState: ...
    def replay_run(self, run_id: str) -> RunState: ...
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
    def resume_run(self, run_id: str) -> RunState: ...
    def decide_approval(self, approval_id: str, request: ApprovalDecisionRequest) -> RunState: ...
    def list_agents(self) -> list[AgentProfile]: ...
    def create_agent(self, agent: AgentProfile) -> AgentProfile: ...
    def patch_agent(self, agent_id: str, patch: AgentProfilePatch) -> AgentProfile: ...
    def add_memory(self, entry: MemoryEntry) -> MemoryEntry: ...
    def search_memory(self, request: MemorySearchRequest) -> list[MemoryEntry]: ...
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
        default_agent = _default_agent()
        self._agents: dict[str, AgentProfile] = {default_agent.id: default_agent}
        self._memory: dict[str, MemoryEntry] = {}
        self._snapshot_path = Path(snapshot_path) if snapshot_path else None
        if self._snapshot_path is not None and self._snapshot_path.exists():
            self._load_snapshot_from_disk()

    def create_run(self, request: RunCreateRequest) -> RunState:
        with self._lock:
            planned_request, planner_decision = self._prepare_run_request_locked(request)
            self._validate_run_agent(planned_request)
            run = RunState(
                id=f"run_{uuid4().hex}",
                goal=planned_request.goal,
                agent_id=planned_request.agent_id,
                status=RunStatus.QUEUED,
                pending_tool_calls=list(planned_request.tool_calls),
                metadata={
                    **planned_request.metadata,
                    "_planner_mode": planned_request.planner_mode.value,
                },
            )
            self._runs[run.id] = run
            self._append_event(
                run,
                RunEventType.RUN_CREATED,
                "実行を作成しました。",
                {
                    "goal": planned_request.goal,
                    "agent_id": planned_request.agent_id,
                    "planner_mode": planned_request.planner_mode.value,
                },
            )
            self._append_planner_event_locked(run, planner_decision)
            self._persist_locked()

        self._start_run(run.id)
        return self.get_run(run.id)

    def replay_run(self, run_id: str) -> RunState:
        with self._lock:
            source = self._require_run(run_id)
            tool_calls = [
                step.tool_call.model_copy(deep=True)
                for step in source.steps
                if step.tool_call is not None
            ]
            tool_calls.extend(call.model_copy(deep=True) for call in source.pending_tool_calls)
            metadata = {**source.metadata, "replayed_from_run_id": source.id}
            self._append_event(
                source,
                RunEventType.RUN_REPLAYED,
                "実行を再実行キューへ投入しました。",
            )
            self._persist_locked()

        return self.create_run(
            RunCreateRequest(
                goal=source.goal,
                agent_id=source.agent_id,
                tool_calls=tool_calls,
                metadata=metadata,
            )
        )

    def list_runs(self) -> list[RunState]:
        with self._lock:
            return [run.model_copy(deep=True) for run in self._sorted_runs_locked()]

    def get_run(self, run_id: str) -> RunState:
        with self._lock:
            run = self._runs.get(run_id)
            if run is None:
                raise KeyError(run_id)
            return run.model_copy(deep=True)

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
            observe_memory_entries(len(self._memory))
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

    def resume_run(self, run_id: str) -> RunState:
        with self._lock:
            run = self._require_run(run_id)
            pending = [
                approval for approval in run.approvals if approval.status == ApprovalStatus.PENDING
            ]
            if pending:
                self._append_event(
                    run,
                    RunEventType.RUN_STATUS_CHANGED,
                    "承認待ちのため再開できません。",
                    {"pending_approval_ids": [approval.id for approval in pending]},
                )
                self._persist_locked()
                return run.model_copy(deep=True)
            if run.status not in {RunStatus.COMPLETED, RunStatus.CANCELLED}:
                run.status = RunStatus.COMPLETED
                run.updated_at = _now()
                self._append_event(run, RunEventType.RUN_COMPLETED, "実行を完了しました。")
                self._write_run_memory(run)
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
            self._persist_locked()

        if request.approved:
            self._execute_approved_step(
                run_id=approval.run_id,
                step_id=approval.step_id,
                approval_id=approval.id,
            )
        else:
            with self._lock:
                run = self._require_run(approval.run_id)
                step = self._require_step(run, approval.step_id)
                step.status = StepStatus.CANCELLED
                step.completed_at = _now()
                run.status = RunStatus.COMPLETED
                run.updated_at = _now()
                self._append_event(
                    run,
                    RunEventType.RUN_COMPLETED,
                    "承認拒否により実行を終了しました。",
                )
                self._write_run_memory(run)
                self._persist_locked()
        return self.get_run(approval.run_id)

    def list_agents(self) -> list[AgentProfile]:
        with self._lock:
            return [agent.model_copy(deep=True) for agent in self._sorted_agents_locked()]

    def create_agent(self, agent: AgentProfile) -> AgentProfile:
        with self._lock:
            if agent.id in self._agents:
                raise ValueError("agent already exists")
            self._validate_agent_tools(agent.tool_names)
            now = _now()
            agent.created_at = now
            agent.updated_at = now
            self._agents[agent.id] = agent
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
            for key, value in data.items():
                setattr(agent, key, value)
            agent.updated_at = _now()
            self._persist_locked()
            return agent.model_copy(deep=True)

    def add_memory(self, entry: MemoryEntry) -> MemoryEntry:
        with self._lock:
            self._memory[entry.id] = entry
            observe_memory_entries(len(self._memory))
            self._persist_locked()
            return entry.model_copy(deep=True)

    def search_memory(self, request: MemorySearchRequest) -> list[MemoryEntry]:
        query = request.query.lower().strip()
        with self._lock:
            entries = list(self._memory.values())
        if request.kind is not None:
            entries = [entry for entry in entries if entry.kind == request.kind]
        if query:
            entries = [
                entry
                for entry in entries
                if query in entry.content.lower()
                or any(query in str(value).lower() for value in entry.metadata.values())
            ]
        return sorted(entries, key=lambda entry: entry.created_at, reverse=True)[: request.limit]

    def _sorted_runs_locked(self) -> list[RunState]:
        return sorted(self._runs.values(), key=lambda run: run.created_at, reverse=True)

    def _sorted_agents_locked(self) -> list[AgentProfile]:
        return sorted(self._agents.values(), key=lambda agent: agent.created_at)

    def _sorted_memory_locked(self) -> list[MemoryEntry]:
        return sorted(self._memory.values(), key=lambda entry: entry.created_at, reverse=True)

    def _export_snapshot_locked(self) -> AgentRuntimeSnapshot:
        return AgentRuntimeSnapshot(
            runs=[run.model_copy(deep=True) for run in self._sorted_runs_locked()],
            agents=[agent.model_copy(deep=True) for agent in self._sorted_agents_locked()],
            memory=[entry.model_copy(deep=True) for entry in self._sorted_memory_locked()],
        )

    def _replace_state_locked(self, snapshot: AgentRuntimeSnapshot) -> None:
        self._runs = {run.id: run.model_copy(deep=True) for run in snapshot.runs}
        self._agents = {agent.id: agent.model_copy(deep=True) for agent in snapshot.agents}
        if "default" not in self._agents:
            self._agents["default"] = _default_agent()
        self._memory = {entry.id: entry.model_copy(deep=True) for entry in snapshot.memory}
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
            observe_memory_entries(len(self._memory))

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

    def _start_run(self, run_id: str) -> None:
        with self._lock:
            run = self._require_run(run_id)
            run.status = RunStatus.RUNNING
            run.updated_at = _now()
            self._append_event(run, RunEventType.RUN_STATUS_CHANGED, "実行を開始しました。")
            self._persist_locked()

        self._continue_run(run_id)

    def _continue_run(self, run_id: str) -> None:
        with self._lock:
            run = self._require_run(run_id)
            if not run.pending_tool_calls:
                if run.status == RunStatus.RUNNING:
                    run.status = RunStatus.COMPLETED
                    run.updated_at = _now()
                    message = (
                        "ツール呼び出しなしで実行を完了しました。"
                        if not run.steps
                        else "実行を完了しました。"
                    )
                    self._append_event(run, RunEventType.RUN_COMPLETED, message)
                    self._write_run_memory(run)
                    self._persist_locked()
                return

        while True:
            with self._lock:
                run = self._require_run(run_id)
                if run.status in {
                    RunStatus.WAITING_APPROVAL,
                    RunStatus.FAILED,
                    RunStatus.CANCELLED,
                }:
                    return
                if not run.pending_tool_calls:
                    if run.status == RunStatus.RUNNING:
                        run.status = RunStatus.COMPLETED
                        run.updated_at = _now()
                        self._append_event(run, RunEventType.RUN_COMPLETED, "実行を完了しました。")
                        self._write_run_memory(run)
                        self._persist_locked()
                    return
                call = run.pending_tool_calls.pop(0)

            state = self._execute_tool_step(run_id, call)
            if state.status in {RunStatus.WAITING_APPROVAL, RunStatus.FAILED, RunStatus.CANCELLED}:
                return

    def _execute_tool_step(self, run_id: str, call: ToolCall) -> RunState:
        with self._lock:
            run = self._require_run(run_id)
            step = RunStep(
                run_id=run_id,
                status=StepStatus.RUNNING,
                tool_call=call,
                started_at=_now(),
            )
            run.steps.append(step)
            self._append_event(
                run,
                RunEventType.STEP_STARTED,
                f"ツール {call.name} を開始しました。",
                {"step_id": step.id, "tool_name": call.name},
            )
            context = self._tool_invocation_context_locked(run, call)

        result = tool_registry.invoke(
            call,
            policy=_active_tool_policy(),
            context=context,
        )

        with self._lock:
            run = self._require_run(run_id)
            step = self._require_step(run, step.id)
            if result.approval_required:
                safety = runtime_config_store.get_runtime_safety()
                pending_count = _pending_approval_count(run)
                if pending_count >= safety.max_pending_approvals_per_run:
                    failure = ToolResult(
                        name=call.name,
                        success=False,
                        error="pending approval limit exceeded",
                        error_code="runtime.pending_approval_limit_exceeded",
                        policy_decision=result.policy_decision,
                        guardrail_warnings=["runtime.pending_approval_limit_exceeded"],
                        audit_metadata={
                            **result.audit_metadata,
                            "pending_approvals": pending_count,
                            "max_pending_approvals_per_run": (safety.max_pending_approvals_per_run),
                        },
                    )
                    step.tool_result = failure
                    step.status = StepStatus.FAILED
                    step.completed_at = _now()
                    run.status = RunStatus.FAILED
                    run.updated_at = _now()
                    self._append_event(
                        run,
                        RunEventType.TOOL_FAILED,
                        f"ツール {call.name} は承認待ち上限により停止しました。",
                        {
                            "step_id": step.id,
                            "tool_name": call.name,
                            "error": failure.error,
                            "error_code": failure.error_code,
                            "duration_ms": failure.duration_ms,
                            "audit_metadata": failure.audit_metadata,
                        },
                    )
                    self._write_tool_learning(run, step, failure)
                    self._write_run_memory(run)
                    self._persist_locked()
                    return run.model_copy(deep=True)
                approval = ApprovalRequest(
                    run_id=run_id,
                    step_id=step.id,
                    tool_call=call,
                    reason=f"{call.name} は承認が必要です。",
                )
                step.tool_result = result
                step.status = StepStatus.WAITING_APPROVAL
                step.approval_id = approval.id
                run.approvals.append(approval)
                self._approvals[approval.id] = approval
                run.status = RunStatus.WAITING_APPROVAL
                run.updated_at = _now()
                self._append_event(
                    run,
                    RunEventType.TOOL_APPROVAL_REQUIRED,
                    f"ツール {call.name} は承認待ちです。",
                    {
                        "approval_id": approval.id,
                        "step_id": step.id,
                        "tool_name": call.name,
                        "policy_decision": result.policy_decision,
                        "duration_ms": result.duration_ms,
                        "audit_metadata": result.audit_metadata,
                    },
                )
                self._persist_locked()
                return run.model_copy(deep=True)

            step.tool_result = result
            step.completed_at = _now()
            if result.success:
                expansion_failure = self._skill_plan_expansion_failure_locked(
                    run,
                    step,
                    result,
                )
                if expansion_failure is not None:
                    self._fail_tool_step_locked(
                        run,
                        step,
                        expansion_failure,
                        f"Skill {call.name} の計画展開に失敗しました。",
                    )
                    self._persist_locked()
                    return run.model_copy(deep=True)
                step.status = StepStatus.COMPLETED
                self._record_tool_success_artifacts(run, step, result)
                self._append_event(
                    run,
                    RunEventType.TOOL_COMPLETED,
                    f"ツール {call.name} が完了しました。",
                    {
                        "step_id": step.id,
                        "tool_name": call.name,
                        "output": result.output,
                        "duration_ms": result.duration_ms,
                        "guardrail_warnings": result.guardrail_warnings,
                        "audit_metadata": result.audit_metadata,
                    },
                )
                self._append_guardrail_events(run, step, result)
                self._maybe_enqueue_planner_continuation_locked(run, step, result)
            else:
                step.status = StepStatus.FAILED
                run.status = RunStatus.FAILED
                self._append_event(
                    run,
                    RunEventType.TOOL_FAILED,
                    f"ツール {call.name} が失敗しました。",
                    {
                        "step_id": step.id,
                        "tool_name": call.name,
                        "error": result.error,
                        "error_code": result.error_code,
                        "error_details": result.error_details,
                        "duration_ms": result.duration_ms,
                        "audit_metadata": result.audit_metadata,
                    },
                )
                self._write_tool_learning(run, step, result)
                self._write_run_memory(run)
            run.updated_at = _now()
            self._persist_locked()
            return run.model_copy(deep=True)

    def _execute_approved_step(self, *, run_id: str, step_id: str, approval_id: str) -> None:
        with self._lock:
            run = self._require_run(run_id)
            step = self._require_step(run, step_id)
            if step.tool_call is None:
                raise RuntimeError("approved step does not have a tool call")
            call = step.tool_call
            step.status = StepStatus.RUNNING
            run.status = RunStatus.RUNNING
            run.updated_at = _now()
            self._append_event(
                run,
                RunEventType.STEP_STARTED,
                f"承認済みツール {call.name} を実行します。",
                {"step_id": step.id, "approval_id": approval_id, "tool_name": call.name},
            )
            context = self._tool_invocation_context_locked(
                run,
                call,
                approval_id=approval_id,
            )
            self._persist_locked()

        result = tool_registry.invoke(
            call,
            policy=_active_tool_policy(),
            context=context,
            force=True,
        )

        with self._lock:
            run = self._require_run(run_id)
            step = self._require_step(run, step_id)
            step.tool_result = result
            step.completed_at = _now()
            if result.success:
                expansion_failure = self._skill_plan_expansion_failure_locked(
                    run,
                    step,
                    result,
                )
                if expansion_failure is not None:
                    self._fail_tool_step_locked(
                        run,
                        step,
                        expansion_failure,
                        f"Skill {call.name} の計画展開に失敗しました。",
                    )
                    self._persist_locked()
                    return
                step.status = StepStatus.COMPLETED
                self._record_tool_success_artifacts(run, step, result)
                self._append_event(
                    run,
                    RunEventType.TOOL_COMPLETED,
                    f"ツール {call.name} が完了しました。",
                    {
                        "step_id": step.id,
                        "approval_id": approval_id,
                        "tool_name": call.name,
                        "output": result.output,
                        "duration_ms": result.duration_ms,
                        "guardrail_warnings": result.guardrail_warnings,
                        "audit_metadata": result.audit_metadata,
                    },
                )
                self._append_guardrail_events(run, step, result)
                self._maybe_enqueue_planner_continuation_locked(run, step, result)
            else:
                step.status = StepStatus.FAILED
                run.status = RunStatus.FAILED
                self._append_event(
                    run,
                    RunEventType.TOOL_FAILED,
                    f"ツール {call.name} が失敗しました。",
                    {
                        "step_id": step.id,
                        "approval_id": approval_id,
                        "tool_name": call.name,
                        "error": result.error,
                        "error_code": result.error_code,
                        "error_details": result.error_details,
                        "duration_ms": result.duration_ms,
                        "audit_metadata": result.audit_metadata,
                    },
                )
                self._write_tool_learning(run, step, result)
                self._write_run_memory(run)
                self._persist_locked()
                return
            run.updated_at = _now()
            self._persist_locked()

        self._continue_run(run_id)

    def _record_tool_success_artifacts(
        self,
        run: RunState,
        step: RunStep,
        result: ToolResult,
    ) -> None:
        if step.tool_call is None or result.output is None:
            return
        artifact_kind_by_tool = {
            "agent_skill_run": "skill_plan",
            "external_rag_search": "rag_evidence",
            "external_nl2sql_query": "structured_table",
            "sandbox_command_run": "command_output",
        }
        kind = artifact_kind_by_tool.get(step.tool_call.name)
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

    def _skill_plan_expansion_failure_locked(
        self,
        run: RunState,
        step: RunStep,
        result: ToolResult,
    ) -> ToolResult | None:
        if step.tool_call is None or step.tool_call.name != "agent_skill_run":
            return None
        try:
            planned_calls = self._planned_tool_calls_from_skill_result_locked(
                run,
                step,
                result,
            )
        except ValueError as exc:
            failure = result.model_copy(deep=True)
            failure.success = False
            failure.error = str(exc)
            failure.error_code = "agent_skill.plan_expansion_failed"
            failure.guardrail_warnings = [
                *failure.guardrail_warnings,
                "agent_skill.plan_expansion_failed",
            ]
            failure.audit_metadata = {
                **failure.audit_metadata,
                "success": False,
                "error_code": failure.error_code,
            }
            return failure
        if planned_calls:
            run.pending_tool_calls = [*planned_calls, *run.pending_tool_calls]
            self._append_event(
                run,
                RunEventType.SKILL_PLANNED,
                "Skill を ToolCall 計画へ展開しました。",
                {
                    "step_id": step.id,
                    "skill_id": result.output.get("skill_id") if result.output else None,
                    "skill_name": result.output.get("skill_name") if result.output else None,
                    "planned_tool_calls": [call.model_dump(mode="json") for call in planned_calls],
                    "planned_tool_call_count": len(planned_calls),
                },
            )
        return None

    def _planned_tool_calls_from_skill_result_locked(
        self,
        run: RunState,
        step: RunStep,
        result: ToolResult,
    ) -> list[ToolCall]:
        output = result.output
        if output is None:
            raise ValueError("skill result is missing output")
        raw_calls = output.get("tool_calls")
        if not isinstance(raw_calls, list):
            raise ValueError("skill result must include tool_calls[]")
        planned_calls: list[ToolCall] = []
        for raw_call in raw_calls:
            if not isinstance(raw_call, dict):
                raise ValueError("skill planned tool call must be an object")
            try:
                call = ToolCall.model_validate(raw_call)
            except ValidationError as exc:
                raise ValueError("skill planned tool call schema is invalid") from exc
            if call.name == "agent_skill_run":
                raise ValueError("nested agent_skill_run is not allowed")
            if tool_registry.get(call.name) is None:
                raise ValueError(f"skill planned unknown tool: {call.name}")
            planned_calls.append(call)
        self._validate_planned_tool_calls_locked(run, planned_calls)
        safety = runtime_config_store.get_runtime_safety()
        total_planned_for_run = len(run.steps) + len(run.pending_tool_calls) + len(planned_calls)
        if total_planned_for_run > safety.max_tool_calls_per_run:
            raise ValueError(
                "skill planned tool call limit exceeded: "
                f"max_tool_calls_per_run={safety.max_tool_calls_per_run}"
            )
        return planned_calls

    def _validate_planned_tool_calls_locked(
        self,
        run: RunState,
        planned_calls: Sequence[ToolCall],
    ) -> None:
        agent = self._agents.get(run.agent_id)
        if agent is None:
            raise ValueError("agent not found")
        allowed_tools = set(agent.tool_names)
        denied_tools = sorted(
            {call.name for call in planned_calls if call.name not in allowed_tools}
        )
        if denied_tools:
            raise ValueError(f"skill planned tool not allowed for agent: {', '.join(denied_tools)}")

    def _fail_tool_step_locked(
        self,
        run: RunState,
        step: RunStep,
        failure: ToolResult,
        message: str,
    ) -> None:
        step.tool_result = failure
        step.status = StepStatus.FAILED
        step.completed_at = _now()
        run.status = RunStatus.FAILED
        run.updated_at = _now()
        self._append_event(
            run,
            RunEventType.TOOL_FAILED,
            message,
            {
                "step_id": step.id,
                "tool_name": step.tool_call.name if step.tool_call else step.kind,
                "error": failure.error,
                "error_code": failure.error_code,
                "error_details": failure.error_details,
                "duration_ms": failure.duration_ms,
                "audit_metadata": failure.audit_metadata,
            },
        )
        self._write_tool_learning(run, step, failure)
        self._write_run_memory(run)

    def _maybe_enqueue_planner_continuation_locked(
        self,
        run: RunState,
        step: RunStep,
        result: ToolResult,
    ) -> None:
        if run.pending_tool_calls or step.tool_call is None:
            return
        if run.metadata.get("_planner_mode") == PlannerMode.OFF.value:
            return
        if step.tool_call.name == "agent_skill_run":
            return
        planner_metadata = {
            **run.metadata,
            "planner_context": self._planner_context_locked(run, step, result),
        }
        try:
            decision = plan_next_step(run.goal, planner_metadata)
        except Exception as exc:  # noqa: BLE001 - planner 境界では継続不能を event 化する
            decision = PlannerDecision(
                planned=False,
                reason=str(exc),
                warnings=["planner.continuation_failed"],
                metadata={"planner_phase": "continue"},
            )
            self._append_planner_event_locked(run, decision)
            return

        if not decision.tool_calls:
            self._append_planner_event_locked(run, decision)
            return
        planned_calls = self._dedupe_planner_tool_calls_locked(run, decision.tool_calls)
        if len(planned_calls) != len(decision.tool_calls):
            decision.warnings.append("planner.duplicate_tool_call_suppressed")
        if not planned_calls:
            decision.planned = False
            decision.tool_calls = []
            self._append_planner_event_locked(run, decision)
            return
        try:
            self._validate_planned_tool_calls_locked(run, planned_calls)
            safety = runtime_config_store.get_runtime_safety()
            total_planned_for_run = (
                len(run.steps) + len(run.pending_tool_calls) + len(planned_calls)
            )
            if total_planned_for_run > safety.max_tool_calls_per_run:
                raise ValueError(
                    "planner continuation tool call limit exceeded: "
                    f"max_tool_calls_per_run={safety.max_tool_calls_per_run}"
                )
        except ValueError as exc:
            decision.planned = False
            decision.tool_calls = []
            decision.reason = str(exc)
            decision.warnings.append("planner.continuation_rejected")
            self._append_planner_event_locked(run, decision)
            return
        run.pending_tool_calls = [*planned_calls, *run.pending_tool_calls]
        decision.planned = True
        decision.tool_calls = planned_calls
        self._append_planner_event_locked(run, decision)

    def _planner_context_locked(
        self,
        run: RunState,
        step: RunStep,
        result: ToolResult,
    ) -> JsonObject:
        completed_tool_names = [
            current.tool_call.name
            for current in run.steps
            if current.tool_call is not None and current.status == StepStatus.COMPLETED
        ]
        artifact_kinds = [
            event.payload.get("kind")
            for event in run.events
            if event.type == RunEventType.ARTIFACT_CREATED and event.payload.get("kind") is not None
        ]
        return {
            "phase": "continue",
            "trigger_step_id": step.id,
            "last_tool_name": step.tool_call.name if step.tool_call else step.kind,
            "last_tool_success": result.success,
            "completed_tool_names": completed_tool_names,
            "tool_call_count": len(run.steps) + len(run.pending_tool_calls),
            "artifact_kinds": artifact_kinds,
            "guardrail_warnings": list(result.guardrail_warnings),
        }

    def _dedupe_planner_tool_calls_locked(
        self,
        run: RunState,
        tool_calls: Sequence[ToolCall],
    ) -> list[ToolCall]:
        existing = {
            _tool_call_signature(step.tool_call) for step in run.steps if step.tool_call is not None
        }
        existing.update(_tool_call_signature(call) for call in run.pending_tool_calls)
        deduped: list[ToolCall] = []
        for call in tool_calls:
            signature = _tool_call_signature(call)
            if signature in existing:
                continue
            existing.add(signature)
            deduped.append(call)
        return deduped

    def _tool_invocation_context_locked(
        self,
        run: RunState,
        call: ToolCall,
        *,
        approval_id: str | None = None,
    ) -> ToolInvocationContext:
        agent = self._agents.get(run.agent_id)
        return ToolInvocationContext(
            approval_id=approval_id,
            trace_id=call.trace_id,
            agent_id=run.agent_id,
            command_allowed_prefixes=(
                list(agent.command_allowed_prefixes) if agent is not None else []
            ),
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
        self._write_tool_learning(run, step, result)

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

    def _write_run_memory(self, run: RunState) -> None:
        if not get_settings().agent_memory_enabled:
            return
        if any(
            entry.kind == MemoryKind.RUN_SUMMARY and entry.metadata.get("run_id") == run.id
            for entry in self._memory.values()
        ):
            return
        succeeded = sum(1 for step in run.steps if step.status == StepStatus.COMPLETED)
        failed = sum(1 for step in run.steps if step.status == StepStatus.FAILED)
        entry = MemoryEntry(
            kind=MemoryKind.RUN_SUMMARY,
            content=(
                f"{run.goal} / status={run.status} / "
                f"tool_success={succeeded} / tool_failed={failed}"
            ),
            metadata={"run_id": run.id, "agent_id": run.agent_id, "status": run.status},
        )
        self._memory[entry.id] = entry
        observe_memory_entries(len(self._memory))
        self._append_event(
            run,
            RunEventType.MEMORY_WRITTEN,
            "実行サマリーを Agent 内部メモリへ保存しました。",
            {"memory_id": entry.id, "kind": entry.kind.value},
        )

    def _write_tool_learning(self, run: RunState, step: RunStep, result: ToolResult) -> None:
        if not get_settings().agent_memory_enabled or step.tool_call is None:
            return
        if any(
            entry.kind == MemoryKind.TOOL_LEARNING and entry.metadata.get("step_id") == step.id
            for entry in self._memory.values()
        ):
            return
        status = "success" if result.success else "failed"
        warnings = ", ".join(result.guardrail_warnings)
        reason = warnings if warnings else result.error_code or result.error or status
        entry = MemoryEntry(
            kind=MemoryKind.TOOL_LEARNING,
            content=f"{step.tool_call.name} / {status} / {reason}",
            metadata={
                "run_id": run.id,
                "step_id": step.id,
                "tool_name": step.tool_call.name,
                "status": status,
                "guardrail_warnings": result.guardrail_warnings,
                "error_code": result.error_code,
                "error": result.error,
            },
        )
        self._memory[entry.id] = entry
        observe_memory_entries(len(self._memory))
        self._append_event(
            run,
            RunEventType.MEMORY_WRITTEN,
            "ツール経験を Agent 内部メモリへ保存しました。",
            {"memory_id": entry.id, "kind": entry.kind.value, "step_id": step.id},
        )

    def _require_run(self, run_id: str) -> RunState:
        run = self._runs.get(run_id)
        if run is None:
            raise KeyError(run_id)
        return run

    def _validate_run_agent(self, request: RunCreateRequest) -> None:
        agent = self._agents.get(request.agent_id)
        if agent is None:
            raise KeyError(request.agent_id)
        if not agent.enabled:
            raise ValueError("agent disabled")
        safety = runtime_config_store.get_runtime_safety()
        if len(request.tool_calls) > safety.max_tool_calls_per_run:
            raise ValueError(
                "tool call limit exceeded: "
                f"max_tool_calls_per_run={safety.max_tool_calls_per_run}"
            )
        allowed_tools = set(agent.tool_names)
        denied_tools = sorted(
            {call.name for call in request.tool_calls if call.name not in allowed_tools}
        )
        if denied_tools:
            raise ValueError(f"tool not allowed for agent: {', '.join(denied_tools)}")

    def _prepare_run_request_locked(
        self,
        request: RunCreateRequest,
    ) -> tuple[RunCreateRequest, PlannerDecision | None]:
        if request.tool_calls or request.planner_mode == PlannerMode.OFF:
            return request, None
        decision = plan_run_goal(request.goal, request.metadata)
        if not decision.tool_calls:
            return request, decision
        planned_request = request.model_copy(update={"tool_calls": decision.tool_calls})
        return planned_request, decision

    def _append_planner_event_locked(
        self,
        run: RunState,
        decision: PlannerDecision | None,
    ) -> None:
        if decision is None:
            return
        if not decision.planned and not decision.warnings:
            return
        message = (
            "Agent planner が ToolCall を生成しました。"
            if decision.planned
            else "Agent planner は実行可能な ToolCall を生成しませんでした。"
        )
        self._append_event(
            run,
            RunEventType.PLANNER_COMPLETED,
            message,
            decision.model_dump(mode="json"),
        )

    @staticmethod
    def _validate_agent_tools(tool_names: list[str]) -> None:
        registered_tools = set(tool_registry.names())
        unknown_tools = sorted({name for name in tool_names if name not in registered_tools})
        if unknown_tools:
            raise ValueError(f"unknown tool: {', '.join(unknown_tools)}")

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
        dsn: str,
        user: str,
        password: str,
        table_name: str = "AGENT_RUNTIME_CHECKPOINTS",
        checkpoint_key: str = "default",
        create_schema: bool = True,
        connect_factory: OracleConnectFactory | None = None,
    ) -> None:
        self._oracle_dsn = dsn
        self._oracle_user = user
        self._oracle_password = password
        self._oracle_table_name = _validate_oracle_identifier(table_name)
        self._oracle_checkpoint_key = checkpoint_key
        self._oracle_connect_factory = connect_factory
        super().__init__(snapshot_path=None)
        if create_schema:
            self._ensure_oracle_schema()
        self._load_snapshot_from_oracle()

    def _connect_oracle(self) -> Any:
        if self._oracle_connect_factory is not None:
            return self._oracle_connect_factory()
        oracledb = import_module("oracledb")
        return oracledb.connect(
            user=self._oracle_user,
            password=self._oracle_password,
            dsn=self._oracle_dsn,
        )

    def _ensure_oracle_schema(self) -> None:
        ddl = f"""
        CREATE TABLE {self._oracle_table_name} (
            checkpoint_key VARCHAR2(128) PRIMARY KEY,
            snapshot_json CLOB NOT NULL,
            updated_at TIMESTAMP WITH TIME ZONE DEFAULT SYSTIMESTAMP NOT NULL
        )
        """
        with self._connect_oracle() as connection, connection.cursor() as cursor:
            try:
                cursor.execute(ddl)
            except Exception as exc:
                if not _is_oracle_object_exists_error(exc):
                    raise
            connection.commit()

    def _load_snapshot_from_oracle(self) -> None:
        query = (
            f"SELECT snapshot_json FROM {self._oracle_table_name} "
            "WHERE checkpoint_key = :checkpoint_key"
        )
        with self._connect_oracle() as connection, connection.cursor() as cursor:
            cursor.execute(query, checkpoint_key=self._oracle_checkpoint_key)
            row = cursor.fetchone()
        if row is None:
            return
        snapshot_json = _oracle_lob_to_text(row[0])
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
            observe_memory_entries(len(self._memory))

    def _persist_locked(self) -> None:
        snapshot_json = self._export_snapshot_locked().model_dump_json()
        with self._connect_oracle() as connection, connection.cursor() as cursor:
            self._write_checkpoint_cursor(cursor, snapshot_json)
            connection.commit()

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
        dsn: str,
        user: str,
        password: str,
        table_name: str = "AGENT_RUNTIME_CHECKPOINTS",
        checkpoint_key: str = "default",
        projection_prefix: str = "AGENT_RUNTIME",
        projection_retention_days: int = 0,
        projection_write_mode: str = "replace",
        create_schema: bool = True,
        connect_factory: OracleConnectFactory | None = None,
    ) -> None:
        self._oracle_projection_prefix = _validate_oracle_identifier(projection_prefix)
        self._oracle_projection_tables = _oracle_projection_tables(self._oracle_projection_prefix)
        self._oracle_projection_retention_days = max(0, projection_retention_days)
        self._oracle_projection_write_mode = projection_write_mode.strip().lower() or "replace"
        if self._oracle_projection_write_mode not in {"replace", "incremental"}:
            raise ValueError("projection_write_mode must be replace or incremental")
        super().__init__(
            dsn=dsn,
            user=user,
            password=password,
            table_name=table_name,
            checkpoint_key=checkpoint_key,
            create_schema=create_schema,
            connect_factory=connect_factory,
        )

    def _ensure_oracle_schema(self) -> None:
        super()._ensure_oracle_schema()
        with self._connect_oracle() as connection, connection.cursor() as cursor:
            for ddl in [*self._oracle_projection_ddls(), *self._oracle_projection_index_ddls()]:
                try:
                    cursor.execute(ddl)
                except Exception as exc:
                    if not _is_oracle_object_exists_error(exc):
                        raise
            connection.commit()

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
        business_view_ids: set[str] | None = None,
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
                business_view_ids=business_view_ids,
            )
            rows = self._projection_tool_call_rows(
                cursor,
                run_id=run_id,
                tool_name=tool_name,
                status=status,
                approval_status=approval_status,
                error_code=error_code,
                has_guardrail_warnings=has_guardrail_warnings,
                business_view_ids=business_view_ids,
                offset=offset,
                limit=limit,
            )

        records: list[RuntimeToolCallAuditRecord] = []
        for row in rows:
            record = _runtime_audit_record_from_projection_row(
                row,
                artifact_ids_by_step=artifact_ids_by_step,
                business_view_ids=business_view_ids,
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
        business_view_ids: set[str] | None,
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
            business_view_ids=business_view_ids,
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
        business_view_ids: set[str] | None,
    ) -> int:
        tables = self._oracle_projection_tables
        where_sql, params = _projection_tool_call_where(
            run_id=run_id,
            tool_name=tool_name,
            status=status,
            approval_status=approval_status,
            error_code=error_code,
            has_guardrail_warnings=has_guardrail_warnings,
            business_view_ids=business_view_ids,
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

    def _oracle_projection_ddls(self) -> list[str]:
        tables = self._oracle_projection_tables
        return [
            f"""
            CREATE TABLE {tables["runs"]} (
                run_id VARCHAR2(128) PRIMARY KEY,
                agent_id VARCHAR2(128) NOT NULL,
                status VARCHAR2(32) NOT NULL,
                goal CLOB NOT NULL,
                metadata_json CLOB,
                pending_tool_calls_json CLOB,
                created_at TIMESTAMP WITH TIME ZONE NOT NULL,
                updated_at TIMESTAMP WITH TIME ZONE NOT NULL
            )
            """,
            f"""
            CREATE TABLE {tables["events"]} (
                event_id VARCHAR2(128) PRIMARY KEY,
                run_id VARCHAR2(128) NOT NULL,
                event_type VARCHAR2(128) NOT NULL,
                message CLOB NOT NULL,
                payload_json CLOB,
                created_at TIMESTAMP WITH TIME ZONE NOT NULL
            )
            """,
            f"""
            CREATE TABLE {tables["steps"]} (
                step_id VARCHAR2(128) PRIMARY KEY,
                run_id VARCHAR2(128) NOT NULL,
                kind VARCHAR2(64) NOT NULL,
                status VARCHAR2(32) NOT NULL,
                tool_name VARCHAR2(256),
                approval_id VARCHAR2(128),
                tool_call_json CLOB,
                tool_result_json CLOB,
                started_at TIMESTAMP WITH TIME ZONE,
                completed_at TIMESTAMP WITH TIME ZONE
            )
            """,
            f"""
            CREATE TABLE {tables["approvals"]} (
                approval_id VARCHAR2(128) PRIMARY KEY,
                run_id VARCHAR2(128) NOT NULL,
                step_id VARCHAR2(128) NOT NULL,
                tool_name VARCHAR2(256) NOT NULL,
                status VARCHAR2(32) NOT NULL,
                reason CLOB NOT NULL,
                decided_by VARCHAR2(256),
                decided_at TIMESTAMP WITH TIME ZONE,
                created_at TIMESTAMP WITH TIME ZONE NOT NULL,
                tool_call_json CLOB NOT NULL
            )
            """,
            f"""
            CREATE TABLE {tables["artifacts"]} (
                artifact_id VARCHAR2(128) PRIMARY KEY,
                run_id VARCHAR2(128) NOT NULL,
                name VARCHAR2(512) NOT NULL,
                kind VARCHAR2(128) NOT NULL,
                content_json CLOB NOT NULL,
                created_at TIMESTAMP WITH TIME ZONE NOT NULL
            )
            """,
            f"""
            CREATE TABLE {tables["memory"]} (
                memory_id VARCHAR2(128) PRIMARY KEY,
                kind VARCHAR2(64) NOT NULL,
                content CLOB NOT NULL,
                metadata_json CLOB,
                created_at TIMESTAMP WITH TIME ZONE NOT NULL
            )
            """,
        ]

    def _oracle_projection_index_ddls(self) -> list[str]:
        tables = self._oracle_projection_tables
        prefix = self._oracle_projection_prefix
        return [
            f"""
            CREATE INDEX {prefix}_RUNS_STATUS_CREATED_IX
            ON {tables["runs"]} (status, created_at)
            """,
            f"""
            CREATE INDEX {prefix}_EVENTS_RUN_TYPE_CREATED_IX
            ON {tables["events"]} (run_id, event_type, created_at)
            """,
            f"""
            CREATE INDEX {prefix}_STEPS_RUN_TOOL_STATUS_IX
            ON {tables["steps"]} (run_id, tool_name, status, completed_at)
            """,
            f"""
            CREATE INDEX {prefix}_STEPS_ERROR_CODE_IX
            ON {tables["steps"]} (
                JSON_VALUE(tool_result_json, '$.error_code' RETURNING VARCHAR2(128))
            )
            """,
            f"""
            CREATE INDEX {prefix}_APPROVALS_RUN_STATUS_IX
            ON {tables["approvals"]} (run_id, status, created_at)
            """,
            f"""
            CREATE INDEX {prefix}_ARTIFACTS_RUN_KIND_IX
            ON {tables["artifacts"]} (run_id, kind, created_at)
            """,
            f"""
            CREATE INDEX {prefix}_MEMORY_KIND_CREATED_IX
            ON {tables["memory"]} (kind, created_at)
            """,
        ]

    def _replace_projection_cursor(
        self,
        cursor: Any,
        snapshot: AgentRuntimeSnapshot,
    ) -> None:
        for table in reversed(list(self._oracle_projection_tables.values())):
            cursor.execute(f"DELETE FROM {table}")
        self._insert_runs(cursor, snapshot.runs)
        self._insert_memory(cursor, snapshot.memory)

    def _upsert_projection_cursor(
        self,
        cursor: Any,
        snapshot: AgentRuntimeSnapshot,
    ) -> None:
        self._upsert_runs(cursor, snapshot.runs)
        self._upsert_memory(cursor, snapshot.memory)

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
            (tables["memory"], "created_at"),
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

    def _insert_memory(self, cursor: Any, memory: list[MemoryEntry]) -> None:
        table = self._oracle_projection_tables["memory"]
        for entry in memory:
            cursor.execute(
                f"""
                INSERT INTO {table} (
                    memory_id,
                    kind,
                    content,
                    metadata_json,
                    created_at
                ) VALUES (
                    :memory_id,
                    :kind,
                    :content,
                    :metadata_json,
                    :created_at
                )
                """,
                memory_id=entry.id,
                kind=entry.kind.value,
                content=entry.content,
                metadata_json=_json_dump(entry.metadata),
                created_at=entry.created_at,
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

    def _upsert_memory(self, cursor: Any, memory: list[MemoryEntry]) -> None:
        table = self._oracle_projection_tables["memory"]
        for entry in memory:
            _merge_projection_row(
                cursor,
                table=table,
                key_column="memory_id",
                values={
                    "memory_id": entry.id,
                    "kind": entry.kind.value,
                    "content": entry.content,
                    "metadata_json": _json_dump(entry.metadata),
                    "created_at": entry.created_at,
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
        "memory": "MEMORY",
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
    business_view_ids: set[str] | None,
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
    if business_view_ids is not None:
        business_view_values = sorted(business_view_ids)
        if business_view_values:
            placeholders: list[str] = []
            for index, value in enumerate(business_view_values):
                key = f"business_view_id_{index}"
                placeholders.append(f":{key}")
                params[key] = value
            in_clause = ", ".join(placeholders)
            clauses.append(
                "("
                "JSON_VALUE(r.metadata_json, '$.business_view_id') "
                f"IN ({in_clause}) OR "
                "JSON_VALUE(s.tool_call_json, '$.arguments.business_view_id') "
                f"IN ({in_clause})"
                ")"
            )
        else:
            clauses.append("1 = 0")
    where_sql = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    return where_sql, params


def _is_oracle_object_exists_error(exc: Exception) -> bool:
    text = str(exc)
    return "ORA-00955" in text or "name is already used by an existing object" in text


def _oracle_lob_to_text(value: object) -> str:
    read = getattr(value, "read", None)
    if callable(read):
        return str(read())
    return str(value)


def _json_dump(value: JsonObject | list[JsonObject]) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), default=str)


def _maybe_externalize_artifact_content(run_id: str, artifact: Artifact) -> Artifact:
    command_policy = runtime_config_store.get_command_policy()
    backend = command_policy.artifact_storage_backend.strip().lower()
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
    command_policy = runtime_config_store.get_command_policy()
    return Path(command_policy.artifact_storage_path).expanduser().resolve()


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
    business_view_ids: set[str] | None,
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
    metadata = _json_load_object(metadata_json)
    if not _projection_business_view_allowed(
        metadata=metadata,
        tool_call=tool_call,
        allowed_business_view_ids=business_view_ids,
    ):
        return None

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


def _projection_business_view_allowed(
    *,
    metadata: JsonObject,
    tool_call: ToolCall | None,
    allowed_business_view_ids: set[str] | None,
) -> bool:
    if allowed_business_view_ids is None:
        return True
    business_view_id = metadata.get("business_view_id")
    if not isinstance(business_view_id, str) and tool_call is not None:
        candidate = tool_call.arguments.get("business_view_id")
        business_view_id = candidate if isinstance(candidate, str) else None
    if not isinstance(business_view_id, str) or not business_view_id:
        return True
    return business_view_id in allowed_business_view_ids


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
    registered_tools = set(tool_registry.names())
    summary = AgentRuntimeSnapshotSummary(
        runs=len(snapshot.runs),
        agents=len(snapshot.agents),
        memory=len(snapshot.memory),
        events=sum(len(run.events) for run in snapshot.runs),
        steps=sum(len(run.steps) for run in snapshot.runs),
        approvals=sum(len(run.approvals) for run in snapshot.runs),
        artifacts=sum(len(run.artifacts) for run in snapshot.runs),
        pending_tool_calls=sum(len(run.pending_tool_calls) for run in snapshot.runs),
    )

    if snapshot.version != "agent-runtime.snapshot.v1":
        errors.append(f"unsupported snapshot version: {snapshot.version}")

    _append_duplicate_errors("run", [run.id for run in snapshot.runs], errors)
    _append_duplicate_errors("agent", [agent.id for agent in snapshot.agents], errors)
    _append_duplicate_errors("memory", [entry.id for entry in snapshot.memory], errors)
    event_ids = [event.id for run in snapshot.runs for event in run.events]
    artifact_ids = [artifact.id for run in snapshot.runs for artifact in run.artifacts]
    _append_duplicate_errors("event", event_ids, errors)
    _append_duplicate_errors("artifact", artifact_ids, errors)

    if not any(agent.id == "default" for agent in snapshot.agents):
        warnings.append("default agent is missing and will be recreated")

    for agent in snapshot.agents:
        _validate_agent_snapshot(agent, registered_tools, errors)

    for run in snapshot.runs:
        _validate_run_snapshot(run, registered_tools, errors, warnings)

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
    registered_tools: set[str],
    errors: list[str],
) -> None:
    if not agent.id:
        errors.append("agent id must not be empty")
    unknown_tools = sorted(
        {tool_name for tool_name in agent.tool_names if tool_name not in registered_tools}
    )
    if unknown_tools:
        errors.append(f"agent {agent.id} references unknown tools: {', '.join(unknown_tools)}")


def _validate_run_snapshot(
    run: RunState,
    registered_tools: set[str],
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
        _validate_step_snapshot(run, step, approval_id_set, registered_tools, errors)
    for approval in run.approvals:
        _validate_approval_snapshot(run, approval, step_id_set, errors)
    for call in run.pending_tool_calls:
        if call.name not in registered_tools:
            errors.append(f"run {run.id} pending call references unknown tool: {call.name}")

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
    registered_tools: set[str],
    errors: list[str],
) -> None:
    if step.run_id != run.id:
        errors.append(f"step {step.id} belongs to {step.run_id}, expected {run.id}")
    if step.approval_id is not None and step.approval_id not in approval_ids:
        errors.append(f"step {step.id} references missing approval {step.approval_id}")
    if step.tool_call is not None and step.tool_call.name not in registered_tools:
        errors.append(f"step {step.id} references unknown tool: {step.tool_call.name}")


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
        description="外部 RAG / NL2SQL と承認フローを使う既定 Agent。",
        instructions="業務データは外部ツール経由で取得し、根拠と監査情報を残す。",
        tool_names=tool_registry.names(),
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
        dsn = settings.agent_runtime_oracle_dsn
        user = settings.agent_runtime_oracle_user
        password = settings.agent_runtime_oracle_password
        missing = [
            name
            for name, value in {
                "AGENT_RUNTIME_ORACLE_DSN": dsn,
                "AGENT_RUNTIME_ORACLE_USER": user,
                "AGENT_RUNTIME_ORACLE_PASSWORD": password,
            }.items()
            if not value
        ]
        if missing:
            raise RuntimeError(
                "Oracle-backed Agent Runtime repository requires: " + ", ".join(missing)
            )
        if dsn is None or user is None or password is None:
            raise RuntimeError("Oracle-backed Agent Runtime repository configuration is invalid")
        if backend == "oracle_normalized":
            return AgentRuntimeOracleNormalizedRepository(
                dsn=dsn,
                user=user,
                password=password,
                table_name=settings.agent_runtime_oracle_table,
                checkpoint_key=settings.agent_runtime_oracle_checkpoint_key,
                projection_prefix=settings.agent_runtime_oracle_projection_prefix,
                projection_retention_days=(settings.agent_runtime_oracle_projection_retention_days),
                projection_write_mode=settings.agent_runtime_oracle_projection_write_mode,
                create_schema=settings.agent_runtime_oracle_create_schema,
            )
        return AgentRuntimeOracleCheckpointRepository(
            dsn=dsn,
            user=user,
            password=password,
            table_name=settings.agent_runtime_oracle_table,
            checkpoint_key=settings.agent_runtime_oracle_checkpoint_key,
            create_schema=settings.agent_runtime_oracle_create_schema,
        )
    if backend in {"memory", "in_memory", "file", "file_snapshot"}:
        snapshot_path = (
            settings.agent_runtime_snapshot_path
            if backend in {"file", "file_snapshot"} or settings.agent_runtime_snapshot_path
            else None
        )
        return AgentRuntimeRepository(snapshot_path=snapshot_path)
    raise RuntimeError(f"unsupported Agent Runtime repository backend: {backend}")


runtime_repository: AgentRuntimeRepositoryContract = build_runtime_repository()
