"""Agent Runtime の統一ツール契約。

Control Plane のツール（`tool_registry`）と、MCP 接続のツール（RAG / NL2SQL / 外部 MCP。#757）を
同じポリシー・ガードレール・監査で呼ぶ。業務 RAG / NL2SQL はこのプロジェクト内で実装しない。
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Callable
from datetime import UTC, datetime
from enum import StrEnum
from time import monotonic, perf_counter
from typing import Any
from uuid import uuid4

import httpx
from pr_system_settings.auth.errors import SecurityApiError
from pr_system_settings.auth.service_token import issue_service_token
from pydantic import BaseModel, Field, ValidationError

from app.features.agent.config import McpConnectionConfig, runtime_config_store
from app.features.agent.skills import (
    AgentSkillListOutput,
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


class ExternalMcpToolInfo(BaseModel):
    name: str
    description: str = ""
    input_schema: JsonObject = Field(default_factory=dict)
    output_schema: JsonObject | None = None
    server_id: str | None = None
    # MCP の annotations.readOnlyHint。True なら既定で承認なしに呼べる（それ以外は承認が必要）。
    read_only: bool = False
    # Agent のモデルに渡す function tool の名前（`<接続>__<ツール>`）。
    function_name: str | None = None
    metadata: JsonObject = Field(default_factory=dict)


class ExternalMcpToolsData(BaseModel):
    tools: list[ExternalMcpToolInfo] = Field(default_factory=list)
    metadata: JsonObject = Field(default_factory=dict)


class JsonRpcResponse(BaseModel):
    jsonrpc: str | None = None
    id: str | int | None = None
    result: JsonObject | None = None
    error: JsonObject | None = None


class ToolInvocationContext(BaseModel):
    approval_id: str | None = None
    trace_id: str | None = None
    agent_id: str | None = None
    # Run の ID と、Run を作った利用者（共通認証の user_uuid。#233）。RAG / NL2SQL の MCP は
    # この利用者の token で呼ぶ。利用者がいない呼び出し（Binding 経由の MCP など）は
    # サービス利用者で呼ぶ。
    run_id: str | None = None
    user_uuid: str | None = None


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
        definition: ToolDefinition | None = None,
        handler: ToolHandler | None = None,
    ) -> ToolResult:
        """ツールを呼ぶ。`definition` / `handler` を渡すと、登録していないツール（MCP 接続の
        ツール。#757）も同じポリシー・ガードレール・監査で呼ぶ。"""
        started_at = _now()
        started_monotonic = perf_counter()
        active_context = context or ToolInvocationContext(trace_id=call.trace_id)
        if definition is None or handler is None:
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


MCP_PROTOCOL_VERSION = "2025-06-18"
_MCP_ACCEPT = "application/json, text/event-stream"
_MCP_METHOD_NOT_FOUND = -32601
# 再試行してよい HTTP status。LLM を使う呼び出し・書き込み（idempotent=False）は、受け付ける前に
# 断られた 429 / 503 と接続できなかったときだけ再試行する（502 / 504・timeout は処理が進んでいる
# かもしれず、再試行すると LLM の呼び出しや会話の書き込みが重複する）。
_RETRY_STATUSES = frozenset({429, 500, 502, 503, 504})
_NON_IDEMPOTENT_RETRY_STATUSES = frozenset({429, 503})


