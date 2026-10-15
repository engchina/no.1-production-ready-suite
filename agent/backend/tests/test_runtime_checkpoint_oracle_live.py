"""Run・業務 Agent の保存先（head と 1 件 = 1 行）を、実の Oracle で確かめる（#1916・#1939）。

- 2 つのプロセスが同じ保存先に書いても、互いの更新を消さない（#1916）。
- migration 016 が、以前の全体の snapshot（1 行の JSON）を Run・業務 Agent の行に移し、元の
  snapshot を残す（#1939）。
- Run が 1,000 件あっても、会話の削除・名前の変更・フィードバック・取り消しが 1 秒以内に終わる
  （#1939。以前の全体の書き戻しの時間も同じ件数で測って出力する）。

CI の実 Oracle の結合テスト（`.github/workflows/oracle-integration.yml`。システムテーブルを
作った後の Oracle AI Database Free）だけで実行する（`AGENT_RUN_ORACLE_INTEGRATION=1`。接続は
`platform/.env` の `PLATFORM_ORACLE_*`）。ほかでは skip する。テストごとに別の CHECKPOINT_KEY で
書き、終わったら消す（並列でも互いの行を読まない）。
"""

from __future__ import annotations

import json
import os
import time
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import uuid4

import oracledb
import pytest

from app.features.agent import runtime
from app.features.agent import runtime_checkpoint_store as checkpoint_store
from app.features.agent.runtime import (
    RUNTIME_CHECKPOINT_TABLE,
    AgentProfile,
    AgentRuntimeOracleCheckpointRepository,
    AgentRuntimeSnapshot,
    Artifact,
    FeedbackRating,
    FeedbackReason,
    RunEvent,
    RunEventType,
    RunFeedback,
    RunState,
    RunStatus,
)
from app.features.agent.runtime_checkpoint_store import ENTITY_TABLE, WRITER_ID
from app.oracle_connection import connect_platform_oracle
from app.system_schema import MIGRATIONS

pytestmark = pytest.mark.skipif(
    os.environ.get("AGENT_RUN_ORACLE_INTEGRATION") != "1",
    reason="実 Oracle の結合テスト（AGENT_RUN_ORACLE_INTEGRATION=1）だけで実行する",
)

MIGRATION_016 = next(item for item in MIGRATIONS if item.name == "20261015_016_runtime_entities")


def _delete_key(value: str) -> None:
    with connect_platform_oracle() as connection, connection.cursor() as cursor:
        for table in (ENTITY_TABLE, RUNTIME_CHECKPOINT_TABLE):
            cursor.execute(
                f"DELETE FROM {table} WHERE CHECKPOINT_KEY = :checkpoint_key",  # nosec B608
                {"checkpoint_key": value},
            )
        connection.commit()


@pytest.fixture
def checkpoint_key() -> Iterator[str]:
    value = f"live-{uuid4().hex}"
    yield value
    _delete_key(value)


def _head(checkpoint_key: str) -> tuple[dict[str, Any], int, str]:
    with connect_platform_oracle() as connection, connection.cursor() as cursor:
        cursor.execute(
            f"SELECT SNAPSHOT_JSON, VERSION_NO, WRITER_ID FROM {RUNTIME_CHECKPOINT_TABLE} "  # nosec B608
            "WHERE CHECKPOINT_KEY = :checkpoint_key",
            {"checkpoint_key": checkpoint_key},
        )
        row = cursor.fetchone()
        assert row is not None
        return json.loads(row[0].read()), int(row[1]), str(row[2])


def _entities(checkpoint_key: str) -> dict[tuple[str, str], tuple[str | None, int]]:
    with connect_platform_oracle() as connection, connection.cursor() as cursor:
        cursor.execute(
            "SELECT ENTITY_KIND, ENTITY_ID, ENTITY_JSON, CHANGE_VERSION "  # nosec B608
            f"FROM {ENTITY_TABLE} WHERE CHECKPOINT_KEY = :checkpoint_key",
            {"checkpoint_key": checkpoint_key},
        )
        return {
            (str(kind), str(entity_id)): (text.read() if text is not None else None, int(version))
            for kind, entity_id, text, version in cursor.fetchall()
        }


