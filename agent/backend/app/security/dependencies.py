"""Agent の全 API の fail-closed 認証/RBAC dependency と WebSocket の認証（#215 / #750）。

認可の流れは platform の `pr_system_settings.auth.dependencies.authorize_request`（RAG / NL2SQL と
同じ）。ここでは Agent の権限 manifest・Cookie 名・local の扱いを渡す。

判定順（`/api` の HTTP）:

1. local（`AGENT_AUTH_MODE=local`）: 全権限のローカル利用者（`request.state.principal`）。
2. production で公開 path（`/health`・`/ready`・`/ready/database`・`/auth/login`）: そのまま通す。
3. production: 共通認証（DB ユーザー・構成管理者）の session Cookie を検証し、更新系は CSRF を
   照合し、manifest の権限を確認する。Cookie が無ければ 401。router は利用者から `ActorPolicy`
   を作る。
4. `POST /mcp`（#778）: Cookie・CSRF を使わず `Authorization: Bearer` で認証する。`prak_` で始まる
   ものは Agent の API キー（作った利用者として、キーに付けた業務 Agent に絞る）、それ以外は
   共通のサービストークン（audience `agent`。RAG / NL2SQL と同じ）。
"""

from __future__ import annotations

import dataclasses
import logging
from collections.abc import AsyncIterator, Mapping
from typing import Any
from urllib.parse import urlsplit

from fastapi import HTTPException, Request, WebSocket
from pr_system_settings.auth import dependencies as platform_dependencies
from pr_system_settings.auth.domain import Principal as PlatformPrincipal
from pr_system_settings.auth.errors import SecurityApiError, SecurityMigrationRequired
from starlette.concurrency import run_in_threadpool
from starlette.requests import HTTPConnection

import app.settings as app_settings
from app.features.agent.api_keys import api_key_registry, looks_like_api_key, narrow_agent_ids
from app.features.agent.control_plane_store import save_api_key

from .domain import LOCAL_DEBUG_USER_UUID, SYSTEM_ADMIN_ROLE_CODE, Principal, as_principal
from .permissions import (
    ALL_PERMISSION_CODES,
    AUTHENTICATED_WITHOUT_PERMISSION,
    OPEN_API_OPERATIONS,
    PUBLIC_API_PATHS,
    SERVICE_TOKEN_API_PATHS,
    SERVICE_TOKEN_AUDIENCE,
    UNCLASSIFIED_PERMISSION,
    WEBSOCKET_PERMISSIONS,
    permission_for_route,
    roles_for_permissions,
)
from .service import get_security_service

permission_route_path = platform_dependencies.permission_route_path
request_context = platform_dependencies.request_context

logger = logging.getLogger(__name__)

_LOGIN_REQUIRED = "ログインしてください。"
_API_KEY_INVALID = "API キーが無効です（期限切れ・削除済み・誤り）。"
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
    api_key = _mcp_api_key(request)
    if api_key is not None:
        request.state.principal = await _authenticate_api_key(api_key)
        yield
        return
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
        service_token_paths=SERVICE_TOKEN_API_PATHS,
        service_token_audience=SERVICE_TOKEN_AUDIENCE,
        open_operations=OPEN_API_OPERATIONS,
    ):
        yield


def _mcp_api_key(request: Request) -> str | None:
    """`POST /api/mcp` の `Authorization: Bearer prak_…`（Agent の API キー）。それ以外は None。"""
    if request.method != "POST" or request.url.path.rstrip("/") != "/api/mcp":
        return None
    scheme, _, token = request.headers.get("authorization", "").partition(" ")
    token = token.strip()
    if scheme.lower() != "bearer" or not looks_like_api_key(token):
        return None
    return token


async def _authenticate_api_key(token: str) -> Principal:
    """API キーの利用者（作った利用者の現在の権限を、キーに付けた業務 Agent に絞る）。"""
    authenticated = api_key_registry.authenticate(token)
    if authenticated is None:
        raise HTTPException(status_code=401, detail=_API_KEY_INVALID)
    record, persist_last_used = authenticated
    if persist_last_used:
        try:
            await run_in_threadpool(save_api_key, record)
        except Exception:  # noqa: BLE001 - 最後に使った日時の保存は認証を止めない
            logger.warning("agent_api_key_last_used_not_saved", extra={"key_id": record.id})
    settings = app_settings.get_settings()
    if record.owner_user_uuid == LOCAL_DEBUG_USER_UUID:
        # local で作ったキーは local でだけ使える（production のローカル利用者はいない）。
        if settings.app_auth_enabled:
            raise HTTPException(status_code=401, detail=_API_KEY_INVALID)
        owner = local_debug_principal()
    else:
        try:
            owner = as_principal(
                await run_in_threadpool(
                    get_security_service().principal_for_worker, record.owner_user_uuid
                )
            )
        except (SecurityApiError, SecurityMigrationRequired) as exc:
            raise HTTPException(
                status_code=403, detail="API キーを作った利用者の権限を確認できません。"
            ) from exc
    return dataclasses.replace(
        owner,
        allowed_agent_ids=narrow_agent_ids(owner.allowed_agent_ids, record.agent_ids),
        session_id=f"api-key:{record.id}",
    )


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
