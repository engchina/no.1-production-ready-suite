"""同じ checkpoint に 2 つのプロセスが書いても、互いの更新を消さない（#1916）。

Run・業務 Agent の checkpoint（`AGENT_RUNTIME_CHECKPOINTS`）は全体を 1 行の JSON で書き戻す。
worktree で主の `.env` のまま 2 つ目の Agent の backend を起動していた間に、主の dev server の
Run の完了の記録が消えた（#1863 の確認中）。書き込みは版（`VERSION_NO`）を確かめ、別のプロセスが
先に書いていたら Run・業務 Agent の単位でまとめ直してから書く。

2 つのプロセスは、同じ偽の表（`CheckpointTable`）を読み書きする 2 つの repository で表す。
"""

from __future__ import annotations

import logging
from typing import Any

import pytest
from checkpoint_support import CheckpointTable

from app.features.agent.runtime import (
    AgentProfile,
    AgentRuntimeCheckpointCorruptError,
    AgentRuntimeOracleCheckpointRepository,
    AgentRuntimeSnapshot,
    RunState,
    RunStatus,
)
from app.features.agent.runtime_checkpoint_store import WRITER_ID, plan_entity_merge

KEY = "default"


def _run(run_id: str, status: RunStatus = RunStatus.RUNNING) -> RunState:
    return RunState(
        id=run_id, goal="規程を調べて", agent_id="default", runtime_id="builtin", status=status
    )


def _table(*runs: RunState, agents: list[AgentProfile] | None = None) -> CheckpointTable:
    table = CheckpointTable()
    table.rows[KEY] = AgentRuntimeSnapshot(runs=list(runs), agents=agents or []).model_dump_json()
    table.versions[KEY] = 0
    return table


def _process(table: CheckpointTable) -> AgentRuntimeOracleCheckpointRepository:
    """同じ保存先を読み込んだ 1 つのプロセス（backend）。"""
    return AgentRuntimeOracleCheckpointRepository(connect_factory=table.connect)


def _stored(table: CheckpointTable) -> AgentRuntimeSnapshot:
    return AgentRuntimeSnapshot.model_validate_json(table.rows[KEY])


def _stored_status(table: CheckpointTable) -> dict[str, RunStatus]:
    return {run.id: run.status for run in _stored(table).runs}


def _merge_logs(caplog: pytest.LogCaptureFixture) -> list[logging.LogRecord]:
    return [
        record
        for record in caplog.records
        if record.getMessage() == "agent_runtime_checkpoint_concurrent_write"
    ]


