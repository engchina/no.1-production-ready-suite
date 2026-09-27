"""MCP サーバーの共通ヘルパー（#230）。"""

from __future__ import annotations

from typing import Any

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import Response
from fastapi.testclient import TestClient
from pydantic import BaseModel, Field

from pr_backend_core.mcp import (
    INVALID_PARAMS,
    LATEST_PROTOCOL_VERSION,
    METHOD_NOT_FOUND,
    TOOL_ARGUMENTS_INVALID_CODE,
    TOOL_FORBIDDEN_CODE,
    TOOL_INTERNAL_ERROR_CODE,
    McpServer,
    McpTool,
    McpToolError,
    mcp_error_from_exception,
    mcp_http_response,
)


class _EchoInput(BaseModel):
    text: str = Field(min_length=1)


class _EchoOutput(BaseModel):
    echoed: str


class _CodedError(Exception):
    """製品の SecurityApiError と同じ形（status_code / public_message / code）。"""

    def __init__(self) -> None:
        super().__init__("scope")
        self.status_code = 403
        self.public_message = "範囲外です。"
        self.code = "RAG_SCOPE_FORBIDDEN"


def _echo(arguments: _EchoInput) -> _EchoOutput:
    if arguments.text == "business":
        raise McpToolError("ECHO_REJECTED", "受け付けられません。", details={"text": "business"})
    if arguments.text == "http":
        raise HTTPException(status_code=404, detail={"code": "NOT_FOUND", "message": "ありません"})
    if arguments.text == "coded":
        raise _CodedError()
    if arguments.text == "boom":
        raise RuntimeError("secret internals")
    return _EchoOutput(echoed=arguments.text)


async def _async_echo(arguments: _EchoInput) -> dict[str, Any]:
    return {"echoed": arguments.text.upper()}


SERVER = McpServer(
    name="test-server",
    version="1",
    tools=[
        McpTool(
            name="echo",
            description="入力を返す",
            input_model=_EchoInput,
            handler=_echo,
            permissions=(frozenset({"menu.search", "menu.chat"}),),
            output_model=_EchoOutput,
        ),
        McpTool(
            name="shout",
            description="大文字で返す",
            input_model=_EchoInput,
            handler=_async_echo,
            permissions=(frozenset({"a"}), frozenset({"b"})),
            read_only=False,
        ),
    ],
    instructions="テスト用",
)


def _client(granted: set[str]) -> TestClient:
    app = FastAPI()

    @app.post("/mcp")
    async def mcp(request: Request) -> Response:
        return await mcp_http_response(
            request, SERVER, has_any_permission=lambda group: bool(group & granted)
        )

    return TestClient(app)


def _rpc(client: TestClient, method: str, params: dict[str, Any] | None = None) -> Any:
    response = client.post(
        "/mcp", json={"jsonrpc": "2.0", "id": 7, "method": method, "params": params or {}}
    )
    assert response.status_code == 200
    body = response.json()
    assert body["id"] == 7
    return body


def _call(client: TestClient, text: str, name: str = "echo") -> dict[str, Any]:
    body = _rpc(client, "tools/call", {"name": name, "arguments": {"text": text}})
    result: dict[str, Any] = body["result"]
    return result


def test_initialize_negotiates_protocol_version() -> None:
    client = _client(set())
    result = _rpc(client, "initialize", {"protocolVersion": "2025-03-26"})["result"]
    assert result["protocolVersion"] == "2025-03-26"
    assert result["serverInfo"] == {"name": "test-server", "version": "1"}
    assert result["capabilities"] == {"tools": {"listChanged": False}}
    assert result["instructions"] == "テスト用"
    unknown = _rpc(client, "initialize", {"protocolVersion": "1999-01-01"})["result"]
    assert unknown["protocolVersion"] == LATEST_PROTOCOL_VERSION


def test_notifications_return_202_and_unknown_method_is_error() -> None:
    client = _client(set())
    response = client.post("/mcp", json={"jsonrpc": "2.0", "method": "notifications/initialized"})
    assert (response.status_code, response.content) == (202, b"")
    assert _rpc(client, "ping")["result"] == {}
    assert _rpc(client, "resources/list")["error"]["code"] == METHOD_NOT_FOUND


