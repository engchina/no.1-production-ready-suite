"""ログイン・ログアウト API。"""

import hmac

from fastapi import APIRouter, HTTPException, Request, Response, status

from app.auth import (
    AUTH_CONFIG_ERROR_MESSAGE,
    AuthSession,
    auth_config_is_complete,
    auth_is_enabled,
    build_auth_session,
    clear_auth_cookie,
    local_session,
    read_auth_session,
    set_auth_cookie,
)
from app.config import Settings, get_settings
from app.schemas.auth import AuthStatus, AuthUser, LoginRequest
from app.schemas.common import ApiResponse

router = APIRouter()


@router.get("/me", response_model=ApiResponse[AuthStatus])
async def me(request: Request) -> ApiResponse[AuthStatus]:
    """現在の認証状態を返す。local mode では常に認証済み扱いにする。"""
    settings = get_settings()
    return ApiResponse(data=_auth_status(request, settings))


@router.post("/login", response_model=ApiResponse[AuthStatus])
async def login(
    payload: LoginRequest,
    request: Request,
    response: Response,
) -> ApiResponse[AuthStatus]:
    """ユーザー名・パスワードでログインする。"""
    settings = get_settings()
    if not auth_is_enabled(settings):
        return ApiResponse(data=_local_auth_status(settings))
    if not auth_config_is_complete(settings):
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=AUTH_CONFIG_ERROR_MESSAGE,
        )
    if not payload.username or not payload.password:
        raise HTTPException(status_code=400, detail="ユーザー名とパスワードは必須です。")

    if not (
        hmac.compare_digest(payload.username, settings.auth_username)
        and hmac.compare_digest(payload.password, settings.auth_password)
    ):
        raise HTTPException(
            status_code=401,
            detail="ユーザー名またはパスワードが正しくありません。",
        )

    session = build_auth_session(settings, payload.username, payload.remember_me)
    set_auth_cookie(response, settings, session)
    request.state.auth_session = session
    return ApiResponse(data=_status_from_session(settings.auth_mode, True, session))


@router.post("/logout", response_model=ApiResponse[AuthStatus])
async def logout(request: Request, response: Response) -> ApiResponse[AuthStatus]:
    """ログアウトし、セッション Cookie を削除する。"""
    settings = get_settings()
    clear_auth_cookie(response, settings)
    if not auth_is_enabled(settings):
        return ApiResponse(data=_local_auth_status(settings))
    request.state.auth_session = None
    return ApiResponse(
        data=AuthStatus(mode=settings.auth_mode, auth_required=True, authenticated=False)
    )


def _auth_status(request: Request, settings: Settings) -> AuthStatus:
    if not auth_is_enabled(settings):
        return _local_auth_status(settings)
    session = read_auth_session(request, settings)
    if session is None:
        return AuthStatus(mode=settings.auth_mode, auth_required=True, authenticated=False)
    return _status_from_session(settings.auth_mode, True, session)


def _local_auth_status(settings: Settings) -> AuthStatus:
    return _status_from_session(settings.auth_mode, False, local_session())


def _status_from_session(mode: str, auth_required: bool, session: AuthSession) -> AuthStatus:
    return AuthStatus(
        mode=mode,
        auth_required=auth_required,
        authenticated=True,
        user=AuthUser(id=session.user_id, name=session.username, role=session.role),
        expires_at=session.expires_at or None,
    )
