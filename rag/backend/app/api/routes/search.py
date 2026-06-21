"""RAG 検索 API。"""

import asyncio
import json
from collections.abc import AsyncIterator
from contextlib import suppress
from time import perf_counter

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse

from app.clients.oracle import OracleClient, SelectAiUnavailableError
from app.config import Settings, get_settings
from app.rag.audit import record_rag_search_audit
from app.rag.business_view_config import resolve_business_view_settings
from app.rag.diagnostics import build_search_diagnostics
from app.rag.guardrails import GuardrailPolicy
from app.rag.kb_adapter_config import (
    KnowledgeBaseQueryConfig,
    apply_adapter_config_or_global,
)
from app.rag.observability import elapsed_ms, new_trace_id, record_rag_request
from app.rag.pipeline import RagPipeline, SearchStageProgress, SearchTokenDelta
from app.rag.rate_limit import enforce_rate_limit
from app.schemas.common import ApiResponse
from app.schemas.feedback import CitationFeedbackRequest, CitationFeedbackResponse
from app.schemas.search import (
    SearchRequest,
    SearchResponse,
    SelectAiAction,
    SelectAiRequest,
    SelectAiResponse,
)

router = APIRouter()
SEARCH_TIMEOUT_MESSAGE = "検索処理がタイムアウトしました。条件を絞って再度お試しください。"
STREAM_ERROR_MESSAGE = "検索処理中にエラーが発生しました。"
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


@router.post("/citation-feedback", response_model=ApiResponse[CitationFeedbackResponse])
async def submit_citation_feedback(
    http_request: Request,
    request: CitationFeedbackRequest,
) -> ApiResponse[CitationFeedbackResponse]:
    """検索結果の引用 feedback を低機密 audit table へ保存する。"""
    enforce_rate_limit("search", http_request)
    payload = request.model_dump(mode="json", exclude={"comment"})
    payload["comment_hash"] = request.comment_hash
    payload["comment_chars"] = request.comment_chars
    feedback_id = await OracleClient().save_citation_feedback(payload)
    return ApiResponse(
        data=CitationFeedbackResponse(
            feedback_id=feedback_id,
            trace_id=request.trace_id,
            document_id=request.document_id,
            chunk_id=request.chunk_id,
            rating=request.rating,
        )
    )


@router.post("/stream")
async def stream_search(
    http_request: Request,
    request: SearchRequest,
) -> StreamingResponse:
    """RAG 検索結果を SSE 形式でストリーミングする。"""
    enforce_rate_limit("search", http_request)
    return StreamingResponse(
        _stream_search_events_with_timeout(request),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )


async def _resolve_query_context(
    request: SearchRequest,
    global_settings: Settings,
) -> tuple[SearchRequest, Settings, str | None, str | None]:
    """検索の有効 request / Settings と適用済みの KB / 業務アシスタント id を返す。

    解決順は request 明示 > 業務アシスタント > (単一 KB 指定時のみ)KB > グローバル既定。
    業務アシスタント指定時は参照 KB 群を検索対象へ展開し、その query 設定・persona を適用する
    (複数 KB の query 設定競合を業務アシスタント 1 枚で解消する)。戻り値は
    (有効 request, 有効 Settings, 適用 KB id, 適用業務アシスタント id)。
    """
    if request.business_view_id:
        view = await OracleClient().get_business_view(request.business_view_id)
        if view is not None:
            effective_request = request
            kb_ids = view.config.normalized_knowledge_base_ids()
            # request 明示の KB があればそちらを優先し、無ければ参照 KB 群を展開する。
            if not request.knowledge_base_ids and kb_ids:
                effective_request = _with_knowledge_base_ids(request, kb_ids)
            # 検索対象が単一 KB に解決したら、その KB の query 既定を業務アシスタントの
            # 下層に per-field merge で重ねる(global < KB < 業務アシスタント < request)。
            kb_query, applied_kb = await _single_kb_query_overlay(
                effective_request.knowledge_base_ids
            )
            settings, applied = resolve_business_view_settings(
                global_settings, view.config, kb_query=kb_query
            )
            applied_view = view.id if (applied or kb_ids) else None
            return effective_request, settings, applied_kb, applied_view

    knowledge_base_ids = request.knowledge_base_ids
    if len(knowledge_base_ids) != 1:
        return request, global_settings, None, None
    knowledge_base = await OracleClient().get_knowledge_base(knowledge_base_ids[0])
    if knowledge_base is None:
        return request, global_settings, None, None
    effective, applied = apply_adapter_config_or_global(
        global_settings,
        knowledge_base.adapter_config,
        scope="query",
    )
    return request, effective, (knowledge_base_ids[0] if applied else None), None


async def _single_kb_query_overlay(
    knowledge_base_ids: list[str],
) -> tuple[KnowledgeBaseQueryConfig | None, str | None]:
    """検索対象が単一 KB のとき、その KB の query 上書きと適用 KB id を返す。

    複数 KB / 0 件、KB 不在、query 上書きが空のときは (None, None)。業務アシスタント
    分岐から呼ばれ、業務アシスタントの下層に重ねる KB query 既定を得る。
    """
    if len(knowledge_base_ids) != 1:
        return None, None
    knowledge_base = await OracleClient().get_knowledge_base(knowledge_base_ids[0])
    if knowledge_base is None:
        return None, None
    kb_query = knowledge_base.adapter_config.query
    if kb_query == KnowledgeBaseQueryConfig():
        return None, None
    return kb_query, knowledge_base_ids[0]


