"""MCP サーバーの共通ヘルパー（#230）。

RAG / NL2SQL が Agent にツールを公開するための最小の MCP（Model Context Protocol）実装。
Streamable HTTP の「POST に JSON で応答する」モードだけを扱う（SSE の stream・resources・
prompts は持たない）。認証と利用者の特定は製品の認可 dependency（サービストークン）が行い、
ここではツールの権限を `has_any_permission` で判定する。

ponytail: 公式 SDK を入れずに `initialize` / `ping` / `tools/list` / `tools/call` だけを手書きする。
SSE での途中経過や `nextCursor` の paging が必要になったら、この module に足す。
"""

from __future__ import annotations

import inspect
import json
import logging
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from typing import Any

from fastapi import HTTPException, Request
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel, ValidationError
from starlette.concurrency import run_in_threadpool

from .api.validation import validation_tool_errors

logger = logging.getLogger(__name__)

LATEST_PROTOCOL_VERSION = "2025-06-18"
SUPPORTED_PROTOCOL_VERSIONS = frozenset({LATEST_PROTOCOL_VERSION, "2025-03-26"})

# JSON-RPC のエラーコード
PARSE_ERROR = -32700
INVALID_REQUEST = -32600
METHOD_NOT_FOUND = -32601
INVALID_PARAMS = -32602

# ツールの実行エラー（`isError: true` の `error_code`）
TOOL_FORBIDDEN_CODE = "MCP_TOOL_FORBIDDEN"
TOOL_ARGUMENTS_INVALID_CODE = "MCP_TOOL_ARGUMENTS_INVALID"
TOOL_INTERNAL_ERROR_CODE = "MCP_TOOL_INTERNAL_ERROR"

HasAnyPermission = Callable[[frozenset[str]], bool]


class McpToolError(Exception):
    """ツールが利用者に返す業務エラー（`isError: true`）。"""

    def __init__(
        self,
        code: str,
        message: str,
        *,
        status: int | None = None,
        details: Mapping[str, Any] | None = None,
    ):
        super().__init__(message)
        self.code = code
        self.message = message
        self.status = status
        self.details = dict(details or {})


@dataclass(frozen=True, slots=True)
class McpToolResult:
    """`structuredContent` に加えて、`content` に足すブロック（画像など）を返すときの結果。

    `output` は通常の handler の戻り値と同じ（`structuredContent` と `content` の text になる）。
    `content` は MCP の content ブロック（例: `{"type": "image", "data": <base64>,
    "mimeType": "image/png"}`）で、text の後ろに足す。大きなデータ（画像）を
    `structuredContent` に入れず、呼び出し側のモデルの文脈を膨らませないために使う（#1282）。
    """

    output: Any
    content: tuple[dict[str, Any], ...] = ()


@dataclass(frozen=True, slots=True)
class McpTool:
    """公開するツール。

    - `handler`: 検証済みの `input_model` を受け取り、`BaseModel` か dict を返す（画像などの
      content を足すときは `McpToolResult`）。
      同期関数は threadpool で実行する（FastAPI の同期 route と同じ）。
    - `permissions`: すべてのグループを満たすこと（グループ内はどれか 1 つ）。
      例: `(frozenset({"a"}), frozenset({"b"}))` は a と b の両方が必要。
    - `output_model`: 指定すると `tools/list` に `outputSchema` を出す（#250）。handler はこの形の
      `structuredContent` を返すこと（呼び出し側と契約で出力の形を確かめられる）。
    """

    name: str
    description: str
    input_model: type[BaseModel]
    handler: Callable[[Any], Any]
    permissions: tuple[frozenset[str], ...] = ()
    read_only: bool = True
    output_model: type[BaseModel] | None = None

    def allowed(self, has_any_permission: HasAnyPermission) -> bool:
        return all(has_any_permission(group) for group in self.permissions)

    def descriptor(self) -> dict[str, Any]:
        descriptor: dict[str, Any] = {
            "name": self.name,
            "description": self.description,
            "inputSchema": self.input_model.model_json_schema(),
            "annotations": {"readOnlyHint": self.read_only},
        }
        if self.output_model is not None:
            descriptor["outputSchema"] = self.output_model.model_json_schema(mode="serialization")
        return descriptor


