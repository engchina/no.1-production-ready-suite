"""伝票分類（カテゴリ）API。"""

from fastapi import APIRouter

from app.schemas.category import Category, default_categories
from app.schemas.common import ApiResponse

router = APIRouter()


@router.get("", response_model=ApiResponse[list[Category]])
async def list_categories() -> ApiResponse[list[Category]]:
    """分類一覧を返す。"""
    return ApiResponse(data=default_categories())
