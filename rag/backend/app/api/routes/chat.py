"""チャット(会話 / マルチモデル比較)API。

会話は検索・回答プロファイル(Search Answer Pro
file)配下に置く。メッセージ送信は既存 RAG パイプラインを

再利用し、会話履歴を生成プロンプトへ前置する(検索は最新メッセージのみで実行)。
``model_ids`` を複数指定すると設定済み OCI モデルへ fan-out し横並び比較できる。
"""

import asyncio
import logging
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import suppress
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Annotated
from uuid import uuid4

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request
from fastapi.responses import StreamingResponse
from pr_backend_core.api import OffsetParams, empty_page, offset_params, paginate

from app.api.routes.search import (
    STREAM_ERROR_MESSAGE,
    _answer_chunks,
    _published_guides,
    _resolve_query_context,
    _sse_event,
    answer_model_choices,
    ensure_search_answer_profile_has_knowledge_bases,
    ensure_search_answer_profile_knowledge_bases_permitted,
)
from app.clients.oci_enterprise_ai import OciEnterpriseAiClient
from app.clients.oracle import OracleClient, StoredConversation, StoredMessage
from app.config import (
    Settings,
    enterprise_ai_default_model_id,
    get_settings,
)
from app.db_degradation import load_or_degrade
from app.rag.answer_engine import AnswerScope
from app.rag.answer_timeout import AnswerTimeoutError, run_answer_with_timeout
from app.rag.chat_answer_runs import (
    CHAT_ANSWER_CANCELLED_MESSAGE,
    CHAT_ANSWER_INTERRUPTED_MESSAGE,
    CHAT_ANSWER_TIMEOUT_MARGIN_SECONDS,
    ChatAnswerLimitError,
    ChatAnswerRun,
    get_chat_answer_run_service,
)
from app.rag.chat_progress import ChatProgressTracker
from app.rag.document_sections_service import document_sections
from app.rag.guardrails import GuardrailPolicy, GuardrailResult
from app.rag.observability import new_trace_id
from app.rag.pipeline import ChatTurn, RagPipeline, SearchStageProgress
from app.rag.rate_limit import enforce_rate_limit
from app.rag.request_context import current_audit_request_context
from app.rag.search_answer_profile_knowledge import (
    clarification_document_ids,
    find_approved_faq,
    load_runtime_knowledge_payload,
    resolve_clarification,
)
from app.rag.support_guide_runtime import (
    GUIDE_CLARIFICATION_PREFIX,
    resolve_guide_clarification,
)
from app.schemas.chat import (
    ChatAnswerCancelResult,
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
from app.schemas.search import RetrievedChunk, SearchRequest, SearchResponse

router = APIRouter()
logger = logging.getLogger(__name__)
# 業務ガイドを選ぶときに含める、前の利用者の発話の数（#1238）。
GUIDE_CONTEXT_TURNS = 3

CHAT_DISABLED_MESSAGE = "チャット機能は現在無効です。"
CONVERSATION_NOT_FOUND_MESSAGE = "会話が見つかりません。"
SEARCH_ANSWER_PROFILE_NOT_FOUND_MESSAGE = "検索・回答プロファイルが見つかりません。"
HISTORY_PROMPT_LIMIT = 40
BLOCKED_MESSAGE_PLACEHOLDER = "安全ポリシーにより内容を保存しませんでした。"
ANSWER_STREAM_NOT_FOUND_MESSAGE = (
    "回答の配信を再開できません。保存済みの会話から回答を取り直してください。"
)
DUPLICATE_MESSAGE_ID_MESSAGE = "同じ質問を既に送信しています。会話を読み込み直してください。"


def _require_chat_enabled(settings: Settings) -> None:
    """チャット無効時は 404 にする(運用キルスイッチ)。"""
    if not settings.rag_chat_enabled:
        raise HTTPException(status_code=404, detail=CHAT_DISABLED_MESSAGE)


def _search_answer_profile_is_archived(view: object) -> bool:
    """テスト fake を含む検索・回答プロファイルの status を寛容に判定する。"""
    status = getattr(view, "status", None)
    return getattr(status, "value", status) == "ARCHIVED"


def _to_conversation_summary(conversation: StoredConversation) -> ConversationSummary:
    return ConversationSummary(
        id=conversation.id,
        search_answer_profile_id=conversation.search_answer_profile_id,
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
        progress=message.progress,
    )


@router.get("/models", response_model=ApiResponse[list[dict[str, str]]])
async def list_compare_models() -> ApiResponse[list[dict[str, str]]]:
    """比較で選べるモデル(既定のテキストモデルと既定の Vision モデル。先頭が既定。#675)。"""
    settings = get_settings()
    _require_chat_enabled(settings)
    models = answer_model_choices(settings)
    return ApiResponse(
        data=models,
        warning_messages=(
            [] if models else ["生成モデルが未設定です。システム設定 > モデルで登録してください。"]
        ),
    )


@router.get("/conversations", response_model=ApiResponse[Page[ConversationSummary]])
async def list_conversations(
    paging: Annotated[OffsetParams, Depends(offset_params(default=50, max_limit=200))],
    search_answer_profile_id: str | None = Query(default=None, max_length=128),
) -> ApiResponse[Page[ConversationSummary]]:
    """会話一覧を返す。DB 停止時は空一覧 + warning で縮退する。"""
    settings = get_settings()
    _require_chat_enabled(settings)
    oracle = OracleClient()

    async def _load() -> Page[ConversationSummary]:
        items = await oracle.list_conversations(
            search_answer_profile_id=search_answer_profile_id,
            limit=paging.limit,
            offset=paging.offset,
        )
        total = await oracle.count_conversations(search_answer_profile_id=search_answer_profile_id)
        return paginate(
            [_to_conversation_summary(item) for item in items],
            total=total,
            limit=paging.limit,
            offset=paging.offset,
        )

    fallback: Page[ConversationSummary] = empty_page(paging)
    page, degraded = await load_or_degrade(
        _load,
        timeout_seconds=settings.db_read_timeout_seconds,
        fallback=fallback,
        log_label="conversations_list",
    )
    return ApiResponse(data=page, warning_messages=[degraded.message] if degraded else [])


@router.post("/conversations", response_model=ApiResponse[ConversationDetail])
async def create_conversation(
    request: ConversationCreateRequest,
) -> ApiResponse[ConversationDetail]:
    """検索・回答プロファイル配下に会話を作成する。"""
    settings = get_settings()
    _require_chat_enabled(settings)
    oracle = OracleClient()
    view = await oracle.get_search_answer_profile(request.search_answer_profile_id)
    if view is None:
        raise HTTPException(status_code=404, detail=SEARCH_ANSWER_PROFILE_NOT_FOUND_MESSAGE)
    if _search_answer_profile_is_archived(view):
        raise HTTPException(
            status_code=409,
            detail="アーカイブ済みの検索・回答プロファイルでは会話を作成できません。",
        )
    conversation = await oracle.create_conversation(
        search_answer_profile_id=request.search_answer_profile_id, title=request.title
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
    # 作成していたプロセスが止まった作成中の回答は、中断の失敗にしてから返す（#1175）。
    if await _close_interrupted_answers(oracle, messages):
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


@router.delete("/conversations/{conversation_id}", response_model=ApiResponse[None])
async def delete_conversation(conversation_id: str) -> ApiResponse[None]:
    """会話とそのメッセージを削除する。"""
    settings = get_settings()
    _require_chat_enabled(settings)
    try:
        await OracleClient().delete_conversation(conversation_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=CONVERSATION_NOT_FOUND_MESSAGE) from exc
    return ApiResponse(data=None)


@router.post("/conversations/{conversation_id}/messages/stream")
async def stream_message(
    conversation_id: str,
    request: ChatMessageRequest,
    http_request: Request,
) -> StreamingResponse:
    """メッセージを送信し、回答(マルチモデルは N 系統)を SSE でストリーミングする。

    回答の作成は接続から切り離した task で進める（#1175）。接続が切れても作成は続き、最終の
    回答は会話のメッセージに保存される。画面は `GET .../messages/{質問の id}/stream` で続きを
    購読し直すか、保存済みの会話を取り直す。停止は `POST .../messages/{質問の id}/cancel`。
    """
    settings = get_settings()
    _require_chat_enabled(settings)
    enforce_rate_limit("search", http_request)
    oracle = OracleClient()
    conversation = await _load_sendable_conversation(oracle, conversation_id)
    context = current_audit_request_context()
    try:
        get_chat_answer_run_service().ensure_capacity(
            tenant_id_hash=context.tenant_id_hash,
            user_id_hash=context.user_id_hash,
            limit=settings.rag_chat_max_active_answers_per_user,
        )
    except ChatAnswerLimitError as exc:
        raise HTTPException(status_code=429, detail=str(exc)) from exc
    # 検索・回答プロファイルの解決・発話の検査・USER と作成中の ASSISTANT の保存は、応答を始める
    # 前に行う。応答を始めた後の HTTPException（409 / 404）や DB の例外は、利用者に理由を返せず
    # stream が途切れるため（#463）。
    turn = await _prepare_chat_turn(
        oracle, conversation_id, conversation.search_answer_profile_id, request, settings
    )
    columns = _resolve_compare_models(request, turn.settings)
    run = await _start_answer_run(oracle, turn, columns)
    return StreamingResponse(
        _subscribe_events(run, after=0),
        media_type="text/event-stream",
        headers=SSE_HEADERS,
    )


async def _load_sendable_conversation(
    oracle: OracleClient, conversation_id: str
) -> StoredConversation:
    """送信できる会話を返す(会話・検索・回答プロファイルの存在と状態、参照 KB の範囲を確認する)。"""
    conversation = await oracle.get_conversation(conversation_id)
    if conversation is None:
        raise HTTPException(status_code=404, detail=CONVERSATION_NOT_FOUND_MESSAGE)
    if conversation.status != "ACTIVE":
        raise HTTPException(status_code=409, detail="アーカイブ済みの会話には送信できません。")
    view = await oracle.get_search_answer_profile(conversation.search_answer_profile_id)
    if view is None:
        raise HTTPException(status_code=404, detail=SEARCH_ANSWER_PROFILE_NOT_FOUND_MESSAGE)
    if _search_answer_profile_is_archived(view):
        raise HTTPException(
            status_code=409,
            detail="アーカイブ済みの検索・回答プロファイルではチャットできません。",
        )
    # 検索・回答プロファイルの参照 KB が 0 件なら 409（
    # 全 KB を検索しない。#304）、1 つも利用できないなら
    #
    # 403（#214）。どちらも生成を始める前に HTTP の status で返す。
    profile_config = getattr(view, "config", None)
    if profile_config is not None:
        knowledge_base_ids = profile_config.normalized_knowledge_base_ids()
        ensure_search_answer_profile_has_knowledge_bases(knowledge_base_ids)
        ensure_search_answer_profile_knowledge_bases_permitted(knowledge_base_ids)
    return conversation


def _resolve_compare_models(
    request: ChatMessageRequest, settings: Settings
) -> list[dict[str, str]]:
    """比較対象の OCI モデル(model_id + label)を解決する。上限は設定値で抑える。"""
    catalog = {
        choice["model_id"]: choice["display_name"] for choice in answer_model_choices(settings)
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


@dataclass(frozen=True, slots=True)
class PreparedChatTurn:
    """保存済みのユーザー発話と、回答生成に使う有効 request / Settings / 履歴。"""

    conversation_id: str
    request: SearchRequest
    settings: Settings
    guardrails: GuardrailPolicy
    query_guardrail: GuardrailResult
    history: list[ChatTurn]
    user_message: StoredMessage
    # 利用者が選んだ類似の承認済み FAQ(質問・承認済みの回答。#684)。
    approved_faq: tuple[str, str] | None = None
    # 利用者が確認の質問で選んだ条件と対象範囲(#717)。
    scope: AnswerScope | None = None


async def _current_section_pages(
    oracle: OracleClient, document_ids: set[str]
) -> dict[tuple[str, str], tuple[int | None, int | None]]:
    """文書の今の章節のページ。確認を保存した後に章節のページが変わっても追う(#721)。"""
    pages: dict[tuple[str, str], tuple[int | None, int | None]] = {}
    for document_id in sorted(document_ids):
        resolved = await document_sections(oracle, document_id, None)
        if resolved is None:
            continue
        for section in resolved[0].sections:
            pages[(document_id, section.id)] = (section.page_start, section.page_end)
    return pages


async def _prepare_chat_turn(
    oracle: OracleClient,
    conversation_id: str,
    search_answer_profile_id: str,
    request: ChatMessageRequest,
    settings: Settings,
) -> PreparedChatTurn:
    """検索・回答プロファイルの設定を解決し、発話を検査して USER メッセージを保存する。"""
    approved_faq: tuple[str, str] | None = None
    if request.approved_faq_id:
        record = await find_approved_faq(oracle, search_answer_profile_id, request.approved_faq_id)
        if record is None:
            raise HTTPException(
                status_code=422,
                detail="選んだ類似問が見つかりません。もう一度送信して選び直してください。",
            )
        approved_faq = (record.question, record.approved_answer)
    scope: AnswerScope | None = None
    filters: dict[str, str] = {}
    conditions: dict[str, str] = {}
    if request.clarification is not None and request.clarification.rule_id.startswith(
        GUIDE_CLARIFICATION_PREFIX
    ):
        guide_answer = resolve_guide_clarification(
            await _published_guides(oracle, search_answer_profile_id),
            request.clarification.rule_id,
            request.clarification.option_ids,
        )
        # 業務ガイドの条件の確認（#1238）。答えは既知の条件として回答に渡す。
        if guide_answer is None:
            raise HTTPException(
                status_code=422,
                detail=(
                    "選んだ確認の答えが見つかりません。業務ガイドが変わった可能性があります。"
                    "もう一度送信して選び直してください。"
                ),
            )
        conditions, context = guide_answer
        scope = AnswerScope(context=context, search_terms=(), label="")
    elif request.clarification is not None:
        payload = await load_runtime_knowledge_payload(oracle, search_answer_profile_id)
        resolved = resolve_clarification(
            payload,
            request.clarification,
            await _current_section_pages(
                oracle, clarification_document_ids(payload, request.clarification)
            ),
        )
        if resolved is None:
            raise HTTPException(
                status_code=422,
                detail=(
                    "選んだ確認の答えが見つかりません。検索・回答プロファイルのルールが変わった可能性が"
                    "あります。もう一度送信して選び直してください。"
                ),
            )
        scope, page_ranges = resolved
        if page_ranges:
            filters["page_ranges"] = page_ranges
    base_request = SearchRequest(
        query=request.content,
        top_k=request.top_k,
        search_answer_profile_id=search_answer_profile_id,
        filters=filters,
        conditions=conditions,
    )
    # 履歴は今回のユーザー発話を保存する前に読む(自分自身を含めない)。
    prior_messages = await oracle.list_messages(conversation_id)
    (
        effective_request,
        effective_settings,
        _applied_kb,
        _applied_view,
    ) = await _resolve_query_context(
        base_request,
        settings,
        # 業務ガイドは前の発話も含めて選ぶ（短い返答だけで照合が外れないように。#1238）。
        guide_context=[message.content for message in prior_messages if message.role == "USER"][
            -GUIDE_CONTEXT_TURNS:
        ],
        interactive=True,
    )
    guardrails = GuardrailPolicy(effective_settings)
    query_guardrail = await asyncio.to_thread(guardrails.validate_query, request.content)
    if request.client_message_id and any(
        message.id == request.client_message_id for message in prior_messages
    ):
        raise HTTPException(status_code=409, detail=DUPLICATE_MESSAGE_ID_MESSAGE)
    history = await _build_safe_history(prior_messages, guardrails)
    now = datetime.now(UTC)
    user_message = await oracle.append_message(
        StoredMessage(
            # 画面が決めた id（#1175）。送信の直後の停止をこの id で取り消せる。
            id=request.client_message_id or uuid4().hex,
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
    return PreparedChatTurn(
        conversation_id=conversation_id,
        request=effective_request,
        settings=effective_settings,
        guardrails=guardrails,
        query_guardrail=query_guardrail,
        history=history,
        user_message=user_message,
        approved_faq=approved_faq,
        scope=scope,
    )


def _chat_error_message(exc: Exception) -> str:
    """回答生成の失敗を利用者向けの文言にする(ERROR メッセージと SSE の error event)。"""
    if isinstance(exc, AnswerTimeoutError):
        return exc.user_message
    return STREAM_ERROR_MESSAGE


async def _generate_chat_answer(
    oracle: OracleClient,
    turn: PreparedChatTurn,
    model_id: str,
    message_id: str,
    *,
    lease_owner: str,
    progress_callback: Callable[[SearchStageProgress], Awaitable[None]] | None = None,
    tracker: ChatProgressTracker | None = None,
) -> tuple[StoredMessage, SearchResponse]:
    """1 モデル分の回答を生成し、作成中の ASSISTANT メッセージに最終の状態を書く(#1175)。

    生成は回答生成の上限（`rag_answer_timeout_seconds`。#375）で打ち切る。時間切れは
    最後の工程を持つ `AnswerTimeoutError` になる。
    失敗したら ERROR を保存してから例外をそのまま送出する。保存は作成中（STREAMING）の行だけに
    当たる（利用者の停止・中断の後は書かない）。処理の段階（`tracker`）は終端にして一緒に保存する。
    """
    trace_id = new_trace_id()
    try:
        llm = OciEnterpriseAiClient(settings=turn.settings, model_id=model_id or None)
        # llm は会話履歴による質問の書き換えに使う。回答は回答エンジンが自分でモデルを呼ぶので、
        # 列のモデルを別に渡す(#593)。
        pipeline = RagPipeline(
            settings=turn.settings,
            llm=llm,
            guardrails=turn.guardrails,
            answer_model_id=model_id or None,
            approved_faq=turn.approved_faq,
            scope=turn.scope,
        )
        result = await run_answer_with_timeout(
            lambda progress_tracker: pipeline.run(
                turn.request,
                trace_id=trace_id,
                progress_callback=progress_tracker,
                history=turn.history,
                query_guardrail_result=turn.query_guardrail,
            ),
            turn.settings,
            progress_callback,
        )
        if tracker is not None:
            tracker.finish(citation_count=len(result.citations))
        assistant = StoredMessage(
            id=message_id,
            conversation_id=turn.conversation_id,
            reply_to_message_id=turn.user_message.id,
            role="ASSISTANT",
            model=model_id or None,
            content=result.answer,
            citations=[citation.model_dump(mode="json") for citation in result.citations],
            guardrail_warnings=result.guardrail_warnings,
            trace_id=result.trace_id,
            status="COMPLETE",
            elapsed_ms=result.elapsed_ms,
            created_at=datetime.now(UTC),
            progress=tracker.snapshot() if tracker is not None else None,
        )
        await oracle.finish_chat_message(assistant, lease_owner=lease_owner)
    except Exception as exc:
        if tracker is not None:
            tracker.fail()
        with suppress(Exception):
            await oracle.finish_chat_message(
                StoredMessage(
                    id=message_id,
                    conversation_id=turn.conversation_id,
                    reply_to_message_id=turn.user_message.id,
                    role="ASSISTANT",
                    model=model_id or None,
                    content=_chat_error_message(exc),
                    trace_id=trace_id,
                    status="ERROR",
                    created_at=datetime.now(UTC),
                    progress=tracker.snapshot() if tracker is not None else None,
                ),
                lease_owner=lease_owner,
            )
        raise
    return assistant, result


# 段階の間に event が無い間（回答の作成など）も、この間隔で SSE のコメントを送る（#1160）。
# 画面は配信が途絶えた（接続が切れた・止まった）かを、届いたバイトで判定する。中継（Nginx など）の
# 無通信の打ち切りも防ぐ。
SSE_HEARTBEAT_SECONDS = 10.0
SSE_HEARTBEAT = ": keepalive\n\n"
SSE_HEADERS = {"Cache-Control": "no-cache", "X-Accel-Buffering": "no"}


async def _start_answer_run(
    oracle: OracleClient,
    turn: PreparedChatTurn,
    columns: list[dict[str, str]],
) -> ChatAnswerRun:
    """作成中の ASSISTANT メッセージを保存し、回答の作成を接続から切り離した task で始める(#1175)。

    最初に `start` の event（保存済みの質問・比較の列・列ごとの作成中のメッセージ）を記録する。
    """
    service = get_chat_answer_run_service()
    context = current_audit_request_context()
    run = service.register(
        run_id=turn.user_message.id,
        conversation_id=turn.conversation_id,
        tenant_id_hash=context.tenant_id_hash,
        user_id_hash=context.user_id_hash,
        store=oracle,
    )
    # 利用者向けの処理の段階（3 製品共通の ChatProgressStep。#1146）。
    trackers = {
        column["model_id"]: ChatProgressTracker(
            rerank_enabled=turn.settings.rag_rerank_enabled, model_id=column["model_id"]
        )
        for column in columns
    }
    try:
        for column in columns:
            model_id = column["model_id"]
            placeholder = await oracle.append_message(
                StoredMessage(
                    id=uuid4().hex,
                    conversation_id=turn.conversation_id,
                    reply_to_message_id=turn.user_message.id,
                    role="ASSISTANT",
                    model=model_id or None,
                    content="",
                    status="STREAMING",
                    created_at=datetime.now(UTC),
                    progress=trackers[model_id].snapshot(),
                    lease_owner=service.worker_id,
                )
            )
            run.message_ids[model_id] = placeholder.id
    except Exception:
        service.discard(run)
        raise
    run.publish(
        "start",
        {
            "conversation_id": turn.conversation_id,
            "user_message": _to_chat_message(turn.user_message).model_dump(mode="json"),
            "columns": [
                {
                    "model_id": column["model_id"],
                    "label": column["label"],
                    "message_id": run.message_ids[column["model_id"]],
                }
                for column in columns
            ],
        },
    )

    async def execute(current: ChatAnswerRun) -> None:
        await _answer_turn(current, oracle, turn, columns, trackers, service.worker_id)

    service.start(
        run,
        execute,
        timeout_seconds=turn.settings.rag_answer_timeout_seconds
        + CHAT_ANSWER_TIMEOUT_MARGIN_SECONDS,
    )
    return run


async def _answer_turn(
    run: ChatAnswerRun,
    oracle: OracleClient,
    turn: PreparedChatTurn,
    columns: list[dict[str, str]],
    trackers: dict[str, ChatProgressTracker],
    lease_owner: str,
) -> None:
    """各モデルへ fan-out して回答を作り、event を記録する（購読の有無に関係なく最後まで進む）。"""

    async def run_model(column: dict[str, str]) -> None:
        model_id = column["model_id"]
        message_id = run.message_ids[model_id]
        tracker = trackers[model_id]

        async def emit_steps(*, persist: bool) -> None:
            steps = tracker.snapshot()
            run.publish("progress", {"model_id": model_id, "steps": steps})
            if not persist:
                return
            # 段階を保存する（再読込・別の worker から作成中の段階を見せる。#1175）。保存は補助。
            try:
                await oracle.update_chat_message_progress(
                    message_id, lease_owner=lease_owner, progress=steps
                )
            except Exception as exc:  # noqa: BLE001 - 段階の保存に失敗しても作成は続ける
                logger.warning(
                    "chat_answer_progress_save_failed",
                    extra={"run_id": run.run_id, "error_type": type(exc).__name__},
                )

        async def emit_progress(progress: SearchStageProgress) -> None:
            run.publish(
                "stage",
                {
                    "model_id": model_id,
                    "trace_id": progress.trace_id,
                    "stage": progress.stage,
                    "outcome": progress.outcome,
                    "elapsed_ms": progress.elapsed_ms,
                },
            )
            if tracker.observe(progress):
                await emit_steps(persist=True)

        await emit_steps(persist=False)
        try:
            assistant, result = await _generate_chat_answer(
                oracle,
                turn,
                model_id,
                message_id,
                lease_owner=lease_owner,
                progress_callback=emit_progress,
                tracker=tracker,
            )
        except Exception as exc:
            run.mark_closed(message_id)
            # SSE では例外を error event へ落とす（段階は失敗にしてから送る）。
            await emit_steps(persist=False)
            run.publish(
                "error",
                {
                    "model_id": model_id,
                    "message_id": message_id,
                    "message": _chat_error_message(exc),
                    "error_type": type(exc).__name__,
                    # 時間切れになった工程（#375）。画面は文言をそのまま出し、工程は記録に使う。
                    "stage": exc.stage if isinstance(exc, AnswerTimeoutError) else None,
                },
            )
            return
        run.mark_closed(message_id)
        await emit_steps(persist=False)
        run.publish(
            "metadata",
            {
                "model_id": model_id,
                "message_id": assistant.id,
                "trace_id": result.trace_id,
                "elapsed_ms": result.elapsed_ms,
                "guardrail_warnings": result.guardrail_warnings,
                # 回答エンジンの根拠・実行記録(無いときは None)。
                "answer_diagnostics": result.diagnostics.answer,
            },
        )
        for chunk in _answer_chunks(result.answer):
            run.publish("delta", {"model_id": model_id, "text": chunk})
        run.publish(
            "citations",
            {
                "model_id": model_id,
                "citations": [citation.model_dump(mode="json") for citation in result.citations],
            },
        )
        run.publish("done", {"model_id": model_id, "message_id": assistant.id})

    await asyncio.gather(*(run_model(column) for column in columns))


async def _subscribe_events(run: ChatAnswerRun, *, after: int) -> AsyncIterator[str]:
    """回答の作成の event を SSE で流す（`id:` は再購読で続きを指す連番）。

    接続が切れて generator が止まっても、作成の task は止めない(#1175)。
    """
    async for event in run.subscribe(after=after, heartbeat_seconds=SSE_HEARTBEAT_SECONDS):
        if event is None:
            yield SSE_HEARTBEAT
            continue
        yield f"id: {event.seq}\n" + _sse_event(event.name, event.payload)


def _last_event_id(header: str | None, after: int | None) -> int:
    """再購読の続きの位置（`after` を優先し、無ければ `Last-Event-ID`。読めなければ最初から）。"""
    if after is not None:
        return after
    if header is None:
        return 0
    try:
        return max(0, int(header.strip()))
    except ValueError:
        return 0


def _run_belongs_to_caller(run: ChatAnswerRun, conversation_id: str) -> bool:
    """run が、この会話の・この利用者（tenant と利用者）のものか。"""
    context = current_audit_request_context()
    return (
        run.conversation_id == conversation_id
        and run.tenant_id_hash == context.tenant_id_hash
        and run.user_id_hash == context.user_id_hash
    )


@router.get("/conversations/{conversation_id}/messages/{message_id}/stream")
async def resume_message_stream(
    conversation_id: str,
    message_id: str,
    after: int | None = Query(default=None, ge=0),
    last_event_id: str | None = Header(default=None, alias="Last-Event-ID"),
) -> StreamingResponse:
    """作成中の回答の配信を、続き（`Last-Event-ID` / `after` の次の event）から購読し直す(#1175)。

    `message_id` は質問（USER のメッセージ）の id。このプロセスで作成していない（別の worker・
    再起動の後・作成の記録を残す時間を過ぎた）ときは 404。画面は保存済みの会話を取り直す。
    """
    settings = get_settings()
    _require_chat_enabled(settings)
    oracle = OracleClient()
    if await oracle.get_conversation(conversation_id) is None:
        raise HTTPException(status_code=404, detail=CONVERSATION_NOT_FOUND_MESSAGE)
    run = get_chat_answer_run_service().get(message_id)
    if run is None or not _run_belongs_to_caller(run, conversation_id):
        raise HTTPException(status_code=404, detail=ANSWER_STREAM_NOT_FOUND_MESSAGE)
    return StreamingResponse(
        _subscribe_events(run, after=_last_event_id(last_event_id, after)),
        media_type="text/event-stream",
        headers=SSE_HEADERS,
    )


@router.post(
    "/conversations/{conversation_id}/messages/{message_id}/cancel",
    response_model=ApiResponse[ChatAnswerCancelResult],
)
async def cancel_message_answer(
    conversation_id: str, message_id: str
) -> ApiResponse[ChatAnswerCancelResult]:
    """質問（`message_id`）への回答の作成を止め、作成中の回答を停止（CANCELLED）にする(#1175)。

    接続の切断では作成を止めないので、利用者の「停止」はこの API で取り消す。
    - このプロセスで作成中: task を止め、停止を保存するまで待つ。
    - 別のプロセスで作成中: 作成中の回答を停止にする（そのプロセスは次の heartbeat で止まる）。
    - まだ作成を始めていない（送信の直後）: 取消を覚えておき、始まったら停止として保存する。
    """
    settings = get_settings()
    _require_chat_enabled(settings)
    oracle = OracleClient()
    if await oracle.get_conversation(conversation_id) is None:
        raise HTTPException(status_code=404, detail=CONVERSATION_NOT_FOUND_MESSAGE)
    service = get_chat_answer_run_service()
    run = service.get(message_id)
    if run is not None and _run_belongs_to_caller(run, conversation_id):
        if run.done:
            return ApiResponse(data=ChatAnswerCancelResult(cancelled=False))
        await service.cancel(run)
        return ApiResponse(data=ChatAnswerCancelResult(cancelled=True))
    messages = await oracle.list_messages(conversation_id)
    if not any(message.id == message_id for message in messages):
        service.remember_early_cancel(message_id)
        return ApiResponse(data=ChatAnswerCancelResult(cancelled=True))
    cancelled = False
    for message in messages:
        if (
            message.role == "ASSISTANT"
            and message.reply_to_message_id == message_id
            and message.status == "STREAMING"
        ):
            cancelled = (
                await oracle.close_streaming_chat_message(
                    message.id, status="CANCELLED", content=CHAT_ANSWER_CANCELLED_MESSAGE
                )
                or cancelled
            )
    return ApiResponse(data=ChatAnswerCancelResult(cancelled=cancelled))


async def _close_interrupted_answers(oracle: OracleClient, messages: list[StoredMessage]) -> bool:
    """作成していたプロセスが止まった作成中の回答を、中断の失敗にする(#1175)。

    このプロセスで作成中のものは除く。このプロセスが作っていたのに動いていないもの（task が
    後始末の前に終わった）と、heartbeat が途絶えたもの（別のプロセスの停止・再起動）だけを、
    DB の時刻で判定して閉じる（永遠に作成中にしない）。閉じたら True。
    """
    service = get_chat_answer_run_service()
    closed = False
    for message in messages:
        if message.role != "ASSISTANT" or message.status != "STREAMING":
            continue
        if service.is_running(message.reply_to_message_id):
            continue
        try:
            closed = (
                await oracle.close_streaming_chat_message(
                    message.id,
                    status="ERROR",
                    content=CHAT_ANSWER_INTERRUPTED_MESSAGE,
                    lease_owner=service.worker_id,
                    stale_seconds=service.stale_seconds,
                )
                or closed
            )
        except Exception as exc:  # noqa: BLE001 - 判定できなければ作成中のまま返す
            logger.warning(
                "chat_answer_interrupt_check_failed", extra={"error_type": type(exc).__name__}
            )
    return closed
