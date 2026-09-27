"""MCP ツールの契約（platform/contracts/mcp/nl2sql-tools.json）と実装の一致（#248）。

契約は Agent が呼ぶツールの名前と inputSchema の正本。ツールを変えたら
`UPDATE_MCP_CONTRACT=1 uv run pytest tests/test_mcp_contract.py` で契約を書き直し、
Agent のテスト（`agent/backend/tests/test_product_mcp_contract.py`）も通ることを確認する。
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import cast

from fastapi import Request

from app.features.mcp.tools import build_mcp_server

CONTRACT = Path(__file__).resolve().parents[3] / "platform/contracts/mcp/nl2sql-tools.json"


def test_nl2sql_mcp_tools_match_platform_contract() -> None:
    # ツールの定義は request を使わない（request は実行時の利用者の判定だけに使う）。
    server = build_mcp_server(cast(Request, None))
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
