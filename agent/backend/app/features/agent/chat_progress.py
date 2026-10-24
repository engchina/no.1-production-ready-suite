"""チャットの処理の経過（3 製品共通のイベント。#1359）を、Run の trace（span の木）から記録する。

処理の経過は trace の投影（#3226）: 組み込み Runtime の実行区間（`agent.execute` /
`agent.resume`）の `operation_trace(listener=...)` が知らせる span の開始・表示用のデータの
変更・終了を、`ChatProgressRecorder.record_span` で `run.progress_events` に記録する（Run の
lock の中。`AgentRuntimeRepository.record_builtin_span`）。終端（完了・失敗・停止）の
イベントは、Run の状態が終わったときに `record_run_terminal` が記録する（Run のイベントを
足すたびに呼ぶ。冪等）。

工程を測る span の表示用のデータ（`SpanDisplay`。画面の名前は frontend の段の定義が `kind` から
付ける）:

- `gen_ai.chat`（モデルの呼び出し）: 応答がツールを求めたら kind `plan`（進め方の検討）、最終の
  回答を作ったら kind `respond`（回答の作成）。呼び出しの後に決める（`model_turn_kind`）。
- `gen_ai.execute_tool`（ツールの呼び出し）: kind `tool`、params `{"tool": 名前, "run_step":
  Run の step の id}`（表示名〔MCP の `title`。Run の metadata `tool_titles`〕があれば `title`
  も）、details はツールの内部名・エラー・引数と結果（`tool_details`）。実行しなかったツール
  （却下・方針で拒否・上限・引数の誤り）も `skipped` の span で残し、承認の却下は params に
  `rejected: true`。
- `agent.approval_wait`（kind `approval`、params `{"tools": 承認を待ったツール名, "round": 回}`）
  と `agent.confirmation_wait`（kind `confirmation`、params `{"round": 回}`）: 承認・Agent
  自身の回答前の確認（`ask_user_confirmation`。#1839）の待ちの区間。待ちが終わって再開した区間で、
  終わった区間として記録する（`record_span`）。待っている間は、見出しの状態と承認・確認の
  カードで示す。
- 回答前の確認のツールの呼び出しそのものは span にしない（利用者の答えを返すだけ。待ちの span が
  表す）。
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Sequence
from typing import TYPE_CHECKING, Any, Literal

from pr_backend_core.chat_progress import (
    ChatProgressParamValue,
    ChatProgressRecorder,
    ChatProgressStepDetails,
    chat_progress_details,
)
from pr_backend_core.observability.tracing import SpanDisplay, SpanUpdate

if TYPE_CHECKING:
    from app.features.agent.confirmation import ConfirmationRequest
    from app.features.agent.runtime import ApprovalRequest, RunState, RunStep
    from app.features.agent.tools import ToolCall, ToolResult

# 工程の種類（画面の段の定義の key と同じ）。
KIND_PLAN = "plan"
KIND_TOOL = "tool"
KIND_APPROVAL = "approval"
KIND_RESPOND = "respond"
KIND_CONFIRMATION = "confirmation"
# 承認・確認の待ちの span の名前。
APPROVAL_WAIT_SPAN = "agent.approval_wait"
CONFIRMATION_WAIT_SPAN = "agent.confirmation_wait"
# ツールの呼び出しの span の名前（OpenTelemetry GenAI semantic conventions）。
EXECUTE_TOOL_SPAN = "gen_ai.execute_tool"
# 回答前の確認のツール（span にしない）。
CONFIRMATION_TOOL = "ask_user_confirmation"
# 承認を待つツール名をつなぐ記号（画面の補足にそのまま出す）。
TOOL_NAME_SEPARATOR = "、"
# Run に渡したツールの表示名（function tool の名前 → MCP の `title`）を残す metadata の key。
TOOL_TITLES_METADATA_KEY = "tool_titles"
# ツールの span の params の、Run の step の id の key（画面がツールの step の記録と結ぶ）。
RUN_STEP_PARAM = "run_step"

# 記録する Run の Runtime（組み込み Runtime だけ。旧エンジンの Run は実行しない）。
_BUILTIN_RUNTIME_ID = "builtin"
_TERMINAL_STATUSES: dict[str, Literal["done", "failed", "cancelled"]] = {
    "completed": "done",
    "failed": "failed",
    "cancelled": "cancelled",
}


def record_run_span(run: RunState, update: SpanUpdate) -> bool:
    """span の知らせを Run の処理の経過のイベントに記録する（記録したら True）。

    Run の lock の中で呼ぶ。終端を記録した後の知らせ（停止の後に終わった span など）は記録しない。
    """
    if run.runtime_id != _BUILTIN_RUNTIME_ID:
        return False
    recorder = ChatProgressRecorder(run.id, events=run.progress_events)
    if recorder.terminal is not None:
        return False
    if recorder.record_span(update) is None:
        return False
    run.progress_events = recorder.events
    return True


def record_run_terminal(run: RunState) -> None:
    """終わった Run（完了・失敗・停止）の処理の経過に終端を記録する。

    Run の lock の中で呼ぶ。終わっていない Run・終端を記録済みの Run には何もしない（冪等）。
    開いている span は終端の状態で閉じる（停止はスキップ）。
    """
    if run.runtime_id != _BUILTIN_RUNTIME_ID:
        return
    terminal = _TERMINAL_STATUSES.get(str(run.status))
    if terminal is None:
        return
    recorder = ChatProgressRecorder(run.id, events=run.progress_events)
    if recorder.complete(terminal) is not None:
        run.progress_events = recorder.events


def model_turn_kind(output: Iterable[Any] | None) -> str | None:
    """モデルの応答の工程の種類（ツールを求めたら進め方の検討、最終の回答なら回答の作成）。

    `output` は Responses API の出力の要素（object か dict）。どちらでもなければ None。
    """
    kinds = {_item_type(item) for item in output or ()}
    if "function_call" in kinds:
        return KIND_PLAN
    if "message" in kinds:
        return KIND_RESPOND
    return None


def _item_type(item: Any) -> str:
    value = item.get("type") if isinstance(item, dict) else getattr(item, "type", None)
    return value if isinstance(value, str) else ""


def tool_params(
    name: str,
    titles: dict[str, str],
    *,
    run_step: str | None = None,
    rejected: bool = False,
) -> dict[str, ChatProgressParamValue]:
    """ツールの span の名前の値（内部名・表示名・Run の step・却下）。"""
    params: dict[str, ChatProgressParamValue] = {"tool": name}
    title = titles.get(name)
    if title:
        params["title"] = title
    if run_step:
        params[RUN_STEP_PARAM] = run_step
    if rejected:
        params["rejected"] = True
    return params


def tool_display(
    call: ToolCall,
    titles: dict[str, str],
    *,
    result: ToolResult | None = None,
    run_step: str | None = None,
    rejected: bool = False,
) -> SpanDisplay:
    """ツールの span の表示用のデータ（kind `tool`・名前の値・開いて見る詳細）。"""
    return SpanDisplay(
        kind=KIND_TOOL,
        params=tool_params(call.name, titles, run_step=run_step, rejected=rejected),
        details=tool_details(call, result),
    )


def tool_attributes(name: str, call_id: str | None) -> dict[str, object]:
    """ツールの span の属性（OpenTelemetry GenAI の execute_tool）。"""
    return {
        "gen_ai.operation.name": "execute_tool",
        "gen_ai.tool.name": name,
        "gen_ai.tool.call.id": call_id or None,
    }


# ツールの段の詳細の見出し（画面の `chat.progress.details.*` と同じ文言）。
_DETAIL_TOOL = "ツール"
_DETAIL_ERROR = "エラー"
_DETAIL_ARGUMENTS = "引数"
_DETAIL_RESULT = "結果"


def tool_details(
    call: ToolCall | None, result: ToolResult | None = None
) -> ChatProgressStepDetails | None:
    """ツールの段の詳細（内部名・エラー・引数と結果の JSON）。

    上限で切り、秘密らしい値を伏せる（共通の規則）。データの範囲の記録は画面が Run の step から
    足す。
    """
    fields: list[tuple[str, object] | tuple[str, object, bool]] = []
    if call is not None and call.name:
        fields.append((_DETAIL_TOOL, call.name, True))
    if result is not None and result.error:
        fields.append((_DETAIL_ERROR, result.error))
    code: list[tuple[str | None, str, Literal["json", "sql", "text"] | None]] = []
    if call is not None and call.arguments:
        code.append((_DETAIL_ARGUMENTS, _json_text(call.arguments), "json"))
    if result is not None and result.output is not None:
        code.append((_DETAIL_RESULT, _json_text(result.output), "json"))
    return chat_progress_details(fields=fields, code=code)


def tool_step_details(step: RunStep) -> ChatProgressStepDetails | None:
    """Run の step のツールの段の詳細（`tool_details` と同じ）。"""
    return tool_details(step.tool_call, step.tool_result)


def _json_text(value: object) -> str:
    try:
        return json.dumps(value, ensure_ascii=False, indent=2, default=str)
    except (TypeError, ValueError):
        return str(value)


def approval_wait_display(
    round_: Sequence[ApprovalRequest], index: int, titles: dict[str, str]
) -> SpanDisplay:
    """承認の待ちの span の表示用のデータ（承認を待ったツールの名前と、何回目の承認か）。"""
    return SpanDisplay(
        kind=KIND_APPROVAL, params={"tools": _tool_names(round_, titles), "round": index + 1}
    )


def confirmation_wait_display(confirmation: ConfirmationRequest) -> SpanDisplay:
    """Agent 自身の回答前の確認の待ちの span の表示用のデータ（何回目の確認か）。"""
    return SpanDisplay(kind=KIND_CONFIRMATION, params={"round": max(1, confirmation.round)})


def recorded_wait_rounds(run: RunState, kind: str) -> set[int]:
    """記録済みの待ちの span の回（同じ待ちを再開のたびに重ねて記録しない）。"""
    rounds: set[int] = set()
    for event in run.progress_events:
        params = getattr(event, "params", None)
        if getattr(event, "kind", None) == kind and params:
            value = params.get("round")
            if isinstance(value, int):
                rounds.add(value)
    return rounds


def _tool_names(approvals: Sequence[ApprovalRequest], titles: dict[str, str]) -> str:
    """承認を待つツールの名前（表示名があれば表示名）。"""
    names: list[str] = []
    for approval in approvals:
        name = titles.get(approval.tool_call.name) or approval.tool_call.name
        if name not in names:
            names.append(name)
    return TOOL_NAME_SEPARATOR.join(names)


def tool_titles(run: RunState) -> dict[str, str]:
    """Run に渡したツールの表示名（function tool の名前 → MCP の `title`）。無ければ空。"""
    value = run.metadata.get(TOOL_TITLES_METADATA_KEY)
    if not isinstance(value, dict):
        return {}
    return {str(name): title for name, title in value.items() if isinstance(title, str) and title}


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
    "APPROVAL_WAIT_SPAN",
    "CONFIRMATION_WAIT_SPAN",
    "EXECUTE_TOOL_SPAN",
    "KIND_APPROVAL",
    "KIND_CONFIRMATION",
    "KIND_PLAN",
    "KIND_RESPOND",
    "KIND_TOOL",
    "RUN_STEP_PARAM",
    "TOOL_TITLES_METADATA_KEY",
    "approval_rounds",
    "approval_wait_display",
    "confirmation_wait_display",
    "model_turn_kind",
    "record_run_span",
    "record_run_terminal",
    "recorded_wait_rounds",
    "tool_attributes",
    "tool_details",
    "tool_display",
    "tool_params",
    "tool_step_details",
    "tool_titles",
]
