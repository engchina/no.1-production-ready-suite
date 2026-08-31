"""アプリケーション認証/RBAC のユースケース。"""

from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import json
import logging
import re
import secrets
import threading
from collections.abc import Iterable, Mapping, Sequence
from datetime import UTC, datetime, timedelta
from functools import lru_cache
from pathlib import Path
from uuid import uuid4

from dotenv import dotenv_values

from app.settings import Settings, get_settings

from .domain import (
    SYSTEM_ADMIN_ROLE_CODE,
    SYSTEM_ADMIN_ROLE_ID,
    DataEntitlementRecord,
    Principal,
    RoleRecord,
    SessionRecord,
    UserRecord,
    scope_filters_canonical_json,
    scope_filters_scope_code,
)
from .passwords import (
    PasswordPolicyError,
    generate_temporary_password,
    hash_password,
    validate_password,
    verify_password,
)
from .permissions import (
    ALL_PERMISSION_CODES,
    expand_permissions,
    normalize_permission_codes,
    unknown_permission_codes,
)
from .store import (
    InMemorySecurityStore,
    OracleSecurityStore,
    SecurityConflict,
    SecurityMigrationRequired,
    SecurityNotFound,
    SecurityStore,
)

DataEntitlementDraft = tuple[str, str, str] | DataEntitlementRecord
logger = logging.getLogger(__name__)


_SECURITY_ERROR_CODES = {
    400: "SECURITY_REQUEST_INVALID",
    401: "SECURITY_AUTHENTICATION_REQUIRED",
    403: "SECURITY_PERMISSION_DENIED",
    404: "SECURITY_RESOURCE_NOT_FOUND",
    409: "SECURITY_STATE_CONFLICT",
    429: "SECURITY_RATE_LIMITED",
    500: "SECURITY_OPERATION_FAILED",
    503: "SECURITY_SERVICE_UNAVAILABLE",
}

_SECURITY_CONFLICT_TITLES = {
    "SECURITY_USER_LOGIN_ID_CONFLICT": "ユーザーを作成できません",
    "SECURITY_ROLE_CODE_CONFLICT": "ロールを作成できません",
}


class SecurityApiError(RuntimeError):
    def __init__(
        self,
        status_code: int,
        public_message: str,
        *,
        code: str | None = None,
        title: str | None = None,
        retryable: bool = False,
        field_errors: Sequence[Mapping[str, str]] = (),
    ) -> None:
        super().__init__(public_message)
        self.status_code = status_code
        self.public_message = public_message
        self.code = code or _SECURITY_ERROR_CODES.get(status_code, "SECURITY_API_ERROR")
        self.title = title
        self.retryable = retryable
        self.field_errors = tuple(dict(item) for item in field_errors)


class LoginFailed(SecurityApiError):
    def __init__(self) -> None:
        super().__init__(401, "ログインユーザーIDまたはパスワードを確認してください。")


def _now() -> datetime:
    return datetime.now(UTC)


def _hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _aware(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value


_SYSTEM_ADMIN_BOOTSTRAP_ONLY_MESSAGE = (
    "SYSTEM_ADMIN ロールは初期システム管理者にのみ割り当てできます。"
)

_SECURITY_SCHEMA_OBJECT_NAMES = frozenset(
    {
        "NL2SQL_APP_USERS",
        "NL2SQL_APP_ROLES",
        "NL2SQL_APP_USER_ROLES",
        "NL2SQL_APP_ROLE_PERMISSIONS",
        "NL2SQL_APP_ROLE_PROFILES",
        "NL2SQL_APP_DATA_ENTITLEMENTS",
        "NL2SQL_AUTH_SESSIONS",
        "NL2SQL_DEEPSEC_MIGRATIONS",
    }
)
_SECURITY_MIGRATION_REQUIRED_MESSAGE = (
    "アプリケーション認証/RBAC の schema migration が未適用です。"
    "`uv run python -m app.cli.app_security_migrate --apply --skip-bootstrap` "
    "を実行してから再試行してください。"
)

_CONFIGURED_SYSTEM_ADMIN_USER_UUID = "00000000-0000-0000-0000-000000000002"
_CONFIGURED_SYSTEM_ADMIN_SESSION_PREFIX = "nl2sql-system-admin-v1"
_CONFIGURED_SYSTEM_ADMIN_TOKEN_TYPE = "configured-system-admin"
_FIXED_APP_ADMIN_LOGIN_USER_ID = "system_admin"
_APP_ADMIN_LOGIN_USER_ID_KEY = "APP_ADMIN_LOGIN_USER_ID"
_LEGACY_APP_ADMIN_USERNAME_KEY = "APP_ADMIN_USERNAME"
_APP_ADMIN_LOGIN_USER_PASSWORD_KEY = "APP_ADMIN_LOGIN_USER_PASSWORD"
_LEGACY_APP_ADMIN_PASSWORD_KEY = "APP_ADMIN_PASSWORD"
_APP_AUTH_ENABLED_KEY = "APP_AUTH_ENABLED"
_BACKEND_ENV_FILE = Path(__file__).resolve().parents[2] / ".env"
_ENV_ASSIGNMENT_RE = re.compile(r"^\s*([A-Za-z_][A-Za-z0-9_]*)\s*=")
_APP_ADMIN_PASSWORD_PATTERN = re.compile(
    r'^(?!.*admin)(?=.*[0-9])(?=.*[a-z])(?=.*[A-Z])(?!.*["]).{12,30}$'
)


def _looks_like_missing_security_schema(exc: Exception) -> bool:
    if isinstance(exc, SecurityMigrationRequired):
        return True
    message = str(exc).upper()
    return "ORA-00942" in message and any(
        object_name in message for object_name in _SECURITY_SCHEMA_OBJECT_NAMES
    )


def _security_migration_diagnostic(exc: Exception) -> tuple[str, str]:
    object_name = exc.object_name if isinstance(exc, SecurityMigrationRequired) else "UNKNOWN"
    raw = exc.__cause__ if isinstance(exc, SecurityMigrationRequired) and exc.__cause__ else exc
    message = str(raw).upper()
    if object_name == "UNKNOWN":
        object_name = next(
            (name for name in _SECURITY_SCHEMA_OBJECT_NAMES if name in message),
            "UNKNOWN",
        )
    code_match = re.search(r"\bORA-\d{5}\b", message)
    return object_name, code_match.group(0) if code_match else "ORA-00942"


def _b64url_encode(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).decode("ascii").rstrip("=")


def _b64url_decode(value: str) -> bytes:
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))


