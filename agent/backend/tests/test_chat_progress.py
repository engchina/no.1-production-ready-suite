"""チャットの処理の段階のイベント（3 製品共通の契約。#1359）を Run の状態から記録・配信するテスト。

組み込み Runtime は SDK の `ScriptedModel` で台本にし（OCI へは接続しない）、記録の位置
（ツールなし・ツール・同じツールの 2 回目・承認・却下・承認の 2 回目・ツールの後のツール・失敗・
停止）、保存先（file / Oracle の checkpoint・normalized）、polling と SSE の endpoint を確かめる。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import anyio
import pytest
from agents.testing import assistant_message, function_call
from pr_backend_core.chat_progress import (
    ChatProgressStepEvent,
    ChatProgressTerminalEvent,
    fold_chat_progress_events,
)
from pr_system_settings.auth.domain import LOCAL_DEBUG_USER_UUID
from pytest import MonkeyPatch
from security_support import (
    ProductionAuth,
    client,
    enable_production_auth,
    login,
)
from test_builtin_runtime import AGENT_ID, LOOKUP, WRITE, _create_run, _script
from test_builtin_runtime import calls as _calls_fixture  # noqa: F401 - pytestmark の fixture
from test_health import _FakeOracleConnection, _FakeOracleStore

from app.features.agent import builtin_runtime
from app.features.agent.chat_progress import approval_rounds, record_run_progress
from app.features.agent.runtime import (
    AgentRuntimeOracleCheckpointRepository,
    AgentRuntimeOracleNormalizedRepository,
    AgentRuntimeRepository,
    ApprovalDecisionRequest,
    RunCreateRequest,
    RunState,
    runtime_repository,
)
from app.features.agent.tools import ToolCall
from app.security.service import set_security_service

# 組み込み Runtime のテスト用のツール・Skill・業務 Agent（test_builtin_runtime の fixture）。
pytestmark = pytest.mark.usefixtures("_calls_fixture")

Step = tuple[str, str, str, dict[str, Any] | None]


def _steps(run: RunState) -> list[Step]:
    """組み立てた段階（id・種類・状態・params）。"""
    fold = fold_chat_progress_events(run.progress_events)
    return [(step.step_id, step.kind, step.status, step.params) for step in fold.step_list()]


def _terminal(run: RunState) -> str | None:
    return fold_chat_progress_events(run.progress_events).terminal


def _assert_contiguous(run: RunState) -> None:
    """番号は 1 から連続し、どのイベントも Run の id を対象にする。"""
    assert [event.seq for event in run.progress_events] == list(
        range(1, len(run.progress_events) + 1)
    )
    assert {event.target_id for event in run.progress_events} == {run.id}


def _approve(run_id: str, *, approved: bool = True) -> None:
    waiting = runtime_repository.get_run(run_id)
    for approval in waiting.approvals:
        if approval.status == "pending":
            runtime_repository.decide_approval(
                approval.id, ApprovalDecisionRequest(approved=approved, decided_by="approver1")
            )


# --------------------------------------------------------------------------------------------
# 記録の位置
# --------------------------------------------------------------------------------------------


def test_queued_run_starts_with_planning() -> None:
    run = runtime_repository.get_run(_create_run())

    assert _steps(run) == [("plan", "plan", "running", None)]
    assert _terminal(run) is None


def test_answer_without_tools_records_plan_and_respond(
    monkeypatch: MonkeyPatch,
) -> None:
    _script(monkeypatch, [assistant_message("こんにちは。")])
    run_id = _create_run("挨拶して")

    anyio.run(builtin_runtime.execute_run, run_id)

    run = runtime_repository.get_run(run_id)
    assert _steps(run) == [
        ("plan", "plan", "done", None),
        ("respond", "respond", "done", None),
    ]
    assert _terminal(run) == "done"
    _assert_contiguous(run)
    fold = fold_chat_progress_events(run.progress_events)
    # ツールを呼ばずに答えたときの回答の作成は、始まりの時刻を持たない（所要時間を出さない）。
    assert fold.steps["respond"].started_at is None
    assert fold.steps["respond"].finished_at is not None
    assert fold.steps["plan"].started_at is not None


def test_tool_call_records_tool_then_respond(monkeypatch: MonkeyPatch) -> None:
    _script(
        monkeypatch,
        [function_call(LOOKUP, {"query": "売上"}, call_id="call-1")],
        [assistant_message("3 件です。")],
    )
    run_id = _create_run()

    anyio.run(builtin_runtime.execute_run, run_id)

    run = runtime_repository.get_run(run_id)
    assert _steps(run) == [
        ("plan", "plan", "done", None),
        (f"tool:{LOOKUP}", "tool", "done", {"tool": LOOKUP}),
        ("respond", "respond", "done", None),
    ]
    assert _terminal(run) == "done"
    _assert_contiguous(run)
    # 段階のイベントは名前（文言）を持たない。kind が step_id と同じなら省く。
    plan_events = [
        event
        for event in run.progress_events
        if isinstance(event, ChatProgressStepEvent) and event.step_id == "plan"
    ]
    assert all(event.kind is None for event in plan_events)


def test_same_tool_twice_and_tool_after_tool(monkeypatch: MonkeyPatch) -> None:
    """同じツールの 2 回目は `#2`。ツールの後にまたツールを呼んだら、回答の作成を「進め方の検討」の
    完了に変える（完了した段階を実行中に戻さない・消さない。#1358）。"""
    _script(
        monkeypatch,
        [function_call(LOOKUP, {"query": "今月"}, call_id="call-1")],
        [function_call(LOOKUP, {"query": "先月"}, call_id="call-2")],
        [assistant_message("比べました。")],
    )
    run_id = _create_run("今月と先月を比べて")

    anyio.run(builtin_runtime.execute_run, run_id)

    run = runtime_repository.get_run(run_id)
    assert _steps(run) == [
        ("plan", "plan", "done", None),
        (f"tool:{LOOKUP}", "tool", "done", {"tool": LOOKUP}),
        ("respond", "plan", "done", None),
        (f"tool:{LOOKUP}#2", "tool", "done", {"tool": LOOKUP}),
        ("respond#2", "respond", "done", None),
    ]
    _assert_contiguous(run)
    # 途中の記録でも、終わった段階が実行中に戻るイベントは無い。
    rank = {"pending": 0, "running": 1, "done": 2, "failed": 2, "skipped": 2}
    seen: dict[str, str] = {}
    for event in run.progress_events:
        if isinstance(event, ChatProgressStepEvent):
            before = seen.get(event.step_id)
            assert before is None or rank[event.status] >= rank[before]
            seen[event.step_id] = event.status


