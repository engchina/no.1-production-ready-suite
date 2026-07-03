"""チャット(会話 / マルチモデル比較)API。

会話は業務ビュー(Business View)配下に置く。メッセージ送信は既存 RAG パイプラインを
再利用し、会話履歴を生成プロンプトへ前置する(検索は最新メッセージのみで実行)。
``model_ids`` を複数指定すると設定済み OCI モデルへ fan-out し横並び比較できる。
"""

import asyncio
from collections.abc import AsyncIterator
from contextlib import suppress
from datetime import UTC, datetime
from uuid import uuid4

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import StreamingResponse

from app.api.routes.search import (
    STREAM_ERROR_MESSAGE,
    _answer_chunks,
    _resolve_query_context,
    _sse_event,
)
from app.clients.oci_enterprise_ai import OciEnterpriseAiClient
from app.clients.oracle import OracleClient, StoredConversation, StoredMessage
from app.config import (
    Settings,
    enterprise_ai_default_model_id,
    enterprise_ai_model_catalog,
    get_settings,
)
from app.db_degradation import load_or_degrade
from app.rag.generation_contract import GenerationContractError
from app.rag.guardrails import GuardrailPolicy
from app.rag.observability import new_trace_id
from app.rag.pipeline import ChatTurn, RagPipeline, SearchStageProgress
from app.rag.rate_limit import enforce_rate_limit
from app.schemas.chat import (
    ChatMessage,
    ChatMessageRequest,
    ConversationCreateRequest,
    ConversationDetail,
    ConversationStatus,
    ConversationSummary,
    ConversationUpdateRequest,
    MessageRole,
    MessageStatus,
)
from app.schemas.common import ApiResponse, Page
from app.schemas.search import RetrievedChunk, SearchRequest

router = APIRouter()

CHAT_DISABLED_MESSAGE = "チャット機能は現在無効です。"
CONVERSATION_NOT_FOUND_MESSAGE = "会話が見つかりません。"
BUSINESS_VIEW_NOT_FOUND_MESSAGE = "業務ビューが見つかりません。"
HISTORY_PROMPT_LIMIT = 40
BLOCKED_MESSAGE_PLACEHOLDER = "安全ポリシーにより内容を保存しませんでした。"


def _require_chat_enabled(settings: Settings) -> None:
    """チャット無効時は 404 にする(運用キルスイッチ)。"""
    if not settings.rag_chat_enabled:
        raise HTTPException(status_code=404, detail=CHAT_DISABLED_MESSAGE)


def _business_view_is_archived(view: object) -> bool:
    """テスト fake を含む業務ビューの status を寛容に判定する。"""
    status = getattr(view, "status", None)
    return getattr(status, "value", status) == "ARCHIVED"


def _to_conversation_summary(conversation: StoredConversation) -> ConversationSummary:
    return ConversationSummary(
        id=conversation.id,
        business_view_id=conversation.business_view_id,
        title=conversation.title,
        status=ConversationStatus(conversation.status),
        message_count=conversation.message_count,
        created_at=conversation.created_at,
        updated_at=conversation.updated_at,
    )


def _to_chat_message(message: StoredMessage) -> ChatMessage:
    citations: list[RetrievedChunk] = []
    for raw in message.citations:
        with suppress(Exception):
            citations.append(RetrievedChunk.model_validate(raw))
    return ChatMessage(
        message_id=message.id,
        conversation_id=message.conversation_id,
        role=MessageRole(message.role),
        content=message.content,
        model=message.model,
        citations=citations,
        guardrail_warnings=message.guardrail_warnings,
        trace_id=message.trace_id,
        status=MessageStatus(message.status),
        reply_to_message_id=message.reply_to_message_id,
        created_at=message.created_at,
    )


@router.get("/models", response_model=ApiResponse[list[dict[str, str]]])
async def list_compare_models() -> ApiResponse[list[dict[str, str]]]:
    """マルチモデル比較で選べる設定済み OCI モデルを返す(先頭が既定モデル)。"""
    settings = get_settings()
    _require_chat_enabled(settings)
    models = [
        {"model_id": model.model_id, "display_name": model.display_name or model.model_id}
        for model in enterprise_ai_model_catalog(settings)
        if model.model_id
    ]
    return ApiResponse(
        data=models,
        warning_messages=(
            [] if models else ["生成モデルが未設定です。システム設定 > モデルで登録してください。"]
        ),
    )


