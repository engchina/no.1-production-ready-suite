"""Agent の全 API の fail-closed 認証/RBAC dependency と WebSocket の認証（#215）。

認可の流れは platform の `pr_system_settings.auth.dependencies.authorize_request`。
ここでは Agent の権限 manifest・Cookie 名・local の扱いと、外部連携（Cookie のないリクエスト）の
判定を渡す。

判定順（`/api` の HTTP）:

1. local（`AGENT_AUTH_MODE=local`）: 全権限のローカル利用者（`request.state.principal`）。
   router の RBAC は従来どおり `AGENT_RBAC_ENABLED` の header / JWT / 外部 policy に従う
   （ローカル利用者は router の RBAC に使わない）。
2. production で公開 path（`/health`・`/ready`・`/auth/login`・`POST /mcp/{binding_id}`）:
   そのまま通す。
3. production で session Cookie あり: 共通認証（DB ユーザー・構成管理者）でセッションを検証し、
   更新系は CSRF を照合し、manifest の権限を確認する。router は利用者から `ActorPolicy` を作る
   （header の RBAC 情報は使わない）。
4. production で session Cookie なし: `AGENT_RBAC_ENABLED=true` かつ信頼できる identity
   （HMAC 署名 header・JWT bearer・外部 policy URL のどれか）が設定されているときだけ、そのロールで
   manifest を確認する（外部連携用）。`X-Agent-Roles` / `X-Agent-Actor` の自己申告は信じず、
   それ以外は 401。
"""

from __future__ import annotations

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

from .domain import LOCAL_DEBUG_USER_UUID, SYSTEM_ADMIN_ROLE_CODE, Principal, as_principal
from .permissions import (
    ALL_PERMISSION_CODES,
    AUTHENTICATED_WITHOUT_PERMISSION,
    PUBLIC_API_PATHS,
    UNCLASSIFIED_PERMISSION,
    WEBSOCKET_PERMISSIONS,
    permission_for_route,
    permissions_for_roles,
    roles_for_permissions,
)
from .service import get_security_service

permission_route_path = platform_dependencies.permission_route_path
request_context = platform_dependencies.request_context

_LOGIN_REQUIRED = "ログインしてください。"
_PERMISSION_DENIED = "この機能を利用する権限がありません。"
_UNCLASSIFIED = "この API は権限一覧に登録されていません。"


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
        allowed_business_view_ids=None,
    )


def _enter_actor(_principal: Any) -> None:
    return None


def _exit_actor(_token: Any) -> None:
    return None


def trusted_external_identity_configured(settings: Any) -> bool:
    """信頼できる identity が設定されているか（HMAC 署名 header・JWT bearer・外部 policy URL）。

    router の `_trusted_policy_required` と同じ条件。これが偽のときの `X-Agent-Roles` /
    `X-Agent-Actor`（と `AGENT_RBAC_ACTOR_POLICIES_JSON` の actor header 引き）は
    自己申告で信頼できない。
    """
    return bool(
        getattr(settings, "agent_rbac_identity_hmac_secret", None)
        or getattr(settings, "agent_rbac_jwt_bearer_enabled", False)
        or getattr(settings, "agent_rbac_policy_url", None)
    )


def external_rbac_accepted(settings: Any) -> bool:
    """production で Cookie のないリクエストを外部連携の RBAC で受け付けるか。

    `AGENT_RBAC_ENABLED=true` かつ信頼できる identity が設定されているときだけ。
    """
    return bool(settings.agent_rbac_enabled) and trusted_external_identity_configured(settings)