def test_parallel_tools_in_one_turn(monkeypatch: MonkeyPatch) -> None:
    _script(
        monkeypatch,
        [
            function_call(LOOKUP, {"query": "今月"}, call_id="call-1"),
            function_call(LOOKUP, {"query": "先月"}, call_id="call-2"),
        ],
        [assistant_message("比べました。")],
    )
    run_id = _create_run("今月と先月を比べて")

    anyio.run(builtin_runtime.execute_run, run_id)

    run = runtime_repository.get_run(run_id)
    ids = [step_id for step_id, *_ in _steps(run)]
    assert ids == ["plan", f"tool:{LOOKUP}", f"tool:{LOOKUP}#2", "respond"]
    assert {status for _, _, status, _ in _steps(run)} == {"done"}


def test_approval_waits_then_runs_the_tool(monkeypatch: MonkeyPatch) -> None:
    _script(
        monkeypatch,
        [function_call(WRITE, {"query": "登録"}, call_id="call-w")],
        [assistant_message("登録しました。")],
    )
    run_id = _create_run("登録して")
    anyio.run(builtin_runtime.execute_run, run_id)

    waiting = runtime_repository.get_run(run_id)
    # 承認待ちはツールの前に出し、ツールは承認の後に実行するので待機中。
    assert _steps(waiting) == [
        ("plan", "plan", "done", None),
        ("approval_wait", "approval", "running", {"tools": WRITE}),
        (f"tool:{WRITE}", "tool", "pending", {"tool": WRITE}),
    ]
    assert _terminal(waiting) is None

    _approve(run_id)
    decided = runtime_repository.get_run(run_id)
    assert _steps(decided)[1] == ("approval_wait", "approval", "done", {"tools": WRITE})
    assert _steps(decided)[2][2] == "pending"

    anyio.run(builtin_runtime.resume_run, run_id)

    run = runtime_repository.get_run(run_id)
    assert _steps(run) == [
        ("plan", "plan", "done", None),
        ("approval_wait", "approval", "done", {"tools": WRITE}),
        (f"tool:{WRITE}", "tool", "done", {"tool": WRITE}),
        ("respond", "respond", "done", None),
    ]
    assert _terminal(run) == "done"
    _assert_contiguous(run)


