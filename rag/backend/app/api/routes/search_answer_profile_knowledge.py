"""検索・回答プロファイル単位の知識 API(ドメインキーワード)。

rag_poc の「ドメインキーワード管理」を検索・回答プロファイル層へ移植したもの。
KB・文書レシピには持たせず、検索時は検索・回答プロファイルのキーワードだけを使う。
"""

import asyncio
from typing import Annotated

from fastapi import APIRouter, File, Form, HTTPException, Query, UploadFile
from rag_engine.knowledge.approved_faq import ApprovedFaqImportRow, ApprovedFaqRecord

from app.api.routes.search import (
    _published_guides,
    profile_scope_filters,
    support_guide_context,
)
from app.clients.oci_genai import OciGenAiClient
from app.clients.oracle import OracleClient
from app.config import get_settings
from app.rag.query_history import query_history_suggestions
from app.rag.search_answer_profile_knowledge import (
    APPROVED_FAQ_PREVIEW_ROWS,
    ApprovedFaqMutation,
    add_approved_faq,
    delete_approved_faq,
    edit_runtime_knowledge,
    import_approved_faq,
    is_direct_faq_match,
    load_approved_faq,
    load_approved_faq_enabled,
    load_domain_keywords,
    load_runtime_knowledge_payload,
    preview_runtime_knowledge,
    read_approved_faq_excel,
    save_approved_faq_enabled,
    save_domain_keywords,
    save_rule_clarification,
    suggest_approved_faq,
    suggest_clarification,
    suggest_domain_keywords,
)
from app.rag.support_guide_runtime import guide_clarification, match_guide
from app.schemas.common import ApiResponse
from app.schemas.search_answer_profile import SearchAnswerProfileDetail
from app.schemas.search_answer_profile_knowledge import (
    ApprovedFaqAddRequest,
    ApprovedFaqDeleteRequest,
    ApprovedFaqImportPreviewData,
    ApprovedFaqImportRowData,
    ApprovedFaqListData,
    ApprovedFaqMutationData,
    ApprovedFaqRecordData,
    ApprovedFaqSettingsRequest,
    ApprovedFaqSuggestionData,
    ApprovedFaqSuggestionsData,
    ApprovedFaqSuggestRequest,
    ClarificationSuggestionData,
    ClarificationSuggestionsData,
    ClarificationSuggestRequest,
    DomainKeywordCandidateData,
    DomainKeywordsData,
    DomainKeywordSuggestionData,
    DomainKeywordsUpdate,
    QuerySuggestion,
    QuerySuggestionsData,
    RuleClarificationRequest,
    RuntimeKnowledgeData,
    RuntimeKnowledgeEditRequest,
    RuntimeKnowledgePreviewData,
    RuntimeKnowledgePreviewRequest,
)

# チャットで提示する類似問の上限(rag_poc の候補の上限は 5。チャットは 3 ＋「どれでもない」。#684)。
CHAT_APPROVED_FAQ_LIMIT = 3

router = APIRouter()
MAX_FAQ_EXCEL_BYTES = 10 * 1024 * 1024


async def _require_search_answer_profile(
    oracle: OracleClient, search_answer_profile_id: str
) -> SearchAnswerProfileDetail:
    view = await oracle.get_search_answer_profile(search_answer_profile_id)
    if view is None:
        raise HTTPException(status_code=404, detail="検索・回答プロファイルが見つかりません。")
    return view


@router.get(
    "/{search_answer_profile_id}/domain-keywords",
    response_model=ApiResponse[DomainKeywordsData],
)
async def get_domain_keywords(search_answer_profile_id: str) -> ApiResponse[DomainKeywordsData]:
    """検索・回答プロファイルのドメインキーワードを返す。"""
    oracle = OracleClient()
    await _require_search_answer_profile(oracle, search_answer_profile_id)
    keywords = await load_domain_keywords(oracle, search_answer_profile_id)
    return ApiResponse(
        data=DomainKeywordsData(
            search_answer_profile_id=search_answer_profile_id, keywords=keywords
        )
    )


