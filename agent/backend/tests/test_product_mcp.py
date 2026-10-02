"""Agent が MCP 接続（RAG / NL2SQL / 外部 MCP）のツールを呼ぶ（#233 / #757）。

契約どおりの fake MCP サーバー（`mcp_support`）に対して、MCP の手順（initialize と session id）、
認証（サービストークンの `sub` / `aud` / claims・API キー）、エラーの変換、再試行、ツールの一覧
（readOnlyHint と承認）、画面からのツールの取得の利用者を確かめる。
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any, Literal

import httpx
import pytest
from mcp_support import (
    SERVICE_TOKEN_SECRET,
    McpToolError,
    fake_product_mcp,
)
from pr_system_settings.auth.service_token import verify_service_token
from pytest import MonkeyPatch
from security_support import (
    ProductionAuth,
    client,
    enable_production_auth,
    login,
)

import app.features.agent.tools as tools_module
from app.features.agent.config import runtime_config_store
from app.features.agent.skills import skill_registry
from app.features.agent.tools import (
    ExternalMcpToolInfo,
    ToolCall,
    ToolInvocationContext,
    ToolPolicy,
    ToolResult,
    list_mcp_connection_tools,
    mcp_function_name,
    mcp_tool_definition,
    mcp_tool_handler,
    tool_registry,
)
from app.security.permissions import MENU_SETTINGS_EXTERNAL_MCP
from app.security.service import set_security_service
from app.settings import get_settings

USER_UUID = "11111111-2222-3333-4444-555555555555"
CONTRACTS = Path(__file__).resolve().parents[3] / "platform/contracts/mcp"
# fake の RAG / NL2SQL のツールの readOnlyHint（`mcp_support.FakeProductMcp`）。
READ_ONLY = {
    "rag_search": True,
    "rag_list_business_views": True,
    "rag_chat_send_message": False,
    "nl2sql_query": False,
    "nl2sql_get_job": True,
}
ALLOW_ALL = ToolPolicy(
    allow={
        mcp_function_name("rag" if name.startswith("rag") else "nl2sql", name) for name in READ_ONLY
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
    server_id: str,
    name: str,
    arguments: dict[str, Any],
    *,
    context: ToolInvocationContext | None = None,
    policy: ToolPolicy | None = ALLOW_ALL,
) -> ToolResult:
    config = runtime_config_store.get_mcp(server_id)
    info = ExternalMcpToolInfo(
        name=name,
        server_id=server_id,
        read_only=READ_ONLY.get(name, False),
        function_name=mcp_function_name(server_id, name),
    )
    definition = mcp_tool_definition(config, info)
    return tool_registry.invoke(
        ToolCall(name=definition.name, arguments=arguments),
        policy=policy,
        context=context or ToolInvocationContext(user_uuid=USER_UUID),
        definition=definition,
        handler=mcp_tool_handler(config, info),
    )


# ---------------------------------------------------------------------------
# 接続の一覧（RAG / NL2SQL は既定の接続）と Skill
# ---------------------------------------------------------------------------


def test_rag_and_nl2sql_are_builtin_service_token_connections() -> None:
    connections = {config.server_id: config for config in runtime_config_store.list_mcp_servers()}

    for server_id in ("rag", "nl2sql"):
        config = connections[server_id]
        assert config.source == "builtin"
        assert config.effective_auth_mode() == "service_token"
        assert config.audience() == server_id
    with pytest.raises(ValueError):
        runtime_config_store.remove_mcp_server("rag")


def test_builtin_skills_use_tools_in_product_contracts() -> None:
    """組み込み Skill が使う RAG / NL2SQL のツールが、製品の MCP の契約にある（#248）。"""
    contract_tools = {
        product: {
            tool["name"]
            for tool in json.loads((CONTRACTS / f"{product}-tools.json").read_text())["tools"]
        }
        for product in ("rag", "nl2sql")
    }
    used = 0
    for skill in skill_registry.export("builtin"):
        for requirement in skill.mcp_requirements:
            if requirement.server_id not in contract_tools:
                continue
            assert set(requirement.tool_names) <= contract_tools[requirement.server_id], skill.id
            used += 1
    assert used >= 3