def test_rejected_tool_is_skipped(monkeypatch: MonkeyPatch) -> None:
    _script(
        monkeypatch,
        [function_call(WRITE, {"query": "登録"}, call_id="call-r")],
        [assistant_message("登録していません。")],
    )
    run_id = _create_run("登録して")
    anyio.run(builtin_runtime.execute_run, run_id)
    _approve(run_id, approved=False)
    anyio.run(builtin_runtime.resume_run, run_id)

    run = runtime_repository.get_run(run_id)
    assert _steps(run) == [
        ("plan", "plan", "done", None),
        ("approval_wait", "approval", "done", {"tools": WRITE}),
        (f"tool:{WRITE}", "tool", "skipped", {"tool": WRITE, "rejected": True}),
        ("respond", "respond", "done", None),
    ]
    assert _terminal(run) == "done"


def test_second_approval_round_is_its_own_step(monkeypatch: MonkeyPatch) -> None:
    """承認の後に別の承認を求めたら、承認待ちを回ごとに出す（#1358）。"""
    _script(
        monkeypatch,
        [function_call(WRITE, {"query": "1 件目"}, call_id="call-1")],
        [function_call(WRITE, {"query": "2 件目"}, call_id="call-2")],
        [assistant_message("2 件登録しました。")],
    )
    run_id = _create_run("2 件登録して")
    anyio.run(builtin_runtime.execute_run, run_id)
    _approve(run_id)
    anyio.run(builtin_runtime.resume_run, run_id)

    second = runtime_repository.get_run(run_id)
    assert second.status == "waiting_approval"
    assert _steps(second) == [
        ("plan", "plan", "done", None),
        ("approval_wait", "approval", "done", {"tools": WRITE}),
        (f"tool:{WRITE}", "tool", "done", {"tool": WRITE}),
        ("respond", "plan", "done", None),
        ("approval_wait#2", "approval", "running", {"tools": WRITE}),
        (f"tool:{WRITE}#2", "tool", "pending", {"tool": WRITE}),
    ]
    assert len(approval_rounds(second.approvals)) == 2

    _approve(run_id)
    anyio.run(builtin_runtime.resume_run, run_id)

    run = runtime_repository.get_run(run_id)
    assert [step_id for step_id, *_ in _steps(run)] == [
        "plan",
        "approval_wait",
        f"tool:{WRITE}",
        "respond",
        "approval_wait#2",
        f"tool:{WRITE}#2",
        "respond#2",
    ]
    assert {status for _, _, status, _ in _steps(run)} == {"done"}
    assert _terminal(run) == "done"
    _assert_contiguous(run)


def test_failed_run_records_failed_terminal(monkeypatch: MonkeyPatch) -> None:
    monkeypatch.setattr(builtin_runtime, "_max_turns", lambda: 1)
    _script(
        monkeypatch,
        [function_call(LOOKUP, {"query": "売上"}, call_id="call-1")],
        [function_call(LOOKUP, {"query": "売上"}, call_id="call-2")],
    )
    run_id = _create_run("ずっと調べる")

    anyio.run(builtin_runtime.execute_run, run_id)

    run = runtime_repository.get_run(run_id)
    assert run.status == "failed"
    assert _terminal(run) == "failed"
    steps = _steps(run)
    assert steps[0][:3] == ("plan", "plan", "done")
    # 失敗の時点で実行中だった段階は失敗にする。
    assert steps[-1][2] == "failed"
    assert not any(status == "running" for _, _, status, _ in steps)


def test_cancelled_run_records_cancelled_terminal() -> None:
    run_id = _create_run()

    cancelled = runtime_repository.cancel_run(run_id)

    assert _steps(cancelled) == [("plan", "plan", "skipped", None)]
    assert _terminal(cancelled) == "cancelled"
    # 終端の後は記録しない（もう一度照合しても増えない）。
    before = len(cancelled.progress_events)
    record_run_progress(cancelled)
    assert len(cancelled.progress_events) == before


def test_cancel_while_waiting_for_approval_skips_open_steps(
    monkeypatch: MonkeyPatch,
) -> None:
    _script(monkeypatch, [function_call(WRITE, {"query": "登録"}, call_id="call-c")])
    run_id = _create_run("登録して")
    anyio.run(builtin_runtime.execute_run, run_id)

    run = runtime_repository.cancel_run(run_id)

    assert _steps(run) == [
        ("plan", "plan", "done", None),
        ("approval_wait", "approval", "skipped", {"tools": WRITE}),
        (f"tool:{WRITE}", "tool", "skipped", {"tool": WRITE}),
    ]
    assert _terminal(run) == "cancelled"


def test_recording_is_idempotent() -> None:
    run = runtime_repository.get_run(_create_run())
    before = list(run.progress_events)

    record_run_progress(run)
    record_run_progress(run)

    assert run.progress_events == before


