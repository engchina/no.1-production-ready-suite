"""業務ルーターの集約（/api 配下に include される）。

`/api` の全 route は `authorize_api_request`（fail-closed の認証/RBAC。#215）を通る。
公開 path（`/health`・`/ready`・`/ready/database`・`/auth/login`）
以外は、production ではログイン（Cookie のセッション）と権限 manifest
（`app.security.permissions`）の権限が必要。
WebSocket は handler の中で認証する（`app.security.dependencies.authenticate_websocket`）。
"""

from fastapi import APIRouter, Depends

from app.features.agent.router import router as agent_router
from app.features.settings.system_tables import router as system_tables_router
from app.security.dependencies import authorize_api_request
from app.security.router import router as security_router

api_router = APIRouter(dependencies=[Depends(authorize_api_request)])
api_router.include_router(security_router)
api_router.include_router(agent_router)
api_router.include_router(system_tables_router)
