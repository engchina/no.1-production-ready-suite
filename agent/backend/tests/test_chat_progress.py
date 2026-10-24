"""チャットの処理の経過（3 製品共通の契約。#1359）を、Run の trace（span の木。#3226）から
記録・配信するテスト。

組み込み Runtime は SDK の `ScriptedModel` で台本にし（OCI へは接続しない）、モデルの呼び出しの
span は OCI の client と同じ形（`gen_ai.chat` に工程の種類を付ける）で作る。記録の位置（ツール
なし・ツール・実行中・並列・承認・却下・承認の 2 回目・引数の誤り・失敗・停止・実行中の停止）、
保存先（file / Oracle の checkpoint・normalized）、polling と SSE の endpoint を確かめる。
"""

from __future__ import annotations

import contextvars
import json
from pathlib import Path
from threading import Barrier
from typing import Any

import anyio
import pytest
from agents.testing import ScriptedModel, assistant_message, function_call
from pr_backend_core.chat_progress import (
    ChatProgressStepEvent,
    ChatProgressTerminalEvent,
    fold_chat_progress_events,
)
from pr_backend_core.observability.tracing import observed_span, operation_trace
from pr_system_settings.auth.domain import LOCAL_DEBUG_USER_UUID
from pytest import MonkeyPatch
from security_support import (
    ProductionAuth,
    client,
    enable_production_auth,
    login,
)
from test_builtin_runtime import AGENT_ID, LOOKUP, WRITE, _create_run
from test_builtin_runtime import _script as _script_model
from test_builtin_runtime import calls as _calls_fixture  # noqa: F401 - pytestmark の fixture
from test_health import _FakeOracleConnection, _FakeOracleStore

from app.features.agent import builtin_runtime
from app.features.agent.chat_progress import (
    RUN_STEP_PARAM,
    model_turn_kind,
    record_run_terminal,
)
from app.features.agent.runtime import (
    AgentRuntimeOracleCheckpointRepository,
    AgentRuntimeOracleNormalizedRepository,
    AgentRuntimeRepository,
    ApprovalDecisionRequest,
    RunCreateRequest,
    RunState,
    runtime_repository,
)
from app.features.agent.tools import ToolCall, ToolInvocationContext, tool_registry
from app.security.service import set_security_service

# 組み込み Runtime のテスト用のツール・Skill・業務 Agent（test_builtin_runtime の fixture）。
pytestmark = pytest.mark.usefixtures("_calls_fixture")

CHAT = "gen_ai.chat"
TOOL = "gen_ai.execute_tool"
APPROVAL = "agent.approval_wait"

Row = tuple[str | None, str | None, str, dict[str, Any] | None]


class _TracedScriptedModel(ScriptedModel):
    """OCI の client と同じく、モデルの呼び出しを `gen_ai.chat` の span で測る台本のモデル。"""

    async def get_response(self, *args: Any, **kwargs: Any) -> Any:
        with observed_span(
            CHAT,
            kind="client",
            attributes={
                "gen_ai.operation.name": "chat",
                "gen_ai.provider.name": "oracle",
                "gen_ai.request.model": "test-model",
            },
        ) as span:
            response = await super().get_response(*args, **kwargs)
            builtin_runtime._set_turn_kind(span, response)  # noqa: SLF001 - OCI の client と同じ印
            return response


def _script(monkeypatch: MonkeyPatch, *steps: Any) -> ScriptedModel:
    model = _TracedScriptedModel(list(steps))
    _script_model(monkeypatch)
    monkeypatch.setattr(builtin_runtime, "model_factory", lambda _target: model)
    return model


def _rows(run: RunState) -> list[Row]:
    """組み立てた行（実行の全体の span を除く。span の名前・工程の種類・状態・params）。

    params の Run の step の id は値が毎回変わるので省く（`_run_step_of` で確かめる）。
    """
    rows: list[Row] = []
    for step in fold_chat_progress_events(run.progress_events).step_list():
        span = step.span or {}
        if span.get("root"):
            continue
        params = {key: value for key, value in (step.params or {}).items() if key != RUN_STEP_PARAM}
        kind = step.kind if step.kind != step.step_id else None
        rows.append((span.get("name"), kind, step.status, params or None))
    return rows


