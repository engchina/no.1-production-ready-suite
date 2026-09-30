"""RAG 検索 API。"""

import asyncio
import json
import logging
from collections.abc import AsyncIterator, Iterable, Sequence
from contextlib import suppress
from datetime import UTC, datetime
from time import perf_counter
from typing import Annotated

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import StreamingResponse
from pr_system_settings.auth.errors import SecurityApiError

from app.clients.oracle import CustomPromptNotConfiguredError, OracleClient
from app.config import OCI_ENTERPRISE_AI_TIMEOUT_MAX_SECONDS, Settings, get_settings
from app.rag.answer_timeout import AnswerTimeoutError, run_answer_with_timeout
from app.rag.audit import record_rag_search_audit
from app.rag.business_view_config import resolve_business_view_settings
from app.rag.business_view_knowledge import RUNTIME_KNOWLEDGE_KIND, load_domain_keywords
from app.rag.diagnostics import build_search_diagnostics
from app.rag.docrag_answer import evaluate_answer_record
from app.rag.extraction_field_adapter import load_field_schema, resolve_field_definitions
from app.rag.generation_config import (
    apply_generation_profile,
    resolve_oracle_generation_settings,
    validate_effective_generation_settings,
)
from app.rag.generation_contract import GenerationContractError
from app.rag.observability import elapsed_ms, new_trace_id, record_rag_request
from app.rag.pipeline import RagPipeline, SearchStageProgress
from app.rag.rate_limit import enforce_rate_limit
from app.rag.request_context import current_audit_request_context
from app.schemas.common import ApiResponse, Page
from app.schemas.search import (
    AnswerEvaluationRequest,
    AnswerRecordDeleteResult,
    AnswerRecordDetail,
    AnswerRecordSummary,
    SearchRequest,
    SearchResponse,
    parse_search_id_filter,
)
from app.schemas.settings import FieldDefinitionData, SearchExtractionFieldsData
from app.security.permissions import SCOPE_FORBIDDEN_CODE

router = APIRouter()
logger = logging.getLogger(__name__)
STREAM_ERROR_MESSAGE = "検索処理中にエラーが発生しました。"
# 保存済みの回答の評価（標準回答による 4 軸評価）の時間の上限（秒。#304）。
# 評価は LLM を複数回呼ぶため、回答生成の timeout（`rag_answer_timeout_seconds`）ではなく、
# LLM 1 回の timeout の設定の上限（`OCI_ENTERPRISE_AI_TIMEOUT_MAX_SECONDS` = 600 秒）を
# 評価全体の上限にする。
# 画面の timeout（frontend の `ANSWER_EVALUATION_TIMEOUT_MS` = 630 秒）と nginx の待ち時間（660 秒。
# `init_script.sh` が生成する設定）はこれより長くし、backend の 504 と理由が画面に
# 届くようにする（画面が先に諦めた後で backend が評価を保存する、を起こさない）。
ANSWER_EVALUATION_TIMEOUT_SECONDS = OCI_ENTERPRISE_AI_TIMEOUT_MAX_SECONDS
ANSWER_EVALUATION_TIMEOUT_MESSAGE = (
    "標準回答による評価が時間内に終わりませんでした。評価は保存していません。"
    "時間をおいて再度お試しください。"
)


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


