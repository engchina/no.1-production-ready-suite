"""Cookie のセッションの利用者から作る ActorPolicy の絞り込みテスト（#215）。

- Run・監査・承認・成果物・SSE・`GET /agents`・
Binding 一覧が、利用者のエージェント・検索・回答プロファイルで

  絞られること（header の RBAC 情報は使わない）
- 承認の決定者（decided_by）が利用者になること（HTTP と WebSocket）
- WebSocket の Cookie・Origin の検証と、範囲外の Run の close 1008
"""

from __future__ import annotations

import contextlib
from collections.abc import Iterator
from dataclasses import dataclass

import pytest
from pytest import MonkeyPatch
from security_support import (
    SESSION_COOKIE,
    ProductionAuth,
    client,
    enable_production_auth,
    login,
    login_configured_admin,
)
from starlette.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from app.features.agent import builtin_runtime
from app.features.agent.runtime import (
    AgentProfile,
    RunCreateRequest,
    RunState,
    runtime_repository,
)
from app.features.agent.tools import ToolCall
from app.main import app
from app.security.service import set_security_service

AGENT_A = "agent-scope-a-215"
AGENT_B = "agent-scope-b-215"
APPROVAL_TOOL = "nl2sql__nl2sql_query"


@dataclass
class ScopeData:
    run_a1: RunState  # agent A / bv-a（承認待ち）
    # agent A / bv-b。検索・回答プロファイルは Agen
    # t の対象範囲ではない（RAG が判定する。#750）ため見える。
    #
    run_a2: RunState
    run_b: RunState  # agent B / bv-a
    run_a_no_view: RunState  # agent A / 検索・回答プロファイルなし


def _create_run(agent_id: str, search_answer_profile_id: str | None, *, approval: bool) -> RunState:
    metadata = (
        {"search_answer_profile_id": search_answer_profile_id} if search_answer_profile_id else {}
    )
    run = runtime_repository.create_builtin_run(
        RunCreateRequest(
            goal=f"scope {agent_id} {search_answer_profile_id}",
            agent_id=agent_id,
            metadata=metadata,
        )
    )
    if not approval:
        return run
    # 組み込み Runtime がツールの承認で中断したときと同じ状態にする（モデルは呼ばない）。
    assert runtime_repository.begin_builtin_run(run.id) is not None
    return runtime_repository.request_builtin_approvals(
        run.id,
        [ToolCall(name=APPROVAL_TOOL, arguments={"question": "範囲を確認する"}, trace_id="call-1")],
        state="{}",
    )


@pytest.fixture
def scope_data() -> Iterator[ScopeData]:
    for agent_id in (AGENT_A, AGENT_B):
        runtime_repository.create_agent(AgentProfile(id=agent_id, name=agent_id))
    try:
        yield ScopeData(
            run_a1=_create_run(AGENT_A, "bv-a", approval=True),
            run_a2=_create_run(AGENT_A, "bv-b", approval=True),
            run_b=_create_run(AGENT_B, "bv-a", approval=True),
            run_a_no_view=_create_run(AGENT_A, None, approval=False),
        )
    finally:
        for agent_id in (AGENT_A, AGENT_B):
            with contextlib.suppress(KeyError, ValueError):
                runtime_repository.delete_agent(agent_id)


@pytest.fixture
def auth(monkeypatch: MonkeyPatch) -> Iterator[ProductionAuth]:
    yield enable_production_auth(monkeypatch)
    set_security_service(None)


def _scoped_user(auth: ProductionAuth, login_user_id: str, permissions: list[str]) -> None:
    auth.user_with_permissions(login_user_id, permissions, agent_ids=[AGENT_A])


def _run_ids(headers: dict[str, str]) -> set[str]:
    response = client.get("/api/runs", headers=headers)
    assert response.status_code == 200, response.text
    return {item["id"] for item in response.json()["data"]["runs"]}