def _terminal(run: RunState) -> str | None:
    return fold_chat_progress_events(run.progress_events).terminal


def _roots(run: RunState) -> list[Any]:
    return [
        step
        for step in fold_chat_progress_events(run.progress_events).step_list()
        if (step.span or {}).get("root")
    ]


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
# 工程の種類
# --------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("output", "kind"),
    [
        ([{"type": "reasoning"}, {"type": "function_call"}], "plan"),
        ([{"type": "message"}], "respond"),
        ([{"type": "reasoning"}], None),
        (None, None),
    ],
    ids=["tool", "answer", "reasoning-only", "none"],
)
def test_model_turn_kind_follows_the_response_output(
    output: list[dict[str, str]] | None, kind: str | None
) -> None:
    assert model_turn_kind(output) == kind


# --------------------------------------------------------------------------------------------
# 記録の位置
# --------------------------------------------------------------------------------------------


def test_queued_run_has_no_progress_yet() -> None:
    """受け付けただけの Run には span が無い（実行の区間が始まってから記録する）。"""
    run = runtime_repository.get_run(_create_run())

    assert run.progress_events == []
    assert _terminal(run) is None


def test_answer_without_tools_records_one_model_call(monkeypatch: MonkeyPatch) -> None:
    _script(monkeypatch, [assistant_message("こんにちは。")])
    run_id = _create_run("挨拶して")

    anyio.run(builtin_runtime.execute_run, run_id)

    run = runtime_repository.get_run(run_id)
    assert _rows(run) == [(CHAT, "respond", "done", None)]
    assert _terminal(run) == "done"
    _assert_contiguous(run)
    # 実行の全体の span（root）も記録し（行にはしない）、モデルの呼び出しはその子。
    [root] = _roots(run)
    assert root.span is not None and root.span["name"] == "agent.execute"
    fold = fold_chat_progress_events(run.progress_events)
    [chat] = [step for step in fold.step_list() if (step.span or {}).get("name") == CHAT]
    assert chat.span is not None
    assert chat.span["parent_id"] == root.step_id
    assert chat.span["trace_id"] == root.span["trace_id"]
    assert chat.span["attributes"]["gen_ai.request.model"] == "test-model"
    assert chat.started_at is not None and chat.finished_at is not None


def test_tool_call_records_plan_tool_and_respond(monkeypatch: MonkeyPatch) -> None:
    _script(
        monkeypatch,
        [function_call(LOOKUP, {"query": "合成の語"}, call_id="call-1")],
        [assistant_message("3 件です。")],
    )
    run_id = _create_run()

    anyio.run(builtin_runtime.execute_run, run_id)

    run = runtime_repository.get_run(run_id)
    assert _rows(run) == [
        (CHAT, "plan", "done", None),
        (TOOL, "tool", "done", {"tool": LOOKUP}),
        (CHAT, "respond", "done", None),
    ]
    assert _terminal(run) == "done"
    _assert_contiguous(run)
    # ツールの span は Run の step と結ぶ（画面がデータの範囲・表の結果を足す）。
    [step] = run.steps
    finished = [
        event
        for event in run.progress_events
        if isinstance(event, ChatProgressStepEvent)
        and event.name == TOOL
        and event.status == "done"
    ]
    assert len(finished) == 1
    assert finished[0].params is not None
    assert finished[0].params[RUN_STEP_PARAM] == step.id
    assert finished[0].attributes is not None
    assert finished[0].attributes["gen_ai.tool.name"] == LOOKUP
    # 終わったツールの詳細（内部名・引数・結果）は、その span の終わりのイベントで送る（#2373）。
    details = finished[0].details
    assert details is not None
    assert [(field.label, field.value) for field in details.fields][0] == ("ツール", LOOKUP)
    labels = [block.label for block in details.code]
    assert labels[0] == "引数"
    assert "合成の語" in details.code[0].text
    assert "結果" in labels


