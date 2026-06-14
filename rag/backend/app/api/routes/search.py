"""RAG 検索 API。"""

import asyncio
import json
from collections.abc import AsyncIterator
from time import perf_counter

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse

from app.clients.oracle import OracleClient, SelectAiUnavailableError
from app.config import get_settings
from app.rag.audit import record_rag_search_audit
from app.rag.diagnostics import build_search_diagnostics
from app.rag.guardrails import GuardrailPolicy
from app.rag.observability import elapsed_ms, new_trace_id, record_rag_request
from app.rag.pipeline import RagPipeline
from app.rag.rate_limit import enforce_rate_limit
from app.schemas.common import ApiResponse
from app.schemas.search import (
    SearchRequest,
    SearchResponse,
    SelectAiAction,
    SelectAiRequest,
    SelectAiResponse,
)

router = APIRouter()
SEARCH_TIMEOUT_MESSAGE = "検索処理がタイムアウトしました。条件を絞って再度お試しください。"
SELECT_AI_BLOCKED_MESSAGE = "Select AI で実行できないクエリです。"


@router.post("", response_model=ApiResponse[SearchResponse])
async def search(
    http_request: Request,
    request: SearchRequest,
) -> ApiResponse[SearchResponse]:
    """自然言語クエリで RAG 検索を実行する。

    フロー: 埋め込み -> Oracle 26ai ベクトル検索 -> Cohere Rerank v4 fast -> LLM 回答生成。
    """
    enforce_rate_limit("search", http_request)
    result = await _run_search_with_timeout(request)
    return ApiResponse(data=result)


@router.post("/select-ai", response_model=ApiResponse[SelectAiResponse])
async def select_ai(
    http_request: Request,
    request: SelectAiRequest,
) -> ApiResponse[SelectAiResponse]:
    """Oracle Select AI で自然言語から SQL または SQL 実行結果を取得する。"""
    enforce_rate_limit("search", http_request)
    guardrail = GuardrailPolicy().validate_query(request.query)
    if not guardrail.allowed:
        raise HTTPException(status_code=400, detail=guardrail.warnings or SELECT_AI_BLOCKED_MESSAGE)
    if request.action == SelectAiAction.RUNSQL and any(
        finding.code == "sql_mutation_intent" for finding in guardrail.findings
    ):
        raise HTTPException(status_code=400, detail=SELECT_AI_BLOCKED_MESSAGE)
    try:
        result_text = await OracleClient().select_ai(
            guardrail.sanitized_text,
            action=request.action,
            profile_name=request.profile_name,
            max_result_chars=request.max_result_chars,
        )
    except SelectAiUnavailableError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    profile_name = request.profile_name or get_settings().oracle_select_ai_profile
    return ApiResponse(
        data=SelectAiResponse(
            action=request.action,
            result_text=result_text,
            generated_sql=result_text if request.action == SelectAiAction.SHOWSQL else None,
            profile_name=profile_name,
            query_chars=len(guardrail.sanitized_text),
            guardrail_warnings=guardrail.warnings,
        )
    )


@router.post("/stream")
async def stream_search(
    http_request: Request,
    request: SearchRequest,
) -> StreamingResponse:
    """RAG 検索結果を SSE 形式でストリーミングする。"""
    enforce_rate_limit("search", http_request)
    result = await _run_search_with_timeout(request)
    return StreamingResponse(
        _search_events(result),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )


async def _run_search_with_timeout(request: SearchRequest) -> SearchResponse:
    """検索 pipeline をリクエスト単位の timeout 付きで実行する。"""
    settings = get_settings()
    timeout = settings.rag_search_timeout_seconds
    started_at = perf_counter()
    trace_id = new_trace_id()
    try:
        return await asyncio.wait_for(
            RagPipeline().run(request, trace_id=trace_id), timeout=timeout
        )
    except TimeoutError as exc:
        elapsed = elapsed_ms(started_at)
        diagnostics = build_search_diagnostics(request, settings=settings)
        record_rag_request(request.mode.value, "error", elapsed / 1000, 0)
        record_rag_search_audit(
            trace_id=trace_id,
            outcome="error",
            mode=request.mode,
            sanitized_query=request.query,
            filters=request.filters,
            findings=[],
            retrieved_count=0,
            citations=[],
            elapsed_ms=elapsed,
            diagnostics=diagnostics,
            error=exc,
            error_stage="timeout",
        )
        raise HTTPException(status_code=504, detail=SEARCH_TIMEOUT_MESSAGE) from exc


async def _search_events(result: SearchResponse) -> AsyncIterator[str]:
    """SearchResponse を SSE イベント列へ変換する。"""
    yield _sse_event(
        "metadata",
        {
            "trace_id": result.trace_id,
            "elapsed_ms": result.elapsed_ms,
            "guardrail_warnings": result.guardrail_warnings,
            "diagnostics": result.diagnostics.model_dump(mode="json"),
        },
    )
    for chunk in _answer_chunks(result.answer):
        yield _sse_event("delta", {"text": chunk})
    yield _sse_event(
        "citations",
        [citation.model_dump(mode="json") for citation in result.citations],
    )
    yield _sse_event("done", {"trace_id": result.trace_id})


def _answer_chunks(answer: str, chunk_size: int = 48) -> list[str]:
    """回答を UI が扱いやすい短い delta に分割する。"""
    if not answer:
        return [""]
    return [answer[index : index + chunk_size] for index in range(0, len(answer), chunk_size)]


def _sse_event(event: str, data: object) -> str:
    """SSE イベント文字列を生成する。"""
    payload = json.dumps(data, ensure_ascii=False)
    return f"event: {event}\ndata: {payload}\n\n"