def test_run_list_and_detail_are_scoped_by_principal(
    auth: ProductionAuth, scope_data: ScopeData
) -> None:
    _scoped_user(auth, "scoped-viewer", ["agent.runs.view"])
    # header の自己申告（全ロール・全検索・回答プロファイル
    # ）は使わない（header の RBAC は無い。#750）。
    #
    headers = {
        **login("scoped-viewer"),
        "X-Agent-Roles": "admin",
        "X-Agent-Business-Views": "*",
    }
    ids = _run_ids(headers)
    assert scope_data.run_a1.id in ids
    assert scope_data.run_a_no_view.id in ids
    assert scope_data.run_a2.id in ids
    assert scope_data.run_b.id not in ids

    assert client.get(f"/api/runs/{scope_data.run_a1.id}", headers=headers).status_code == 200
    for run in (scope_data.run_b,):
        assert client.get(f"/api/runs/{run.id}", headers=headers).status_code == 403
        artifacts = client.get(f"/api/runs/{run.id}/artifacts", headers=headers)
        assert artifacts.status_code == 403
        events = client.get(f"/api/runs/{run.id}/events", headers=headers)
        assert events.status_code == 403
    allowed_events = client.get(f"/api/runs/{scope_data.run_a1.id}/events", headers=headers)
    assert allowed_events.status_code == 200
    assert allowed_events.headers["content-type"].startswith("text/event-stream")
    assert (
        client.get(f"/api/runs/{scope_data.run_a1.id}/artifacts", headers=headers).status_code
        == 200
    )
    # viewer は操作できない（header の admin も効かない）。
    cancel = client.post(f"/api/runs/{scope_data.run_a1.id}/cancel", headers=headers)
    assert cancel.status_code == 403


def test_unrestricted_principals_see_all_runs(auth: ProductionAuth, scope_data: ScopeData) -> None:
    auth.user_with_permissions("agent-admin", ["agent.admin"])
    expected = {
        scope_data.run_a1.id,
        scope_data.run_a2.id,
        scope_data.run_b.id,
        scope_data.run_a_no_view.id,
    }
    assert expected <= _run_ids(login("agent-admin"))
    assert expected <= _run_ids(login_configured_admin())


def test_audit_is_scoped_by_principal(auth: ProductionAuth, scope_data: ScopeData) -> None:
    _scoped_user(auth, "scoped-auditor", ["agent.audit.view"])
    headers = login("scoped-auditor")
    audit = client.get(f"/api/runs/{scope_data.run_a1.id}/audit", headers=headers)
    assert audit.status_code == 200
    assert client.get(f"/api/runs/{scope_data.run_b.id}/audit", headers=headers).status_code == 403
    records = client.get("/api/audit/tool-calls?limit=1000", headers=headers)
    assert records.status_code == 200
    run_ids = {item["run_id"] for item in records.json()["data"]["records"]}
    assert scope_data.run_a1.id in run_ids
    assert scope_data.run_b.id not in run_ids
    csv = client.get("/api/audit/tool-calls.csv?limit=5000", headers=headers)
    assert csv.status_code == 200
    assert scope_data.run_b.id not in csv.text


def test_audit_tool_names_are_scoped_by_principal(
    auth: ProductionAuth, scope_data: ScopeData
) -> None:
    """ツール名の選択肢にも、対象範囲の外の業務 Agent の Run のツール名を出さない（#983）。"""
    other_tool = "scope_b_only__lookup"
    run = runtime_repository.create_builtin_run(RunCreateRequest(goal="scope b", agent_id=AGENT_B))
    assert runtime_repository.begin_builtin_run(run.id) is not None
    runtime_repository.request_builtin_approvals(
        run.id, [ToolCall(name=other_tool, arguments={}, trace_id="call-b")], state="{}"
    )
    _scoped_user(auth, "scoped-auditor-names", ["agent.audit.view"])

    records = client.get("/api/audit/tool-calls", headers=login("scoped-auditor-names"))

    assert records.status_code == 200
    tool_names = records.json()["data"]["tool_names"]
    assert APPROVAL_TOOL in tool_names
    assert other_tool not in tool_names
    admin = client.get("/api/audit/tool-calls", headers=login_configured_admin())
    assert other_tool in admin.json()["data"]["tool_names"]
    del scope_data