def test_running_tool_is_visible_while_it_runs(monkeypatch: MonkeyPatch) -> None:
    """実行中のツールは、終わる前から実行中の span として記録され、呼んだ引数を開いて見られる。"""
    seen: list[list[Row]] = []
    details: list[Any] = []

    def lookup(_arguments: dict[str, Any], context: ToolInvocationContext) -> dict[str, Any]:
        current = runtime_repository.get_run(context.run_id or "")
        seen.append(_rows(current))
        details.extend(
            event.details
            for event in current.progress_events
            if isinstance(event, ChatProgressStepEvent) and event.name == TOOL
        )
        return {"rows": 1}

    definition = tool_registry.get(LOOKUP)
    assert definition is not None
    tool_registry.register(definition, lookup)
    _script(
        monkeypatch,
        [function_call(LOOKUP, {"query": "合成の語"}, call_id="call-1")],
        [assistant_message("1 件です。")],
    )
    run_id = _create_run()

    anyio.run(builtin_runtime.execute_run, run_id)

    assert seen == [[(CHAT, "plan", "done", None), (TOOL, "tool", "running", {"tool": LOOKUP})]]
    assert details and details[0] is not None
    assert "合成の語" in details[0].code[0].text


def test_parallel_tools_in_one_turn(monkeypatch: MonkeyPatch) -> None:
    # 即時に終わる fake は次のツールの開始より先に完了し得るため、両方の開始を待つ。
    started = Barrier(2)

    def parallel_lookup(
        _arguments: dict[str, Any], _context: ToolInvocationContext
    ) -> dict[str, Any]:
        started.wait(timeout=5)
        return {"name": LOOKUP, "rows": 3}

    definition = tool_registry.get(LOOKUP)
    assert definition is not None
    tool_registry.register(definition, parallel_lookup)
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
    assert [(name, kind) for name, kind, _status, _params in _rows(run)] == [
        (CHAT, "plan"),
        (TOOL, "tool"),
        (TOOL, "tool"),
        (CHAT, "respond"),
    ]
    assert {status for _, _, status, _ in _rows(run)} == {"done"}


def test_span_from_a_tool_thread_is_nested_under_the_tool(monkeypatch: MonkeyPatch) -> None:
    """ツールのスレッドで開いた span（MCP の呼び出しなど）も、ツールの span の子として記録する。"""

    def lookup(_arguments: dict[str, Any], _context: ToolInvocationContext) -> dict[str, Any]:
        with observed_span("mcp.request", kind="client", attributes={"pr.attempt": 1}):
            return {"rows": 1}

    definition = tool_registry.get(LOOKUP)
    assert definition is not None
    tool_registry.register(definition, lookup)
    _script(
        monkeypatch,
        [function_call(LOOKUP, {"query": "x"}, call_id="call-1")],
        [assistant_message("1 件です。")],
    )
    run_id = _create_run()

    anyio.run(builtin_runtime.execute_run, run_id)

    run = runtime_repository.get_run(run_id)
    steps = fold_chat_progress_events(run.progress_events).step_list()
    [tool] = [step for step in steps if (step.span or {}).get("name") == TOOL]
    [mcp] = [step for step in steps if (step.span or {}).get("name") == "mcp.request"]
    assert mcp.span is not None and mcp.span["parent_id"] == tool.step_id
    assert mcp.status == "done"