@router.post("/stream")
async def stream_search(
    http_request: Request,
    request: SearchRequest,
) -> StreamingResponse:
    """RAG 検索結果を SSE 形式でストリーミングする。"""
    enforce_rate_limit("search", http_request)
    # 業務ビュー・KB の解決（404 / 403）は stream を始める前に行い、HTTP の status で返す。
    resolved = await _resolve_query_context(request, get_settings())
    return StreamingResponse(
        _stream_search_events_with_timeout(resolved),
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
    """検索の有効 request / Settings と適用済みの Business View id を返す。

    解決順は request 明示 > Business View > グローバル既定。
    業務ビュー指定時は参照 KB 群を検索対象へ展開し、その query 設定・persona を適用する。
    KB はナレッジ構築設定だけを持つため、KB query legacy 値は検索 runtime へ反映しない。
    戻り値は (有効 request, 有効 Settings, 適用 KB id, 適用 Business View id)。
    """
    oracle = OracleClient()
    settings = await resolve_oracle_generation_settings(
        global_settings,
        client=oracle,
    )
    if request.business_view_ids:
        views = []
        for business_view_id in request.business_view_ids:
            view = await oracle.get_business_view(business_view_id)
            if view is None:
                raise HTTPException(
                    status_code=404,
                    detail=f"指定した業務ビューが見つかりません: {business_view_id}",
                )
            status = getattr(view, "status", None)
            if getattr(status, "value", status) == "ARCHIVED":
                raise HTTPException(
                    status_code=409,
                    detail=f"アーカイブ済みの業務ビューは検索に使用できません: {business_view_id}",
                )
            views.append(view)
        if views:
            effective_request = request
            kb_ids = _merge_business_view_knowledge_base_ids(
                view.config.normalized_knowledge_base_ids() for view in views
            )
            # 参照 KB が 0 件の業務ビューで利用者の全 KB を検索しない（#304）。
            if not request.knowledge_base_ids:
                ensure_business_view_has_knowledge_bases(kb_ids)
            # request 明示の KB があればそちらを優先し、無ければ参照 KB 群を展開する。
            if not request.knowledge_base_ids:
                effective_request = _with_knowledge_base_ids(request, kb_ids)
            # 利用者の KB 範囲との積集合にする（request で範囲外の KB を読めないようにする）。
            effective_request = _scope_request_knowledge_bases(
                effective_request, from_business_view=not request.knowledge_base_ids
            )
            settings, applied = resolve_business_view_settings(settings, views[0].config)
            try:
                if views[0].config.query.generation_profile is not None:
                    settings = apply_generation_profile(
                        settings,
                        views[0].config.query.generation_profile,
                        source="business_view",
                    )
                if request.generation_profile is not None:
                    settings = apply_generation_profile(
                        settings,
                        request.generation_profile,
                        source="request",
                    )
                settings = validate_effective_generation_settings(settings)
            except CustomPromptNotConfiguredError as exc:
                raise HTTPException(status_code=409, detail=str(exc)) from exc
            # 業務ビューのドメインキーワードは全文検索で 1 語として優先する(複数ビューは和集合)。
            domain_keywords: list[str] = []
            for view in views:
                for keyword in await load_domain_keywords(oracle, view.id):
                    if keyword not in domain_keywords:
                        domain_keywords.append(keyword)
            if domain_keywords:
                settings = settings.model_copy(update={"rag_domain_keywords": domain_keywords})
            # 用語・ルールは DocRAG 回答エンジンだけが使う(先頭の業務ビューのもの)。
            runtime_knowledge = await oracle.get_business_view_knowledge(
                views[0].id, RUNTIME_KNOWLEDGE_KIND
            )
            if runtime_knowledge:
                settings = settings.model_copy(update={"rag_runtime_knowledge": runtime_knowledge})
            applied_view = ",".join(view.id for view in views) if (applied or kb_ids) else None
            return effective_request, settings, None, applied_view

    request = _scope_request_knowledge_bases(request)
    try:
        if request.generation_profile is not None:
            settings = apply_generation_profile(
                settings,
                request.generation_profile,
                source="request",
            )
        settings = validate_effective_generation_settings(settings)
    except CustomPromptNotConfiguredError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return request, settings, None, None


BUSINESS_VIEW_NO_KNOWLEDGE_BASES_MESSAGE = (
    "この業務ビューには参照するナレッジベースがありません。"
    "業務ビューの設定でナレッジベースを追加してください。"
)
BUSINESS_VIEW_KNOWLEDGE_BASES_FORBIDDEN_MESSAGE = (
    "この業務ビューのナレッジベースを利用する権限がありません。管理者に権限を依頼してください。"
)
REQUEST_KNOWLEDGE_BASES_FORBIDDEN_MESSAGE = (
    "指定したナレッジベースを利用する権限がありません。管理者に権限を依頼してください。"
)


def permitted_knowledge_base_ids(knowledge_base_ids: Iterable[str]) -> list[str] | None:
    """KB を、利用者が利用できる KB との積集合にする（順序は保つ）。None は制限なし。"""
    allowed = current_audit_request_context().allowed_knowledge_base_ids
    if allowed is None:
        return None
    return [item for item in knowledge_base_ids if item in allowed]


def ensure_business_view_has_knowledge_bases(knowledge_base_ids: Sequence[str]) -> None:
    """業務ビューの参照 KB が 0 件なら検索・チャットをしない（409。#304）。

    KB を絞らずに検索すると利用者が使える全 KB を検索してしまい、画面の説明（選択した業務
    ビューに紐づく KB を検索する）と合わない。理由を返し、画面はその場で表示する。
    """
    if not knowledge_base_ids:
        raise HTTPException(status_code=409, detail=BUSINESS_VIEW_NO_KNOWLEDGE_BASES_MESSAGE)


def ensure_business_view_knowledge_bases_permitted(knowledge_base_ids: Sequence[str]) -> None:
    """業務ビューの参照 KB が 1 つも利用できないなら 403（黙って 0 件にしない。#214）。"""
    permitted = permitted_knowledge_base_ids(knowledge_base_ids)
    if knowledge_base_ids and permitted is not None and not permitted:
        raise SecurityApiError(
            403, BUSINESS_VIEW_KNOWLEDGE_BASES_FORBIDDEN_MESSAGE, code=SCOPE_FORBIDDEN_CODE
        )


def _scope_request_knowledge_bases(
    request: SearchRequest, *, from_business_view: bool = False
) -> SearchRequest:
    """検索対象の KB を、利用者が利用できる KB との積集合にする（#214）。

    業務ビューの参照 KB を展開した後、または request が明示した KB に適用する。一部だけ
    許可されていれば積集合で検索を続け、積集合が空なら検索しない（403。業務ビューの KB か
    request が指定した KB かで文言を分ける）。KB を指定しない検索は、Oracle の検索条件が
    利用できる KB へ絞る。
    """
    if not request.knowledge_base_ids:
        return request
    permitted = permitted_knowledge_base_ids(request.knowledge_base_ids)
    if permitted is None or permitted == list(request.knowledge_base_ids):
        return request
    if not permitted:
        raise SecurityApiError(
            403,
            (
                BUSINESS_VIEW_KNOWLEDGE_BASES_FORBIDDEN_MESSAGE
                if from_business_view
                else REQUEST_KNOWLEDGE_BASES_FORBIDDEN_MESSAGE
            ),
            code=SCOPE_FORBIDDEN_CODE,
        )
    return _with_knowledge_base_ids(request, permitted)


def _merge_business_view_knowledge_base_ids(
    knowledge_base_id_sets: Iterable[Iterable[str]],
) -> list[str]:
    """複数業務ビューの参照 KB ID を選択順で重複排除する。"""
    seen: set[str] = set()
    merged: list[str] = []
    for knowledge_base_ids in knowledge_base_id_sets:
        for knowledge_base_id in knowledge_base_ids:
            if knowledge_base_id in seen:
                continue
            seen.add(knowledge_base_id)
            merged.append(knowledge_base_id)
    return merged


def _with_knowledge_base_ids(
    request: SearchRequest,
    knowledge_base_ids: list[str],
) -> SearchRequest:
    """業務ビューの参照 KB 群を検索対象へ展開した request を作る。"""
    payload = request.model_dump()
    payload["knowledge_base_ids"] = knowledge_base_ids
    filters = dict(payload.get("filters") or {})
    # validator が knowledge_base_ids から knowledge_base_id フィルターを再構成する。
    filters.pop("knowledge_base_id", None)
    payload["filters"] = filters
    return SearchRequest.model_validate(payload)


async def _run_search_with_timeout(request: SearchRequest) -> SearchResponse:
    """検索・回答の pipeline を回答生成の上限（`rag_answer_timeout_seconds`。#375）付きで実行する。

    pipeline は回答を LLM で生成するため、LLM を何度か呼んでも収まる回答生成の上限を使う。
    """
    request, settings, applied_kb, applied_view = await _resolve_query_context(
        request, get_settings()
    )
    started_at = perf_counter()
    trace_id = new_trace_id()
    try:
        result = await run_answer_with_timeout(
            lambda tracker: RagPipeline(settings=settings).run(
                request, trace_id=trace_id, progress_callback=tracker
            ),
            settings,
        )
        if applied_kb is not None:
            result.diagnostics.kb_adapter_config_applied = applied_kb
        if applied_view is not None:
            result.diagnostics.business_view_applied = applied_view
        return result
    except AnswerTimeoutError as exc:
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
            # 監査の error_type は従来どおり元の TimeoutError にする。
            error=exc.original_error,
            error_stage="timeout",
        )
        raise HTTPException(status_code=504, detail=exc.user_message) from exc
    except GenerationContractError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


