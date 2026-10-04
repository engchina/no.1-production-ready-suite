"""実行の WebSocket のコマンド・接続の拒否の文（#1031）。

画面は `error_code` で権限の拒否を判定し、それ以外は `message` を Toast の説明に出すため、
`message` は利用者に見せる日本語の文にする（`error_code` は変えない）。
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any, cast

import anyio
from pr_system_settings.auth.domain import LOCAL_DEBUG_USER_UUID
from starlette.websockets import WebSocket

from app.features.agent import runtime as runtime_module
from app.features.agent.router import stream_run_events_websocket
from app.features.agent.runtime import RunCreateRequest, RunState
from app.features.agent.tools import ToolCall

RELOAD = "画面を再読み込みしてから、もう一度操作してください。"


class _CommandWebSocket:
    """`stream_run_events_websocket` が使う WebSocket の最小の代役。"""

    def __init__(self, messages: list[Any]) -> None:
        self.headers: dict[str, str] = {}
        self.state = SimpleNamespace()
        self.sent_json: list[dict[str, Any]] = []
        self.close_code: int | None = None
        self._messages = list(messages)

    async def accept(self) -> None:
        return None

    async def send_json(self, data: Any) -> None:
        assert isinstance(data, dict)
        self.sent_json.append(data)

    async def close(self, code: int = 1000) -> None:
        self.close_code = code

    async def receive_json(self) -> Any:
        if self._messages:
            return self._messages.pop(0)
        await anyio.sleep(1)
        return {}


def _waiting_run(goal: str) -> RunState:
    """ツールの承認で止まった Run（モデルは呼ばない）。"""
    repository = runtime_module.runtime_repository
    run = repository.create_builtin_run(
        RunCreateRequest(goal=goal, agent_id="default"),
        created_by_user_uuid=LOCAL_DEBUG_USER_UUID,
    )
    assert repository.begin_builtin_run(run.id) is not None
    call = ToolCall(
        name="nl2sql__nl2sql_query", arguments={"question": goal}, trace_id=f"call-{run.id}"
    )
    waiting: RunState = repository.request_builtin_approvals(run.id, [call], state="{}")
    assert waiting.approvals
    return waiting


def _run_websocket(websocket: _CommandWebSocket, run_id: str) -> None:
    async def run() -> None:
        await stream_run_events_websocket(
            cast(WebSocket, websocket), run_id, heartbeat_interval_seconds=999
        )

    anyio.run(run)


def _errors(websocket: _CommandWebSocket) -> dict[str | None, dict[str, Any]]:
    return {
        message.get("command_id"): message
        for message in websocket.sent_json
        if message.get("type") == "error"
    }


def test_websocket_command_errors_are_japanese() -> None:
    run = _waiting_run("WebSocket の拒否の文を確かめる")
    other = _waiting_run("別の実行の承認")
    websocket = _CommandWebSocket(
        [
            [1],
            {"type": "dance", "command_id": "c-unknown"},
            {"type": "approval_decision", "command_id": "c-no-approval"},
            {"type": "approval_decision", "approval_id": "x", "command_id": "c-no-approved"},
            {
                "type": "approval_decision",
                "approval_id": "missing-approval",
                "approved": True,
                "command_id": "c-missing",
            },
            {
                "type": "approval_decision",
                "approval_id": other.approvals[0].id,
                "approved": True,
                "command_id": "c-mismatch",
            },
            {"type": "resume", "command_id": "c-dup"},
            {"type": "cancel", "command_id": "c-dup"},
            {"type": "cancel", "command_id": "c-cancel"},
        ]
    )

    _run_websocket(websocket, run.id)

    errors = _errors(websocket)
    expected = {
        None: (
            "websocket.invalid_message",
            "操作の内容を読み取れませんでした。" + RELOAD,
        ),
        "c-unknown": ("websocket.unknown_command", "この操作（dance）には対応していません。"),
        "c-no-approval": (
            "websocket.invalid_command",
            "承認の依頼が指定されていません。" + RELOAD,
        ),
        "c-no-approved": (
            "websocket.invalid_command",
            "承認か却下かが指定されていません。" + RELOAD,
        ),
        "c-missing": ("approval.not_found", "承認の依頼が見つかりません。"),
        "c-mismatch": (
            "approval.run_mismatch",
            "この承認の依頼は表示中の実行のものではありません。" + RELOAD,
        ),
        "c-dup": (
            "websocket.command_id_conflict",
            "同じ操作 ID で別の操作が送られています。" + RELOAD,
        ),
    }
    assert {key: (value["error_code"], value["message"]) for key, value in errors.items()} == (
        expected
    )
    # 拒否しても接続は保ち、最後の取消は受け付ける。
    accepted = [
        message["command_id"]
        for message in websocket.sent_json
        if message.get("type") == "command.accepted"
    ]
    assert accepted == ["c-dup", "c-cancel"]
    assert websocket.close_code == 1000


def test_websocket_connection_rejection_is_japanese() -> None:
    websocket = _CommandWebSocket([])

    _run_websocket(websocket, "missing-run-1031")

    assert websocket.sent_json == [
        {"type": "error", "error_code": "run.not_found", "message": "実行が見つかりません。"}
    ]
    assert websocket.close_code == 1008