def test_second_process_does_not_erase_the_first_process_updates(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """古いメモリのプロセスが書いても、別のプロセスが先に書いた Run の更新は残る。"""
    table = _table(_run("run_a"), _run("run_b"))
    first = _process(table)
    second = _process(table)

    first.cancel_run("run_a")
    with caplog.at_level(logging.WARNING):
        second.cancel_run("run_b")

    # 修正前は、second が読み込んだ時点の run_a（running）で上書きし、取消が消えていた。
    assert _stored_status(table) == {
        "run_a": RunStatus.CANCELLED,
        "run_b": RunStatus.CANCELLED,
    }
    assert table.versions[KEY] == 2
    # まとめ直したプロセスのメモリも、保存先の更新を取り込む（画面に古い状態を出し続けない）。
    assert second.get_run("run_a").status == RunStatus.CANCELLED
    [record] = _merge_logs(caplog)
    assert record.levelno == logging.WARNING
    extra: Any = record
    assert extra.other_writer_id == WRITER_ID
    assert extra.expected_version == 0
    assert extra.current_version == 1
    assert extra.runs_from_storage == 1
    assert extra.overlapping_runs == []
    assert "2 つ目の Agent の backend" in extra.reason

    # 先に書いたプロセスも、次の保存で相手の更新を消さない。
    first.persist_control_plane_state()
    assert _stored_status(table)["run_b"] == RunStatus.CANCELLED
    assert first.get_run("run_b").status == RunStatus.CANCELLED
    assert table.versions[KEY] == 3


def test_writes_without_conflict_do_not_merge(caplog: pytest.LogCaptureFixture) -> None:
    """1 つのプロセスだけが書くときは、版を進めるだけでまとめ直さない（本番の 1 プロセス）。"""
    table = _table(_run("run_a"), _run("run_b"))
    only = _process(table)

    with caplog.at_level(logging.INFO):
        only.cancel_run("run_a")
        only.cancel_run("run_b")

    assert table.versions[KEY] == 2
    assert table.writers[KEY] == WRITER_ID
    assert _merge_logs(caplog) == []


def test_same_run_changed_by_both_keeps_the_later_writer_and_logs_it(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """両方が同じ Run を変えたときだけ、後から書いたプロセスの値にする（ログに残す）。"""
    table = _table(_run("run_a"), _run("run_b"))
    first = _process(table)
    second = _process(table)

    first.cancel_run("run_a")
    second.cancel_run("run_a")

    assert _stored_status(table)["run_a"] == RunStatus.CANCELLED
    assert _stored_status(table)["run_b"] == RunStatus.RUNNING
    [record] = _merge_logs(caplog)
    extra: Any = record
    assert extra.overlapping_runs == ["run_a"]


def test_agent_deleted_by_another_process_is_not_restored() -> None:
    """別のプロセスが消した業務 Agent を、古いメモリのプロセスが書き戻さない。"""
    custom = AgentProfile(id="agent_custom", name="経費の確認")
    table = _table(_run("run_a"), agents=[custom])
    first = _process(table)
    second = _process(table)

    first.delete_agent("agent_custom")
    second.cancel_run("run_a")

    assert "agent_custom" not in {agent.id for agent in _stored(table).agents}
    assert "agent_custom" not in {agent.id for agent in second.list_agents()}
    assert _stored_status(table)["run_a"] == RunStatus.CANCELLED


def test_first_writes_to_an_empty_table_do_not_overwrite_each_other() -> None:
    """行が無い保存先に 2 つのプロセスが続けて書いても、後の方は前の方の行を上書きしない。"""
    table = CheckpointTable()
    first = _process(table)
    second = _process(table)

    first.create_agent(AgentProfile(id="agent_first", name="一つ目"))
    second.create_agent(AgentProfile(id="agent_second", name="二つ目"))

    stored_agents = {agent.id for agent in _stored(table).agents}
    assert {"agent_first", "agent_second"} <= stored_agents
    assert table.versions[KEY] == 2


def test_unreadable_checkpoint_written_by_another_process_is_not_overwritten() -> None:
    """別のプロセスが書いた checkpoint が読めなければ、上書きせずに止める。"""
    table = _table(_run("run_a"))
    process = _process(table)
    table.rows[KEY] = "{broken"
    table.versions[KEY] = 7

    with pytest.raises(AgentRuntimeCheckpointCorruptError, match="上書きせずに止めました"):
        process.cancel_run("run_a")

    assert table.rows[KEY] == "{broken"


def test_table_without_version_column_keeps_writing_until_migrated(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """migration 015 の前（版の列が無い）は今までどおり書き、適用の後に読み直すと版を確かめる。"""
    table = _table(_run("run_a"), _run("run_b"))
    table.versioned = False
    del table.versions[KEY]
    with caplog.at_level(logging.WARNING):
        process = _process(table)
        process.cancel_run("run_a")

    assert _stored_status(table)["run_a"] == RunStatus.CANCELLED
    assert KEY not in table.versions
    assert any(
        record.getMessage() == "agent_runtime_checkpoint_version_missing"
        for record in caplog.records
    )

    # システムテーブルの更新（migration 015）で列が足され、動いている backend は読み直す。
    table.versioned = True
    with process.storage_rewrite() as reload_saved_data:
        reload_saved_data()
    process.cancel_run("run_b")

    assert table.versions[KEY] == 1
    assert _stored_status(table) == {"run_a": RunStatus.CANCELLED, "run_b": RunStatus.CANCELLED}


@pytest.mark.parametrize(
    ("base", "ours", "theirs", "take", "drop", "overlapping"),
    [
        # 相手だけが変えた・足した → 相手の値
        ([{"id": "a", "v": 1}], [{"id": "a", "v": 1}], [{"id": "a", "v": 2}], {"a"}, set(), []),
        ([], [], [{"id": "b", "v": 1}], {"b"}, set(), []),
        # 自分だけが変えた・足した・消した → 自分の値（何もしない）
        ([{"id": "a", "v": 1}], [{"id": "a", "v": 2}], [{"id": "a", "v": 1}], set(), set(), []),
        ([], [{"id": "c", "v": 1}], [], set(), set(), []),
        ([{"id": "a", "v": 1}], [], [{"id": "a", "v": 1}], set(), set(), []),
        # 相手が消した（自分は変えていない） → メモリから消す
        ([{"id": "a", "v": 1}], [{"id": "a", "v": 1}], [], set(), {"a"}, []),
        # 両方が変えた → 自分の値を残し、重なりとして記録する
        ([{"id": "a", "v": 1}], [{"id": "a", "v": 2}], [{"id": "a", "v": 3}], set(), set(), ["a"]),
        # 両方が同じ値に変えた → 重なりにしない
        ([{"id": "a", "v": 1}], [{"id": "a", "v": 2}], [{"id": "a", "v": 2}], set(), set(), []),
    ],
    ids=[
        "theirs-changed",
        "theirs-added",
        "ours-changed",
        "ours-added",
        "ours-deleted",
        "theirs-deleted",
        "both-changed",
        "both-same",
    ],
)
def test_plan_entity_merge(
    base: list[Any],
    ours: list[Any],
    theirs: list[Any],
    take: set[str],
    drop: set[str],
    overlapping: list[str],
) -> None:
    plan = plan_entity_merge(base, ours, theirs)
    assert set(plan.take) == take
    assert plan.drop == drop
    assert plan.overlapping == overlapping


def test_plan_entity_merge_without_base_adopts_storage() -> None:
    """前回の同期が無い（このプロセスは何も変えていない）ときは、保存先の値にそろえる。"""
    plan = plan_entity_merge(None, [{"id": "a", "v": 1}], [{"id": "a", "v": 2}, {"v": 9}])
    assert plan.take == {"a": {"id": "a", "v": 2}}
    assert plan.unidentified == [{"v": 9}]
