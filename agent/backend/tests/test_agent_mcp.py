"""業務 Agent を外部の MCP クライアントに公開する（`POST /api/mcp`。#1874）。

業務 Agent のモデルは SDK の `ScriptedModel` に差し替え、OCI へは接続しない。API キーは共通の
`pr_system_settings.auth.api_keys`（InMemory の store）。
"""

from __future__ import annotations

import contextlib
import json
from collections.abc import Iterator
from typing import Any

import pytest
from agents.testing import ScriptedModel, assistant_message, function_call
from pr_system_settings.auth.api_keys import api_key_rate_limiter
from pytest import MonkeyPatch
from security_support import ProductionAuth, client, enable_production_auth, login

from app.features.agent import builtin_runtime, mcp_server
from app.features.agent.builtin_runtime import ModelTarget
from app.features.agent.confirmation import CONFIRMATION_TOOL
from app.features.agent.runtime import (
    AgentProfile,
    Artifact,
    RunCreateRequest,
    RunState,
    RunStatus,
    RunStep,
    StepStatus,
    runtime_repository,
)
from app.features.agent.tools import ToolCall, ToolResult
from app.security.permissions import RUNS_OPERATE, RUNS_VIEW
from app.security.service import set_security_service
from app.settings import get_settings

AGENT_ID = "mcp1874-agent"
OTHER_AGENT_ID = "mcp1874-other"
TOOLS = {"agent_list", "agent_chat", "agent_get_run", "agent_answer_confirmation"}
ASK_ARGUMENTS = {
    "question": "どの期間の経費を見ますか？",
    "reason": "期間が質問から決められません。",
    "options": [
        {"label": "今月", "description": ""},
        {"label": "今年度", "description": "4 月から"},
    ],
    "multiple": False,
}


@pytest.fixture
def agents(monkeypatch: MonkeyPatch) -> Iterator[None]:
    monkeypatch.setattr(mcp_server, "wait_poll_seconds", 0.01)
    settings = get_settings()
    # 台本のモデルはツールを呼ばずに答えるので、最終の検証と拒答のガードは外す。
    monkeypatch.setattr(settings, "agent_final_validation_enabled", False)
    monkeypatch.setattr(settings, "agent_tool_grounding_required", False)
    for agent_id, name in ((AGENT_ID, "経理の Agent"), (OTHER_AGENT_ID, "人事の Agent")):
        runtime_repository.create_agent(
            AgentProfile(
                id=agent_id, name=name, description=f"{name}の説明", instructions="答える。"
            )
        )
    api_key_rate_limiter.reset()
    try:
        yield
    finally:
        repository: Any = runtime_repository
        with repository._lock:  # noqa: SLF001 - テストの後始末
            for run_id in [
                run_id
                for run_id, run in repository._runs.items()
                if run.agent_id in {AGENT_ID, OTHER_AGENT_ID}
            ]:
                repository._runs.pop(run_id)
        for agent_id in (AGENT_ID, OTHER_AGENT_ID):
            with contextlib.suppress(KeyError, ValueError):
                runtime_repository.delete_agent(agent_id)


@pytest.fixture
def auth(monkeypatch: MonkeyPatch) -> Iterator[ProductionAuth]:
    yield enable_production_auth(monkeypatch)
    set_security_service(None)


def _script(monkeypatch: MonkeyPatch, *steps: Any) -> ScriptedModel:
    model = ScriptedModel(list(steps))
    monkeypatch.setattr(
        builtin_runtime,
        "resolve_model_target",
        lambda model_id="": ModelTarget(
            model_id=model_id or "m", endpoint="https://oci.example", project_ocid="", api_key="k"
        ),
    )
    monkeypatch.setattr(builtin_runtime, "model_factory", lambda _target: model)
    return model


def _rpc(
    method: str, params: dict[str, Any] | None = None, headers: dict[str, str] | None = None
) -> Any:
    return client.post(
        "/api/mcp",
        json={"jsonrpc": "2.0", "id": 1, "method": method, "params": params or {}},
        headers=headers or {},
    )


def _call(
    name: str, arguments: dict[str, Any], headers: dict[str, str] | None = None
) -> dict[str, Any]:
    response = _rpc("tools/call", {"name": name, "arguments": arguments}, headers)
    assert response.status_code == 200, response.text
    result: dict[str, Any] = response.json()["result"]
    return result