def test_two_processes_keep_each_others_updates_in_oracle(checkpoint_key: str) -> None:
    # どちらも行が無い時点で読み込んだ 2 つのプロセス（同じ .env の 2 つの backend）。
    first = AgentRuntimeOracleCheckpointRepository(checkpoint_key=checkpoint_key)
    second = AgentRuntimeOracleCheckpointRepository(checkpoint_key=checkpoint_key)

    # head を作る INSERT が重なる（後の方は ORA-00001 で、作られた head を lock して読む）。
    first.create_agent(AgentProfile(id="agent_live_first", name="一つ目"))
    second.create_agent(AgentProfile(id="agent_live_second", name="二つ目"))
    # first は版 1 の時点のメモリで書くが、保存先は版 2（second の業務 Agent を取り込む）。
    first.create_agent(AgentProfile(id="agent_live_third", name="三つ目"))

    header, version, writer_id = _head(checkpoint_key)
    rows = _entities(checkpoint_key)
    assert {
        ("agent", "agent_live_first"),
        ("agent", "agent_live_second"),
        ("agent", "agent_live_third"),
    } <= set(rows)
    assert header["storage"] == "entities"
    assert version == 3
    assert writer_id == WRITER_ID
    # 2 つのプロセスのメモリも、相手の更新を取り込んでいる。
    assert "agent_live_second" in {agent.id for agent in first.list_agents()}
    assert "agent_live_first" in {agent.id for agent in second.list_agents()}

    # 業務 Agent の削除は行を消さずに印を残し、別のプロセスのメモリからも消える。
    second.delete_agent("agent_live_first")
    first.create_agent(AgentProfile(id="agent_live_fourth", name="四つ目"))
    assert _entities(checkpoint_key)[("agent", "agent_live_first")][0] is None
    assert "agent_live_first" not in {agent.id for agent in first.list_agents()}


def _legacy_run(run_id: str) -> dict[str, Any]:
    run = RunState(
        id=run_id,
        goal="規程を調べて",
        agent_id="default",
        runtime_id="builtin",
        status=RunStatus.RUNNING,
    )
    return run.model_dump(mode="json")


def test_migration_016_moves_the_legacy_snapshot_without_losing_data(
    checkpoint_key: str,
) -> None:
    """以前の全体の snapshot を行に移し、元の snapshot を残す（もう一度当てても変えない）。"""
    agent = AgentProfile(id="agent_legacy", name="移す前の業務 Agent").model_dump(mode="json")
    duplicate = {**_legacy_run("run_legacy_a"), "goal": "同じ id の 2 つ目"}
    legacy = {
        "version": "agent-control-plane.snapshot.v2",
        "runs": [_legacy_run("run_legacy_a"), _legacy_run("run_legacy_b"), duplicate, {"x": 1}],
        "agents": [agent],
        "quarantined": [{"kind": "run", "id": "run_old", "reasons": ["前の退避"], "raw": {}}],
        "memory": [],
        "control_plane_state": {},
    }
    legacy_text = json.dumps(legacy, ensure_ascii=False)
    with connect_platform_oracle() as connection, connection.cursor() as cursor:
        cursor.setinputsizes(snapshot_json=oracledb.DB_TYPE_CLOB)
        cursor.execute(
            f"INSERT INTO {RUNTIME_CHECKPOINT_TABLE} "  # nosec B608
            "(CHECKPOINT_KEY, SNAPSHOT_JSON, VERSION_NO) "
            "VALUES (:checkpoint_key, :snapshot_json, 4)",
            {"checkpoint_key": checkpoint_key, "snapshot_json": legacy_text},
        )
        connection.commit()
    # 移す前の形は読まない（システムテーブルの更新へ案内する）。
    with pytest.raises(runtime.AgentRuntimeMigrationRequiredError):
        AgentRuntimeOracleCheckpointRepository(checkpoint_key=checkpoint_key)

    for _attempt in range(2):
        with connect_platform_oracle() as connection, connection.cursor() as cursor:
            # 表と索引は作成済み（システムテーブル）。データを移す文だけを当てる。
            for statement in MIGRATION_016.statements[2:]:
                cursor.execute(statement)
            connection.commit()

        header, version, writer_id = _head(checkpoint_key)
        rows = _entities(checkpoint_key)
        assert header["storage"] == "entities"
        assert "runs" not in header and "agents" not in header
        assert header["quarantined"][0]["id"] == "run_old"
        assert version == 5
        assert writer_id == "migration:20261015_016"
        assert set(rows) == {
            ("run", "run_legacy_a"),
            ("run", "run_legacy_b"),
            ("agent", "agent_legacy"),
            ("legacy_snapshot", "20261015_016"),
        }
        # 同じ id の 2 つ目・id の無い要素は移さず、元の snapshot に残る。
        run_a = json.loads(rows[("run", "run_legacy_a")][0] or "{}")
        assert run_a["goal"] == "規程を調べて"
        assert rows[("run", "run_legacy_a")][1] == 5
        assert json.loads(rows[("legacy_snapshot", "20261015_016")][0] or "{}") == legacy

    repository = AgentRuntimeOracleCheckpointRepository(checkpoint_key=checkpoint_key)
    assert {run.id for run in repository.list_runs()} == {"run_legacy_a", "run_legacy_b"}
    assert "agent_legacy" in {item.id for item in repository.list_agents()}
    assert repository.storage_health().skipped_runs == 1
    repository.cancel_run("run_legacy_a")
    assert _head(checkpoint_key)[1] == 6
    # 移す前の snapshot は残る（アプリは読まず、書き込みでも消さない）。
    assert ("legacy_snapshot", "20261015_016") in _entities(checkpoint_key)