# --------------------------------------------------------------------------------------------
# 保存と読み込み
# --------------------------------------------------------------------------------------------


def _seed_completed_run(repository: AgentRuntimeRepository) -> RunState:
    run = repository.create_builtin_run(RunCreateRequest(goal="保存", agent_id="default"))
    assert repository.begin_builtin_run(run.id) is not None
    step_id, _context = repository.start_builtin_tool_step(
        run.id, ToolCall(name="echo", arguments={"text": "x"}, trace_id="call-1")
    )
    from app.features.agent.tools import ToolResult

    repository.finish_builtin_tool_step(
        run.id, step_id, ToolResult(name="echo", success=True, output={"text": "x"})
    )
    return repository.complete_builtin_run(run.id, "回答しました。")


def test_progress_events_survive_the_file_snapshot(tmp_path: Path) -> None:
    path = tmp_path / "runtime.json"
    repository = AgentRuntimeRepository(snapshot_path=path)
    completed = _seed_completed_run(repository)
    assert _terminal(completed) == "done"

    reloaded = AgentRuntimeRepository(snapshot_path=path).get_run(completed.id)

    assert reloaded.progress_events == completed.progress_events
    # 保存の JSON は値の無い項目を出さない（契約の JSON と同じ形）。
    saved = json.loads(path.read_text(encoding="utf-8"))
    [saved_run] = [item for item in saved["runs"] if item["id"] == completed.id]
    assert all("detail" not in event for event in saved_run["progress_events"])


@pytest.mark.parametrize(
    "repository_class",
    [AgentRuntimeOracleCheckpointRepository, AgentRuntimeOracleNormalizedRepository],
    ids=["checkpoint", "normalized"],
)
def test_progress_events_survive_the_oracle_checkpoint(repository_class: Any) -> None:
    store = _FakeOracleStore()

    def connect() -> _FakeOracleConnection:
        return _FakeOracleConnection(store)

    repository = repository_class(connect_factory=connect)
    completed = _seed_completed_run(repository)

    reloaded = repository_class(connect_factory=connect).get_run(completed.id)

    assert reloaded.progress_events == completed.progress_events
    assert _terminal(reloaded) == "done"


def test_broken_saved_progress_events_are_dropped() -> None:
    run = RunState.model_validate(
        {
            "id": "run_broken_progress",
            "goal": "x",
            "agent_id": "default",
            "status": "completed",
            "progress_events": [
                {"type": "step", "seq": 1},  # 形の違う要素は捨てる
                {
                    "type": "terminal",
                    "seq": 2,
                    "target_id": "run_broken_progress",
                    "emitted_at": "2026-10-09T00:00:00Z",
                    "status": "done",
                },
            ],
        }
    )

    assert [event.seq for event in run.progress_events] == [2]
    assert isinstance(run.progress_events[0], ChatProgressTerminalEvent)


# --------------------------------------------------------------------------------------------
# API（polling・SSE・権限）
# --------------------------------------------------------------------------------------------


def _completed_api_run(monkeypatch: MonkeyPatch) -> str:
    _script(
        monkeypatch,
        [function_call(LOOKUP, {"query": "売上"}, call_id="call-1")],
        [assistant_message("3 件です。")],
    )
    # 会話は作った利用者だけが読む（local はローカル利用者）。
    run_id = runtime_repository.create_builtin_run(
        RunCreateRequest(goal="今月の売上を教えて", agent_id=AGENT_ID),
        created_by_user_uuid=LOCAL_DEBUG_USER_UUID,
    ).id
    anyio.run(builtin_runtime.execute_run, run_id)
    return run_id


def _sse_events(text: str) -> list[tuple[str | None, str, dict[str, Any]]]:
    """SSE の本文を (id, event, data) の一覧にする。"""
    items: list[tuple[str | None, str, dict[str, Any]]] = []
    for block in text.split("\n\n"):
        fields: dict[str, str] = {}
        for line in block.splitlines():
            key, _, value = line.partition(": ")
            fields[key] = value
        if "data" in fields:
            items.append((fields.get("id"), fields.get("event", ""), json.loads(fields["data"])))
    return items


def test_thread_and_run_responses_include_progress_events(
    monkeypatch: MonkeyPatch,
) -> None:
    run_id = _completed_api_run(monkeypatch)
    run = runtime_repository.get_run(run_id)

    detail = client.get(f"/api/runs/{run_id}")
    assert detail.status_code == 200, detail.text
    events = detail.json()["data"]["progress_events"]
    assert [event["seq"] for event in events] == list(range(1, len(events) + 1))
    assert events[-1] == {
        "schema_version": 1,
        "seq": len(events),
        "target_id": run_id,
        "attempt": 0,
        "emitted_at": events[-1]["emitted_at"],
        "type": "terminal",
        "status": "done",
    }
    thread = client.get(f"/api/threads/{run.thread_id}")
    assert thread.status_code == 200, thread.text
    assert thread.json()["data"]["runs"][0]["progress_events"] == events