def _constant_time_equal(left: str, right: str) -> bool:
    return hmac.compare_digest(left.encode("utf-8"), right.encode("utf-8"))


def _env_assignment_key(line: str) -> str | None:
    match = _ENV_ASSIGNMENT_RE.match(line)
    return match.group(1) if match else None


def _format_env_value(value: str) -> str:
    if not value:
        return '""'
    if re.search(r"\s|#|=|'|\\", value):
        escaped = value.replace("\\", "\\\\").replace('"', '\\"')
        return f'"{escaped}"'
    return value


def _read_backend_env_value(key: str) -> str | None:
    if not _BACKEND_ENV_FILE.exists():
        return None
    values = dotenv_values(_BACKEND_ENV_FILE)
    value = values.get(key)
    return str(value) if value is not None else None


def _replace_backend_env_file(content: str) -> None:
    _BACKEND_ENV_FILE.parent.mkdir(parents=True, exist_ok=True)
    mode = _BACKEND_ENV_FILE.stat().st_mode & 0o777 if _BACKEND_ENV_FILE.exists() else 0o600
    temporary_path = _BACKEND_ENV_FILE.with_name(f".{_BACKEND_ENV_FILE.name}.{uuid4().hex}.tmp")
    temporary_path.write_text(content, encoding="utf-8")
    temporary_path.chmod(mode)
    temporary_path.replace(_BACKEND_ENV_FILE)


