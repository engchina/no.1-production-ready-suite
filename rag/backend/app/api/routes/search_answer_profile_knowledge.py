"""検索・回答プロファイル単位の知識 API(ドメインキーワード)。

rag_poc の「ドメインキーワード管理」を検索・回答プロファイル層へ移植したもの。
KB・文書レシピには持たせず、検索時は検索・回答プロファイルのキーワードだけを使う。
"""

import asyncio
from typing import Annotated, Any, cast

from fastapi import APIRouter, File, Form, HTTPException, Query, UploadFile
from fastapi.concurrency import run_in_threadpool
from rag_engine.knowledge.approved_faq import ApprovedFaqImportRow

from app.api.routes.search import (
    _published_guides,
    ensure_search_answer_profile_knowledge_bases_permitted,
    profile_scope_filters,
    support_guide_context,
)
from app.clients.oci_genai import OciGenAiClient
from app.clients.oracle import OracleClient
from app.config import get_settings
from app.rag.approved_faq_scope import ApprovedFaqScopeState
from app.rag.domain_keyword_import import (
    PREVIEW_ROWS,
    KeywordImportMode,
    import_keywords,
    read_keyword_excel,
)
from app.rag.profile_language import profile_language_scope
from app.rag.search_answer_profile_config import resolve_search_answer_profile_settings
from app.rag.search_answer_profile_knowledge import (
    APPROVED_FAQ_PREVIEW_ROWS,
    FAQ_DIRECT_ANSWER_ENABLED_KEY,
    FAQ_ENABLED_KEY,
    ApprovedFaqMutation,
    add_approved_faq,
    approved_faq_direct_answer_enabled,
    approved_faq_enabled,
    approved_faq_match_options,
    confirm_approved_faq,
    delete_approved_faq,
    edit_runtime_knowledge,
    import_approved_faq,
    is_direct_faq_match,
    load_approved_faq_snapshot,
    load_domain_keywords,
    load_runtime_knowledge_payload,
    match_direct_approved_faq,
    preview_runtime_knowledge,
    rank_approved_faq,
    read_approved_faq_excel,
    save_approved_faq_negative_questions,
    save_approved_faq_settings,
    save_domain_keywords,
    suggest_domain_keywords,
)
from app.rag.support_guide_conditions import resolve_text_conditions
from app.rag.support_guide_query import build_guide_query
from app.rag.support_guide_runtime import (
    GuideChoice,
    guide_choice_clarification,
    guide_clarification,
    select_guide,
)
from app.schemas.common import ApiResponse
from app.schemas.search_answer_profile import SearchAnswerProfileDetail
from app.schemas.search_answer_profile_knowledge import (
    ApprovedFaqAddRequest,
    ApprovedFaqConfirmRequest,
    ApprovedFaqDeleteRequest,
    ApprovedFaqImportPreviewData,
    ApprovedFaqImportRowData,
    ApprovedFaqKnowledgeBaseData,
    ApprovedFaqListData,
    ApprovedFaqMutationData,
    ApprovedFaqNegativeQuestionsRequest,
    ApprovedFaqRecordData,
    ApprovedFaqReviewReason,
    ApprovedFaqSettingsRequest,
    ApprovedFaqSuggestionData,
    ApprovedFaqSuggestionsData,
    ApprovedFaqSuggestRequest,
    ClarificationSuggestionData,
    ClarificationSuggestionsData,
    ClarificationSuggestRequest,
    DomainKeywordCandidateData,
    DomainKeywordImportData,
    DomainKeywordImportPreviewData,
    DomainKeywordImportRowData,
    DomainKeywordsData,
    DomainKeywordSuggestionData,
    DomainKeywordsUpdate,
    RuntimeKnowledgeData,
    RuntimeKnowledgeEditRequest,
    RuntimeKnowledgePreviewData,
    RuntimeKnowledgePreviewRequest,
)
from app.security.user_names import user_display_names

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


