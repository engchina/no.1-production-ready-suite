"""Agent の統一ツール契約と registry（skeleton）。

ツール呼び出しを router に散らさず、共通契約（ToolCall / ToolResult）と registry に集約する。
実ツール（oracle_sql / rag_search / http_request 等）は接続時に register する。
"""

from collections.abc import Callable
from typing import Any

from pydantic import BaseModel, Field


class ToolCall(BaseModel):
    name: str
    arguments: dict[str, Any] = Field(default_factory=dict)


class ToolResult(BaseModel):
    name: str
    success: bool
    output: dict[str, Any] | None = None
    error: str | None = None


ToolHandler = Callable[[dict[str, Any]], dict[str, Any]]


class ToolRegistry:
    """名前 -> ツールハンドラの registry。"""

    def __init__(self) -> None:
        self._tools: dict[str, ToolHandler] = {}

    def register(self, name: str, handler: ToolHandler) -> None:
        self._tools[name] = handler

    def names(self) -> list[str]:
        return sorted(self._tools)

    def invoke(self, call: ToolCall) -> ToolResult:
        handler = self._tools.get(call.name)
        if handler is None:
            return ToolResult(name=call.name, success=False, error="unknown tool")
        try:
            output = handler(call.arguments)
        except Exception as exc:  # noqa: BLE001 - ツール失敗を結果へ正規化する境界
            return ToolResult(name=call.name, success=False, error=str(exc))
        return ToolResult(name=call.name, success=True, output=output)


def _echo_tool(arguments: dict[str, Any]) -> dict[str, Any]:
    return {"echo": arguments}


# 既定 registry（skeleton では echo のみ。実ツールは接続時に register）。
tool_registry = ToolRegistry()
tool_registry.register("echo", _echo_tool)
