"""Agent の MCP のツールの契約（platform/contracts/mcp/agent-tools.json）と実装の一致（#1874）。

契約は外部の MCP クライアント（Claude Code・Codex など）が呼ぶツールの名前・inputSchema・
outputSchema・annotations の正本。ツールを変えたら
`UPDATE_MCP_CONTRACT=1 uv run pytest tests/test_mcp_contract.py` で契約を書き直す。
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import cast

from fastapi import Request

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