# ---------------------------------------------------------------------------
# MCP の手順と token
# ---------------------------------------------------------------------------


def test_initialize_session_id_and_headers(monkeypatch: MonkeyPatch) -> None:
    mcp = fake_product_mcp(monkeypatch)

    result = _invoke(
        "rag",
        "rag_search",
        {"query": "契約の更新条件"},
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
    # 標準の tools/call（旧 gateway の server_id は送らない）。trace_id は JSON-RPC の id。
    assert call["body"]["id"] == "t-1"
    assert call["body"]["params"] == {
        "name": "rag_search",
        "arguments": {"query": "契約の更新条件"},
    }
    assert result.output is not None
    assert result.output["answer"] == "根拠付き回答"
    assert result.output["citations"][0]["file_name"] == "契約書.pdf"
    assert result.audit_metadata["tool_name"] == "rag__rag_search"
    assert result.audit_metadata["audit_tags"] == ["mcp", "rag"]


def test_token_subject_audience_and_claims_follow_run_user(monkeypatch: MonkeyPatch) -> None:
    mcp = fake_product_mcp(monkeypatch)
    context = ToolInvocationContext(user_uuid=USER_UUID, run_id="run-233", agent_id="agent-233")

    rag = _invoke("rag", "rag_list_business_views", {"limit": 10}, context=context)
    nl2sql = _invoke("nl2sql", "nl2sql_get_job", {"job_id": "job-1"}, context=context)

    assert rag.success is True, rag.error
    assert nl2sql.success is True, nl2sql.error
    rag_call, nl2sql_call = mcp.tool_calls
    # 接続ごとの audience の token（fake が verify_service_token で検証済み）。
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

    first = _invoke("rag", "rag_search", {"query": "a"}, context=ToolInvocationContext())
    second = _invoke("rag", "rag_search", {"query": "b"}, context=ToolInvocationContext())

    assert first.success is True, first.error
    assert second.success is True, second.error
    assert [call["claims"]["sub"] for call in mcp.tool_calls] == [service_user.user_uuid] * 2
    assert "run_id" not in mcp.tool_calls[0]["claims"]


def test_missing_user_and_service_user_fail_before_calling(monkeypatch: MonkeyPatch) -> None:
    mcp = fake_product_mcp(monkeypatch)
    monkeypatch.setattr(get_settings(), "agent_mcp_service_user_login_id", "")

    result = _invoke("rag", "rag_search", {"query": "a"}, context=ToolInvocationContext())

    assert result.success is False
    assert result.error_code == "mcp.user_required"
    assert "AGENT_MCP_SERVICE_USER_LOGIN_ID" in (result.error or "")
    assert mcp.requests == []


def test_unknown_service_user_fails(monkeypatch: MonkeyPatch, auth: ProductionAuth) -> None:
    mcp = fake_product_mcp(monkeypatch)
    monkeypatch.setattr(get_settings(), "agent_mcp_service_user_login_id", "missing-user")

    result = _invoke("nl2sql", "nl2sql_get_job", {"job_id": "j"}, context=ToolInvocationContext())

    assert result.error_code == "mcp.service_user_not_found"
    assert mcp.requests == []


def test_missing_service_token_secret_fails(monkeypatch: MonkeyPatch) -> None:
    mcp = fake_product_mcp(monkeypatch, secret="too-short")

    result = _invoke("rag", "rag_search", {"query": "a"})

    assert result.error_code == "mcp.service_token_not_configured"
    assert "PLATFORM_SERVICE_TOKEN_SECRET" in (result.error or "")
    assert mcp.requests == []


def test_not_configured_mcp_url_fails(monkeypatch: MonkeyPatch) -> None:
    fake_product_mcp(monkeypatch)
    runtime_config_store.upsert_mcp_server("rag", base_url="")

    result = _invoke("rag", "rag_search", {"query": "a"})

    assert result.error_code == "mcp.not_configured"
    assert result.error_details["server_id"] == "rag"


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

    result = _invoke("rag", "rag_search", {"query": "a", "business_view_id": "bv-x"})

    assert result.success is False
    assert result.error_code == "mcp.tool_error"
    assert result.error == "この業務ビューは利用できません。"
    assert result.error_details["error_code"] == "RAG_BUSINESS_VIEW_FORBIDDEN"
    assert result.error_details["details"] == {"business_view_id": "bv-x"}
    assert result.error_details["tool_name"] == "rag_search"
    assert result.error_details["server_id"] == "rag"


def test_invalid_arguments_are_rejected_by_the_connection(monkeypatch: MonkeyPatch) -> None:
    fake_product_mcp(monkeypatch)

    result = _invoke("rag", "rag_search", {"top_k": 0})

    # 引数の検証は呼び先（MCP サーバー）の契約で行い、結果をそのままモデルへ返す。
    assert result.error_code == "mcp.tool_error"
    assert result.error_details["error_code"] == "MCP_TOOL_ARGUMENTS_INVALID"


@pytest.mark.parametrize("status", [502, 504, "timeout"])
def test_tool_calls_do_not_retry_gateway_errors(
    monkeypatch: MonkeyPatch, status: int | Literal["timeout"]
) -> None:
    mcp = fake_product_mcp(monkeypatch)
    mcp.tool_call_statuses.extend([status, status])

    rag = _invoke("rag", "rag_search", {"query": "a"})
    mcp.tool_call_statuses[:] = [status, status]
    nl2sql = _invoke("nl2sql", "nl2sql_query", {"question": "売上"})

    expected = "timeout" if status == "timeout" else "http_error"
    assert rag.error_code == f"mcp.{expected}"
    assert nl2sql.error_code == f"mcp.{expected}"
    # LLM を使う呼び出しは 1 回だけ（読み取り専用の rag_search も、再試行で重複させない）。
    assert [call["name"] for call in mcp.tool_calls] == ["rag_search", "nl2sql_query"]


def test_tool_calls_retry_503(monkeypatch: MonkeyPatch) -> None:
    mcp = fake_product_mcp(monkeypatch)
    mcp.tool_call_statuses.append(503)

    result = _invoke("rag", "rag_search", {"query": "a"})

    assert result.success is True, result.error
    assert [call["name"] for call in mcp.tool_calls] == ["rag_search", "rag_search"]


# ---------------------------------------------------------------------------
# ツールの一覧・承認・ガードレール
# ---------------------------------------------------------------------------


def test_tools_list_reports_read_only_and_function_names(monkeypatch: MonkeyPatch) -> None:
    fake_product_mcp(monkeypatch)
    context = ToolInvocationContext(user_uuid=USER_UUID)

    rag = {tool.name: tool for tool in list_mcp_connection_tools("rag", context=context).tools}
    nl2sql = {
        tool.name: tool for tool in list_mcp_connection_tools("nl2sql", context=context).tools
    }

    assert rag["rag_search"].read_only is True
    assert rag["rag_chat_send_message"].read_only is False
    assert rag["rag_search"].function_name == "rag__rag_search"
    assert "query" in rag["rag_search"].input_schema["properties"]
    assert nl2sql["nl2sql_query"].read_only is False
    assert nl2sql["nl2sql_get_job"].read_only is True


def test_function_names_fit_the_model_tool_name_rules() -> None:
    assert mcp_function_name("rag", "rag_search") == "rag__rag_search"
    assert mcp_function_name("erp.v2", "stock/lookup") == "erp_v2__stock_lookup"
    long_name = mcp_function_name("connection", "x" * 100)
    assert len(long_name) == 64
    assert long_name != mcp_function_name("connection", "x" * 101)


def test_write_tools_require_approval_by_default(monkeypatch: MonkeyPatch) -> None:
    mcp = fake_product_mcp(monkeypatch)
    default_policy = ToolPolicy()

    query = _invoke("nl2sql", "nl2sql_query", {"question": "売上"}, policy=default_policy)
    chat = _invoke(
        "rag",
        "rag_chat_send_message",
        {"content": "質問", "business_view_id": "bv-sales"},
        policy=default_policy,
    )
    search = _invoke("rag", "rag_search", {"query": "契約"}, policy=default_policy)

    assert query.approval_required is True
    assert chat.approval_required is True
    assert search.success is True, search.error
    assert [call["name"] for call in mcp.tool_calls] == ["rag_search"]


def test_nl2sql_sql_is_flagged_by_the_guardrail(monkeypatch: MonkeyPatch) -> None:
    job = {"job_id": "job-x", "status": "done", "generated_sql": "drop table customers"}
    fake_product_mcp(monkeypatch, outputs={"nl2sql_get_job": job})

    result = _invoke("nl2sql", "nl2sql_get_job", {"job_id": "job-x"})

    assert result.success is True, result.error
    assert "nl2sql.non_readonly_sql_returned_as_audit_only" in result.guardrail_warnings


# ---------------------------------------------------------------------------
# 外部 MCP の接続（API キー・structuredContent の無い応答）
# ---------------------------------------------------------------------------


def test_api_key_connection_sends_bearer_and_returns_text_content(
    monkeypatch: MonkeyPatch,
) -> None:
    requests: list[dict[str, Any]] = []

    def handle(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        requests.append({"headers": dict(request.headers), "body": body})
        if "id" not in body:
            return httpx.Response(202)
        if body["method"] == "initialize":
            return httpx.Response(
                200, json={"jsonrpc": "2.0", "id": body["id"], "result": {"capabilities": {}}}
            )
        return httpx.Response(
            200,
            json={
                "jsonrpc": "2.0",
                "id": body["id"],
                "result": {"content": [{"type": "text", "text": "在庫は 12 個です。"}]},
            },
        )

    real_client = httpx.Client
    monkeypatch.setattr(
        "app.features.agent.tools.httpx.Client",
        lambda timeout: real_client(transport=httpx.MockTransport(handle), timeout=timeout),
    )
    runtime_config_store.upsert_mcp_server(
        "erp757", base_url="https://erp.example.test/mcp", auth_mode="api_key", api_key="k-757"
    )
    try:
        result = _invoke(
            "erp757",
            "stock_lookup",
            {"sku": "A-1"},
            policy=ToolPolicy(allow={"erp757__stock_lookup"}),
        )
    finally:
        runtime_config_store.remove_mcp_server("erp757")

    assert result.success is True, result.error
    assert result.output == {"content": "在庫は 12 個です。"}
    assert all(item["headers"]["authorization"] == "Bearer k-757" for item in requests)
    assert requests[-1]["body"]["params"] == {"name": "stock_lookup", "arguments": {"sku": "A-1"}}


# ---------------------------------------------------------------------------
# 画面からのツールの取得（production）
# ---------------------------------------------------------------------------


def test_connection_tools_endpoint_uses_logged_in_user(
    monkeypatch: MonkeyPatch, auth: ProductionAuth
) -> None:
    mcp = fake_product_mcp(monkeypatch)
    viewer = auth.user_with_permissions("mcp-viewer", [MENU_SETTINGS_EXTERNAL_MCP])

    response = client.get("/api/settings/mcp-connections/rag/tools", headers=login("mcp-viewer"))

    assert response.status_code == 200, response.text
    names = [tool["name"] for tool in response.json()["data"]["tools"]]
    assert "rag_search" in names
    assert mcp.requests[-1]["method"] == "tools/list"
    assert mcp.requests[-1]["claims"]["sub"] == viewer.user_uuid
