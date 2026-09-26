"""共通認証のユースケース（3製品共通。NL2SQL の実装を基準に移設。#212）。

ログイン・ロック・セッション・CSRF・パスワード変更・構成管理者・ユーザー CRUD・ロールの基本操作・
最後の管理者の保護・製品をまたぐ権限昇格の防止を持つ。製品は継承して、実効権限の組み立て
（`_role_permissions` / `_build_principal`）や製品固有の条件を上書きする。
"""

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
from collections.abc import Iterable, Sequence
from datetime import UTC, datetime, timedelta
from typing import Any, Protocol
from uuid import uuid4

from ..users_roles import PasswordPolicyError, generate_temporary_password, validate_password
from .domain import (
    CONFIGURED_SYSTEM_ADMIN_USER_UUID,
    FIXED_ADMIN_LOGIN_USER_ID,
    SYSTEM_ADMIN_ROLE_CODE,
    SYSTEM_ADMIN_ROLE_ID,
    Principal,
    RoleRecord,
    SessionRecord,
    UserIdentity,
    UserRecord,
)
from .errors import (
    LoginFailed,
    SecurityApiError,
    SecurityConflict,
    SecurityMigrationRequired,
    SecurityNotFound,
)
from .passwords import hash_password, verify_password
from .store import PLATFORM_AUTH_TABLES, PRODUCT_ROLE_PERMISSION_TABLES, AuthStore

logger = logging.getLogger(__name__)

_SECURITY_CONFLICT_TITLES = {
    "SECURITY_USER_LOGIN_ID_CONFLICT": "ユーザーを作成できません",
    "SECURITY_ROLE_CODE_CONFLICT": "ロールを作成できません",
    "SECURITY_ROLE_CODE_RESERVED": "ロールを作成できません",
}
_SYSTEM_ADMIN_BOOTSTRAP_ONLY_MESSAGE = (
    "SYSTEM_ADMIN ロールは初期システム管理者にのみ割り当てできます。"
)
_SYSTEM_ADMIN_ROLE_CODE_RESERVED_MESSAGE = (
    "SYSTEM_ADMIN は組み込みロール専用のコードです。別のロールコードを入力してください。"
)
_CONFIGURED_SYSTEM_ADMIN_TOKEN_TYPE = "configured-system-admin"  # nosec B105
_CONFIGURED_SYSTEM_ADMIN_DISPLAY_NAME = f"{FIXED_ADMIN_LOGIN_USER_ID}（システム管理者）"
_ADMIN_PASSWORD_PATTERN = re.compile(
    r'^(?!.*admin)(?=.*[0-9])(?=.*[a-z])(?=.*[A-Z])(?!.*["]).{12,30}$'
)
_CROSS_PRODUCT_DENIED_MESSAGE = (
    "このロールには、あなたが持たない他の製品の権限が含まれています。"
    "システム管理者に割り当てを依頼してください。"
)


class AuthSettings(Protocol):
    service_name: str
    app_admin_login_user_id: str
    app_admin_login_user_password: str
    app_auth_absolute_timeout_hours: int
    app_auth_idle_timeout_minutes: int
    app_auth_failed_login_limit: int
    app_auth_lockout_minutes: int
    app_auth_password_min_length: int
    app_auth_password_max_length: int
    app_auth_argon2_time_cost: int
    app_auth_argon2_memory_kib: int
    app_auth_argon2_parallelism: int


def _now() -> datetime:
    return datetime.now(UTC)


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _aware(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value


def _b64url_encode(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).decode("ascii").rstrip("=")


def _b64url_decode(value: str) -> bytes:
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))


def constant_time_equal(left: str, right: str) -> bool:
    return hmac.compare_digest(left.encode("utf-8"), right.encode("utf-8"))