@router.get("/conversations", response_model=ApiResponse[Page[ConversationSummary]])
async def list_conversations(
    business_view_id: str | None = Query(default=None, max_length=128),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
) -> ApiResponse[Page[ConversationSummary]]:
    """会話一覧を返す。DB 停止時は空一覧 + warning で縮退する。"""
    settings = get_settings()
    _require_chat_enabled(settings)
    oracle = OracleClient()

    async def _load() -> Page[ConversationSummary]:
        items = await oracle.list_conversations(
            business_view_id=business_view_id, limit=limit, offset=offset
        )
        total = await oracle.count_conversations(business_view_id=business_view_id)
        return Page(
            items=[_to_conversation_summary(item) for item in items],
            total=total,
            limit=limit,
            offset=offset,
            has_next=offset + limit < total,
        )

    empty_page: Page[ConversationSummary] = Page(
        items=[], total=0, limit=limit, offset=offset, has_next=False
    )
    page, degraded = await load_or_degrade(
        _load,
        timeout_seconds=settings.db_read_timeout_seconds,
        fallback=empty_page,
        log_label="conversations_list",
    )
    return ApiResponse(data=page, warning_messages=[degraded.message] if degraded else [])


@router.post("/conversations", response_model=ApiResponse[ConversationDetail])
async def create_conversation(
    request: ConversationCreateRequest,
) -> ApiResponse[ConversationDetail]:
    """業務ビュー配下に会話を作成する。"""
    settings = get_settings()
    _require_chat_enabled(settings)
    oracle = OracleClient()
    view = await oracle.get_business_view(request.business_view_id)
    if view is None:
        raise HTTPException(status_code=404, detail=BUSINESS_VIEW_NOT_FOUND_MESSAGE)
    if _business_view_is_archived(view):
        raise HTTPException(
            status_code=409,
            detail="アーカイブ済みの業務ビューでは会話を作成できません。",
        )
    conversation = await oracle.create_conversation(
        business_view_id=request.business_view_id, title=request.title
    )
    detail = ConversationDetail(**_to_conversation_summary(conversation).model_dump(), messages=[])
    return ApiResponse(data=detail)


@router.get("/conversations/{conversation_id}", response_model=ApiResponse[ConversationDetail])
async def get_conversation(conversation_id: str) -> ApiResponse[ConversationDetail]:
    """会話詳細とメッセージ列を返す。"""
    settings = get_settings()
    _require_chat_enabled(settings)
    oracle = OracleClient()
    conversation = await oracle.get_conversation(conversation_id)
    if conversation is None:
        raise HTTPException(status_code=404, detail=CONVERSATION_NOT_FOUND_MESSAGE)
    messages = await oracle.list_messages(conversation_id)
    detail = ConversationDetail(
        **_to_conversation_summary(conversation).model_dump(),
        messages=[_to_chat_message(message) for message in messages],
    )
    return ApiResponse(data=detail)


@router.patch("/conversations/{conversation_id}", response_model=ApiResponse[ConversationSummary])
async def rename_conversation(
    conversation_id: str,
    request: ConversationUpdateRequest,
) -> ApiResponse[ConversationSummary]:
    """会話タイトルを変更する。"""
    settings = get_settings()
    _require_chat_enabled(settings)
    try:
        conversation = await OracleClient().rename_conversation(conversation_id, request.title)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=CONVERSATION_NOT_FOUND_MESSAGE) from exc
    return ApiResponse(data=_to_conversation_summary(conversation))


@router.post(
    "/conversations/{conversation_id}/archive", response_model=ApiResponse[ConversationSummary]
)
async def archive_conversation(conversation_id: str) -> ApiResponse[ConversationSummary]:
    """会話をアーカイブする。"""
    settings = get_settings()
    _require_chat_enabled(settings)
    try:
        conversation = await OracleClient().archive_conversation(conversation_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=CONVERSATION_NOT_FOUND_MESSAGE) from exc
    return ApiResponse(data=_to_conversation_summary(conversation))


