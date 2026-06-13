"""API ルーターの集約。"""

from fastapi import APIRouter

from app.api.routes import (
    categories,
    dashboard,
    documents,
    evaluation,
    health,
    search,
    table_browser,
)

api_router = APIRouter()
api_router.include_router(health.router, tags=["health"])
api_router.include_router(dashboard.router, prefix="/dashboard", tags=["dashboard"])
api_router.include_router(documents.router, prefix="/documents", tags=["documents"])
api_router.include_router(categories.router, prefix="/categories", tags=["categories"])
api_router.include_router(search.router, prefix="/search", tags=["search"])
api_router.include_router(evaluation.router, prefix="/evaluation", tags=["evaluation"])
api_router.include_router(table_browser.router, prefix="/table-browser", tags=["table-browser"])