def test_approval_wait_is_recorded_when_the_run_resumes(monkeypatch: MonkeyPatch) -> None:
    _script(
        monkeypatch,
        [function_call(WRITE, {"query": "登録"}, call_id="call-w")],
        [assistant_message("登録しました。")],
    )
    run_id = _create_run("登録して")
    anyio.run(builtin_runtime.execute_run, run_id)

    waiting = runtime_repository.get_run(run_id)
    # 待っている間は待ちの行を出さない（見出しの状態と承認のカードで示す）。実行の区間は閉じる。
    assert _rows(waiting) == [(CHAT, "plan", "done", None)]
    assert _terminal(waiting) is None
    assert [root.status for root in _roots(waiting)] == ["done"]

    _approve(run_id)
    anyio.run(builtin_runtime.resume_run, run_id)

    run = runtime_repository.get_run(run_id)
    assert _rows(run) == [
        (CHAT, "plan", "done", None),
        (APPROVAL, "approval", "done", {"tools": WRITE, "round": 1}),
        (TOOL, "tool", "done", {"tool": WRITE}),
        (CHAT, "respond", "done", None),
    ]
    assert _terminal(run) == "done"
    _assert_contiguous(run)
    # 再開の区間は同じ trace の、実行の区間の子（画面は 2 つの root を行にせず、1 本の木にする）。
    execute, resume = _roots(run)
    assert (execute.span["name"], resume.span["name"]) == ("agent.execute", "agent.resume")
    assert resume.span["parent_id"] == execute.step_id
    assert resume.span["trace_id"] == execute.span["trace_id"]
    approval = run.approvals[0]
    fold = fold_chat_progress_events(run.progress_events)
    [wait] = [step for step in fold.step_list() if (step.span or {}).get("name") == APPROVAL]
    assert wait.span is not None and wait.span["parent_id"] == resume.step_id
    assert (wait.started_at, wait.finished_at) == (approval.created_at, approval.decided_at)


def test_tool_titles_are_in_tool_and_approval_params(monkeypatch: MonkeyPatch) -> None:
    """ツールの表示名（MCP の `title`。Run の metadata）があれば、ツールの span の params に
    `title`、承認の待ちの補足に表示名を出す（内部名の `tool` は残す）。"""
    _script(
        monkeypatch,
        [function_call(WRITE, {"query": "登録"}, call_id="call-t")],
        [assistant_message("登録しました。")],
    )
    run_id = _create_run("登録して")
    runtime_repository.note_builtin_tool_titles(run_id, {WRITE: "記録の登録"})
    anyio.run(builtin_runtime.execute_run, run_id)
    _approve(run_id)
    anyio.run(builtin_runtime.resume_run, run_id)

    run = runtime_repository.get_run(run_id)
    assert _rows(run)[1:3] == [
        (APPROVAL, "approval", "done", {"tools": "記録の登録", "round": 1}),
        (TOOL, "tool", "done", {"tool": WRITE, "title": "記録の登録"}),
    ]
    # 表示名は足すだけ（空は記録しない）。
    runtime_repository.note_builtin_tool_titles(run_id, {})
    assert runtime_repository.get_run(run_id).metadata["tool_titles"] == {WRITE: "記録の登録"}


def test_rejected_tool_is_a_skipped_tool_span(monkeypatch: MonkeyPatch) -> None:
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
    assert _rows(run) == [
        (CHAT, "plan", "done", None),
        (APPROVAL, "approval", "done", {"tools": WRITE, "round": 1}),
        (TOOL, "tool", "skipped", {"tool": WRITE, "rejected": True}),
    ]
    assert _terminal(run) == "done"


def test_second_approval_round_is_its_own_wait(monkeypatch: MonkeyPatch) -> None:
    """承認の後に別の承認を求めたら、承認の待ちを回ごとに出す（同じ待ちを重ねない。#1358）。"""
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
    assert _rows(second) == [
        (CHAT, "plan", "done", None),
        (APPROVAL, "approval", "done", {"tools": WRITE, "round": 1}),
        (TOOL, "tool", "done", {"tool": WRITE}),
        (CHAT, "plan", "done", None),
    ]

    _approve(run_id)
    anyio.run(builtin_runtime.resume_run, run_id)

    run = runtime_repository.get_run(run_id)
    assert _rows(run) == [
        (CHAT, "plan", "done", None),
        (APPROVAL, "approval", "done", {"tools": WRITE, "round": 1}),
        (TOOL, "tool", "done", {"tool": WRITE}),
        (CHAT, "plan", "done", None),
        (APPROVAL, "approval", "done", {"tools": WRITE, "round": 2}),
        (TOOL, "tool", "done", {"tool": WRITE}),
        (CHAT, "respond", "done", None),
    ]
    assert _terminal(run) == "done"
    _assert_contiguous(run)
    # 3 つの区間（実行・再開・再開）は 1 つの trace。
    assert len({root.span["trace_id"] for root in _roots(run)}) == 1