def _synthetic_runs(count: int) -> list[RunState]:
    base = datetime(2026, 10, 1, tzinfo=UTC)
    runs: list[RunState] = []
    for index in range(count):
        created = base + timedelta(minutes=index)
        run = RunState(
            id=f"run_perf_{index:05d}",
            goal=f"第 {index} 四半期の売上の推移を教えてください。",
            agent_id="default",
            runtime_id="builtin",
            created_by_user_uuid="user-perf",
            thread_id=f"thread_perf_{index // 2:05d}",
            status=RunStatus.COMPLETED,
            created_at=created,
            updated_at=created,
        )
        run.events = [
            RunEvent(
                run_id=run.id,
                type=RunEventType.RUNTIME_EVENT,
                message=f"経過 {number}",
                payload={"text": "経過の詳細" * 20},
            )
            for number in range(10)
        ]
        run.artifacts = [
            Artifact(name="回答", kind="answer", content={"text": "回答の本文です。" * 100})
        ]
        runs.append(run)
    return runs


def test_small_changes_stay_fast_with_many_runs(
    checkpoint_key: str, capsys: pytest.CaptureFixture[str]
) -> None:
    """Run が 1,000 件あっても、小さな変更は 1 秒以内（以前の全体の書き戻しの時間も出す）。"""
    count = 1000
    runs = _synthetic_runs(count)
    started = time.perf_counter()
    with connect_platform_oracle() as connection, connection.cursor() as cursor:
        checkpoint_store.insert_head(
            cursor,
            RUNTIME_CHECKPOINT_TABLE,
            checkpoint_key,
            checkpoint_store.header_json(quarantined=[]),
        )
        checkpoint_store.write_entities(
            cursor,
            checkpoint_key,
            {(checkpoint_store.RUN, run.id): run.model_dump_json() for run in runs},
            version=0,
        )
        connection.commit()
    seed_ms = (time.perf_counter() - started) * 1000

    started = time.perf_counter()
    repository = AgentRuntimeOracleCheckpointRepository(checkpoint_key=checkpoint_key)
    load_ms = (time.perf_counter() - started) * 1000
    assert len(repository.list_run_summaries()) == count

    timings: dict[str, float] = {}

    def measure(label: str, action: Any) -> None:
        started = time.perf_counter()
        action()
        timings[label] = (time.perf_counter() - started) * 1000

    # 接続の初回（pool の作成）を計測から外す。
    repository.set_run_feedback("run_perf_00000", RunFeedback(rating=FeedbackRating.HELPFUL))
    measure(
        "delete_thread",
        lambda: repository.delete_thread("thread_perf_00010", user_uuid="user-perf"),
    )
    measure(
        "rename_thread",
        lambda: repository.rename_thread("thread_perf_00011", user_uuid="user-perf", title="名前"),
    )
    measure(
        "set_run_feedback",
        lambda: repository.set_run_feedback(
            "run_perf_00030",
            RunFeedback(rating=FeedbackRating.NOT_HELPFUL, reason=FeedbackReason.INCORRECT),
        ),
    )
    measure("cancel_run", lambda: repository.cancel_run("run_perf_00040"))

    # 以前の実装の 1 回の保存（全体の snapshot を JSON にして 1 行の CLOB に書き戻す）。
    legacy_key = f"{checkpoint_key}-legacy"
    try:
        started = time.perf_counter()
        text = AgentRuntimeSnapshot(runs=runs, agents=repository.list_agents()).model_dump_json()
        with connect_platform_oracle() as connection, connection.cursor() as cursor:
            cursor.setinputsizes(snapshot_json=oracledb.DB_TYPE_CLOB)
            cursor.execute(
                f"INSERT INTO {RUNTIME_CHECKPOINT_TABLE} "  # nosec B608
                "(CHECKPOINT_KEY, SNAPSHOT_JSON) VALUES (:checkpoint_key, :snapshot_json)",
                {"checkpoint_key": legacy_key, "snapshot_json": text},
            )
            connection.commit()
        legacy_ms = (time.perf_counter() - started) * 1000
    finally:
        _delete_key(legacy_key)

    with capsys.disabled():
        print(
            f"\n[#1939] runs={count} snapshot_bytes={len(text.encode())} "
            f"seed_ms={seed_ms:.0f} load_ms={load_ms:.0f} legacy_full_write_ms={legacy_ms:.0f} "
            + " ".join(f"{label}_ms={value:.0f}" for label, value in timings.items())
        )
    for label, value in timings.items():
        assert value < 1000, f"{label} が {value:.0f}ms かかりました"
    stored = _entities(checkpoint_key)
    deleted = json.loads(stored[("run", "run_perf_00020")][0] or "{}")
    assert deleted["thread_id"] is None
    assert deleted["metadata"]["deleted_thread_id"] == "thread_perf_00010"
