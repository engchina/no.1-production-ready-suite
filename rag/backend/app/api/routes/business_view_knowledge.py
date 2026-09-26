"""業務ビュー単位の知識 API(ドメインキーワード)。

rag_poc(DocRAG)の「ドメインキーワード管理」を業務ビュー層へ移植したもの。
KB・文書レシピには持たせず、検索時は業務ビューのキーワードだけを使う。
"""

import asyncio
from typing import Annotated

from docrag.knowledge.approved_faq import ApprovedFaqImportRow, ApprovedFaqRecord
from fastapi import APIRouter, File, Form, HTTPException, Query, UploadFile

from app.clients.oci_genai import OciGenAiClient
from app.clients.oracle import OracleClient
from app.config import get_settings
from app.rag.business_view_knowledge import (
    APPROVED_FAQ_PREVIEW_ROWS,
    ApprovedFaqMutation,
    add_approved_faq,
    delete_approved_faq,
    edit_runtime_knowledge,
    import_approved_faq,
    is_direct_faq_match,
    load_approved_faq,
    load_domain_keywords,
    load_runtime_knowledge_payload,
    preview_runtime_knowledge,
    read_approved_faq_excel,
    save_domain_keywords,
    suggest_approved_faq,
    suggest_domain_keywords,
)
from app.rag.query_history import query_history_suggestions
from app.schemas.business_view import BusinessViewDetail
from app.schemas.business_view_knowledge import (
    ApprovedFaqAddRequest,
    ApprovedFaqDeleteRequest,
    ApprovedFaqImportPreviewData,
    ApprovedFaqImportRowData,
    ApprovedFaqListData,
    ApprovedFaqMutationData,
    ApprovedFaqRecordData,
    ApprovedFaqSuggestionData,
    ApprovedFaqSuggestionsData,
    ApprovedFaqSuggestRequest,
    DomainKeywordCandidateData,
    DomainKeywordsData,
    DomainKeywordSuggestionData,
    DomainKeywordsUpdate,
    QuerySuggestion,
    QuerySuggestionsData,
    RuntimeKnowledgeData,
    RuntimeKnowledgeEditRequest,
    RuntimeKnowledgePreviewData,
    RuntimeKnowledgePreviewRequest,
)
from app.schemas.common import ApiResponse

router = APIRouter()
MAX_FAQ_EXCEL_BYTES = 10 * 1024 * 1024


async def _require_business_view(oracle: OracleClient, business_view_id: str) -> BusinessViewDetail:
    view = await oracle.get_business_view(business_view_id)
    if view is None:
        raise HTTPException(status_code=404, detail="業務ビューが見つかりません。")
    return view


@router.get(
    "/{business_view_id}/domain-keywords",
    response_model=ApiResponse[DomainKeywordsData],
)
async def get_domain_keywords(business_view_id: str) -> ApiResponse[DomainKeywordsData]:
    """業務ビューのドメインキーワードを返す。"""
    oracle = OracleClient()
    await _require_business_view(oracle, business_view_id)
    keywords = await load_domain_keywords(oracle, business_view_id)
    return ApiResponse(
        data=DomainKeywordsData(business_view_id=business_view_id, keywords=keywords)
    )


@router.put(
    "/{business_view_id}/domain-keywords",
    response_model=ApiResponse[DomainKeywordsData],
)
async def put_domain_keywords(
    business_view_id: str,
    request: DomainKeywordsUpdate,
) -> ApiResponse[DomainKeywordsData]:
    """ドメインキーワードを全置換で保存する。"""
    oracle = OracleClient()
    await _require_business_view(oracle, business_view_id)
    try:
        keywords = await save_domain_keywords(oracle, business_view_id, request.keywords)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return ApiResponse(
        data=DomainKeywordsData(business_view_id=business_view_id, keywords=keywords)
    )


