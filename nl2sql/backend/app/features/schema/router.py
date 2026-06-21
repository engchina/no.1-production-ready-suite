"""Oracle schema catalog router."""

from fastapi import APIRouter
from pr_backend_core import ApiResponse

from app.features.nl2sql.models import CsvImportData, CsvImportRequest, SchemaCatalog
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


@router.post("/import-csv", response_model=ApiResponse[CsvImportData])
async def import_csv(req: CsvImportRequest) -> ApiResponse[CsvImportData]:
    """CSV sample data を Oracle へ投入するための dry-run / 実行 API。

    既定は dry-run。`execute=true` かつ Oracle runtime のときだけ実 DB に反映する。
    """
    return ApiResponse(data=nl2sql_service.import_csv_sample(req))
