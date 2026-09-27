"""MCP の入口（`POST /api/mcp`。#232）。

Agent がサービストークン（`Authorization: Bearer`、audience `rag`）で呼ぶ。認証は
`authorize_api_request` が行い（token の `sub` の利用者。local は全権限のローカル利用者）、
ツールごとの権限はここで利用者の権限から判定する。ツールは `app.mcp.tools`。
"""

from fastapi import APIRouter, Request
from fastapi.responses import Response
from pr_backend_core.mcp import mcp_http_response

from app.mcp.tools import build_rag_mcp_server

router = APIRouter()


@router.post("", response_class=Response)
async def mcp_endpoint(request: Request) -> Response:
    """MCP の JSON-RPC（`initialize` / `ping` / `tools/list` / `tools/call`）を処理する。"""
    principal = getattr(request.state, "principal", None)

    def has_any_permission(permissions: frozenset[str]) -> bool:
        return principal is not None and bool(principal.has_any_permission(set(permissions)))

    return await mcp_http_response(
        request, build_rag_mcp_server(request), has_any_permission=has_any_permission
    )
