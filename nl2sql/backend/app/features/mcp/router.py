"""NL2SQL の MCP エンドポイント（`POST /api/mcp`、#231）。

Streamable HTTP の JSON 応答だけを扱う（`pr_backend_core.mcp`）。認証は `authorize_api_request`
がサービストークン（`Authorization: Bearer`、audience `nl2sql`）で行い、route manifest では
「認証済みなら通す」。ツールごとの権限はツール側で判定する。
"""

from __future__ import annotations

from fastapi import APIRouter, Request, Response
from pr_backend_core.mcp import mcp_http_response

from .tools import build_mcp_server, has_any_permission_for

router = APIRouter(tags=["mcp"])


@router.post("/mcp", response_class=Response)
async def mcp_endpoint(request: Request) -> Response:
    """MCP の JSON-RPC（initialize / ping / tools/list / tools/call）を処理する。"""
    return await mcp_http_response(
        request,
        build_mcp_server(request),
        has_any_permission=has_any_permission_for(request),
    )
