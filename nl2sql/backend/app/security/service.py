"""アプリケーション認証/RBAC のユースケース。"""

from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import json
import re
import secrets
import threading
from datetime import UTC, datetime, timedelta
from functools import lru_cache
from uuid import uuid4

from app.settings import Settings, get_settings

from .domain import (
    SYSTEM_ADMIN_ROLE_CODE,
    SYSTEM_ADMIN_ROLE_ID,
    DataEntitlementRecord,
    Principal,
    RoleRecord,
    SessionRecord,
    UserRecord,
)
from .passwords import (
    PasswordPolicyError,
    generate_temporary_password,
    hash_password,
    validate_password,
    verify_password,
)
from .permissions import ALL_PERMISSION_CODES, expand_permissions, unknown_permission_codes
from .store import (
    InMemorySecurityStore,
    OracleSecurityStore,
    SecurityConflict,
    SecurityNotFound,
    SecurityStore,
)


class SecurityApiError(RuntimeError):
    def __init__(self, status_code: int, public_message: str) -> None:
        super().__init__(public_message)
        self.status_code = status_code
        self.public_message = public_message


class LoginFailed(SecurityApiError):
    def __init__(self) -> None:
        super().__init__(401, "ログイン名またはパスワードを確認してください。")


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
        "NL2SQL_APP_DATA_ENTITLEMENTS",
        "NL2SQL_AUTH_SESSIONS",
        "NL2SQL_DEEPSEC_MIGRATIONS",
    }
)

_CONFIGURED_SYSTEM_ADMIN_USER_ID = "00000000-0000-0000-0000-000000000002"
_CONFIGURED_SYSTEM_ADMIN_SESSION_PREFIX = "nl2sql-system-admin-v1"
_CONFIGURED_SYSTEM_ADMIN_TOKEN_TYPE = "configured-system-admin"
_APP_ADMIN_PASSWORD_PATTERN = re.compile(
    r'^(?!.*admin)(?=.*[0-9])(?=.*[a-z])(?=.*[A-Z])(?!.*["]).{12,30}$'
)


def _looks_like_missing_security_schema(exc: Exception) -> bool:
    message = str(exc).upper()
    return "ORA-00942" in message and any(
        object_name in message for object_name in _SECURITY_SCHEMA_OBJECT_NAMES
    )


def _b64url_encode(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).decode("ascii").rstrip("=")


def _b64url_decode(value: str) -> bytes:
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))


def _constant_time_equal(left: str, right: str) -> bool:
    return hmac.compare_digest(left.encode("utf-8"), right.encode("utf-8"))