@router.post("/conversations/{conversation_id}/messages/stream")
async def stream_message(
    conversation_id: str,
    request: ChatMessageRequest,
    http_request: Request,
) -> StreamingResponse:
    """メッセージを送信し、回答(マルチモデルは N 系統)を SSE でストリーミングする。"""
    settings = get_settings()
    _require_chat_enabled(settings)
    enforce_rate_limit("search", http_request)
    oracle = OracleClient()
    conversation = await oracle.get_conversation(conversation_id)
    if conversation is None:
        raise HTTPException(status_code=404, detail=CONVERSATION_NOT_FOUND_MESSAGE)
    if conversation.status != "ACTIVE":
        raise HTTPException(status_code=409, detail="アーカイブ済みの会話には送信できません。")
    view = await oracle.get_business_view(conversation.business_view_id)
    if view is None:
        raise HTTPException(status_code=404, detail=BUSINESS_VIEW_NOT_FOUND_MESSAGE)
    if _business_view_is_archived(view):
        raise HTTPException(
            status_code=409,
            detail="アーカイブ済みの業務ビューではチャットできません。",
        )
    return StreamingResponse(
        _stream_chat_events(conversation_id, conversation.business_view_id, request, settings),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


def _resolve_compare_models(
    request: ChatMessageRequest, settings: Settings
) -> list[dict[str, str]]:
    """比較対象の OCI モデル(model_id + label)を解決する。上限は設定値で抑える。"""
    catalog = {
        model.model_id: (model.display_name or model.model_id)
        for model in enterprise_ai_model_catalog(settings)
        if model.model_id
    }
    default_model = enterprise_ai_default_model_id(settings)
    if request.model_ids:
        selected = [
            {"model_id": model_id, "label": catalog.get(model_id, model_id)}
            for model_id in request.model_ids
            if model_id in catalog
        ]
    else:
        selected = []
    if not selected:
        label = catalog.get(default_model, default_model) or "既定モデル"
        selected = [{"model_id": default_model, "label": label}]
    return selected[: settings.rag_chat_max_compare_models]


async def _build_safe_history(
    messages: list[StoredMessage], guardrails: GuardrailPolicy
) -> list[ChatTurn]:
    """ブロック済みターンを除外し、旧メッセージも再検査して生成履歴を作る。"""
    turns: list[ChatTurn] = []
    seen_replies: set[str] = set()
    allowed_user_ids: set[str] = set()
    blocked_user_ids: set[str] = set()
    for message in messages[-HISTORY_PROMPT_LIMIT * 3 :]:
        if message.role == "USER":
            if message.status != "COMPLETE":
                blocked_user_ids.add(message.id)
                continue
            result = await asyncio.to_thread(guardrails.validate_query, message.content)
            if not result.allowed:
                blocked_user_ids.add(message.id)
                continue
            allowed_user_ids.add(message.id)
            turns.append(ChatTurn(role="USER", content=result.sanitized_text))
        elif message.role == "ASSISTANT":
            if message.status != "COMPLETE":
                continue
            if message.reply_to_message_id in blocked_user_ids:
                continue
            if message.reply_to_message_id and message.reply_to_message_id not in allowed_user_ids:
                continue
            # 同一ユーザーターンに複数モデルの回答がある場合は先頭だけを履歴に使う。
            key = message.reply_to_message_id or message.id
            if key in seen_replies:
                continue
            result = await asyncio.to_thread(guardrails.validate_answer, message.content)
            if not result.allowed:
                continue
            seen_replies.add(key)
            turns.append(ChatTurn(role="ASSISTANT", content=result.sanitized_text))
    return turns[-HISTORY_PROMPT_LIMIT:]


async def _stream_chat_events(
    conversation_id: str,
    business_view_id: str,
    request: ChatMessageRequest,
    settings: Settings,
) -> AsyncIterator[str]:
    """USER 永続化 → 各モデルへ fan-out 生成 → ASSISTANT 永続化 を SSE で流す。"""
    oracle = OracleClient()
    base_request = SearchRequest(
        query=request.content,
        mode=request.mode,
        top_k=request.top_k,
        business_view_ids=[business_view_id],
    )
    effective_request, effective_settings, _applied_kb, _applied_view = (
        await _resolve_query_context(base_request, settings)
    )
    guardrails = GuardrailPolicy(effective_settings)
    query_guardrail = await asyncio.to_thread(guardrails.validate_query, request.content)
    # 履歴は今回のユーザー発話を保存する前に読む(自分自身を含めない)。
    prior_messages = await oracle.list_messages(conversation_id)
    history = await _build_safe_history(prior_messages, guardrails)
    now = datetime.now(UTC)
    user_message = await oracle.append_message(
        StoredMessage(
            id=uuid4().hex,
            conversation_id=conversation_id,
            role="USER",
            content=(
                query_guardrail.sanitized_text
                if query_guardrail.allowed
                else BLOCKED_MESSAGE_PLACEHOLDER
            ),
            guardrail_warnings=query_guardrail.warnings,
            status="COMPLETE" if query_guardrail.allowed else "ERROR",
            created_at=now,
        )
    )

    columns = _resolve_compare_models(request, effective_settings)
    timeout = effective_settings.rag_search_timeout_seconds
    queue: asyncio.Queue[tuple[str, object] | None] = asyncio.Queue()

    async def run_model(column: dict[str, str]) -> None:
        model_id = column["model_id"]
        trace_id = new_trace_id()

        async def emit_progress(progress: SearchStageProgress) -> None:
            await queue.put(
                (
                    "stage",
                    {
                        "model_id": model_id,
                        "trace_id": progress.trace_id,
                        "stage": progress.stage,
                        "outcome": progress.outcome,
                        "elapsed_ms": progress.elapsed_ms,
                    },
                )
            )

        try:
            llm = OciEnterpriseAiClient(settings=effective_settings, model_id=model_id or None)
            pipeline = RagPipeline(settings=effective_settings, llm=llm, guardrails=guardrails)
            result = await asyncio.wait_for(
                pipeline.run(
                    effective_request,
                    trace_id=trace_id,
                    progress_callback=emit_progress,
                    history=history,
                    query_guardrail_result=query_guardrail,
                ),
                timeout=timeout,
            )
            assistant = await oracle.append_message(
                StoredMessage(
                    id=uuid4().hex,
                    conversation_id=conversation_id,
                    reply_to_message_id=user_message.id,
                    role="ASSISTANT",
                    model=model_id or None,
                    content=result.answer,
                    citations=[citation.model_dump(mode="json") for citation in result.citations],
                    guardrail_warnings=result.guardrail_warnings,
                    trace_id=result.trace_id,
                    status="COMPLETE",
                    elapsed_ms=result.elapsed_ms,
                    created_at=datetime.now(UTC),
                )
            )
            await queue.put(
                (
                    "result",
                    {
                        "model_id": model_id,
                        "message_id": assistant.id,
                        "trace_id": result.trace_id,
                        "answer": result.answer,
                        "guardrail_warnings": result.guardrail_warnings,
                        "elapsed_ms": result.elapsed_ms,
                        "citations": [
                            citation.model_dump(mode="json") for citation in result.citations
                        ],
                    },
                )
            )
        except Exception as exc:
            # SSE では例外を error event へ落とし、ストリームを正常終了させる。
            if isinstance(exc, TimeoutError):
                message = "回答生成がタイムアウトしました。"
            elif isinstance(exc, GenerationContractError):
                message = str(exc)
            else:
                message = STREAM_ERROR_MESSAGE
            with suppress(Exception):
                await oracle.append_message(
                    StoredMessage(
                        id=uuid4().hex,
                        conversation_id=conversation_id,
                        reply_to_message_id=user_message.id,
                        role="ASSISTANT",
                        model=model_id or None,
                        content=message,
                        trace_id=trace_id,
                        status="ERROR",
                        created_at=datetime.now(UTC),
                    )
                )
            await queue.put(
                (
                    "error",
                    {"model_id": model_id, "message": message, "error_type": type(exc).__name__},
                )
            )
        finally:
            await queue.put(("model_done", {"model_id": model_id}))

    # 先頭に会話の枠組み(永続化済みユーザー発話 + 比較カラム)を 1 回送る。
    yield _sse_event(
        "start",
        {
            "conversation_id": conversation_id,
            "user_message": _to_chat_message(user_message).model_dump(mode="json"),
            "columns": [{"model_id": c["model_id"], "label": c["label"]} for c in columns],
        },
    )

    tasks = [asyncio.create_task(run_model(column)) for column in columns]
    remaining = len(columns)
    try:
        while remaining > 0:
            event = await queue.get()
            if event is None:
                break
            name, payload = event
            if name == "model_done":
                remaining -= 1
                continue
            if name == "result" and isinstance(payload, dict):
                model_id = str(payload["model_id"])
                yield _sse_event(
                    "metadata",
                    {
                        "model_id": model_id,
                        "message_id": payload["message_id"],
                        "trace_id": payload["trace_id"],
                        "elapsed_ms": payload["elapsed_ms"],
                        "guardrail_warnings": payload["guardrail_warnings"],
                    },
                )
                for chunk in _answer_chunks(str(payload["answer"])):
                    yield _sse_event("delta", {"model_id": model_id, "text": chunk})
                yield _sse_event(
                    "citations", {"model_id": model_id, "citations": payload["citations"]}
                )
                yield _sse_event(
                    "done", {"model_id": model_id, "message_id": payload["message_id"]}
                )
                continue
            yield _sse_event(name, payload)
        yield _sse_event("all_done", {"conversation_id": conversation_id})
    finally:
        for task in tasks:
            if not task.done():
                task.cancel()
        for task in tasks:
            with suppress(asyncio.CancelledError, Exception):
                await task