def _bearer(token: str) -> dict[str, str]:
    return {"authorization": f"Bearer {token}"}


def _issue_key(headers: dict[str, str], **body: Any) -> dict[str, Any]:
    payload = {"name": "Claude Code", "products": ["agent"], **body}
    response = client.post("/api/account/api-keys", json=payload, headers=headers)
    assert response.status_code == 200, response.text
    data: dict[str, Any] = response.json()["data"]
    return data


# ---- local（ログインなし）--------------------------------------------------------------


def test_tools_list_and_chat_answers_with_the_agent(monkeypatch: MonkeyPatch, agents: None) -> None:
    del agents
    _script(monkeypatch, [assistant_message("今月の経費は 50 万円です。")])

    initialized = _rpc("initialize", {"protocolVersion": "2025-06-18"}).json()["result"]
    assert initialized["serverInfo"]["name"] == "production-ready-agent"
    assert "agent_chat" in initialized["instructions"]
    tools = {tool["name"]: tool for tool in _rpc("tools/list").json()["result"]["tools"]}
    assert set(tools) == TOOLS
    assert tools["agent_list"]["annotations"]["readOnlyHint"] is True
    assert tools["agent_get_run"]["annotations"]["readOnlyHint"] is True
    assert tools["agent_chat"]["annotations"]["readOnlyHint"] is False
    assert tools["agent_answer_confirmation"]["annotations"]["readOnlyHint"] is False
    assert all("outputSchema" in tool for tool in tools.values())

    listed = _call("agent_list", {})["structuredContent"]["agents"]
    mine = next(item for item in listed if item["id"] == AGENT_ID)
    assert (mine["name"], mine["description"]) == ("経理の Agent", "経理の Agentの説明")

    answered = _call("agent_chat", {"agent_id": AGENT_ID, "question": "今月の経費は？"})
    output = answered["structuredContent"]
    assert answered["isError"] is False
    assert (output["status"], output["answer"]) == ("completed", "今月の経費は 50 万円です。")
    assert output["in_progress"] is False and output["confirmation"] is None
    assert output["thread_id"].startswith("thread_")
    run = runtime_repository.get_run(output["run_id"])
    # MCP の Run の印（承認の扱い・監査）。local はキーが無いのでキーの ID は付かない。
    assert run.metadata["source"] == "mcp"
    assert "api_key_id" not in run.metadata
    again = _call("agent_get_run", {"run_id": output["run_id"], "wait_seconds": 0})
    assert again["structuredContent"]["answer"] == "今月の経費は 50 万円です。"


def test_chat_returns_the_run_when_the_answer_is_not_ready(agents: None) -> None:
    del agents
    output = _call("agent_chat", {"agent_id": AGENT_ID, "question": "q", "wait_seconds": 0})
    structured = output["structuredContent"]
    assert structured["status"] in {"queued", "running"}
    assert structured["in_progress"] is True
    assert "agent_get_run" in structured["message"]

    missing = _call("agent_chat", {"agent_id": "no-such-agent", "question": "q"})
    assert missing["isError"] is True
    assert missing["structuredContent"]["error_code"] == "AGENT_NOT_FOUND"
    invalid = _call("agent_chat", {"agent_id": AGENT_ID, "question": "q", "wait_seconds": 999})
    assert invalid["structuredContent"]["error_code"] == "MCP_TOOL_ARGUMENTS_INVALID"
    unknown_run = _call("agent_get_run", {"run_id": "run_missing", "wait_seconds": 0})
    assert unknown_run["structuredContent"]["error_code"] == "RUN_NOT_FOUND"