class McpServer:
    def __init__(
        self,
        *,
        name: str,
        version: str,
        tools: Iterable[McpTool],
        instructions: str | None = None,
    ) -> None:
        self.name = name
        self.version = version
        self.instructions = instructions
        self.tools = {tool.name: tool for tool in tools}

    async def handle(
        self, message: Any, *, has_any_permission: HasAnyPermission
    ) -> dict[str, Any] | None:
        """JSON-RPC の 1 メッセージを処理する。通知・応答なら None。"""
        if not isinstance(message, dict) or message.get("jsonrpc") != "2.0":
            return _error(None, INVALID_REQUEST, "JSON-RPC 2.0 の request を 1 件送ってください。")
        if "id" not in message:
            return None  # 通知（notifications/initialized など）と、client からの応答
        request_id = message["id"]
        method = message.get("method")
        params = message.get("params") or {}
        if not isinstance(method, str) or not isinstance(params, dict):
            return _error(request_id, INVALID_REQUEST, "method と params を確認してください。")
        if method == "initialize":
            return _result(request_id, self._initialize(params))
        if method == "ping":
            return _result(request_id, {})
        if method == "tools/list":
            tools = [t.descriptor() for t in self.tools.values() if t.allowed(has_any_permission)]
            return _result(request_id, {"tools": tools})
        if method == "tools/call":
            return await self._call_tool(request_id, params, has_any_permission)
        return _error(request_id, METHOD_NOT_FOUND, f"未対応の method です: {method}")

    def _initialize(self, params: Mapping[str, Any]) -> dict[str, Any]:
        requested = params.get("protocolVersion")
        version = requested if requested in SUPPORTED_PROTOCOL_VERSIONS else LATEST_PROTOCOL_VERSION
        result: dict[str, Any] = {
            "protocolVersion": version,
            "capabilities": {"tools": {"listChanged": False}},
            "serverInfo": {"name": self.name, "version": self.version},
        }
        if self.instructions:
            result["instructions"] = self.instructions
        return result

    async def _call_tool(
        self, request_id: Any, params: Mapping[str, Any], has_any_permission: HasAnyPermission
    ) -> dict[str, Any]:
        tool = self.tools.get(str(params.get("name", "")))
        if tool is None:
            return _error(request_id, INVALID_PARAMS, f"未知のツールです: {params.get('name')}")
        arguments = params.get("arguments") or {}
        if not tool.allowed(has_any_permission):
            return _result(
                request_id,
                tool_error(TOOL_FORBIDDEN_CODE, "このツールを利用する権限がありません。"),
            )
        try:
            validated = tool.input_model.model_validate(arguments)
        except ValidationError as exc:
            # 位置は cases[0].query の形、文は日本語にする（原文は type / raw_message。#1065）。
            errors = validation_tool_errors(exc.errors(include_url=False, include_input=False))
            return _result(
                request_id,
                tool_error(
                    TOOL_ARGUMENTS_INVALID_CODE,
                    "ツールの引数が正しくありません。",
                    details={"errors": errors},
                ),
            )
        try:
            output = await _invoke(tool.handler, validated)
        except McpToolError as exc:
            return _result(
                request_id,
                tool_error(exc.code, exc.message, status=exc.status, details=exc.details),
            )
        except Exception as exc:  # noqa: BLE001 - 業務エラーを isError にし、それ以外は隠す
            converted = _http_error(exc)
            if converted is None:
                logger.exception("mcp tool failed", extra={"tool": tool.name})
                converted = tool_error(TOOL_INTERNAL_ERROR_CODE, "ツールの実行に失敗しました。")
            return _result(request_id, converted)
        return _result(request_id, tool_result(output))


async def _invoke(handler: Callable[[Any], Any], argument: BaseModel) -> Any:
    if inspect.iscoroutinefunction(handler):
        return await handler(argument)
    result = await run_in_threadpool(handler, argument)
    if inspect.isawaitable(result):
        return await result
    return result


def mcp_error_from_exception(
    exc: Exception, *, details: Mapping[str, Any] | None = None
) -> McpToolError | None:
    """`HTTPException` と、`status_code` を持つ製品の例外（`SecurityApiError` など）を
    ツールのエラーにする。500 系の内部エラー（502 / 503 / 504 以外）は None（内容を隠す）。

    ツールが失敗に詳細（例: 作成済みの ID）を添えたいときに、`details` を足して raise する（#252）。
    """
    status = getattr(exc, "status_code", None)
    if not isinstance(status, int) or status >= 500 and status not in {502, 503, 504}:
        return None
    detail: Any = getattr(exc, "public_message", None)
    if detail is None and isinstance(exc, HTTPException):
        detail = exc.detail
    code = getattr(exc, "code", None)
    if isinstance(detail, dict):
        code = code or detail.get("code") or detail.get("error_code")
        detail = detail.get("message") or detail.get("detail") or json.dumps(detail)
    return McpToolError(
        str(code or f"HTTP_{status}"),
        str(detail or "処理できませんでした。"),
        status=status,
        details=details,
    )


def _http_error(exc: Exception) -> dict[str, Any] | None:
    converted = mcp_error_from_exception(exc)
    if converted is None:
        return None
    return tool_error(converted.code, converted.message, status=converted.status)


def tool_result(output: Any) -> dict[str, Any]:
    extra: tuple[dict[str, Any], ...] = ()
    if isinstance(output, McpToolResult):
        output, extra = output.output, output.content
    body = output.model_dump(mode="json") if isinstance(output, BaseModel) else output
    body = jsonable_encoder(body)
    if not isinstance(body, dict):
        body = {"result": body}
    return {
        "content": [
            {"type": "text", "text": json.dumps(body, ensure_ascii=False)},
            *(dict(block) for block in extra),
        ],
        "structuredContent": body,
        "isError": False,
    }


def tool_error(
    code: str,
    message: str,
    *,
    status: int | None = None,
    details: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    body: dict[str, Any] = {"error_code": code, "message": message}
    if status is not None:
        body["status"] = status
    if details:
        body["details"] = jsonable_encoder(dict(details))
    return {
        "content": [{"type": "text", "text": message}],
        "structuredContent": body,
        "isError": True,
    }


def _result(request_id: Any, result: dict[str, Any]) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": request_id, "result": result}


def _error(request_id: Any, code: int, message: str) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": request_id, "error": {"code": code, "message": message}}


async def mcp_http_response(
    request: Request, server: McpServer, *, has_any_permission: HasAnyPermission
) -> Response:
    """Streamable HTTP の POST を処理する。通知だけなら 202、request には JSON で応答する。"""
    version = request.headers.get("mcp-protocol-version")
    if version and version not in SUPPORTED_PROTOCOL_VERSIONS:
        return JSONResponse(
            _error(None, INVALID_REQUEST, f"未対応の MCP-Protocol-Version です: {version}"),
            status_code=400,
        )
    try:
        message = json.loads(await request.body())
    except (ValueError, UnicodeDecodeError):
        return JSONResponse(_error(None, PARSE_ERROR, "JSON を解析できません。"), status_code=400)
    response = await server.handle(message, has_any_permission=has_any_permission)
    if response is None:
        return Response(status_code=202)
    return JSONResponse(response)
