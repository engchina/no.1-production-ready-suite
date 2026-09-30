"""画面の必須・数値の検証と backend の検証をそろえる（#540 / #541）。

画面の検証は先回りで、正本は backend。API を直接呼んでも、画面と同じ規則・日本語の文言で拒否する。
どのテストも拒否だけを確かめ、設定を書き換えない（並列でも直列でも通る）。
"""

from __future__ import annotations

import httpx
import pytest

from app.features.agent.runtime import RunCreateRequest
from app.main import app


async def _request(method: str, path: str, payload: dict[str, object]) -> httpx.Response:
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        return await client.request(method, path, json=payload)


@pytest.mark.parametrize("goal", ["", "   "], ids=["empty", "blank"])
def test_run_goal_is_required(goal: str) -> None:
    with pytest.raises(ValueError, match="ゴールを入力してください。"):
        RunCreateRequest(goal=goal)
    assert RunCreateRequest(goal=" 要点を整理する ").goal == " 要点を整理する "


@pytest.mark.asyncio
async def test_run_with_empty_goal_is_rejected_with_field_message() -> None:
    response = await _request("POST", "/api/runs", {"goal": " "})
    assert response.status_code == 422
    assert response.json()["error_messages"] == [
        "body.goal: Value error, ゴールを入力してください。"
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("method", "path", "payload"),
    [
        ("POST", "/api/settings/external-mcp-servers", {"server_id": "t0", "timeout_seconds": 0}),
        ("PATCH", "/api/settings/external-mcp-servers/default", {"timeout_seconds": -1}),
        ("PATCH", "/api/settings/external-mcp", {"timeout_seconds": 601}),
        ("PATCH", "/api/settings/external-nl2sql", {"timeout_seconds": 0}),
    ],
    ids=["mcp-create-zero", "mcp-patch-negative", "legacy-mcp-over", "nl2sql-zero"],
)
async def test_mcp_timeout_is_never_saved_as_zero(
    method: str, path: str, payload: dict[str, object]
) -> None:
    response = await _request(method, path, payload)
    assert response.status_code == 422
    (message,) = response.json()["error_messages"]
    assert message.endswith("タイムアウト秒は 0 より大きく 600 以下の数値を入力してください。")


@pytest.mark.asyncio
async def test_nl2sql_default_limit_message_matches_screen() -> None:
    response = await _request("PATCH", "/api/settings/external-nl2sql", {"default_limit": 0})
    assert response.status_code == 422
    (message,) = response.json()["error_messages"]
    assert message.endswith("既定取得件数は 1 以上 1000 以下の整数を入力してください。")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("payload", "message"),
    [
        (
            {"max_tool_calls_per_run": -1},
            "Run あたり最大ツール呼び出しは 0 以上の整数を入力してください。",
        ),
        (
            {"max_pending_approvals_per_run": -1},
            "Run あたり最大承認待ちは 0 以上の整数を入力してください。",
        ),
    ],
    ids=["tool-calls", "pending-approvals"],
)
async def test_runtime_safety_messages_match_screen(
    payload: dict[str, object], message: str
) -> None:
    response = await _request("PATCH", "/api/settings/runtime-safety", payload)
    assert response.status_code == 400
    assert response.json()["error_messages"] == [message]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("payload", "message"),
    [
        ({"workspace_root": " "}, "Workspace root を入力してください。"),
        ({"artifact_storage_path": ""}, "Artifact storage path を入力してください。"),
        ({"output_limit_bytes": 0}, "出力上限 bytes は 1 以上の整数を入力してください。"),
        (
            {"default_timeout_seconds": 0},
            "既定タイムアウト秒は 0 より大きい数値を入力してください。",
        ),
        ({"max_timeout_seconds": -1}, "最大タイムアウト秒は 0 より大きい数値を入力してください。"),
        (
            {"default_timeout_seconds": 10, "max_timeout_seconds": 5},
            "既定タイムアウト秒は最大タイムアウト秒以下の数値を入力してください。",
        ),
    ],
    ids=[
        "workspace-root",
        "artifact-path",
        "output-limit",
        "default-timeout",
        "max-timeout",
        "order",
    ],
)
async def test_command_policy_messages_match_screen(
    payload: dict[str, object], message: str
) -> None:
    response = await _request("PATCH", "/api/settings/command-policy", payload)
    assert response.status_code == 400
    assert response.json()["error_messages"] == [message]
