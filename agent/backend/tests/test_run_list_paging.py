"""実行履歴・承認の一覧のサーバー側のページングと要約（#1897）。

一覧は全 Run（step・Event・成果物）を返さず、行に要る項目の要約を `limit` / `offset` で返す。
承認の詳細は承認の ID からその Run だけを読む。repository は他のテストと共有するため、
業務 Agent の ID を一意にし、検索語でこのテストの Run だけに絞って確かめる。
"""

from __future__ import annotations

import contextlib
from collections.abc import Iterator
from dataclasses import dataclass
from typing import Any
from uuid import uuid4

import pytest
from starlette.testclient import TestClient

from app.features.agent.runtime import (
    AgentProfile,
    ApprovalDecisionRequest,
    RunCreateRequest,
    RunState,
    runtime_repository,
)
from app.features.agent.tools import ToolCall
from app.main import app

client = TestClient(app)
APPROVAL_TOOL = "nl2sql__nl2sql_query"


@dataclass
class PagingData:
    agent_id: str
    completed: list[RunState]
    waiting: RunState


def _run(agent_id: str, goal: str, *, approval: bool) -> RunState:
    run = runtime_repository.create_builtin_run(RunCreateRequest(goal=goal, agent_id=agent_id))
    if not approval:
        return run
    assert runtime_repository.begin_builtin_run(run.id) is not None
    return runtime_repository.request_builtin_approvals(
        run.id,
        [ToolCall(name=APPROVAL_TOOL, arguments={"question": goal}, trace_id="call-1")],
        state="{}",
    )


@pytest.fixture
def paging_data() -> Iterator[PagingData]:
    agent_id = f"agent-paging-{uuid4().hex[:12]}"
    runtime_repository.create_agent(AgentProfile(id=agent_id, name="ページング確認の業務"))
    try:
        completed = [_run(agent_id, f"一覧の確認 {index}", approval=False) for index in range(12)]
        waiting = _run(agent_id, "承認の確認 ＡＢＣ", approval=True)
        yield PagingData(agent_id=agent_id, completed=completed, waiting=waiting)
    finally:
        with contextlib.suppress(KeyError, ValueError):
            runtime_repository.delete_agent(agent_id)


def _runs(**params: Any) -> dict[str, Any]:
    response = client.get("/api/runs", params=params)
    assert response.status_code == 200, response.text
    data: dict[str, Any] = response.json()["data"]
    return data


def test_run_list_is_paged_summaries(paging_data: PagingData) -> None:
    first = _runs(q=paging_data.agent_id)
    assert (first["total"], first["limit"], first["offset"], first["has_next"]) == (13, 10, 0, True)
    items = first["items"]
    assert isinstance(items, list) and len(items) == 10
    # 新しい順。一覧の行は要約だけで、step・Event・成果物を持たない。
    assert items[0]["id"] == paging_data.waiting.id
    assert {"steps", "events", "artifacts", "approvals", "metadata"}.isdisjoint(items[0])
    assert items[0]["pending_approval_count"] == 1
    assert (items[0]["source"], items[0]["api_key_name"]) == ("user", None)
    assert items[0]["pending_approval"]["tool_name"] == APPROVAL_TOOL
    assert items[0]["pending_approval"]["id"] == paging_data.waiting.approvals[0].id
    assert isinstance(first["unfiltered_total"], int) and first["unfiltered_total"] >= 13

    second = _runs(q=paging_data.agent_id, offset=10)
    second_items = second["items"]
    assert isinstance(second_items, list) and len(second_items) == 3
    assert second["has_next"] is False
    listed = {item["id"] for item in [*items, *second_items]}
    assert listed == {run.id for run in [*paging_data.completed, paging_data.waiting]}


def test_run_list_filters_by_status_and_search(paging_data: PagingData) -> None:
    waiting = _runs(q=paging_data.agent_id, status="waiting_approval")
    assert [item["id"] for item in waiting["items"]] == [paging_data.waiting.id]
    # 「実行中・待機中」は queued / running / waiting_confirmation（作っただけの Run は queued）。
    active = _runs(q=paging_data.agent_id, status="active")
    assert active["total"] == 12
    # 検索は全角・半角と大文字・小文字を区別せず、空白区切りの語をすべて含む Run。
    # 業務 Agent の名前でも探せる。
    by_goal = _runs(q=f"{paging_data.agent_id} abc")
    assert [item["id"] for item in by_goal["items"]] == [paging_data.waiting.id]
    by_name = _runs(q="ページング確認の業務 承認")
    assert paging_data.waiting.id in {item["id"] for item in by_name["items"]}

    assert client.get("/api/runs", params={"status": "unknown"}).status_code == 422
    assert client.get("/api/runs", params={"limit": 101}).status_code == 422


def test_approval_list_and_detail(paging_data: PagingData) -> None:
    approval = paging_data.waiting.approvals[0]
    pending = client.get("/api/approvals", params={"status": "pending", "q": approval.id})
    assert pending.status_code == 200, pending.text
    data = pending.json()["data"]
    assert data["total"] == 1
    [row] = data["items"]
    assert row == {
        **row,
        "id": approval.id,
        "run_id": paging_data.waiting.id,
        "tool_name": APPROVAL_TOOL,
        "status": "pending",
        "run_goal": "承認の確認 ＡＢＣ",
        "run_status": "waiting_approval",
        "agent_id": paging_data.agent_id,
    }
    assert "tool_call" not in row

    runtime_repository.decide_approval(
        approval.id, ApprovalDecisionRequest(approved=False, decided_by="system_admin")
    )
    assert (
        client.get("/api/approvals", params={"status": "pending", "q": approval.id}).json()["data"][
            "total"
        ]
        == 0
    )
    decided = client.get("/api/approvals", params={"status": "decided", "q": approval.id})
    assert [item["status"] for item in decided.json()["data"]["items"]] == ["rejected"]

    detail = client.get(f"/api/approvals/{approval.id}")
    assert detail.status_code == 200, detail.text
    body = detail.json()["data"]
    assert body["approval"]["tool_call"]["arguments"] == {"question": "承認の確認 ＡＢＣ"}
    assert body["approval"]["decided_by"] == "system_admin"
    assert body["run"]["id"] == paging_data.waiting.id
    assert "steps" not in body["run"]
    assert body["user_display_names"].get("system_admin")

    missing = client.get("/api/approvals/approval-missing")
    assert missing.status_code == 404
    assert missing.json()["error_messages"] == ["承認の依頼が見つかりません。"]
    assert client.get("/api/approvals", params={"status": "all"}).status_code == 422
