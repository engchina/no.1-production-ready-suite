"""Agent の全 API の fail-closed 認証/RBAC dependency と WebSocket の認証（#215 / #750）。

認可の流れは platform の `pr_system_settings.auth.dependencies.authorize_request`（RAG / NL2SQL と
同じ）。ここでは Agent の権限 manifest・Cookie 名・local の扱いを渡す。

判定順（`/api` の HTTP）:

1. local（`AGENT_AUTH_MODE=local`）: 全権限のローカル利用者（`request.state.principal`）。
2. production で公開 path（`/health`・`/ready`・`/ready/database`・`/auth/login`）: そのまま通す。
3. production: 共通認証（DB ユーザー・構成管理者）の session Cookie を検証し、更新系は CSRF を
   照合し、manifest の権限を確認する。Cookie が無ければ 401。router は利用者から `ActorPolicy`
   を作る。
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Mapping
from typing import Any
from urllib.parse import urlsplit

from fastapi import Request, WebSocket
from pr_system_settings.auth import dependencies as platform_dependencies
from pr_system_settings.auth.domain import Principal as PlatformPrincipal
from pr_system_settings.auth.errors import SecurityApiError, SecurityMigrationRequired
from starlette.concurrency import run_in_threadpool
from starlette.requests import HTTPConnection

import app.settings as app_settings

from .domain import LOCAL_DEBUG_USER_UUID, SYSTEM_ADMIN_ROLE_CODE, Principal, as_principal
from .permissions import (
    ALL_PERMISSION_CODES,
    AUTHENTICATED_WITHOUT_PERMISSION,
    OPEN_API_OPERATIONS,
    PUBLIC_API_PATHS,
    UNCLASSIFIED_PERMISSION,
    WEBSOCKET_PERMISSIONS,
    permission_for_route,
    roles_for_permissions,
)
from .service import get_security_service

permission_route_path = platform_dependencies.permission_route_path
request_context = platform_dependencies.request_context

_LOGIN_REQUIRED = "ログインしてください。"
_PERMISSION_DENIED = "この機能を利用する権限がありません。"


def local_debug_principal() -> Principal:
    """DB セッションを作らない local 専用の利用者（全権限・対象範囲の制限なし）。"""
    return Principal(
        user_uuid=LOCAL_DEBUG_USER_UUID,
        login_user_id="local",
        display_name="ローカル利用者",
        status="ACTIVE",
        force_password_change=False,
        role_codes=[SYSTEM_ADMIN_ROLE_CODE],
        permissions=set(ALL_PERMISSION_CODES),
        session_id="local-debug",
        csrf_token_hash="",  # nosec B106 - local はブラウザのセッションを作らない
        password_change_allowed=False,
        allowed_agent_ids=None,
    )


def _enter_actor(_principal: Any) -> None:
    return None


def _exit_actor(_token: Any) -> None:
    return None


async def authorize_api_request(connection: HTTPConnection) -> AsyncIterator[None]:
    """`/api` の全 route の dependency。公開 path 以外はログインと manifest の権限を確認する。

    WebSocket は Request 前提の認可を通さず、handler の中で `authenticate_websocket` を使う。
    """
    if not isinstance(connection, Request):
        yield
        return
    request = connection
    async with platform_dependencies.authorize_request(
        request,
        settings=app_settings.get_settings(),
        service=get_security_service(),
        run_sync=lambda *args: run_in_threadpool(*args),
        permission_for_route=permission_for_route,
        public_paths=PUBLIC_API_PATHS,
        authenticated_without_permission=AUTHENTICATED_WITHOUT_PERMISSION,
        local_debug_principal=local_debug_principal,
        enter_actor=_enter_actor,
        exit_actor=_exit_actor,
        unclassified_permission=UNCLASSIFIED_PERMISSION,
        open_operations=OPEN_API_OPERATIONS,
    ):
        yield


def current_principal(request: Request) -> Principal:
    return platform_dependencies.current_principal(request, Principal)


def actor_roles_for_principal(principal: PlatformPrincipal) -> set[str]:
    """利用者の capability → 従来のロール名（SYSTEM_ADMIN は admin）。"""
    roles = roles_for_permissions(principal.permissions)
    if principal.is_system_admin:
        roles.add("admin")
    return roles


# ---- WebSocket ----


class WebSocketAuthRejected(Exception):
    """WebSocket の認証を拒否する（handler は accept せずに close 1008 する）。"""


def websocket_origin_matches_host(headers: Mapping[str, str]) -> bool:
    """Cookie で認証する WebSocket は、ブラウザの `Origin` が `Host` と一致することを必須にする。

    WebSocket は CSRF header を送れないため、別サイトからの接続（Cross-Site WebSocket Hijacking）を
    Origin で拒否する。
    """
    origin = headers.get("origin", "").strip()
    host = headers.get("host", "").strip().lower()
    if not origin or not host:
        return False
    parsed = urlsplit(origin)
    return parsed.scheme in {"http", "https"} and parsed.netloc.lower() == host


async def authenticate_websocket(websocket: WebSocket, route_path: str) -> Principal:
    """WebSocket の認証（accept 前に呼ぶ）。

    - 戻り値は利用者（`websocket.state.principal` にも入れる）。local はローカル利用者。
    - production で Cookie が無い、Origin 不一致、セッション無効、強制パスワード変更中、
      manifest の権限がないときは `WebSocketAuthRejected`。
    """
    settings = app_settings.get_settings()
    if not settings.app_auth_enabled:
        principal = local_debug_principal()
        websocket.state.principal = principal
        return principal
    token = websocket.cookies.get(settings.app_auth_session_cookie_name, "")
    if not token:
        raise WebSocketAuthRejected(_LOGIN_REQUIRED)
    if not websocket_origin_matches_host(websocket.headers):
        raise WebSocketAuthRejected("接続元（Origin）を確認できません。")
    try:
        principal = as_principal(
            await run_in_threadpool(get_security_service().authenticate_session, token)
        )
    except (SecurityApiError, SecurityMigrationRequired) as exc:
        raise WebSocketAuthRejected(_LOGIN_REQUIRED) from exc
    if principal.force_password_change:
        raise WebSocketAuthRejected("初回パスワード変更を完了してください。")
    permissions = WEBSOCKET_PERMISSIONS.get(route_path, frozenset({UNCLASSIFIED_PERMISSION}))
    if UNCLASSIFIED_PERMISSION in permissions or not principal.has_any_permission(set(permissions)):
        raise WebSocketAuthRejected(_PERMISSION_DENIED)
    websocket.state.principal = principal
    return principal