class AuthService:
    """共通認証のサービス。製品は継承してクラス属性と hook を上書きする。"""

    # 製品のキー（`PRODUCT_ROLE_PERMISSION_TABLES` のキー）。他製品の権限テーブルの判定に使う。
    product_key: str = ""
    principal_class: type[Principal] = Principal
    role_class: type[RoleRecord] = RoleRecord
    # アーカイブ済みを含む全ロールを参照できる権限（ロール管理・権限管理など）。
    role_catalog_permissions: frozenset[str] = frozenset()
    # 構成管理者の token の接頭辞。製品ごとに変える（既存 token を無効にしないため）。
    configured_admin_token_prefix: str = "platform-system-admin-v1"
    admin_login_env_key: str = "PLATFORM_ADMIN_LOGIN_USER_ID"
    admin_password_env_key: str = "PLATFORM_ADMIN_LOGIN_USER_PASSWORD"  # nosec B105
    migration_hint: str = "security migration を適用してから再試行してください。"
    # ORA-00942 のとき「migration が必要」と判定する object。製品は自分のテーブルを足す。
    schema_object_names: frozenset[str] = frozenset(PLATFORM_AUTH_TABLES)

    def __init__(self, store: AuthStore, settings: AuthSettings) -> None:
        self.store = store
        self.settings = settings
        self._bootstrap_lock = threading.Lock()
        self._bootstrap_checked = False

    # ---- bootstrap ----

    def bootstrap(self) -> bool:
        self._ensure_configured_system_admin_ready()
        return False

    def ensure_bootstrapped(self) -> None:
        """process ごとに一度だけ、構成管理者の設定を確認する。"""
        if self._bootstrap_checked:
            return
        with self._bootstrap_lock:
            if self._bootstrap_checked:
                return
            self.bootstrap()
            self._bootstrap_checked = True

    # ---- login / session ----

    def login(
        self,
        login_user_id: str,
        password: str,
        *,
        request_id: str = "",
        client_ip: str = "",
    ) -> tuple[Principal, str, str]:
        normalized_login_user_id = login_user_id.strip()
        if normalized_login_user_id == FIXED_ADMIN_LOGIN_USER_ID:
            _, configured_password = self._ensure_configured_system_admin_ready()
            if constant_time_equal(password, configured_password):
                return self._create_configured_system_admin_session()
            raise LoginFailed()
        if normalized_login_user_id.casefold() == FIXED_ADMIN_LOGIN_USER_ID:
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
        verified, updated_hash = self._verify_password(password, user.password_hash)
        if not verified:
            failed_count = user.failed_login_count + 1
            locked_until = None
            if failed_count >= self.settings.app_auth_failed_login_limit:
                locked_until = now + timedelta(minutes=self.settings.app_auth_lockout_minutes)
                failed_count = 0
            self.store.record_login_failure(
                user.user_uuid, failed_count=failed_count, locked_until=locked_until
            )
            raise LoginFailed()
        self.store.record_login_success(user.user_uuid, password_hash=updated_hash)
        token = secrets.token_urlsafe(32)
        csrf_token = secrets.token_urlsafe(32)
        session = SessionRecord(
            session_id=str(uuid4()),
            user_uuid=user.user_uuid,
            token_hash=hash_token(token),
            csrf_token_hash=hash_token(csrf_token),
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
        session = self.store.get_session_by_token_hash(hash_token(token))
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
        self.store.touch_session(session.session_id, last_seen_at=now, idle_expires_at=idle_expires)
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
        if not hmac.compare_digest(hash_token(header_token), principal.csrf_token_hash):
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
            if not constant_time_equal(current_password, configured_password):
                raise SecurityApiError(400, "現在のパスワードを確認してください。")
            self._validate_configured_system_admin_password_for_change(new_password)
            self._write_configured_system_admin_password(new_password)
            self.settings.app_admin_login_user_id = FIXED_ADMIN_LOGIN_USER_ID
            self.settings.app_admin_login_user_password = new_password
            return principal
        user = self.store.get_user(principal.user_uuid)
        if user is None or not self._verify_password(current_password, user.password_hash)[0]:
            raise SecurityApiError(400, "現在のパスワードを確認してください。")
        self._validate_new_password(new_password, user.login_user_id)
        self.store.set_password(
            user.user_uuid, self._hash_password(new_password), force_change=False
        )
        self.store.revoke_user_sessions(user.user_uuid)
        # 現 session は revoke 済み。呼び出し側は cookie を削除して再ログインさせる。
        return principal

    # ---- users ----

    def list_users(self) -> list[UserRecord]:
        try:
            return self.store.list_users()
        except Exception as exc:
            self._raise_security_migration_if_needed(exc)
            raise

    def history_user_identities(
        self, actor: Principal, user_uuids: list[str]
    ) -> dict[str, UserIdentity]:
        if not actor.is_system_admin:
            raise SecurityApiError(403, "履歴の実行者情報はシステム管理者のみ確認できます。")
        # 構成管理者は認証テーブルに存在しない。履歴表示には公開 identity だけを補完する。
        stored_uuids = [
            user_uuid
            for user_uuid in user_uuids
            if user_uuid and user_uuid != CONFIGURED_SYSTEM_ADMIN_USER_UUID
        ]
        identities = self.store.get_user_identities(stored_uuids) if stored_uuids else {}
        if CONFIGURED_SYSTEM_ADMIN_USER_UUID in user_uuids:
            identities[CONFIGURED_SYSTEM_ADMIN_USER_UUID] = UserIdentity(
                user_uuid=CONFIGURED_SYSTEM_ADMIN_USER_UUID,
                login_user_id=FIXED_ADMIN_LOGIN_USER_ID,
                display_name=_CONFIGURED_SYSTEM_ADMIN_DISPLAY_NAME,
            )
        return identities

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
        if normalized_login_user_id.casefold() == FIXED_ADMIN_LOGIN_USER_ID:
            raise SecurityApiError(409, "system_admin は構成管理者専用のログインユーザーIDです。")
        password = temporary_password or generate_temporary_password()
        self._validate_new_password(password, normalized_login_user_id)
        user = UserRecord(
            user_uuid=str(uuid4()),
            login_user_id=normalized_login_user_id,
            display_name=display_name.strip(),
            password_hash=self._hash_password(password),
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
            actor, normalized_role_ids, existing_role_ids=current.role_ids
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
                409, "初期システム管理者は削除できません。", code="SECURITY_USER_DELETE_PROTECTED"
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
        self.store.set_password(user_uuid, self._hash_password(password), force_change=True)
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

    # ---- roles ----

    def list_roles(self, *, include_archived: bool = False) -> Sequence[RoleRecord]:
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
        if actor.has_any_permission(self.role_catalog_permissions):
            return list(self.list_roles(include_archived=include_archived))
        roles = self.list_roles(include_archived=False)
        assignable = self._assignable_role_ids(actor, roles)
        return [role for role in roles if role.role_id in assignable]

    def get_role_for_actor(self, role_id: str, actor: Principal) -> RoleRecord | None:
        role = self.get_role(role_id)
        if role is None:
            return None
        if actor.has_any_permission(self.role_catalog_permissions):
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
        actor: Principal,
        request_id: str = "",
        client_ip: str = "",
    ) -> RoleRecord:
        """ロール管理画面の新規作成（基本情報だけ）。"""
        normalized_role_code = self._assert_role_code_not_reserved(role_code)
        role = self.role_class(
            role_id=str(uuid4()),
            role_code=normalized_role_code,
            display_name=display_name.strip(),
            description=description.strip(),
            is_built_in=False,
            archived=False,
            version=1,
        )
        try:
            return self.store.create_role(role)
        except SecurityConflict as exc:
            raise self._store_error(exc) from exc

    def update_role(
        self,
        role_id: str,
        *,
        expected_version: int,
        display_name: str | None = None,
        description: str | None = None,
        actor: Principal,
        request_id: str = "",
        client_ip: str = "",
    ) -> RoleRecord:
        """ロール管理画面の基本情報の更新。製品固有のデータは現在値を保つ。"""
        current = self._editable_role(role_id)
        current.display_name = (
            current.display_name if display_name is None else display_name.strip()
        )
        current.description = current.description if description is None else description.strip()
        try:
            return self.store.update_role(current, expected_version=expected_version)
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
            return self.store.archive_role(role_id, expected_version=expected_version)
        except (SecurityConflict, SecurityNotFound) as exc:
            raise self._store_error(exc) from exc

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
        # アーカイブ中の実効権限は空。復元は全権限の再付与として検証する。
        self._assert_actor_can_restore_role(actor, role)
        if not actor.is_system_admin and not self._cross_product_permissions_within_actor(
            actor, [role.role_id]
        ):
            raise SecurityApiError(403, _CROSS_PRODUCT_DENIED_MESSAGE)
        try:
            return self.store.restore_role(role_id, expected_version=expected_version)
        except (SecurityConflict, SecurityNotFound) as exc:
            raise self._store_error(exc) from exc

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
        self._assert_role_deletable(role)
        try:
            self.store.delete_role(role_id, expected_version=expected_version)
        except (SecurityConflict, SecurityNotFound) as exc:
            raise self._store_error(exc) from exc
        return role

    def _editable_role(self, role_id: str) -> RoleRecord:
        current = self.get_role(role_id)
        if current is None:
            raise SecurityApiError(404, "ロールが見つかりません。")
        if current.is_built_in:
            raise SecurityApiError(409, "組み込み SYSTEM_ADMIN ロールは変更できません。")
        if current.archived:
            raise SecurityApiError(409, "アーカイブ済みロールは変更できません。")
        return current

    @staticmethod
    def _assert_role_code_not_reserved(role_code: str) -> str:
        normalized_role_code = role_code.strip().upper()
        if normalized_role_code == SYSTEM_ADMIN_ROLE_CODE:
            raise SecurityApiError(
                409,
                _SYSTEM_ADMIN_ROLE_CODE_RESERVED_MESSAGE,
                code="SECURITY_ROLE_CODE_RESERVED",
                title=_SECURITY_CONFLICT_TITLES["SECURITY_ROLE_CODE_RESERVED"],
                field_errors=(
                    {
                        "pointer": "/role_code",
                        "code": "reserved",
                        "message": _SYSTEM_ADMIN_ROLE_CODE_RESERVED_MESSAGE,
                    },
                ),
            )
        return normalized_role_code

    # ---- product hooks ----

    def all_permissions(self) -> set[str]:
        """構成管理者・local debug に与える全権限（製品のカタログ）。"""
        return set()

    def _role_permissions(self, roles: Sequence[RoleRecord]) -> set[str]:
        """有効なロールから、この製品の実効権限を組み立てる。"""
        return set()

    def _build_principal(
        self, user: UserRecord, session: SessionRecord, active_roles: list[RoleRecord]
    ) -> Principal:
        return self.principal_class(**self._principal_kwargs(user, session, active_roles))

    def _principal_kwargs(
        self, user: UserRecord, session: SessionRecord, active_roles: list[RoleRecord]
    ) -> dict[str, Any]:
        return {
            "user_uuid": user.user_uuid,
            "login_user_id": user.login_user_id,
            "display_name": user.display_name,
            "status": user.status,
            "force_password_change": user.force_password_change,
            "role_codes": sorted(role.role_code for role in active_roles),
            "permissions": self._role_permissions(active_roles),
            "session_id": session.session_id,
            "csrf_token_hash": session.csrf_token_hash,
            "role_ids": [role.role_id for role in active_roles],
        }

    def _role_within_actor(self, actor: Principal, role: RoleRecord) -> bool:
        """この製品の権限で、ロールが操作者の権限に収まるか（製品の暗黙権限の展開を含む）。"""
        return True

    def _assert_actor_can_restore_role(self, actor: Principal, role: RoleRecord) -> None:
        """この製品の権限で、ロールを復元してよいか。"""

    def _assert_role_deletable(self, role: RoleRecord) -> None:
        """製品固有の削除条件（例: Data Grant が残っていないこと）。"""

    # ---- principal ----

    def principal_for_worker(self, user_uuid: str) -> Principal:
        """受理後の非対話実行でも現在の user/role 権限を再計算する。"""
        if user_uuid == CONFIGURED_SYSTEM_ADMIN_USER_UUID:
            login_user_id, _ = self._ensure_configured_system_admin_ready()
            return self._configured_system_admin_principal(
                login_user_id=login_user_id,
                session_id="configured-system-admin:worker",
                csrf_token_hash="",  # nosec B106 - worker は browser session を作成しない
            )
        user = self.store.get_user(user_uuid)
        if user is None or user.status != "ACTIVE" or user.force_password_change:
            raise SecurityApiError(403, "生成を受け付けたユーザーの実行権限を確認できません。")
        current = _now()
        return self._principal_for(
            user,
            SessionRecord(
                session_id="worker",
                user_uuid=user_uuid,
                token_hash="",  # nosec B106 - 認証 token として保存・使用しない
                csrf_token_hash="",  # nosec B106 - worker は browser session を作成しない
                idle_expires_at=current,
                absolute_expires_at=current,
                last_seen_at=current,
            ),
        )

    def _principal_for(self, user: UserRecord, session: SessionRecord) -> Principal:
        roles = [self.get_role(role_id) for role_id in user.role_ids]
        active_roles = [role for role in roles if role is not None and not role.archived]
        return self._build_principal(user, session, active_roles)

    # ---- configured system admin ----

    def _create_configured_system_admin_session(self) -> tuple[Principal, str, str]:
        now = _now()
        configured_login_user_id, _ = self._ensure_configured_system_admin_ready()
        csrf_token = secrets.token_urlsafe(32)
        session_id = f"configured-system-admin:{uuid4()}"
        payload = {
            "type": _CONFIGURED_SYSTEM_ADMIN_TOKEN_TYPE,
            "sid": session_id,
            "user_uuid": CONFIGURED_SYSTEM_ADMIN_USER_UUID,
            "login_user_id": configured_login_user_id,
            "csrf_hash": hash_token(csrf_token),
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
        prefix = self.configured_admin_token_prefix + "."
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
        configured_login_user_id, _ = self._ensure_configured_system_admin_ready()
        if not constant_time_equal(login_user_id, configured_login_user_id):
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
        return f"{self.configured_admin_token_prefix}.{payload_segment}.{signature}"

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
        if login_user_id != FIXED_ADMIN_LOGIN_USER_ID:
            raise SecurityApiError(
                503,
                "構成管理者の認証情報が正しく設定されていません。"
                f"{self.admin_login_env_key} は system_admin に固定してください。",
            )
        self._validate_configured_system_admin_password(password)
        return login_user_id, password

    def _configured_system_admin_credentials(self) -> tuple[str, str]:
        """構成管理者の (login_user_id, password)。製品は `.env` から読むよう上書きできる。"""
        return (
            self.settings.app_admin_login_user_id.strip(),
            self.settings.app_admin_login_user_password,
        )

    def _validate_configured_system_admin_password(self, password: str) -> None:
        if (
            password == "TODO"  # nosec B105
            or "\r" in password
            or "\n" in password
            or not _ADMIN_PASSWORD_PATTERN.match(password)
        ):
            raise SecurityApiError(
                503,
                "構成管理者の認証情報が設定されていません。"
                f"{self.admin_login_env_key} と {self.admin_password_env_key} を設定してください。",
            )

    @staticmethod
    def _validate_configured_system_admin_password_for_change(password: str) -> None:
        if (
            password == "TODO"  # nosec B105
            or "\r" in password
            or "\n" in password
            or not _ADMIN_PASSWORD_PATTERN.match(password)
        ):
            raise SecurityApiError(
                400,
                "新しいパスワードは12〜30文字で、大文字・小文字・数字を含め、"
                "admin と二重引用符を含めないでください。",
            )

    def _write_configured_system_admin_password(self, password: str) -> None:
        """構成管理者のパスワードを `.env` へ書き戻す。製品が上書きする。"""
        raise SecurityApiError(409, "構成管理者のパスワードは .env で変更してください。")

    def _configured_system_admin_principal(
        self,
        *,
        login_user_id: str,
        session_id: str,
        csrf_token_hash: str,
    ) -> Principal:
        return self.principal_class(
            user_uuid=CONFIGURED_SYSTEM_ADMIN_USER_UUID,
            login_user_id=login_user_id,
            display_name=_CONFIGURED_SYSTEM_ADMIN_DISPLAY_NAME,
            status="ACTIVE",
            force_password_change=False,
            role_codes=[SYSTEM_ADMIN_ROLE_CODE],
            permissions=self.all_permissions(),
            session_id=session_id,
            csrf_token_hash=csrf_token_hash,
            password_change_allowed=True,
        )

    @staticmethod
    def _is_configured_system_admin_principal(principal: Principal) -> bool:
        return (
            principal.user_uuid == CONFIGURED_SYSTEM_ADMIN_USER_UUID
            and principal.session_id.startswith("configured-system-admin:")
        )

    # ---- escalation ----

    def _assert_actor_can_assign_roles(
        self,
        actor: Principal,
        role_ids: list[str],
        *,
        existing_role_ids: Iterable[str] = (),
    ) -> None:
        existing_role_id_set = set(existing_role_ids)
        roles: list[RoleRecord] = []
        for role_id in role_ids:
            role = self.get_role(role_id)
            if role is None or role.archived:
                if role_id in existing_role_id_set:
                    continue
                raise SecurityApiError(404, "指定された有効なロールが見つかりません。")
            roles.append(role)
        assignable = self._assignable_role_ids(actor, roles)
        if any(role.role_id not in assignable for role in roles):
            raise SecurityApiError(403, "このロールを割り当てる権限がありません。")

    def _assert_actor_can_manage_user(self, actor: Principal, user: UserRecord) -> None:
        if actor.is_system_admin:
            return
        roles = [
            role
            for role_id in user.role_ids
            if (role := self.get_role(role_id)) is not None and not role.archived
        ]
        assignable = self._assignable_role_ids(actor, roles)
        if any(role.role_id not in assignable for role in roles):
            raise SecurityApiError(403, "このユーザーを管理する権限がありません。")

    def _actor_can_assign_role(self, actor: Principal, role: RoleRecord) -> bool:
        return role.role_id in self._assignable_role_ids(actor, [role])

    def _assignable_role_ids(self, actor: Principal, roles: Sequence[RoleRecord]) -> set[str]:
        """操作者が割り当ててよいロール。この製品の判定と、他製品の権限の判定の両方を通るもの。"""
        if actor.is_system_admin:
            return {role.role_id for role in roles}
        candidates = [
            role
            for role in roles
            if role.role_code != SYSTEM_ADMIN_ROLE_CODE
            and not role.archived
            and self._role_within_actor(actor, role)
        ]
        if not candidates:
            return set()
        allowed = self._cross_product_allowed_role_ids(actor, [role.role_id for role in candidates])
        return {role.role_id for role in candidates if role.role_id in allowed}

    def _cross_product_permissions_within_actor(
        self, actor: Principal, role_ids: Sequence[str]
    ) -> bool:
        return set(role_ids) <= self._cross_product_allowed_role_ids(actor, role_ids)

    def _cross_product_allowed_role_ids(
        self, actor: Principal, role_ids: Sequence[str]
    ) -> set[str]:
        """他製品の権限テーブルで、ロールの権限コードが操作者の有効ロールの権限に収まるもの。

        他製品の暗黙権限の展開はこの製品では分からないため、生のコードで比べる（厳しい側）。
        """
        tables = [
            table
            for key, table in PRODUCT_ROLE_PERMISSION_TABLES.items()
            if key != self.product_key
        ]
        if not tables:
            return set(role_ids)
        codes = self.store.role_permission_codes([*role_ids, *actor.role_ids], tables=tables)
        allowed: set[str] = set()
        for role_id in role_ids:
            if all(
                codes[table].get(role_id, set())
                <= {
                    code
                    for actor_role_id in actor.role_ids
                    for code in codes[table].get(actor_role_id, set())
                }
                for table in tables
            ):
                allowed.add(role_id)
        return allowed

    # ---- helpers ----

    def _hash_password(self, password: str) -> str:
        return hash_password(
            password,
            time_cost=self.settings.app_auth_argon2_time_cost,
            memory_kib=self.settings.app_auth_argon2_memory_kib,
            parallelism=self.settings.app_auth_argon2_parallelism,
        )

    def _verify_password(self, password: str, password_hash: str) -> tuple[bool, str | None]:
        return verify_password(
            password,
            password_hash,
            time_cost=self.settings.app_auth_argon2_time_cost,
            memory_kib=self.settings.app_auth_argon2_memory_kib,
            parallelism=self.settings.app_auth_argon2_parallelism,
        )

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

    def _schema_object_names(self) -> frozenset[str]:
        return self.schema_object_names | frozenset(
            getattr(self.store, "schema_object_names", frozenset())
        )

    def _raise_security_migration_if_needed(self, exc: Exception) -> None:
        object_names = self._schema_object_names()
        message = str(exc).upper()
        if not isinstance(exc, SecurityMigrationRequired) and not (
            "ORA-00942" in message and any(name in message for name in object_names)
        ):
            return
        object_name = (
            exc.object_name
            if isinstance(exc, SecurityMigrationRequired)
            else next((name for name in object_names if name in message), "UNKNOWN")
        )
        raw = exc.__cause__ if isinstance(exc, SecurityMigrationRequired) and exc.__cause__ else exc
        code_match = re.search(r"\bORA-\d{5}\b", str(raw).upper())
        logger.error(
            "security_schema_migration_required",
            extra={
                "database_object": object_name,
                "oracle_error_code": code_match.group(0) if code_match else "ORA-00942",
                "error_code": "SECURITY_SCHEMA_MIGRATION_REQUIRED",
            },
        )
        raise SecurityApiError(
            409,
            "アプリケーション認証/RBAC の schema migration が未適用です。" + self.migration_hint,
            code="SECURITY_SCHEMA_MIGRATION_REQUIRED",
            title="セキュリティ初期化が必要です",
        ) from exc

    @staticmethod
    def _store_error(exc: Exception) -> SecurityApiError:
        if isinstance(exc, SecurityNotFound):
            return SecurityApiError(404, str(exc))
        if isinstance(exc, SecurityConflict):
            field_errors = (
                ({"pointer": exc.pointer, "code": exc.field_code, "message": str(exc)},)
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
