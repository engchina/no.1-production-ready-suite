"""保存先の snapshot の読み込みを Run 単位にし、起動を止めない（#853）。

1 件の Run の不整合（終わった Run に残った pending の承認など）は直し、直せない Run は元の JSON
ごと退避して、backend の起動（module の import）を止めない。snapshot 全体が読めないときだけ、
`auto` は memory で起動して案内し、明示した `oracle_*` / `file` は直し方を書いて止める。
起動時の DB の接続のエラーは上限付きで再試行する。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from pytest import MonkeyPatch

from app.features.agent import control_plane_store, runtime, storage_backend, storage_status
from app.features.agent.control_plane_store import MemoryItemStore
from app.features.agent.runtime import (
    AgentRuntimeCheckpointCorruptError,
    AgentRuntimeOracleCheckpointRepository,
    AgentRuntimeRepository,
    AgentRuntimeSnapshot,
    ApprovalRequest,
    ApprovalStatus,
    RunEventType,
    RunState,
    RunStatus,
    RunStep,
    StepStatus,
    load_snapshot_tolerant,
)
from app.features.agent.tools import ToolCall
from app.settings import get_settings

# --------------------------------------------------------------------------------------------
# fake の Oracle（AGENT_RUNTIME_CHECKPOINTS の 1 行だけ）
# --------------------------------------------------------------------------------------------


class _Checkpoints:
    def __init__(self, snapshot_json: str | None = None) -> None:
        self.rows: dict[str, str] = {}
        if snapshot_json is not None:
            self.rows["default"] = snapshot_json
        self.connects = 0

    def connect(self) -> _Connection:
        self.connects += 1
        return _Connection(self)

    def stored(self) -> dict[str, Any]:
        value: dict[str, Any] = json.loads(self.rows["default"])
        return value


class _Cursor:
    def __init__(self, store: _Checkpoints) -> None:
        self._store = store
        self._row: tuple[str] | None = None

    def __enter__(self) -> _Cursor:
        return self

    def __exit__(self, *_args: object) -> None:
        return None

    def setinputsizes(self, **_sizes: object) -> None:
        return None

    def execute(self, statement: str, **params: Any) -> None:
        normalized = " ".join(statement.upper().split())
        if normalized.startswith("SELECT SNAPSHOT_JSON"):
            value = self._store.rows.get(str(params["checkpoint_key"]))
            self._row = (value,) if value is not None else None
        elif normalized.startswith("MERGE INTO"):
            self._store.rows[str(params["checkpoint_key"])] = str(params["snapshot_json"])

    def fetchone(self) -> tuple[str] | None:
        return self._row


class _Connection:
    def __init__(self, store: _Checkpoints) -> None:
        self._store = store

    def __enter__(self) -> _Connection:
        return self

    def __exit__(self, *_args: object) -> None:
        return None

    def cursor(self) -> _Cursor:
        return _Cursor(self._store)

    def commit(self) -> None:
        return None


# --------------------------------------------------------------------------------------------
# snapshot の部品
# --------------------------------------------------------------------------------------------


def _approval_run(
    run_id: str,
    *,
    status: RunStatus,
    approval_status: ApprovalStatus = ApprovalStatus.PENDING,
    step_id: str | None = None,
) -> RunState:
    """承認を 1 つ持つ Run（`step_id` を変えると、承認が無い step を指す）。"""
    call = ToolCall(name="nl2sql__nl2sql_query", arguments={"question": "売上"}, trace_id="c1")
    step = RunStep(
        id=f"step_{run_id}",
        run_id=run_id,
        status=StepStatus.WAITING_APPROVAL,
        tool_call=call,
        approval_id=f"approval_{run_id}",
    )
    approval = ApprovalRequest(
        id=f"approval_{run_id}",
        run_id=run_id,
        step_id=step_id or step.id,
        tool_call=call,
        status=approval_status,
        reason="業務 DB へ SQL を実行します",
    )
    return RunState(
        id=run_id,
        goal="売上を調べて",
        agent_id="default",
        runtime_id="builtin",
        status=status,
        steps=[step],
        approvals=[approval],
        metadata={"_builtin_sdk_state": "{}"} if status == RunStatus.WAITING_APPROVAL else {},
    )


def _snapshot_json(*runs: RunState, extra_runs: list[Any] | None = None) -> str:
    data = json.loads(AgentRuntimeSnapshot(runs=list(runs)).model_dump_json())
    data["runs"].extend(extra_runs or [])
    return json.dumps(data)


def _oracle(monkeypatch: MonkeyPatch, store: _Checkpoints, backend: str) -> None:
    monkeypatch.setattr(get_settings(), "agent_runtime_repository_backend", backend)
    monkeypatch.setattr(storage_backend, "database_readiness", lambda _settings: "ok", raising=True)
    monkeypatch.setattr(runtime, "connect_platform_oracle", store.connect)


# --------------------------------------------------------------------------------------------
# Run 単位の修復
# --------------------------------------------------------------------------------------------


@pytest.mark.parametrize("backend", ["auto", "oracle_checkpoint"])
def test_terminal_run_with_pending_approval_no_longer_blocks_startup(
    monkeypatch: MonkeyPatch, backend: str
) -> None:
    """実環境で起きた「terminal run ... has pending approvals」で起動が止まらない。"""
    store = _Checkpoints(_snapshot_json(_approval_run("run_done", status=RunStatus.COMPLETED)))
    _oracle(monkeypatch, store, backend)

    repository = runtime.build_runtime_repository()

    assert isinstance(repository, AgentRuntimeOracleCheckpointRepository)
    run = repository.get_run("run_done")
    assert run.status == RunStatus.COMPLETED
    assert run.approvals[0].status == ApprovalStatus.CANCELLED
    assert run.approvals[0].decided_by == "system:storage-repair"
    assert run.steps[0].status == StepStatus.CANCELLED
    repair_events = [
        event for event in run.events if event.payload.get("source") == "storage_repair"
    ]
    assert repair_events[-1].payload["repairs"] == ["terminal_run_pending_approvals_cancelled"]
    assert repository.storage_health().repaired_runs == 1
    # 直した状態を読み込みの直後に保存する（次の起動・dispatcher は直した状態を読む）。
    stored_run = store.stored()["runs"][0]
    assert stored_run["approvals"][0]["status"] == "cancelled"
    assert repository.validate_snapshot(repository.export_snapshot()).valid is True


def test_waiting_approval_without_pending_approval_is_failed_not_resumed() -> None:
    run = _approval_run(
        "run_wait", status=RunStatus.WAITING_APPROVAL, approval_status=ApprovalStatus.APPROVED
    )

    snapshot, report = load_snapshot_tolerant(_snapshot_json(run))

    loaded = snapshot.runs[0]
    assert loaded.status == RunStatus.FAILED
    assert "_builtin_sdk_state" not in loaded.metadata
    assert any(
        event.type == RunEventType.RUNTIME_FAILED
        and event.payload.get("error_code") == "runtime.inconsistent_state"
        for event in loaded.events
    )
    assert report.repaired[0].repairs == ["waiting_approval_without_pending_failed"]


def test_mismatched_run_ids_and_missing_approval_references_are_repaired() -> None:
    run = _approval_run("run_ids", status=RunStatus.QUEUED)
    run.approvals = []
    run.steps[0].run_id = "run_other"

    snapshot, report = load_snapshot_tolerant(_snapshot_json(run))

    loaded = snapshot.runs[0]
    assert loaded.steps[0].run_id == "run_ids"
    assert loaded.steps[0].approval_id is None
    assert loaded.steps[0].status == StepStatus.CANCELLED
    assert report.repaired[0].repairs == [
        "run_id_mismatch_fixed",
        "missing_approval_reference_cleared",
    ]


def test_consistent_snapshot_is_loaded_without_repairs_or_writes(monkeypatch: MonkeyPatch) -> None:
    run = _approval_run("run_ok", status=RunStatus.WAITING_APPROVAL)
    original = _snapshot_json(run)
    store = _Checkpoints(original)
    _oracle(monkeypatch, store, "oracle_checkpoint")

    repository = runtime.build_runtime_repository()

    assert repository.get_run("run_ok").status == RunStatus.WAITING_APPROVAL
    assert repository.storage_health().model_dump() == {
        "repaired_runs": 0,
        "skipped_runs": 0,
        "skipped_agents": 0,
    }
    assert store.rows["default"] == original


# --------------------------------------------------------------------------------------------
# 直せない Run の退避
# --------------------------------------------------------------------------------------------


def test_unrepairable_runs_are_quarantined_and_kept_on_the_next_save(
    monkeypatch: MonkeyPatch,
) -> None:
    broken_schema = {"id": "run_broken", "goal": "x", "agent_id": "default", "status": "unknown"}
    missing_step = _approval_run("run_missing_step", status=RunStatus.FAILED, step_id="step_x")
    duplicate = _approval_run(
        "run_ok", status=RunStatus.COMPLETED, approval_status=ApprovalStatus.APPROVED
    )
    store = _Checkpoints(
        _snapshot_json(
            duplicate,
            missing_step,
            extra_runs=[broken_schema, json.loads(duplicate.model_dump_json())],
        )
    )
    _oracle(monkeypatch, store, "oracle_checkpoint")

    repository = runtime.build_runtime_repository()

    assert [run.id for run in repository.list_runs()] == ["run_ok"]
    health = repository.storage_health()
    assert health.skipped_runs == 3
    quarantined = {record.id: record for record in repository.export_snapshot().quarantined}
    assert quarantined["run_broken"].raw == broken_schema
    assert quarantined["run_broken"].reasons == ["status: enum"]
    assert "無いステップ" in quarantined["run_missing_step"].reasons[0]
    # 退避した JSON は、ほかの変更の保存でも消えない。
    repository.persist_control_plane_state()
    stored = store.stored()
    assert {record["id"] for record in stored["quarantined"]} == {
        "run_broken",
        "run_missing_step",
        "run_ok",
    }
    assert [run["id"] for run in stored["runs"]] == ["run_ok"]

    # 次の起動でも退避したまま（件数も同じ）。
    reloaded = AgentRuntimeOracleCheckpointRepository(connect_factory=store.connect)
    assert reloaded.storage_health().skipped_runs == 3
    assert reloaded.storage_health().repaired_runs == 0


def test_agent_with_invalid_schema_is_quarantined() -> None:
    data = json.loads(AgentRuntimeSnapshot().model_dump_json())
    data["agents"] = [{"id": "", "name": "x"}, {"name": 1}]

    snapshot, report = load_snapshot_tolerant(json.dumps(data))

    assert snapshot.agents == []
    assert [record.kind for record in report.quarantined] == ["agent", "agent"]
    repository = AgentRuntimeRepository()
    repository.replace_snapshot(snapshot)
    assert repository.storage_health().skipped_agents == 2


# --------------------------------------------------------------------------------------------
# snapshot 全体が読めない
# --------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "payload",
    ["{not json", "[]", json.dumps({"version": "agent-control-plane.snapshot.v9", "runs": []})],
    ids=["broken-json", "not-object", "unknown-version"],
)
def test_corrupt_checkpoint_under_auto_starts_in_memory_with_a_clear_status(
    monkeypatch: MonkeyPatch, payload: str
) -> None:
    store = _Checkpoints(payload)
    _oracle(monkeypatch, store, "auto")

    repository = runtime.build_runtime_repository()

    assert type(repository) is AgentRuntimeRepository
    assert storage_backend.fallback_reason() == "checkpoint_invalid"
    # 壊れた checkpoint を上書きしない。
    assert store.rows["default"] == payload
    control_plane_store.set_control_plane_store(MemoryItemStore())
    try:
        status = storage_status.runtime_storage_status()
    finally:
        control_plane_store.set_control_plane_store(None)
    assert status.persistent is False
    assert status.reason == "checkpoint_invalid"


def test_corrupt_checkpoint_under_explicit_oracle_refuses_with_an_actionable_message(
    monkeypatch: MonkeyPatch,
) -> None:
    store = _Checkpoints("{not json")
    _oracle(monkeypatch, store, "oracle_checkpoint")

    with pytest.raises(AgentRuntimeCheckpointCorruptError) as caught:
        runtime.build_runtime_repository()

    message = str(caught.value)
    assert "CHECKPOINT_KEY='default'" in message
    assert "バックアップと復元" in message
    assert "AGENT_RUNTIME_REPOSITORY_BACKEND=auto" in message
    assert storage_backend.fell_back_to_memory() is False


def test_file_snapshot_is_loaded_per_run_and_refuses_only_when_corrupt(tmp_path: Path) -> None:
    path = tmp_path / "snapshot.json"
    path.write_text(_snapshot_json(_approval_run("run_done", status=RunStatus.CANCELLED)))

    repository = AgentRuntimeRepository(snapshot_path=path)

    assert repository.get_run("run_done").approvals[0].status == ApprovalStatus.CANCELLED
    assert json.loads(path.read_text())["runs"][0]["approvals"][0]["status"] == "cancelled"

    path.write_text("{not json")
    with pytest.raises(RuntimeError, match="snapshot.json"):
        AgentRuntimeRepository(snapshot_path=path)


def test_dispatcher_claim_tolerates_an_inconsistent_run(monkeypatch: MonkeyPatch) -> None:
    store = _Checkpoints()
    repository = AgentRuntimeOracleCheckpointRepository(connect_factory=store.connect)
    queued = RunState(
        id="run_queued", goal="g", agent_id="default", runtime_id="builtin", status=RunStatus.QUEUED
    )
    store.rows["default"] = _snapshot_json(
        queued, _approval_run("run_done", status=RunStatus.COMPLETED)
    )

    claimed = repository.claim_control_plane_run("worker-1", lease_seconds=60)

    assert claimed is not None and claimed.id == "run_queued"


# --------------------------------------------------------------------------------------------
# 起動時の DB の接続の再試行
# --------------------------------------------------------------------------------------------


class _FlakyConnect:
    """最初の `failures` 回は接続のエラー（DPY-6005）を投げる。"""

    def __init__(self, store: _Checkpoints, failures: int) -> None:
        self._store = store
        self.failures = failures
        self.calls = 0

    def __call__(self) -> _Connection:
        self.calls += 1
        if self.calls <= self.failures:
            raise RuntimeError("DPY-6005: cannot connect to database")
        return self._store.connect()


def _record_sleeps(monkeypatch: MonkeyPatch) -> list[float]:
    sleeps: list[float] = []
    monkeypatch.setattr(runtime, "_retry_sleep", sleeps.append)
    return sleeps


def test_startup_retries_connection_errors_with_backoff(monkeypatch: MonkeyPatch) -> None:
    store = _Checkpoints(_snapshot_json())
    _oracle(monkeypatch, store, "oracle_checkpoint")
    flaky = _FlakyConnect(store, failures=2)
    monkeypatch.setattr(runtime, "connect_platform_oracle", flaky)
    monkeypatch.setattr(get_settings(), "agent_runtime_storage_connect_retries", 2)
    monkeypatch.setattr(get_settings(), "agent_runtime_storage_connect_retry_delay_seconds", 2.0)
    sleeps = _record_sleeps(monkeypatch)

    repository = runtime.build_runtime_repository()

    assert isinstance(repository, AgentRuntimeOracleCheckpointRepository)
    assert flaky.calls == 3
    # 2 秒・4 秒の指数 backoff（後半を jitter）。
    assert len(sleeps) == 2
    assert 1.0 <= sleeps[0] <= 2.0
    assert 2.0 <= sleeps[1] <= 4.0


def test_startup_gives_up_after_the_retries(monkeypatch: MonkeyPatch) -> None:
    store = _Checkpoints(_snapshot_json())
    monkeypatch.setattr(get_settings(), "agent_runtime_storage_connect_retries", 1)
    sleeps = _record_sleeps(monkeypatch)

    _oracle(monkeypatch, store, "oracle_checkpoint")
    monkeypatch.setattr(runtime, "connect_platform_oracle", _FlakyConnect(store, failures=99))
    with pytest.raises(RuntimeError) as caught:
        runtime.build_runtime_repository()
    assert "DPY-6005" in str(caught.value)
    assert "PLATFORM_ORACLE_" in str(caught.value)
    assert len(sleeps) == 1

    # auto は #851 のとおり memory で起動する。
    storage_backend.reset()
    _oracle(monkeypatch, store, "auto")
    monkeypatch.setattr(runtime, "connect_platform_oracle", _FlakyConnect(store, failures=99))
    repository = runtime.build_runtime_repository()
    assert type(repository) is AgentRuntimeRepository
    assert storage_backend.fallback_reason() == "connection"


def test_startup_does_not_retry_non_connection_errors(monkeypatch: MonkeyPatch) -> None:
    calls: list[int] = []

    def denied() -> _Connection:
        calls.append(1)
        raise RuntimeError("ORA-01031: insufficient privileges")

    _oracle(monkeypatch, _Checkpoints(), "auto")
    monkeypatch.setattr(runtime, "connect_platform_oracle", denied)
    sleeps = _record_sleeps(monkeypatch)

    with pytest.raises(RuntimeError, match="ORA-01031"):
        runtime.build_runtime_repository()
    assert calls == [1]
    assert sleeps == []


def test_storage_api_reports_repaired_and_skipped_runs(monkeypatch: MonkeyPatch) -> None:
    from security_support import client

    store = _Checkpoints(
        _snapshot_json(
            _approval_run("run_done", status=RunStatus.COMPLETED),
            extra_runs=[{"id": "run_broken"}],
        )
    )
    _oracle(monkeypatch, store, "oracle_checkpoint")
    repository = runtime.build_runtime_repository()
    monkeypatch.setattr(runtime, "runtime_repository", repository)
    control_plane_store.set_control_plane_store(MemoryItemStore())
    try:
        response = client.get("/api/runtime/storage")
    finally:
        control_plane_store.set_control_plane_store(None)

    assert response.status_code == 200, response.text
    data = response.json()["data"]
    assert data["repaired_runs"] == 1
    assert data["skipped_runs"] == 1
    assert data["skipped_agents"] == 0


def test_dispatcher_keeps_running_after_a_failed_iteration(monkeypatch: MonkeyPatch) -> None:
    """1 回の claim の失敗（DB の一時的な障害など）で runtime-dispatcher を止めない。"""
    import asyncio

    from app.features.agent import runtime_dispatcher

    outcomes: list[object] = [
        RuntimeError("DPY-6005"),
        RuntimeError("DPY-6005"),
        True,
        RuntimeError("DPY-6005"),
        asyncio.CancelledError(),
    ]
    sleeps: list[float] = []

    async def dispatch_once(_worker_id: str) -> bool:
        outcome = outcomes.pop(0)
        if isinstance(outcome, BaseException):
            raise outcome
        return bool(outcome)

    async def sleep(seconds: float) -> None:
        sleeps.append(seconds)

    monkeypatch.setattr(runtime_dispatcher, "dispatch_once", dispatch_once)
    monkeypatch.setattr(runtime_dispatcher, "_sleep", sleep)
    monkeypatch.setattr(get_settings(), "agent_runtime_dispatch_poll_seconds", 1.0)

    with pytest.raises(asyncio.CancelledError):
        asyncio.run(runtime_dispatcher.run_forever())

    # 失敗が続くと待ちを 2 倍にし、成功したら戻す。
    assert sleeps == [1.0, 2.0, 1.0]
