"""監査ログ用のリクエストコンテキスト。"""

import hashlib
from collections.abc import Mapping
from contextvars import ContextVar, Token
from dataclasses import dataclass

from app.config import Settings, get_settings

TENANT_ID_HEADER = "x-tenant-id"
USER_ID_HEADER = "x-user-id"
MAX_CONTEXT_VALUE_CHARS = 256


@dataclass(frozen=True)
class AuditRequestContext:
    """監査ログに付与する非機密リクエスト情報。"""

    request_id: str | None = None
    tenant_id_hash: str | None = None
    user_id_hash: str | None = None


_AUDIT_REQUEST_CONTEXT: ContextVar[AuditRequestContext | None] = ContextVar(
    "audit_request_context",
    default=None,
)


def audit_request_context_from_headers(
    headers: Mapping[str, str],
    *,
    request_id: str,
    settings: Settings | None = None,
) -> AuditRequestContext:
    """HTTP header から監査用 context を作る。raw id は保存しない。"""
    resolved_settings = settings or get_settings()
    return AuditRequestContext(
        request_id=request_id,
        tenant_id_hash=_header_hash(headers.get(TENANT_ID_HEADER), resolved_settings),
        user_id_hash=_header_hash(headers.get(USER_ID_HEADER), resolved_settings),
    )


def set_audit_request_context(
    context: AuditRequestContext,
) -> Token[AuditRequestContext | None]:
    """現在の async context へ監査 context を設定する。"""
    return _AUDIT_REQUEST_CONTEXT.set(context)


def reset_audit_request_context(token: Token[AuditRequestContext | None]) -> None:
    """監査 context を以前の状態へ戻す。"""
    _AUDIT_REQUEST_CONTEXT.reset(token)


def current_audit_request_context() -> AuditRequestContext:
    """現在の監査 context を返す。"""
    return _AUDIT_REQUEST_CONTEXT.get() or AuditRequestContext()


def _header_hash(value: str | None, settings: Settings) -> str | None:
    normalized = _normalize_context_value(value)
    if normalized is None:
        return None
    salt = settings.audit_context_hash_salt
    payload = f"{salt}\0{normalized}" if salt else normalized
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _normalize_context_value(value: str | None) -> str | None:
    if value is None:
        return None
    normalized = value.strip()
    if not normalized or len(normalized) > MAX_CONTEXT_VALUE_CHARS:
        return None
    if any(ord(character) < 32 or ord(character) == 127 for character in normalized):
        return None
    return normalized
