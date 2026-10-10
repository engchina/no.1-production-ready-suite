"""RAG / NL2SQL の MCP の契約どおりの fake サーバー（#233）。

`pr_backend_core.mcp.McpServer` で契約（#230〜#233）のツールを持つサーバーを作り、
`httpx.MockTransport` で Agent の `httpx.Client` の送信先にする。サービストークンは
`verify_service_token` で検証し、呼び出しごとの claims（`sub` / `aud` / `run_id` /
`agent_id` / データの範囲の `profile_ids`。#1379）を記録する。
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
    # 旧版も検索するかは専用の真偽値（#1392。filters の値は文字列だけ）。
    include_superseded: bool = False
    evidence_limit: int = Field(default=12, ge=1, le=50)
    conditions: dict[str, str] | None = None


class RagRetrieveEvidenceIn(RagSearchIn):
    # 根拠の収集は検索する件数（top_k の既定 20）と同じ件数を既定で返す（#1365）。
    evidence_limit: int = Field(default=20, ge=1, le=50)


class RagLookupGuidesIn(_ContractInput):
    query: str
    search_answer_profile_id: str
    conditions: dict[str, str] | None = None
    limit: int = Field(default=3, ge=1, le=10)


class RagReadSourceIn(_ContractInput):
    document_id: str
    # chunk_id か要素の定位子（locator。#1330）のどちらかで読む。
    chunk_id: str | None = None
    locator: str | None = Field(default=None, min_length=1, max_length=1024)
    offset: int = Field(default=0, ge=0)
    max_chars: int = Field(default=8000, ge=1, le=20000)
    # 図の根拠の元の画像（#1282）と、画面で開く短命の URL（#1311）。
    include_image: bool = False
    include_image_url: bool = False


class RagOutlineIn(_ContractInput):
    document_id: str = Field(min_length=1, max_length=128)


class RagReadDocumentIn(_ContractInput):
    # 文書を順に読む（#1332）。読み始めの位置はどれか 1 つ。
    document_id: str = Field(min_length=1, max_length=128)
    cursor: str | None = Field(default=None, min_length=1, max_length=2048)
    locator: str | None = Field(default=None, min_length=1, max_length=1024)
    page: int | None = Field(default=None, ge=1)
    section: str | None = Field(default=None, min_length=1, max_length=512)
    max_chars: int = Field(default=8000, ge=1, le=20000)


class RagEvidenceRefIn(_ContractInput):
    document_id: str = Field(min_length=1, max_length=128)
    chunk_id: str = Field(min_length=1, max_length=512)


class RagValidateRequestIn(_ContractInput):
    id: str = Field(min_length=1, max_length=64)
    text: str = Field(default="", max_length=2000)
    status: Literal["addressed", "partial", "missing", "unknown"]


class RagValidateGuideIn(_ContractInput):
    search_answer_profile_id: str = Field(min_length=1, max_length=128)
    guide_id: str = Field(min_length=1, max_length=64)
    revision: int = Field(ge=1)
    conditions: dict[str, str] | None = None


class RagValidateAnswerIn(_ContractInput):
    query: str = Field(min_length=1, max_length=8000)
    answer: str = Field(min_length=1, max_length=20000)
    evidence: list[RagEvidenceRefIn] = Field(min_length=1, max_length=30)
    # 決定的な検査の任意の入力（#1276）。
    requests: list[RagValidateRequestIn] | None = Field(default=None, max_length=30)
    gaps: list[str] | None = Field(default=None, max_length=30)
    guide: RagValidateGuideIn | None = None


class RagListSearchAnswerProfilesIn(_ContractInput):
    query: str | None = None
    limit: int = Field(default=50, ge=1, le=200)


class Nl2SqlQueryIn(_ContractInput):
    question: str = Field(min_length=1, max_length=4000)
    # 契約どおり必須（"default" に切り替えない。#1379）。
    profile_id: str = Field(min_length=1, max_length=128)
    row_limit: int = Field(default=100, ge=1, le=1000)
    wait_seconds: int = Field(default=40, ge=0, le=45)


class Nl2SqlGetJobIn(_ContractInput):
    job_id: str
    wait_seconds: int = Field(default=0, ge=0, le=45)


class Nl2SqlListProfilesIn(_ContractInput):
    query: str | None = Field(default=None, max_length=200)
    limit: int = Field(default=20, ge=1, le=100)


class Nl2SqlRecommendProfileIn(_ContractInput):
    question: str = Field(min_length=1, max_length=4000)
    profile_ids: list[str] | None = Field(default=None, min_length=1, max_length=50)


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
    "evidence_type": "text",
    "image_ref": None,
    "locator": _LOCATOR,
    "excerpt": "契約条項",
    "truncated": False,
    "text_length": 4,
    "used_in_answer": True,
    "role": "retrieved_anchor",
    "score": 0.9,
    "rerank_score": None,
}

# RAG の MCP の出力の形の版（platform/contracts/mcp/rag-tools.json の schema_version。#1276）。
RAG_OUTPUT_SCHEMA_VERSION = 4

DEFAULT_OUTPUTS: dict[str, Any] = {
    "rag_search": {
        "schema_version": RAG_OUTPUT_SCHEMA_VERSION,
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
        "schema_version": RAG_OUTPUT_SCHEMA_VERSION,
        "evidence_id": "chunk-1",
        "document_id": "doc-1",
        "chunk_id": "chunk-1",
        "file_name": "契約書.pdf",
        "chunk_set_id": "cs-1",
        "recipe_id": "recipe-1",
        "content_kind": "text",
        "evidence_type": "text",
        "image_ref": None,
        "image": None,
        "image_url": None,
        "locator": _LOCATOR,
        "text": "契約条項の全文",
        "offset": 0,
        "text_length": 7,
        "truncated": False,
        "next_offset": None,
        "parent_text": None,
        "parent_truncated": False,
    },
    "rag_outline": {
        "schema_version": RAG_OUTPUT_SCHEMA_VERSION,
        "document_id": "doc-1",
        "file_name": "契約書.pdf",
        "chunk_set_id": "cs-1",
        "chunk_count": 2,
        "page_start": 1,
        "page_end": 4,
        "sections": [
            {
                "section_path": ["契約書", "第5条（更新）"],
                "page_start": 3,
                "page_end": 3,
                "chunk_count": 1,
                "chars": 7,
                "cursor": "cursor-section-5",
            }
        ],
        "sections_omitted": 0,
        "superseded": False,
    },
    "rag_read_document": {
        "schema_version": RAG_OUTPUT_SCHEMA_VERSION,
        "document_id": "doc-1",
        "file_name": "契約書.pdf",
        "chunk_set_id": "cs-1",
        "text": "--- p.3 ---\n契約条項の全文\n更新の条件",
        "chunks": [
            {
                "chunk_id": "chunk-3",
                "element_locator": None,
                "section_path": ["契約書", "第5条（更新）"],
                "page_start": 3,
                "page_end": 3,
                "start": 12,
                "end": 19,
            },
            {
                "chunk_id": "chunk-4",
                "element_locator": None,
                "section_path": ["契約書", "第5条（更新）"],
                "page_start": 3,
                "page_end": 3,
                "start": 20,
                "end": 25,
            },
        ],
        "next_cursor": None,
        "superseded": False,
    },
    "rag_list_search_answer_profiles": {
        "schema_version": RAG_OUTPUT_SCHEMA_VERSION,
        "search_answer_profiles": [
            {
                "id": "bv-sales",
                "name": "営業の検索・回答プロファイル",
                "description": "営業部門の文書",
                "status": "ACTIVE",
                "knowledge_base_count": 2,
            }
        ],
    },
    "rag_lookup_guides": {
        "schema_version": RAG_OUTPUT_SCHEMA_VERSION,
        "guides": [
            {
                "guide_id": "guide-1",
                "revision": 1,
                "title": "契約の更新",
                "decision": "clarify",
                "known_conditions": [],
                "unknown_conditions": [{"id": "kind", "label": "契約の種類", "handling": "ask"}],
                "expected_result": "契約を更新できる",
                "score": 2,
                "clarifications": [
                    {
                        "condition_id": "kind",
                        "label": "契約の種類",
                        "question": "契約の種類は何ですか？",
                        "options": ["年間", "月額"],
                    }
                ],
                "steps": [
                    {"id": "s1", "title": "契約を開く", "depends_on": [], "allowed_tools": []}
                ],
                "impact_scope": "individual",
                "approval_required": False,
                "handoff_contact": "",
            }
        ],
    },
    "rag_retrieve_evidence": {
        "schema_version": RAG_OUTPUT_SCHEMA_VERSION,
        "trace_id": "rag-trace-2",
        "guardrail_warnings": [],
        "evidence": [{**_EVIDENCE, "used_in_answer": False}],
        "evidence_omitted": 0,
    },
    # 回答の最終の検証（#1246）。既定は根拠で裏付けられた回答。
    "rag_validate_answer": {
        "schema_version": RAG_OUTPUT_SCHEMA_VERSION,
        "valid": True,
        "status": "completed",
        "counts": {"supported": 1, "unsupported": 0, "contradicted": 0},
        "claims": [
            {
                "answer_quote": "根拠付き回答",
                "status": "supported",
                "chunk_id": "chunk-1",
                "reason": "根拠の契約条項に書かれている。",
            }
        ],
        "missing_evidence": [],
        "stale_evidence": [],
        "evidence_truncated": False,
        "checks": [],
        "findings": [],
        "guide_revision": None,
    },
    "nl2sql_query": _job(),
    "nl2sql_get_job": _job(),
    "nl2sql_list_profiles": {
        "profiles": [
            {
                "id": "profile-sales",
                "name": "売上",
                "category": "営業",
                "description": "売上の集計",
            }
        ]
    },
    "nl2sql_recommend_profile": {
        "recommended_profile_id": "profile-sales",
        "rewritten_question": None,
        "candidates": [{"id": "profile-sales", "name": "売上", "reason": None, "score": 0.9}],
    },
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
                    self._tool("rag_lookup_guides", RagLookupGuidesIn),
                    self._tool("rag_retrieve_evidence", RagRetrieveEvidenceIn),
                    self._tool("rag_outline", RagOutlineIn),
                    self._tool("rag_read_document", RagReadDocumentIn),
                    self._tool("rag_validate_answer", RagValidateAnswerIn),
                ],
            ),
            "nl2sql": McpServer(
                name="production-ready-nl2sql",
                version="test",
                tools=[
                    self._tool("nl2sql_query", Nl2SqlQueryIn, read_only=False),
                    self._tool("nl2sql_get_job", Nl2SqlGetJobIn),
                    self._tool("nl2sql_list_profiles", Nl2SqlListProfilesIn),
                    self._tool("nl2sql_recommend_profile", Nl2SqlRecommendProfileIn),
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