async def _stream_search_events_with_timeout(
    resolved: tuple[SearchRequest, Settings, str | None, str | None],
) -> AsyncIterator[str]:
    """stage progress を即時 SSE で返しながら検索 pipeline を実行する。

    `resolved` は `_resolve_query_context` の結果（有効 request・Settings・適用 KB・業務ビュー）。
    """
    request, settings, applied_kb, applied_view = resolved
    started_at = perf_counter()
    trace_id = new_trace_id()
    queue: asyncio.Queue[tuple[str, object] | None] = asyncio.Queue()
    stage_timings: dict[str, float] = {}

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

    async def produce() -> None:
        try:
            # 回答を LLM で生成するため、回答生成の上限で打ち切る（#375）。
            result = await run_answer_with_timeout(
                lambda tracker: RagPipeline(settings=settings).run(
                    request,
                    trace_id=trace_id,
                    progress_callback=tracker,
                ),
                settings,
                emit_progress,
            )
            if applied_kb is not None:
                result.diagnostics.kb_adapter_config_applied = applied_kb
            if applied_view is not None:
                result.diagnostics.business_view_applied = applied_view
            await queue.put(("result", result))
        except AnswerTimeoutError as exc:
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
                error=exc.original_error,
                error_stage="timeout",
            )
            await queue.put(
                (
                    "error",
                    {
                        "trace_id": trace_id,
                        "message": exc.user_message,
                        # 画面は error_type で 504 相当に揃える
                        # （`AnswerTimeoutError` は TimeoutError の派生）。
                        "error_type": "TimeoutError",
                        "stage": exc.stage,
                    },
                )
            )
        except GenerationContractError as exc:
            await queue.put(
                (
                    "error",
                    {
                        "trace_id": trace_id,
                        "message": str(exc),
                        "error_type": type(exc).__name__,
                        "validation_codes": list(exc.codes),
                    },
                )
            )
        except Exception as exc:
            # stream は応答を始めた後なので共通の例外ハンドラーを通らない。原因を追えるよう
            # traceback をここで記録する（利用者へは秘匿した定型文だけを返す）。
            logger.exception(
                "rag_search_stream_failed",
                extra={"trace_id": trace_id, "exception_type": type(exc).__name__},
            )
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
                async for item in _search_events(payload):
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
) -> AsyncIterator[str]:
    """検証済み SearchResponse を SSE イベント列へ変換する。"""
    diagnostics = result.diagnostics.model_dump(mode="json")
    for payload, candidate in zip(
        diagnostics["retrieval_candidates"],
        result.diagnostics.retrieval_candidates,
        strict=True,
    ):
        payload["text"] = candidate.text
    yield _sse_event(
        "metadata",
        {
            "trace_id": result.trace_id,
            "elapsed_ms": result.elapsed_ms,
            "guardrail_warnings": result.guardrail_warnings,
            "diagnostics": diagnostics,
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


@router.get("/extraction-fields", response_model=ApiResponse[SearchExtractionFieldsData])
async def list_search_extraction_fields(
    business_view_ids: Annotated[str, Query(min_length=1, max_length=4000)],
) -> ApiResponse[SearchExtractionFieldsData]:
    """検索の絞り込みに使える項目を返す(#549)。

    選んだ業務ビューの参照 KB のうち利用者が使える有効な KB の定義(KB に無ければ全体の既定)の
    和集合。同じ項目名は先の KB(作成の古い順)の定義を使う。存在しない業務ビューは 404。
    """
    oracle = OracleClient()
    knowledge_base_id_sets: list[list[str]] = []
    for business_view_id in parse_search_id_filter(business_view_ids):
        view = await oracle.get_business_view(business_view_id)
        if view is None:
            raise HTTPException(
                status_code=404,
                detail=f"指定した業務ビューが見つかりません: {business_view_id}",
            )
        knowledge_base_id_sets.append(view.config.normalized_knowledge_base_ids())
    # 利用者が使えない KB とアーカイブ済みの KB は Oracle の条件で除く。
    field_sets = await oracle.list_knowledge_base_extraction_field_sets(
        _merge_business_view_knowledge_base_ids(knowledge_base_id_sets)
    )
    fields = resolve_field_definitions(field_sets, load_field_schema().fields) if field_sets else []
    return ApiResponse(
        data=SearchExtractionFieldsData(
            fields=[FieldDefinitionData.model_validate(field.model_dump()) for field in fields]
        )
    )


# チャットが会話の回答を引き当てるときに一度に渡せる trace_id の上限。
ANSWER_TRACE_ID_FILTER_MAX = 100


@router.get("/answers", response_model=ApiResponse[Page[AnswerRecordSummary]])
async def list_docrag_answers(
    business_view_id: str | None = Query(default=None, max_length=128),
    limit: int = Query(default=10, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    trace_id: Annotated[list[str] | None, Query(max_length=ANSWER_TRACE_ID_FILTER_MAX)] = None,
) -> ApiResponse[Page[AnswerRecordSummary]]:
    """保存済み DocRAG 回答を新しい順に返す(業務ビューで絞り込み可。総件数つき。#304)。

    持ち主の回答だけを返す（SYSTEM_ADMIN と `rag.feedback.manage` は全件）。`trace_id` を
    繰り返して渡すと、その回答だけにする（チャットが会話の回答の保存有無を引き当てる）。
    """
    trace_ids = _normalize_trace_id_filter(trace_id)
    oracle = OracleClient()
    rows = await oracle.list_answer_records(
        business_view_id=business_view_id, limit=limit, offset=offset, trace_ids=trace_ids
    )
    total = await oracle.count_answer_records(
        business_view_id=business_view_id, trace_ids=trace_ids
    )
    return ApiResponse(
        data=Page(
            items=[AnswerRecordSummary.model_validate(row) for row in rows],
            total=total,
            limit=limit,
            offset=offset,
            has_next=offset + len(rows) < total,
        )
    )


def _normalize_trace_id_filter(values: list[str] | None) -> list[str] | None:
    """trace_id の絞り込みを空白除去・重複排除する。長すぎる ID は 422。"""
    if values is None:
        return None
    normalized: list[str] = []
    for value in values:
        cleaned = value.strip()
        if not cleaned or len(cleaned) > 64:
            raise HTTPException(status_code=422, detail="trace_id の指定が正しくありません。")
        if cleaned not in normalized:
            normalized.append(cleaned)
    return normalized


@router.get("/answers/{trace_id}", response_model=ApiResponse[AnswerRecordDetail])
async def get_docrag_answer(trace_id: str) -> ApiResponse[AnswerRecordDetail]:
    """保存済み DocRAG 回答 1 件(回答・引用・根拠と実行記録)を返す。"""
    row = await OracleClient().get_answer_record(trace_id)
    if row is None:
        raise HTTPException(status_code=404, detail="回答が見つかりません。")
    return ApiResponse(data=_answer_record_detail(row))


def _answer_record_detail(row: dict[str, object]) -> AnswerRecordDetail:
    return AnswerRecordDetail.model_validate(
        {
            **row,
            "citations": row.get("citations_json") or [],
            "docrag": row.get("diagnostics_json") or {},
            "evaluation_available": bool(row.get("evaluation_input_json")),
            "evaluation": row.get("evaluation_json") or None,
        }
    )


@router.post("/answers/{trace_id}/evaluation", response_model=ApiResponse[AnswerRecordDetail])
async def evaluate_docrag_answer(
    http_request: Request, trace_id: str, body: AnswerEvaluationRequest
) -> ApiResponse[AnswerRecordDetail]:
    """保存済み DocRAG 回答を標準回答で評価し(rag_poc の 4 軸評価)、結果を保存して返す。

    評価は LLM を複数回呼ぶ。失敗しても例外にせず、status=error の評価として保存する
    (rag_poc と同じく部分評価は採用しない)。
    """
    enforce_rate_limit("search", http_request)
    oracle = OracleClient()
    row = await oracle.get_answer_record(trace_id)
    if row is None:
        raise HTTPException(status_code=404, detail="回答が見つかりません。")
    evaluation_input = row.get("evaluation_input_json")
    if not isinstance(evaluation_input, dict) or not evaluation_input:
        raise HTTPException(
            status_code=409,
            detail="この回答には評価に必要な記録がありません。もう一度回答を生成してから評価してください。",
        )
    try:
        # worker thread の評価は止められないため、時間切れのときは結果を捨てて保存しない。
        evaluation = await asyncio.wait_for(
            asyncio.to_thread(
                evaluate_answer_record, evaluation_input, body.standard_answer, get_settings()
            ),
            timeout=ANSWER_EVALUATION_TIMEOUT_SECONDS,
        )
    except TimeoutError as exc:
        raise HTTPException(status_code=504, detail=ANSWER_EVALUATION_TIMEOUT_MESSAGE) from exc
    saved = {
        **evaluation,
        "standard_answer": body.standard_answer,
        "evaluated_at": datetime.now(UTC).isoformat(),
    }
    if not await oracle.save_answer_evaluation(trace_id, saved):
        raise HTTPException(status_code=404, detail="回答が見つかりません。")
    return ApiResponse(data=_answer_record_detail({**row, "evaluation_json": saved}))


@router.delete("/answers/{trace_id}", response_model=ApiResponse[AnswerRecordDeleteResult])
async def delete_docrag_answer(trace_id: str) -> ApiResponse[AnswerRecordDeleteResult]:
    """保存済み DocRAG 回答を 1 件削除する。"""
    if not await OracleClient().delete_answer_record(trace_id):
        raise HTTPException(status_code=404, detail="回答が見つかりません。")
    return ApiResponse(data=AnswerRecordDeleteResult(trace_id=trace_id))
