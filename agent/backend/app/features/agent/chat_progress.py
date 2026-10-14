"""チャットの回答の処理の段階（3 製品共通のイベント。#1359）を、Run の状態から記録する。

段階の記録は `pr_backend_core.chat_progress.ChatProgressRecorder` に任せ、ここは「Run の状態の
どこが、どの段階の開始・終了か」だけを書く。repository が Run のイベントを足すたびに（Run の
lock の中で）`record_run_progress(run)` を呼ぶ。Run の状態から段階を照合する冪等な関数で、
変わりが無ければ記録しない（記録の no-op）ので、何度呼んでも重複しない。

段階（並びは最初に出た順。画面の名前は frontend の段階の定義が `kind` から付ける）:

- `plan`（kind `plan`）: Run を受け付けたら実行中。モデルが進め方を考えている。
- `tool:<名前>`（2 回目から `#n`。kind `tool`、params `{"tool": 名前}`）: ツールの呼び出し。
  呼び出しを始めたら、開いている「考える」段階（plan / respond）を完了にする。
  却下したツールはスキップ（params に `rejected: true`）。
- `approval_wait`（2 回目から `#n`。kind `approval`、params `{"tools": 承認を待つツール名}`）:
  承認を求めた回ごとに 1 つ。承認を待つツールの前に出し、ツールは待機中で出す（承認の後に実行）。
  その回の承認がすべて決まったら完了。
- `confirmation_wait`（2 回目から `#n`。kind `confirmation`）: Agent 自身の回答前の確認
  （`ask_user_confirmation`。#1839）で利用者の答えを待つ。答え・スキップで完了。確認のツールの呼び出し
  そのものは段階に出さない（利用者の答えを返すだけ）。
- `respond`（2 回目から `#n`。kind `respond`）: ツールがすべて終わり（承認の待ちも無く）、モデルが
  回答を作っている。その後に別のツールを呼んだら、その段階は kind `plan`（進め方の検討）に変えて
  完了にする（完了した段階を実行中に戻さない・消さない。#1358）。
- 終わり: 完了は開いている段階を完了にして `complete("done")`（ツールを呼ばずに答えたときは
  plan 完了と respond 完了の 2 つ）、失敗は `complete("failed")`、停止は `complete("cancelled")`。
"""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

from pr_backend_core.chat_progress import ChatProgressRecorder, ChatProgressStepState

if TYPE_CHECKING:
    from app.features.agent.confirmation import ConfirmationRequest
    from app.features.agent.runtime import ApprovalRequest, RunState, RunStep

# 段階の id・種類（画面の段階の定義の key と同じ）。
PLAN_STEP = "plan"
RESPOND_STEP = "respond"
APPROVAL_STEP = "approval_wait"
CONFIRMATION_STEP = "confirmation_wait"
# 回答前の確認のツール（段階には確認の待ちとして出し、ツールの呼び出しとしては出さない）。
_CONFIRMATION_TOOL = "ask_user_confirmation"
TOOL_STEP_PREFIX = "tool:"
KIND_PLAN = "plan"
KIND_TOOL = "tool"
KIND_APPROVAL = "approval"
KIND_RESPOND = "respond"
KIND_CONFIRMATION = "confirmation"
# 承認を待つツール名をつなぐ記号（画面の補足にそのまま出す）。
TOOL_NAME_SEPARATOR = "、"

# 記録する Run の Runtime（組み込み Runtime だけ。旧エンジンの Run は実行しない）。
_BUILTIN_RUNTIME_ID = "builtin"
# 「考える」段階（モデルが次の手を決めている間）。ツールを呼んだら終わる。
_THINKING_KINDS = frozenset({KIND_PLAN, KIND_RESPOND})
_FINISHED_TOOL_STATUSES = frozenset({"completed", "failed", "cancelled"})
_RECORDED_FINISHED = frozenset({"done", "failed", "skipped"})


def record_run_progress(run: RunState) -> None:
    """Run の今の状態を処理の段階のイベントに照合し、変わった段階だけを
    `run.progress_events` に足す。

    Run の lock の中で呼ぶ（記録はスレッドから同時に呼ばない）。何度呼んでも、状態が変わらなければ
    何も記録しない。終端を記録した後は何もしない。
    """

    if run.runtime_id != _BUILTIN_RUNTIME_ID:
        return
    recorder = ChatProgressRecorder(run.id, events=run.progress_events)
    if recorder.terminal is not None:
        return
    before = recorder.last_seq
    _reconcile(run, recorder)
    if recorder.last_seq != before:
        run.progress_events = recorder.events


