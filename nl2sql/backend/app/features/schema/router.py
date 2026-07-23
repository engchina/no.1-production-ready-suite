"""Oracle schema catalog router."""

from typing import Annotated

from fastapi import APIRouter, Header, HTTPException, Response, status
from pr_backend_core import ApiResponse

from app.features.nl2sql.models import (
    SchemaCatalog,
    SchemaCatalogHead,
    SchemaObjectDetail,
    SchemaObjectPage,
    SchemaOwnersData,
    SchemaRefreshJob,
)
from app.features.nl2sql.service import nl2sql_service

router = APIRouter(prefix="/schema", tags=["schema"])


@router.get("/owners", response_model=ApiResponse[SchemaOwnersData])
def owners() -> ApiResponse[SchemaOwnersData]:
    """現在の接続ユーザーが参照可能な業務 schema 一覧を返す。"""

    return ApiResponse(data=nl2sql_service.get_schema_owners())


@router.get("/catalog", response_model=ApiResponse[SchemaCatalog])
def catalog(response: Response) -> ApiResponse[SchemaCatalog]:
    """NL2SQL の表/列選択 UI が利用する schema catalog を返す。"""
    response.headers["Deprecation"] = "true"
    response.headers["Sunset"] = "Wed, 30 Sep 2026 00:00:00 GMT"
    response.headers["Link"] = '</api/schema/objects>; rel="successor-version"'
    return ApiResponse(data=nl2sql_service.get_catalog())


@router.post("/refresh", response_model=ApiResponse[SchemaCatalog])
def refresh(response: Response) -> ApiResponse[SchemaCatalog]:
    """Oracle schema catalog を再取得する。

    local skeleton では deterministic sample catalog を返す。
    """
    response.headers["Deprecation"] = "true"
    response.headers["Sunset"] = "Wed, 30 Sep 2026 00:00:00 GMT"
    response.headers["Link"] = '</api/schema/refresh-jobs>; rel="successor-version"'
    return ApiResponse(data=nl2sql_service.refresh_catalog())


@router.get("/catalog/head", response_model=ApiResponse[SchemaCatalogHead])
def catalog_head(
    response: Response,
    if_none_match: Annotated[str | None, Header(alias="If-None-Match")] = None,
) -> ApiResponse[SchemaCatalogHead] | Response:
    """Catalog 全体を decode せず active metadata だけを返す。"""
    head = nl2sql_service.get_catalog_head()
    quoted_etag = f'"{head.etag}"'
    if head.etag and if_none_match == quoted_etag:
        return Response(status_code=304, headers={"ETag": quoted_etag})
    if head.etag:
        response.headers["ETag"] = quoted_etag
    return ApiResponse(data=head)


@router.get("/objects", response_model=ApiResponse[SchemaObjectPage])
def search_objects(
    response: Response,
    cursor: str | None = None,
    limit: int = 50,
    q: str = "",
    owner: str = "",
    type: str = "",  # noqa: A002 - public query parameter name
    row_state: str = "",
    profile_id: str | None = None,
    if_none_match: Annotated[str | None, Header(alias="If-None-Match")] = None,
) -> ApiResponse[SchemaObjectPage] | Response:
    """Schema picker 用 keyset page。"""
    if limit < 1 or limit > 100:
        raise HTTPException(status_code=422, detail="limit は 1 から 100 で指定してください。")
    if row_state not in {"", "all", "with_rows", "empty_rows", "unknown_rows"}:
        raise HTTPException(status_code=422, detail="row_state が不正です。")
    page = nl2sql_service.search_schema_objects(
        cursor=cursor,
        limit=limit,
        query=q,
        owner=owner,
        object_type=type,
        profile_id=profile_id,
        row_state=row_state,
    )
    quoted_etag = f'"schema-{page.catalog_version}"'
    if if_none_match == quoted_etag:
        return Response(status_code=304, headers={"ETag": quoted_etag})
    response.headers["ETag"] = quoted_etag
    return ApiResponse(data=page)


@router.get(
    "/objects/{owner}/{object_name}", response_model=ApiResponse[SchemaObjectDetail]
)
def object_detail(
    owner: str,
    object_name: str,
    response: Response,
    if_none_match: Annotated[str | None, Header(alias="If-None-Match")] = None,
) -> ApiResponse[SchemaObjectDetail] | Response:
    """選択 object の columns/constraints/dependency だけを返す。"""
    detail = nl2sql_service.get_schema_object(owner, object_name)
    if detail is None:
        raise HTTPException(status_code=404, detail="Schema object が見つかりません。")
    quoted_etag = f'"{detail.etag}"'
    if detail.etag and if_none_match == quoted_etag:
        return Response(status_code=304, headers={"ETag": quoted_etag})
    if detail.etag:
        response.headers["ETag"] = quoted_etag
    return ApiResponse(data=detail)


@router.post(
    "/refresh-jobs",
    response_model=ApiResponse[SchemaRefreshJob],
    status_code=status.HTTP_202_ACCEPTED,
)
def start_refresh_job() -> ApiResponse[SchemaRefreshJob]:
    """Schema refresh を永続 job として投入し即時に返す。"""
    return ApiResponse(data=nl2sql_service.start_schema_refresh_job())


@router.get("/refresh-jobs/{job_id}", response_model=ApiResponse[SchemaRefreshJob])
def refresh_job(job_id: str) -> ApiResponse[SchemaRefreshJob]:
    job = nl2sql_service.get_schema_refresh_job(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Schema refresh job が見つかりません。")
    return ApiResponse(data=job)