class SecurityService:
    def __init__(self, store: SecurityStore, settings: Settings) -> None:
        self.store = store
        self.settings = settings
        self._bootstrap_lock = threading.Lock()
        self._bootstrap_checked = False

    def bootstrap(self) -> bool:
        self._ensure_configured_system_admin_ready()
        return False

    def ensure_bootstrapped(self) -> None:
        """process ごとに一度だけ、DB lock 付きで初期管理者を確認する。"""

        if self._bootstrap_checked:
            return
        with self._bootstrap_lock:
            if self._bootstrap_checked:
                return
            self.bootstrap()
            self._bootstrap_checked = True

    def login(
        self,
        login_user_id: str,
        password: str,
        *,
        request_id: str = "",
        client_ip: str = "",
    ) -> tuple[Principal, str, str]:
        normalized_login_user_id = login_user_id.strip()
        if normalized_login_user_id == _FIXED_APP_ADMIN_LOGIN_USER_ID:
            _, configured_password = self._ensure_configured_system_admin_ready()
            if _constant_time_equal(password, configured_password):
                return self._create_configured_system_admin_session()
            raise LoginFailed()
        if normalized_login_user_id.casefold() == _FIXED_APP_ADMIN_LOGIN_USER_ID:
            raise LoginFailed()
        try:
            user = self.store.get_user_by_login_user_id(normalized_login_user_id.casefold())
        except Exception as exc:
            self._raise_security_migration_if_needed(exc)
            raise
        now = _now()
        if user is None:
            raise LoginFailed()
        if user.status != "ACTIVE" or (
            user.locked_until is not None and _aware(user.locked_until) > now
        ):
            raise LoginFailed()
        verified, updated_hash = verify_password(password, user.password_hash)
        if not verified:
            failed_count = user.failed_login_count + 1
            locked_until = None
            if failed_count >= self.settings.app_auth_failed_login_limit:
                locked_until = now + timedelta(minutes=self.settings.app_auth_lockout_minutes)
                failed_count = 0
            self.store.record_login_failure(
                user.user_uuid,
                failed_count=failed_count,
                locked_until=locked_until,
            )
            raise LoginFailed()
        self.store.record_login_success(user.user_uuid, password_hash=updated_hash)
        token = secrets.token_urlsafe(32)
        csrf_token = secrets.token_urlsafe(32)
        session = SessionRecord(
            session_id=str(uuid4()),
            user_uuid=user.user_uuid,
            token_hash=_hash_token(token),
            csrf_token_hash=_hash_token(csrf_token),
            idle_expires_at=now + timedelta(minutes=self.settings.app_auth_idle_timeout_minutes),
            absolute_expires_at=now
            + timedelta(hours=self.settings.app_auth_absolute_timeout_hours),
            last_seen_at=now,
        )
        self.store.create_session(session)
        try:
            principal = self._principal_for(user, session)
        except Exception as exc:
            self._raise_security_migration_if_needed(exc)
            raise
        return principal, token, csrf_token

    def authenticate_session(self, token: str) -> Principal:
        if not token:
            raise SecurityApiError(401, "ログインしてください。")
        configured_admin = self._authenticate_configured_system_admin_session(token)
        if configured_admin is not None:
            return configured_admin
        session = self.store.get_session_by_token_hash(_hash_token(token))
        now = _now()
        if session is None or session.revoked_at is not None:
            raise SecurityApiError(401, "ログインしてください。")
        if _aware(session.idle_expires_at) <= now or _aware(session.absolute_expires_at) <= now:
            self.store.revoke_session(session.session_id)
            raise SecurityApiError(
                401, "セッションの有効期限が切れました。再度ログインしてください。"
            )
        user = self.store.get_user(session.user_uuid)
        if user is None or user.status != "ACTIVE":
            self.store.revoke_session(session.session_id)
            raise SecurityApiError(401, "ログインしてください。")
        idle_expires = min(
            now + timedelta(minutes=self.settings.app_auth_idle_timeout_minutes),
            _aware(session.absolute_expires_at),
        )
        self.store.touch_session(
            session.session_id,
            last_seen_at=now,
            idle_expires_at=idle_expires,
        )
        session.idle_expires_at = idle_expires
        try:
            return self._principal_for(user, session)
        except Exception as exc:
            self._raise_security_migration_if_needed(exc)
            raise

    def verify_csrf(self, principal: Principal, cookie_token: str, header_token: str) -> None:
        if (
            not cookie_token
            or not header_token
            or not hmac.compare_digest(cookie_token, header_token)
        ):
            raise SecurityApiError(
                403, "リクエストの安全性を確認できません。画面を再読込してください。"
            )
        if not hmac.compare_digest(_hash_token(header_token), principal.csrf_token_hash):
            raise SecurityApiError(
                403, "リクエストの安全性を確認できません。画面を再読込してください。"
            )

    def logout(self, principal: Principal, *, request_id: str = "", client_ip: str = "") -> None:
        if self._is_configured_system_admin_principal(principal):
            return
        self.store.revoke_session(principal.session_id)

    def change_password(
        self,
        principal: Principal,
        current_password: str,
        new_password: str,
        *,
        request_id: str = "",
        client_ip: str = "",
    ) -> Principal:
        if self._is_configured_system_admin_principal(principal):
            _, configured_password = self._ensure_configured_system_admin_ready()
            if not _constant_time_equal(current_password, configured_password):
                raise SecurityApiError(400, "現在のパスワードを確認してください。")
            self._validate_configured_system_admin_password_for_change(new_password)
            self._write_configured_system_admin_password(new_password)
            self.settings.app_admin_login_user_id = _FIXED_APP_ADMIN_LOGIN_USER_ID
            self.settings.app_admin_login_user_password = new_password
            return principal
        user = self.store.get_user(principal.user_uuid)
        if user is None or not verify_password(current_password, user.password_hash)[0]:
            raise SecurityApiError(400, "現在のパスワードを確認してください。")
        self._validate_new_password(new_password, user.login_user_id)
        self.store.set_password(user.user_uuid, hash_password(new_password), force_change=False)
        self.store.revoke_user_sessions(user.user_uuid)
        # 現 session は revoke 済み。呼び出し側は cookie を削除して再ログインさせる。
        return principal

    def list_users(self) -> list[UserRecord]:
        try:
            return self.store.list_users()
        except Exception as exc:
            self._raise_security_migration_if_needed(exc)
            raise

    def create_user(
        self,
        *,
        login_user_id: str,
        display_name: str,
        role_ids: list[str],
        temporary_password: str | None,
        actor: Principal,
        request_id: str = "",
        client_ip: str = "",
    ) -> tuple[UserRecord, str]:
        normalized_role_ids = list(dict.fromkeys(role_ids))
        if SYSTEM_ADMIN_ROLE_ID in normalized_role_ids:
            raise SecurityApiError(409, _SYSTEM_ADMIN_BOOTSTRAP_ONLY_MESSAGE)
        self._assert_actor_can_assign_roles(actor, normalized_role_ids)
        normalized_login_user_id = login_user_id.strip()
        if normalized_login_user_id.casefold() == _FIXED_APP_ADMIN_LOGIN_USER_ID:
            raise SecurityApiError(409, "system_admin は構成管理者専用のログインユーザーIDです。")
        password = temporary_password or generate_temporary_password()
        self._validate_new_password(password, normalized_login_user_id)
        user = UserRecord(
            user_uuid=str(uuid4()),
            login_user_id=normalized_login_user_id,
            display_name=display_name.strip(),
            password_hash=hash_password(password),
            status="ACTIVE",
            force_password_change=True,
            failed_login_count=0,
            locked_until=None,
            version=1,
            role_ids=normalized_role_ids,
        )
        try:
            created = self.store.create_user(user)
        except (SecurityConflict, SecurityNotFound) as exc:
            raise self._store_error(exc) from exc
        return created, password

    def update_user(
        self,
        user_uuid: str,
        *,
        expected_version: int,
        display_name: str,
        status: str,
        role_ids: list[str],
        actor: Principal,
        request_id: str = "",
        client_ip: str = "",
    ) -> UserRecord:
        current = self.store.get_user(user_uuid)
        if current is None:
            raise SecurityApiError(404, "ユーザーが見つかりません。")
        self._assert_actor_can_manage_user(actor, current)
        normalized_role_ids = list(dict.fromkeys(role_ids))
        current_roles = [self.get_role(role_id) for role_id in current.role_ids]
        is_admin = any(role and role.role_code == SYSTEM_ADMIN_ROLE_CODE for role in current_roles)
        next_roles = [self.get_role(role_id) for role_id in normalized_role_ids]
        remains_admin = any(
            role and role.role_code == SYSTEM_ADMIN_ROLE_CODE for role in next_roles
        )
        grants_system_admin = (
            SYSTEM_ADMIN_ROLE_ID in normalized_role_ids
            and SYSTEM_ADMIN_ROLE_ID not in current.role_ids
        )
        if grants_system_admin and not current.is_bootstrap_admin:
            raise SecurityApiError(409, _SYSTEM_ADMIN_BOOTSTRAP_ONLY_MESSAGE)
        self._assert_actor_can_assign_roles(
            actor,
            normalized_role_ids,
            existing_role_ids=current.role_ids,
        )
        if (
            is_admin
            and (status != "ACTIVE" or not remains_admin)
            and self.store.count_active_system_admins() <= 1
        ):
            raise SecurityApiError(409, "最後のシステム管理者は無効化または権限解除できません。")
        try:
            updated = self.store.update_user(
                user_uuid,
                expected_version=expected_version,
                display_name=display_name.strip(),
                status=status,
                role_ids=normalized_role_ids,
            )
        except (SecurityConflict, SecurityNotFound) as exc:
            raise self._store_error(exc) from exc
        if status != "ACTIVE":
            self.store.revoke_user_sessions(user_uuid)
        return updated

    def delete_user(
        self,
        user_uuid: str,
        *,
        expected_version: int,
        actor: Principal,
        request_id: str = "",
        client_ip: str = "",
    ) -> UserRecord:
        current = self.store.get_user(user_uuid)
        if current is None:
            raise SecurityApiError(404, "ユーザーが見つかりません。")
        self._assert_actor_can_manage_user(actor, current)
        if actor.user_uuid == user_uuid:
            raise SecurityApiError(
                409,
                "ログイン中のユーザー自身は削除できません。別の管理者で操作してください。",
                code="SECURITY_USER_DELETE_SELF_FORBIDDEN",
            )
        if current.is_bootstrap_admin:
            raise SecurityApiError(
                409,
                "初期システム管理者は削除できません。",
                code="SECURITY_USER_DELETE_PROTECTED",
            )
        if current.status != "DISABLED":
            raise SecurityApiError(
                409,
                "ユーザーを先に無効化してから削除してください。",
                code="SECURITY_USER_DELETE_REQUIRES_DISABLED",
            )
        try:
            self.store.delete_user(user_uuid, expected_version=expected_version)
        except (SecurityConflict, SecurityNotFound) as exc:
            raise self._store_error(exc) from exc
        return current

    def reset_password(
        self,
        user_uuid: str,
        temporary_password: str | None,
        *,
        actor: Principal,
        request_id: str = "",
        client_ip: str = "",
    ) -> tuple[UserRecord, str]:
        user = self.store.get_user(user_uuid)
        if user is None:
            raise SecurityApiError(404, "ユーザーが見つかりません。")
        self._assert_actor_can_manage_user(actor, user)
        password = temporary_password or generate_temporary_password()
        self._validate_new_password(password, user.login_user_id)
        self.store.set_password(user_uuid, hash_password(password), force_change=True)
        self.store.revoke_user_sessions(user_uuid)
        updated = self.store.get_user(user_uuid)
        if updated is None:
            raise SecurityApiError(404, "ユーザーが見つかりません。")
        return updated, password

    def unlock_user(
        self, user_uuid: str, *, actor: Principal, request_id: str = "", client_ip: str = ""
    ) -> UserRecord:
        user = self.store.get_user(user_uuid)
        if user is None:
            raise SecurityApiError(404, "ユーザーが見つかりません。")
        self._assert_actor_can_manage_user(actor, user)
        self.store.record_login_success(user_uuid)
        updated = self.store.get_user(user_uuid)
        if updated is None:
            raise SecurityApiError(404, "ユーザーが見つかりません。")
        return updated

    def list_roles(self, *, include_archived: bool = False) -> list[RoleRecord]:
        try:
            return self.store.list_roles(include_archived=include_archived)
        except Exception as exc:
            self._raise_security_migration_if_needed(exc)
            raise

    def get_role(self, role_id: str) -> RoleRecord | None:
        try:
            return self.store.get_role(role_id)
        except Exception as exc:
            self._raise_security_migration_if_needed(exc)
            raise

    def list_roles_for_actor(
        self, actor: Principal, *, include_archived: bool = False
    ) -> list[RoleRecord]:
        if actor.has_permission("menu.security_roles"):
            return self.list_roles(include_archived=include_archived)
        return [
            role
            for role in self.list_roles(include_archived=False)
            if self._actor_can_assign_role(actor, role)
        ]

    def get_role_for_actor(self, role_id: str, actor: Principal) -> RoleRecord | None:
        role = self.get_role(role_id)
        if role is None:
            return None
        if actor.has_permission("menu.security_roles"):
            return role
        if self._actor_can_assign_role(actor, role):
            return role
        return None

    def create_role(
        self,
        *,
        role_code: str,
        display_name: str,
        description: str,
        permissions: set[str],
        entitlements: list[DataEntitlementDraft],
        allowed_profile_ids: set[str] | None = None,
        actor: Principal,
        request_id: str = "",
        client_ip: str = "",
    ) -> RoleRecord:
        if allowed_profile_ids:
            self._assert_actor_can_manage_profile_access(actor)
        role = self._build_role(
            role_id=str(uuid4()),
            role_code=role_code,
            display_name=display_name,
            description=description,
            permissions=permissions,
            entitlements=entitlements,
            allowed_profile_ids=allowed_profile_ids or set(),
            version=1,
        )
        try:
            created = self.store.create_role(role)
        except SecurityConflict as exc:
            raise self._store_error(exc) from exc
        return created

    def update_role(
        self,
        role_id: str,
        *,
        expected_version: int,
        display_name: str,
        description: str,
        permissions: set[str],
        entitlements: list[DataEntitlementDraft],
        allowed_profile_ids: set[str] | None = None,
        actor: Principal,
        request_id: str = "",
        client_ip: str = "",
    ) -> RoleRecord:
        current = self.get_role(role_id)
        if current is None:
            raise SecurityApiError(404, "ロールが見つかりません。")
        if current.is_built_in:
            raise SecurityApiError(409, "組み込み SYSTEM_ADMIN ロールは変更できません。")
        requested_profile_ids = (
            allowed_profile_ids
            if allowed_profile_ids is not None
            else set(current.allowed_profile_ids)
        )
        if requested_profile_ids != current.allowed_profile_ids:
            self._assert_actor_can_manage_profile_access(actor)
        role = self._build_role(
            role_id=role_id,
            role_code=current.role_code,
            display_name=display_name,
            description=description,
            permissions=permissions,
            entitlements=entitlements,
            allowed_profile_ids=requested_profile_ids,
            version=current.version,
        )
        try:
            updated = self.store.update_role(role, expected_version=expected_version)
        except (SecurityConflict, SecurityNotFound) as exc:
            raise self._store_error(exc) from exc
        return updated

    def update_role_data_entitlements(
        self,
        role_id: str,
        *,
        expected_version: int,
        entitlements: list[DataEntitlementDraft],
        actor: Principal,
        request_id: str = "",
        client_ip: str = "",
    ) -> RoleRecord:
        current = self.get_role(role_id)
        if current is None:
            raise SecurityApiError(404, "ロールが見つかりません。")
        if current.is_built_in:
            raise SecurityApiError(409, "組み込み SYSTEM_ADMIN ロールは変更できません。")
        if current.archived:
            raise SecurityApiError(409, "アーカイブ済みロールは変更できません。")
        data_records = self._data_entitlement_records(
            role_id,
            entitlements,
            current_entitlements=current.entitlements,
        )
        role = RoleRecord(
            role_id=current.role_id,
            role_code=current.role_code,
            display_name=current.display_name,
            description=current.description,
            is_built_in=current.is_built_in,
            archived=current.archived,
            version=current.version,
            permissions=set(current.permissions),
            entitlements=data_records,
            allowed_profile_ids=set(current.allowed_profile_ids),
        )
        try:
            updated = self.store.update_role(role, expected_version=expected_version)
        except (SecurityConflict, SecurityNotFound) as exc:
            raise self._store_error(exc) from exc
        return updated

    def commit_role_data_entitlement_sync(
        self,
        role_id: str,
        *,
        expected_version: int,
        entitlements: list[DataEntitlementRecord],
        actor: Principal,
    ) -> RoleRecord:
        """Oracle 同期成功後のロール全体 Data Grant snapshot を確定する。"""
        _ = actor
        current = self.get_role(role_id)
        if current is None:
            raise SecurityApiError(404, "ロールが見つかりません。")
        if current.is_built_in:
            raise SecurityApiError(409, "組み込み SYSTEM_ADMIN ロールは変更できません。")
        if current.archived:
            raise SecurityApiError(409, "アーカイブ済みロールは変更できません。")
        if current.version != expected_version:
            raise SecurityApiError(
                409,
                "ロールが別の操作で更新されています。表示を更新して再試行してください。",
            )
        role = RoleRecord(
            role_id=current.role_id,
            role_code=current.role_code,
            display_name=current.display_name,
            description=current.description,
            is_built_in=current.is_built_in,
            archived=current.archived,
            version=current.version,
            permissions=set(current.permissions),
            entitlements=list(entitlements),
            allowed_profile_ids=set(current.allowed_profile_ids),
        )
        try:
            return self.store.update_role(role, expected_version=expected_version)
        except (SecurityConflict, SecurityNotFound) as exc:
            raise self._store_error(exc) from exc

    def archive_role(
        self,
        role_id: str,
        *,
        expected_version: int,
        actor: Principal,
        request_id: str = "",
        client_ip: str = "",
    ) -> RoleRecord:
        role = self.get_role(role_id)
        if role is None:
            raise SecurityApiError(404, "ロールが見つかりません。")
        if role.is_built_in:
            raise SecurityApiError(409, "組み込み SYSTEM_ADMIN ロールはアーカイブできません。")
        try:
            archived = self.store.archive_role(role_id, expected_version=expected_version)
        except (SecurityConflict, SecurityNotFound) as exc:
            raise self._store_error(exc) from exc
        return archived

    def restore_role(
        self,
        role_id: str,
        *,
        expected_version: int,
        actor: Principal,
        request_id: str = "",
        client_ip: str = "",
    ) -> RoleRecord:
        role = self.get_role(role_id)
        if role is None:
            raise SecurityApiError(404, "ロールが見つかりません。")
        if role.is_built_in:
            raise SecurityApiError(409, "組み込み SYSTEM_ADMIN ロールは復元できません。")
        if not role.archived:
            raise SecurityApiError(409, "ロールはアーカイブされていません。")
        try:
            restored = self.store.restore_role(role_id, expected_version=expected_version)
        except (SecurityConflict, SecurityNotFound) as exc:
            raise self._store_error(exc) from exc
        return restored

    def delete_role(
        self,
        role_id: str,
        *,
        expected_version: int,
        actor: Principal,
        request_id: str = "",
        client_ip: str = "",
    ) -> RoleRecord:
        role = self.get_role(role_id)
        if role is None:
            raise SecurityApiError(404, "ロールが見つかりません。")
        if role.is_built_in:
            raise SecurityApiError(
                409,
                "組み込み SYSTEM_ADMIN ロールは削除できません。",
                code="SECURITY_ROLE_DELETE_PROTECTED",
            )
        if not role.archived:
            raise SecurityApiError(
                409,
                "ロールを先にアーカイブしてから削除してください。",
                code="SECURITY_ROLE_DELETE_REQUIRES_ARCHIVED",
            )
        if role.entitlements:
            raise SecurityApiError(
                409,
                "このロールにはデータ権限が残っています。"
                "Deep Data Security で空の Data Grant を適用してから削除してください。",
                code="SECURITY_ROLE_DELETE_ENTITLEMENTS_PRESENT",
            )
        try:
            self.store.delete_role(role_id, expected_version=expected_version)
        except (SecurityConflict, SecurityNotFound) as exc:
            raise self._store_error(exc) from exc
        return role

    def _matches_configured_system_admin_login_user_id(self, login_user_id: str) -> bool:
        configured_login_user_id, _ = self._ensure_configured_system_admin_ready()
        return _constant_time_equal(login_user_id, configured_login_user_id)

    def _create_configured_system_admin_session(self) -> tuple[Principal, str, str]:
        now = _now()
        configured_login_user_id, _ = self._ensure_configured_system_admin_ready()
        csrf_token = secrets.token_urlsafe(32)
        session_id = f"configured-system-admin:{uuid4()}"
        payload = {
            "type": _CONFIGURED_SYSTEM_ADMIN_TOKEN_TYPE,
            "sid": session_id,
            "user_uuid": _CONFIGURED_SYSTEM_ADMIN_USER_UUID,
            "login_user_id": configured_login_user_id,
            "csrf_hash": _hash_token(csrf_token),
            "exp": int(
                (now + timedelta(hours=self.settings.app_auth_absolute_timeout_hours)).timestamp()
            ),
        }
        token = self._sign_configured_system_admin_payload(payload)
        principal = self._configured_system_admin_principal(
            login_user_id=configured_login_user_id,
            session_id=session_id,
            csrf_token_hash=str(payload["csrf_hash"]),
        )
        return principal, token, csrf_token

    def _authenticate_configured_system_admin_session(self, token: str) -> Principal | None:
        prefix = _CONFIGURED_SYSTEM_ADMIN_SESSION_PREFIX + "."
        if not token.startswith(prefix):
            return None
        try:
            payload_segment, signature = token.removeprefix(prefix).split(".", 1)
        except ValueError as exc:
            raise SecurityApiError(401, "ログインしてください。") from exc
        expected_signature = self._configured_system_admin_signature(payload_segment)
        if not hmac.compare_digest(signature, expected_signature):
            raise SecurityApiError(401, "ログインしてください。")
        try:
            payload = json.loads(_b64url_decode(payload_segment).decode("utf-8"))
        except (TypeError, ValueError, UnicodeDecodeError, binascii.Error) as exc:
            raise SecurityApiError(401, "ログインしてください。") from exc
        if payload.get("type") != _CONFIGURED_SYSTEM_ADMIN_TOKEN_TYPE:
            raise SecurityApiError(401, "ログインしてください。")
        login_user_id = str(payload.get("login_user_id") or payload.get("login") or "")
        if not self._matches_configured_system_admin_login_user_id(login_user_id):
            raise SecurityApiError(401, "ログインしてください。")
        try:
            expires_at = int(payload.get("exp"))
        except (TypeError, ValueError) as exc:
            raise SecurityApiError(401, "ログインしてください。") from exc
        if expires_at <= int(_now().timestamp()):
            raise SecurityApiError(
                401, "セッションの有効期限が切れました。再度ログインしてください。"
            )
        session_id = str(payload.get("sid") or "")
        csrf_token_hash = str(payload.get("csrf_hash") or "")
        if not session_id.startswith("configured-system-admin:") or not csrf_token_hash:
            raise SecurityApiError(401, "ログインしてください。")
        return self._configured_system_admin_principal(
            login_user_id=login_user_id,
            session_id=session_id,
            csrf_token_hash=csrf_token_hash,
        )

    def _sign_configured_system_admin_payload(self, payload: dict[str, object]) -> str:
        payload_segment = _b64url_encode(
            json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8")
        )
        signature = self._configured_system_admin_signature(payload_segment)
        return f"{_CONFIGURED_SYSTEM_ADMIN_SESSION_PREFIX}.{payload_segment}.{signature}"

    def _configured_system_admin_signature(self, payload_segment: str) -> str:
        return _b64url_encode(
            hmac.new(
                self._configured_system_admin_token_key(),
                payload_segment.encode("ascii"),
                hashlib.sha256,
            ).digest()
        )

    def _configured_system_admin_token_key(self) -> bytes:
        configured_login_user_id, configured_password = self._ensure_configured_system_admin_ready()
        configured_secret = (
            f"{self.settings.service_name}:{configured_login_user_id}:{configured_password}"
        )
        return hashlib.sha256(configured_secret.encode("utf-8")).digest()

    def _ensure_configured_system_admin_ready(self) -> tuple[str, str]:
        login_user_id, password = self._configured_system_admin_credentials()
        if login_user_id != _FIXED_APP_ADMIN_LOGIN_USER_ID:
            raise SecurityApiError(
                503,
                "構成管理者の認証情報が正しく設定されていません。"
                "APP_ADMIN_LOGIN_USER_ID は system_admin に固定してください。",
            )
        self._validate_configured_system_admin_password(password)
        return login_user_id, password

    def _configured_system_admin_credentials(self) -> tuple[str, str]:
        login_user_id = _read_backend_env_value(_APP_ADMIN_LOGIN_USER_ID_KEY)
        if login_user_id is None:
            login_user_id = _read_backend_env_value(_LEGACY_APP_ADMIN_USERNAME_KEY)
        password = _read_backend_env_value(_APP_ADMIN_LOGIN_USER_PASSWORD_KEY)
        if password is None:
            password = _read_backend_env_value(_LEGACY_APP_ADMIN_PASSWORD_KEY)
        if login_user_id is None:
            login_user_id = self.settings.app_admin_login_user_id
        if password is None:
            password = self.settings.app_admin_login_user_password
        return login_user_id.strip(), password

    @staticmethod
    def _validate_configured_system_admin_password(password: str) -> None:
        if (
            password == "TODO"
            or "\r" in password
            or "\n" in password
            or not _APP_ADMIN_PASSWORD_PATTERN.match(password)
        ):
            raise SecurityApiError(
                503,
                "構成管理者の認証情報が設定されていません。"
                "APP_ADMIN_LOGIN_USER_ID と APP_ADMIN_LOGIN_USER_PASSWORD を設定してください。",
            )

    @staticmethod
    def _validate_configured_system_admin_password_for_change(password: str) -> None:
        if (
            password == "TODO"
            or "\r" in password
            or "\n" in password
            or not _APP_ADMIN_PASSWORD_PATTERN.match(password)
        ):
            raise SecurityApiError(
                400,
                "新しいパスワードは12〜30文字で、大文字・小文字・数字を含め、"
                "admin と二重引用符を含めないでください。",
            )

    def _write_configured_system_admin_password(self, password: str) -> None:
        if _BACKEND_ENV_FILE.exists():
            lines = _BACKEND_ENV_FILE.read_text(encoding="utf-8").splitlines()
        else:
            lines = []
        next_lines = [
            line
            for line in lines
            if _env_assignment_key(line)
            not in {
                _APP_ADMIN_LOGIN_USER_ID_KEY,
                _LEGACY_APP_ADMIN_USERNAME_KEY,
                _APP_ADMIN_LOGIN_USER_PASSWORD_KEY,
                _LEGACY_APP_ADMIN_PASSWORD_KEY,
            }
        ]
        admin_lines = [
            f"{_APP_ADMIN_LOGIN_USER_ID_KEY}={_FIXED_APP_ADMIN_LOGIN_USER_ID}",
            f"{_APP_ADMIN_LOGIN_USER_PASSWORD_KEY}={_format_env_value(password)}",
        ]
        insert_at = next(
            (
                index
                for index, line in enumerate(next_lines)
                if _env_assignment_key(line) == _APP_AUTH_ENABLED_KEY
            ),
            None,
        )
        if insert_at is None:
            if next_lines and next_lines[-1].strip():
                next_lines.append("")
            next_lines.extend(admin_lines)
        else:
            next_lines[insert_at:insert_at] = admin_lines
        _replace_backend_env_file("\n".join(next_lines).rstrip() + "\n")

    def _configured_system_admin_principal(
        self,
        *,
        login_user_id: str,
        session_id: str,
        csrf_token_hash: str,
    ) -> Principal:
        return Principal(
            user_uuid=_CONFIGURED_SYSTEM_ADMIN_USER_UUID,
            login_user_id=login_user_id,
            display_name=f"{login_user_id}（構成管理者）",
            status="ACTIVE",
            force_password_change=False,
            role_codes=[SYSTEM_ADMIN_ROLE_CODE],
            permissions=set(ALL_PERMISSION_CODES),
            data_entitlements=[],
            allowed_profile_ids=set(),
            session_id=session_id,
            csrf_token_hash=csrf_token_hash,
            password_change_allowed=True,
        )

    @staticmethod
    def _is_configured_system_admin_principal(principal: Principal) -> bool:
        return (
            principal.user_uuid == _CONFIGURED_SYSTEM_ADMIN_USER_UUID
            and principal.session_id.startswith("configured-system-admin:")
        )

    def _principal_for(self, user: UserRecord, session: SessionRecord) -> Principal:
        roles = [self.get_role(role_id) for role_id in user.role_ids]
        active_roles = [role for role in roles if role is not None and not role.archived]
        permissions = expand_permissions(
            {permission for role in active_roles for permission in role.permissions}
        )
        entitlements: dict[tuple[str, str, str], DataEntitlementRecord] = {}
        allowed_profile_ids: set[str] = set()
        for role in active_roles:
            allowed_profile_ids.update(role.allowed_profile_ids)
            for entitlement in role.entitlements:
                key = (
                    entitlement.entitlement_id or entitlement.resource_code,
                    entitlement.scope_code,
                    entitlement.capability,
                )
                entitlements[key] = entitlement
        return Principal(
            user_uuid=user.user_uuid,
            login_user_id=user.login_user_id,
            display_name=user.display_name,
            status=user.status,
            force_password_change=user.force_password_change,
            role_codes=sorted(role.role_code for role in active_roles),
            permissions=permissions,
            data_entitlements=list(entitlements.values()),
            allowed_profile_ids=allowed_profile_ids,
            session_id=session.session_id,
            csrf_token_hash=session.csrf_token_hash,
        )

    def _build_role(
        self,
        *,
        role_id: str,
        role_code: str,
        display_name: str,
        description: str,
        permissions: set[str],
        entitlements: list[DataEntitlementDraft],
        allowed_profile_ids: set[str],
        version: int,
    ) -> RoleRecord:
        unknown = unknown_permission_codes(permissions)
        if unknown:
            raise SecurityApiError(400, f"未登録の権限コードです: {', '.join(sorted(unknown))}")
        normalized = normalize_permission_codes(permissions)
        data_records = self._data_entitlement_records(role_id, entitlements)
        return RoleRecord(
            role_id=role_id,
            role_code=role_code,
            display_name=display_name.strip(),
            description=description.strip(),
            is_built_in=False,
            archived=False,
            version=version,
            permissions=normalized,
            entitlements=data_records,
            allowed_profile_ids={item.strip() for item in allowed_profile_ids if item.strip()},
        )

    @staticmethod
    def _assert_actor_can_manage_profile_access(
        actor: Principal,
    ) -> None:
        if not actor.is_system_admin:
            raise SecurityApiError(
                403,
                "業務プロファイル利用権限を変更できるのは SYSTEM_ADMIN のみです。",
            )

    @staticmethod
    def _data_entitlement_policy_signature(
        entitlement: DataEntitlementRecord,
    ) -> tuple[str, str, str, str, str, str, tuple[str, ...], str, str, str]:
        return (
            entitlement.resource_code.strip().upper(),
            entitlement.scope_code.strip(),
            entitlement.capability.strip().upper(),
            entitlement.target_owner.strip().upper(),
            entitlement.target_object.strip().upper(),
            entitlement.target_type.strip().upper(),
            tuple(column.strip().upper() for column in entitlement.column_names),
            entitlement.scope_mode.strip().upper(),
            entitlement.scope_column.strip().upper(),
            scope_filters_canonical_json(entitlement.scope_filters),
        )

    @classmethod
    def _data_entitlement_records(
        cls,
        role_id: str,
        entitlements: Sequence[DataEntitlementDraft],
        *,
        current_entitlements: list[DataEntitlementRecord] | None = None,
    ) -> list[DataEntitlementRecord]:
        records: list[DataEntitlementRecord] = []
        seen: set[tuple[str, str, str, str, str, str, str, str, str]] = set()
        current_by_id = {
            entitlement.entitlement_id: entitlement
            for entitlement in current_entitlements or []
            if entitlement.entitlement_id
        }
        for entitlement in entitlements:
            if isinstance(entitlement, DataEntitlementRecord):
                record = DataEntitlementRecord(
                    entitlement_id=entitlement.entitlement_id or str(uuid4()),
                    role_id=role_id,
                    resource_code=entitlement.resource_code,
                    scope_code=(
                        "*"
                        if entitlement.scope_mode.strip().upper() == "ALL"
                        else (
                            scope_filters_scope_code(entitlement.scope_filters)
                            if entitlement.scope_mode.strip().upper() == "FILTERS"
                            else entitlement.scope_code
                        )
                    ),
                    capability=entitlement.capability,
                    target_owner=entitlement.target_owner,
                    target_object=entitlement.target_object,
                    target_type=entitlement.target_type,
                    column_names=list(entitlement.column_names),
                    scope_mode=entitlement.scope_mode,
                    scope_column=entitlement.scope_column,
                    scope_filters=list(entitlement.scope_filters),
                    data_grant_name=entitlement.data_grant_name,
                    sql_checksum=entitlement.sql_checksum,
                    apply_status=entitlement.apply_status,
                    apply_error_message=entitlement.apply_error_message,
                    applied_at=entitlement.applied_at,
                )
                current = current_by_id.get(record.entitlement_id)
                if current is not None:
                    if cls._data_entitlement_policy_signature(
                        record
                    ) == cls._data_entitlement_policy_signature(current):
                        record.apply_status = current.apply_status
                        record.apply_error_message = current.apply_error_message
                        record.data_grant_name = current.data_grant_name
                        record.sql_checksum = current.sql_checksum
                        record.applied_at = current.applied_at
                    else:
                        record.apply_status = "PENDING"
                        record.apply_error_message = ""
                        record.data_grant_name = current.data_grant_name
                        record.sql_checksum = ""
                        record.applied_at = None
                elif current_entitlements is not None:
                    record.apply_status = "PENDING"
                    record.apply_error_message = ""
                    record.sql_checksum = ""
                    record.applied_at = None
            else:
                resource, scope, capability = entitlement
                record = DataEntitlementRecord(
                    entitlement_id=str(uuid4()),
                    role_id=role_id,
                    resource_code=resource,
                    scope_code=scope,
                    capability=capability,
                )
            key = (
                record.resource_code,
                record.scope_code,
                record.capability,
                record.target_owner,
                record.target_object,
                ",".join(record.column_names),
                record.scope_mode,
                record.scope_column,
                scope_filters_canonical_json(record.scope_filters),
            )
            if key in seen:
                continue
            seen.add(key)
            records.append(record)
        return records

    def _assert_actor_can_assign_roles(
        self,
        actor: Principal,
        role_ids: list[str],
        *,
        existing_role_ids: Iterable[str] = (),
    ) -> None:
        existing_role_id_set = set(existing_role_ids)
        for role_id in role_ids:
            role = self.get_role(role_id)
            if role is None or role.archived:
                if role_id in existing_role_id_set:
                    continue
                raise SecurityApiError(404, "指定された有効なロールが見つかりません。")
            if not self._actor_can_assign_role(actor, role):
                raise SecurityApiError(403, "このロールを割り当てる権限がありません。")

    def _assert_actor_can_manage_user(self, actor: Principal, user: UserRecord) -> None:
        if actor.is_system_admin:
            return
        for role_id in user.role_ids:
            role = self.get_role(role_id)
            if (
                role is not None
                and not role.archived
                and not self._actor_can_assign_role(actor, role)
            ):
                raise SecurityApiError(403, "このユーザーを管理する権限がありません。")

    @staticmethod
    def _actor_can_assign_role(actor: Principal, role: RoleRecord) -> bool:
        if actor.is_system_admin:
            return True
        if role.role_code == SYSTEM_ADMIN_ROLE_CODE:
            return False
        if role.archived:
            return False
        return expand_permissions(role.permissions).issubset(actor.permissions)

    def _validate_new_password(self, password: str, login_user_id: str) -> None:
        try:
            validate_password(
                password,
                login_user_id=login_user_id,
                min_length=self.settings.app_auth_password_min_length,
                max_length=self.settings.app_auth_password_max_length,
            )
        except PasswordPolicyError as exc:
            raise SecurityApiError(400, str(exc)) from exc

    @staticmethod
    def _raise_security_migration_if_needed(exc: Exception) -> None:
        if _looks_like_missing_security_schema(exc):
            object_name, oracle_code = _security_migration_diagnostic(exc)
            logger.error(
                "security_schema_migration_required",
                extra={
                    "database_object": object_name,
                    "oracle_error_code": oracle_code,
                    "error_code": "SECURITY_SCHEMA_MIGRATION_REQUIRED",
                },
            )
            raise SecurityApiError(
                409,
                _SECURITY_MIGRATION_REQUIRED_MESSAGE,
                code="SECURITY_SCHEMA_MIGRATION_REQUIRED",
                title="セキュリティ初期化が必要です",
            ) from exc

    @staticmethod
    def _store_error(exc: Exception) -> SecurityApiError:
        if isinstance(exc, SecurityNotFound):
            return SecurityApiError(404, str(exc))
        if isinstance(exc, SecurityConflict):
            field_errors = (
                (
                    {
                        "pointer": exc.pointer,
                        "code": exc.field_code,
                        "message": str(exc),
                    },
                )
                if exc.pointer
                else ()
            )
            return SecurityApiError(
                409,
                str(exc),
                code=exc.code,
                title=_SECURITY_CONFLICT_TITLES.get(exc.code),
                field_errors=field_errors,
            )
        return SecurityApiError(409, str(exc))


@lru_cache
def get_security_service() -> SecurityService:
    settings = get_settings()
    store: SecurityStore
    if settings.nl2sql_persistence_mode.strip().lower() == "memory":
        store = InMemorySecurityStore()
    else:
        store = OracleSecurityStore(settings)
    return SecurityService(store, settings)


def reset_security_service() -> None:
    get_security_service.cache_clear()
