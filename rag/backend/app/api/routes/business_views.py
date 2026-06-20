"""業務アシスタント(Business View)API。作成・一覧・詳細・更新・アーカイブ。

KB が「文書をどう加工して索引するか」を司るのに対し、業務アシスタントは「どの KB 群を
どんな検索/生成方針・persona で束ねて回答するか」を司る利用者視点のエンティティ。
"""

from fastapi import APIRouter, HTTPException, Query

from app.clients.oracle import OracleClient
from app.config import get_settings
from app.db_degradation import load_or_degrade
from app.schemas.business_view import (
    BusinessViewCreateRequest,
    BusinessViewDetail,
    BusinessViewStatus,
    BusinessViewSummary,
    BusinessViewUpdateRequest,
)
from app.schemas.common import ApiResponse, Page

router = APIRouter()


@router.get("", response_model=ApiResponse[Page[BusinessViewSummary]])
async def list_business_views(
    status: BusinessViewStatus | None = None,
    q: str | None = Query(default=None, min_length=1, max_length=200),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
) -> ApiResponse[Page[BusinessViewSummary]]:
    """業務アシスタント一覧を返す。DB 停止時は空一覧 + warning で縮退する。"""
    oracle = OracleClient()
    settings = get_settings()

    async def _load() -> Page[BusinessViewSummary]:
        items = await oracle.list_business_views(status=status, query=q, limit=limit, offset=offset)
        total = await oracle.count_business_views(status=status, query=q)
        return Page(
            items=items,
            total=total,
            limit=limit,
            offset=offset,
            has_next=offset + limit < total,
        )

    empty_page: Page[BusinessViewSummary] = Page(
        items=[], total=0, limit=limit, offset=offset, has_next=False
    )
    page, degraded = await load_or_degrade(
        _load,
        timeout_seconds=settings.db_read_timeout_seconds,
        fallback=empty_page,
        log_label="business_views_list",
    )
    return ApiResponse(
        data=page,
        warning_messages=[degraded.message] if degraded else [],
    )


@router.post("", response_model=ApiResponse[BusinessViewDetail])
async def create_business_view(
    request: BusinessViewCreateRequest,
) -> ApiResponse[BusinessViewDetail]:
    """業務アシスタントを作成する。"""
    created = await OracleClient().create_business_view(
        name=request.name,
        description=request.description,
        config=request.config,
    )
    # 参照 KB 名を解決した詳細を返す。
    detail = await OracleClient().get_business_view(created.id)
    return ApiResponse(data=detail or created)


@router.get("/{business_view_id}", response_model=ApiResponse[BusinessViewDetail])
async def get_business_view(
    business_view_id: str,
) -> ApiResponse[BusinessViewDetail]:
    """業務アシスタント詳細を返す。"""
    detail = await OracleClient().get_business_view(business_view_id)
    if detail is None:
        raise HTTPException(status_code=404, detail="業務アシスタントが見つかりません。")
    return ApiResponse(data=detail)


@router.patch("/{business_view_id}", response_model=ApiResponse[BusinessViewDetail])
async def update_business_view(
    business_view_id: str,
    request: BusinessViewUpdateRequest,
) -> ApiResponse[BusinessViewDetail]:
    """業務アシスタントを更新する。"""
    update_fields = set(request.model_fields_set)
    try:
        await OracleClient().update_business_view(
            business_view_id,
            name=request.name,
            description=request.description,
            config=request.config,
            update_fields=update_fields,
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="業務アシスタントが見つかりません。") from exc
    detail = await OracleClient().get_business_view(business_view_id)
    if detail is None:
        raise HTTPException(status_code=404, detail="業務アシスタントが見つかりません。")
    return ApiResponse(data=detail)


@router.post("/{business_view_id}/archive", response_model=ApiResponse[BusinessViewDetail])
async def archive_business_view(
    business_view_id: str,
) -> ApiResponse[BusinessViewDetail]:
    """業務アシスタントをアーカイブする。参照 KB・文書は変更しない。"""
    try:
        await OracleClient().archive_business_view(business_view_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="業務アシスタントが見つかりません。") from exc
    detail = await OracleClient().get_business_view(business_view_id)
    return ApiResponse(data=detail)
