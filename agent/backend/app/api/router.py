"""業務ルーターの集約（/api 配下に include される）。

`/api` の全 route は `authorize_api_request`（fail-closed の認証/RBAC。#215）を通る。
公開 path（`/health`・`/ready`・`/ready/database`・`/auth/login`）
以外は、production ではログイン（Cookie のセッション）と権限 manifest
（`app.security.permissions`）の権限が必要。
WebSocket は handler の中で認証する（`app.security.dependencies.authenticate_websocket`）。

認証の前に `require_agent_storage` が、Run・定義の保存先の読み込みを待つ（#1212。DB に接続
できなければ 503。DB の状態・システム設定・認証・ユーザーとロールの API は待たない）。API キーの
認証も読み込んだ定義を使うため、認証より前に置く。
"""

from fastapi import APIRouter, Depends
from starlette.concurrency import run_in_threadpool
from starlette.requests import HTTPConnection, Request

from app.features.agent import storage_bootstrap
from app.features.agent.router import router as agent_router
from app.features.settings.system_tables import router as system_tables_router
from app.security.dependencies import authorize_api_request
from app.security.router import router as security_router


async def require_agent_storage(connection: HTTPConnection) -> None:
    """業務の API の前に、Run の repository と定義を読み込む（済んでいれば何もしない。#1212）。"""
    if storage_bootstrap.is_ready() or not isinstance(connection, Request):
        return
    if not storage_bootstrap.requires_storage(connection.url.path):
        return
    await run_in_threadpool(storage_bootstrap.ensure_ready)


api_router = APIRouter(
    dependencies=[Depends(require_agent_storage), Depends(authorize_api_request)]
)
api_router.include_router(security_router)
api_router.include_router(agent_router)
api_router.include_router(system_tables_router)
