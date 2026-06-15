"""API ルーターの集約。"""

from fastapi import APIRouter

from app.api.routes import (
    auth,
    dashboard,
    documents,
    evaluation,
    health,
    knowledge_bases,
    search,
    settings,
)

api_router = APIRouter()
api_router.include_router(health.router, tags=["health"])
api_router.include_router(auth.router, prefix="/auth", tags=["auth"])
api_router.include_router(dashboard.router, prefix="/dashboard", tags=["dashboard"])
api_router.include_router(documents.router, prefix="/documents", tags=["documents"])
api_router.include_router(
    knowledge_bases.router,
    prefix="/knowledge-bases",
    tags=["knowledge-bases"],
)
api_router.include_router(search.router, prefix="/search", tags=["search"])
api_router.include_router(evaluation.router, prefix="/evaluation", tags=["evaluation"])
api_router.include_router(settings.router, prefix="/settings", tags=["settings"])