async def _keyword_excel_rows(file: UploadFile) -> list[tuple[int, str]]:
    content = await file.read(MAX_FAQ_EXCEL_BYTES + 1)
    if len(content) > MAX_FAQ_EXCEL_BYTES:
        raise HTTPException(status_code=413, detail="Excel ファイルが大きすぎます(上限 10MB)。")
    try:
        return await asyncio.to_thread(read_keyword_excel, content, file.filename or "")
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.post(
    "/{search_answer_profile_id}/domain-keywords/import/preview",
    response_model=ApiResponse[DomainKeywordImportPreviewData],
)
async def preview_domain_keyword_import(
    search_answer_profile_id: str, file: Annotated[UploadFile, File()]
) -> ApiResponse[DomainKeywordImportPreviewData]:
    await _require_search_answer_profile(OracleClient(), search_answer_profile_id)
    rows = await _keyword_excel_rows(file)
    return ApiResponse(
        data=DomainKeywordImportPreviewData(
            total=len(rows),
            rows=[
                DomainKeywordImportRowData(row=number, keyword=word)
                for number, word in rows[:PREVIEW_ROWS]
            ],
        )
    )


@router.post(
    "/{search_answer_profile_id}/domain-keywords/import",
    response_model=ApiResponse[DomainKeywordImportData],
)
async def import_domain_keyword_excel(
    search_answer_profile_id: str,
    file: Annotated[UploadFile, File()],
    mode: Annotated[KeywordImportMode, Form()] = "INSERT",
) -> ApiResponse[DomainKeywordImportData]:
    oracle = OracleClient()
    await _require_search_answer_profile(oracle, search_answer_profile_id)
    rows = await _keyword_excel_rows(file)
    try:
        keywords, inserted = await import_keywords(
            oracle, search_answer_profile_id, [word for _, word in rows], mode=mode
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return ApiResponse(
        data=DomainKeywordImportData(
            search_answer_profile_id=search_answer_profile_id,
            keywords=keywords,
            inserted_count=inserted,
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


def _faq_record_data(
    state: ApprovedFaqScopeState, approver_names: dict[str, str]
) -> ApprovedFaqRecordData:
    record = state.record
    approval = record.approval
    return ApprovedFaqRecordData(
        id=record.id,
        question=record.question,
        answer=record.approved_answer,
        alternate_questions=list(record.alternate_questions),
        negative_questions=list(record.negative_questions),
        status=record.status,
        target_knowledge_base_ids=list(record.target_knowledge_base_ids or ()),
        source_document_ids=[source.document_id for source in record.source_documents],
        approved_by=approver_names.get(approval.approved_by) if approval else None,
        approved_at=(approval.approved_at or None) if approval else None,
        review_reasons=[cast(ApprovedFaqReviewReason, reason) for reason in state.review_reasons],
        source_updated=state.source_updated,
    )


async def _faq_list_data(
    oracle: OracleClient, search_answer_profile_id: str
) -> ApprovedFaqListData:
    """FAQ の一覧と今の根拠の状態・承認者の表示名・対象の KB の名前(#4621)。"""
    snapshot = await load_approved_faq_snapshot(oracle, search_answer_profile_id)
    approvers = sorted(
        {state.record.approval.approved_by for state in snapshot.states if state.record.approval}
        - {""}
    )
    approver_names = await run_in_threadpool(user_display_names, approvers) if approvers else {}
    profile_kbs = snapshot.profile_knowledge_base_ids
    kb_ids = list(
        dict.fromkeys(
            [
                *profile_kbs,
                *(
                    knowledge_base_id
                    for record in snapshot.records
                    for knowledge_base_id in record.target_knowledge_base_ids or ()
                ),
            ]
        )
    )
    names = (
        {kb.id: kb.name for kb in await oracle.list_knowledge_bases(knowledge_base_ids=kb_ids)}
        if kb_ids
        else {}
    )
    return ApprovedFaqListData(
        search_answer_profile_id=search_answer_profile_id,
        records=[_faq_record_data(state, approver_names) for state in snapshot.states],
        enabled=approved_faq_enabled(snapshot.payload),
        # スイッチの保存値をそのまま返す(類似問の提示がオフの間は使わないが、設定は残す)。
        direct_answer_enabled=(snapshot.payload or {}).get(FAQ_DIRECT_ANSWER_ENABLED_KEY) is True,
        direct_answer_min_score=get_settings().rag_approved_faq_direct_answer_min_score,
        knowledge_bases=[
            ApprovedFaqKnowledgeBaseData(
                id=kb_id, name=names.get(kb_id, kb_id), in_profile=kb_id in profile_kbs
            )
            for kb_id in kb_ids
        ],
    )


async def _faq_mutation_data(
    oracle: OracleClient, search_answer_profile_id: str, mutation: ApprovedFaqMutation
) -> ApprovedFaqMutationData:
    listed = await _faq_list_data(oracle, search_answer_profile_id)
    return ApprovedFaqMutationData(
        **listed.model_dump(),
        inserted_count=mutation.inserted_count,
        deleted_count=mutation.deleted_count,
    )


@router.get(
    "/{search_answer_profile_id}/approved-faq",
    response_model=ApiResponse[ApprovedFaqListData],
)
async def get_approved_faq(search_answer_profile_id: str) -> ApiResponse[ApprovedFaqListData]:
    """検索・回答プロファイルの承認済み FAQ 一覧を返す(根拠の状態・承認者・対象の KB を含む)。"""
    oracle = OracleClient()
    await _require_search_answer_profile(oracle, search_answer_profile_id)
    return ApiResponse(data=await _faq_list_data(oracle, search_answer_profile_id))


@router.post(
    "/{search_answer_profile_id}/approved-faq/{faq_id}/confirm",
    response_model=ApiResponse[ApprovedFaqListData],
)
async def confirm_search_answer_profile_approved_faq(
    search_answer_profile_id: str, faq_id: str, request: ApprovedFaqConfirmRequest
) -> ApiResponse[ApprovedFaqListData]:
    """FAQ の対象の KB と根拠を確かめ直し、承認者と時刻を記録する(要確認からの復帰。#4621)。"""
    oracle = OracleClient()
    await _require_search_answer_profile(oracle, search_answer_profile_id)
    try:
        await confirm_approved_faq(
            oracle,
            search_answer_profile_id,
            faq_id,
            target_knowledge_base_ids=request.target_knowledge_base_ids,
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="FAQ が見つかりません。") from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return ApiResponse(data=await _faq_list_data(oracle, search_answer_profile_id))


@router.put(
    "/{search_answer_profile_id}/approved-faq/settings",
    response_model=ApiResponse[ApprovedFaqListData],
)
async def put_approved_faq_settings(
    search_answer_profile_id: str, request: ApprovedFaqSettingsRequest
) -> ApiResponse[ApprovedFaqListData]:
    """プロファイルごとの FAQ の設定を保存する(送った項目だけ。#684・#4788)。

    類似問を提示するか・一致度が下限以上のとき FAQ の回答をそのまま返すか。
    """
    oracle = OracleClient()
    await _require_search_answer_profile(oracle, search_answer_profile_id)
    changes: dict[str, bool] = {}
    if request.enabled is not None:
        changes[FAQ_ENABLED_KEY] = request.enabled
    if request.direct_answer_enabled is not None:
        changes[FAQ_DIRECT_ANSWER_ENABLED_KEY] = request.direct_answer_enabled
    if changes:
        await save_approved_faq_settings(oracle, search_answer_profile_id, changes)
    return await get_approved_faq(search_answer_profile_id)


@router.put(
    "/{search_answer_profile_id}/approved-faq/{faq_id}/negative-questions",
    response_model=ApiResponse[ApprovedFaqListData],
)
async def put_approved_faq_negative_questions(
    search_answer_profile_id: str, faq_id: str, request: ApprovedFaqNegativeQuestionsRequest
) -> ApiResponse[ApprovedFaqListData]:
    """FAQ の否定の質問(この FAQ を当てたくない質問の例)を置き換える(#4788)。"""
    oracle = OracleClient()
    await _require_search_answer_profile(oracle, search_answer_profile_id)
    try:
        await save_approved_faq_negative_questions(
            oracle, search_answer_profile_id, faq_id, request.negative_questions
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="FAQ が見つかりません。") from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return ApiResponse(data=await _faq_list_data(oracle, search_answer_profile_id))


@router.post(
    "/{search_answer_profile_id}/approved-faq",
    response_model=ApiResponse[ApprovedFaqMutationData],
)
async def post_approved_faq(
    search_answer_profile_id: str, request: ApprovedFaqAddRequest
) -> ApiResponse[ApprovedFaqMutationData]:
    """FAQ を 1 件追加する(同じ質問は追加しない)。対象の KB の未指定はプロファイルの全 KB。"""
    oracle = OracleClient()
    await _require_search_answer_profile(oracle, search_answer_profile_id)
    try:
        mutation = await add_approved_faq(
            oracle,
            search_answer_profile_id,
            question=request.question,
            answer=request.answer,
            target_knowledge_base_ids=request.target_knowledge_base_ids,
            negative_questions=request.negative_questions,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return ApiResponse(data=await _faq_mutation_data(oracle, search_answer_profile_id, mutation))


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
    return ApiResponse(data=await _faq_mutation_data(oracle, search_answer_profile_id, mutation))


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
                    question=row.question,
                    answer=row.approved_answer,
                    row=row.row,
                    negative_questions=list(row.negative_questions),
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
    target_knowledge_base_ids: Annotated[list[str] | None, Form()] = None,
) -> ApiResponse[ApprovedFaqMutationData]:
    """Excel から FAQ を取り込む。INSERT は同じ質問をスキップ、DELETE_THEN_INSERT は置き換え。

    取り込む FAQ の対象の KB は ``target_knowledge_base_ids``(未指定はプロファイルの全 KB)。
    """
    oracle = OracleClient()
    await _require_search_answer_profile(oracle, search_answer_profile_id)
    rows = await _excel_rows(file)
    try:
        mutation = await import_approved_faq(
            oracle,
            search_answer_profile_id,
            rows,
            mode=mode,
            target_knowledge_base_ids=target_knowledge_base_ids,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return ApiResponse(data=await _faq_mutation_data(oracle, search_answer_profile_id, mutation))


@router.post(
    "/{search_answer_profile_id}/approved-faq/suggest",
    response_model=ApiResponse[ApprovedFaqSuggestionsData],
)
async def suggest_search_answer_profile_approved_faq(
    search_answer_profile_id: str, request: ApprovedFaqSuggestRequest
) -> ApiResponse[ApprovedFaqSuggestionsData]:
    """質問に近い承認済み FAQ(類似問)を返す。回答前の提示に使う。"""
    oracle = OracleClient()
    view = await _require_search_answer_profile(oracle, search_answer_profile_id)
    # 検索・チャットと同じく、参照 KB を 1 つも使えない利用者には FAQ の回答を返さない(403。#4618)。
    ensure_search_answer_profile_knowledge_bases_permitted(
        view.config.normalized_knowledge_base_ids()
    )
    settings = get_settings()
    snapshot = await load_approved_faq_snapshot(oracle, search_answer_profile_id)
    ranking = await rank_approved_faq(
        oracle,
        search_answer_profile_id,
        request.query,
        # チャットは最大 3 件 ＋「どれでもない」を出し、どれかを選ぶまで回答しない(#684)。
        limit=min(request.limit, CHAT_APPROVED_FAQ_LIMIT)
        if request.purpose == "chat"
        else request.limit,
        min_score=settings.rag_approved_faq_suggestion_min_score,
        snapshot=snapshot,
        **approved_faq_match_options(settings, lambda: OciGenAiClient(settings=settings)),
    )
    suggestions = list(ranking.suggestions)
    direct_min_score = settings.rag_approved_faq_direct_answer_min_score
    if (
        suggestions
        and approved_faq_direct_answer_enabled(snapshot.payload)
        and is_direct_faq_match(suggestions[0], direct_min_score)
    ):
        # 一致度が下限以上のチャットの質問は、候補を選ばせずに送る。送信の後、補完した質問で
        # 照合し直して FAQ の回答をそのまま返す(#4788)。
        return ApiResponse(data=ApprovedFaqSuggestionsData(direct_answer=True))
    if suggestions:
        # 1 位とほぼ同じくらい近い候補だけを出す(#709)。
        top = max(item.score for item in suggestions)
        suggestions = [
            item
            for item in suggestions
            if item.score >= top - settings.rag_approved_faq_suggestion_max_gap
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
                    direct=is_direct_faq_match(item, direct_min_score),
                )
                for item in suggestions
            ]
        )
    )


# --- 用語・同義語(runtime knowledge)-------------------------------------------


def _runtime_knowledge_data(
    search_answer_profile_id: str, payload: dict[str, object]
) -> RuntimeKnowledgeData:
    return RuntimeKnowledgeData.model_validate(
        {"search_answer_profile_id": search_answer_profile_id, "terms": payload.get("terms") or []}
    )


@router.get(
    "/{search_answer_profile_id}/runtime-knowledge",
    response_model=ApiResponse[RuntimeKnowledgeData],
)
async def get_runtime_knowledge(search_answer_profile_id: str) -> ApiResponse[RuntimeKnowledgeData]:
    """検索・回答プロファイルの用語・同義語を返す。"""
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
    """用語を 1 行追加・更新・削除する。"""
    oracle = OracleClient()
    await _require_search_answer_profile(oracle, search_answer_profile_id)
    try:
        payload = await edit_runtime_knowledge(
            oracle,
            search_answer_profile_id,
            selected=request.selected,
            name=request.name,
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
    """照合テスト: 質問に一致する用語・同義語と拡張後の検索文を返す(保存しない)。"""
    oracle = OracleClient()
    await _require_search_answer_profile(oracle, search_answer_profile_id)
    payload = await load_runtime_knowledge_payload(oracle, search_answer_profile_id)
    context = await asyncio.to_thread(preview_runtime_knowledge, payload, request.question)
    return ApiResponse(
        data=RuntimeKnowledgePreviewData(
            expanded_question=context.expanded_question,
            matched_terms=[term.term for term in context.matched_terms],
        )
    )


@router.post(
    "/{search_answer_profile_id}/clarifications/suggest",
    response_model=ApiResponse[ClarificationSuggestionsData],
)
async def post_clarification_suggest(
    search_answer_profile_id: str, request: ClarificationSuggestRequest
) -> ApiResponse[ClarificationSuggestionsData]:
    """質問に出す確認(業務ガイドの不明・矛盾の条件のうち、選択肢で答えられる最初の 1 件。#1238)。"""
    oracle = OracleClient()
    view = await _require_search_answer_profile(oracle, search_answer_profile_id)
    from app.api.routes.chat import _guide_user_context, prepare_chat_question

    settings, _ = resolve_search_answer_profile_settings(get_settings(), view.config)
    with profile_language_scope(settings):
        query, context_token = await prepare_chat_question(
            oracle,
            request.conversation_id,
            search_answer_profile_id,
            request.query,
            settings,
            force_token=True,
        )

        def response(
            data: ClarificationSuggestionsData,
        ) -> ApiResponse[ClarificationSuggestionsData]:
            data.question_context_token = context_token
            return ApiResponse(data=data)

        # 方針が「前提を示して回答する」なら確認を出さない（#1839）。
        if view.config.clarification_policy == "assume":
            return response(ClarificationSuggestionsData())
        # 業務ガイドの不明・矛盾の条件（選択肢付き）を聞く（#1238）。
        # 適用範囲は検索と同じ手がかりで確かめる（#1278）。
        if not settings.rag_support_guide_enabled:
            return response(ClarificationSuggestionsData())
        # 補完した質問が承認済み FAQ に下限以上で一致するなら、送信の側が FAQ の回答をそのまま返す
        # ので確認も出さない(#4788。利用者が類似問を「どれでもない」と断った質問は照合しない)。
        if not request.approved_faq_declined:
            direct = await match_direct_approved_faq(
                oracle,
                search_answer_profile_id,
                query,
                settings,
                lambda: OciGenAiClient(settings=settings),
            )
            if direct is not None and direct.answer is not None:
                return response(ClarificationSuggestionsData())
        payload = await load_runtime_knowledge_payload(oracle, search_answer_profile_id)
        guides = await _published_guides(oracle, search_answer_profile_id)
        # 文面の補完を OFF にしても、ガイドの既存の利用者履歴の照合は維持する。
        if request.conversation_id and not settings.rag_history_rewrite_enabled:
            query = "\n".join(
                [
                    *_guide_user_context(await oracle.list_messages(request.conversation_id)),
                    query,
                ]
            )
        # 検索と同じく、用語の別名で展開し、ON なら意味の照合を使う（#2398）。
        guide_query = await build_guide_query(
            oracle, settings, search_answer_profile_id, guides, query, payload
        )
        selected = select_guide(
            guides,
            guide_query,
            {},
            context=await support_guide_context(oracle, guides, query, profile_scope_filters(view)),
            close_margin=(
                settings.rag_support_guide_close_match_margin
                if settings.rag_support_guide_close_match_clarification_enabled
                else None
            ),
        )
        selected = await resolve_text_conditions(selected, query, settings)
        guide: tuple[str, str, Any] | None = None
        if isinstance(selected, GuideChoice):
            guide = guide_choice_clarification(selected)
        elif selected is not None:
            guide = guide_clarification(selected)
        if guide is None:
            return response(ClarificationSuggestionsData())
        guide_condition_id, title, guide_question = guide
        return response(
            ClarificationSuggestionsData(
                suggestion=ClarificationSuggestionData(
                    guide_condition_id=guide_condition_id,
                    guide_title=title,
                    clarification=guide_question,
                )
            )
        )
