"""API ルーターの集約。

`/api` の全 route は `authorize_api_request`（fail-closed の認証/RBAC。#214）を通る。
公開 path（`/health` `/ready` `/ready/database` `/auth/login`）以外はログインと、
権限 manifest（`app.security.permissions`）の権限が必要。未定義の path は 404 のまま。
"""

from fastapi import APIRouter, Depends

from app.api.routes import (
    business_view_knowledge,
    business_views,
    chat,
    documents,
    evaluation,
    feedback,
    health,
    knowledge_bases,
    mcp,
    search,
    services,
    settings,
)
from app.security.dependencies import authorize_api_request
from app.security.router import router as security_router

api_router = APIRouter(dependencies=[Depends(authorize_api_request)])
api_router.include_router(health.router, tags=["health"])
# 認証（/auth/*）・ユーザー管理・ロール管理（platform の共通 router）と RAG の権限管理。
api_router.include_router(security_router)
api_router.include_router(documents.router, prefix="/documents", tags=["documents"])
api_router.include_router(
    knowledge_bases.router,
    prefix="/knowledge-bases",
    tags=["knowledge-bases"],
)
api_router.include_router(
    business_views.router,
    prefix="/business-views",
    tags=["business-views"],
)
api_router.include_router(
    business_view_knowledge.router,
    prefix="/business-views",
    tags=["business-views"],
)
api_router.include_router(chat.router, prefix="/chat", tags=["chat"])
api_router.include_router(search.router, prefix="/search", tags=["search"])
api_router.include_router(evaluation.router, prefix="/evaluation", tags=["evaluation"])
api_router.include_router(feedback.router, prefix="/feedback", tags=["feedback"])
api_router.include_router(settings.router, prefix="/settings", tags=["settings"])
api_router.include_router(services.router, prefix="/services", tags=["services"])
# Agent から利用者として呼ぶ MCP（サービストークンで認証。#232）。
api_router.include_router(mcp.router, prefix="/mcp", tags=["mcp"])