def _reconcile(run: RunState, recorder: ChatProgressRecorder) -> None:
    status = str(run.status)
    if status == "failed":
        recorder.complete("failed")
        return
    if status == "cancelled":
        recorder.complete("cancelled")
        return
    # 受け付けた Run（開始待ちを含む）は、モデルが考えている段階から始める。承認の後の再開で
    # 開始待ちに戻っても、完了した plan は実行中に戻らない（記録が戻さない）。
    if not recorder.steps():
        recorder.start(PLAN_STEP, kind=KIND_PLAN, at=run.created_at)

    tool_steps = [
        step
        for step in run.steps
        if step.tool_call is not None and step.tool_call.name != _CONFIRMATION_TOOL
    ]
    approvals = {approval.id: approval for approval in run.approvals}
    rounds = approval_rounds(run.approvals)
    round_index = {approval.id: index for index, round_ in enumerate(rounds) for approval in round_}

    counts: dict[str, int] = {}
    for step in tool_steps:
        name = step.tool_call.name if step.tool_call is not None else ""
        counts[name] = counts.get(name, 0) + 1
        step_id = tool_step_id(name, counts[name])
        approval = approvals.get(step.approval_id) if step.approval_id else None
        if approval is not None and approval.id in round_index:
            index = round_index[approval.id]
            _sync_approval_round(recorder, rounds[index], index)
        _sync_tool_step(recorder, step, step_id, name, approval)

    confirmations = sorted(
        (item for item in run.confirmations if item.source == "agent"),
        key=lambda item: item.created_at,
    )
    for index, confirmation in enumerate(confirmations):
        _sync_confirmation(recorder, confirmation, index)

    if status == "running" and _ready_to_respond(run, tool_steps) and not _thinking_steps(recorder):
        recorder.start(_next_respond_id(recorder), kind=KIND_RESPOND)
    if status == "completed":
        if not any(step.kind == KIND_RESPOND for step in _thinking_steps(recorder)):
            # ツールを呼ばずに答えた（plan のまま）・考える段階が開いていない: 考える段階を閉じて、
            # 回答の作成を完了で出す（始まりの時刻は無い）。
            _close_thinking(recorder)
            recorder.finish(_next_respond_id(recorder), kind=KIND_RESPOND)
        recorder.complete("done")


def _sync_tool_step(
    recorder: ChatProgressRecorder,
    step: RunStep,
    step_id: str,
    name: str,
    approval: ApprovalRequest | None,
) -> None:
    params: dict[str, str | int | float | bool] = {"tool": name}
    status = str(step.status)
    current = recorder.step(step_id)
    if current is not None and current.status in _RECORDED_FINISHED:
        # 記録済みの終わったツール（照合のたびに考える段階を閉じない）。
        return
    if status in {"waiting_approval", "pending"}:
        # 承認待ち・承認の後の実行待ち。まだ実行していないので待機中で出す。
        if current is None:
            _close_thinking(recorder, at=step.started_at)
            recorder.declare(step_id, kind=KIND_TOOL)
            recorder.update(step_id, params=params)
        return
    if status == "running":
        _close_thinking(recorder, at=step.started_at)
        recorder.start(step_id, kind=KIND_TOOL, params=params, at=step.started_at)
        return
    if status not in _FINISHED_TOOL_STATUSES:
        return
    if status == "cancelled":
        rejected = approval is not None and str(approval.status) == "rejected"
        if rejected:
            params = {**params, "rejected": True}
        _close_thinking(recorder, at=step.completed_at)
        recorder.finish(
            step_id,
            "skipped",
            kind=KIND_TOOL,
            params=params,
            at=step.completed_at,
        )
        return
    if current is None or current.status == "pending":
        # 開始を記録する前に終わった（1 回の更新で始まって終わった）ツールも、開始の時刻を残す。
        _close_thinking(recorder, at=step.started_at)
        recorder.start(step_id, kind=KIND_TOOL, params=params, at=step.started_at)
    recorder.finish(
        step_id,
        "done" if status == "completed" else "failed",
        kind=KIND_TOOL,
        params=params,
        at=step.completed_at,
    )


def _sync_approval_round(
    recorder: ChatProgressRecorder, round_: list[ApprovalRequest], index: int
) -> None:
    step_id = approval_step_id(index)
    pending = [approval for approval in round_ if str(approval.status) == "pending"]
    started_at = min(approval.created_at for approval in round_)
    if pending:
        current = recorder.step(step_id)
        if current is None or current.status == "pending":
            _close_thinking(recorder, at=started_at)
        recorder.start(
            step_id,
            kind=KIND_APPROVAL,
            params={"tools": _tool_names(pending)},
            at=started_at,
        )
        return
    current = recorder.step(step_id)
    if current is not None and current.status in _RECORDED_FINISHED:
        return
    if current is None:
        recorder.start(
            step_id, kind=KIND_APPROVAL, params={"tools": _tool_names(round_)}, at=started_at
        )
    decided = [approval.decided_at for approval in round_ if approval.decided_at is not None]
    recorder.finish(step_id, kind=KIND_APPROVAL, at=max(decided) if decided else None)