def test_approval_is_scoped_and_decided_by_principal(
    auth: ProductionAuth, scope_data: ScopeData
) -> None:
    _scoped_user(auth, "scoped-approver", ["agent.approvals.decide"])
    headers = login("scoped-approver")
    out_of_scope = scope_data.run_b.approvals[0].id
    denied = client.post(
        f"/api/approvals/{out_of_scope}/decision",
        json={"approved": False, "decided_by": "spoofed"},
        headers=headers,
    )
    assert denied.status_code == 403
    assert denied.json()["error_messages"] == ["この業務 Agent を利用する権限がありません。"]
    missing = client.post(
        "/api/approvals/approval-missing/decision", json={"approved": True}, headers=headers
    )
    assert missing.status_code == 404
    assert missing.json()["error_messages"] == ["承認の依頼が見つかりません。"]
    approval_id = scope_data.run_a1.approvals[0].id
    decided = client.post(
        f"/api/approvals/{approval_id}/decision",
        json={"approved": False, "decided_by": "spoofed-user", "comment": "範囲内"},
        headers=headers,
    )
    assert decided.status_code == 200, decided.text
    approval = next(
        item for item in decided.json()["data"]["approvals"] if item["id"] == approval_id
    )
    # body の decided_by ではなく、ログイン中の利用者が決定者になる。
    assert approval["decided_by"] == "scoped-approver"


def test_operator_run_creation_is_scoped(auth: ProductionAuth, scope_data: ScopeData) -> None:
    _scoped_user(auth, "scoped-operator", ["agent.runs.operate"])
    headers = login("scoped-operator")
    denied_agent = client.post(
        "/api/runs",
        json={
            "goal": "範囲外",
            "agent_id": AGENT_B,
            "metadata": {"search_answer_profile_id": "bv-a"},
        },
        headers=headers,
    )
    assert denied_agent.status_code == 403
    # チャットの送信の失敗にそのまま出るので、英語・内部の ID を出さない。
    assert denied_agent.json()["error_messages"] == ["この業務 Agent を利用する権限がありません。"]
    created = client.post(
        "/api/runs",
        json={
            "goal": "範囲内",
            "agent_id": AGENT_A,
            "metadata": {"search_answer_profile_id": "bv-a"},
        },
        headers=headers,
    )
    assert created.status_code == 200, created.text


@pytest.mark.parametrize(
    "metadata",
    [
        # 承認待ちから再開する SDK の状態。偽ると、最初からの実行ではなく再開になる（#1130）。
        {"_builtin_sdk_state": "{}"},
        {"_runtime_dispatch_lease": {"owner": "x"}},
        {"evaluation_dry_run": True},
        {"automation_id": "automation-of-another-user"},
        {"source": "mcp", "search_answer_profile_id": "bv-a"},
    ],
    ids=["sdk-state", "dispatch-lease", "evaluation-dry-run", "automation-id", "source"],
)
def test_run_creation_rejects_reserved_metadata(
    auth: ProductionAuth,
    scope_data: ScopeData,
    monkeypatch: MonkeyPatch,
    metadata: dict[str, object],
) -> None:
    """利用者は Control Plane の予約の metadata を付けて Run を作れない（#1130）。"""
    scheduled: list[str] = []

    async def record(run_id: str) -> None:
        scheduled.append(run_id)

    monkeypatch.setattr(builtin_runtime, "resume_run", record)
    monkeypatch.setattr(builtin_runtime, "execute_run", record)
    _scoped_user(auth, "reserved-metadata-operator", ["agent.runs.operate"])
    headers = login("reserved-metadata-operator")
    before = {run.id for run in runtime_repository.list_runs()}

    response = client.post(
        "/api/runs",
        json={"goal": "予約の metadata", "agent_id": AGENT_A, "metadata": metadata},
        headers=headers,
    )

    assert response.status_code == 422, response.text
    assert "metadata" in response.json()["error_messages"][0]
    assert {run.id for run in runtime_repository.list_runs()} == before
    assert scheduled == []