@router.put(
    "/{search_answer_profile_id}/domain-keywords",
    response_model=ApiResponse[DomainKeywordsData],
)
async def put_domain_keywords(
    search_answer_profile_id: str,
    request: DomainKeywordsUpdate,
) -> ApiResponse[DomainKeywordsData]:
    """ドメインキーワードを全置換で保存する。"""
    oracle = OracleClient()
    await _require_search_answer_profile(oracle, search_answer_profile_id)
    try:
        keywords = await save_domain_keywords(oracle, search_answer_profile_id, request.keywords)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return ApiResponse(
        data=DomainKeywordsData(
            search_answer_profile_id=search_answer_profile_id, keywords=keywords
        )
    )


@router.post(
    "/{search_answer_profile_id}/domain-keywords/suggest",
    response_model=ApiResponse[DomainKeywordSuggestionData],
)
async def suggest_search_answer_profile_domain_keywords(
    search_answer_profile_id: str,
    limit: int = Query(default=50, ge=1, le=200),
) -> ApiResponse[DomainKeywordSuggestionData]:
    """参照 KB の配信中チャンクからキーワード候補を返す(保存はしない)。"""
    oracle = OracleClient()
    view = await _require_search_answer_profile(oracle, search_answer_profile_id)
    result = await suggest_domain_keywords(
        oracle,
        search_answer_profile_id,
        view.config.normalized_knowledge_base_ids(),
        limit=limit,
    )
    return ApiResponse(
        data=DomainKeywordSuggestionData(
            candidates=[
                DomainKeywordCandidateData(
                    keyword=item.keyword,
                    score=round(item.score, 6),
                    frequency=item.frequency,
                    chunk_count=item.chunk_count,
                    document_count=item.document_count,
                )
                for item in result.candidates
            ],
            processed_chunk_count=result.processed_chunk_count,
        )
    )


# --- Approved FAQ(類似問)-----------------------------------------------------


def _faq_record_data(record: ApprovedFaqRecord) -> ApprovedFaqRecordData:
    return ApprovedFaqRecordData(
        id=record.id,
        question=record.question,
        answer=record.approved_answer,
        alternate_questions=list(record.alternate_questions),
        status=record.status,
    )


def _faq_mutation_data(
    search_answer_profile_id: str, mutation: ApprovedFaqMutation
) -> ApprovedFaqMutationData:
    return ApprovedFaqMutationData(
        search_answer_profile_id=search_answer_profile_id,
        records=[_faq_record_data(record) for record in mutation.records],
        inserted_count=mutation.inserted_count,
        deleted_count=mutation.deleted_count,
    )


@router.get(
    "/{search_answer_profile_id}/approved-faq",
    response_model=ApiResponse[ApprovedFaqListData],
)
async def get_approved_faq(search_answer_profile_id: str) -> ApiResponse[ApprovedFaqListData]:
    """検索・回答プロファイルの承認済み FAQ 一覧を返す。"""
    oracle = OracleClient()
    await _require_search_answer_profile(oracle, search_answer_profile_id)
    records = await load_approved_faq(oracle, search_answer_profile_id)
    return ApiResponse(
        data=ApprovedFaqListData(
            search_answer_profile_id=search_answer_profile_id,
            records=[_faq_record_data(record) for record in records],
            enabled=await load_approved_faq_enabled(oracle, search_answer_profile_id),
        )
    )


@router.put(
    "/{search_answer_profile_id}/approved-faq/settings",
    response_model=ApiResponse[ApprovedFaqListData],
)
async def put_approved_faq_settings(
    search_answer_profile_id: str, request: ApprovedFaqSettingsRequest
) -> ApiResponse[ApprovedFaqListData]:
    """回答の前に類似問を提示するかを保存する(検索・回答プロファイルごと。#684)。"""
    oracle = OracleClient()
    await _require_search_answer_profile(oracle, search_answer_profile_id)
    await save_approved_faq_enabled(oracle, search_answer_profile_id, request.enabled)
    return await get_approved_faq(search_answer_profile_id)


