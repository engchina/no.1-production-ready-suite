"""RAG / NL2SQL の MCP の契約どおりの fake サーバー（#233）。

`pr_backend_core.mcp.McpServer` で契約（#230〜#233）のツールを持つサーバーを作り、
`httpx.MockTransport` で Agent の `httpx.Client` の送信先にする。サービストークンは
`verify_service_token` で検証し、呼び出しごとの claims（`sub` / `aud` / `run_id` /
`agent_id`）を記録する。
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from dataclasses import dataclass, field
from typing import Any, Literal

import httpx
from pr_backend_core.mcp import McpServer, McpTool, McpToolError
from pr_system_settings.auth.errors import SecurityApiError
from pr_system_settings.auth.service_token import verify_service_token
from pydantic import BaseModel, ConfigDict, Field
from pytest import MonkeyPatch

from app.features.agent.config import (
    ExternalNl2SqlRuntimeConfig,
    ExternalRagRuntimeConfig,
    runtime_config_store,
)
from app.settings import get_settings

SERVICE_TOKEN_SECRET = "agent-mcp-test-secret-0123456789abcdef"  # nosec B105 - テスト用
RAG_MCP_URL = "http://rag.test/api/mcp"
NL2SQL_MCP_URL = "http://nl2sql.test/api/mcp"
# monkeypatch で httpx.Client を差し替えても、fake の中では本物の client を使う。
_REAL_HTTPX_CLIENT = httpx.Client


class _ContractInput(BaseModel):
    # 契約にない項目を Agent が送ったら検出できるよう、fake は未知の項目を拒否する。
    model_config = ConfigDict(extra="forbid")


class RagSearchIn(_ContractInput):
    query: str
    business_view_id: str | None = None
    knowledge_base_ids: list[str] | None = None
    top_k: int | None = Field(default=None, ge=1, le=100)
    filters: dict[str, str] | None = None


class RagChatIn(_ContractInput):
    content: str = Field(min_length=1, max_length=8000)
    conversation_id: str | None = None
    business_view_id: str | None = None
    title: str | None = None


class RagListBusinessViewsIn(_ContractInput):
    query: str | None = None
    limit: int = Field(default=50, ge=1, le=200)


class Nl2SqlQueryIn(_ContractInput):
    question: str = Field(min_length=1, max_length=4000)
    profile_id: str | None = None
    row_limit: int = Field(default=100, ge=1, le=1000)
    wait_seconds: int = Field(default=40, ge=0, le=45)


class Nl2SqlGetJobIn(_ContractInput):
    job_id: str
    wait_seconds: int = Field(default=0, ge=0, le=45)


def _job(status: str = "done") -> dict[str, Any]:
    return {
        "job_id": "job-1",
        "status": status,
        "profile_id": "profile-sales",
        "generated_sql": "select department, sum(amount) amount from sales group by department",
        "executable_sql": "select department, sum(amount) amount from sales group by department",
        "explanation": "部門別の売上を集計しました。",
        "is_safe": True,
        "safety_issues": [],
        "columns": ["DEPARTMENT", "AMOUNT"],
        "rows": [{"DEPARTMENT": "営業", "AMOUNT": 1200}],
        "returned_count": 1,
        "total": 1,
        "has_more": False,
        "truncated": False,
        "history_id": "history-1",
    }


_CITATION = {
    "document_id": "doc-1",
    "chunk_id": "chunk-1",
    "file_name": "契約書.pdf",
    "text": "契約条項",
    "score": 0.9,
}

DEFAULT_OUTPUTS: dict[str, Any] = {
    "rag_search": {
        "answer": "根拠付き回答",
        "trace_id": "rag-trace-1",
        "guardrail_warnings": [],
        "citations": [_CITATION],
    },
    "rag_chat_send_message": {
        "conversation_id": "conversation-1",
        "message_id": "message-1",
        "answer": "会話の回答",
        "trace_id": "rag-trace-2",
        "guardrail_warnings": [],
        "citations": [_CITATION],
    },
    "rag_list_business_views": {
        "business_views": [
            {
                "id": "bv-sales",
                "name": "営業の業務ビュー",
                "description": "営業部門の文書",
                "status": "ACTIVE",
                "knowledge_base_count": 2,
            }
        ]
    },
    "nl2sql_query": _job(),
    "nl2sql_get_job": _job(),
}

Product = Literal["rag", "nl2sql"]
ToolOutput = dict[str, Any] | Exception | Callable[[BaseModel], dict[str, Any]]


@dataclass
class FakeProductMcp:
    """契約どおりの RAG / NL2SQL の MCP サーバー（HTTP の記録つき）。"""

    outputs: dict[str, ToolOutput] = field(default_factory=lambda: deepcopy(DEFAULT_OUTPUTS))
    # 全 HTTP request（product / method / headers / body / claims）。
    requests: list[dict[str, Any]] = field(default_factory=list)
    # tools/call だけ（product / name / arguments / claims / timeout）。
    tool_calls: list[dict[str, Any]] = field(default_factory=list)
    # 次の tools/call に返す HTTP status（再試行の確認用。先頭から使う）。
    # "timeout" は読み取りの timeout。
    tool_call_statuses: list[int | Literal["timeout"]] = field(default_factory=list)
    # MCP 以外の URL（planner など）への応答と、その request（url / json / timeout）。
    other_responses: dict[str, dict[str, Any]] = field(default_factory=dict)
    other_calls: list[dict[str, Any]] = field(default_factory=list)
    _session_counter: int = 0

    def __post_init__(self) -> None:
        self.servers: dict[str, McpServer] = {
            "rag": McpServer(
                name="production-ready-rag",
                version="test",
                tools=[
                    self._tool("rag_search", RagSearchIn),
                    self._tool("rag_chat_send_message", RagChatIn, read_only=False),
                    self._tool("rag_list_business_views", RagListBusinessViewsIn),
                ],
            ),
            "nl2sql": McpServer(
                name="production-ready-nl2sql",
                version="test",
                tools=[
                    self._tool("nl2sql_query", Nl2SqlQueryIn, read_only=False),
                    self._tool("nl2sql_get_job", Nl2SqlGetJobIn),
                ],
            ),
        }

    def _tool(self, name: str, model: type[BaseModel], *, read_only: bool = True) -> McpTool:
        async def handler(argument: BaseModel) -> dict[str, Any]:
            output = self.outputs[name]
            if isinstance(output, Exception):
                raise output
            if callable(output):
                return output(argument)
            return deepcopy(output)

        return McpTool(
            name=name, description=name, input_model=model, handler=handler, read_only=read_only
        )

    def calls_of(self, name: str) -> list[dict[str, Any]]:
        return [call for call in self.tool_calls if call["name"] == name]

    def client_factory(self, timeout: float) -> httpx.Client:
        return _REAL_HTTPX_CLIENT(transport=httpx.MockTransport(self._handle), timeout=timeout)

    def _handle(self, request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        body = json.loads(request.content or b"null")
        product: Product | None = {"rag.test": "rag", "nl2sql.test": "nl2sql"}.get(request.url.host)  # type: ignore[assignment]
        if product is None:
            self.other_calls.append(
                {
                    "url": url,
                    "json": body,
                    "headers": dict(request.headers),
                    "timeout": request.extensions.get("timeout", {}).get("read"),
                }
            )
            return httpx.Response(200, json=self.other_responses[url])
        claims: dict[str, Any] | None = None
        authorization = request.headers.get("authorization", "")
        try:
            claims = verify_service_token(
                SERVICE_TOKEN_SECRET, authorization.removeprefix("Bearer "), audience=product
            )
        except SecurityApiError:
            claims = None
        method = body.get("method") if isinstance(body, dict) else None
        self.requests.append(
            {
                "product": product,
                "method": method,
                "headers": dict(request.headers),
                "body": body,
                "claims": claims,
            }
        )
        if claims is None:
            return httpx.Response(401, json={"detail": "invalid token"})
        if method == "tools/call":
            params = body.get("params") or {}
            self.tool_calls.append(
                {
                    "product": product,
                    "name": params.get("name"),
                    "arguments": params.get("arguments"),
                    "claims": claims,
                    "timeout": request.extensions.get("timeout", {}).get("read"),
                }
            )
            if self.tool_call_statuses:
                status = self.tool_call_statuses.pop(0)
                if status == "timeout":
                    raise httpx.ReadTimeout("read timeout", request=request)
                return httpx.Response(status, text="upstream error")
        response = _run_async(
            self.servers[product].handle(body, has_any_permission=lambda _codes: True)
        )
        if response is None:
            return httpx.Response(202)
        headers: dict[str, str] = {}
        if method == "initialize":
            self._session_counter += 1
            headers["Mcp-Session-Id"] = f"session-{product}-{self._session_counter}"
        return httpx.Response(200, json=response, headers=headers)


def _run_async(coroutine: Any) -> Any:
    # tool の呼び出しは event loop の中（async の route）からも来るため、別 thread で動かす。
    with ThreadPoolExecutor(max_workers=1) as executor:
        return executor.submit(asyncio.run, coroutine).result()


def fake_product_mcp(
    monkeypatch: MonkeyPatch,
    *,
    outputs: dict[str, ToolOutput] | None = None,
    rag_timeout_seconds: float = 60.0,
    nl2sql_timeout_seconds: float = 60.0,
    nl2sql_default_limit: int = 100,
    secret: str = SERVICE_TOKEN_SECRET,
) -> FakeProductMcp:
    """RAG / NL2SQL の MCP を fake にし、Agent の接続設定と署名鍵を合わせる。"""
    fake = FakeProductMcp()
    if outputs:
        fake.outputs.update(outputs)
    monkeypatch.setattr(get_settings(), "app_service_token_secret", secret)
    monkeypatch.setattr(
        runtime_config_store,
        "_rag",
        ExternalRagRuntimeConfig(mcp_url=RAG_MCP_URL, timeout_seconds=rag_timeout_seconds),
    )
    monkeypatch.setattr(
        runtime_config_store,
        "_nl2sql",
        ExternalNl2SqlRuntimeConfig(
            mcp_url=NL2SQL_MCP_URL,
            timeout_seconds=nl2sql_timeout_seconds,
            default_limit=nl2sql_default_limit,
        ),
    )
    monkeypatch.setattr("app.features.agent.tools.httpx.Client", fake.client_factory)
    return fake


__all__ = [
    "DEFAULT_OUTPUTS",
    "NL2SQL_MCP_URL",
    "RAG_MCP_URL",
    "SERVICE_TOKEN_SECRET",
    "FakeProductMcp",
    "McpToolError",
    "fake_product_mcp",
]