def test_confirmation_is_returned_and_answered_through_mcp(
    monkeypatch: MonkeyPatch, agents: None
) -> None:
    """回答前の確認（#1839）を構造化して返し、agent_answer_confirmation の答えで続ける。"""
    del agents
    model = _script(
        monkeypatch,
        [function_call(CONFIRMATION_TOOL, ASK_ARGUMENTS, call_id="call-ask")],
        [assistant_message("今年度の経費は 1,200 万円です。")],
    )
    asked = _call("agent_chat", {"agent_id": AGENT_ID, "question": "経費を教えて"})
    output = asked["structuredContent"]
    assert output["status"] == "waiting_confirmation"
    confirmation = output["confirmation"]
    assert confirmation["source"] == "agent"
    [item] = confirmation["items"]
    assert item["prompt"] == "どの期間の経費を見ますか？"
    assert [option["label"] for option in item["options"]] == ["今月", "今年度"]
    assert "agent_answer_confirmation" in output["message"]

    wrong = _call(
        "agent_answer_confirmation",
        {
            "run_id": output["run_id"],
            "confirmation_id": confirmation["id"],
            "answers": [{"item_id": item["id"], "option_ids": ["option_x"]}],
        },
    )
    assert wrong["structuredContent"]["error_code"] == "CONFIRMATION_ANSWER_INVALID"

    answered = _call(
        "agent_answer_confirmation",
        {
            "run_id": output["run_id"],
            "confirmation_id": confirmation["id"],
            "answers": [{"item_id": item["id"], "option_ids": [item["options"][1]["id"]]}],
        },
    )["structuredContent"]
    assert (answered["run_id"], answered["status"]) == (output["run_id"], "completed")
    assert answered["answer"] == "今年度の経費は 1,200 万円です。"
    assert answered["confirmation"] is None
    assert "今年度" in json.dumps(model.calls[1].input, ensure_ascii=False, default=str)
    # 答え済みの確認には答えられない。
    closed = _call(
        "agent_answer_confirmation",
        {"run_id": output["run_id"], "confirmation_id": confirmation["id"], "action": "skip"},
    )
    assert closed["structuredContent"]["error_code"] == "CONFIRMATION_CLOSED"


def test_chat_continues_the_conversation_with_thread_id(
    monkeypatch: MonkeyPatch, agents: None
) -> None:
    del agents
    model = _script(
        monkeypatch,
        [assistant_message("今月の経費は 50 万円です。")],
        [assistant_message("最も多いのは交通費です。")],
    )
    first = _call("agent_chat", {"agent_id": AGENT_ID, "question": "今月の経費は？"})
    thread_id = first["structuredContent"]["thread_id"]
    second = _call(
        "agent_chat",
        {"agent_id": AGENT_ID, "question": "その中で一番多いのは？", "thread_id": thread_id},
    )["structuredContent"]
    assert (second["thread_id"], second["answer"]) == (thread_id, "最も多いのは交通費です。")
    last_input = json.dumps(model.calls[-1].input, ensure_ascii=False, default=str)
    assert "今月の経費は？" in last_input and "今月の経費は 50 万円です。" in last_input
    # 別の業務 Agent・存在しない会話は続けられない（存在を漏らさない）。
    for agent_id, other_thread in ((OTHER_AGENT_ID, thread_id), (AGENT_ID, f"thread_{'0' * 32}")):
        refused = _call(
            "agent_chat", {"agent_id": agent_id, "question": "続き", "thread_id": other_thread}
        )
        assert refused["structuredContent"]["error_code"] == "THREAD_NOT_FOUND"


def test_unpublished_agents_are_not_listed_or_asked(agents: None) -> None:
    del agents
    draft_id = "mcp1874-draft"
    created = client.post(
        "/api/agents", json={"id": draft_id, "name": "下書きの Agent", "instructions": "答える。"}
    )
    assert created.status_code == 200, created.text
    try:
        assert created.json()["data"]["published_version"] is None
        listed = _call("agent_list", {})["structuredContent"]["agents"]
        assert draft_id not in {item["id"] for item in listed}
        refused = _call("agent_chat", {"agent_id": draft_id, "question": "q"})
        assert refused["structuredContent"]["error_code"] == "AGENT_NOT_AVAILABLE"
    finally:
        with contextlib.suppress(KeyError, ValueError):
            runtime_repository.delete_agent(draft_id)


# ---- production（API キー）----------------------------------------------------------------