def untrusted_external_rbac_warning(settings: Any) -> str | None:
    """信頼できる identity なしで外部連携の RBAC が有効なときの警告文（起動時に出す）。"""
    if (
        getattr(settings, "app_auth_enabled", False)
        and settings.agent_rbac_enabled
        and not trusted_external_identity_configured(settings)
    ):
        return (
            "AGENT_AUTH_MODE=production で AGENT_RBAC_ENABLED=true ですが、信頼できる identity"
            "（AGENT_RBAC_IDENTITY_HMAC_SECRET / AGENT_RBAC_JWT_BEARER_ENABLED /"
            " AGENT_RBAC_POLICY_URL）"
            "が設定されていません。Cookie のないリクエストの X-Agent-Roles / X-Agent-Actor は"
            "自己申告のため受け付けず、401 にします。"
        )
    return None


def _uses_external_actor(request: Request) -> bool:
    """production で session Cookie がなく、信頼できる外部連携の RBAC が有効なとき。"""
    settings = app_settings.get_settings()
    return (
        settings.app_auth_enabled
        and external_rbac_accepted(settings)
        and not request.cookies.get(settings.app_auth_session_cookie_name, "")
    )


async def authorize_external_actor(request: Request) -> None:
    """外部連携（header / JWT / 外部 policy）のロールで manifest を確認する（既定拒否）。"""
    route_path = permission_route_path(request)
    if route_path in PUBLIC_API_PATHS:
        return
    permissions = permission_for_route(request.method, route_path)
    if permissions is None or route_path in AUTHENTICATED_WITHOUT_PERMISSION:
        # ログインだけで使える API（/auth/me など）は Cookie のセッションが必要。
        raise HTTPException(status_code=401, detail=_LOGIN_REQUIRED)
    if UNCLASSIFIED_PERMISSION in permissions:
        raise HTTPException(status_code=403, detail=_UNCLASSIFIED)
    # router は security を import するため、循環を避けて呼出し時に読む。
    from app.features.agent import router as agent_router

    roles = await run_in_threadpool(agent_router.external_actor_roles, request)
    granted = permissions_for_roles(roles)
    if not granted:
        raise HTTPException(status_code=401, detail=_LOGIN_REQUIRED)
    if not granted.intersection(permissions):
        raise HTTPException(status_code=403, detail=_PERMISSION_DENIED)


async def authorize_api_request(connection: HTTPConnection) -> AsyncIterator[None]:
    """`/api` の全 route の dependency。公開 path 以外はログインと manifest の権限を確認する。

    WebSocket は Request 前提の認可を通さず、handler の中で `authenticate_websocket` を使う。
    """
    if not isinstance(connection, Request):
        yield
        return
    request = connection
    if _uses_external_actor(request):
        await authorize_external_actor(request)
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
    ):
        yield


def current_principal(request: Request) -> Principal:
    return platform_dependencies.current_principal(request, Principal)


def session_principal(connection: object) -> Principal | None:
    """Cookie のセッションで認証した利用者（local のローカル利用者は含まない）。

    router の RBAC はこの利用者があれば利用者から、なければ従来の header / JWT から判定する。
    """
    state = getattr(connection, "state", None)
    principal = getattr(state, "principal", None) if state is not None else None
    if isinstance(principal, Principal) and principal.user_uuid != LOCAL_DEBUG_USER_UUID:
        return principal
    return None


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


async def authenticate_websocket(websocket: WebSocket, route_path: str) -> Principal | None:
    """production の WebSocket の認証（accept 前に呼ぶ）。

    - 戻り値は Cookie のセッションの利用者（`websocket.state.principal` にも入れる）。
    - None は従来の判定を使う（local、または production で Cookie がなく
      `AGENT_RBAC_ENABLED=true` のとき）。
    - Cookie がなく外部連携の RBAC も無効、Origin 不一致、セッション無効、強制パスワード変更中、
      manifest の権限がないときは `WebSocketAuthRejected`。
    """
    settings = app_settings.get_settings()
    if not settings.app_auth_enabled:
        return None
    token = websocket.cookies.get(settings.app_auth_session_cookie_name, "")
    if not token:
        if external_rbac_accepted(settings):
            return None
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
