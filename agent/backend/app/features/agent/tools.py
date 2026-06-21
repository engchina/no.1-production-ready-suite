"""Agent Runtime の統一ツール契約。

業務 RAG / NL2SQL はこのプロジェクト内で実装せず、外部サービスを
安全に呼ぶ Tool として扱う。Tool は必ず schema / 権限 / 監査情報を持つ。
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import resource
import shlex
import subprocess  # nosec B404
from collections.abc import Callable
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from time import monotonic, perf_counter
from typing import Any
from uuid import uuid4

import httpx
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from app.features.agent.config import CommandPolicyRuntimeConfig, runtime_config_store
from app.features.agent.skills import (
    AgentSkillListOutput,
    AgentSkillPlanOutput,
    AgentSkillRunInput,
    skill_registry,
)
from app.settings import get_settings

JsonObject = dict[str, Any]
_MCP_OAUTH_TOKEN_SKEW_SECONDS = 30.0
_mcp_oauth_token_cache: dict[str, tuple[str, float]] = {}


def _now() -> datetime:
    return datetime.now(UTC)


class ToolPermissionLevel(StrEnum):
    READ = "read"
    WRITE = "write"
    SENSITIVE = "sensitive"


class ToolPolicyDecision(StrEnum):
    ALLOW = "allow"
    ASK = "ask"
    DENY = "deny"


class ExternalToolMode(StrEnum):
    EXECUTE = "execute"
    DRY_RUN = "dry_run"


class ToolCall(BaseModel):
    name: str
    arguments: JsonObject = Field(default_factory=dict)
    trace_id: str | None = None


class ToolDefinition(BaseModel):
    name: str
    description: str
    input_schema: JsonObject
    output_schema: JsonObject
    permission_level: ToolPermissionLevel = ToolPermissionLevel.READ
    side_effects: bool = False
    timeout_seconds: float = 10.0
    max_retries: int = 0
    audit_tags: list[str] = Field(default_factory=list)


class ToolResult(BaseModel):
    name: str
    success: bool
    output: JsonObject | None = None
    error: str | None = None
    error_code: str | None = None
    error_details: JsonObject = Field(default_factory=dict)
    started_at: datetime = Field(default_factory=_now)
    completed_at: datetime = Field(default_factory=_now)
    duration_ms: int = 0
    policy_decision: ToolPolicyDecision = ToolPolicyDecision.ALLOW
    approval_required: bool = False
    approval_id: str | None = None
    guardrail_warnings: list[str] = Field(default_factory=list)
    audit_metadata: JsonObject = Field(default_factory=dict)


class ExternalRagSearchInput(BaseModel):
    query: str
    business_view_id: str | None = None
    filters: JsonObject = Field(default_factory=dict)
    top_k: int | None = Field(default=None, ge=1, le=100)
    trace_id: str | None = None


class RagContext(BaseModel):
    id: str | None = None
    title: str | None = None
    content: str
    score: float | None = None
    metadata: JsonObject = Field(default_factory=dict)


class RagCitation(BaseModel):
    source_id: str | None = None
    title: str | None = None
    url: str | None = None
    page: int | None = None
    chunk_id: str | None = None
    metadata: JsonObject = Field(default_factory=dict)


class ExternalRagSearchOutput(BaseModel):
    answer: str | None = None
    contexts: list[RagContext] = Field(default_factory=list)
    citations: list[RagCitation] = Field(default_factory=list)
    metadata: JsonObject = Field(default_factory=dict)


class ExternalNl2SqlInput(BaseModel):
    question: str
    data_domain_id: str | None = None
    business_view_id: str | None = None
    filters: JsonObject = Field(default_factory=dict)
    limit: int | None = Field(default=None, ge=1, le=10_000)
    mode: ExternalToolMode = ExternalToolMode.EXECUTE
    include_sql: bool = True
    trace_id: str | None = None


class StructuredColumn(BaseModel):
    name: str
    type: str
    label: str | None = None
    unit: str | None = None


class ExternalNl2SqlOutput(BaseModel):
    sql: str | None = None
    columns: list[StructuredColumn] = Field(default_factory=list)
    rows: list[JsonObject] = Field(default_factory=list)
    row_count: int = 0
    truncated: bool = False
    execution_time_ms: int | None = None
    lineage: JsonObject | None = None
    warnings: list[str] = Field(default_factory=list)
    metadata: JsonObject = Field(default_factory=dict)


class ExternalMcpCallInput(BaseModel):
    tool_name: str
    arguments: JsonObject = Field(default_factory=dict)
    server_id: str | None = None
    trace_id: str | None = None


class ExternalMcpListToolsInput(BaseModel):
    server_id: str | None = None
    trace_id: str | None = None


class ExternalMcpToolInfo(BaseModel):
    name: str
    description: str = ""
    input_schema: JsonObject = Field(default_factory=dict)
    output_schema: JsonObject | None = None
    server_id: str | None = None
    metadata: JsonObject = Field(default_factory=dict)


class ExternalMcpToolsData(BaseModel):
    tools: list[ExternalMcpToolInfo] = Field(default_factory=list)
    metadata: JsonObject = Field(default_factory=dict)


class ExternalMcpCallOutput(BaseModel):
    tool_name: str
    content: list[JsonObject] = Field(default_factory=list)
    structured_content: JsonObject | None = None
    result: JsonObject = Field(default_factory=dict)
    is_error: bool = False
    metadata: JsonObject = Field(default_factory=dict)


class JsonRpcResponse(BaseModel):
    jsonrpc: str | None = None
    id: str | int | None = None
    result: JsonObject | None = None
    error: JsonObject | None = None


class SandboxCommandInput(BaseModel):
    command: list[str] = Field(min_length=1, max_length=32)
    cwd: str | None = None
    timeout_seconds: float | None = Field(default=None, ge=0.1, le=300)
    output_limit_bytes: int | None = Field(default=None, ge=100, le=200_000)
    trace_id: str | None = None


class SandboxCommandOutput(BaseModel):
    command: list[str]
    cwd: str
    exit_code: int | None
    stdout: str
    stderr: str
    duration_ms: int
    timed_out: bool = False
    truncated: bool = False
    metadata: JsonObject = Field(default_factory=dict)


class ToolInvocationContext(BaseModel):
    approval_id: str | None = None
    trace_id: str | None = None
    agent_id: str | None = None
    command_allowed_prefixes: list[str] = Field(default_factory=list)


ToolHandler = Callable[[JsonObject, ToolInvocationContext], JsonObject]


class ExternalToolError(RuntimeError):
    def __init__(self, code: str, message: str, details: JsonObject | None = None) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.details = details or {}


class ToolPolicy(BaseModel):
    default_mode: str = "approval"
    allow: set[str] = Field(default_factory=set)
    ask: set[str] = Field(default_factory=set)
    deny: set[str] = Field(default_factory=set)

    def decide(self, definition: ToolDefinition) -> ToolPolicyDecision:
        if definition.name in self.deny:
            return ToolPolicyDecision.DENY
        if definition.name in self.ask:
            return ToolPolicyDecision.ASK
        if definition.name in self.allow:
            return ToolPolicyDecision.ALLOW
        if definition.permission_level == ToolPermissionLevel.READ and not definition.side_effects:
            return ToolPolicyDecision.ALLOW
        if self.default_mode == "deny":
            return ToolPolicyDecision.DENY
        return ToolPolicyDecision.ASK


def _tool_result(
    *,
    name: str,
    success: bool,
    started_at: datetime,
    started_monotonic: float,
    output: JsonObject | None = None,
    error: str | None = None,
    error_code: str | None = None,
    error_details: JsonObject | None = None,
    policy_decision: ToolPolicyDecision = ToolPolicyDecision.ALLOW,
    approval_required: bool = False,
    approval_id: str | None = None,
    guardrail_warnings: list[str] | None = None,
    audit_metadata: JsonObject | None = None,
) -> ToolResult:
    completed_at = _now()
    metadata = dict(audit_metadata or {})
    metadata["success"] = success
    if error_code:
        metadata["error_code"] = error_code
    return ToolResult(
        name=name,
        success=success,
        output=output,
        error=error,
        error_code=error_code,
        error_details=error_details or {},
        started_at=started_at,
        completed_at=completed_at,
        duration_ms=max(0, round((perf_counter() - started_monotonic) * 1000)),
        policy_decision=policy_decision,
        approval_required=approval_required,
        approval_id=approval_id,
        guardrail_warnings=guardrail_warnings or [],
        audit_metadata=metadata,
    )


def _tool_audit_metadata(
    *,
    definition: ToolDefinition,
    context: ToolInvocationContext,
    force: bool,
) -> JsonObject:
    return {
        "tool_name": definition.name,
        "permission_level": definition.permission_level.value,
        "side_effects": definition.side_effects,
        "timeout_seconds": definition.timeout_seconds,
        "max_retries": definition.max_retries,
        "audit_tags": list(definition.audit_tags),
        "trace_id": context.trace_id,
        "approval_id": context.approval_id,
        "force": force,
    }


class ToolRegistry:
    """名前 -> schema 化 Tool の registry。"""

    def __init__(self) -> None:
        self._definitions: dict[str, ToolDefinition] = {}
        self._handlers: dict[str, ToolHandler] = {}

    def register(self, definition: ToolDefinition, handler: ToolHandler) -> None:
        self._definitions[definition.name] = definition
        self._handlers[definition.name] = handler

    def names(self) -> list[str]:
        return sorted(self._definitions)

    def definitions(self) -> list[ToolDefinition]:
        return [self._definitions[name] for name in self.names()]

    def get(self, name: str) -> ToolDefinition | None:
        return self._definitions.get(name)

    def invoke(
        self,
        call: ToolCall,
        *,
        policy: ToolPolicy | None = None,
        context: ToolInvocationContext | None = None,
        force: bool = False,
    ) -> ToolResult:
        started_at = _now()
        started_monotonic = perf_counter()
        active_context = context or ToolInvocationContext(trace_id=call.trace_id)
        definition = self._definitions.get(call.name)
        handler = self._handlers.get(call.name)
        if definition is None or handler is None:
            return _tool_result(
                name=call.name,
                success=False,
                error="unknown tool",
                started_at=started_at,
                started_monotonic=started_monotonic,
                audit_metadata={
                    "tool_name": call.name,
                    "trace_id": active_context.trace_id,
                    "approval_id": active_context.approval_id,
                },
            )

        active_policy = policy or ToolPolicy(
            default_mode=get_settings().agent_permission_default_mode
        )
        decision = active_policy.decide(definition)
        audit_metadata = _tool_audit_metadata(
            definition=definition,
            context=active_context,
            force=force,
        )
        if decision == ToolPolicyDecision.DENY:
            return _tool_result(
                name=call.name,
                success=False,
                error="tool invocation denied by policy",
                started_at=started_at,
                started_monotonic=started_monotonic,
                policy_decision=decision,
                audit_metadata=audit_metadata,
            )
        if decision == ToolPolicyDecision.ASK and not force:
            return _tool_result(
                name=call.name,
                success=False,
                started_at=started_at,
                started_monotonic=started_monotonic,
                policy_decision=decision,
                approval_required=True,
                error="approval required",
                audit_metadata=audit_metadata,
            )

        try:
            output = handler(
                call.arguments,
                active_context,
            )
        except ExternalToolError as exc:
            return _tool_result(
                name=call.name,
                success=False,
                error=exc.message,
                error_code=exc.code,
                error_details=exc.details,
                started_at=started_at,
                started_monotonic=started_monotonic,
                policy_decision=ToolPolicyDecision.ALLOW,
                audit_metadata=audit_metadata,
            )
        except Exception as exc:  # noqa: BLE001 - ツール境界では失敗を結果に正規化する
            return _tool_result(
                name=call.name,
                success=False,
                error=str(exc),
                error_code="tool.unhandled_error",
                started_at=started_at,
                started_monotonic=started_monotonic,
                policy_decision=ToolPolicyDecision.ALLOW,
                audit_metadata=audit_metadata,
            )
        guarded_output, warnings = _guard_tool_output(definition, output)
        return _tool_result(
            name=call.name,
            success=True,
            output=guarded_output,
            started_at=started_at,
            started_monotonic=started_monotonic,
            policy_decision=ToolPolicyDecision.ALLOW,
            guardrail_warnings=warnings,
            audit_metadata=audit_metadata,
        )


class ExternalRagClient:
    def __init__(
        self,
        base_url: str,
        api_key: str | None,
        timeout_seconds: float,
        max_retries: int,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._api_key = api_key
        self._timeout_seconds = timeout_seconds
        self._max_retries = max(0, max_retries)

    def search(self, request: ExternalRagSearchInput) -> ExternalRagSearchOutput:
        headers = _auth_headers(self._api_key)
        payload = request.model_dump(exclude_none=True)
        response_payload = _post_external_json(
            service_code="external_rag",
            service_label="external RAG",
            url=f"{self._base_url}/search",
            payload=payload,
            headers=headers,
            timeout_seconds=self._timeout_seconds,
            max_retries=self._max_retries,
        )
        try:
            return ExternalRagSearchOutput.model_validate(response_payload)
        except ValidationError as exc:
            raise ExternalToolError(
                "external_rag.invalid_response",
                "external RAG response schema is invalid",
                {"errors": _validation_errors(exc)},
            ) from exc


class ExternalNl2SqlClient:
    def __init__(
        self,
        base_url: str,
        api_key: str | None,
        timeout_seconds: float,
        default_limit: int,
        max_retries: int,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._api_key = api_key
        self._timeout_seconds = timeout_seconds
        self._default_limit = default_limit
        self._max_retries = max(0, max_retries)

    def query(self, request: ExternalNl2SqlInput) -> ExternalNl2SqlOutput:
        headers = _auth_headers(self._api_key)
        payload = request.model_dump(exclude_none=True)
        payload["limit"] = request.limit or self._default_limit
        response_payload = _post_external_json(
            service_code="external_nl2sql",
            service_label="external NL2SQL",
            url=f"{self._base_url}/query",
            payload=payload,
            headers=headers,
            timeout_seconds=self._timeout_seconds,
            max_retries=self._max_retries,
        )
        try:
            return ExternalNl2SqlOutput.model_validate(response_payload)
        except ValidationError as exc:
            raise ExternalToolError(
                "external_nl2sql.invalid_response",
                "external NL2SQL response schema is invalid",
                {"errors": _validation_errors(exc)},
            ) from exc


class ExternalMcpClient:
    def __init__(
        self,
        base_url: str,
        api_key: str | None,
        session_id: str | None,
        oauth_token_url: str | None,
        oauth_client_id: str | None,
        oauth_client_secret: str | None,
        oauth_scope: str | None,
        timeout_seconds: float,
        max_retries: int,
    ) -> None:
        self._endpoint_url = base_url.rstrip("/")
        self._api_key = api_key
        self._session_id = session_id
        self._oauth_token_url = oauth_token_url
        self._oauth_client_id = oauth_client_id
        self._oauth_client_secret = oauth_client_secret
        self._oauth_scope = oauth_scope
        self._timeout_seconds = timeout_seconds
        self._max_retries = max(0, max_retries)

    def list_tools(self, request: ExternalMcpListToolsInput) -> ExternalMcpToolsData:
        request_id = request.trace_id or f"mcp_{uuid4().hex}"
        payload: JsonObject = {
            "jsonrpc": "2.0",
            "id": request_id,
            "method": "tools/list",
        }
        if request.server_id:
            payload["params"] = {"server_id": request.server_id}
        response_payload = _post_external_json(
            service_code="external_mcp",
            service_label="external MCP gateway",
            url=self._endpoint_url,
            payload=payload,
            headers=self._headers(),
            timeout_seconds=self._timeout_seconds,
            max_retries=self._max_retries,
        )
        response = _mcp_jsonrpc_response(response_payload)
        if response.error is not None:
            raise ExternalToolError(
                "external_mcp.rpc_error",
                "external MCP gateway returned a JSON-RPC error",
                {
                    "jsonrpc_id": response.id,
                    "error": response.error,
                    "server_id": request.server_id,
                    "method": "tools/list",
                },
            )
        if response.result is None:
            raise ExternalToolError(
                "external_mcp.missing_result",
                "external MCP response is missing result",
                {"jsonrpc_id": response.id, "method": "tools/list"},
            )
        return _mcp_tools_from_result(request, response)

    def call_tool(self, request: ExternalMcpCallInput) -> ExternalMcpCallOutput:
        request_id = request.trace_id or f"mcp_{uuid4().hex}"
        params: JsonObject = {
            "name": request.tool_name,
            "arguments": request.arguments,
        }
        if request.server_id:
            params["server_id"] = request.server_id
        payload: JsonObject = {
            "jsonrpc": "2.0",
            "id": request_id,
            "method": "tools/call",
            "params": params,
        }
        response_payload = _post_external_json(
            service_code="external_mcp",
            service_label="external MCP gateway",
            url=self._endpoint_url,
            payload=payload,
            headers=self._headers(),
            timeout_seconds=self._timeout_seconds,
            max_retries=self._max_retries,
        )
        response = _mcp_jsonrpc_response(response_payload)
        if response.error is not None:
            raise ExternalToolError(
                "external_mcp.rpc_error",
                "external MCP gateway returned a JSON-RPC error",
                {
                    "jsonrpc_id": response.id,
                    "error": response.error,
                    "server_id": request.server_id,
                    "tool_name": request.tool_name,
                },
            )
        if response.result is None:
            raise ExternalToolError(
                "external_mcp.missing_result",
                "external MCP response is missing result",
                {"jsonrpc_id": response.id, "tool_name": request.tool_name},
            )
        return _mcp_output_from_result(request, response)

    def _headers(self) -> dict[str, str]:
        oauth_token = _mcp_oauth_bearer_token(
            token_url=self._oauth_token_url,
            client_id=self._oauth_client_id,
            client_secret=self._oauth_client_secret,
            scope=self._oauth_scope,
            timeout_seconds=self._timeout_seconds,
        )
        return _mcp_headers(
            api_key=self._api_key,
            session_id=self._session_id,
            oauth_token=oauth_token,
        )


def _post_external_json(
    *,
    service_code: str,
    service_label: str,
    url: str,
    payload: JsonObject,
    headers: dict[str, str],
    timeout_seconds: float,
    max_retries: int,
) -> JsonObject:
    attempts = max_retries + 1
    last_error: ExternalToolError | None = None
    for attempt in range(1, attempts + 1):
        try:
            with httpx.Client(timeout=timeout_seconds) as client:
                response = client.post(url, json=payload, headers=headers)
                response.raise_for_status()
                data = _response_json_object(
                    response,
                    service_code=service_code,
                    service_label=service_label,
                    attempt=attempt,
                )
            if not isinstance(data, dict):
                raise ExternalToolError(
                    f"{service_code}.invalid_json",
                    f"{service_label} response body must be a JSON object",
                    {"attempt": attempt},
                )
            return data
        except httpx.TimeoutException as exc:
            last_error = ExternalToolError(
                f"{service_code}.timeout",
                f"{service_label} request timed out",
                {"attempt": attempt, "max_retries": max_retries},
            )
            if attempt >= attempts:
                raise last_error from exc
        except httpx.HTTPStatusError as exc:
            status_code = exc.response.status_code
            last_error = ExternalToolError(
                f"{service_code}.http_error",
                f"{service_label} returned HTTP {status_code}",
                {
                    "attempt": attempt,
                    "max_retries": max_retries,
                    "status_code": status_code,
                    "body": _response_text(exc.response),
                },
            )
            if attempt >= attempts or status_code not in {429, 500, 502, 503, 504}:
                raise last_error from exc
        except httpx.RequestError as exc:
            last_error = ExternalToolError(
                f"{service_code}.request_error",
                f"{service_label} request failed",
                {
                    "attempt": attempt,
                    "max_retries": max_retries,
                    "reason": str(exc),
                },
            )
            if attempt >= attempts:
                raise last_error from exc
        except ExternalToolError:
            raise
        except ValueError as exc:
            raise ExternalToolError(
                f"{service_code}.invalid_json",
                f"{service_label} response body is not valid JSON",
                {"attempt": attempt},
            ) from exc
    if last_error is not None:
        raise last_error
    raise ExternalToolError(f"{service_code}.unknown_error", f"{service_label} failed")


def _response_json_object(
    response: httpx.Response,
    *,
    service_code: str,
    service_label: str,
    attempt: int,
) -> JsonObject:
    try:
        data = response.json()
    except ValueError:
        data = _stream_json_object(response.text)
    if not isinstance(data, dict):
        raise ExternalToolError(
            f"{service_code}.invalid_json",
            f"{service_label} response body must be a JSON object",
            {"attempt": attempt},
        )
    return data


def _stream_json_object(text: str) -> JsonObject:
    candidates: list[JsonObject] = []
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith(":"):
            continue
        if line.startswith("data:"):
            line = line.removeprefix("data:").strip()
        if not line or line == "[DONE]":
            continue
        try:
            value = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict):
            candidates.append(value)
    if not candidates:
        raise ValueError("stream response did not contain a JSON object")
    return candidates[-1]


def _response_text(response: httpx.Response) -> str:
    text = response.text.strip()
    if len(text) > 500:
        return f"{text[:500]}..."
    return text


def _mcp_jsonrpc_response(payload: JsonObject) -> JsonRpcResponse:
    try:
        return JsonRpcResponse.model_validate(payload)
    except ValidationError as exc:
        raise ExternalToolError(
            "external_mcp.invalid_response",
            "external MCP response schema is invalid",
            {"errors": _validation_errors(exc)},
        ) from exc


def _mcp_tools_from_result(
    request: ExternalMcpListToolsInput,
    response: JsonRpcResponse,
) -> ExternalMcpToolsData:
    result = response.result or {}
    raw_tools = result.get("tools")
    if not isinstance(raw_tools, list):
        raise ExternalToolError(
            "external_mcp.invalid_response",
            "external MCP tools/list result must include tools[]",
            {"jsonrpc_id": response.id, "server_id": request.server_id},
        )
    tools: list[ExternalMcpToolInfo] = []
    for raw_tool in raw_tools:
        if not isinstance(raw_tool, dict):
            raise ExternalToolError(
                "external_mcp.invalid_response",
                "external MCP tool descriptor must be an object",
                {"jsonrpc_id": response.id, "server_id": request.server_id},
            )
        name = raw_tool.get("name")
        if not isinstance(name, str) or not name:
            raise ExternalToolError(
                "external_mcp.invalid_response",
                "external MCP tool descriptor is missing name",
                {"jsonrpc_id": response.id, "server_id": request.server_id},
            )
        input_schema = _mcp_schema_value(raw_tool, "inputSchema", "input_schema")
        output_schema = _mcp_schema_value(raw_tool, "outputSchema", "output_schema")
        description = raw_tool.get("description")
        tool_server_id = raw_tool.get("server_id") or raw_tool.get("serverId") or request.server_id
        metadata = raw_tool.get("metadata")
        tools.append(
            ExternalMcpToolInfo(
                name=name,
                description=description if isinstance(description, str) else "",
                input_schema=input_schema or {},
                output_schema=output_schema,
                server_id=tool_server_id if isinstance(tool_server_id, str) else None,
                metadata=metadata if isinstance(metadata, dict) else {},
            )
        )
    next_cursor = result.get("nextCursor", result.get("next_cursor"))
    return ExternalMcpToolsData(
        tools=tools,
        metadata={
            "jsonrpc_id": response.id,
            "server_id": request.server_id,
            "method": "tools/list",
            "next_cursor": next_cursor if isinstance(next_cursor, str) else None,
        },
    )


def _mcp_schema_value(raw_tool: JsonObject, camel_key: str, snake_key: str) -> JsonObject | None:
    value = raw_tool.get(camel_key)
    if not isinstance(value, dict):
        value = raw_tool.get(snake_key)
    return value if isinstance(value, dict) else None


def _mcp_output_from_result(
    request: ExternalMcpCallInput,
    response: JsonRpcResponse,
) -> ExternalMcpCallOutput:
    result = response.result or {}
    raw_content = result.get("content")
    content = (
        [item for item in raw_content if isinstance(item, dict)]
        if isinstance(raw_content, list)
        else []
    )
    structured_content = result.get("structuredContent")
    if not isinstance(structured_content, dict):
        structured_content = result.get("structured_content")
    if not isinstance(structured_content, dict):
        structured_content = None
    is_error = result.get("isError")
    if not isinstance(is_error, bool):
        is_error = bool(result.get("is_error", False))
    return ExternalMcpCallOutput(
        tool_name=request.tool_name,
        content=content,
        structured_content=structured_content,
        result=result,
        is_error=is_error,
        metadata={
            "jsonrpc_id": response.id,
            "server_id": request.server_id,
            "method": "tools/call",
        },
    )


def _validation_errors(exc: ValidationError) -> list[JsonObject]:
    return [
        dict(error)
        for error in exc.errors(include_url=False, include_context=False, include_input=False)
    ]


def _mcp_oauth_bearer_token(
    *,
    token_url: str | None,
    client_id: str | None,
    client_secret: str | None,
    scope: str | None,
    timeout_seconds: float,
) -> str | None:
    if not token_url and not client_id and not client_secret:
        return None
    if not token_url or not client_id or not client_secret:
        raise ExternalToolError(
            "external_mcp.oauth_not_configured",
            "external MCP OAuth client credentials are incomplete",
            {
                "token_url_configured": bool(token_url),
                "client_id_configured": bool(client_id),
                "client_secret_configured": bool(client_secret),
            },
        )
    cache_key = _mcp_oauth_cache_key(token_url, client_id, client_secret, scope)
    cached = _mcp_oauth_token_cache.get(cache_key)
    if cached is not None:
        token, expires_at = cached
        if monotonic() < expires_at:
            return token
    token, expires_at = _fetch_mcp_oauth_bearer_token(
        token_url=token_url,
        client_id=client_id,
        client_secret=client_secret,
        scope=scope,
        timeout_seconds=timeout_seconds,
    )
    _mcp_oauth_token_cache[cache_key] = (token, expires_at)
    return token


def _mcp_oauth_cache_key(
    token_url: str,
    client_id: str,
    client_secret: str,
    scope: str | None,
) -> str:
    secret_fingerprint = hashlib.sha256(client_secret.encode("utf-8")).hexdigest()
    return "\n".join([token_url, client_id, scope or "", secret_fingerprint])


def _fetch_mcp_oauth_bearer_token(
    *,
    token_url: str,
    client_id: str,
    client_secret: str,
    scope: str | None,
    timeout_seconds: float,
) -> tuple[str, float]:
    form = {"grant_type": "client_credentials"}
    if scope:
        form["scope"] = scope
    try:
        with httpx.Client(timeout=timeout_seconds) as client:
            response = client.post(
                token_url,
                data=form,
                auth=(client_id, client_secret),
                headers={"Accept": "application/json"},
            )
            response.raise_for_status()
            data = _response_json_object(
                response,
                service_code="external_mcp",
                service_label="external MCP OAuth token endpoint",
                attempt=1,
            )
    except httpx.TimeoutException as exc:
        raise ExternalToolError(
            "external_mcp.oauth_timeout",
            "external MCP OAuth token request timed out",
            {"token_url": token_url},
        ) from exc
    except httpx.HTTPStatusError as exc:
        raise ExternalToolError(
            "external_mcp.oauth_http_error",
            f"external MCP OAuth token endpoint returned HTTP {exc.response.status_code}",
            {"status_code": exc.response.status_code, "body": _response_text(exc.response)},
        ) from exc
    except httpx.RequestError as exc:
        raise ExternalToolError(
            "external_mcp.oauth_request_error",
            "external MCP OAuth token request failed",
            {"reason": str(exc)},
        ) from exc
    except ExternalToolError:
        raise
    except ValueError as exc:
        raise ExternalToolError(
            "external_mcp.oauth_invalid_response",
            "external MCP OAuth token response is not valid JSON",
        ) from exc

    access_token = data.get("access_token")
    if not isinstance(access_token, str) or not access_token:
        raise ExternalToolError(
            "external_mcp.oauth_invalid_response",
            "external MCP OAuth token response is missing access_token",
        )
    expires_in = data.get("expires_in", 300)
    ttl_seconds = float(expires_in) if isinstance(expires_in, int | float) else 300.0
    expires_at = monotonic() + max(0.0, ttl_seconds - _MCP_OAUTH_TOKEN_SKEW_SECONDS)
    return access_token, expires_at


def _auth_headers(api_key: str | None) -> dict[str, str]:
    if not api_key:
        return {}
    return {"Authorization": f"Bearer {api_key}"}


def _mcp_headers(
    api_key: str | None,
    session_id: str | None,
    oauth_token: str | None = None,
) -> dict[str, str]:
    headers = {"Authorization": f"Bearer {oauth_token}"} if oauth_token else _auth_headers(api_key)
    if session_id:
        headers["Mcp-Session-Id"] = session_id
    return headers


def _schema(model: type[BaseModel]) -> JsonObject:
    return model.model_json_schema()


def _echo_tool(arguments: JsonObject, _context: ToolInvocationContext) -> JsonObject:
    return {"echo": arguments}


def _agent_skill_list(_arguments: JsonObject, _context: ToolInvocationContext) -> JsonObject:
    skills = skill_registry.list()
    return AgentSkillListOutput(
        skills=skills,
        metadata={"count": len(skills)},
    ).model_dump()


def _agent_skill_run(arguments: JsonObject, context: ToolInvocationContext) -> JsonObject:
    try:
        request = AgentSkillRunInput.model_validate(arguments)
    except ValidationError as exc:
        raise ExternalToolError(
            "agent_skill.invalid_request",
            "Agent skill request schema is invalid",
            {"errors": _validation_errors(exc)},
        ) from exc
    if request.trace_id is None:
        request.trace_id = context.trace_id
    try:
        return skill_registry.plan(request).model_dump()
    except KeyError as exc:
        raise ExternalToolError(
            "agent_skill.not_found",
            "Agent skill was not found",
            {"skill_id": request.skill_id},
        ) from exc
    except ValueError as exc:
        raise ExternalToolError(
            "agent_skill.disabled",
            str(exc),
            {"skill_id": request.skill_id},
        ) from exc


_SENSITIVE_KEY_PATTERN = re.compile(
    r"(password|passwd|secret|token|api[_-]?key|credential|authorization|access[_-]?key)",
    re.IGNORECASE,
)
_INLINE_SECRET_PATTERN = re.compile(
    r"\b(password|passwd|secret|token|api[_-]?key|credential)" r"(\s*[:=]\s*)([^\s,;]+)",
    re.IGNORECASE,
)
_BEARER_TOKEN_PATTERN = re.compile(r"\bBearer\s+[A-Za-z0-9._~+/=-]{8,}", re.IGNORECASE)
_EMAIL_PATTERN = re.compile(r"\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b", re.IGNORECASE)
_SSN_PATTERN = re.compile(r"\b\d{3}-\d{2}-\d{4}\b")
_CREDIT_CARD_CANDIDATE_PATTERN = re.compile(r"\b(?:\d[ -]?){13,19}\b")
_PROMPT_INJECTION_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    (
        "prompt_injection.ignore_instructions",
        re.compile(r"ignore (all )?(previous|prior) instructions", re.IGNORECASE),
    ),
    (
        "prompt_injection.system_prompt",
        re.compile(r"(system|developer) (prompt|message|instruction)", re.IGNORECASE),
    ),
    (
        "prompt_injection.tool_control",
        re.compile(r"(call|invoke|execute).{0,24}(tool|command|shell)", re.IGNORECASE),
    ),
    (
        "prompt_injection.data_exfiltration",
        re.compile(
            r"(exfiltrate|leak|dump).{0,24}(secret|token|credential|data)",
            re.IGNORECASE,
        ),
    ),
)
_NON_READONLY_SQL_PATTERN = re.compile(
    r"\b(insert|update|delete|drop|alter|truncate|merge|create|grant|revoke)\b",
    re.IGNORECASE,
)


def _guard_tool_output(
    definition: ToolDefinition,
    output: JsonObject,
) -> tuple[JsonObject, list[str]]:
    warnings: set[str] = set()
    guarded = _sanitize_value(output, warnings)
    if (
        definition.name == "external_nl2sql_query"
        and isinstance(guarded.get("sql"), str)
        and _NON_READONLY_SQL_PATTERN.search(guarded["sql"])
    ):
        warnings.add("nl2sql.non_readonly_sql_returned_as_audit_only")

    if warnings:
        metadata = guarded.get("metadata")
        if not isinstance(metadata, dict):
            metadata = {}
        metadata["agent_guardrail_warnings"] = sorted(warnings)
        guarded["metadata"] = metadata
    return guarded, sorted(warnings)


def _sanitize_value(value: Any, warnings: set[str]) -> Any:
    if isinstance(value, dict):
        sanitized: dict[str, Any] = {}
        for key, item in value.items():
            if _SENSITIVE_KEY_PATTERN.search(str(key)):
                sanitized[key] = "***MASKED***"
                warnings.add(f"sensitive_field_masked:{key}")
                continue
            sanitized[key] = _sanitize_value(item, warnings)
        return sanitized
    if isinstance(value, list):
        return [_sanitize_value(item, warnings) for item in value]
    if isinstance(value, str):
        for code, pattern in _PROMPT_INJECTION_PATTERNS:
            if pattern.search(value):
                warnings.add(code)
        return _sanitize_sensitive_text(value, warnings)
    return value


def _sanitize_sensitive_text(value: str, warnings: set[str]) -> str:
    sanitized = _INLINE_SECRET_PATTERN.sub(
        lambda match: _mask_inline_secret(match, warnings),
        value,
    )
    sanitized = _BEARER_TOKEN_PATTERN.sub(
        lambda _match: _mask_pattern(
            "***MASKED_BEARER_TOKEN***",
            warnings,
            "secret.bearer_token_masked",
        ),
        sanitized,
    )
    sanitized = _EMAIL_PATTERN.sub(
        lambda _match: _mask_pattern("***MASKED_EMAIL***", warnings, "pii.email_masked"),
        sanitized,
    )
    sanitized = _SSN_PATTERN.sub(
        lambda _match: _mask_pattern("***MASKED_SSN***", warnings, "pii.ssn_masked"),
        sanitized,
    )
    return _CREDIT_CARD_CANDIDATE_PATTERN.sub(
        lambda match: _mask_credit_card(match, warnings),
        sanitized,
    )


def _mask_inline_secret(match: re.Match[str], warnings: set[str]) -> str:
    warnings.add(f"sensitive_inline_masked:{match.group(1).lower()}")
    return f"{match.group(1)}{match.group(2)}***MASKED***"


def _mask_pattern(mask: str, warnings: set[str], warning: str) -> str:
    warnings.add(warning)
    return mask


def _mask_credit_card(match: re.Match[str], warnings: set[str]) -> str:
    value = match.group(0)
    digits = re.sub(r"\D", "", value)
    if 13 <= len(digits) <= 19 and _luhn_valid(digits):
        warnings.add("pii.credit_card_masked")
        return "***MASKED_CREDIT_CARD***"
    return value


def _luhn_valid(digits: str) -> bool:
    total = 0
    parity = len(digits) % 2
    for index, char in enumerate(digits):
        number = int(char)
        if index % 2 == parity:
            number *= 2
            if number > 9:
                number -= 9
        total += number
    return total % 10 == 0


def _external_rag_search(arguments: JsonObject, context: ToolInvocationContext) -> JsonObject:
    config = runtime_config_store.get_rag()
    if not config.base_url:
        raise ExternalToolError(
            "external_rag.not_configured",
            "external RAG service is not configured",
        )
    try:
        request = ExternalRagSearchInput.model_validate(arguments)
    except ValidationError as exc:
        raise ExternalToolError(
            "external_rag.invalid_request",
            "external RAG request schema is invalid",
            {"errors": _validation_errors(exc)},
        ) from exc
    if request.trace_id is None:
        request.trace_id = context.trace_id
    client = ExternalRagClient(
        base_url=config.base_url,
        api_key=config.api_key,
        timeout_seconds=config.timeout_seconds,
        max_retries=get_settings().agent_external_rag_max_retries,
    )
    return client.search(request).model_dump()


def _external_nl2sql_query(arguments: JsonObject, context: ToolInvocationContext) -> JsonObject:
    config = runtime_config_store.get_nl2sql()
    if not config.base_url:
        raise ExternalToolError(
            "external_nl2sql.not_configured",
            "external NL2SQL service is not configured",
        )
    try:
        request = ExternalNl2SqlInput.model_validate(arguments)
    except ValidationError as exc:
        raise ExternalToolError(
            "external_nl2sql.invalid_request",
            "external NL2SQL request schema is invalid",
            {"errors": _validation_errors(exc)},
        ) from exc
    if request.trace_id is None:
        request.trace_id = context.trace_id
    if request.limit is None:
        request.limit = config.default_limit
    client = ExternalNl2SqlClient(
        base_url=config.base_url,
        api_key=config.api_key,
        timeout_seconds=config.timeout_seconds,
        default_limit=config.default_limit,
        max_retries=get_settings().agent_external_nl2sql_max_retries,
    )
    return client.query(request).model_dump()


def _external_mcp_call(arguments: JsonObject, context: ToolInvocationContext) -> JsonObject:
    try:
        request = ExternalMcpCallInput.model_validate(arguments)
    except ValidationError as exc:
        raise ExternalToolError(
            "external_mcp.invalid_request",
            "external MCP request schema is invalid",
            {"errors": _validation_errors(exc)},
        ) from exc
    if request.trace_id is None:
        request.trace_id = context.trace_id
    return _external_mcp_client().call_tool(request).model_dump()


def _external_mcp_list_tools(arguments: JsonObject, context: ToolInvocationContext) -> JsonObject:
    try:
        request = ExternalMcpListToolsInput.model_validate(arguments)
    except ValidationError as exc:
        raise ExternalToolError(
            "external_mcp.invalid_request",
            "external MCP tools/list request schema is invalid",
            {"errors": _validation_errors(exc)},
        ) from exc
    if request.trace_id is None:
        request.trace_id = context.trace_id
    return list_external_mcp_tools(
        server_id=request.server_id,
        trace_id=request.trace_id,
    ).model_dump()


def list_external_mcp_tools(
    *,
    server_id: str | None = None,
    trace_id: str | None = None,
) -> ExternalMcpToolsData:
    return _external_mcp_client().list_tools(
        ExternalMcpListToolsInput(server_id=server_id, trace_id=trace_id)
    )


def _external_mcp_client() -> ExternalMcpClient:
    config = runtime_config_store.get_mcp()
    if not config.base_url:
        raise ExternalToolError(
            "external_mcp.not_configured",
            "external MCP gateway is not configured",
        )
    return ExternalMcpClient(
        base_url=config.base_url,
        api_key=config.api_key,
        session_id=config.session_id,
        oauth_token_url=config.oauth_token_url,
        oauth_client_id=config.oauth_client_id,
        oauth_client_secret=config.oauth_client_secret,
        oauth_scope=config.oauth_scope,
        timeout_seconds=config.timeout_seconds,
        max_retries=get_settings().agent_external_mcp_max_retries,
    )


def _sandbox_command_run(arguments: JsonObject, context: ToolInvocationContext) -> JsonObject:
    command_policy = runtime_config_store.get_command_policy()
    if not command_policy.enabled:
        raise ExternalToolError(
            "sandbox_command.disabled",
            "sandbox command tool is disabled",
        )
    try:
        request = SandboxCommandInput.model_validate(arguments)
    except ValidationError as exc:
        raise ExternalToolError(
            "sandbox_command.invalid_request",
            "sandbox command request schema is invalid",
            {"errors": _validation_errors(exc)},
        ) from exc
    if request.trace_id is None:
        request.trace_id = context.trace_id
    root = Path(command_policy.workspace_root).resolve()
    cwd = _resolve_command_cwd(root, request.cwd)
    raw_allowed_prefixes = (
        ",".join(context.command_allowed_prefixes)
        if context.command_allowed_prefixes
        else ",".join(command_policy.allowed_prefixes)
    )
    command_policy_source = "agent" if context.command_allowed_prefixes else "global"
    allowed_prefixes = _command_allowed_prefixes(raw_allowed_prefixes)
    if not _command_matches_allowed_prefix(request.command, allowed_prefixes):
        raise ExternalToolError(
            "sandbox_command.prefix_not_allowed",
            "command prefix is not allowed",
            {
                "command": request.command,
                "allowed_prefixes": allowed_prefixes,
                "command_policy_source": command_policy_source,
                "agent_id": context.agent_id,
                "trace_id": request.trace_id,
            },
        )
    timeout_seconds = min(
        request.timeout_seconds or command_policy.default_timeout_seconds,
        command_policy.max_timeout_seconds,
    )
    output_limit = min(
        request.output_limit_bytes or command_policy.output_limit_bytes,
        command_policy.output_limit_bytes,
    )
    execution_command = _sandbox_execution_command(request.command, root, cwd, command_policy)
    execution_cwd = root if command_policy.isolation_mode == "container" else cwd
    started = perf_counter()
    try:
        completed = subprocess.run(  # nosec B603
            execution_command,
            cwd=str(execution_cwd),
            env=_sandbox_command_env(command_policy),
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
            shell=False,
            check=False,
            start_new_session=command_policy.start_new_session,
            preexec_fn=_sandbox_command_preexec(command_policy, timeout_seconds),
        )
    except subprocess.TimeoutExpired as exc:
        stdout, stdout_truncated = _truncate_output(exc.stdout or "", output_limit)
        stderr, stderr_truncated = _truncate_output(exc.stderr or "", output_limit)
        return SandboxCommandOutput(
            command=request.command,
            cwd=str(cwd),
            exit_code=None,
            stdout=stdout,
            stderr=stderr,
            duration_ms=max(0, round((perf_counter() - started) * 1000)),
            timed_out=True,
            truncated=stdout_truncated or stderr_truncated,
            metadata={
                "trace_id": request.trace_id,
                "timeout_seconds": timeout_seconds,
                "workspace_root": str(root),
                "agent_id": context.agent_id,
                "command_policy_source": command_policy_source,
                "sanitized_env_enabled": command_policy.sanitized_env_enabled,
                "resource_limits": _sandbox_command_resource_limits(command_policy),
                "isolation_mode": command_policy.isolation_mode,
            },
        ).model_dump()
    stdout, stdout_truncated = _truncate_output(completed.stdout, output_limit)
    stderr, stderr_truncated = _truncate_output(completed.stderr, output_limit)
    return SandboxCommandOutput(
        command=request.command,
        cwd=str(cwd),
        exit_code=completed.returncode,
        stdout=stdout,
        stderr=stderr,
        duration_ms=max(0, round((perf_counter() - started) * 1000)),
        timed_out=False,
        truncated=stdout_truncated or stderr_truncated,
        metadata={
            "trace_id": request.trace_id,
            "timeout_seconds": timeout_seconds,
            "workspace_root": str(root),
            "agent_id": context.agent_id,
            "command_policy_source": command_policy_source,
            "sanitized_env_enabled": command_policy.sanitized_env_enabled,
            "resource_limits": _sandbox_command_resource_limits(command_policy),
            "isolation_mode": command_policy.isolation_mode,
        },
    ).model_dump()


def _sandbox_execution_command(
    command: list[str],
    root: Path,
    cwd: Path,
    command_policy: CommandPolicyRuntimeConfig,
) -> list[str]:
    isolation_mode = command_policy.isolation_mode.strip().lower()
    if isolation_mode == "process":
        return command
    if isolation_mode != "container":
        raise ExternalToolError(
            "sandbox_command.invalid_isolation_mode",
            "sandbox command isolation mode must be process or container",
            {"isolation_mode": command_policy.isolation_mode},
        )
    if not command_policy.container_image:
        raise ExternalToolError(
            "sandbox_command.container_image_required",
            "container isolation requires a container image",
        )
    relative_cwd = cwd.relative_to(root)
    container_cwd = "/workspace"
    if str(relative_cwd) != ".":
        container_cwd = f"/workspace/{relative_cwd.as_posix()}"
    docker_command = [
        "docker",
        "run",
        "--rm",
        "--network",
        command_policy.container_network or "none",
    ]
    for security_opt in command_policy.container_security_opts:
        docker_command.extend(["--security-opt", security_opt])
    if command_policy.container_userns:
        docker_command.extend(["--userns", command_policy.container_userns])
    if command_policy.container_user:
        docker_command.extend(["--user", command_policy.container_user])
    docker_command.extend(
        [
            "-v",
            f"{root}:/workspace:rw",
            "-w",
            container_cwd,
            command_policy.container_image,
            *command,
        ]
    )
    return docker_command


def _sandbox_command_env(command_policy: CommandPolicyRuntimeConfig) -> dict[str, str] | None:
    if not command_policy.sanitized_env_enabled:
        return None
    sanitized = {
        name: os.environ[name]
        for name in command_policy.env_allowlist
        if name in os.environ and name
    }
    sanitized.setdefault("PATH", "/usr/local/bin:/usr/bin:/bin")
    return sanitized


def _sandbox_command_preexec(
    command_policy: CommandPolicyRuntimeConfig,
    timeout_seconds: float,
) -> Callable[[], None]:
    def apply_limits() -> None:
        cpu_seconds = max(1, int(timeout_seconds) + 1)
        _set_resource_limit(resource.RLIMIT_CPU, cpu_seconds)
        if command_policy.max_memory_mb > 0:
            _set_resource_limit(
                resource.RLIMIT_AS,
                command_policy.max_memory_mb * 1024 * 1024,
            )
        if command_policy.max_open_files > 0:
            _set_resource_limit(resource.RLIMIT_NOFILE, command_policy.max_open_files)

    return apply_limits


def _set_resource_limit(resource_kind: int, soft_limit: int) -> None:
    try:
        _current_soft, hard_limit = resource.getrlimit(resource_kind)
        effective_soft_limit = soft_limit
        if hard_limit != resource.RLIM_INFINITY:
            effective_soft_limit = min(effective_soft_limit, hard_limit)
        resource.setrlimit(resource_kind, (effective_soft_limit, hard_limit))
    except (OSError, ValueError):
        return


def _sandbox_command_resource_limits(
    command_policy: CommandPolicyRuntimeConfig,
) -> JsonObject:
    return {
        "max_memory_mb": command_policy.max_memory_mb,
        "max_open_files": command_policy.max_open_files,
        "start_new_session": command_policy.start_new_session,
    }


def _resolve_command_cwd(root: Path, cwd: str | None) -> Path:
    if cwd is None:
        resolved = root
    elif Path(cwd).is_absolute():
        resolved = Path(cwd).resolve()
    else:
        resolved = (root / cwd).resolve()
    if not _path_is_within(resolved, root):
        raise ExternalToolError(
            "sandbox_command.cwd_outside_workspace",
            "command cwd must stay within workspace root",
            {"cwd": str(resolved), "workspace_root": str(root)},
        )
    return resolved


def _path_is_within(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
    except ValueError:
        return False
    return True


def _command_allowed_prefixes(raw_prefixes: str) -> list[list[str]]:
    prefixes: list[list[str]] = []
    for raw_prefix in raw_prefixes.split(","):
        stripped = raw_prefix.strip()
        if not stripped:
            continue
        tokens = shlex.split(stripped)
        if tokens:
            prefixes.append(tokens)
    return prefixes


def _command_matches_allowed_prefix(command: list[str], prefixes: list[list[str]]) -> bool:
    if not prefixes:
        return False
    for prefix in prefixes:
        if len(command) >= len(prefix) and command[: len(prefix)] == prefix:
            return True
    return False


def _truncate_output(value: object, limit: int) -> tuple[str, bool]:
    text = value.decode("utf-8", errors="replace") if isinstance(value, bytes) else str(value)
    encoded = text.encode("utf-8")
    if len(encoded) <= limit:
        return text, False
    truncated = encoded[:limit].decode("utf-8", errors="ignore")
    return f"{truncated}\n...[truncated]", True


class ToolsData(BaseModel):
    tools: list[str]


class ToolDefinitionsData(BaseModel):
    tools: list[ToolDefinition]


class ExternalServiceSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")

    base_url: str | None = None
    api_key_configured: bool = False
    oauth_configured: bool = False
    auth_mode: str = "none"
    session_configured: bool = False
    timeout_seconds: float
    default_limit: int | None = None
    configured: bool = False


tool_registry = ToolRegistry()
tool_registry.register(
    ToolDefinition(
        name="echo",
        description="入力をそのまま返す疎通確認用ツール。",
        input_schema={"type": "object", "additionalProperties": True},
        output_schema={"type": "object", "additionalProperties": True},
        permission_level=ToolPermissionLevel.READ,
        audit_tags=["debug"],
    ),
    _echo_tool,
)
tool_registry.register(
    ToolDefinition(
        name="agent_skill_list",
        description="Agent Runtime に登録された Skill 一覧を返す。",
        input_schema={"type": "object", "additionalProperties": False},
        output_schema=_schema(AgentSkillListOutput),
        permission_level=ToolPermissionLevel.READ,
        side_effects=False,
        timeout_seconds=1.0,
        max_retries=0,
        audit_tags=["agent", "skill", "discovery"],
    ),
    _agent_skill_list,
)
tool_registry.register(
    ToolDefinition(
        name="agent_skill_run",
        description=(
            "指定 Skill を標準 ToolCall 計画へ展開する。" "実行は Runtime の通常ステップで行う。"
        ),
        input_schema=_schema(AgentSkillRunInput),
        output_schema=_schema(AgentSkillPlanOutput),
        permission_level=ToolPermissionLevel.READ,
        side_effects=False,
        timeout_seconds=1.0,
        max_retries=0,
        audit_tags=["agent", "skill", "planner"],
    ),
    _agent_skill_run,
)
tool_registry.register(
    ToolDefinition(
        name="external_rag_search",
        description="外部業務 RAG サービスを呼び、回答・根拠・引用を取得する。",
        input_schema=_schema(ExternalRagSearchInput),
        output_schema=_schema(ExternalRagSearchOutput),
        permission_level=ToolPermissionLevel.READ,
        timeout_seconds=get_settings().agent_external_rag_timeout_seconds,
        max_retries=get_settings().agent_external_rag_max_retries,
        audit_tags=["external", "rag", "business-data"],
    ),
    _external_rag_search,
)
tool_registry.register(
    ToolDefinition(
        name="external_nl2sql_query",
        description="外部 NL2SQL/構造化データサービスを呼び、SQL と表形式結果を取得する。",
        input_schema=_schema(ExternalNl2SqlInput),
        output_schema=_schema(ExternalNl2SqlOutput),
        permission_level=ToolPermissionLevel.SENSITIVE,
        side_effects=False,
        timeout_seconds=get_settings().agent_external_nl2sql_timeout_seconds,
        max_retries=get_settings().agent_external_nl2sql_max_retries,
        audit_tags=["external", "nl2sql", "structured-data", "audit-sql"],
    ),
    _external_nl2sql_query,
)
tool_registry.register(
    ToolDefinition(
        name="external_mcp_call",
        description="外部 MCP JSON-RPC gateway 経由で MCP tool を呼び出す。",
        input_schema=_schema(ExternalMcpCallInput),
        output_schema=_schema(ExternalMcpCallOutput),
        permission_level=ToolPermissionLevel.SENSITIVE,
        side_effects=True,
        timeout_seconds=get_settings().agent_external_mcp_timeout_seconds,
        max_retries=get_settings().agent_external_mcp_max_retries,
        audit_tags=["external", "mcp", "tool-gateway"],
    ),
    _external_mcp_call,
)
tool_registry.register(
    ToolDefinition(
        name="external_mcp_list_tools",
        description="外部 MCP JSON-RPC gateway の tools/list を呼び、利用可能 tool を取得する。",
        input_schema=_schema(ExternalMcpListToolsInput),
        output_schema=_schema(ExternalMcpToolsData),
        permission_level=ToolPermissionLevel.READ,
        side_effects=False,
        timeout_seconds=get_settings().agent_external_mcp_timeout_seconds,
        max_retries=get_settings().agent_external_mcp_max_retries,
        audit_tags=["external", "mcp", "tool-discovery"],
    ),
    _external_mcp_list_tools,
)
tool_registry.register(
    ToolDefinition(
        name="sandbox_command_run",
        description="許可済み prefix のコマンドを workspace 内で shell なしに実行する。",
        input_schema=_schema(SandboxCommandInput),
        output_schema=_schema(SandboxCommandOutput),
        permission_level=ToolPermissionLevel.SENSITIVE,
        side_effects=True,
        timeout_seconds=get_settings().agent_command_default_timeout_seconds,
        max_retries=0,
        audit_tags=["command", "sandbox", "side-effect"],
    ),
    _sandbox_command_run,
)
