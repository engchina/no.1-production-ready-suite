"""FastAPI 全 API の fail-closed 認証/RBAC dependency。"""

from __future__ import annotations

from collections.abc import AsyncIterator

from fastapi import HTTPException, Request

from app.api.concurrency import run_sync_io as run_in_threadpool
from app.settings import get_settings

from .domain import SYSTEM_ADMIN_ROLE_CODE, Principal
from .permissions import (
    ALL_PERMISSION_CODES,
    AUTHENTICATED_WITHOUT_PERMISSION,
    PUBLIC_API_PATHS,
    UNCLASSIFIED_PERMISSION,
    permission_for_route,
)
from .request_actor import reset_actor_user_id, set_actor_user_id
from .service import SecurityApiError, get_security_service

LOCAL_DEBUG_USER_ID = "00000000-0000-0000-0000-000000000000"


def local_debug_principal() -> Principal:
    """DB session を作らない local debug 専用 SYSTEM_ADMIN identity。"""
    return Principal(
        user_id=LOCAL_DEBUG_USER_ID,
        login_name="local-debug",
        display_name="ローカル DEBUG 管理者",
        status="ACTIVE",
        force_password_change=False,
        role_codes=[SYSTEM_ADMIN_ROLE_CODE],
        permissions=set(ALL_PERMISSION_CODES),
        data_entitlements=[],
        session_id="local-debug",
        csrf_token_hash="",
    )


async def authorize_api_request(request: Request) -> AsyncIterator[None]:
    settings = get_settings()
    if settings.local_debug_enabled:
        principal = local_debug_principal()
        request.state.principal = principal
        actor_token = set_actor_user_id(principal.user_id)
        try:
            yield
        finally:
            reset_actor_user_id(actor_token)
        return
    if not settings.app_auth_enabled:
        yield
        return
    route = request.scope.get("route")
    route_path = str(getattr(route, "path", request.url.path)).removeprefix("/api")
    if route_path in PUBLIC_API_PATHS:
        yield
        return
    token = request.cookies.get(settings.app_auth_session_cookie_name, "")
    try:
        principal = await run_in_threadpool(get_security_service().authenticate_session, token)
        request.state.principal = principal
        if request.method.upper() not in {"GET", "HEAD", "OPTIONS"}:
            cookie_csrf = request.cookies.get(settings.app_auth_csrf_cookie_name, "")
            header_csrf = request.headers.get("X-CSRF-Token", "")
            await run_in_threadpool(
                get_security_service().verify_csrf,
                principal,
                cookie_csrf,
                header_csrf,
            )
        if principal.force_password_change and route_path not in AUTHENTICATED_WITHOUT_PERMISSION:
            raise SecurityApiError(403, "初回パスワード変更を完了してください。")
        permission = permission_for_route(request.method, route_path)
        if permission == UNCLASSIFIED_PERMISSION:
            raise SecurityApiError(403, "この API は権限一覧に登録されていません。")
        if permission is not None and not principal.has_permission(permission):
            raise SecurityApiError(403, "この機能を利用する権限がありません。")
    except SecurityApiError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.public_message) from exc
    actor_token = set_actor_user_id(principal.user_id)
    try:
        yield
    finally:
        reset_actor_user_id(actor_token)


def current_principal(request: Request) -> Principal:
    principal = getattr(request.state, "principal", None)
    if not isinstance(principal, Principal):
        raise HTTPException(status_code=401, detail="ログインしてください。")
    return principal


def request_context(request: Request) -> tuple[str, str]:
    request_id = request.headers.get("X-Request-ID", "")[:128]
    client_ip = request.client.host[:128] if request.client else ""
    return request_id, client_ip
