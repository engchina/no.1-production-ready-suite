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
    ExternalRagChatOutput,
    ExternalRagListBusinessViewsInput,
    ExternalRagListBusinessViewsOutput,
    ExternalRagSearchInput,
    ExternalRagSearchOutput,
    Nl2SqlJobResult,
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


# Agent が受け取る出力の model（呼び先の structuredContent を検証する。extra は無視）。
OUTPUTS: list[tuple[str, str, type[BaseModel]]] = [
    ("rag", "rag_search", ExternalRagSearchOutput),
    ("rag", "rag_chat_send_message", ExternalRagChatOutput),
    ("rag", "rag_list_business_views", ExternalRagListBusinessViewsOutput),
    ("nl2sql", "nl2sql_query", Nl2SqlJobResult),
    ("nl2sql", "nl2sql_get_job", Nl2SqlJobResult),
]


def _resolve(node: dict[str, Any], defs: dict[str, Any]) -> dict[str, Any]:
    """`$ref` と「X または null」を解いた schema。"""
    ref = node.get("$ref")
    if isinstance(ref, str):
        return _resolve(defs[ref.rsplit("/", 1)[-1]], defs)
    options = [item for item in node.get("anyOf", []) if item.get("type") != "null"]
    if len(options) == 1:
        return _resolve(options[0], defs)
    return node


def _missing_output_fields(
    agent: dict[str, Any],
    product: dict[str, Any],
    agent_defs: dict[str, Any],
    product_defs: dict[str, Any],
    path: str = "",
) -> list[str]:
    """Agent が読む項目のうち、呼び先の出力にないもの（入れ子の object・配列の要素もたどる）。"""
    agent, product = _resolve(agent, agent_defs), _resolve(product, product_defs)
    missing: list[str] = []
    if "properties" in agent and "properties" in product:
        for name, child in agent["properties"].items():
            if name not in product["properties"]:
                missing.append(f"{path}{name}")
                continue
            missing += _missing_output_fields(
                child, product["properties"][name], agent_defs, product_defs, f"{path}{name}."
            )
    elif "items" in agent and "items" in product:
        missing += _missing_output_fields(
            agent["items"], product["items"], agent_defs, product_defs, f"{path}[]."
        )
    return missing


@pytest.mark.parametrize(("product", "tool_name", "model"), OUTPUTS)
def test_agent_output_models_fit_product_mcp_contract(
    product: str, tool_name: str, model: type[BaseModel]
) -> None:
    """呼び先の出力の項目名が変わると、Agent は既定値のまま気づけないため契約で確かめる（#250）。"""
    output_schema = _contract_tool(product, tool_name).get("outputSchema")
    assert output_schema is not None, f"{tool_name} の契約に outputSchema がありません"
    agent_schema = model.model_json_schema()
    missing = _missing_output_fields(
        agent_schema, output_schema, agent_schema.get("$defs", {}), output_schema.get("$defs", {})
    )
    assert not missing, f"呼び先の出力にない項目を読みます: {missing}"
