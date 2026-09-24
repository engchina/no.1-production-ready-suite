"""業務ビュー単位の知識 API(ドメインキーワード)。

rag_poc(DocRAG)の「ドメインキーワード管理」を業務ビュー層へ移植したもの。
KB・文書レシピには持たせず、検索時は業務ビューのキーワードだけを使う。
"""

from fastapi import APIRouter, HTTPException, Query

from app.clients.oracle import OracleClient
from app.rag.business_view_knowledge import (
    load_domain_keywords,
    save_domain_keywords,
    suggest_domain_keywords,
)
from app.schemas.business_view import BusinessViewDetail
from app.schemas.business_view_knowledge import (
    DomainKeywordCandidateData,
    DomainKeywordsData,
    DomainKeywordSuggestionData,
    DomainKeywordsUpdate,
)
from app.schemas.common import ApiResponse

router = APIRouter()


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
