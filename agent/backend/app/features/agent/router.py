"""Agent feature（skeleton）。

実 run orchestration / planner / executor はバックエンド接続時に実装する。
ここでは共通 envelope と統一ツール契約（registry）を示す。
"""

from fastapi import APIRouter
from pr_backend_core import ApiResponse
from pydantic import BaseModel

from app.features.agent.tools import ToolCall, ToolResult, tool_registry

router = APIRouter(prefix="/agent", tags=["agent"])


class ToolsData(BaseModel):
    tools: list[str]


@router.get("/tools", response_model=ApiResponse[ToolsData])
async def list_tools() -> ApiResponse[ToolsData]:
    """登録済みツール一覧を返す。"""
    return ApiResponse(data=ToolsData(tools=tool_registry.names()))


@router.post("/tools/invoke", response_model=ApiResponse[ToolResult])
async def invoke_tool(call: ToolCall) -> ApiResponse[ToolResult]:
    """統一契約でツールを呼び出す（skeleton: echo のみ登録）。"""
    return ApiResponse(data=tool_registry.invoke(call))