class McpSession:
    """1 回のツール呼び出しの間だけ使う MCP（Streamable HTTP の JSON 応答）の client。

    最初に `initialize` → `notifications/initialized` を送り、応答の `Mcp-Session-Id` を以降の
    request に付ける。固定の session id を設定した従来の gateway には `initialize` を送らない。

    ponytail: セッションはツール呼び出しをまたいで再利用しない（呼び出しごとに initialize する）。
    SSE の応答は最後の JSON だけを読み（途中経過の通知は捨てる）、`nextCursor` の paging はしない。
    """

    def __init__(
        self,
        *,
        url: str,
        headers: dict[str, str],
        timeout_seconds: float,
        max_retries: int,
        service_code: str,
        service_label: str,
        session_id: str | None = None,
    ) -> None:
        self._url = url
        self._headers = dict(headers)
        self._timeout_seconds = timeout_seconds
        self._max_retries = max(0, max_retries)
        self._service_code = service_code
        self._service_label = service_label
        self._session_id = session_id
        self._protocol_version = MCP_PROTOCOL_VERSION
        self._initialized = session_id is not None

    def request(
        self,
        method: str,
        params: JsonObject | None,
        *,
        request_id: str,
        idempotent: bool = True,
    ) -> JsonRpcResponse:
        if not self._initialized:
            self._initialize()
        payload: JsonObject = {"jsonrpc": "2.0", "id": request_id, "method": method}
        if params is not None:
            payload["params"] = params
        data, _headers = self._post(payload, idempotent=idempotent)
        if data is None:
            raise ExternalToolError(
                f"{self._service_code}.missing_result",
                f"{self._service_label} returned an empty response",
                {"method": method},
            )
        return _mcp_jsonrpc_response(data)

    def _initialize(self) -> None:
        self._initialized = True
        data, headers = self._post(
            {
                "jsonrpc": "2.0",
                "id": f"init_{uuid4().hex}",
                "method": "initialize",
                "params": {
                    "protocolVersion": MCP_PROTOCOL_VERSION,
                    "capabilities": {},
                    "clientInfo": {
                        "name": "production-ready-agent",
                        "version": get_settings().app_version,
                    },
                },
            },
            idempotent=True,
        )
        response = _mcp_jsonrpc_response(data or {})
        if response.error is not None:
            # initialize を持たない従来の JSON-RPC gateway は、そのまま tools/* を呼ぶ。
            if response.error.get("code") == _MCP_METHOD_NOT_FOUND:
                return
            raise ExternalToolError(
                f"{self._service_code}.initialize_failed",
                f"{self._service_label} rejected MCP initialize",
                {"error": response.error},
            )
        session_id = headers.get("mcp-session-id")
        if session_id:
            self._session_id = session_id
        version = (response.result or {}).get("protocolVersion")
        if isinstance(version, str) and version:
            self._protocol_version = version
        self._post({"jsonrpc": "2.0", "method": "notifications/initialized"}, idempotent=True)

    def _request_headers(self) -> dict[str, str]:
        headers = {
            **self._headers,
            "Accept": _MCP_ACCEPT,
            "MCP-Protocol-Version": self._protocol_version,
        }
        if self._session_id:
            headers["Mcp-Session-Id"] = self._session_id
        return headers

    def _post(self, payload: JsonObject, *, idempotent: bool) -> tuple[JsonObject | None, Any]:
        return _post_mcp_message(
            service_code=self._service_code,
            service_label=self._service_label,
            url=self._url,
            payload=payload,
            headers=self._request_headers(),
            timeout_seconds=self._timeout_seconds,
            max_retries=self._max_retries,
            idempotent=idempotent,
        )


def _post_mcp_message(
    *,
    service_code: str,
    service_label: str,
    url: str,
    payload: JsonObject,
    headers: dict[str, str],
    timeout_seconds: float,
    max_retries: int,
    idempotent: bool = True,
) -> tuple[JsonObject | None, Any]:
    """JSON-RPC の 1 メッセージを POST する。本文のない応答（通知への 202）は None。"""
    attempts = max_retries + 1
    retry_statuses = _RETRY_STATUSES if idempotent else _NON_IDEMPOTENT_RETRY_STATUSES
    last_error: ExternalToolError | None = None
    for attempt in range(1, attempts + 1):
        try:
            with httpx.Client(timeout=timeout_seconds) as client:
                response = client.post(url, json=payload, headers=headers)
                response.raise_for_status()
                response_headers = response.headers
                if response.status_code == 202 or not response.content:
                    return None, response_headers
                data = _response_json_object(
                    response,
                    service_code=service_code,
                    service_label=service_label,
                    attempt=attempt,
                )
            return data, response_headers
        except httpx.TimeoutException as exc:
            last_error = ExternalToolError(
                f"{service_code}.timeout",
                f"{service_label} request timed out",
                {"attempt": attempt, "max_retries": max_retries},
            )
            if attempt >= attempts or not idempotent:
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
            if attempt >= attempts or status_code not in retry_statuses:
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
            # 送信前の失敗（接続できない）だけは、LLM を使う呼び出しでも再試行してよい。
            if attempt >= attempts or not (idempotent or isinstance(exc, httpx.ConnectError)):
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