def _with_knowledge_base_ids(
    request: SearchRequest,
    knowledge_base_ids: list[str],
) -> SearchRequest:
    """業務アシスタントの参照 KB 群を検索対象へ展開した request を作る。"""
    payload = request.model_dump()
    payload["knowledge_base_ids"] = knowledge_base_ids
    filters = dict(payload.get("filters") or {})
    # validator が knowledge_base_ids から knowledge_base_id フィルターを再構成する。
    filters.pop("knowledge_base_id", None)
    payload["filters"] = filters
    return SearchRequest.model_validate(payload)


async def _run_search_with_timeout(request: SearchRequest) -> SearchResponse:
    """検索 pipeline をリクエスト単位の timeout 付きで実行する。"""
    request, settings, applied_kb, applied_view = await _resolve_query_context(
        request, get_settings()
    )
    timeout = settings.rag_search_timeout_seconds
    started_at = perf_counter()
    trace_id = new_trace_id()
    try:
        result = await asyncio.wait_for(
            RagPipeline(settings=settings).run(request, trace_id=trace_id), timeout=timeout
        )
        if applied_kb is not None:
            result.diagnostics.kb_adapter_config_applied = applied_kb
        if applied_view is not None:
            result.diagnostics.business_view_applied = applied_view
        return result
    except TimeoutError as exc:
        elapsed = elapsed_ms(started_at)
        diagnostics = build_search_diagnostics(request, settings=settings)
        if applied_kb is not None:
            diagnostics.kb_adapter_config_applied = applied_kb
        if applied_view is not None:
            diagnostics.business_view_applied = applied_view
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


async def _stream_search_events_with_timeout(request: SearchRequest) -> AsyncIterator[str]:
    """stage progress を即時 SSE で返しながら検索 pipeline を実行する。"""
    request, settings, applied_kb, applied_view = await _resolve_query_context(
        request, get_settings()
    )
    timeout = settings.rag_search_timeout_seconds
    started_at = perf_counter()
    trace_id = new_trace_id()
    queue: asyncio.Queue[tuple[str, object] | None] = asyncio.Queue()
    stage_timings: dict[str, float] = {}
    realtime_delta_sent = False

    async def emit_progress(progress: SearchStageProgress) -> None:
        if progress.outcome != "started":
            stage_timings[progress.stage] = progress.elapsed_ms
        await queue.put(
            (
                "stage",
                {
                    "trace_id": progress.trace_id,
                    "stage": progress.stage,
                    "outcome": progress.outcome,
                    "elapsed_ms": progress.elapsed_ms,
                    "attributes": dict(progress.attributes),
                },
            )
        )

    async def emit_delta(delta: SearchTokenDelta) -> None:
        nonlocal realtime_delta_sent
        if not delta.text:
            return
        realtime_delta_sent = True
        await queue.put(("delta", {"text": delta.text}))

    async def produce() -> None:
        try:
            result = await asyncio.wait_for(
                RagPipeline(settings=settings).run(
                    request,
                    trace_id=trace_id,
                    progress_callback=emit_progress,
                    token_callback=(emit_delta if settings.rag_stream_realtime_enabled else None),
                ),
                timeout=timeout,
            )
            if applied_kb is not None:
                result.diagnostics.kb_adapter_config_applied = applied_kb
            if applied_view is not None:
                result.diagnostics.business_view_applied = applied_view
            await queue.put(("result", result))
        except TimeoutError as exc:
            elapsed = elapsed_ms(started_at)
            diagnostics = build_search_diagnostics(
                request,
                settings=settings,
                stream_stage_timings=stage_timings,
            )
            if applied_kb is not None:
                diagnostics.kb_adapter_config_applied = applied_kb
            if applied_view is not None:
                diagnostics.business_view_applied = applied_view
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
            await queue.put(
                (
                    "error",
                    {
                        "trace_id": trace_id,
                        "message": SEARCH_TIMEOUT_MESSAGE,
                        "error_type": type(exc).__name__,
                    },
                )
            )
        except Exception as exc:
            await queue.put(
                (
                    "error",
                    {
                        "trace_id": trace_id,
                        "message": STREAM_ERROR_MESSAGE,
                        "error_type": type(exc).__name__,
                    },
                )
            )
        finally:
            await queue.put(None)

    producer = asyncio.create_task(produce())
    try:
        while True:
            event = await queue.get()
            if event is None:
                break
            event_name, payload = event
            if event_name == "result" and isinstance(payload, SearchResponse):
                async for item in _search_events(
                    payload,
                    include_answer_deltas=not realtime_delta_sent,
                ):
                    yield item
                continue
            yield _sse_event(event_name, payload)
    finally:
        if not producer.done():
            producer.cancel()
            with suppress(asyncio.CancelledError):
                await producer


async def _search_events(
    result: SearchResponse,
    *,
    include_answer_deltas: bool = True,
) -> AsyncIterator[str]:
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
    if include_answer_deltas:
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