class SecurityService:
    def __init__(self, store: SecurityStore, settings: Settings) -> None:
        self.store = store
        self.settings = settings
        self._bootstrap_lock = threading.Lock()
        self._bootstrap_checked = False

    def bootstrap(self) -> bool:
        login_name = self.settings.app_admin_username.strip()
        password = self.settings.app_admin_password
        if not login_name or login_name.casefold() == "todo" or not password:
            raise SecurityApiError(
                503,
                "初期システム管理者を作成できません。"
                "APP_ADMIN_USERNAME と APP_ADMIN_PASSWORD を設定してください。",
            )
        self._ensure_configured_system_admin_password_ready()
        password_hash = hash_password(password)
        return self.store.bootstrap(
            login_name=login_name,
            display_name=f"{login_name}（システム管理者）",
            password_hash=password_hash,
        )

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
        login_name: str,
        password: str,
        *,
        request_id: str = "",
        client_ip: str = "",
    ) -> tuple[Principal, str, str]:
        normalized_login = login_name.strip()
        if self._matches_configured_system_admin_login_name(normalized_login):
            self._ensure_configured_system_admin_password_ready()
            if _constant_time_equal(password, self.settings.app_admin_password):
                return self._create_configured_system_admin_session(normalized_login)
            raise LoginFailed()
        try:
            user = self.store.get_user_by_login(normalized_login.casefold())
        except Exception as exc:
            if _looks_like_missing_security_schema(exc):
                raise SecurityApiError(
                    503,
                    "認証テーブルが初期化されていません。"
                    "構成管理者でログインして初期設定を完了してください。",
                ) from exc
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
                user.user_id,
                failed_count=failed_count,
                locked_until=locked_until,
            )
            raise LoginFailed()
        self.store.record_login_success(user.user_id, password_hash=updated_hash)
        token = secrets.token_urlsafe(32)
        csrf_token = secrets.token_urlsafe(32)
        session = SessionRecord(
            session_id=str(uuid4()),
            user_id=user.user_id,
            token_hash=_hash_token(token),
            csrf_token_hash=_hash_token(csrf_token),
            idle_expires_at=now + timedelta(minutes=self.settings.app_auth_idle_timeout_minutes),
            absolute_expires_at=now
            + timedelta(hours=self.settings.app_auth_absolute_timeout_hours),
            last_seen_at=now,
        )
        self.store.create_session(session)
        principal = self._principal_for(user, session)
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
        user = self.store.get_user(session.user_id)
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
        return self._principal_for(user, session)

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
            raise SecurityApiError(
                409,
                "構成管理者のパスワードはアプリケーションから変更できません。",
            )
        user = self.store.get_user(principal.user_id)
        if user is None or not verify_password(current_password, user.password_hash)[0]:
            raise SecurityApiError(400, "現在のパスワードを確認してください。")
        self._validate_new_password(new_password, user.login_name)
        self.store.set_password(user.user_id, hash_password(new_password), force_change=False)
        self.store.revoke_user_sessions(user.user_id)
        # 現 session は revoke 済み。呼び出し側は cookie を削除して再ログインさせる。
        return principal

    def list_users(self) -> list[UserRecord]:
        return self.store.list_users()

    def create_user(
        self,
        *,
        login_name: str,
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
        password = temporary_password or generate_temporary_password()
        self._validate_new_password(password, login_name)
        user = UserRecord(
            user_id=str(uuid4()),
            login_name=login_name,
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
        user_id: str,
        *,
        expected_version: int,
        display_name: str,
        status: str,
        role_ids: list[str],
        actor: Principal,
        request_id: str = "",
        client_ip: str = "",
    ) -> UserRecord:
        current = self.store.get_user(user_id)
        if current is None:
            raise SecurityApiError(404, "ユーザーが見つかりません。")
        normalized_role_ids = list(dict.fromkeys(role_ids))
        current_roles = [self.store.get_role(role_id) for role_id in current.role_ids]
        is_admin = any(role and role.role_code == SYSTEM_ADMIN_ROLE_CODE for role in current_roles)
        next_roles = [self.store.get_role(role_id) for role_id in normalized_role_ids]
        remains_admin = any(
            role and role.role_code == SYSTEM_ADMIN_ROLE_CODE for role in next_roles
        )
        grants_system_admin = (
            SYSTEM_ADMIN_ROLE_ID in normalized_role_ids
            and SYSTEM_ADMIN_ROLE_ID not in current.role_ids
        )
        if grants_system_admin and not current.is_bootstrap_admin:
            raise SecurityApiError(409, _SYSTEM_ADMIN_BOOTSTRAP_ONLY_MESSAGE)
        if (
            is_admin
            and (status != "ACTIVE" or not remains_admin)
            and self.store.count_active_system_admins() <= 1
        ):
            raise SecurityApiError(409, "最後のシステム管理者は無効化または権限解除できません。")
        try:
            updated = self.store.update_user(
                user_id,
                expected_version=expected_version,
                display_name=display_name.strip(),
                status=status,
                role_ids=normalized_role_ids,
            )
        except (SecurityConflict, SecurityNotFound) as exc:
            raise self._store_error(exc) from exc
        if status != "ACTIVE":
            self.store.revoke_user_sessions(user_id)
        return updated

    def reset_password(
        self,
        user_id: str,
        temporary_password: str | None,
        *,
        actor: Principal,
        request_id: str = "",
        client_ip: str = "",
    ) -> tuple[UserRecord, str]:
        user = self.store.get_user(user_id)
        if user is None:
            raise SecurityApiError(404, "ユーザーが見つかりません。")
        password = temporary_password or generate_temporary_password()
        self._validate_new_password(password, user.login_name)
        self.store.set_password(user_id, hash_password(password), force_change=True)
        self.store.revoke_user_sessions(user_id)
        updated = self.store.get_user(user_id)
        if updated is None:
            raise SecurityApiError(404, "ユーザーが見つかりません。")
        return updated, password

    def unlock_user(
        self, user_id: str, *, actor: Principal, request_id: str = "", client_ip: str = ""
    ) -> UserRecord:
        user = self.store.get_user(user_id)
        if user is None:
            raise SecurityApiError(404, "ユーザーが見つかりません。")
        self.store.record_login_success(user_id)
        updated = self.store.get_user(user_id)
        if updated is None:
            raise SecurityApiError(404, "ユーザーが見つかりません。")
        return updated

    def list_roles(self, *, include_archived: bool = False) -> list[RoleRecord]:
        return self.store.list_roles(include_archived=include_archived)

    def create_role(
        self,
        *,
        role_code: str,
        display_name: str,
        description: str,
        permissions: set[str],
        entitlements: list[tuple[str, str, str]],
        actor: Principal,
        request_id: str = "",
        client_ip: str = "",
    ) -> RoleRecord:
        role = self._build_role(
            role_id=str(uuid4()),
            role_code=role_code,
            display_name=display_name,
            description=description,
            permissions=permissions,
            entitlements=entitlements,
            version=1,
        )
        try:
            created = self.store.create_role(role)
        except SecurityConflict as exc:
            raise SecurityApiError(409, str(exc)) from exc
        return created

    def update_role(
        self,
        role_id: str,
        *,
        expected_version: int,
        display_name: str,
        description: str,
        permissions: set[str],
        entitlements: list[tuple[str, str, str]],
        actor: Principal,
        request_id: str = "",
        client_ip: str = "",
    ) -> RoleRecord:
        current = self.store.get_role(role_id)
        if current is None:
            raise SecurityApiError(404, "ロールが見つかりません。")
        if current.is_built_in:
            raise SecurityApiError(409, "組み込み SYSTEM_ADMIN ロールは変更できません。")
        role = self._build_role(
            role_id=role_id,
            role_code=current.role_code,
            display_name=display_name,
            description=description,
            permissions=permissions,
            entitlements=entitlements,
            version=current.version,
        )
        try:
            updated = self.store.update_role(role, expected_version=expected_version)
        except (SecurityConflict, SecurityNotFound) as exc:
            raise self._store_error(exc) from exc
        return updated

    def archive_role(
        self,
        role_id: str,
        *,
        expected_version: int,
        actor: Principal,
        request_id: str = "",
        client_ip: str = "",
    ) -> RoleRecord:
        role = self.store.get_role(role_id)
        if role is None:
            raise SecurityApiError(404, "ロールが見つかりません。")
        if role.is_built_in:
            raise SecurityApiError(409, "組み込み SYSTEM_ADMIN ロールはアーカイブできません。")
        try:
            archived = self.store.archive_role(role_id, expected_version=expected_version)
        except (SecurityConflict, SecurityNotFound) as exc:
            raise self._store_error(exc) from exc
        return archived

    def _matches_configured_system_admin_login_name(self, login_name: str) -> bool:
        configured_login = self.settings.app_admin_username.strip()
        if not configured_login or configured_login.casefold() == "todo":
            return False
        return bool(configured_login) and _constant_time_equal(
            login_name.casefold(), configured_login.casefold()
        )

    def _create_configured_system_admin_session(
        self, login_name: str
    ) -> tuple[Principal, str, str]:
        now = _now()
        self._ensure_configured_system_admin_password_ready()
        csrf_token = secrets.token_urlsafe(32)
        session_id = f"configured-system-admin:{uuid4()}"
        payload = {
            "type": _CONFIGURED_SYSTEM_ADMIN_TOKEN_TYPE,
            "sid": session_id,
            "login": login_name,
            "csrf_hash": _hash_token(csrf_token),
            "exp": int(
                (now + timedelta(hours=self.settings.app_auth_absolute_timeout_hours)).timestamp()
            ),
        }
        token = self._sign_configured_system_admin_payload(payload)
        principal = self._configured_system_admin_principal(
            login_name=login_name,
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
        login_name = str(payload.get("login") or "")
        if not self._matches_configured_system_admin_login_name(login_name):
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
            login_name=login_name,
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
        configured_secret = (
            f"{self.settings.service_name}:"
            f"{self.settings.app_admin_username.strip()}:"
            f"{self.settings.app_admin_password}"
        )
        return hashlib.sha256(configured_secret.encode("utf-8")).digest()

    def _ensure_configured_system_admin_password_ready(self) -> None:
        password = self.settings.app_admin_password
        if (
            password == "TODO"
            or "\r" in password
            or "\n" in password
            or not _APP_ADMIN_PASSWORD_PATTERN.match(password)
        ):
            raise SecurityApiError(
                503,
                "構成管理者の認証情報が設定されていません。"
                "APP_ADMIN_USERNAME と APP_ADMIN_PASSWORD を設定してください。",
            )

    def _configured_system_admin_principal(
        self,
        *,
        login_name: str,
        session_id: str,
        csrf_token_hash: str,
    ) -> Principal:
        return Principal(
            user_id=_CONFIGURED_SYSTEM_ADMIN_USER_ID,
            login_name=login_name,
            display_name=f"{login_name}（構成管理者）",
            status="ACTIVE",
            force_password_change=False,
            role_codes=[SYSTEM_ADMIN_ROLE_CODE],
            permissions=set(ALL_PERMISSION_CODES),
            data_entitlements=[],
            session_id=session_id,
            csrf_token_hash=csrf_token_hash,
            password_change_allowed=False,
        )

    @staticmethod
    def _is_configured_system_admin_principal(principal: Principal) -> bool:
        return (
            principal.user_id == _CONFIGURED_SYSTEM_ADMIN_USER_ID
            and principal.session_id.startswith("configured-system-admin:")
        )

    def _principal_for(self, user: UserRecord, session: SessionRecord) -> Principal:
        roles = [self.store.get_role(role_id) for role_id in user.role_ids]
        active_roles = [role for role in roles if role is not None and not role.archived]
        permissions = expand_permissions(
            {permission for role in active_roles for permission in role.permissions}
        )
        entitlements: dict[tuple[str, str, str], DataEntitlementRecord] = {}
        for role in active_roles:
            for entitlement in role.entitlements:
                key = (entitlement.resource_code, entitlement.scope_code, entitlement.capability)
                entitlements[key] = entitlement
        return Principal(
            user_id=user.user_id,
            login_name=user.login_name,
            display_name=user.display_name,
            status=user.status,
            force_password_change=user.force_password_change,
            role_codes=sorted(role.role_code for role in active_roles),
            permissions=permissions,
            data_entitlements=list(entitlements.values()),
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
        entitlements: list[tuple[str, str, str]],
        version: int,
    ) -> RoleRecord:
        unknown = unknown_permission_codes(permissions)
        if unknown:
            raise SecurityApiError(400, f"未登録の権限コードです: {', '.join(sorted(unknown))}")
        expanded = expand_permissions(permissions)
        data_records = [
            DataEntitlementRecord(
                entitlement_id=str(uuid4()),
                role_id=role_id,
                resource_code=resource,
                scope_code=scope,
                capability=capability,
            )
            for resource, scope, capability in dict.fromkeys(entitlements)
        ]
        return RoleRecord(
            role_id=role_id,
            role_code=role_code,
            display_name=display_name.strip(),
            description=description.strip(),
            is_built_in=False,
            archived=False,
            version=version,
            permissions=expanded,
            entitlements=data_records,
        )

    def _validate_new_password(self, password: str, login_name: str) -> None:
        try:
            validate_password(
                password,
                login_name=login_name,
                min_length=self.settings.app_auth_password_min_length,
                max_length=self.settings.app_auth_password_max_length,
            )
        except PasswordPolicyError as exc:
            raise SecurityApiError(400, str(exc)) from exc

    @staticmethod
    def _store_error(exc: Exception) -> SecurityApiError:
        if isinstance(exc, SecurityNotFound):
            return SecurityApiError(404, str(exc))
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