def test_run_creation_keeps_unreserved_metadata(
    auth: ProductionAuth, scope_data: ScopeData, monkeypatch: MonkeyPatch
) -> None:
    async def record(run_id: str) -> None:
        return None

    monkeypatch.setattr(builtin_runtime, "execute_run", record)
    _scoped_user(auth, "unreserved-metadata-operator", ["agent.runs.operate"])
    headers = login("unreserved-metadata-operator")

    response = client.post(
        "/api/runs",
        json={
            "goal": "予約していない metadata",
            "agent_id": AGENT_A,
            "metadata": {"search_answer_profile_id": "bv-a", "ticket": "T-1"},
        },
        headers=headers,
    )

    assert response.status_code == 200, response.text
    metadata = response.json()["data"]["metadata"]
    assert metadata["search_answer_profile_id"] == "bv-a"
    assert metadata["ticket"] == "T-1"


def test_agent_list_is_scoped(auth: ProductionAuth, scope_data: ScopeData) -> None:
    _scoped_user(auth, "scoped-agents", ["agent.runs.operate", "menu.agents"])
    headers = login("scoped-agents")
    agents = client.get("/api/agents", headers=headers)
    assert agents.status_code == 200
    assert [item["id"] for item in agents.json()["data"]["agents"]] == [AGENT_A]

    admin = login_configured_admin()
    all_agents = {
        item["id"] for item in client.get("/api/agents", headers=admin).json()["data"]["agents"]
    }
    assert {AGENT_A, AGENT_B, "default"} <= all_agents


# ---------------------------------------------------------------------------
# WebSocket
# ---------------------------------------------------------------------------


def _ws_path(run: RunState) -> str:
    return f"/api/runs/{run.id}/events/ws?heartbeat_interval_seconds=60"


def _session_cookie(headers: dict[str, str]) -> str:
    return headers["cookie"].split(";")[0]


def test_websocket_requires_cookie_in_production(
    auth: ProductionAuth, scope_data: ScopeData
) -> None:
    with TestClient(app) as test_client:
        with (
            pytest.raises(WebSocketDisconnect) as closed,
            test_client.websocket_connect(
                _ws_path(scope_data.run_a1),
                headers={"origin": "http://testserver", "X-Agent-Roles": "admin"},
            ) as websocket,
        ):
            websocket.receive_json()
        assert closed.value.code == 1008


def test_websocket_rejects_cross_origin_and_invalid_session(
    auth: ProductionAuth, scope_data: ScopeData
) -> None:
    _scoped_user(auth, "ws-viewer", ["agent.runs.view"])
    cookie = _session_cookie(login("ws-viewer"))
    with TestClient(app) as test_client:
        for headers in (
            {"cookie": cookie},  # Origin なし
            {"cookie": cookie, "origin": "https://evil.example.com"},
            {"cookie": cookie, "origin": "http://testserver:8080"},
            {"cookie": f"{SESSION_COOKIE}=tampered", "origin": "http://testserver"},
        ):
            with (
                pytest.raises(WebSocketDisconnect) as closed,
                test_client.websocket_connect(
                    _ws_path(scope_data.run_a1), headers=headers
                ) as websocket,
            ):
                websocket.receive_json()
            assert closed.value.code == 1008, headers