@router.post(
    "/{search_answer_profile_id}/approved-faq",
    response_model=ApiResponse[ApprovedFaqMutationData],
)
async def post_approved_faq(
    search_answer_profile_id: str, request: ApprovedFaqAddRequest
) -> ApiResponse[ApprovedFaqMutationData]:
    """FAQ を 1 件追加する(同じ質問は置き換え)。"""
    oracle = OracleClient()
    await _require_search_answer_profile(oracle, search_answer_profile_id)
    try:
        mutation = await add_approved_faq(
            oracle, search_answer_profile_id, question=request.question, answer=request.answer
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return ApiResponse(data=_faq_mutation_data(search_answer_profile_id, mutation))


@router.post(
    "/{search_answer_profile_id}/approved-faq/delete",
    response_model=ApiResponse[ApprovedFaqMutationData],
)
async def delete_search_answer_profile_approved_faq(
    search_answer_profile_id: str, request: ApprovedFaqDeleteRequest
) -> ApiResponse[ApprovedFaqMutationData]:
    """ID を指定して FAQ を削除する。"""
    oracle = OracleClient()
    await _require_search_answer_profile(oracle, search_answer_profile_id)
    try:
        mutation = await delete_approved_faq(oracle, search_answer_profile_id, request.ids)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return ApiResponse(data=_faq_mutation_data(search_answer_profile_id, mutation))


async def _excel_rows(file: UploadFile) -> list[ApprovedFaqImportRow]:
    content = await file.read()
    if len(content) > MAX_FAQ_EXCEL_BYTES:
        raise HTTPException(status_code=413, detail="Excel ファイルが大きすぎます(上限 10MB)。")
    try:
        return await asyncio.to_thread(read_approved_faq_excel, content, file.filename or "")
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.post(
    "/{search_answer_profile_id}/approved-faq/import/preview",
    response_model=ApiResponse[ApprovedFaqImportPreviewData],
)
async def preview_approved_faq_import(
    search_answer_profile_id: str, file: Annotated[UploadFile, File()]
) -> ApiResponse[ApprovedFaqImportPreviewData]:
    """Excel(QUESTION / ANSWER 列)の取込内容を先頭 10 件だけ返す(保存しない)。"""
    await _require_search_answer_profile(OracleClient(), search_answer_profile_id)
    rows = await _excel_rows(file)
    return ApiResponse(
        data=ApprovedFaqImportPreviewData(
            total=len(rows),
            rows=[
                ApprovedFaqImportRowData(
                    question=row.question, answer=row.approved_answer, row=row.row
                )
                for row in rows[:APPROVED_FAQ_PREVIEW_ROWS]
            ],
        )
    )


@router.post(
    "/{search_answer_profile_id}/approved-faq/import",
    response_model=ApiResponse[ApprovedFaqMutationData],
)
async def import_search_answer_profile_approved_faq(
    search_answer_profile_id: str,
    file: Annotated[UploadFile, File()],
    mode: Annotated[str, Form()] = "INSERT",
) -> ApiResponse[ApprovedFaqMutationData]:
    """Excel から FAQ を取り込む。INSERT は同じ質問をスキップ、DELETE_THEN_INSERT は置き換え。"""
    oracle = OracleClient()
    await _require_search_answer_profile(oracle, search_answer_profile_id)
    rows = await _excel_rows(file)
    try:
        mutation = await import_approved_faq(oracle, search_answer_profile_id, rows, mode=mode)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return ApiResponse(data=_faq_mutation_data(search_answer_profile_id, mutation))


@router.post(
    "/{search_answer_profile_id}/approved-faq/suggest",
    response_model=ApiResponse[ApprovedFaqSuggestionsData],
)
async def suggest_search_answer_profile_approved_faq(
    search_answer_profile_id: str, request: ApprovedFaqSuggestRequest
) -> ApiResponse[ApprovedFaqSuggestionsData]:
    """質問に近い承認済み FAQ(類似問)を返す。回答前の提示に使う。"""
    oracle = OracleClient()
    await _require_search_answer_profile(oracle, search_answer_profile_id)
    settings = get_settings()
    genai = (
        OciGenAiClient(settings=settings) if settings.rag_approved_faq_semantic_enabled else None
    )
    suggestions = await suggest_approved_faq(
        oracle,
        search_answer_profile_id,
        request.query,
        # チャットは最大 3 件 ＋「どれでもない」を出し、どれかを選ぶまで回答しない(#684)。
        limit=min(request.limit, CHAT_APPROVED_FAQ_LIMIT)
        if request.purpose == "chat"
        else request.limit,
        min_score=settings.rag_approved_faq_chat_min_score if request.purpose == "chat" else None,
        embed=(
            (lambda texts, input_type: genai.embed(texts, input_type=input_type))
            if genai is not None
            else None
        ),
        embedding_model=settings.oci_genai_embedding_model,
        embedding_dimensions=settings.oci_genai_embedding_dim,
    )
    if request.purpose == "chat" and suggestions:
        # 1 位とほぼ同じくらい近い候補だけを出す(#709)。
        top = max(item.score for item in suggestions)
        suggestions = [
            item
            for item in suggestions
            if item.score >= top - settings.rag_approved_faq_chat_max_gap
        ]
    return ApiResponse(
        data=ApprovedFaqSuggestionsData(
            suggestions=[
                ApprovedFaqSuggestionData(
                    id=item.record.id,
                    question=item.record.question,
                    matched_question=item.matched_question,
                    answer=item.record.approved_answer,
                    score=round(item.score, 4),
                    direct=is_direct_faq_match(item),
                )
                for item in suggestions
            ]
        )
    )


# --- 用語・ルール(runtime knowledge)-------------------------------------------


def _runtime_knowledge_data(
    search_answer_profile_id: str, payload: dict[str, object]
) -> RuntimeKnowledgeData:
    return RuntimeKnowledgeData.model_validate(
        {
            "search_answer_profile_id": search_answer_profile_id,
            "terms": payload.get("terms") or [],
            "rules": payload.get("rules") or [],
        }
    )


@router.get(
    "/{search_answer_profile_id}/runtime-knowledge",
    response_model=ApiResponse[RuntimeKnowledgeData],
)
async def get_runtime_knowledge(search_answer_profile_id: str) -> ApiResponse[RuntimeKnowledgeData]:
    """検索・回答プロファイルの用語・ルールを返す。"""
    oracle = OracleClient()
    await _require_search_answer_profile(oracle, search_answer_profile_id)
    payload = await load_runtime_knowledge_payload(oracle, search_answer_profile_id)
    return ApiResponse(data=_runtime_knowledge_data(search_answer_profile_id, payload))


@router.post(
    "/{search_answer_profile_id}/runtime-knowledge/edit",
    response_model=ApiResponse[RuntimeKnowledgeData],
)
async def post_runtime_knowledge_edit(
    search_answer_profile_id: str, request: RuntimeKnowledgeEditRequest
) -> ApiResponse[RuntimeKnowledgeData]:
    """用語またはルールを 1 行追加・更新・削除する。"""
    oracle = OracleClient()
    await _require_search_answer_profile(oracle, search_answer_profile_id)
    try:
        payload = await edit_runtime_knowledge(
            oracle,
            search_answer_profile_id,
            kind=request.kind,
            selected=request.selected,
            name=request.name,
            title=request.title,
            labels=request.labels,
            content=request.content,
            source=request.source,
            enabled=request.enabled,
            delete=request.delete,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return ApiResponse(data=_runtime_knowledge_data(search_answer_profile_id, payload))


@router.post(
    "/{search_answer_profile_id}/runtime-knowledge/preview",
    response_model=ApiResponse[RuntimeKnowledgePreviewData],
)
async def post_runtime_knowledge_preview(
    search_answer_profile_id: str, request: RuntimeKnowledgePreviewRequest
) -> ApiResponse[RuntimeKnowledgePreviewData]:
    """照合テスト: 質問に一致する用語・ルールと拡張後の検索文を返す(保存しない)。"""
    oracle = OracleClient()
    await _require_search_answer_profile(oracle, search_answer_profile_id)
    payload = await load_runtime_knowledge_payload(oracle, search_answer_profile_id)
    context = await asyncio.to_thread(preview_runtime_knowledge, payload, request.question)
    return ApiResponse(
        data=RuntimeKnowledgePreviewData(
            expanded_question=context.expanded_question,
            matched_terms=[term.term for term in context.matched_terms],
            matched_rules=[rule.title or rule.rule_id for rule in context.matched_rules],
        )
    )


@router.get(
    "/{search_answer_profile_id}/query-suggestions",
    response_model=ApiResponse[QuerySuggestionsData],
)
async def get_query_suggestions(
    search_answer_profile_id: str,
    q: str = Query(default="", max_length=500),
    large_category: str = Query(default="", max_length=200),
    middle_category: str = Query(default="", max_length=200),
    small_category: str = Query(default="", max_length=200),
) -> ApiResponse[QuerySuggestionsData]:
    """検索・回答プロファイルでよく聞かれる質問を、入力中の質問との類似度順に返す(質問履歴が有効なときだけ)。"""
    settings = get_settings()
    oracle = OracleClient()
    await _require_search_answer_profile(oracle, search_answer_profile_id)
    classification = {
        key: value
        for key, value in {
            "large_category": large_category,
            "middle_category": middle_category,
            "small_category": small_category,
        }.items()
        if value.strip()
    }
    suggestions = await query_history_suggestions(
        oracle,
        settings,
        search_answer_profile_id=search_answer_profile_id,
        question=q,
        classification=classification,
    )
    return ApiResponse(
        data=QuerySuggestionsData(
            search_answer_profile_id=search_answer_profile_id,
            enabled=settings.rag_query_history_enabled,
            suggestions=[
                QuerySuggestion(question=item.question, count=item.count) for item in suggestions
            ],
        )
    )


@router.put(
    "/{search_answer_profile_id}/runtime-knowledge/rules/{rule_id}/clarification",
    response_model=ApiResponse[RuntimeKnowledgeData],
)
async def put_rule_clarification(
    search_answer_profile_id: str, rule_id: str, request: RuleClarificationRequest
) -> ApiResponse[RuntimeKnowledgeData]:
    """ルールの確認の質問と選択肢を保存する(None は外す。#717)。"""
    oracle = OracleClient()
    await _require_search_answer_profile(oracle, search_answer_profile_id)
    try:
        payload = await save_rule_clarification(
            oracle, search_answer_profile_id, rule_id, request.clarification
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="ルールが見つかりません。") from exc
    return ApiResponse(data=_runtime_knowledge_data(search_answer_profile_id, payload))


@router.post(
    "/{search_answer_profile_id}/clarifications/suggest",
    response_model=ApiResponse[ClarificationSuggestionsData],
)
async def post_clarification_suggest(
    search_answer_profile_id: str, request: ClarificationSuggestRequest
) -> ApiResponse[ClarificationSuggestionsData]:
    """質問に出す確認(一致したルールのうち確認を持つ最初の 1 件。#717)。"""
    oracle = OracleClient()
    view = await _require_search_answer_profile(oracle, search_answer_profile_id)
    payload = await load_runtime_knowledge_payload(oracle, search_answer_profile_id)
    found = await asyncio.to_thread(suggest_clarification, payload, request.query)
    if found is None:
        # 業務ガイドの不明・矛盾の条件（選択肢付き）を、ルールの確認と同じ形で聞く（#1238）。
        # 適用範囲は検索と同じ手がかりで確かめる（#1278）。
        guides = await _published_guides(oracle, search_answer_profile_id)
        match = match_guide(
            guides,
            request.query,
            {},
            context=await support_guide_context(
                oracle, guides, request.query, profile_scope_filters(view)
            ),
        )
        guide = guide_clarification(match) if match is not None else None
        if guide is None:
            return ApiResponse(data=ClarificationSuggestionsData())
        rule_id, title, guide_question = guide
        return ApiResponse(
            data=ClarificationSuggestionsData(
                suggestion=ClarificationSuggestionData(
                    rule_id=rule_id, rule_title=title, clarification=guide_question
                )
            )
        )
    rule, clarification = found
    return ApiResponse(
        data=ClarificationSuggestionsData(
            suggestion=ClarificationSuggestionData(
                rule_id=str(rule.get("id", "")),
                rule_title=str(rule.get("title", "") or rule.get("id", "")),
                clarification=clarification,
            )
        )
    )
