"""Agent正本のnamespace・保存取引・実削除と全文の所属を対応させる。"""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import asdict
from typing import Any

import pytest
from checkpoint_support import CheckpointConnection, CheckpointTable
from pr_backend_core.observability.trace_bodies import BodyOwner, prepare_body

from app.features.agent import runtime_checkpoint_store as checkpoint_store
from app.features.agent import trace_bodies
from app.features.agent.runtime import (
    AgentRuntimeOracleCheckpointRepository,
    RunCreateRequest,
)


class Cursor:
    def __init__(self) -> None:
        self.primary_written = False
        self.calls: list[tuple[str, Any]] = []

    def setinputsizes(self, **_: Any) -> None:
        pass

    def executemany(self, sql: str, rows: Any) -> None:
        assert "MERGE INTO AGENT_RUNTIME_ENTITIES" in sql
        self.primary_written = True
        self.calls.append((sql, rows))

    def execute(self, sql: str, parameters: Any = None) -> None:
        self.calls.append((sql, parameters))


def test_body_owner_binds_checkpoint_namespace_and_run_without_ambiguous_joining() -> None:
    owner = trace_bodies.run_body_owner("checkpoint-a", "run-a")
    assert owner == trace_bodies.run_body_owner("checkpoint-a", "run-a")
    assert owner != trace_bodies.run_body_owner("checkpoint-b", "run-a")
    assert trace_bodies.run_body_owner("a:b", "c") != trace_bodies.run_body_owner("a", "b:c")


@pytest.mark.parametrize("fail", [False, True])
def test_entity_write_registers_only_runs_after_actual_merge_and_preserves_failure_reason(
    monkeypatch: pytest.MonkeyPatch, fail: bool
) -> None:
    body = prepare_body("synthetic full body", part_characters=4)
    details = {
        "span-a": {
            "call": {"input": {"status": "recorded", "body_reference": asdict(body.reference)}}
        }
    }
    text = json.dumps({"span_details": details})
    cursor = Cursor()
    owners: list[BodyOwner] = []

    class Persistence:
        def register(self, actual: Any, owner: BodyOwner, snapshot: Mapping[str, Any]) -> Any:
            assert actual is cursor and actual.primary_written
            assert snapshot == details
            owners.append(owner)
            return {body.reference.body_id: "queue_full"} if fail else {}

    monkeypatch.setattr(trace_bodies, "_PERSISTENCE", Persistence())
    checkpoint_store.write_entities(
        cursor, "checkpoint-a", {("run", "run-a"): text, ("agent", "agent-a"): "{}"}, version=2
    )
    assert owners == [trace_bodies.run_body_owner("checkpoint-a", "run-a")]
    assert len(cursor.calls) == (2 if fail else 1)
    if fail:
        saved = json.loads(cursor.calls[-1][1]["entity_json"])
        assert saved["span_details"]["span-a"]["call"]["input"]["status"] == "incomplete"
        assert saved["span_details"]["span-a"]["call"]["input"]["failure_code"] == "queue_full"
        assert json.loads(text)["span_details"]["span-a"]["call"]["input"]["status"] == "recorded"


def test_actual_run_tombstone_releases_only_namespaced_owner_without_live_writer(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cursor = Cursor()
    owners: list[BodyOwner] = []

    def release(actual: Any, owner: BodyOwner) -> None:
        assert actual is cursor and cursor.primary_written
        owners.append(owner)

    monkeypatch.setattr(trace_bodies, "_PERSISTENCE", None)
    monkeypatch.setattr(trace_bodies, "release_body_owner", release)
    checkpoint_store.write_entities(
        cursor, "checkpoint-a", {("run", "run-a"): None, ("agent", "agent-a"): None}, version=2
    )
    assert owners == [trace_bodies.run_body_owner("checkpoint-a", "run-a")]


@pytest.mark.parametrize("head_transition", [False, True])
@pytest.mark.parametrize("commit_fails", [False, True])
def test_repository_confirms_body_only_after_successful_commit(
    monkeypatch: pytest.MonkeyPatch, head_transition: bool, commit_fails: bool
) -> None:
    table = CheckpointTable()
    repository = AgentRuntimeOracleCheckpointRepository(connect_factory=table.connect)
    run = repository.create_builtin_run(RunCreateRequest(goal="合成の質問", agent_id="default"))
    events: list[str] = []

    class Persistence:
        def register(self, _cursor: Any, _owner: BodyOwner, _details: Any) -> dict[str, str]:
            events.append("register")
            return {}

        def confirm_saved(self, _details: Any) -> None:
            events.append("confirm")

    def commit(_connection: Any) -> None:
        events.append("commit")
        if commit_fails:
            raise RuntimeError("synthetic commit failure")

    monkeypatch.setattr(trace_bodies, "_PERSISTENCE", Persistence())
    monkeypatch.setattr(CheckpointConnection, "commit", commit)

    def transition() -> None:
        if head_transition:
            assert repository.claim_control_plane_run("worker-a", lease_seconds=30) is not None
        else:
            repository.cancel_run(run.id)

    if commit_fails:
        with pytest.raises(RuntimeError, match="synthetic commit failure"):
            transition()
        assert events == ["register", "commit"]
    else:
        transition()
        assert events == ["register", "commit", "confirm"]
    assert repository.trace_body_namespace() == "default"