def test_websocket_streams_in_scope_run_and_closes_out_of_scope(
    auth: ProductionAuth, scope_data: ScopeData
) -> None:
    _scoped_user(auth, "ws-scoped", ["agent.runs.view"])
    headers = {"cookie": _session_cookie(login("ws-scoped")), "origin": "http://testserver"}
    with TestClient(app) as test_client:
        with test_client.websocket_connect(
            _ws_path(scope_data.run_a1), headers=headers
        ) as websocket:
            first = websocket.receive_json()
            assert first["type"] != "error"
            assert first["event"]["run_id"] == scope_data.run_a1.id

        for run, error_code in ((scope_data.run_b, "rbac.agent_forbidden"),):
            with test_client.websocket_connect(_ws_path(run), headers=headers) as websocket:
                message = websocket.receive_json()
                assert message["type"] == "error"
                assert message["error_code"] == error_code
                with pytest.raises(WebSocketDisconnect) as closed:
                    websocket.receive_json()
                assert closed.value.code == 1008


def test_websocket_requires_run_capability(auth: ProductionAuth, scope_data: ScopeData) -> None:
    """メニューだけの利用者は接続後に rbac.forbidden で close 1008。"""
    auth.user_with_permissions("ws-menu-only", ["menu.runs"], agent_ids=[AGENT_A])
    headers = {"cookie": _session_cookie(login("ws-menu-only")), "origin": "http://testserver"}
    with (
        TestClient(app) as test_client,
        test_client.websocket_connect(_ws_path(scope_data.run_a1), headers=headers) as websocket,
    ):
        message = websocket.receive_json()
        assert message["error_code"] == "rbac.forbidden"
        with pytest.raises(WebSocketDisconnect) as closed:
            websocket.receive_json()
        assert closed.value.code == 1008


def test_websocket_commands_use_principal(auth: ProductionAuth, scope_data: ScopeData) -> None:
    """WebSocket のコマンドも同じ利用者で判定し、承認の決定者は利用者にする。"""
    _scoped_user(auth, "ws-approver", ["agent.approvals.decide"])
    headers = {"cookie": _session_cookie(login("ws-approver")), "origin": "http://testserver"}
    approval_id = scope_data.run_a1.approvals[0].id
    with (
        TestClient(app) as test_client,
        test_client.websocket_connect(_ws_path(scope_data.run_a1), headers=headers) as websocket,
    ):
        websocket.send_json({"type": "cancel", "command_id": "ws-cancel-215"})
        websocket.send_json(
            {
                "type": "approval_decision",
                "command_id": "ws-approval-215",
                "approval_id": approval_id,
                "approved": False,
                "decided_by": "spoofed-user",
            }
        )
        replies: dict[str, dict[str, object]] = {}
        while len(replies) < 2:
            message = websocket.receive_json()
            if message.get("command") in {"cancel", "approval_decision"}:
                replies[str(message["command"])] = message
    # approver は cancel（operator）を使えない。
    assert replies["cancel"]["error_code"] == "rbac.forbidden"
    assert replies["approval_decision"]["type"] == "command.accepted"
    run = runtime_repository.get_run(scope_data.run_a1.id)
    approval = next(item for item in run.approvals if item.id == approval_id)
    assert approval.decided_by == "ws-approver"


def test_trace_events_are_scoped_for_restricted_principal(
    auth: ProductionAuth, scope_data: ScopeData
) -> None:
    from app.observability import record_runtime_event

    for run in (scope_data.run_a1, scope_data.run_b):
        record_runtime_event("run.scope_check", {"run_id": run.id, "scope": "215"})
    _scoped_user(auth, "trace-auditor", ["agent.audit.view"])
    headers = login("trace-auditor")
    response = client.get("/api/observability/events?limit=1000", headers=headers)
    assert response.status_code == 200
    run_ids = {item["run_id"] for item in response.json()["data"]["events"]}
    assert scope_data.run_b.id not in run_ids
    assert None not in run_ids
    admin = login_configured_admin()
    all_ids = {
        item["run_id"]
        for item in client.get("/api/observability/events?limit=1000", headers=admin).json()[
            "data"
        ]["events"]
    }
    assert scope_data.run_b.id in all_ids
