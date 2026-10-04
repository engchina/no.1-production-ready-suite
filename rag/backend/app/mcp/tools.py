"""RAG が Agent に公開する MCP ツール（#232）。

入口は `POST /api/mcp`（`app.api.routes.mcp`）。利用者はサービストークンの `sub`（Agent の Run の
利用者）で、権限・検索・回答プロファイル / ナレッ
ジベースの対象範囲は画面と同じ判定を使う。各ツールは

既存の route と同じ関数（rate limit・timeout・範囲の判定を含む）を呼ぶだけにする。

- `rag_list_search_answer_profiles`（検索・回答プロファイル一覧の route と同じ権限）:
  `search_answer_profiles.list_search_answer_profiles`（ACTIVE のみ）
- `rag_search`（`menu.search`）: `search._run_search_with_timeout`

RAG のチャットは画面の機能で、MCP では提供しない（#787）。MCP で提供するのは検索だけにする。
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import HTTPException, Request
from pr_backend_core.api.validation import validation_tool_errors
from pr_backend_core.mcp import (
    TOOL_ARGUMENTS_INVALID_CODE,
    McpServer,
    McpTool,
    McpToolError,
)
from pydantic import AfterValidator, BaseModel, ConfigDict, Field, ValidationError

from app.api.routes import search as search_route
from app.api.routes import search_answer_profiles as search_answer_profiles_route
from app.config import get_settings
from app.rag.rate_limit import enforce_rate_limit
from app.schemas.search import RetrievedChunk, SearchRequest, SearchResponse
from app.schemas.search_answer_profile import SearchAnswerProfileStatus
from app.security.permissions import MENU_SEARCH, ROUTE_PERMISSIONS

MCP_SERVER_NAME = "production-ready-rag"
CITATION_TEXT_MAX_CHARS = 1000

SEARCH_ANSWER_PROFILE_READ_PERMISSIONS = ROUTE_PERMISSIONS[("GET", "/search-answer-profiles")]
SEARCH_PERMISSIONS = frozenset({MENU_SEARCH})

INSTRUCTIONS = (
    "Production Ready RAG の検索・回答のツールです。"
    "まず rag_list_search_answer_profiles で使"
    "える検索・回答プロファイルを確認し、その id を rag_search に"
    "渡してください。回答の根拠は citations にあります。"
)


def _strip_or_none(value: str | None) -> str | None:
    if value is None:
        return None
    cleaned = value.strip()
    return cleaned or None


# 前後の空白を除き、空文字は未指定として扱う文字列。
OptionalText = Annotated[str | None, AfterValidator(_strip_or_none)]


# ---- 入力 ----


class ListSearchAnswerProfilesInput(BaseModel):
    """検索・回答プロファイル一覧の条件。"""

    query: OptionalText = Field(
        default=None, max_length=200, description="名前・説明の部分一致（省略時は全件）。"
    )
    limit: int = Field(default=50, ge=1, le=200, description="返す最大件数。")


class SearchInput(BaseModel):
    """検索・回答の条件。旧 scope を含む未定義項目は拒否する。"""

    model_config = ConfigDict(extra="forbid")

    query: str = Field(..., min_length=1, max_length=8000, description="質問文。")
    search_answer_profile_id: OptionalText = Field(
        default=None,
        max_length=128,
        description="検索・回答プロファイルの id。参照するナレッジベースと検索・回答設定を使う。",
    )
    knowledge_base_ids: list[str] = Field(
        default_factory=list,
        max_length=200,
        description="検索するナレッジベースの id（省略時は検索・回答プロファイルの参照先）。",
    )
    top_k: int | None = Field(default=None, ge=1, le=100, description="検索する件数。")
    filters: dict[str, str] = Field(
        default_factory=dict, description="検索フィルター（例: category_name）。"
    )


# ---- 出力 ----


class SearchAnswerProfileItem(BaseModel):
    id: str
    name: str
    description: str | None = None
    status: str
    knowledge_base_count: int


class ListSearchAnswerProfilesOutput(BaseModel):
    search_answer_profiles: list[SearchAnswerProfileItem]


class RagCitation(BaseModel):
    document_id: str
    chunk_id: str
    file_name: str | None = None
    text: str = Field(description=f"根拠の本文（先頭 {CITATION_TEXT_MAX_CHARS} 文字）。")
    score: float | None = None


class SearchOutput(BaseModel):
    answer: str
    trace_id: str
    guardrail_warnings: list[str]
    citations: list[RagCitation]


def _citation(chunk: RetrievedChunk) -> RagCitation:
    return RagCitation(
        document_id=chunk.document_id,
        chunk_id=chunk.chunk_id,
        file_name=chunk.file_name,
        text=chunk.text[:CITATION_TEXT_MAX_CHARS],
        score=chunk.score,
    )


def _answer_fields(result: SearchResponse) -> dict[str, Any]:
    return {
        "answer": result.answer,
        "trace_id": result.trace_id,
        "guardrail_warnings": list(result.guardrail_warnings),
        "citations": [_citation(chunk) for chunk in result.citations],
    }


def _search_request(arguments: SearchInput) -> SearchRequest:
    payload: dict[str, Any] = {
        "query": arguments.query,
        "knowledge_base_ids": arguments.knowledge_base_ids,
        "filters": arguments.filters,
    }
    if arguments.search_answer_profile_id is not None:
        payload["search_answer_profile_id"] = arguments.search_answer_profile_id
    if arguments.top_k is not None:
        payload["top_k"] = arguments.top_k
    try:
        return SearchRequest.model_validate(payload)
    except ValidationError as exc:
        errors = validation_tool_errors(exc.errors(include_url=False, include_input=False))
        raise McpToolError(
            TOOL_ARGUMENTS_INVALID_CODE,
            "検索条件が正しくありません。",
            details={"errors": errors},
        ) from exc


def build_rag_mcp_server(http_request: Request) -> McpServer:
    """1 リクエスト分の MCP サーバー（rate limit に呼び出し元の request を使う）。"""

    async def list_search_answer_profiles(
        arguments: ListSearchAnswerProfilesInput,
    ) -> ListSearchAnswerProfilesOutput:
        response = await search_answer_profiles_route.list_search_answer_profiles(
            status=SearchAnswerProfileStatus.ACTIVE,
            q=arguments.query,
            limit=arguments.limit,
            offset=0,
        )
        if response.warning_messages:
            # DB 停止時の縮退（空一覧）は、Agent には「0 件」と区別できるエラーで返す。
            raise HTTPException(status_code=503, detail=response.warning_messages[0])
        page = response.data
        items = page.items if page is not None else []
        return ListSearchAnswerProfilesOutput(
            search_answer_profiles=[
                SearchAnswerProfileItem(
                    id=view.id,
                    name=view.name,
                    description=view.description,
                    status=view.status.value,
                    knowledge_base_count=view.knowledge_base_count,
                )
                for view in items
            ]
        )

    async def search(arguments: SearchInput) -> SearchOutput:
        request = _search_request(arguments)
        enforce_rate_limit("search", http_request)
        result = await search_route._run_search_with_timeout(request)
        return SearchOutput(**_answer_fields(result))

    return McpServer(
        name=MCP_SERVER_NAME,
        version=get_settings().app_version,
        instructions=INSTRUCTIONS,
        tools=[
            McpTool(
                name="rag_list_search_answer_profiles",
                description="利用できる検索・回答プロファイル（ACTIVE）の一覧を返します。",
                input_model=ListSearchAnswerProfilesInput,
                handler=list_search_answer_profiles,
                output_model=ListSearchAnswerProfilesOutput,
                permissions=(SEARCH_ANSWER_PROFILE_READ_PERMISSIONS,),
            ),
            McpTool(
                name="rag_search",
                description=(
                    "検索・回答プロファイルのナレッジベースを検索し、根拠（citations）付きの回答を返します。"
                ),
                input_model=SearchInput,
                handler=search,
                output_model=SearchOutput,
                permissions=(SEARCH_PERMISSIONS,),
            ),
        ],
    )