def test_api_key_acts_as_its_owner_and_is_limited_to_the_key_scope(
    monkeypatch: MonkeyPatch, auth: ProductionAuth, agents: None
) -> None:
    del agents
    _script(monkeypatch, [assistant_message("経費は 50 万円です。")])
    auth.user_with_permissions("mcp1874-op", [RUNS_OPERATE], agent_ids=[AGENT_ID, OTHER_AGENT_ID])
    session = login("mcp1874-op")
    scoped = _issue_key(session, name="経理の連携", resource_scopes={"agent": [AGENT_ID]})
    assert scoped["key"]["resource_scopes"] == {"agent": [AGENT_ID]}
    headers = _bearer(scoped["token"])

    # キーなし・Cookie なしは 401。キーは MCP の入口だけで使える（画面の API は 401）。
    assert _rpc("tools/list").status_code == 401
    assert client.get("/api/agents", headers=headers).status_code == 401
    # キーの範囲の業務 Agent だけが見え、範囲の外は 403。
    listed = _call("agent_list", {}, headers)["structuredContent"]["agents"]
    assert [item["id"] for item in listed] == [AGENT_ID]
    outside = _call("agent_chat", {"agent_id": OTHER_AGENT_ID, "question": "q"}, headers)
    assert outside["structuredContent"]["error_code"] == "AGENT_OUT_OF_KEY_SCOPE"
    assert outside["structuredContent"]["status"] == 403

    answered = _call("agent_chat", {"agent_id": AGENT_ID, "question": "経費は？"}, headers)
    output = answered["structuredContent"]
    assert output["answer"] == "経費は 50 万円です。"
    run = runtime_repository.get_run(output["run_id"])
    # キーを発行した利用者の Run。監査に「MCP（キーの名前）」を出すため、キーの ID と名前を残す。
    owner = auth.store.get_user_by_login_user_id("mcp1874-op")
    assert owner is not None and run.created_by_user_uuid == owner.user_uuid
    assert (run.metadata["source"], run.metadata["api_key_name"]) == ("mcp", "経理の連携")
    assert run.metadata["api_key_id"] == scoped["key"]["id"]

    # 範囲なしのキーは利用者が使えるすべての業務 Agent。
    unscoped = _issue_key(session, name="すべて")
    listed = _call("agent_list", {}, _bearer(unscoped["token"]))["structuredContent"]["agents"]
    assert {item["id"] for item in listed} >= {AGENT_ID, OTHER_AGENT_ID}
    # キーで範囲の外の業務 Agent の Run を読むのも 403。
    other_run = _call(
        "agent_chat",
        {"agent_id": OTHER_AGENT_ID, "question": "q", "wait_seconds": 0},
        _bearer(unscoped["token"]),
    )["structuredContent"]
    denied = _call("agent_get_run", {"run_id": other_run["run_id"], "wait_seconds": 0}, headers)
    assert denied["structuredContent"]["error_code"] == "AGENT_OUT_OF_KEY_SCOPE"


def test_api_key_needs_the_agent_product_and_the_owner_permissions(
    auth: ProductionAuth, agents: None
) -> None:
    del agents
    auth.user_with_permissions("mcp1874-viewer", [RUNS_VIEW], agent_ids=[AGENT_ID])
    session = login("mcp1874-viewer")
    rag_only = _issue_key(session, products=["rag"])
    refused = _rpc("tools/list", headers=_bearer(rag_only["token"]))
    assert refused.status_code == 403
    assert refused.json()["error_code"] == "SECURITY_API_KEY_PRODUCT_FORBIDDEN"

    viewer = _issue_key(session)
    tools = _rpc("tools/list", headers=_bearer(viewer["token"])).json()["result"]["tools"]
    # 閲覧だけの利用者には質問・答えのツールを出さず、呼んでも権限なし。
    assert {tool["name"] for tool in tools} == {"agent_list", "agent_get_run"}
    denied = _call("agent_chat", {"agent_id": AGENT_ID, "question": "q"}, _bearer(viewer["token"]))
    assert denied["structuredContent"]["error_code"] == "MCP_TOOL_FORBIDDEN"

    # ほかの利用者の Run は読めない（存在を漏らさない）。
    auth.user_with_permissions("mcp1874-op2", [RUNS_OPERATE], agent_ids=[AGENT_ID])
    operator = _issue_key(login("mcp1874-op2"))
    asked = _call(
        "agent_chat",
        {"agent_id": AGENT_ID, "question": "q", "wait_seconds": 0},
        _bearer(operator["token"]),
    )["structuredContent"]
    other = _call(
        "agent_get_run", {"run_id": asked["run_id"], "wait_seconds": 0}, _bearer(viewer["token"])
    )
    assert other["structuredContent"]["error_code"] == "RUN_NOT_FOUND"

    # 削除したキーは使えない。
    deleted = client.delete(f"/api/account/api-keys/{viewer['key']['id']}", headers=session)
    assert deleted.status_code == 200
    assert _rpc("tools/list", headers=_bearer(viewer["token"])).status_code == 401