def _mcp_schema_value(raw_tool: JsonObject, camel_key: str, snake_key: str) -> JsonObject | None:
    value = raw_tool.get(camel_key)
    if not isinstance(value, dict):
        value = raw_tool.get(snake_key)
    return value if isinstance(value, dict) else None


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
    # NL2SQL の SQL は監査用に受け取るだけ（この Runtime では実行しない）。
    if any(
        isinstance(guarded.get(key), str) and _NON_READONLY_SQL_PATTERN.search(guarded[key])
        for key in ("generated_sql", "executable_sql")
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


# サービス利用者（AGENT_MCP_SERVICE_USER_LOGIN_ID）の login ID → user_uuid。
_service_user_uuid_cache: dict[str, str] = {}


def _mcp_subject(context: ToolInvocationContext, *, code: str) -> str:
    """token の `sub`。Run の利用者、いなければサービス利用者の user_uuid。"""
    if context.user_uuid:
        return context.user_uuid
    login_id = get_settings().agent_mcp_service_user_login_id.strip()
    if not login_id:
        raise ExternalToolError(
            f"{code}.user_required",
            "Run の利用者がいない呼び出しです。AGENT_MCP_SERVICE_USER_LOGIN_ID に"
            "サービス利用者のログインユーザー ID を設定してください。",
        )
    cached = _service_user_uuid_cache.get(login_id)
    if cached is not None:
        return cached
    # security は agent の router を import するため、ここで遅延 import する（循環を避ける）。
    from app.security.service import get_security_service

    try:
        user = get_security_service().store.get_user_by_login_user_id(login_id.casefold())
    except Exception as exc:  # noqa: BLE001 - 認証 store の障害はツールの失敗として返す
        raise ExternalToolError(
            f"{code}.service_user_unavailable",
            "サービス利用者を確認できません。共通認証のデータベースを確認してください。",
            {"reason": exc.__class__.__name__},
        ) from exc
    if user is None:
        raise ExternalToolError(
            f"{code}.service_user_not_found",
            f"サービス利用者（{login_id}）が見つかりません。ユーザー管理で作成してください。",
        )
    _service_user_uuid_cache[login_id] = user.user_uuid
    return user.user_uuid


# ---------------------------------------------------------------------------
# MCP 接続（#757）。RAG / NL2SQL / 外部 MCP を同じ client で呼ぶ。
# ---------------------------------------------------------------------------

MCP_TOOL_SEPARATOR = "__"
_FUNCTION_NAME_PATTERN = re.compile(r"[^A-Za-z0-9_-]")
# OpenAI 互換の function tool の名前の上限。
_FUNCTION_NAME_MAX = 64


def mcp_function_name(server_id: str, tool_name: str) -> str:
    """モデルに渡す function tool の名前（`<接続>__<ツール>`。英数字・`_`・`-`、64 文字以内）。"""
    raw = f"{server_id}{MCP_TOOL_SEPARATOR}{tool_name}"
    name = _FUNCTION_NAME_PATTERN.sub("_", raw)
    if len(name) <= _FUNCTION_NAME_MAX:
        return name
    digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()[:8]
    return f"{name[: _FUNCTION_NAME_MAX - 9]}_{digest}"


def mcp_base_tool_name(function_name: str) -> str:
    """`<接続>__<ツール>` のツールの部分（成果物の種類の判定に使う）。"""
    return function_name.split(MCP_TOOL_SEPARATOR, 1)[-1]


class McpConnectionClient:
    """MCP 接続の client。認証は接続の方式（サービストークン・OAuth・API キー・なし）。

    サービストークンは呼び出しごとに作る短命の token（`sub` = Run の利用者、なければ
    サービス利用者。`aud` = 接続の audience）。呼び先は `sub` の利用者の権限で実行する（#233）。
    """

    def __init__(self, config: McpConnectionConfig, *, context: ToolInvocationContext) -> None:
        self._config = config
        self._context = context

    @property
    def server_id(self) -> str:
        return self._config.server_id

    def list_tools(self, *, trace_id: str | None = None) -> ExternalMcpToolsData:
        response = self._session().request(
            "tools/list", None, request_id=trace_id or f"mcp_{uuid4().hex}"
        )
        if response.error is not None:
            raise ExternalToolError(
                "mcp.rpc_error",
                f"MCP 接続「{self._label}」が JSON-RPC のエラーを返しました。",
                {"error": response.error, "server_id": self.server_id, "method": "tools/list"},
            )
        return _mcp_tools_from_result(self.server_id, response)

    def call_tool(
        self,
        tool_name: str,
        arguments: JsonObject,
        *,
        idempotent: bool = False,
        trace_id: str | None = None,
    ) -> JsonObject:
        """ツールを呼ぶ。既定では 502 / 504・timeout で再試行しない（読み取り専用でも LLM を
        使うツール（rag_search など）があり、再試行すると処理が重複する）。"""
        response = self._session().request(
            "tools/call",
            {"name": tool_name, "arguments": arguments},
            request_id=trace_id or f"mcp_{uuid4().hex}",
            idempotent=idempotent,
        )
        details: JsonObject = {"server_id": self.server_id, "tool_name": tool_name}
        if response.error is not None:
            raise ExternalToolError(
                "mcp.rpc_error",
                f"MCP 接続「{self._label}」が JSON-RPC のエラーを返しました。",
                {**details, "error": response.error},
            )
        result = response.result or {}
        structured = result.get("structuredContent")
        content = result.get("content")
        texts = [
            str(item.get("text"))
            for item in (content if isinstance(content, list) else [])
            if isinstance(item, dict) and item.get("type") == "text" and item.get("text")
        ]
        if result.get("isError") is True:
            body = structured if isinstance(structured, dict) else {}
            message = body.get("message")
            if not isinstance(message, str) or not message:
                message = texts[0] if texts else f"ツール {tool_name} が失敗しました。"
            raise ExternalToolError(
                "mcp.tool_error",
                message,
                {
                    **details,
                    "error_code": body.get("error_code"),
                    "status": body.get("status"),
                    "details": body.get("details"),
                },
            )
        if isinstance(structured, dict):
            return structured
        return {"content": "\n".join(texts)} if texts else {"result": result}

    @property
    def _label(self) -> str:
        return self._config.label or self._config.server_id

    def _session(self) -> McpSession:
        config = self._config
        if not config.base_url:
            raise ExternalToolError(
                "mcp.not_configured",
                f"MCP 接続「{self._label}」の URL が設定されていません。",
                {"server_id": config.server_id},
            )
        headers: dict[str, str] = {}
        mode = config.effective_auth_mode()
        if mode == "service_token":
            headers["Authorization"] = f"Bearer {self._service_token()}"
        elif mode == "oauth_client_credentials":
            token = _mcp_oauth_bearer_token(
                token_url=config.oauth_token_url,
                client_id=config.oauth_client_id,
                client_secret=config.oauth_client_secret,
                scope=config.oauth_scope,
                timeout_seconds=config.timeout_seconds,
            )
            if token is None:
                raise ExternalToolError(
                    "mcp.oauth_not_configured",
                    f"MCP 接続「{self._label}」の OAuth の資格情報が足りません。",
                    {"server_id": config.server_id},
                )
            headers["Authorization"] = f"Bearer {token}"
        elif mode == "api_key":
            if not config.api_key:
                raise ExternalToolError(
                    "mcp.api_key_not_configured",
                    f"MCP 接続「{self._label}」の API キーが設定されていません。",
                    {"server_id": config.server_id},
                )
            headers["Authorization"] = f"Bearer {config.api_key}"
        return McpSession(
            url=config.base_url.rstrip("/"),
            headers=headers,
            timeout_seconds=config.timeout_seconds,
            max_retries=get_settings().agent_external_mcp_max_retries,
            service_code="mcp",
            service_label=f"MCP 接続「{self._label}」",
            session_id=config.session_id,
        )

    def _service_token(self) -> str:
        subject = _mcp_subject(self._context, code="mcp")
        claims = {"run_id": self._context.run_id, "agent_id": self._context.agent_id}
        try:
            return issue_service_token(
                get_settings().app_service_token_secret,
                subject=subject,
                audience=self._config.audience(),
                issuer="agent",
                claims={key: value for key, value in claims.items() if value},
            )
        except SecurityApiError as exc:
            raise ExternalToolError(
                "mcp.service_token_not_configured",
                exc.public_message,
                {"server_id": self._config.server_id},
            ) from exc


def _mcp_tools_from_result(server_id: str, response: JsonRpcResponse) -> ExternalMcpToolsData:
    result = response.result or {}
    raw_tools = result.get("tools")
    if not isinstance(raw_tools, list):
        raise ExternalToolError(
            "mcp.invalid_response",
            "MCP の tools/list の結果に tools[] がありません。",
            {"server_id": server_id},
        )
    tools: list[ExternalMcpToolInfo] = []
    for raw_tool in raw_tools:
        if not isinstance(raw_tool, dict):
            continue
        name = raw_tool.get("name")
        if not isinstance(name, str) or not name:
            continue
        description = raw_tool.get("description")
        annotations = raw_tool.get("annotations")
        read_only = isinstance(annotations, dict) and annotations.get("readOnlyHint") is True
        metadata = raw_tool.get("metadata")
        tools.append(
            ExternalMcpToolInfo(
                name=name,
                description=description if isinstance(description, str) else "",
                input_schema=_mcp_schema_value(raw_tool, "inputSchema", "input_schema") or {},
                output_schema=_mcp_schema_value(raw_tool, "outputSchema", "output_schema"),
                server_id=server_id,
                read_only=read_only,
                function_name=mcp_function_name(server_id, name),
                metadata=metadata if isinstance(metadata, dict) else {},
            )
        )
    next_cursor = result.get("nextCursor")
    return ExternalMcpToolsData(
        tools=tools,
        metadata={
            "server_id": server_id,
            "method": "tools/list",
            "next_cursor": next_cursor if isinstance(next_cursor, str) else None,
        },
    )


def mcp_tool_definition(config: McpConnectionConfig, tool: ExternalMcpToolInfo) -> ToolDefinition:
    """MCP 接続のツールを Runtime のツール定義にする（承認の要否は readOnlyHint とポリシー）。"""
    schema = dict(tool.input_schema) or {"type": "object", "properties": {}}
    schema.setdefault("type", "object")
    label = config.label or config.server_id
    return ToolDefinition(
        name=tool.function_name or mcp_function_name(config.server_id, tool.name),
        description=f"[{label}] {tool.description}".strip(),
        input_schema=schema,
        output_schema=tool.output_schema or {"type": "object"},
        permission_level=ToolPermissionLevel.READ if tool.read_only else ToolPermissionLevel.WRITE,
        side_effects=not tool.read_only,
        timeout_seconds=config.timeout_seconds,
        max_retries=get_settings().agent_external_mcp_max_retries,
        audit_tags=["mcp", config.server_id],
    )


def mcp_tool_handler(config: McpConnectionConfig, tool: ExternalMcpToolInfo) -> ToolHandler:
    """MCP 接続のツールを呼ぶ handler（429 / 503・接続失敗だけ再試行する）。"""

    def handle(arguments: JsonObject, context: ToolInvocationContext) -> JsonObject:
        client = McpConnectionClient(config, context=context)
        return client.call_tool(tool.name, arguments, trace_id=context.trace_id)

    return handle


def list_mcp_connection_tools(
    server_id: str, *, context: ToolInvocationContext, trace_id: str | None = None
) -> ExternalMcpToolsData:
    """MCP 接続のツール一覧（呼び先は利用者の権限で絞る）。未登録の接続は KeyError。"""
    config = runtime_config_store.get_mcp(server_id)
    return McpConnectionClient(config, context=context).list_tools(trace_id=trace_id)


class ToolsData(BaseModel):
    tools: list[str]


class ToolDefinitionsData(BaseModel):
    tools: list[ToolDefinition]


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
