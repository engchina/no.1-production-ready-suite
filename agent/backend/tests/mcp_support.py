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

from app.features.agent.config import McpConnectionConfig, runtime_config_store
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
    search_answer_profile_id: str | None = None
    knowledge_base_ids: list[str] | None = None
    top_k: int | None = Field(default=None, ge=1, le=100)
    filters: dict[str, str] | None = None
    evidence_limit: int = Field(default=12, ge=1, le=50)


class RagReadSourceIn(_ContractInput):
    document_id: str
    chunk_id: str
    offset: int = Field(default=0, ge=0)
    max_chars: int = Field(default=8000, ge=1, le=20000)


class RagListSearchAnswerProfilesIn(_ContractInput):
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


_LOCATOR = {"section_path": ["契約書", "第5条（更新）"], "page_start": 3, "page_end": 3}

# RAG の `rag_search` の根拠（platform/contracts/mcp/rag-tools.json の evidence。#1219）。
_EVIDENCE = {
    "evidence_id": "chunk-1",
    "document_id": "doc-1",
    "chunk_id": "chunk-1",
    "file_name": "契約書.pdf",
    "chunk_set_id": "cs-1",
    "recipe_id": "recipe-1",
    "content_kind": "text",
    "locator": _LOCATOR,
    "excerpt": "契約条項",
    "truncated": False,
    "text_length": 4,
    "used_in_answer": True,
    "role": "retrieved_anchor",
    "score": 0.9,
    "rerank_score": None,
}

DEFAULT_OUTPUTS: dict[str, Any] = {
    "rag_search": {
        "answer": "根拠付き回答",
        "trace_id": "rag-trace-1",
        "guardrail_warnings": [],
        "outcome": "answered",
        "requests": [{"id": "Q1", "text": "契約条項", "status": "addressed"}],
        "conditions": [],
        "gaps": [],
        "confirmations": [],
        "insufficient_reason": None,
        "needs_human_review": False,
        "evidence": [_EVIDENCE],
        "evidence_omitted": 0,
    },
    "rag_read_source": {
        "evidence_id": "chunk-1",
        "document_id": "doc-1",
        "chunk_id": "chunk-1",
        "file_name": "契約書.pdf",
        "chunk_set_id": "cs-1",
        "recipe_id": "recipe-1",
        "content_kind": "text",
        "locator": _LOCATOR,
        "text": "契約条項の全文",
        "offset": 0,
        "text_length": 7,
        "truncated": False,
        "next_offset": None,
        "parent_text": None,
        "parent_truncated": False,
    },
    "rag_list_search_answer_profiles": {
        "search_answer_profiles": [
            {
                "id": "bv-sales",
                "name": "営業の検索・回答プロファイル",
                "description": "営業部門の文書",
                "status": "ACTIVE",
                "knowledge_base_count": 2,
            }
        ]
    },
    "rag_retrieve_evidence": {
        "trace_id": "rag-trace-2",
        "guardrail_warnings": [],
        "evidence": [{**_EVIDENCE, "used_in_answer": False}],
        "evidence_omitted": 0,
    },
    "nl2sql_query": _job(),
    "nl2sql_get_job": _job(),
}

Product = Literal["rag", "nl2sql"]
ToolOutput = dict[str, Any] | Exception | Callable[[BaseModel], dict[str, Any]]


# 失敗の指定（HTTP status・読み取りの timeout・接続できない・status と header）。
Failure = int | Literal["timeout", "connect", "connect_timeout"] | tuple[int, dict[str, str]]


@dataclass
class FakeProductMcp:
    """契約どおりの RAG / NL2SQL の MCP サーバー（HTTP の記録つき）。"""

    outputs: dict[str, ToolOutput] = field(default_factory=lambda: deepcopy(DEFAULT_OUTPUTS))
    # 全 HTTP request（product / method / headers / body / claims）。
    requests: list[dict[str, Any]] = field(default_factory=list)
    # tools/call だけ（product / name / arguments / claims / timeout）。
    tool_calls: list[dict[str, Any]] = field(default_factory=list)
    # 次の tools/call に返す HTTP status（再試行の確認用。先頭から使う。呼び先に届いた後の失敗）。
    # "timeout" は読み取りの timeout、(status, headers) は応答の header 付き（Retry-After など）。
    tool_call_statuses: list[Failure] = field(default_factory=list)
    # メソッドごとの失敗（先頭から使う。#854）。呼び先に届く前に判定し、request を記録しない。
    # "connect" / "connect_timeout" は接続できない（送信前の失敗）。
    method_failures: dict[str, list[Failure]] = field(default_factory=dict)
    # MCP 以外の URL（外部 MCP など）への応答と、その request（url / json / timeout）。
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
                    self._tool("rag_list_search_answer_profiles", RagListSearchAnswerProfilesIn),
                    self._tool("rag_read_source", RagReadSourceIn),
                    self._tool("rag_retrieve_evidence", RagSearchIn),
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
        failures = self.method_failures.get(str(method), [])
        if failures:
            return _failure_response(failures.pop(0), request)
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
                return _failure_response(self.tool_call_statuses.pop(0), request)
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


def _failure_response(failure: Failure, request: httpx.Request) -> httpx.Response:
    if failure == "timeout":
        raise httpx.ReadTimeout("read timeout", request=request)
    if failure == "connect":
        raise httpx.ConnectError("connection refused", request=request)
    if failure == "connect_timeout":
        raise httpx.ConnectTimeout("connect timeout", request=request)
    if isinstance(failure, tuple):
        status, headers = failure
        return httpx.Response(status, text="upstream error", headers=headers)
    return httpx.Response(failure, text="upstream error")


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
    secret: str = SERVICE_TOKEN_SECRET,
) -> FakeProductMcp:
    """RAG / NL2SQL の MCP を fake にし、MCP 接続（`rag` / `nl2sql`）の URL と署名鍵を合わせる。"""
    fake = FakeProductMcp()
    if outputs:
        fake.outputs.update(outputs)
    monkeypatch.setattr(get_settings(), "app_service_token_secret", secret)
    connections: dict[str, McpConnectionConfig] = {
        config.server_id: config for config in runtime_config_store.list_mcp_servers()
    }
    connections["rag"] = connections["rag"].model_copy(
        update={"base_url": RAG_MCP_URL, "timeout_seconds": rag_timeout_seconds}
    )
    connections["nl2sql"] = connections["nl2sql"].model_copy(
        update={"base_url": NL2SQL_MCP_URL, "timeout_seconds": nl2sql_timeout_seconds}
    )
    monkeypatch.setattr(runtime_config_store, "_mcp_servers", connections)
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