def _sync_confirmation(
    recorder: ChatProgressRecorder, confirmation: ConfirmationRequest, index: int
) -> None:
    """Agent 自身の回答前の確認の待ち（#1839）。答えるまで実行中、答え・スキップで完了。"""
    step_id = CONFIRMATION_STEP if index == 0 else f"{CONFIRMATION_STEP}#{index + 1}"
    current = recorder.step(step_id)
    if current is not None and current.status in _RECORDED_FINISHED:
        return
    if confirmation.status == "pending":
        if current is None or current.status == "pending":
            _close_thinking(recorder, at=confirmation.created_at)
        recorder.start(step_id, kind=KIND_CONFIRMATION, at=confirmation.created_at)
        return
    if current is None:
        _close_thinking(recorder, at=confirmation.created_at)
        recorder.start(step_id, kind=KIND_CONFIRMATION, at=confirmation.created_at)
    if confirmation.status in {"answered", "skipped"}:
        recorder.finish(step_id, kind=KIND_CONFIRMATION, at=confirmation.decided_at)
    else:
        recorder.finish(step_id, "skipped", kind=KIND_CONFIRMATION, at=confirmation.decided_at)


def _ready_to_respond(run: RunState, tool_steps: list[RunStep]) -> bool:
    """ツールがすべて終わり、承認・確認の待ちも無い（モデルが結果から回答を作っている）。"""
    if not tool_steps:
        return False
    if any(str(approval.status) == "pending" for approval in run.approvals):
        return False
    if any(item.source == "agent" and item.status == "pending" for item in run.confirmations):
        return False
    return all(str(step.status) in _FINISHED_TOOL_STATUSES for step in tool_steps)


def _thinking_steps(recorder: ChatProgressRecorder) -> list[ChatProgressStepState]:
    return [
        step
        for step in recorder.steps()
        if step.status == "running" and step.kind in _THINKING_KINDS
    ]


def _close_thinking(recorder: ChatProgressRecorder, *, at: datetime | None = None) -> None:
    """開いている「考える」段階を完了にする（回答の作成の後にツールを呼んだら、進め方の検討に変える）。"""
    for step in _thinking_steps(recorder):
        recorder.finish(step.step_id, kind=KIND_PLAN, at=at)


def _next_respond_id(recorder: ChatProgressRecorder) -> str:
    count = sum(
        1
        for step in recorder.steps()
        if step.step_id == RESPOND_STEP or step.step_id.startswith(f"{RESPOND_STEP}#")
    )
    return RESPOND_STEP if count == 0 else f"{RESPOND_STEP}#{count + 1}"


def _tool_names(approvals: list[ApprovalRequest]) -> str:
    names: list[str] = []
    for approval in approvals:
        if approval.tool_call.name not in names:
            names.append(approval.tool_call.name)
    return TOOL_NAME_SEPARATOR.join(names)


def tool_step_id(name: str, count: int) -> str:
    """ツールの段階の id（同じツールの 2 回目から回数を付ける。id は Run の中で一意）。"""
    return f"{TOOL_STEP_PREFIX}{name}" if count <= 1 else f"{TOOL_STEP_PREFIX}{name}#{count}"


def approval_step_id(index: int) -> str:
    """承認待ちの段階の id（承認を求めた回ごと。2 回目から回数を付ける。#1358）。"""
    return APPROVAL_STEP if index == 0 else f"{APPROVAL_STEP}#{index + 1}"


def approval_rounds(approvals: list[ApprovalRequest]) -> list[list[ApprovalRequest]]:
    """承認を求めた回（同じ中断で求めた承認）に分ける。

    前の回の承認がすべて決まった後に求めた承認は、次の回にする（1 回の中断で求めた承認は、
    すべて決まるまで次の中断が起きない）。
    """
    rounds: list[list[ApprovalRequest]] = []
    for approval in sorted(approvals, key=lambda item: item.created_at):
        current = rounds[-1] if rounds else None
        closed = current is not None and all(
            str(item.status) != "pending"
            and item.decided_at is not None
            and item.decided_at <= approval.created_at
            for item in current
        )
        if current is None or closed:
            rounds.append([approval])
        else:
            current.append(approval)
    return rounds


__all__ = [
    "APPROVAL_STEP",
    "KIND_APPROVAL",
    "KIND_PLAN",
    "KIND_RESPOND",
    "KIND_TOOL",
    "PLAN_STEP",
    "RESPOND_STEP",
    "approval_rounds",
    "approval_step_id",
    "record_run_progress",
    "tool_step_id",
]
