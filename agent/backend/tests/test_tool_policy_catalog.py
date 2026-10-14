"""ツール権限の画面のツールの一覧（Control Plane のツールと MCP 接続のツール。#1814）。"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import pytest
from mcp_support import fake_product_mcp
from pytest import MonkeyPatch
from security_support import client

from app.features.agent import tool_policy_catalog
from app.features.agent.config import runtime_config_store
from app.features.agent.tools import ExternalToolError


@pytest.fixture
def restore_policy() -> Iterator[None]:
    original = runtime_config_store.get_tool_policy()
    try:
        yield
    finally:
        runtime_config_store.patch_tool_policy(
            default_mode=original.default_mode,
            allow=sorted(original.allow),
            ask=sorted(original.ask),
            deny=sorted(original.deny),
        )


def _tools(data: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {tool["name"]: tool for tool in data["tools"]}


def test_lists_mcp_connection_tools_with_the_default_decision(
    monkeypatch: MonkeyPatch, restore_policy: None
) -> None:
    """MCP 接続のツールも並べ、個別に決めていないときの判定（Run と同じ）を付ける。"""
    del restore_policy
    fake_product_mcp(monkeypatch)
    # 個別のポリシーは「既定に従う」の判定（default_decision）を変えない。
    runtime_config_store.patch_tool_policy(allow=["nl2sql__nl2sql_query"])

    response = client.get("/api/settings/tool-policy/tools")

    assert response.status_code == 200, response.text
    data = response.json()["data"]
    tools = _tools(data)
    # Control Plane のツール。
    assert tools["agent_skill_list"]["server_id"] == "control-plane"
    assert tools["agent_skill_list"]["default_decision"] == "allow"
    # NL2SQL の標準の接続: readOnlyHint=false のツールは既定で承認が要る。
    query = tools["nl2sql__nl2sql_query"]
    assert (query["server_id"], query["default_decision"], query["available"]) == (
        "nl2sql",
        "ask",
        True,
    )
    assert query["server_label"] == "データ問い合わせ（NL2SQL）"
    assert query["permission_level"] == "write" and query["side_effects"] is True
    assert tools["nl2sql__nl2sql_cancel_requirement"]["default_decision"] == "ask"
    assert tools["nl2sql__nl2sql_get_job"]["default_decision"] == "allow"
    # RAG の標準の接続は読み取り専用（承認なし）。
    assert tools["rag__rag_search"]["default_decision"] == "allow"
    connections = {item["server_id"]: item for item in data["connections"]}
    assert connections["nl2sql"] == {
        "server_id": "nl2sql",
        "label": "データ問い合わせ（NL2SQL）",
        "configured": True,
        "tool_count": 5,
        "error": None,
    }


def test_default_mode_deny_changes_the_default_decision(
    monkeypatch: MonkeyPatch, restore_policy: None
) -> None:
    del restore_policy
    fake_product_mcp(monkeypatch)
    runtime_config_store.patch_tool_policy(default_mode="deny")

    tools = _tools(client.get("/api/settings/tool-policy/tools").json()["data"])

    assert tools["nl2sql__nl2sql_query"]["default_decision"] == "deny"
    assert tools["rag__rag_search"]["default_decision"] == "allow"


def test_unreachable_connection_reports_the_reason_and_keeps_saved_policies(
    monkeypatch: MonkeyPatch, restore_policy: None
) -> None:
    """一覧を取れない接続は理由を返し、その接続の保存したポリシーは一覧に無い行として残す。"""
    del restore_policy
    fake_product_mcp(monkeypatch)
    servers = {config.server_id: config for config in runtime_config_store.list_mcp_servers()}
    # 呼び先が応答しない外部の接続（一覧を取れない）と、URL を設定していない接続（失敗にしない）。
    servers["crm1814"] = servers["nl2sql"].model_copy(
        update={"server_id": "crm1814", "label": "CRM", "base_url": "https://crm.test/mcp"}
    )
    servers["blank1814"] = servers["nl2sql"].model_copy(
        update={"server_id": "blank1814", "label": "未設定", "base_url": None}
    )
    monkeypatch.setattr(runtime_config_store, "_mcp_servers", servers)
    original = tool_policy_catalog.list_mcp_connection_tools

    def list_tools(server_id: str, *, context: Any) -> Any:
        if server_id == "crm1814":
            raise ExternalToolError("mcp.http_error", "MCP 接続「CRM」に接続できません。")
        return original(server_id, context=context)

    monkeypatch.setattr(tool_policy_catalog, "list_mcp_connection_tools", list_tools)
    runtime_config_store.patch_tool_policy(deny=["crm1814__delete_customer"])

    data = client.get("/api/settings/tool-policy/tools").json()["data"]

    connections = {item["server_id"]: item for item in data["connections"]}
    assert connections["crm1814"]["tool_count"] == 0
    assert connections["crm1814"]["error"] == "MCP 接続「CRM」に接続できません。"
    assert (connections["blank1814"]["configured"], connections["blank1814"]["error"]) == (
        False,
        None,
    )
    assert connections["rag"]["error"] is None
    stale = _tools(data)["crm1814__delete_customer"]
    assert stale == {
        "name": "crm1814__delete_customer",
        "description": "",
        "server_id": "crm1814",
        "server_label": "CRM",
        "permission_level": None,
        "side_effects": None,
        "default_decision": None,
        "available": False,
    }