def test_api_key_limits_runs_per_day_and_calls_per_minute(
    monkeypatch: MonkeyPatch, auth: ProductionAuth, agents: None
) -> None:
    del agents
    settings = get_settings()
    monkeypatch.setattr(settings, "app_api_key_runs_per_day", 1)
    auth.user_with_permissions("mcp1874-limit", [RUNS_OPERATE], agent_ids=[AGENT_ID])
    headers = _bearer(_issue_key(login("mcp1874-limit"))["token"])
    chat = {"agent_id": AGENT_ID, "question": "q", "wait_seconds": 0}
    assert _call("agent_chat", chat, headers)["isError"] is False
    limited = _call("agent_chat", chat, headers)["structuredContent"]
    assert (limited["error_code"], limited["status"]) == ("API_KEY_RUN_LIMIT", 429)
    assert limited["details"]["retry_after_seconds"] > 0
    assert limited["details"]["limit"] == 1

    # 1 分あたりの呼び出しの回数は共通の認証が数え、超えたら 429 と Retry-After。
    monkeypatch.setattr(settings, "app_api_key_calls_per_minute", 1)
    api_key_rate_limiter.reset()
    assert _rpc("tools/list", headers=headers).status_code == 200
    throttled = _rpc("tools/list", headers=headers)
    assert throttled.status_code == 429
    assert int(throttled.headers["retry-after"]) >= 1
    assert throttled.json()["error_code"] == "SECURITY_API_KEY_RATE_LIMITED"


def test_run_output_sources_use_the_rag_tool_results_checked_by_the_validation() -> None:
    """根拠は RAG のツールの結果の evidence のうち、最終の検証で確かめた chunk（無ければ全件）。"""
    evidence = [
        {
            "evidence_id": "e1",
            "chunk_id": "c1",
            "file_name": "経費精算規程.pdf",
            "excerpt": "翌月10日までに申請する。",
            "locator": {"page_start": 1, "section_path": ["第2条（精算の期限）"]},
        },
        {"evidence_id": "e2", "chunk_id": "c2", "file_name": "在宅勤務規程.pdf", "excerpt": "x"},
    ]
    run = RunState(
        id="run_sources",
        goal="期限は？",
        agent_id=AGENT_ID,
        status=RunStatus.COMPLETED,
        steps=[
            RunStep(
                run_id="run_sources",
                status=StepStatus.COMPLETED,
                tool_call=ToolCall(name="rag__rag_retrieve_evidence", arguments={}),
                tool_result=ToolResult(
                    name="rag__rag_retrieve_evidence", success=True, output={"evidence": evidence}
                ),
            )
        ],
        artifacts=[
            Artifact(name="回答", kind="answer", content={"text": "翌月10日までです。"}),
            Artifact(
                name="回答の検証",
                kind="answer_validation",
                content={"evidence": [{"chunk_id": "c1"}]},
            ),
        ],
    )
    output = mcp_server.run_output(run)
    assert [(item.title, item.location) for item in output.sources] == [
        ("経費精算規程.pdf", "p.1 · 第2条（精算の期限）")
    ]
    assert output.tools_used == ["rag__rag_retrieve_evidence"]
    unvalidated = run.model_copy(update={"artifacts": run.artifacts[:1]})
    assert len(mcp_server.run_output(unvalidated).sources) == 2


def test_audit_shows_mcp_runs_with_the_key_name(agents: None) -> None:
    """監査のツールの呼び出しに、Run のきっかけ「MCP（キーの名前）」を出す（#1874）。"""
    del agents
    run = runtime_repository.create_builtin_run(
        RunCreateRequest(goal="登録して", agent_id=AGENT_ID),
        extra_metadata={"source": "mcp", "api_key_id": "0123456789abcdef", "api_key_name": "経理"},
    )
    runtime_repository.record_builtin_unattended_skips(
        run.id, [ToolCall(name="nl2sql__nl2sql_cancel_requirement", arguments={})], mcp=True
    )
    audit = client.get(f"/api/audit/tool-calls?run_id={run.id}")
    assert audit.status_code == 200, audit.text
    [record] = audit.json()["data"]["items"]
    assert (record["run_source"], record["api_key_name"]) == ("mcp", "経理")
    assert record["error_code"] == "mcp.approval_required"
    exported = client.get(f"/api/audit/tool-calls.csv?run_id={run.id}").text
    assert "run_source,api_key_name" in exported
    assert ",mcp,経理" in exported