def test_invalid_arguments_are_a_skipped_tool_span(monkeypatch: MonkeyPatch) -> None:
    """引数の誤りで呼び先へ送らなかった呼び出しも、実行しなかったツールとして残す（#3226）。"""
    _script(
        monkeypatch,
        [function_call(LOOKUP, {"other": "x"}, call_id="call-bad")],
        [assistant_message("調べられませんでした。")],
    )
    run_id = _create_run()

    anyio.run(builtin_runtime.execute_run, run_id)

    run = runtime_repository.get_run(run_id)
    rows = _rows(run)
    assert rows[0] == (CHAT, "plan", "done", None)
    assert rows[1] == (TOOL, "tool", "skipped", {"tool": LOOKUP})
    skipped = [
        event
        for event in run.progress_events
        if isinstance(event, ChatProgressStepEvent) and event.name == TOOL
    ]
    details = skipped[-1].details
    assert details is not None
    assert any(field.label == "エラー" for field in details.fields)


def test_unattended_run_keeps_the_tool_it_did_not_run(monkeypatch: MonkeyPatch) -> None:
    """承認の要るツールを呼ばない Run（外部の MCP クライアント）も、呼ばなかったツールを残す。"""
    _script(
        monkeypatch,
        [function_call(WRITE, {"query": "登録"}, call_id="call-mcp")],
        [assistant_message("登録はできませんでした。")],
    )
    run_id = runtime_repository.create_builtin_run(
        RunCreateRequest(goal="登録して", agent_id=AGENT_ID),
        created_by_user_uuid=LOCAL_DEBUG_USER_UUID,
        extra_metadata={"source": "mcp", "api_key_id": "0123456789abcdef"},
    ).id

    anyio.run(builtin_runtime.execute_run, run_id)

    run = runtime_repository.get_run(run_id)
    assert _rows(run) == [
        (CHAT, "plan", "done", None),
        (TOOL, "tool", "skipped", {"tool": WRITE}),
        (CHAT, "respond", "done", None),
    ]
    [step] = run.steps
    skipped = [
        event
        for event in run.progress_events
        if isinstance(event, ChatProgressStepEvent) and event.name == TOOL
    ]
    assert skipped[-1].params is not None and skipped[-1].params[RUN_STEP_PARAM] == step.id


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
    rows = _rows(run)
    assert rows[0][:3] == (CHAT, "plan", "done")
    assert not any(status == "running" for _, _, status, _ in rows)


def test_cancelled_run_records_cancelled_terminal() -> None:
    run_id = _create_run()

    cancelled = runtime_repository.cancel_run(run_id)

    assert _rows(cancelled) == []
    assert _terminal(cancelled) == "cancelled"
    # 終端の後は記録しない（もう一度呼んでも増えない）。
    before = len(cancelled.progress_events)
    record_run_terminal(cancelled)
    assert len(cancelled.progress_events) == before


def test_cancel_while_waiting_for_approval_ends_the_progress(monkeypatch: MonkeyPatch) -> None:
    _script(monkeypatch, [function_call(WRITE, {"query": "登録"}, call_id="call-c")])
    run_id = _create_run("登録して")
    anyio.run(builtin_runtime.execute_run, run_id)

    run = runtime_repository.cancel_run(run_id)

    assert _rows(run) == [(CHAT, "plan", "done", None)]
    assert _terminal(run) == "cancelled"


