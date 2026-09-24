"""Cookie セッションベースの認証ヘルパー。"""

import base64
import binascii
import hashlib
import hmac
import json
import time
from dataclasses import dataclass
from typing import Any

from fastapi import Request
from starlette.responses import JSONResponse, Response

from app.config import Settings
from app.schemas.common import ApiResponse

AUTH_ERROR_MESSAGE = "ログインしてください。"
AUTH_CONFIG_ERROR_MESSAGE = (
    "認証設定が不足しています。"
    "AUTH_USERNAME、AUTH_PASSWORD、AUTH_SESSION_SECRET を確認してください。"
)
AUTH_COOKIE_PATH = "/"
AUTH_EXEMPT_PATHS = {"/api/health", "/api/ready"}
AUTH_EXEMPT_PREFIXES = ("/api/auth",)


@dataclass(frozen=True)
class AuthSession:
    """署名済み Cookie から復元したセッション。"""

    user_id: str
    username: str
    role: str
    expires_at: int
    remember_me: bool


def auth_is_enabled(settings: Settings) -> bool:
    """local mode では認証を無効化し、production mode では有効化する。"""
    return settings.auth_mode == "production"


def auth_config_is_complete(settings: Settings) -> bool:
    """production 認証に必要な secret 設定が揃っているか確認する。"""
    return all(
        value.strip()
        for value in (settings.auth_username, settings.auth_password, settings.auth_session_secret)
    )


def local_session() -> AuthSession:
    """local mode 用の仮想ログインユーザー。"""
    return AuthSession(
        user_id="local-user",
        username="local",
        role="LOCAL",
        expires_at=0,
        remember_me=False,
    )


def prepare_auth_request(request: Request, settings: Settings) -> Response | None:
    """保護 API の認証を確認し、不許可なら ApiResponse 形式で返す。"""
    if not auth_is_enabled(settings):
        request.state.auth_session = local_session()
        return None

    path = request.url.path
    if _is_auth_exempt(path):
        request.state.auth_session = read_auth_session(request, settings)
        return None

    if not auth_config_is_complete(settings):
        return _auth_error_response(503, AUTH_CONFIG_ERROR_MESSAGE)

    session = read_auth_session(request, settings)
    if session is None:
        return _auth_error_response(401, AUTH_ERROR_MESSAGE)

    request.state.auth_session = session
    request.state.refresh_auth_cookie = True
    return None


def attach_refreshed_auth_cookie(response: Response, request: Request, settings: Settings) -> None:
    """認証済み API 呼び出し後にセッション期限を延長する。"""
    if not auth_is_enabled(settings) or not getattr(request.state, "refresh_auth_cookie", False):
        return
    session = getattr(request.state, "auth_session", None)
    if not isinstance(session, AuthSession):
        return
    refreshed = AuthSession(
        user_id=session.user_id,
        username=session.username,
        role=session.role,
        expires_at=_expiry_timestamp(settings),
        remember_me=session.remember_me,
    )
    set_auth_cookie(response, settings, refreshed)


def read_auth_session(request: Request, settings: Settings) -> AuthSession | None:
    """リクエスト Cookie からセッションを検証して返す。"""
    if not auth_config_is_complete(settings):
        return None
    cookie_value = request.cookies.get(settings.auth_cookie_name)
    if not cookie_value:
        return None
    payload = _decode_signed_payload(cookie_value, settings.auth_session_secret)
    if payload is None:
        return None
    try:
        expires_at = int(payload["expires_at"])
        username = str(payload["username"])
        role = str(payload.get("role") or "ADMIN")
        user_id = str(payload.get("user_id") or "admin-user-id")
        remember_me = bool(payload.get("remember_me", True))
    except (KeyError, TypeError, ValueError):
        return None
    if expires_at <= int(time.time()):
        return None
    return AuthSession(
        user_id=user_id,
        username=username,
        role=role,
        expires_at=expires_at,
        remember_me=remember_me,
    )


def build_auth_session(settings: Settings, username: str, remember_me: bool) -> AuthSession:
    """ログイン成功時のセッションを作成する。"""
    return AuthSession(
        user_id="admin-user-id",
        username=username,
        role="ADMIN",
        expires_at=_expiry_timestamp(settings),
        remember_me=remember_me,
    )


def set_auth_cookie(response: Response, settings: Settings, session: AuthSession) -> None:
    """署名済みセッション Cookie を設定する。"""
    value = _encode_signed_payload(
        {
            "user_id": session.user_id,
            "username": session.username,
            "role": session.role,
            "expires_at": session.expires_at,
            "remember_me": session.remember_me,
        },
        settings.auth_session_secret,
    )
    max_age = settings.auth_session_timeout_seconds if session.remember_me else None
    response.set_cookie(
        key=settings.auth_cookie_name,
        value=value,
        max_age=max_age,
        httponly=True,
        secure=settings.auth_cookie_secure,
        samesite="lax",
        path=AUTH_COOKIE_PATH,
    )


def clear_auth_cookie(response: Response, settings: Settings) -> None:
    """セッション Cookie を削除する。"""
    response.delete_cookie(
        key=settings.auth_cookie_name,
        path=AUTH_COOKIE_PATH,
        httponly=True,
        secure=settings.auth_cookie_secure,
        samesite="lax",
    )


def _expiry_timestamp(settings: Settings) -> int:
    return int(time.time()) + settings.auth_session_timeout_seconds


def _is_auth_exempt(path: str) -> bool:
    return path in AUTH_EXEMPT_PATHS or path.startswith(AUTH_EXEMPT_PREFIXES)


def _auth_error_response(status_code: int, message: str) -> JSONResponse:
    body = ApiResponse[object](data=None, error_messages=[message])
    return JSONResponse(status_code=status_code, content=body.model_dump(mode="json"))


def _encode_signed_payload(payload: dict[str, Any], secret: str) -> str:
    raw_payload = json.dumps(payload, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
    payload_part = _base64url_encode(raw_payload.encode("utf-8"))
    signature = _sign(payload_part, secret)
    return f"{payload_part}.{signature}"


def _decode_signed_payload(value: str, secret: str) -> dict[str, Any] | None:
    payload_part, separator, signature = value.partition(".")
    if not separator:
        return None
    expected = _sign(payload_part, secret)
    if not hmac.compare_digest(signature, expected):
        return None
    try:
        decoded = _base64url_decode(payload_part).decode("utf-8")
        payload = json.loads(decoded)
    except (binascii.Error, UnicodeDecodeError, ValueError):
        return None
    return payload if isinstance(payload, dict) else None


def _sign(payload_part: str, secret: str) -> str:
    digest = hmac.new(
        secret.encode("utf-8"),
        payload_part.encode("ascii"),
        hashlib.sha256,
    ).digest()
    return _base64url_encode(digest)


def _base64url_encode(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).decode("ascii").rstrip("=")


def _base64url_decode(value: str) -> bytes:
    padding = "=" * (-len(value) % 4)
    return base64.urlsafe_b64decode((value + padding).encode("ascii"))
