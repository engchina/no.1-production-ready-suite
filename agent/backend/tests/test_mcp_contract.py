"""Agent の MCP のツールの契約（platform/contracts/mcp/agent-tools.json）と実装の一致（#1874）。

契約は外部の MCP クライアント（Claude Code・Codex など）が呼ぶツールの名前・inputSchema・
outputSchema・annotations の正本。ツールを変えたら
`UPDATE_MCP_CONTRACT=1 uv run pytest tests/test_mcp_contract.py` で契約を書き直す。
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, cast

from fastapi import Request

from app.features.agent.answer_outcome import ANSWER_OUTCOMES
from app.features.agent.mcp_server import build_agent_mcp_server

CONTRACT = Path(__file__).resolve().parents[3] / "platform/contracts/mcp/agent-tools.json"


def test_agent_mcp_tools_match_platform_contract() -> None:
    # ツールの定義は request を使わない（request は実行時の利用者の判定だけに使う）。
    server = build_agent_mcp_server(cast(Request, None))
    actual = {
        "server": server.name,
        "tools": sorted(
            (tool.descriptor() for tool in server.tools.values()), key=lambda t: t["name"]
        ),
    }
    if os.environ.get("UPDATE_MCP_CONTRACT") == "1":
        text = json.dumps(actual, ensure_ascii=False, indent=2) + "\n"
        CONTRACT.write_text(text, encoding="utf-8")
    assert json.loads(CONTRACT.read_text(encoding="utf-8")) == actual, (
        "MCP ツールの定義が契約と違います。"
        "意図した変更なら UPDATE_MCP_CONTRACT=1 で契約を更新してください。"
    )


RAG_CONTRACT = CONTRACT.with_name("rag-tools.json")


def _tools(path: Path) -> dict[str, dict[str, Any]]:
    data = json.loads(path.read_text(encoding="utf-8"))
    return {tool["name"]: tool for tool in data["tools"]}


def _enum(schema: dict[str, Any]) -> list[str]:
    """`enum` か、`anyOf` の enum（null を許す Literal）の値。"""
    if "enum" in schema:
        return list(schema["enum"])
    return [value for item in schema.get("anyOf", []) for value in item.get("enum", [])]


def test_run_output_uses_the_names_and_vocabulary_of_the_rag_contract() -> None:
    """RunOutput の outcome は rag_search の outcome と同じ語彙、根拠の ID は RAG の根拠と同じ名前
    （#3240。RAG が名前・語彙を変えたらここで落ちる）。"""
    rag = _tools(RAG_CONTRACT)["rag_search"]["outputSchema"]
    for name in ("agent_chat", "agent_get_run", "agent_answer_confirmation"):
        run_output = _tools(CONTRACT)[name]["outputSchema"]
        outcome = run_output["properties"]["outcome"]
        assert _enum(outcome) == _enum(rag["properties"]["outcome"]), name
        assert {"type": "null"} in outcome["anyOf"], name
        assert set(_enum(outcome)) == ANSWER_OUTCOMES
        rag_evidence = rag["$defs"]["RagEvidence"]["properties"]
        source = run_output["$defs"]["SourceOutput"]["properties"]
        for field in ("document_id", "chunk_id"):
            assert rag_evidence[field]["type"] == "string"
            assert {"type": "string"} in source[field]["anyOf"], (name, field)


def test_every_agent_tool_output_has_a_schema_version() -> None:
    """3 製品の MCP の出力の外枠をそろえる（形の版を記録できる。#3240）。"""
    for name, tool in _tools(CONTRACT).items():
        assert "schema_version" in tool["outputSchema"]["properties"], name