def test_cancel_from_another_context_while_a_tool_runs(monkeypatch: MonkeyPatch) -> None:
    """実行中のツールの間に別の文脈（画面の操作）で停止したら、終端は停止で、開いていた span は
    スキップで閉じる。停止の後に終わった span は記録しない（終端の後に行を増やさない）。"""

    def lookup(_arguments: dict[str, Any], context: ToolInvocationContext) -> dict[str, Any]:
        # 実行の文脈（trace の記録）を持たない、新しい文脈で停止する。
        contextvars.Context().run(runtime_repository.cancel_run, context.run_id or "")
        return {"rows": 1}

    definition = tool_registry.get(LOOKUP)
    assert definition is not None
    tool_registry.register(definition, lookup)
    _script(
        monkeypatch,
        [function_call(LOOKUP, {"query": "x"}, call_id="call-1")],
        [assistant_message("1 件です。")],
    )
    run_id = _create_run()

    anyio.run(builtin_runtime.execute_run, run_id)

    run = runtime_repository.get_run(run_id)
    assert run.status == "cancelled"
    assert _terminal(run) == "cancelled"
    assert isinstance(run.progress_events[-1], ChatProgressTerminalEvent)
    assert _rows(run) == [
        (CHAT, "plan", "done", None),
        (TOOL, "tool", "skipped", {"tool": LOOKUP}),
    ]
    assert all(root.status == "skipped" for root in _roots(run))
    _assert_contiguous(run)


# --------------------------------------------------------------------------------------------
# 保存と読み込み
# --------------------------------------------------------------------------------------------


def _seed_completed_run(repository: AgentRuntimeRepository) -> RunState:
    run = repository.create_builtin_run(RunCreateRequest(goal="保存", agent_id="default"))
    assert repository.begin_builtin_run(run.id) is not None
    from app.features.agent.tools import ToolResult

    with operation_trace(
        "agent.execute",
        operation_id=run.id,
        listener=lambda update: repository.record_builtin_span(run.id, update),
    ):
        with observed_span("gen_ai.execute_tool", attributes={"gen_ai.tool.name": "echo"}):
            step_id, _context = repository.start_builtin_tool_step(
                run.id, ToolCall(name="echo", arguments={"text": "x"}, trace_id="call-1")
            )
            repository.finish_builtin_tool_step(
                run.id, step_id, ToolResult(name="echo", success=True, output={"text": "x"})
            )
        return repository.complete_builtin_run(run.id, "回答しました。")


def test_progress_events_survive_the_file_snapshot(tmp_path: Path) -> None:
    path = tmp_path / "runtime.json"
    repository = AgentRuntimeRepository(snapshot_path=path)
    completed = _seed_completed_run(repository)
    assert _terminal(completed) == "done"
    assert [row[0] for row in _rows(completed)] == ["gen_ai.execute_tool"]

    reloaded = AgentRuntimeRepository(snapshot_path=path).get_run(completed.id)

    assert reloaded.progress_events == completed.progress_events
    # 保存の JSON は値の無い項目を出さない（契約の JSON と同じ形）。
    saved = json.loads(path.read_text(encoding="utf-8"))
    [saved_run] = [item for item in saved["runs"] if item["id"] == completed.id]
    assert all("detail" not in event for event in saved_run["progress_events"])


