"""会話の中の Run の順を、作成日時（OS の時計）ではなく会話の中の番号で決める（#5150）。

OS の時計は時刻の同期で後ろへ戻ることがあり、先に作った Run の作成日時が後の Run より新しく
なる。前の Run・最初の Run・履歴の順は Run の `thread_seq`（作るときに会話の最大 + 1）で決める。
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from checkpoint_support import CheckpointTable
from clock_support import RuntimeClock

from app.features.agent.runtime import (
    AgentProfile,
    AgentRuntimeOracleCheckpointRepository,
    AgentRuntimeRepository,
    Artifact,
    RunCreateRequest,
    RunState,
    RunStatus,
)

AGENT_ID = "agent-thread-order-5150"
USER = "user-thread-order"
OLD_THREAD = "thread_" + "0" * 31 + "1"


def _repository() -> AgentRuntimeRepository:
    repository = AgentRuntimeRepository()
    repository.create_agent(AgentProfile(id=AGENT_ID, name="Agent A", instructions="合成の指示"))
    return repository


def _create(
    repository: AgentRuntimeRepository, goal: str, *, thread_id: str | None = None
) -> RunState:
    return repository.create_builtin_run(
        RunCreateRequest(goal=goal, agent_id=AGENT_ID, thread_id=thread_id),
        created_by_user_uuid=USER,
    )


def _complete(
    repository: AgentRuntimeRepository, run: RunState, answer: str, state: dict[str, object]
) -> None:
    assert repository.begin_builtin_run(run.id) is not None
    repository.save_support_task(
        run.id, {"thread_id": run.thread_id, "owner_user_uuid": USER, **state}
    )
    repository.complete_builtin_run(run.id, answer)


def _old_run(run_id: str, *, created_at: datetime, thread_id: str = OLD_THREAD) -> RunState:
    """番号の無い既存の Run（#5150 より前に作った Run）。"""
    return RunState(
        id=run_id,
        goal=f"合成の質問 {run_id}",
        agent_id=AGENT_ID,
        runtime_id="builtin",
        status=RunStatus.COMPLETED,
        thread_id=thread_id,
        created_by_user_uuid=USER,
        artifacts=[Artifact(name="回答", kind="answer", content={"text": f"合成の回答 {run_id}"})],
        created_at=created_at,
        updated_at=created_at,
    )


def test_thread_seq_starts_at_one_and_increases_in_the_thread() -> None:
    repository = _repository()
    first = _create(repository, "合成の質問 1")
    second = _create(repository, "合成の質問 2", thread_id=first.thread_id)
    third = _create(repository, "合成の質問 3", thread_id=first.thread_id)
    other = _create(repository, "別の会話の質問")

    assert (first.thread_seq, second.thread_seq, third.thread_seq) == (1, 2, 3)
    assert other.thread_seq == 1


def test_previous_runs_follow_the_thread_order_when_the_clock_goes_back(
    runtime_clock: RuntimeClock,
) -> None:
    """時計が戻って後の Run の作成日時が前の Run より古くても、前の Run の状態・履歴を引き継ぐ。"""
    repository = _repository()
    first = _create(repository, "合成の最初の質問")
    _complete(repository, first, "合成の回答 1", {"pending_clarifications": ["問い A"]})
    runtime_clock.rewind(60)
    second = _create(repository, "合成の答え", thread_id=first.thread_id)
    assert second.created_at < first.created_at
    assert second.thread_seq == 2

    goal, state = repository.support_task_context(second.id)
    assert goal == "合成の最初の質問"
    assert state is not None
    assert state["pending_clarifications"] == ["問い A"]
    assert [run.id for run in repository.thread_previous_runs(second.id)] == [first.id]
    [turn] = repository.thread_history(second.id)
    assert "合成の回答 1" in str(turn)
    # 前の Run から見て、後の Run は前の Run ではない。
    assert repository.thread_previous_runs(first.id) == []
    assert repository.thread_history(first.id) == []

    # 会話の表示の順・名前（最初の Run）も番号の順。
    thread = repository.get_thread(first.thread_id or "", user_uuid=USER)
    assert [run.id for run in thread.runs] == [first.id, second.id]
    [summary] = repository.list_threads(user_uuid=USER)
    assert summary.title == "合成の最初の質問"
    assert summary.created_at == first.created_at
    renamed = repository.rename_thread(first.thread_id or "", user_uuid=USER, title="名前 A")
    assert renamed.title == "名前 A"
    stored_first = repository.get_run(first.id)
    assert stored_first.metadata["thread_title"] == "名前 A"


