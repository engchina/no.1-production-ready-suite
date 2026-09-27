"""Agent が送る MCP の引数と、RAG / NL2SQL のツールの契約の一致（#248）。

契約の正本は `platform/contracts/mcp/<製品>-tools.json`（RAG / NL2SQL のテストが実装と一致させる）。
Agent は入力 model の `model_dump(exclude_none=True)` をそのまま送るため、入力 model の項目が
契約の `properties` に収まり（NL2SQL は未知の引数を拒否する）、
契約の `required` を必ず満たすことを確かめる。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from pydantic import BaseModel

from app.features.agent.tools import (
    ExternalNl2SqlGetJobInput,
    ExternalNl2SqlInput,
    ExternalRagChatInput,
    ExternalRagListBusinessViewsInput,
    ExternalRagSearchInput,
)

CONTRACTS = Path(__file__).resolve().parents[3] / "platform/contracts/mcp"

# Agent のツールが呼ぶ（製品, ツール, Agent の入力 model）。
# tools.py の call_product_mcp_tool と同じ組。
CALLS: list[tuple[str, str, type[BaseModel]]] = [
    ("rag", "rag_search", ExternalRagSearchInput),
    ("rag", "rag_chat_send_message", ExternalRagChatInput),
    ("rag", "rag_list_business_views", ExternalRagListBusinessViewsInput),
    ("nl2sql", "nl2sql_query", ExternalNl2SqlInput),
    ("nl2sql", "nl2sql_get_job", ExternalNl2SqlGetJobInput),
]


def _contract_tool(product: str, tool_name: str) -> dict[str, Any]:
    contract = json.loads((CONTRACTS / f"{product}-tools.json").read_text(encoding="utf-8"))
    tools = {tool["name"]: tool for tool in contract["tools"]}
    assert tool_name in tools, f"{product} の MCP に {tool_name} がありません: {sorted(tools)}"
    tool: dict[str, Any] = tools[tool_name]
    return tool


@pytest.mark.parametrize(("product", "tool_name", "model"), CALLS)
def test_agent_arguments_fit_product_mcp_contract(
    product: str, tool_name: str, model: type[BaseModel]
) -> None:
    schema = _contract_tool(product, tool_name)["inputSchema"]
    properties = set(schema.get("properties", {}))
    fields = set(model.model_fields)
    assert fields <= properties, f"契約にない引数を送ります: {sorted(fields - properties)}"
    agent_required = {name for name, field in model.model_fields.items() if field.is_required()}
    # nl2sql_query の row_limit は Agent が省略時に設定の既定値を入れる（_external_nl2sql_query）。
    always_sent = {"row_limit"} if tool_name == "nl2sql_query" else set()
    missing = set(schema.get("required", [])) - agent_required - always_sent
    assert not missing, f"契約の必須の引数を必ずは送りません: {sorted(missing)}"