def test_span_records_are_saved_together_in_the_background(
    tmp_path: Path, monkeypatch: MonkeyPatch
) -> None:
    """span の知らせはメモリの Run にすぐ記録し、保存先へは少し待ってまとめて書く（知らせのたびに
    Run の JSON を書かない）。"""
    from app.features.agent import runtime as runtime_module

    monkeypatch.setattr(runtime_module, "BACKGROUND_WRITES", True)
    monkeypatch.setattr(runtime_module, "SPAN_PERSIST_DELAY_SECONDS", 0.05)
    path = tmp_path / "runtime.json"
    repository = AgentRuntimeRepository(snapshot_path=path)
    run = repository.create_builtin_run(RunCreateRequest(goal="保存", agent_id="default"))
    assert repository.begin_builtin_run(run.id) is not None
    repository.flush_writes()
    staged: list[int] = []
    original = repository._persist_locked  # noqa: SLF001 - 保存の予定の回数を数える

    def counting(*, background: bool = False) -> bool:
        staged.append(1)
        return original(background=background)

    monkeypatch.setattr(repository, "_persist_locked", counting)
    with operation_trace(
        "agent.execute",
        operation_id=run.id,
        listener=lambda update: repository.record_builtin_span(run.id, update),
    ):
        for _ in range(5):
            with observed_span("mcp.request", kind="client"):
                pass
        # メモリの Run にはすぐ入る。
        assert len(_rows(repository.get_run(run.id))) == 5

    def saved_rows() -> int:
        saved = json.loads(path.read_text(encoding="utf-8"))
        [item] = [entry for entry in saved["runs"] if entry["id"] == run.id]
        return sum(1 for event in item["progress_events"] if event.get("name") == "mcp.request")

    for _ in range(100):
        if repository.pending_writes() == 0 and saved_rows() == 10:
            break
        anyio.run(anyio.sleep, 0.05)
    # 開始と終了の 10 個のイベントが保存され、保存の予定は知らせの数より少ない。
    assert saved_rows() == 10
    assert 0 < len(staged) < 12


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
    assert [row[0] for row in _rows(reloaded)] == ["gen_ai.execute_tool"]


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


def _completed_api_run(monkeypatch: MonkeyPatch, owner: str = LOCAL_DEBUG_USER_UUID) -> str:
    _script(
        monkeypatch,
        [function_call(LOOKUP, {"query": "売上"}, call_id="call-1")],
        [assistant_message("3 件です。")],
    )
    # 会話は作った利用者だけが読む（local はローカル利用者）。
    run_id = runtime_repository.create_builtin_run(
        RunCreateRequest(goal="今月の売上を教えて", agent_id=AGENT_ID),
        created_by_user_uuid=owner,
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
    # span のイベントは span の名前・親・trace id を持つ（画面が木にする）。
    spans = [event for event in events if event["type"] == "step"]
    assert spans and all(event.get("name") and event.get("trace_id") for event in spans)
    assert {event["name"] for event in spans} >= {"agent.execute", CHAT, TOOL}
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
    owner = auth.user_with_permissions(
        "progress-chat", ["agent.runs.operate"], agent_ids=[AGENT_ID]
    )
    run_id = _completed_api_run(monkeypatch, owner=owner.user_uuid)
    paths = [f"/api/runs/{run_id}/progress", f"/api/runs/{run_id}/progress/stream?since=0"]

    # チャットの利用者（業務 Agent の実行）は、対象範囲の Agent の自分の Run を読める。
    headers = login("progress-chat")
    for path in paths:
        assert client.get(path, headers=headers).status_code == 200, path

    # 同じ範囲でも、ほかの利用者の Run は 404（存在を漏らさない。#1889）。
    auth.user_with_permissions("progress-peer", ["agent.runs.operate"], agent_ids=[AGENT_ID])
    peer = login("progress-peer")
    for path in paths:
        assert client.get(path, headers=peer).status_code == 404, path

    # 対象範囲の外の Agent の、ほかの利用者の Run も 404。
    auth.user_with_permissions("progress-other", ["agent.runs.operate"], agent_ids=["other"])
    other = login("progress-other")
    for path in paths:
        assert client.get(path, headers=other).status_code == 404, path

    # メニューも capability も無い利用者は経路で 403。
    auth.user_with_permissions("progress-none", [], agent_ids=[AGENT_ID])
    none = login("progress-none")
    for path in paths:
        assert client.get(path, headers=none).status_code == 403, path
    # ログインしていなければ 401。
    assert client.get(paths[0]).status_code == 401


def test_created_run_response_has_no_progress_yet(monkeypatch: MonkeyPatch) -> None:
    monkeypatch.setattr("app.features.agent.router._schedule_builtin_run", lambda run: None)

    created = client.post("/api/runs", json={"goal": "質問", "agent_id": "default"})

    assert created.status_code == 200, created.text
    # 処理の経過は実行の区間の span から記録する（受け付けただけの Run は空）。
    assert created.json()["data"]["progress_events"] == []