def test_rejects_bad_json_batch_and_protocol_header() -> None:
    client = _client(set())
    assert client.post("/mcp", content=b"{").status_code == 400
    batch = client.post("/mcp", json=[{"jsonrpc": "2.0", "id": 1, "method": "ping"}])
    assert batch.json()["error"]["code"] == -32600
    response = client.post(
        "/mcp",
        json={"jsonrpc": "2.0", "id": 1, "method": "ping"},
        headers={"MCP-Protocol-Version": "1999-01-01"},
    )
    assert response.status_code == 400


def test_tools_list_shows_only_permitted_tools_with_schema() -> None:
    tools = _rpc(_client({"menu.chat"}), "tools/list")["result"]["tools"]
    assert [tool["name"] for tool in tools] == ["echo"]
    assert tools[0]["inputSchema"]["required"] == ["text"]
    assert tools[0]["annotations"] == {"readOnlyHint": True}
    # output_model を渡したツールだけ outputSchema を出す（#250）。
    assert tools[0]["outputSchema"]["required"] == ["echoed"]
    shout = _rpc(_client({"a", "b"}), "tools/list")["result"]["tools"][0]
    assert "outputSchema" not in shout
    # グループはすべて満たす必要がある（a と b）。
    assert len(_rpc(_client({"a"}), "tools/list")["result"]["tools"]) == 0
    assert len(_rpc(_client({"a", "b"}), "tools/list")["result"]["tools"]) == 1


def test_tools_call_returns_structured_content() -> None:
    result = _call(_client({"menu.search"}), "こんにちは")
    assert result["isError"] is False
    assert result["structuredContent"] == {"echoed": "こんにちは"}
    assert "こんにちは" in result["content"][0]["text"]
    shouted = _call(_client({"a", "b"}), "hi", name="shout")
    assert shouted["structuredContent"] == {"echoed": "HI"}


def test_tools_call_errors() -> None:
    client = _client({"menu.search"})
    unknown = _rpc(client, "tools/call", {"name": "missing", "arguments": {}})
    assert unknown["error"]["code"] == INVALID_PARAMS

    forbidden = _call(client, "hi", name="shout")
    assert forbidden["isError"] is True
    assert forbidden["structuredContent"]["error_code"] == TOOL_FORBIDDEN_CODE

    invalid = _rpc(client, "tools/call", {"name": "echo", "arguments": {"text": ""}})["result"]
    assert invalid["structuredContent"]["error_code"] == TOOL_ARGUMENTS_INVALID_CODE
    assert invalid["structuredContent"]["details"]["errors"][0]["loc"] == "text"

    business = _call(client, "business")["structuredContent"]
    assert business == {
        "error_code": "ECHO_REJECTED",
        "message": "受け付けられません。",
        "details": {"text": "business"},
    }
    http = _call(client, "http")["structuredContent"]
    assert http == {"error_code": "NOT_FOUND", "message": "ありません", "status": 404}
    coded = _call(client, "coded")["structuredContent"]
    assert coded == {"error_code": "RAG_SCOPE_FORBIDDEN", "message": "範囲外です。", "status": 403}

    internal = _call(client, "boom")
    assert internal["isError"] is True
    assert internal["structuredContent"]["error_code"] == TOOL_INTERNAL_ERROR_CODE
    assert "secret" not in internal["content"][0]["text"]


def test_mcp_error_from_exception_keeps_code_and_adds_details() -> None:
    """例外からツールのエラーを作り、詳細を添えられる。内部エラーは None（#252）。"""
    converted = mcp_error_from_exception(_CodedError(), details={"conversation_id": "c1"})
    assert converted is not None
    assert (converted.code, converted.message, converted.status, converted.details) == (
        "RAG_SCOPE_FORBIDDEN",
        "範囲外です。",
        403,
        {"conversation_id": "c1"},
    )
    timeout = mcp_error_from_exception(HTTPException(status_code=504, detail="タイムアウト"))
    assert timeout is not None and (timeout.code, timeout.status) == ("HTTP_504", 504)
    assert mcp_error_from_exception(HTTPException(status_code=500, detail="x")) is None
    assert mcp_error_from_exception(RuntimeError("x")) is None
