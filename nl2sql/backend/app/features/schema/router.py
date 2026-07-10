"""Oracle schema catalog router."""

from fastapi import APIRouter
from pr_backend_core import ApiResponse

from app.features.nl2sql.models import SchemaCatalog
from app.features.nl2sql.service import nl2sql_service

router = APIRouter(prefix="/schema", tags=["schema"])


@router.get("/catalog", response_model=ApiResponse[SchemaCatalog])
async def catalog() -> ApiResponse[SchemaCatalog]:
    """NL2SQL の表/列選択 UI が利用する schema catalog を返す。"""
    return ApiResponse(data=nl2sql_service.get_catalog())


@router.post("/refresh", response_model=ApiResponse[SchemaCatalog])
async def refresh() -> ApiResponse[SchemaCatalog]:
    """Oracle schema catalog を再取得する。

    local skeleton では deterministic sample catalog を返す。
    """
    return ApiResponse(data=nl2sql_service.refresh_catalog())