@router.post(
    "/{business_view_id}/domain-keywords/suggest",
    response_model=ApiResponse[DomainKeywordSuggestionData],
)
async def suggest_business_view_domain_keywords(
    business_view_id: str,
    limit: int = Query(default=50, ge=1, le=200),
) -> ApiResponse[DomainKeywordSuggestionData]:
    """参照 KB の配信中チャンクからキーワード候補を返す(保存はしない)。"""
    oracle = OracleClient()
    view = await _require_business_view(oracle, business_view_id)
    result = await suggest_domain_keywords(
        oracle,
        business_view_id,
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
    business_view_id: str, mutation: ApprovedFaqMutation
) -> ApprovedFaqMutationData:
    return ApprovedFaqMutationData(
        business_view_id=business_view_id,
        records=[_faq_record_data(record) for record in mutation.records],
        inserted_count=mutation.inserted_count,
        deleted_count=mutation.deleted_count,
    )


@router.get(
    "/{business_view_id}/approved-faq",
    response_model=ApiResponse[ApprovedFaqListData],
)
async def get_approved_faq(business_view_id: str) -> ApiResponse[ApprovedFaqListData]:
    """業務ビューの承認済み FAQ 一覧を返す。"""
    oracle = OracleClient()
    await _require_business_view(oracle, business_view_id)
    records = await load_approved_faq(oracle, business_view_id)
    return ApiResponse(
        data=ApprovedFaqListData(
            business_view_id=business_view_id,
            records=[_faq_record_data(record) for record in records],
        )
    )


@router.post(
    "/{business_view_id}/approved-faq",
    response_model=ApiResponse[ApprovedFaqMutationData],
)
async def post_approved_faq(
    business_view_id: str, request: ApprovedFaqAddRequest
) -> ApiResponse[ApprovedFaqMutationData]:
    """FAQ を 1 件追加する(同じ質問は置き換え)。"""
    oracle = OracleClient()
    await _require_business_view(oracle, business_view_id)
    try:
        mutation = await add_approved_faq(
            oracle, business_view_id, question=request.question, answer=request.answer
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return ApiResponse(data=_faq_mutation_data(business_view_id, mutation))


@router.post(
    "/{business_view_id}/approved-faq/delete",
    response_model=ApiResponse[ApprovedFaqMutationData],
)
async def delete_business_view_approved_faq(
    business_view_id: str, request: ApprovedFaqDeleteRequest
) -> ApiResponse[ApprovedFaqMutationData]:
    """ID を指定して FAQ を削除する。"""
    oracle = OracleClient()
    await _require_business_view(oracle, business_view_id)
    try:
        mutation = await delete_approved_faq(oracle, business_view_id, request.ids)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return ApiResponse(data=_faq_mutation_data(business_view_id, mutation))


async def _excel_rows(file: UploadFile) -> list[ApprovedFaqImportRow]:
    content = await file.read()
    if len(content) > MAX_FAQ_EXCEL_BYTES:
        raise HTTPException(status_code=413, detail="Excel ファイルが大きすぎます(上限 10MB)。")
    try:
        return await asyncio.to_thread(read_approved_faq_excel, content, file.filename or "")
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.post(
    "/{business_view_id}/approved-faq/import/preview",
    response_model=ApiResponse[ApprovedFaqImportPreviewData],
)
async def preview_approved_faq_import(
    business_view_id: str, file: Annotated[UploadFile, File()]
) -> ApiResponse[ApprovedFaqImportPreviewData]:
    """Excel(QUESTION / ANSWER 列)の取込内容を先頭 10 件だけ返す(保存しない)。"""
    await _require_business_view(OracleClient(), business_view_id)
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
    "/{business_view_id}/approved-faq/import",
    response_model=ApiResponse[ApprovedFaqMutationData],
)
async def import_business_view_approved_faq(
    business_view_id: str,
    file: Annotated[UploadFile, File()],
    mode: Annotated[str, Form()] = "INSERT",
) -> ApiResponse[ApprovedFaqMutationData]:
    """Excel から FAQ を取り込む。INSERT は同じ質問をスキップ、DELETE_THEN_INSERT は置き換え。"""
    oracle = OracleClient()
    await _require_business_view(oracle, business_view_id)
    rows = await _excel_rows(file)
    try:
        mutation = await import_approved_faq(oracle, business_view_id, rows, mode=mode)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return ApiResponse(data=_faq_mutation_data(business_view_id, mutation))