def test_runs_without_thread_seq_are_numbered_in_created_order() -> None:
    """番号の無い既存の Run は、会話を読むときに作成日時の順で番号を振る（1 回だけ）。"""
    repository = _repository()
    base = datetime(2026, 1, 1, tzinfo=UTC)
    old = [
        _old_run("run_c", created_at=base + timedelta(seconds=2)),
        _old_run("run_a", created_at=base),
        _old_run("run_b", created_at=base + timedelta(seconds=1)),
    ]
    with repository._lock:  # noqa: SLF001 - 既存の保存先の Run を置く
        for run in old:
            repository._runs[run.id] = run  # noqa: SLF001

    thread = repository.get_thread(OLD_THREAD, user_uuid=USER)
    assert [(run.id, run.thread_seq) for run in thread.runs] == [
        ("run_a", 1),
        ("run_b", 2),
        ("run_c", 3),
    ]
    # 振った番号は変わらない（後から作る Run は最大 + 1）。
    later = _create(repository, "合成の続きの質問", thread_id=OLD_THREAD)
    assert later.thread_seq == 4
    assert [run.id for run in repository.thread_previous_runs(later.id)] == [
        "run_a",
        "run_b",
        "run_c",
    ]


def test_numbers_for_runs_without_thread_seq_are_saved_once() -> None:
    """Oracle の保存先では振った番号を書き、2 つ目のプロセスも同じ番号を読む。"""
    base = datetime(2026, 1, 1, tzinfo=UTC)
    table = CheckpointTable()
    table.seed(
        [
            _old_run("run_y", created_at=base + timedelta(seconds=1)),
            _old_run("run_x", created_at=base),
        ],
        [AgentProfile(id=AGENT_ID, name="Agent A", instructions="合成の指示")],
    )
    first = AgentRuntimeOracleCheckpointRepository(connect_factory=table.connect)
    assert first.thread_previous_runs("run_y")[0].id == "run_x"
    first.flush_writes()
    stored = {run.id: run.thread_seq for run in table.snapshot().runs}
    assert stored == {"run_x": 1, "run_y": 2}

    second = AgentRuntimeOracleCheckpointRepository(connect_factory=table.connect)
    assert [run.thread_seq for run in second.get_thread(OLD_THREAD, user_uuid=USER).runs] == [
        1,
        2,
    ]


def test_runs_with_the_same_thread_seq_are_ordered_by_run_id() -> None:
    """同じ番号の Run（別のプロセスが同じ番号を書いたとき）は run id の順にする。"""
    repository = _repository()
    base = datetime(2026, 1, 1, tzinfo=UTC)
    first = _old_run("run_1", created_at=base)
    first.thread_seq = 1
    # 作成日時は run_b の方が古いが、同じ番号なので run id の順（run_a → run_b）。
    run_b = _old_run("run_b", created_at=base + timedelta(seconds=1))
    run_a = _old_run("run_a", created_at=base + timedelta(seconds=2))
    run_a.thread_seq = run_b.thread_seq = 2
    with repository._lock:  # noqa: SLF001 - 保存先の Run を置く
        for run in (run_b, first, run_a):
            repository._runs[run.id] = run  # noqa: SLF001

    thread = repository.get_thread(OLD_THREAD, user_uuid=USER)
    assert [run.id for run in thread.runs] == ["run_1", "run_a", "run_b"]
    assert [run.id for run in repository.thread_previous_runs("run_a")] == ["run_1"]
    assert [run.id for run in repository.thread_previous_runs("run_b")] == ["run_1", "run_a"]
    later = _create(repository, "合成の続きの質問", thread_id=OLD_THREAD)
    assert later.thread_seq == 3


def test_another_process_runs_are_read_before_the_thread_seq_is_decided() -> None:
    """続ける会話の Run は、別のプロセスが同じ会話に作った Run を読んでから番号を決める。"""
    table = CheckpointTable()
    process_a = AgentRuntimeOracleCheckpointRepository(connect_factory=table.connect)
    process_a.create_agent(AgentProfile(id=AGENT_ID, name="Agent A", instructions="合成の指示"))
    process_b = AgentRuntimeOracleCheckpointRepository(connect_factory=table.connect)

    first = _create(process_a, "合成の質問 1")
    # B のメモリにはまだ A の Run が無い（読み直す前）。
    assert first.id not in {run.id for run in process_b.list_runs()}
    second = _create(process_b, "合成の質問 2", thread_id=first.thread_id)
    # A のメモリには B の Run が無いが、番号を決める前に読む。
    third = _create(process_a, "合成の質問 3", thread_id=first.thread_id)

    assert (first.thread_seq, second.thread_seq, third.thread_seq) == (1, 2, 3)
    stored = {run.id: run.thread_seq for run in table.snapshot().runs}
    assert stored == {first.id: 1, second.id: 2, third.id: 3}