def test_progress_polling_returns_events_after_since(
    monkeypatch: MonkeyPatch,
) -> None:
    run_id = _completed_api_run(monkeypatch)
    total = len(runtime_repository.get_run(run_id).progress_events)

    first = client.get(f"/api/runs/{run_id}/progress")
    assert first.status_code == 200, first.text
    page = first.json()["data"]
    assert (page["target_id"], page["attempt"], page["last_seq"], page["terminal"]) == (
        run_id,
        0,
        total,
        True,
    )
    assert [event["seq"] for event in page["events"]] == list(range(1, total + 1))

    rest = client.get(f"/api/runs/{run_id}/progress?since={total - 2}").json()["data"]
    assert [event["seq"] for event in rest["events"]] == [total - 1, total]
    assert client.get(f"/api/runs/{run_id}/progress?since=-1").status_code == 422
    assert client.get("/api/runs/run-missing/progress").status_code == 404


def test_progress_stream_sends_events_and_closes_at_the_terminal(
    monkeypatch: MonkeyPatch,
) -> None:
    run_id = _completed_api_run(monkeypatch)
    total = len(runtime_repository.get_run(run_id).progress_events)

    response = client.get(f"/api/runs/{run_id}/progress/stream")
    assert response.status_code == 200, response.text
    assert response.headers["content-type"].startswith("text/event-stream")
    assert response.headers["x-accel-buffering"] == "no"
    events = _sse_events(response.text)
    assert [item[0] for item in events] == [str(seq) for seq in range(1, total + 1)]
    assert {item[1] for item in events} == {"chat_progress"}
    assert events[-1][2]["type"] == "terminal"

    # 続き（`since` と、ブラウザの張り直しの `Last-Event-ID` の大きい方）から送る。
    resumed = client.get(
        f"/api/runs/{run_id}/progress/stream?since=1",
        headers={"Last-Event-ID": str(total - 1)},
    )
    assert [item[0] for item in _sse_events(resumed.text)] == [str(total)]
    # 終わった Run を続きから求めたら 204（ブラウザは張り直しをやめる）。
    finished = client.get(f"/api/runs/{run_id}/progress/stream?since={total}")
    assert finished.status_code == 204
    assert client.get("/api/runs/run-missing/progress/stream").status_code == 404


@pytest.fixture
def auth(monkeypatch: MonkeyPatch) -> Any:
    yield enable_production_auth(monkeypatch)
    set_security_service(None)


def test_progress_endpoints_follow_the_run_scope(
    monkeypatch: MonkeyPatch, auth: ProductionAuth
) -> None:
    run_id = _completed_api_run(monkeypatch)
    paths = [f"/api/runs/{run_id}/progress", f"/api/runs/{run_id}/progress/stream?since=0"]

    # チャットの利用者（業務 Agent の実行）は、対象範囲の Agent の Run を読める。
    auth.user_with_permissions("progress-chat", ["agent.runs.operate"], agent_ids=[AGENT_ID])
    headers = login("progress-chat")
    for path in paths:
        assert client.get(path, headers=headers).status_code == 200, path

    # 対象範囲の外の Agent の Run は 403。
    auth.user_with_permissions("progress-other", ["agent.runs.operate"], agent_ids=["other"])
    other = login("progress-other")
    for path in paths:
        assert client.get(path, headers=other).status_code == 403, path

    # メニューも capability も無い利用者は経路で 403。
    auth.user_with_permissions("progress-none", [], agent_ids=[AGENT_ID])
    none = login("progress-none")
    for path in paths:
        assert client.get(path, headers=none).status_code == 403, path
    # ログインしていなければ 401。
    assert client.get(paths[0]).status_code == 401


def test_created_run_response_carries_the_first_step(monkeypatch: MonkeyPatch) -> None:
    monkeypatch.setattr("app.features.agent.router._schedule_builtin_run", lambda run: None)

    created = client.post("/api/runs", json={"goal": "質問", "agent_id": "default"})

    assert created.status_code == 200, created.text
    [event] = created.json()["data"]["progress_events"]
    assert (event["step_id"], event["status"]) == ("plan", "running")
    assert event["target_id"] == created.json()["data"]["id"]