@router.post(
    "/{business_view_id}/approved-faq/suggest",
    response_model=ApiResponse[ApprovedFaqSuggestionsData],
)
async def suggest_business_view_approved_faq(
    business_view_id: str, request: ApprovedFaqSuggestRequest
) -> ApiResponse[ApprovedFaqSuggestionsData]:
    """質問に近い承認済み FAQ(類似問)を返す。回答前の提示に使う。"""
    oracle = OracleClient()
    await _require_business_view(oracle, business_view_id)
    settings = get_settings()
    genai = (
        OciGenAiClient(settings=settings) if settings.rag_approved_faq_semantic_enabled else None
    )
    suggestions = await suggest_approved_faq(
        oracle,
        business_view_id,
        request.query,
        limit=request.limit,
        embed=(
            (lambda texts, input_type: genai.embed(texts, input_type=input_type))
            if genai is not None
            else None
        ),
        embedding_model=settings.oci_genai_embedding_model,
        embedding_dimensions=settings.oci_genai_embedding_dim,
    )
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
    business_view_id: str, payload: dict[str, object]
) -> RuntimeKnowledgeData:
    return RuntimeKnowledgeData.model_validate(
        {
            "business_view_id": business_view_id,
            "terms": payload.get("terms") or [],
            "rules": payload.get("rules") or [],
        }
    )


@router.get(
    "/{business_view_id}/runtime-knowledge",
    response_model=ApiResponse[RuntimeKnowledgeData],
)
async def get_runtime_knowledge(business_view_id: str) -> ApiResponse[RuntimeKnowledgeData]:
    """業務ビューの用語・ルールを返す。"""
    oracle = OracleClient()
    await _require_business_view(oracle, business_view_id)
    payload = await load_runtime_knowledge_payload(oracle, business_view_id)
    return ApiResponse(data=_runtime_knowledge_data(business_view_id, payload))


@router.post(
    "/{business_view_id}/runtime-knowledge/edit",
    response_model=ApiResponse[RuntimeKnowledgeData],
)
async def post_runtime_knowledge_edit(
    business_view_id: str, request: RuntimeKnowledgeEditRequest
) -> ApiResponse[RuntimeKnowledgeData]:
    """用語またはルールを 1 行追加・更新・削除する。"""
    oracle = OracleClient()
    await _require_business_view(oracle, business_view_id)
    try:
        payload = await edit_runtime_knowledge(
            oracle,
            business_view_id,
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
    return ApiResponse(data=_runtime_knowledge_data(business_view_id, payload))


@router.post(
    "/{business_view_id}/runtime-knowledge/preview",
    response_model=ApiResponse[RuntimeKnowledgePreviewData],
)
async def post_runtime_knowledge_preview(
    business_view_id: str, request: RuntimeKnowledgePreviewRequest
) -> ApiResponse[RuntimeKnowledgePreviewData]:
    """照合テスト: 質問に一致する用語・ルールと拡張後の検索文を返す(保存しない)。"""
    oracle = OracleClient()
    await _require_business_view(oracle, business_view_id)
    payload = await load_runtime_knowledge_payload(oracle, business_view_id)
    context = await asyncio.to_thread(preview_runtime_knowledge, payload, request.question)
    return ApiResponse(
        data=RuntimeKnowledgePreviewData(
            expanded_question=context.expanded_question,
            matched_terms=[term.term for term in context.matched_terms],
            matched_rules=[rule.title or rule.rule_id for rule in context.matched_rules],
        )
    )


@router.get(
    "/{business_view_id}/query-suggestions",
    response_model=ApiResponse[QuerySuggestionsData],
)
async def get_query_suggestions(
    business_view_id: str,
    q: str = Query(default="", max_length=500),
    large_category: str = Query(default="", max_length=200),
    middle_category: str = Query(default="", max_length=200),
    small_category: str = Query(default="", max_length=200),
) -> ApiResponse[QuerySuggestionsData]:
    """業務ビューでよく聞かれる質問を、入力中の質問との類似度順に返す(質問履歴が有効なときだけ)。"""
    settings = get_settings()
    oracle = OracleClient()
    await _require_business_view(oracle, business_view_id)
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
        business_view_id=business_view_id,
        question=q,
        classification=classification,
    )
    return ApiResponse(
        data=QuerySuggestionsData(
            business_view_id=business_view_id,
            enabled=settings.rag_query_history_enabled,
            suggestions=[
                QuerySuggestion(question=item.question, count=item.count) for item in suggestions
            ],
        )
    )
