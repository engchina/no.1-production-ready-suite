"""Agent が RAG / NL2SQL の MCP を Run の利用者として呼ぶ（#233）。

契約どおりの fake MCP サーバー（`mcp_support`）に対して、MCP の手順（initialize と session id）、
サービストークン（`sub` / `aud` / claims）、エラーの変換、再試行、承認と利用者の関係を確かめる。
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any, Literal

import pytest
from mcp_support import (
    SERVICE_TOKEN_SECRET,
    FakeProductMcp,
    McpToolError,
    fake_product_mcp,
)
from pr_system_settings.auth.domain import CONFIGURED_SYSTEM_ADMIN_USER_UUID
from pr_system_settings.auth.service_token import verify_service_token
from pytest import MonkeyPatch
from security_support import (
    ProductionAuth,
    client,
    enable_production_auth,
    enable_signed_identity,
    login,
    login_configured_admin,
    signed_identity_headers,
)

import app.features.agent.tools as tools_module
from app.features.agent.config import ExternalRagRuntimeConfig, runtime_config_store
from app.features.agent.control_plane import RuntimeBinding, runtime_binding_registry
from app.features.agent.tools import (
    ToolCall,
    ToolInvocationContext,
    ToolPolicy,
    ToolResult,
    tool_registry,
)
from app.security.permissions import APPROVALS_DECIDE, MENU_APPROVALS, MENU_RUNS, RUNS_OPERATE
from app.security.service import set_security_service
from app.settings import get_settings

USER_UUID = "11111111-2222-3333-4444-555555555555"
ALLOW_ALL = ToolPolicy(
    allow={
        "external_rag_search",
        "external_rag_chat",
        "external_rag_list_business_views",
        "external_nl2sql_query",
        "external_nl2sql_get_job",
    }
)


@pytest.fixture(autouse=True)
def _clear_service_user_cache(monkeypatch: MonkeyPatch) -> None:
    monkeypatch.setattr(tools_module, "_service_user_uuid_cache", {})


@pytest.fixture
def auth(monkeypatch: MonkeyPatch) -> Iterator[ProductionAuth]:
    yield enable_production_auth(monkeypatch)
    set_security_service(None)


def _invoke(
    name: str,
    arguments: dict[str, Any],
    *,
    context: ToolInvocationContext | None = None,
    policy: ToolPolicy | None = ALLOW_ALL,
) -> ToolResult:
    return tool_registry.invoke(
        ToolCall(name=name, arguments=arguments),
        policy=policy,
        context=context or ToolInvocationContext(user_uuid=USER_UUID),
    )


# ---------------------------------------------------------------------------
# MCP の手順と token
# ---------------------------------------------------------------------------


def test_initialize_session_id_and_headers(monkeypatch: MonkeyPatch) -> None:
    mcp = fake_product_mcp(monkeypatch)

    result = _invoke(
        "external_rag_search",
        {"query": "契約の更新条件", "trace_id": "planner-trace"},
        context=ToolInvocationContext(user_uuid=USER_UUID, trace_id="t-1"),
    )

    assert result.success is True, result.error
    assert [request["method"] for request in mcp.requests] == [
        "initialize",
        "notifications/initialized",
        "tools/call",
    ]
    initialize, initialized, call = mcp.requests
    assert initialize["body"]["params"]["protocolVersion"] == "2025-06-18"
    assert initialize["body"]["params"]["clientInfo"]["name"] == "production-ready-agent"
    assert "mcp-session-id" not in initialize["headers"]
    # サーバーが返した session id を以降の request に付ける。
    assert initialized["headers"]["mcp-session-id"] == "session-rag-1"
    assert call["headers"]["mcp-session-id"] == "session-rag-1"
    for request in mcp.requests:
        assert request["headers"]["accept"] == "application/json, text/event-stream"
        assert request["headers"]["mcp-protocol-version"] == "2025-06-18"
    # 契約の項目だけを送る（fake は未知の項目を拒否する）。trace_id は JSON-RPC の id に使う。
    assert call["body"]["id"] == "t-1"
    assert call["body"]["params"] == {
        "name": "rag_search",
        "arguments": {"query": "契約の更新条件"},
    }
    assert result.output == {
        "answer": "根拠付き回答",
        "trace_id": "rag-trace-1",
        "guardrail_warnings": [],
        "citations": [
            {
                "document_id": "doc-1",
                "chunk_id": "chunk-1",
                "file_name": "契約書.pdf",
                "text": "契約条項",
                "score": 0.9,
            }
        ],
    }


def test_token_subject_audience_and_claims_follow_run_user(monkeypatch: MonkeyPatch) -> None:
    mcp = fake_product_mcp(monkeypatch)
    context = ToolInvocationContext(user_uuid=USER_UUID, run_id="run-233", agent_id="agent-233")

    rag = _invoke("external_rag_list_business_views", {"limit": 10}, context=context)
    nl2sql = _invoke("external_nl2sql_get_job", {"job_id": "job-1"}, context=context)

    assert rag.success is True, rag.error
    assert nl2sql.success is True, nl2sql.error
    rag_call, nl2sql_call = mcp.tool_calls
    # 製品ごとの audience の token（fake が verify_service_token で検証済み）。
    assert rag_call["product"] == "rag"
    assert nl2sql_call["product"] == "nl2sql"
    for call, audience in ((rag_call, "rag"), (nl2sql_call, "nl2sql")):
        claims = call["claims"]
        assert claims["sub"] == USER_UUID
        assert claims["aud"] == audience
        assert claims["iss"] == "agent"
        assert claims["run_id"] == "run-233"
        assert claims["agent_id"] == "agent-233"
        assert claims["exp"] - claims["iat"] == 60
    # 別の製品の audience では通らない。
    token = mcp.requests[-1]["headers"]["authorization"].removeprefix("Bearer ")
    with pytest.raises(Exception, match="無効"):
        verify_service_token(SERVICE_TOKEN_SECRET, token, audience="rag")


def test_service_user_is_used_without_run_user(
    monkeypatch: MonkeyPatch, auth: ProductionAuth
) -> None:
    service_user = auth.create_user("svc-agent")
    monkeypatch.setattr(get_settings(), "agent_mcp_service_user_login_id", "SVC-Agent")
    mcp = fake_product_mcp(monkeypatch)

    first = _invoke("external_rag_search", {"query": "a"}, context=ToolInvocationContext())
    second = _invoke("external_rag_search", {"query": "b"}, context=ToolInvocationContext())

    assert first.success is True, first.error
    assert second.success is True, second.error
    assert [call["claims"]["sub"] for call in mcp.tool_calls] == [service_user.user_uuid] * 2
    assert "run_id" not in mcp.tool_calls[0]["claims"]


def test_missing_user_and_service_user_fail_before_calling(monkeypatch: MonkeyPatch) -> None:
    mcp = fake_product_mcp(monkeypatch)
    monkeypatch.setattr(get_settings(), "agent_mcp_service_user_login_id", "")

    result = _invoke("external_rag_search", {"query": "a"}, context=ToolInvocationContext())

    assert result.success is False
    assert result.error_code == "external_rag.user_required"
    assert "AGENT_MCP_SERVICE_USER_LOGIN_ID" in (result.error or "")
    assert mcp.requests == []


def test_unknown_service_user_fails(monkeypatch: MonkeyPatch, auth: ProductionAuth) -> None:
    mcp = fake_product_mcp(monkeypatch)
    monkeypatch.setattr(get_settings(), "agent_mcp_service_user_login_id", "missing-user")

    result = _invoke("external_nl2sql_get_job", {"job_id": "j"}, context=ToolInvocationContext())

    assert result.error_code == "external_nl2sql.service_user_not_found"
    assert mcp.requests == []


def test_missing_service_token_secret_fails(monkeypatch: MonkeyPatch) -> None:
    mcp = fake_product_mcp(monkeypatch, secret="too-short")

    result = _invoke("external_rag_search", {"query": "a"})

    assert result.error_code == "external_rag.service_token_not_configured"
    assert "PLATFORM_SERVICE_TOKEN_SECRET" in (result.error or "")
    assert mcp.requests == []


def test_not_configured_mcp_url_fails(monkeypatch: MonkeyPatch) -> None:
    fake_product_mcp(monkeypatch)
    monkeypatch.setattr(runtime_config_store, "_rag", ExternalRagRuntimeConfig(mcp_url=None))

    result = _invoke("external_rag_search", {"query": "a"})

    assert result.error_code == "external_rag.not_configured"


# ---------------------------------------------------------------------------
# エラーの変換と再試行
# ---------------------------------------------------------------------------


def test_is_error_is_converted_to_tool_error(monkeypatch: MonkeyPatch) -> None:
    fake_product_mcp(
        monkeypatch,
        outputs={
            "rag_search": McpToolError(
                "RAG_BUSINESS_VIEW_FORBIDDEN",
                "この業務ビューは利用できません。",
                details={"business_view_id": "bv-x"},
            )
        },
    )

    result = _invoke("external_rag_search", {"query": "a", "business_view_id": "bv-x"})

    assert result.success is False
    assert result.error_code == "external_rag.tool_error"
    assert result.error == "この業務ビューは利用できません。"
    assert result.error_details["error_code"] == "RAG_BUSINESS_VIEW_FORBIDDEN"
    assert result.error_details["details"] == {"business_view_id": "bv-x"}
    assert result.error_details["tool_name"] == "rag_search"


def test_invalid_arguments_and_response_are_normalized(monkeypatch: MonkeyPatch) -> None:
    mcp = fake_product_mcp(monkeypatch, outputs={"nl2sql_get_job": {"status": "unknown"}})

    invalid_request = _invoke("external_rag_search", {"top_k": 0})
    invalid_response = _invoke("external_nl2sql_get_job", {"job_id": "job-1"})
    chat_without_view = _invoke("external_rag_chat", {"content": "こんにちは"})

    assert invalid_request.error_code == "external_rag.invalid_request"
    assert invalid_request.error_details["errors"]
    assert invalid_response.error_code == "external_nl2sql.invalid_response"
    assert chat_without_view.error_code == "external_rag.invalid_request"
    assert [call["name"] for call in mcp.tool_calls] == ["nl2sql_get_job"]


@pytest.mark.parametrize("status", [502, 504, "timeout"])
def test_llm_tools_do_not_retry_gateway_errors(
    monkeypatch: MonkeyPatch, status: int | Literal["timeout"]
) -> None:
    mcp = fake_product_mcp(monkeypatch)
    mcp.tool_call_statuses.extend([status, status])

    rag = _invoke("external_rag_search", {"query": "a"})
    mcp.tool_call_statuses[:] = [status, status]
    nl2sql = _invoke("external_nl2sql_query", {"question": "売上"})

    expected = "timeout" if status == "timeout" else "http_error"
    assert rag.error_code == f"external_rag.{expected}"
    assert nl2sql.error_code == f"external_nl2sql.{expected}"
    # LLM を使う呼び出しは 1 回だけ（再試行で重複させない）。
    assert [call["name"] for call in mcp.tool_calls] == ["rag_search", "nl2sql_query"]


def test_llm_tools_retry_503_and_idempotent_tools_retry_502(monkeypatch: MonkeyPatch) -> None:
    mcp = fake_product_mcp(monkeypatch)
    mcp.tool_call_statuses.append(503)
    rag = _invoke("external_rag_search", {"query": "a"})
    mcp.tool_call_statuses.append(502)
    views = _invoke("external_rag_list_business_views", {})

    assert rag.success is True, rag.error
    assert views.success is True, views.error
    assert [call["name"] for call in mcp.tool_calls] == [
        "rag_search",
        "rag_search",
        "rag_list_business_views",
        "rag_list_business_views",
    ]


# ---------------------------------------------------------------------------
# ツールごとの入出力と権限
# ---------------------------------------------------------------------------


def test_tool_definitions_and_permission_levels() -> None:
    definitions = {item.name: item for item in tool_registry.definitions()}

    assert definitions["external_rag_search"].permission_level == "read"
    assert definitions["external_rag_list_business_views"].permission_level == "read"
    assert definitions["external_nl2sql_get_job"].permission_level == "read"
    assert definitions["external_rag_chat"].permission_level == "write"
    assert definitions["external_rag_chat"].side_effects is True
    assert definitions["external_nl2sql_query"].permission_level == "sensitive"
    assert set(definitions["external_nl2sql_query"].input_schema["properties"]) == {
        "question",
        "profile_id",
        "row_limit",
        "wait_seconds",
    }


def test_nl2sql_query_and_rag_chat_require_approval_by_default(monkeypatch: MonkeyPatch) -> None:
    mcp = fake_product_mcp(monkeypatch)

    query = _invoke("external_nl2sql_query", {"question": "売上"}, policy=None)
    chat = _invoke(
        "external_rag_chat", {"content": "質問", "business_view_id": "bv-sales"}, policy=None
    )

    assert query.approval_required is True
    assert chat.approval_required is True
    assert mcp.requests == []


def test_nl2sql_query_passes_row_limit_and_returns_pending_job(monkeypatch: MonkeyPatch) -> None:
    pending = {
        "job_id": "job-pending",
        "status": "running",
        "columns": [],
        "rows": [],
        "returned_count": 0,
        "has_more": False,
        "truncated": False,
    }
    mcp = fake_product_mcp(
        monkeypatch, nl2sql_default_limit=5000, outputs={"nl2sql_query": pending}
    )

    defaulted = _invoke("external_nl2sql_query", {"question": "売上", "profile_id": "p-1"})
    explicit = _invoke("external_nl2sql_query", {"question": "売上", "row_limit": 10})
    assert explicit.success is True, explicit.error
    job = _invoke("external_nl2sql_get_job", {"job_id": "job-pending", "wait_seconds": 30})

    assert defaulted.output is not None
    assert defaulted.output["status"] == "running"
    assert defaulted.output["job_id"] == "job-pending"
    assert job.success is True, job.error
    # 既定取得件数は契約の上限（1000）に丸める。明示した row_limit はそのまま渡す。
    assert [call["arguments"] for call in mcp.tool_calls] == [
        {"question": "売上", "profile_id": "p-1", "row_limit": 1000, "wait_seconds": 40},
        {"question": "売上", "row_limit": 10, "wait_seconds": 40},
        {"job_id": "job-pending", "wait_seconds": 30},
    ]


def test_rag_chat_sends_message(monkeypatch: MonkeyPatch) -> None:
    mcp = fake_product_mcp(monkeypatch)

    result = _invoke(
        "external_rag_chat",
        {"content": "更新条件は？", "business_view_id": "bv-sales", "title": "契約"},
    )

    assert result.success is True, result.error
    assert result.output is not None
    assert result.output["conversation_id"] == "conversation-1"
    assert mcp.tool_calls[0]["name"] == "rag_chat_send_message"
    assert mcp.tool_calls[0]["arguments"] == {
        "content": "更新条件は？",
        "business_view_id": "bv-sales",
        "title": "契約",
    }


# ---------------------------------------------------------------------------
# Run の利用者（production）
# ---------------------------------------------------------------------------


def _run_with_nl2sql(headers: dict[str, str]) -> dict[str, Any]:
    created = client.post(
        "/api/runs",
        json={
            "goal": "部門別の売上を確認する",
            "planner_mode": "off",
            "tool_calls": [{"name": "external_nl2sql_query", "arguments": {"question": "売上"}}],
        },
        headers={**headers, "X-Agent-API-Version": "1"},
    )
    assert created.status_code == 200, created.text
    data: dict[str, Any] = created.json()["data"]
    return data


def test_approved_tool_runs_as_run_creator_not_approver(
    monkeypatch: MonkeyPatch, auth: ProductionAuth
) -> None:
    mcp = fake_product_mcp(monkeypatch)
    creator = auth.user_with_permissions(
        "run-creator", [MENU_RUNS, RUNS_OPERATE], agent_ids=["default"]
    )
    approver = auth.user_with_permissions(
        "run-approver", [MENU_APPROVALS, APPROVALS_DECIDE], agent_ids=["default"]
    )

    run = _run_with_nl2sql(login("run-creator"))
    assert run["status"] == "waiting_approval"
    assert run["created_by_user_uuid"] == creator.user_uuid
    assert mcp.tool_calls == []

    decided = client.post(
        f"/api/approvals/{run['approvals'][0]['id']}/decision",
        json={"approved": True},
        headers=login("run-approver"),
    )

    assert decided.status_code == 200, decided.text
    assert decided.json()["data"]["status"] == "completed"
    [call] = mcp.calls_of("nl2sql_query")
    assert call["claims"]["sub"] == creator.user_uuid != approver.user_uuid
    assert call["claims"]["run_id"] == run["id"]
    assert call["claims"]["agent_id"] == "default"


def test_direct_tool_invoke_uses_logged_in_user(
    monkeypatch: MonkeyPatch, auth: ProductionAuth
) -> None:
    mcp = fake_product_mcp(monkeypatch)
    operator = auth.user_with_permissions("tool-operator", [MENU_RUNS, RUNS_OPERATE])

    response = client.post(
        "/api/tools/invoke",
        json={"name": "external_rag_search", "arguments": {"query": "契約"}},
        headers=login("tool-operator"),
    )

    assert response.status_code == 200, response.text
    assert response.json()["data"]["success"] is True
    assert mcp.calls_of("rag_search")[0]["claims"]["sub"] == operator.user_uuid


def test_run_created_by_external_rbac_has_no_user(monkeypatch: MonkeyPatch) -> None:
    enable_production_auth(monkeypatch, rbac_enabled=True)
    try:
        enable_signed_identity(monkeypatch)
        run = _run_with_nl2sql(signed_identity_headers(sub="svc-operator", roles=["operator"]))
    finally:
        set_security_service(None)

    assert run["created_by_user_uuid"] is None


# ---------------------------------------------------------------------------
# Binding 経由の MCP と権限管理の業務ビューの候補
# ---------------------------------------------------------------------------


def test_binding_mcp_calls_rag_as_service_user(
    monkeypatch: MonkeyPatch, auth: ProductionAuth
) -> None:
    service_user = auth.create_user("svc-binding")
    monkeypatch.setattr(get_settings(), "agent_mcp_service_user_login_id", "svc-binding")
    mcp = fake_product_mcp(monkeypatch)
    binding = RuntimeBinding(
        id="binding-mcp-233",
        agent_id="default",
        runtime_id="hermes-default",
        native_agent_ref="agent",
        enabled=True,
    )
    monkeypatch.setattr(runtime_binding_registry, "get", lambda binding_id: binding)
    monkeypatch.setenv("AGENT_BINDING_MCP_TOKEN_BINDING_MCP_233", "binding-token-233")

    response = client.post(
        "/api/mcp/binding-mcp-233",
        json={
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {"name": "external_rag_search", "arguments": {"query": "契約"}},
        },
        headers={"Authorization": "Bearer binding-token-233"},
    )

    assert response.status_code == 200, response.text
    assert response.json()["result"]["isError"] is False
    [call] = mcp.calls_of("rag_search")
    assert call["claims"]["sub"] == service_user.user_uuid
    assert call["claims"]["agent_id"] == "default"


def _access_targets(headers: dict[str, str]) -> dict[str, Any]:
    response = client.get("/api/security/access-targets", headers=headers)
    assert response.status_code == 200, response.text
    body: dict[str, Any] = response.json()
    return body


def test_access_targets_include_rag_business_views_as_viewer(
    monkeypatch: MonkeyPatch, auth: ProductionAuth
) -> None:
    mcp = fake_product_mcp(monkeypatch)

    body = _access_targets(login_configured_admin())

    views = {item["id"]: item["name"] for item in body["data"]["business_views"]}
    assert views["bv-sales"] == "営業の業務ビュー"
    assert body["warning_messages"] == []
    assert body["data"]["business_view_warnings"] == []
    [call] = mcp.calls_of("rag_list_business_views")
    assert call["arguments"] == {"limit": 200}
    # 画面を開いた管理者として RAG を呼ぶ。
    assert call["claims"]["sub"] == CONFIGURED_SYSTEM_ADMIN_USER_UUID


def test_access_targets_warn_when_rag_fails_or_is_not_configured(
    monkeypatch: MonkeyPatch, auth: ProductionAuth
) -> None:
    auth.create_role([], business_view_ids=["bv-assigned"])
    mcp: FakeProductMcp = fake_product_mcp(monkeypatch)
    mcp.tool_call_statuses.extend([401] * 4)
    headers = login_configured_admin()

    failed = _access_targets(headers)
    monkeypatch.setattr(runtime_config_store, "_rag", ExternalRagRuntimeConfig(mcp_url=None))
    unconfigured = _access_targets(headers)

    for body in (failed, unconfigured):
        view_ids = [item["id"] for item in body["data"]["business_views"]]
        assert "bv-assigned" in view_ids
        assert "bv-sales" not in view_ids
        assert len(body["warning_messages"]) == 1
        # 画面が候補を出したまま警告を表示できるよう、data にも同じ警告を入れる（#240）。
        assert body["data"]["business_view_warnings"] == body["warning_messages"]
    assert "RAG の業務ビューを取得できませんでした" in failed["warning_messages"][0]
    assert "設定されていない" in unconfigured["warning_messages"][0]
