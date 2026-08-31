"""認証/RBAC の永続化境界。DDL は migration 004 の責務。"""

from __future__ import annotations

import copy
import json
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from typing import Any, Protocol
from uuid import uuid4

from app.features.nl2sql.oracle_adapter import OracleNl2SqlAdapter
from app.settings import Settings

from .domain import (
    SYSTEM_ADMIN_ROLE_CODE,
    SYSTEM_ADMIN_ROLE_ID,
    DataEntitlementRecord,
    RoleRecord,
    SessionRecord,
    UserRecord,
    scope_filters_canonical_json,
    scope_filters_from_json,
)


class SecurityStoreError(RuntimeError):
    """認証 store の基底例外。"""


class SecurityNotFound(SecurityStoreError):
    pass


class SecurityConflict(SecurityStoreError):
    """競合の機械判定情報を store から service へ安全に伝える。"""

    def __init__(
        self,
        message: str,
        *,
        code: str = "SECURITY_STATE_CONFLICT",
        pointer: str | None = None,
        field_code: str = "conflict",
    ) -> None:
        super().__init__(message)
        self.code = code
        self.pointer = pointer
        self.field_code = field_code


class SecurityMigrationRequired(SecurityStoreError):
    """security migration が必要な DB object に到達した。"""

    def __init__(self, object_name: str) -> None:
        self.object_name = object_name
        super().__init__(f"{object_name} security migration is required.")


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


def _missing_security_migration_object(exc: Exception) -> str | None:
    if isinstance(exc, SecurityMigrationRequired):
        return exc.object_name
    message = str(exc).upper()
    if "ORA-00942" not in message:
        return None
    return next(
        (object_name for object_name in _SECURITY_SCHEMA_OBJECT_NAMES if object_name in message),
        None,
    )


def _raise_missing_security_migration_if_needed(exc: Exception, object_name: str) -> None:
    missing_object = _missing_security_migration_object(exc)
    if missing_object == object_name.upper() or (
        missing_object is None and "ORA-00942" in str(exc).upper()
    ):
        raise SecurityMigrationRequired(object_name.upper()) from exc


class SecurityStore(Protocol):
    def bootstrap(self, *, login_user_id: str, display_name: str, password_hash: str) -> bool: ...
    def get_user_by_login_user_id(self, normalized_login_user_id: str) -> UserRecord | None: ...
    def get_user(self, user_uuid: str) -> UserRecord | None: ...
    def list_users(self) -> list[UserRecord]: ...
    def create_user(self, user: UserRecord) -> UserRecord: ...
    def update_user(
        self,
        user_uuid: str,
        *,
        expected_version: int,
        display_name: str,
        status: str,
        role_ids: list[str],
    ) -> UserRecord: ...
    def delete_user(self, user_uuid: str, *, expected_version: int) -> None: ...
    def set_password(self, user_uuid: str, password_hash: str, *, force_change: bool) -> None: ...
    def record_login_failure(
        self, user_uuid: str, *, failed_count: int, locked_until: datetime | None
    ) -> None: ...
    def record_login_success(self, user_uuid: str, *, password_hash: str | None = None) -> None: ...
    def list_roles(self, *, include_archived: bool = False) -> list[RoleRecord]: ...
    def get_role(self, role_id: str) -> RoleRecord | None: ...
    def create_role(self, role: RoleRecord) -> RoleRecord: ...
    def update_role(self, role: RoleRecord, *, expected_version: int) -> RoleRecord: ...
    def archive_role(self, role_id: str, *, expected_version: int) -> RoleRecord: ...
    def restore_role(self, role_id: str, *, expected_version: int) -> RoleRecord: ...
    def delete_role(self, role_id: str, *, expected_version: int) -> None: ...
    def count_active_system_admins(self) -> int: ...
    def create_session(self, session: SessionRecord) -> None: ...
    def get_session_by_token_hash(self, token_hash: str) -> SessionRecord | None: ...
    def touch_session(
        self, session_id: str, *, last_seen_at: datetime, idle_expires_at: datetime
    ) -> None: ...
    def revoke_session(self, session_id: str) -> None: ...
    def revoke_user_sessions(self, user_uuid: str) -> None: ...
    def get_deepsec_states(self) -> dict[tuple[str, int], dict[str, object]]: ...
    def set_deepsec_state(
        self,
        *,
        version: str,
        step_no: int,
        step_key: str,
        checksum: str,
        status: str,
        error_message: str,
        executed_by_user_uuid: str | None,
    ) -> None: ...
    def clear_deepsec_states(self, *, version: str, step_numbers: list[int]) -> None: ...
    def set_deepsec_entitlement_apply_state(
        self,
        entitlement_id: str,
        *,
        status: str,
        data_grant_name: str = "",
        sql_checksum: str = "",
        error_message: str = "",
    ) -> None: ...
    def clear_deepsec_entitlement_apply_states(self) -> None: ...


def _now() -> datetime:
    return datetime.now(UTC)


def _copy_optional[T](value: T | None) -> T | None:
    return copy.deepcopy(value) if value is not None else None


class InMemorySecurityStore:
    """単体テスト用。production は OracleSecurityStore を使う。"""

    def __init__(self) -> None:
        self.users: dict[str, UserRecord] = {}
        self.roles: dict[str, RoleRecord] = {}
        self.sessions: dict[str, SessionRecord] = {}
        self.deepsec_states: dict[tuple[str, int], dict[str, object]] = {}
        self._lock = threading.RLock()

    def bootstrap(self, *, login_user_id: str, display_name: str, password_hash: str) -> bool:
        with self._lock:
            self._ensure_system_admin_role()
            if self.users:
                return False
            user = UserRecord(
                user_uuid=str(uuid4()),
                login_user_id=login_user_id,
                display_name=display_name,
                password_hash=password_hash,
                status="ACTIVE",
                force_password_change=True,
                failed_login_count=0,
                locked_until=None,
                version=1,
                role_ids=[SYSTEM_ADMIN_ROLE_ID],
                is_bootstrap_admin=True,
            )
            self.users[user.user_uuid] = user
            return True

    def _ensure_system_admin_role(self) -> None:
        if SYSTEM_ADMIN_ROLE_ID in self.roles:
            return
        self.roles[SYSTEM_ADMIN_ROLE_ID] = RoleRecord(
            role_id=SYSTEM_ADMIN_ROLE_ID,
            role_code=SYSTEM_ADMIN_ROLE_CODE,
            display_name="システム管理者",
            description="すべてのアプリケーション機能を管理する組み込みロールです。",
            is_built_in=True,
            archived=False,
            version=1,
            entitlements=[],
        )

    def get_user_by_login_user_id(self, normalized_login_user_id: str) -> UserRecord | None:
        with self._lock:
            return _copy_optional(
                next(
                    (
                        user
                        for user in self.users.values()
                        if user.login_user_id.casefold() == normalized_login_user_id.casefold()
                    ),
                    None,
                )
            )

    def get_user(self, user_uuid: str) -> UserRecord | None:
        with self._lock:
            return _copy_optional(self.users.get(user_uuid))

    def list_users(self) -> list[UserRecord]:
        with self._lock:
            return [
                copy.deepcopy(item)
                for item in sorted(self.users.values(), key=lambda u: u.login_user_id)
            ]

    def create_user(self, user: UserRecord) -> UserRecord:
        with self._lock:
            if any(
                item.login_user_id.casefold() == user.login_user_id.casefold()
                for item in self.users.values()
            ):
                raise SecurityConflict(
                    "このログインユーザーIDは既に使用されています。別のIDを入力してください。",
                    code="SECURITY_USER_LOGIN_ID_CONFLICT",
                    pointer="/login_user_id",
                    field_code="already_exists",
                )
            self._validate_role_ids(user.role_ids)
            self.users[user.user_uuid] = copy.deepcopy(user)
            return copy.deepcopy(user)

    def update_user(
        self,
        user_uuid: str,
        *,
        expected_version: int,
        display_name: str,
        status: str,
        role_ids: list[str],
    ) -> UserRecord:
        with self._lock:
            user = self.users.get(user_uuid)
            if user is None:
                raise SecurityNotFound("ユーザーが見つかりません。")
            if user.version != expected_version:
                raise SecurityConflict("ユーザーが別の操作で更新されています。")
            self._validate_role_ids(
                role_ids,
                allow_inactive_role_ids=set(user.role_ids),
            )
            removes_last_admin = (
                user.status == "ACTIVE"
                and SYSTEM_ADMIN_ROLE_ID in user.role_ids
                and (status != "ACTIVE" or SYSTEM_ADMIN_ROLE_ID not in role_ids)
                and self.count_active_system_admins() <= 1
            )
            if removes_last_admin:
                raise SecurityConflict("最後のシステム管理者は無効化または権限解除できません。")
            user.display_name = display_name
            user.status = status
            user.role_ids = list(dict.fromkeys(role_ids))
            user.version += 1
            return copy.deepcopy(user)

    def set_password(self, user_uuid: str, password_hash: str, *, force_change: bool) -> None:
        with self._lock:
            user = self._required_user(user_uuid)
            user.password_hash = password_hash
            user.force_password_change = force_change
            user.failed_login_count = 0
            user.locked_until = None
            user.version += 1

    def delete_user(self, user_uuid: str, *, expected_version: int) -> None:
        with self._lock:
            user = self.users.get(user_uuid)
            if user is None:
                raise SecurityNotFound("ユーザーが見つかりません。")
            if user.version != expected_version:
                raise SecurityConflict("ユーザーが別の操作で更新されています。")
            if user.status != "DISABLED":
                raise SecurityConflict(
                    "ユーザーを先に無効化してから削除してください。",
                    code="SECURITY_USER_DELETE_REQUIRES_DISABLED",
                )
            if user.is_bootstrap_admin:
                raise SecurityConflict(
                    "初期システム管理者は削除できません。",
                    code="SECURITY_USER_DELETE_PROTECTED",
                )
            self.sessions = {
                session_id: session
                for session_id, session in self.sessions.items()
                if session.user_uuid != user_uuid
            }
            del self.users[user_uuid]

    def record_login_failure(
        self, user_uuid: str, *, failed_count: int, locked_until: datetime | None
    ) -> None:
        with self._lock:
            user = self._required_user(user_uuid)
            user.failed_login_count = failed_count
            user.locked_until = locked_until

    def record_login_success(self, user_uuid: str, *, password_hash: str | None = None) -> None:
        with self._lock:
            user = self._required_user(user_uuid)
            user.failed_login_count = 0
            user.locked_until = None
            if password_hash:
                user.password_hash = password_hash

    def list_roles(self, *, include_archived: bool = False) -> list[RoleRecord]:
        with self._lock:
            roles = [item for item in self.roles.values() if include_archived or not item.archived]
            return [copy.deepcopy(item) for item in sorted(roles, key=lambda role: role.role_code)]

    def get_role(self, role_id: str) -> RoleRecord | None:
        with self._lock:
            return _copy_optional(self.roles.get(role_id))

    def create_role(self, role: RoleRecord) -> RoleRecord:
        with self._lock:
            if any(item.role_code == role.role_code for item in self.roles.values()):
                raise SecurityConflict(
                    "このロールコードは既に使用されています。別のコードを入力してください。",
                    code="SECURITY_ROLE_CODE_CONFLICT",
                    pointer="/role_code",
                    field_code="already_exists",
                )
            self.roles[role.role_id] = copy.deepcopy(role)
            return copy.deepcopy(role)

    def update_role(self, role: RoleRecord, *, expected_version: int) -> RoleRecord:
        with self._lock:
            current = self.roles.get(role.role_id)
            if current is None:
                raise SecurityNotFound("ロールが見つかりません。")
            if current.version != expected_version:
                raise SecurityConflict("ロールが別の操作で更新されています。")
            role.version = expected_version + 1
            self.roles[role.role_id] = copy.deepcopy(role)
            return copy.deepcopy(role)

    def archive_role(self, role_id: str, *, expected_version: int) -> RoleRecord:
        role = self.get_role(role_id)
        if role is None:
            raise SecurityNotFound("ロールが見つかりません。")
        role.archived = True
        return self.update_role(role, expected_version=expected_version)

    def restore_role(self, role_id: str, *, expected_version: int) -> RoleRecord:
        role = self.get_role(role_id)
        if role is None:
            raise SecurityNotFound("ロールが見つかりません。")
        if not role.archived:
            raise SecurityConflict("ロールはアーカイブされていません。")
        role.archived = False
        return self.update_role(role, expected_version=expected_version)

    def delete_role(self, role_id: str, *, expected_version: int) -> None:
        with self._lock:
            role = self.roles.get(role_id)
            if role is None:
                raise SecurityNotFound("ロールが見つかりません。")
            if role.version != expected_version:
                raise SecurityConflict("ロールが別の操作で更新されています。")
            if role.is_built_in:
                raise SecurityConflict(
                    "組み込み SYSTEM_ADMIN ロールは削除できません。",
                    code="SECURITY_ROLE_DELETE_PROTECTED",
                )
            if not role.archived:
                raise SecurityConflict(
                    "ロールを先にアーカイブしてから削除してください。",
                    code="SECURITY_ROLE_DELETE_REQUIRES_ARCHIVED",
                )
            if any(role_id in user.role_ids for user in self.users.values()):
                raise SecurityConflict(
                    "このロールはユーザーに割り当てられています。割り当てを解除してから削除してください。",
                    code="SECURITY_ROLE_DELETE_ASSIGNED",
                )
            if role.entitlements:
                raise SecurityConflict(
                    "このロールにはデータ権限が残っています。"
                    "Deep Data Security で空の Data Grant を適用してから削除してください。",
                    code="SECURITY_ROLE_DELETE_ENTITLEMENTS_PRESENT",
                )
            del self.roles[role_id]

    def count_active_system_admins(self) -> int:
        with self._lock:
            return sum(
                1
                for user in self.users.values()
                if user.status == "ACTIVE" and SYSTEM_ADMIN_ROLE_ID in user.role_ids
            )

    def create_session(self, session: SessionRecord) -> None:
        with self._lock:
            self.sessions[session.session_id] = copy.deepcopy(session)

    def get_session_by_token_hash(self, token_hash: str) -> SessionRecord | None:
        with self._lock:
            return _copy_optional(
                next(
                    (item for item in self.sessions.values() if item.token_hash == token_hash), None
                )
            )

    def touch_session(
        self, session_id: str, *, last_seen_at: datetime, idle_expires_at: datetime
    ) -> None:
        with self._lock:
            session = self.sessions.get(session_id)
            if session:
                session.last_seen_at = last_seen_at
                session.idle_expires_at = idle_expires_at

    def revoke_session(self, session_id: str) -> None:
        with self._lock:
            if session_id in self.sessions:
                self.sessions[session_id].revoked_at = _now()

    def revoke_user_sessions(self, user_uuid: str) -> None:
        with self._lock:
            for session in self.sessions.values():
                if session.user_uuid == user_uuid and session.revoked_at is None:
                    session.revoked_at = _now()

    def get_deepsec_states(self) -> dict[tuple[str, int], dict[str, object]]:
        with self._lock:
            return copy.deepcopy(self.deepsec_states)

    def set_deepsec_state(
        self,
        *,
        version: str,
        step_no: int,
        step_key: str,
        checksum: str,
        status: str,
        error_message: str,
        executed_by_user_uuid: str | None,
    ) -> None:
        with self._lock:
            self.deepsec_states[(version, step_no)] = {
                "step_key": step_key,
                "checksum": checksum,
                "status": status,
                "error_message": error_message,
                "executed_by_user_uuid": executed_by_user_uuid,
                "executed_at": _now() if status in {"APPLIED", "FAILED"} else None,
            }

    def clear_deepsec_states(self, *, version: str, step_numbers: list[int]) -> None:
        with self._lock:
            for step_no in step_numbers:
                self.deepsec_states.pop((version, step_no), None)

    def set_deepsec_entitlement_apply_state(
        self,
        entitlement_id: str,
        *,
        status: str,
        data_grant_name: str = "",
        sql_checksum: str = "",
        error_message: str = "",
    ) -> None:
        with self._lock:
            for role in self.roles.values():
                for entitlement in role.entitlements:
                    if entitlement.entitlement_id != entitlement_id:
                        continue
                    entitlement.apply_status = status
                    if data_grant_name:
                        entitlement.data_grant_name = data_grant_name
                    if sql_checksum:
                        entitlement.sql_checksum = sql_checksum
                    entitlement.apply_error_message = error_message
                    entitlement.applied_at = (
                        _now() if status == "APPLIED" else entitlement.applied_at
                    )
                    return
            raise SecurityNotFound("データ権限が見つかりません。")

    def clear_deepsec_entitlement_apply_states(self) -> None:
        with self._lock:
            for role in self.roles.values():
                for entitlement in role.entitlements:
                    entitlement.apply_status = "PENDING"
                    entitlement.apply_error_message = ""
                    entitlement.sql_checksum = ""
                    entitlement.applied_at = None

    def _required_user(self, user_uuid: str) -> UserRecord:
        user = self.users.get(user_uuid)
        if user is None:
            raise SecurityNotFound("ユーザーが見つかりません。")
        return user

    def _validate_role_ids(
        self,
        role_ids: list[str],
        *,
        allow_inactive_role_ids: set[str] | None = None,
    ) -> None:
        allowed = allow_inactive_role_ids or set()
        for role_id in dict.fromkeys(role_ids):
            role = self.roles.get(role_id)
            if role is not None and not role.archived:
                continue
            if role_id in allowed:
                continue
            raise SecurityNotFound("指定された有効なロールが見つかりません。")


class OracleSecurityStore:
    """Oracle 26ai backed security store。"""

    def __init__(self, settings: Settings) -> None:
        self._adapter = OracleNl2SqlAdapter(settings)

    @contextmanager
    def connection(self, migration_object: str | None = None) -> Iterator[Any]:
        try:
            with self._adapter.connection() as connection:
                yield connection
        except SecurityMigrationRequired:
            raise
        except Exception as exc:
            object_name = _missing_security_migration_object(exc)
            if object_name is None and migration_object and "ORA-00942" in str(exc).upper():
                object_name = migration_object.upper()
            if object_name is not None:
                raise SecurityMigrationRequired(object_name) from exc
            raise

    def bootstrap(self, *, login_user_id: str, display_name: str, password_hash: str) -> bool:
        with self.connection("NL2SQL_APP_USERS") as conn, conn.cursor() as cursor:
            # 初回 user 判定から INSERT までを DB lock で直列化し、複数 worker の
            # 同時 startup でも管理者を一度だけ作成する。
            cursor.execute("LOCK TABLE NL2SQL_APP_USERS IN EXCLUSIVE MODE")
            cursor.execute("SELECT COUNT(*) FROM NL2SQL_APP_USERS")
            user_count = int(cursor.fetchone()[0])
            cursor.execute(
                """
                MERGE INTO NL2SQL_APP_ROLES r
                USING (SELECT :role_id role_id FROM dual) s
                ON (r.ROLE_ID = s.role_id)
                WHEN NOT MATCHED THEN INSERT
                  (ROLE_ID, ROLE_CODE, DISPLAY_NAME, DESCRIPTION, IS_BUILT_IN, ARCHIVED, VERSION_NO)
                VALUES
                  (:role_id, :role_code, :display_name, :description, 1, 0, 1)
                """,
                {
                    "role_id": SYSTEM_ADMIN_ROLE_ID,
                    "role_code": SYSTEM_ADMIN_ROLE_CODE,
                    "display_name": "システム管理者",
                    "description": "すべてのアプリケーション機能を管理する組み込みロールです。",
                },
            )
            if user_count:
                conn.commit()
                return False
            user_uuid = str(uuid4())
            cursor.execute(
                """
                INSERT INTO NL2SQL_APP_USERS
                  (USER_UUID, LOGIN_USER_ID, LOGIN_USER_ID_NORMALIZED, DISPLAY_NAME, PASSWORD_HASH,
                   STATUS, FORCE_PASSWORD_CHANGE, FAILED_LOGIN_COUNT, VERSION_NO)
                VALUES
                  (:user_uuid, :login_user_id, :normalized, :display_name, :password_hash,
                   'ACTIVE', 1, 0, 1)
                """,
                {
                    "user_uuid": user_uuid,
                    "login_user_id": login_user_id,
                    "normalized": login_user_id.casefold(),
                    "display_name": display_name,
                    "password_hash": password_hash,
                },
            )
            cursor.execute(
                "INSERT INTO NL2SQL_APP_USER_ROLES "
                "(USER_UUID, ROLE_ID) VALUES (:user_uuid, :role_id)",
                {"user_uuid": user_uuid, "role_id": SYSTEM_ADMIN_ROLE_ID},
            )
            conn.commit()
            return True

    def get_user_by_login_user_id(self, normalized_login_user_id: str) -> UserRecord | None:
        with self.connection("NL2SQL_APP_USERS") as conn, conn.cursor() as cursor:
            cursor.execute(
                self._user_select() + " WHERE LOGIN_USER_ID_NORMALIZED = :login",
                {"login": normalized_login_user_id.casefold()},
            )
            row = cursor.fetchone()
            return self._user_from_row(cursor, row) if row else None

    def get_user(self, user_uuid: str) -> UserRecord | None:
        with self.connection("NL2SQL_APP_USERS") as conn, conn.cursor() as cursor:
            cursor.execute(
                self._user_select() + " WHERE USER_UUID = :user_uuid",
                {"user_uuid": user_uuid},
            )
            row = cursor.fetchone()
            return self._user_from_row(cursor, row) if row else None

    def list_users(self) -> list[UserRecord]:
        with self.connection("NL2SQL_APP_USERS") as conn, conn.cursor() as cursor:
            cursor.execute(self._user_select() + " ORDER BY LOGIN_USER_ID_NORMALIZED")
            rows = cursor.fetchall()
            return [self._user_from_row(cursor, row) for row in rows]

    @staticmethod
    def _user_select() -> str:
        return (
            "SELECT USER_UUID, LOGIN_USER_ID, DISPLAY_NAME, PASSWORD_HASH, STATUS, "
            "FORCE_PASSWORD_CHANGE, FAILED_LOGIN_COUNT, LOCKED_UNTIL, VERSION_NO, "
            "CASE WHEN USER_UUID = ("
            "  SELECT MIN(USER_UUID) KEEP (DENSE_RANK FIRST ORDER BY CREATED_AT, USER_UUID) "
            "  FROM NL2SQL_APP_USERS"
            ") THEN 1 ELSE 0 END AS IS_BOOTSTRAP_ADMIN "
            "FROM NL2SQL_APP_USERS"
        )

    def _user_from_row(self, cursor: Any, row: Any) -> UserRecord:
        user_uuid = str(row[0])
        cursor.execute(
            "SELECT ROLE_ID FROM NL2SQL_APP_USER_ROLES "
            "WHERE USER_UUID = :user_uuid ORDER BY ROLE_ID",
            {"user_uuid": user_uuid},
        )
        role_ids = [str(item[0]) for item in cursor.fetchall()]
        return UserRecord(
            user_uuid=user_uuid,
            login_user_id=str(row[1]),
            display_name=str(row[2]),
            password_hash=str(row[3]),
            status=str(row[4]),
            force_password_change=bool(row[5]),
            failed_login_count=int(row[6] or 0),
            locked_until=row[7],
            version=int(row[8]),
            role_ids=role_ids,
            is_bootstrap_admin=bool(row[9]),
        )

    def create_user(self, user: UserRecord) -> UserRecord:
        try:
            with self.connection("NL2SQL_APP_USERS") as conn, conn.cursor() as cursor:
                self._assert_role_ids(cursor, user.role_ids)
                cursor.execute(
                    """
                    INSERT INTO NL2SQL_APP_USERS
                      (USER_UUID, LOGIN_USER_ID, LOGIN_USER_ID_NORMALIZED,
                       DISPLAY_NAME, PASSWORD_HASH,
                       STATUS, FORCE_PASSWORD_CHANGE, FAILED_LOGIN_COUNT, LOCKED_UNTIL, VERSION_NO)
                    VALUES
                      (:user_uuid, :login_user_id, :normalized, :display_name, :password_hash,
                       :status, :force_change, 0, NULL, 1)
                    """,
                    {
                        "user_uuid": user.user_uuid,
                        "login_user_id": user.login_user_id,
                        "normalized": user.login_user_id.casefold(),
                        "display_name": user.display_name,
                        "password_hash": user.password_hash,
                        "status": user.status,
                        "force_change": int(user.force_password_change),
                    },
                )
                self._replace_user_roles(cursor, user.user_uuid, user.role_ids)
                conn.commit()
        except Exception as exc:
            if "ORA-00001" in str(exc):
                raise SecurityConflict(
                    "このログインユーザーIDは既に使用されています。別のIDを入力してください。",
                    code="SECURITY_USER_LOGIN_ID_CONFLICT",
                    pointer="/login_user_id",
                    field_code="already_exists",
                ) from exc
            raise
        return self.get_user(user.user_uuid) or user

    def update_user(
        self,
        user_uuid: str,
        *,
        expected_version: int,
        display_name: str,
        status: str,
        role_ids: list[str],
    ) -> UserRecord:
        with self.connection("NL2SQL_APP_USERS") as conn, conn.cursor() as cursor:
            # 最後の管理者判定と更新を一つの DB critical section に置く。
            # 複数 API worker が同時に別の管理者を無効化しても 0 人にはならない。
            cursor.execute("LOCK TABLE NL2SQL_APP_USERS IN SHARE ROW EXCLUSIVE MODE")
            cursor.execute("LOCK TABLE NL2SQL_APP_USER_ROLES IN SHARE ROW EXCLUSIVE MODE")
            cursor.execute(
                "SELECT STATUS FROM NL2SQL_APP_USERS WHERE USER_UUID = :user_uuid",
                {"user_uuid": user_uuid},
            )
            current_row = cursor.fetchone()
            if current_row is None:
                raise SecurityNotFound("ユーザーが見つかりません。")
            cursor.execute(
                "SELECT ROLE_ID FROM NL2SQL_APP_USER_ROLES WHERE USER_UUID = :user_uuid",
                {"user_uuid": user_uuid},
            )
            current_role_ids = {str(item[0]) for item in cursor.fetchall()}
            self._assert_role_ids(
                cursor,
                role_ids,
                allow_inactive_role_ids=current_role_ids,
            )
            is_admin = SYSTEM_ADMIN_ROLE_ID in current_role_ids
            removes_admin = is_admin and (
                status != "ACTIVE" or SYSTEM_ADMIN_ROLE_ID not in role_ids
            )
            if removes_admin:
                cursor.execute(
                    """
                    SELECT COUNT(*)
                      FROM NL2SQL_APP_USERS u
                      JOIN NL2SQL_APP_USER_ROLES ur ON ur.USER_UUID = u.USER_UUID
                     WHERE u.STATUS = 'ACTIVE' AND ur.ROLE_ID = :role_id
                    """,
                    {"role_id": SYSTEM_ADMIN_ROLE_ID},
                )
                if int(cursor.fetchone()[0]) <= 1:
                    raise SecurityConflict("最後のシステム管理者は無効化または権限解除できません。")
            cursor.execute(
                """
                UPDATE NL2SQL_APP_USERS
                   SET DISPLAY_NAME = :display_name, STATUS = :status,
                       VERSION_NO = VERSION_NO + 1, UPDATED_AT = SYSTIMESTAMP
                 WHERE USER_UUID = :user_uuid AND VERSION_NO = :expected_version
                """,
                {
                    "display_name": display_name,
                    "status": status,
                    "user_uuid": user_uuid,
                    "expected_version": expected_version,
                },
            )
            if cursor.rowcount == 0:
                self._raise_not_found_or_conflict(
                    cursor,
                    "NL2SQL_APP_USERS",
                    "USER_UUID",
                    user_uuid,
                )
            self._replace_user_roles(cursor, user_uuid, role_ids)
            conn.commit()
        updated = self.get_user(user_uuid)
        if updated is None:
            raise SecurityNotFound("ユーザーが見つかりません。")
        return updated

    def delete_user(self, user_uuid: str, *, expected_version: int) -> None:
        with self.connection("NL2SQL_APP_USERS") as conn, conn.cursor() as cursor:
            cursor.execute(
                """
                SELECT STATUS, VERSION_NO
                  FROM NL2SQL_APP_USERS
                 WHERE USER_UUID = :user_uuid
                   FOR UPDATE
                """,
                {"user_uuid": user_uuid},
            )
            current = cursor.fetchone()
            if current is None:
                raise SecurityNotFound("ユーザーが見つかりません。")
            if int(current[1]) != expected_version:
                raise SecurityConflict("ユーザーが別の操作で更新されています。")
            if str(current[0]) != "DISABLED":
                raise SecurityConflict(
                    "ユーザーを先に無効化してから削除してください。",
                    code="SECURITY_USER_DELETE_REQUIRES_DISABLED",
                )
            cursor.execute("""
                SELECT MIN(USER_UUID) KEEP (DENSE_RANK FIRST ORDER BY CREATED_AT, USER_UUID)
                  FROM NL2SQL_APP_USERS
                """)
            bootstrap_user_uuid = cursor.fetchone()[0]
            if bootstrap_user_uuid is not None and str(bootstrap_user_uuid) == user_uuid:
                raise SecurityConflict(
                    "初期システム管理者は削除できません。",
                    code="SECURITY_USER_DELETE_PROTECTED",
                )
            cursor.execute(
                "DELETE FROM NL2SQL_AUTH_SESSIONS WHERE USER_UUID = :user_uuid",
                {"user_uuid": user_uuid},
            )
            cursor.execute(
                "DELETE FROM NL2SQL_APP_USER_ROLES WHERE USER_UUID = :user_uuid",
                {"user_uuid": user_uuid},
            )
            cursor.execute(
                """
                DELETE FROM NL2SQL_APP_USERS
                 WHERE USER_UUID = :user_uuid
                   AND VERSION_NO = :expected_version
                   AND STATUS = 'DISABLED'
                """,
                {"user_uuid": user_uuid, "expected_version": expected_version},
            )
            if cursor.rowcount == 0:
                self._raise_not_found_or_conflict(
                    cursor,
                    "NL2SQL_APP_USERS",
                    "USER_UUID",
                    user_uuid,
                )
            conn.commit()

    def set_password(self, user_uuid: str, password_hash: str, *, force_change: bool) -> None:
        with self.connection("NL2SQL_APP_USERS") as conn, conn.cursor() as cursor:
            cursor.execute(
                """
                UPDATE NL2SQL_APP_USERS
                   SET PASSWORD_HASH = :password_hash, FORCE_PASSWORD_CHANGE = :force_change,
                       FAILED_LOGIN_COUNT = 0, LOCKED_UNTIL = NULL,
                       VERSION_NO = VERSION_NO + 1, UPDATED_AT = SYSTIMESTAMP
                 WHERE USER_UUID = :user_uuid
                """,
                {
                    "password_hash": password_hash,
                    "force_change": int(force_change),
                    "user_uuid": user_uuid,
                },
            )
            if cursor.rowcount == 0:
                raise SecurityNotFound("ユーザーが見つかりません。")
            conn.commit()

    def record_login_failure(
        self, user_uuid: str, *, failed_count: int, locked_until: datetime | None
    ) -> None:
        with self.connection("NL2SQL_APP_USERS") as conn, conn.cursor() as cursor:
            cursor.execute(
                """
                UPDATE NL2SQL_APP_USERS SET FAILED_LOGIN_COUNT = :failed_count,
                    LOCKED_UNTIL = :locked_until, UPDATED_AT = SYSTIMESTAMP
                WHERE USER_UUID = :user_uuid
                """,
                {
                    "failed_count": failed_count,
                    "locked_until": locked_until,
                    "user_uuid": user_uuid,
                },
            )
            conn.commit()

    def record_login_success(self, user_uuid: str, *, password_hash: str | None = None) -> None:
        with self.connection("NL2SQL_APP_USERS") as conn, conn.cursor() as cursor:
            if password_hash:
                cursor.execute(
                    """
                    UPDATE NL2SQL_APP_USERS SET FAILED_LOGIN_COUNT = 0, LOCKED_UNTIL = NULL,
                        PASSWORD_HASH = :password_hash, UPDATED_AT = SYSTIMESTAMP
                    WHERE USER_UUID = :user_uuid
                    """,
                    {"password_hash": password_hash, "user_uuid": user_uuid},
                )
            else:
                cursor.execute(
                    """
                    UPDATE NL2SQL_APP_USERS SET FAILED_LOGIN_COUNT = 0, LOCKED_UNTIL = NULL,
                        UPDATED_AT = SYSTIMESTAMP WHERE USER_UUID = :user_uuid
                    """,
                    {"user_uuid": user_uuid},
                )
            conn.commit()

    def list_roles(self, *, include_archived: bool = False) -> list[RoleRecord]:
        with self.connection("NL2SQL_APP_ROLES") as conn, conn.cursor() as cursor:
            sql = self._role_select()
            if not include_archived:
                sql += " WHERE ARCHIVED = 0"
            sql += " ORDER BY ROLE_CODE"
            cursor.execute(sql)
            return [self._role_from_row(cursor, row) for row in cursor.fetchall()]

    def get_role(self, role_id: str) -> RoleRecord | None:
        with self.connection("NL2SQL_APP_ROLES") as conn, conn.cursor() as cursor:
            cursor.execute(self._role_select() + " WHERE ROLE_ID = :role_id", {"role_id": role_id})
            row = cursor.fetchone()
            return self._role_from_row(cursor, row) if row else None

    @staticmethod
    def _role_select() -> str:
        return (
            "SELECT ROLE_ID, ROLE_CODE, DISPLAY_NAME, DESCRIPTION, IS_BUILT_IN, "
            "ARCHIVED, VERSION_NO FROM NL2SQL_APP_ROLES"
        )

    def _role_from_row(self, cursor: Any, row: Any) -> RoleRecord:
        role_id = str(row[0])
        cursor.execute(
            "SELECT PERMISSION_CODE FROM NL2SQL_APP_ROLE_PERMISSIONS WHERE ROLE_ID = :role_id",
            {"role_id": role_id},
        )
        permissions = {str(item[0]) for item in cursor.fetchall()}
        try:
            cursor.execute(
                "SELECT PROFILE_ID FROM NL2SQL_APP_ROLE_PROFILES WHERE ROLE_ID = :role_id",
                {"role_id": role_id},
            )
        except Exception as exc:
            _raise_missing_security_migration_if_needed(exc, "NL2SQL_APP_ROLE_PROFILES")
            raise
        allowed_profile_ids = {str(item[0]) for item in cursor.fetchall()}
        cursor.execute(
            """
            SELECT ENTITLEMENT_ID, RESOURCE_CODE, SCOPE_CODE, CAPABILITY,
                   TARGET_OWNER, TARGET_OBJECT, TARGET_TYPE, COLUMN_NAMES,
                   SCOPE_MODE, SCOPE_COLUMN, SCOPE_FILTERS, DATA_GRANT_NAME, SQL_CHECKSUM,
                   APPLY_STATUS, APPLY_ERROR_MESSAGE, APPLIED_AT
              FROM NL2SQL_APP_DATA_ENTITLEMENTS
             WHERE ROLE_ID = :role_id
             ORDER BY TARGET_OWNER, TARGET_OBJECT, SCOPE_CODE, CAPABILITY, ENTITLEMENT_ID
            """,
            {"role_id": role_id},
        )
        entitlements = [
            DataEntitlementRecord(
                entitlement_id=str(item[0]),
                role_id=role_id,
                resource_code=str(item[1]),
                scope_code=str(item[2]),
                capability=str(item[3]),
                target_owner="" if item[4] is None else str(item[4]),
                target_object="" if item[5] is None else str(item[5]),
                target_type="TABLE" if item[6] is None else str(item[6]),
                column_names=self._json_string_list(item[7]),
                scope_mode="ALL" if item[8] is None else str(item[8]),
                scope_column="" if item[9] is None else str(item[9]),
                scope_filters=scope_filters_from_json(item[10]),
                data_grant_name="" if item[11] is None else str(item[11]),
                sql_checksum="" if item[12] is None else str(item[12]),
                apply_status="PENDING" if item[13] is None else str(item[13]),
                apply_error_message="" if item[14] in (None, "-") else str(item[14]),
                applied_at=item[15],
            )
            for item in cursor.fetchall()
        ]
        return RoleRecord(
            role_id=role_id,
            role_code=str(row[1]),
            display_name=str(row[2]),
            description="" if row[3] in (None, "-") else str(row[3]),
            is_built_in=bool(row[4]),
            archived=bool(row[5]),
            version=int(row[6]),
            permissions=permissions,
            entitlements=entitlements,
            allowed_profile_ids=allowed_profile_ids,
        )

    @staticmethod
    def _json_string_list(value: Any) -> list[str]:
        if value is None:
            return []
        if hasattr(value, "read"):
            value = value.read()
        try:
            payload = json.loads(str(value or "[]"))
        except (TypeError, ValueError):
            return []
        if not isinstance(payload, list):
            return []
        return [str(item) for item in payload if str(item).strip()]

    def create_role(self, role: RoleRecord) -> RoleRecord:
        try:
            with self.connection("NL2SQL_APP_ROLES") as conn, conn.cursor() as cursor:
                cursor.execute(
                    """
                    INSERT INTO NL2SQL_APP_ROLES
                      (ROLE_ID, ROLE_CODE, DISPLAY_NAME, DESCRIPTION,
                       IS_BUILT_IN, ARCHIVED, VERSION_NO)
                    VALUES
                      (:role_id, :role_code, :display_name, :description,
                       :is_built_in, :archived, 1)
                    """,
                    {
                        "role_id": role.role_id,
                        "role_code": role.role_code,
                        "display_name": role.display_name,
                        "description": role.description or "-",
                        "is_built_in": int(role.is_built_in),
                        "archived": int(role.archived),
                    },
                )
                self._replace_role_access(cursor, role)
                conn.commit()
        except Exception as exc:
            if "ORA-00001" in str(exc):
                raise SecurityConflict(
                    "このロールコードは既に使用されています。別のコードを入力してください。",
                    code="SECURITY_ROLE_CODE_CONFLICT",
                    pointer="/role_code",
                    field_code="already_exists",
                ) from exc
            raise
        return self.get_role(role.role_id) or role

    def update_role(self, role: RoleRecord, *, expected_version: int) -> RoleRecord:
        with self.connection("NL2SQL_APP_ROLES") as conn, conn.cursor() as cursor:
            cursor.execute(
                """
                UPDATE NL2SQL_APP_ROLES
                   SET DISPLAY_NAME = :display_name, DESCRIPTION = :description,
                       VERSION_NO = VERSION_NO + 1, UPDATED_AT = SYSTIMESTAMP
                 WHERE ROLE_ID = :role_id AND VERSION_NO = :expected_version
                """,
                {
                    "display_name": role.display_name,
                    "description": role.description or "-",
                    "role_id": role.role_id,
                    "expected_version": expected_version,
                },
            )
            if cursor.rowcount == 0:
                self._raise_not_found_or_conflict(
                    cursor, "NL2SQL_APP_ROLES", "ROLE_ID", role.role_id
                )
            self._replace_role_access(cursor, role)
            conn.commit()
        updated = self.get_role(role.role_id)
        if updated is None:
            raise SecurityNotFound("ロールが見つかりません。")
        return updated

    def archive_role(self, role_id: str, *, expected_version: int) -> RoleRecord:
        with self.connection("NL2SQL_APP_ROLES") as conn, conn.cursor() as cursor:
            cursor.execute(
                """
                UPDATE NL2SQL_APP_ROLES SET ARCHIVED = 1, VERSION_NO = VERSION_NO + 1,
                    UPDATED_AT = SYSTIMESTAMP
                WHERE ROLE_ID = :role_id AND VERSION_NO = :expected_version AND IS_BUILT_IN = 0
                """,
                {"role_id": role_id, "expected_version": expected_version},
            )
            if cursor.rowcount == 0:
                self._raise_not_found_or_conflict(cursor, "NL2SQL_APP_ROLES", "ROLE_ID", role_id)
            conn.commit()
        role = self.get_role(role_id)
        if role is None:
            raise SecurityNotFound("ロールが見つかりません。")
        return role

    def restore_role(self, role_id: str, *, expected_version: int) -> RoleRecord:
        current = self.get_role(role_id)
        if current is None:
            raise SecurityNotFound("ロールが見つかりません。")
        if not current.archived:
            raise SecurityConflict("ロールはアーカイブされていません。")
        with self.connection("NL2SQL_APP_ROLES") as conn, conn.cursor() as cursor:
            cursor.execute(
                """
                UPDATE NL2SQL_APP_ROLES SET ARCHIVED = 0, VERSION_NO = VERSION_NO + 1,
                    UPDATED_AT = SYSTIMESTAMP
                WHERE ROLE_ID = :role_id AND VERSION_NO = :expected_version AND IS_BUILT_IN = 0
                """,
                {"role_id": role_id, "expected_version": expected_version},
            )
            if cursor.rowcount == 0:
                self._raise_not_found_or_conflict(cursor, "NL2SQL_APP_ROLES", "ROLE_ID", role_id)
            conn.commit()
        role = self.get_role(role_id)
        if role is None:
            raise SecurityNotFound("ロールが見つかりません。")
        return role

    def delete_role(self, role_id: str, *, expected_version: int) -> None:
        with self.connection("NL2SQL_APP_ROLES") as conn, conn.cursor() as cursor:
            cursor.execute(
                """
                SELECT IS_BUILT_IN, ARCHIVED, VERSION_NO
                  FROM NL2SQL_APP_ROLES
                 WHERE ROLE_ID = :role_id
                   FOR UPDATE
                """,
                {"role_id": role_id},
            )
            current = cursor.fetchone()
            if current is None:
                raise SecurityNotFound("ロールが見つかりません。")
            if int(current[2]) != expected_version:
                raise SecurityConflict("ロールが別の操作で更新されています。")
            if bool(current[0]):
                raise SecurityConflict(
                    "組み込み SYSTEM_ADMIN ロールは削除できません。",
                    code="SECURITY_ROLE_DELETE_PROTECTED",
                )
            if not bool(current[1]):
                raise SecurityConflict(
                    "ロールを先にアーカイブしてから削除してください。",
                    code="SECURITY_ROLE_DELETE_REQUIRES_ARCHIVED",
                )
            cursor.execute(
                "SELECT COUNT(*) FROM NL2SQL_APP_USER_ROLES WHERE ROLE_ID = :role_id",
                {"role_id": role_id},
            )
            if int(cursor.fetchone()[0]) > 0:
                raise SecurityConflict(
                    "このロールはユーザーに割り当てられています。割り当てを解除してから削除してください。",
                    code="SECURITY_ROLE_DELETE_ASSIGNED",
                )
            cursor.execute(
                "SELECT COUNT(*) FROM NL2SQL_APP_DATA_ENTITLEMENTS WHERE ROLE_ID = :role_id",
                {"role_id": role_id},
            )
            if int(cursor.fetchone()[0]) > 0:
                raise SecurityConflict(
                    "このロールにはデータ権限が残っています。"
                    "Deep Data Security で空の Data Grant を適用してから削除してください。",
                    code="SECURITY_ROLE_DELETE_ENTITLEMENTS_PRESENT",
                )
            cursor.execute(
                "DELETE FROM NL2SQL_APP_ROLE_PERMISSIONS WHERE ROLE_ID = :role_id",
                {"role_id": role_id},
            )
            try:
                cursor.execute(
                    "DELETE FROM NL2SQL_APP_ROLE_PROFILES WHERE ROLE_ID = :role_id",
                    {"role_id": role_id},
                )
            except Exception as exc:
                _raise_missing_security_migration_if_needed(exc, "NL2SQL_APP_ROLE_PROFILES")
                raise
            cursor.execute(
                """
                DELETE FROM NL2SQL_APP_ROLES
                 WHERE ROLE_ID = :role_id
                   AND VERSION_NO = :expected_version
                   AND IS_BUILT_IN = 0
                   AND ARCHIVED = 1
                """,
                {"role_id": role_id, "expected_version": expected_version},
            )
            if cursor.rowcount == 0:
                self._raise_not_found_or_conflict(
                    cursor,
                    "NL2SQL_APP_ROLES",
                    "ROLE_ID",
                    role_id,
                )
            conn.commit()

    def count_active_system_admins(self) -> int:
        with self.connection("NL2SQL_APP_USERS") as conn, conn.cursor() as cursor:
            cursor.execute("""
                SELECT COUNT(*)
                  FROM NL2SQL_APP_USERS u
                  JOIN NL2SQL_APP_USER_ROLES ur ON ur.USER_UUID = u.USER_UUID
                  JOIN NL2SQL_APP_ROLES r ON r.ROLE_ID = ur.ROLE_ID
                 WHERE u.STATUS = 'ACTIVE' AND r.ROLE_CODE = 'SYSTEM_ADMIN' AND r.ARCHIVED = 0
                """)
            return int(cursor.fetchone()[0])

    def create_session(self, session: SessionRecord) -> None:
        with self.connection("NL2SQL_AUTH_SESSIONS") as conn, conn.cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO NL2SQL_AUTH_SESSIONS
                  (SESSION_ID, USER_UUID, TOKEN_HASH, CSRF_TOKEN_HASH, IDLE_EXPIRES_AT,
                   ABSOLUTE_EXPIRES_AT, LAST_SEEN_AT)
                VALUES
                  (:session_id, :user_uuid, :token_hash, :csrf_hash, :idle_expires,
                   :absolute_expires, :last_seen)
                """,
                {
                    "session_id": session.session_id,
                    "user_uuid": session.user_uuid,
                    "token_hash": session.token_hash,
                    "csrf_hash": session.csrf_token_hash,
                    "idle_expires": session.idle_expires_at,
                    "absolute_expires": session.absolute_expires_at,
                    "last_seen": session.last_seen_at,
                },
            )
            conn.commit()

    def get_session_by_token_hash(self, token_hash: str) -> SessionRecord | None:
        with self.connection("NL2SQL_AUTH_SESSIONS") as conn, conn.cursor() as cursor:
            cursor.execute(
                """
                SELECT SESSION_ID, USER_UUID, TOKEN_HASH, CSRF_TOKEN_HASH, IDLE_EXPIRES_AT,
                       ABSOLUTE_EXPIRES_AT, LAST_SEEN_AT, REVOKED_AT
                  FROM NL2SQL_AUTH_SESSIONS WHERE TOKEN_HASH = :token_hash
                """,
                {"token_hash": token_hash},
            )
            row = cursor.fetchone()
            if not row:
                return None
            return SessionRecord(
                session_id=str(row[0]),
                user_uuid=str(row[1]),
                token_hash=str(row[2]),
                csrf_token_hash=str(row[3]),
                idle_expires_at=row[4],
                absolute_expires_at=row[5],
                last_seen_at=row[6],
                revoked_at=row[7],
            )

    def touch_session(
        self, session_id: str, *, last_seen_at: datetime, idle_expires_at: datetime
    ) -> None:
        with self.connection("NL2SQL_AUTH_SESSIONS") as conn, conn.cursor() as cursor:
            cursor.execute(
                """
                UPDATE NL2SQL_AUTH_SESSIONS SET LAST_SEEN_AT = :last_seen,
                    IDLE_EXPIRES_AT = :idle_expires
                WHERE SESSION_ID = :session_id AND REVOKED_AT IS NULL
                """,
                {
                    "last_seen": last_seen_at,
                    "idle_expires": idle_expires_at,
                    "session_id": session_id,
                },
            )
            conn.commit()

    def revoke_session(self, session_id: str) -> None:
        with self.connection("NL2SQL_AUTH_SESSIONS") as conn, conn.cursor() as cursor:
            cursor.execute(
                "UPDATE NL2SQL_AUTH_SESSIONS SET REVOKED_AT = SYSTIMESTAMP "
                "WHERE SESSION_ID = :session_id AND REVOKED_AT IS NULL",
                {"session_id": session_id},
            )
            conn.commit()

    def revoke_user_sessions(self, user_uuid: str) -> None:
        with self.connection("NL2SQL_AUTH_SESSIONS") as conn, conn.cursor() as cursor:
            cursor.execute(
                "UPDATE NL2SQL_AUTH_SESSIONS SET REVOKED_AT = SYSTIMESTAMP "
                "WHERE USER_UUID = :user_uuid AND REVOKED_AT IS NULL",
                {"user_uuid": user_uuid},
            )
            conn.commit()

    def get_deepsec_states(self) -> dict[tuple[str, int], dict[str, object]]:
        with self.connection("NL2SQL_DEEPSEC_MIGRATIONS") as conn, conn.cursor() as cursor:
            cursor.execute("""
                SELECT PLAN_VERSION, STEP_NO, STEP_KEY, CHECKSUM, STATUS,
                       ERROR_MESSAGE, EXECUTED_BY_USER_UUID, EXECUTED_AT
                  FROM NL2SQL_DEEPSEC_MIGRATIONS
                """)
            return {
                (str(row[0]), int(row[1])): {
                    "step_key": str(row[2]),
                    "checksum": str(row[3]),
                    "status": str(row[4]),
                    "error_message": "" if row[5] in (None, "-") else str(row[5]),
                    "executed_by_user_uuid": str(row[6]) if row[6] else None,
                    "executed_at": row[7],
                }
                for row in cursor.fetchall()
            }

    def set_deepsec_state(
        self,
        *,
        version: str,
        step_no: int,
        step_key: str,
        checksum: str,
        status: str,
        error_message: str,
        executed_by_user_uuid: str | None,
    ) -> None:
        with self.connection("NL2SQL_DEEPSEC_MIGRATIONS") as conn, conn.cursor() as cursor:
            cursor.execute(
                """
                MERGE INTO NL2SQL_DEEPSEC_MIGRATIONS m
                USING (SELECT :version plan_version, :step_no step_no FROM dual) s
                ON (m.PLAN_VERSION = s.plan_version AND m.STEP_NO = s.step_no)
                WHEN MATCHED THEN UPDATE SET
                    STEP_KEY = :step_key, CHECKSUM = :checksum, STATUS = :status,
                    ERROR_MESSAGE = :error_message, EXECUTED_BY_USER_UUID = :executed_by_user_uuid,
                    EXECUTED_AT = CASE WHEN :status IN ('APPLIED', 'FAILED')
                                       THEN SYSTIMESTAMP ELSE NULL END,
                    UPDATED_AT = SYSTIMESTAMP
                WHEN NOT MATCHED THEN INSERT
                    (PLAN_VERSION, STEP_NO, STEP_KEY, CHECKSUM, STATUS, ERROR_MESSAGE,
                     EXECUTED_BY_USER_UUID, EXECUTED_AT)
                VALUES
                    (:version, :step_no, :step_key, :checksum, :status, :error_message,
                     :executed_by_user_uuid, CASE WHEN :status IN ('APPLIED', 'FAILED')
                                        THEN SYSTIMESTAMP ELSE NULL END)
                """,
                {
                    "version": version,
                    "step_no": step_no,
                    "step_key": step_key,
                    "checksum": checksum,
                    "status": status,
                    "error_message": error_message[:2000] or "-",
                    "executed_by_user_uuid": executed_by_user_uuid,
                },
            )
            conn.commit()

    def clear_deepsec_states(self, *, version: str, step_numbers: list[int]) -> None:
        if not step_numbers:
            return
        with self.connection("NL2SQL_DEEPSEC_MIGRATIONS") as conn, conn.cursor() as cursor:
            cursor.executemany(
                """
                DELETE FROM NL2SQL_DEEPSEC_MIGRATIONS
                 WHERE PLAN_VERSION = :version AND STEP_NO = :step_no
                """,
                [{"version": version, "step_no": step_no} for step_no in step_numbers],
            )
            conn.commit()

    @staticmethod
    def _replace_user_roles(cursor: Any, user_uuid: str, role_ids: list[str]) -> None:
        cursor.execute(
            "DELETE FROM NL2SQL_APP_USER_ROLES WHERE USER_UUID = :user_uuid",
            {"user_uuid": user_uuid},
        )
        for role_id in dict.fromkeys(role_ids):
            cursor.execute(
                "INSERT INTO NL2SQL_APP_USER_ROLES "
                "(USER_UUID, ROLE_ID) VALUES (:user_uuid, :role_id)",
                {"user_uuid": user_uuid, "role_id": role_id},
            )

    @staticmethod
    def _replace_role_access(cursor: Any, role: RoleRecord) -> None:
        cursor.execute(
            "DELETE FROM NL2SQL_APP_ROLE_PERMISSIONS WHERE ROLE_ID = :role_id",
            {"role_id": role.role_id},
        )
        for permission in sorted(role.permissions):
            cursor.execute(
                "INSERT INTO NL2SQL_APP_ROLE_PERMISSIONS (ROLE_ID, PERMISSION_CODE) "
                "VALUES (:role_id, :code)",
                {"role_id": role.role_id, "code": permission},
            )
        try:
            cursor.execute(
                "DELETE FROM NL2SQL_APP_ROLE_PROFILES WHERE ROLE_ID = :role_id",
                {"role_id": role.role_id},
            )
            for profile_id in sorted(role.allowed_profile_ids):
                cursor.execute(
                    "INSERT INTO NL2SQL_APP_ROLE_PROFILES (ROLE_ID, PROFILE_ID) "
                    "VALUES (:role_id, :profile_id)",
                    {"role_id": role.role_id, "profile_id": profile_id},
                )
        except Exception as exc:
            _raise_missing_security_migration_if_needed(exc, "NL2SQL_APP_ROLE_PROFILES")
            raise
        cursor.execute(
            "DELETE FROM NL2SQL_APP_DATA_ENTITLEMENTS WHERE ROLE_ID = :role_id",
            {"role_id": role.role_id},
        )
        for entitlement in role.entitlements:
            cursor.execute(
                """
                INSERT INTO NL2SQL_APP_DATA_ENTITLEMENTS
                  (ENTITLEMENT_ID, ROLE_ID, RESOURCE_CODE, SCOPE_CODE, CAPABILITY,
                   TARGET_OWNER, TARGET_OBJECT, TARGET_TYPE, COLUMN_NAMES,
                   SCOPE_MODE, SCOPE_COLUMN, SCOPE_FILTERS, DATA_GRANT_NAME, SQL_CHECKSUM,
                   APPLY_STATUS, APPLY_ERROR_MESSAGE, APPLIED_AT)
                VALUES
                  (:entitlement_id, :role_id, :resource_code, :scope_code, :capability_code,
                   :target_owner, :target_object, :target_type, :column_names,
                   :scope_mode, :scope_column, :scope_filters, :data_grant_name, :sql_checksum,
                   :apply_status, :apply_error_message, :applied_at)
                """,
                {
                    "entitlement_id": entitlement.entitlement_id,
                    "role_id": role.role_id,
                    "resource_code": entitlement.resource_code,
                    "scope_code": entitlement.scope_code,
                    "capability_code": entitlement.capability,
                    "target_owner": entitlement.target_owner or None,
                    "target_object": entitlement.target_object or None,
                    "target_type": entitlement.target_type or "TABLE",
                    "column_names": json.dumps(
                        list(entitlement.column_names),
                        ensure_ascii=False,
                        separators=(",", ":"),
                    ),
                    "scope_mode": entitlement.scope_mode or "ALL",
                    "scope_column": entitlement.scope_column or None,
                    "scope_filters": scope_filters_canonical_json(entitlement.scope_filters),
                    "data_grant_name": entitlement.data_grant_name or None,
                    "sql_checksum": entitlement.sql_checksum or None,
                    "apply_status": entitlement.apply_status or "PENDING",
                    "apply_error_message": entitlement.apply_error_message[:2000] or "-",
                    "applied_at": entitlement.applied_at,
                },
            )

    def set_deepsec_entitlement_apply_state(
        self,
        entitlement_id: str,
        *,
        status: str,
        data_grant_name: str = "",
        sql_checksum: str = "",
        error_message: str = "",
    ) -> None:
        with self.connection("NL2SQL_APP_DATA_ENTITLEMENTS") as conn, conn.cursor() as cursor:
            cursor.execute(
                """
                UPDATE NL2SQL_APP_DATA_ENTITLEMENTS
                   SET APPLY_STATUS = :status,
                       DATA_GRANT_NAME = COALESCE(:data_grant_name, DATA_GRANT_NAME),
                       SQL_CHECKSUM = COALESCE(:sql_checksum, SQL_CHECKSUM),
                       APPLY_ERROR_MESSAGE = :error_message,
                       APPLIED_AT = CASE
                         WHEN :status = 'APPLIED' THEN SYSTIMESTAMP
                         WHEN :status = 'PENDING' THEN NULL
                         ELSE APPLIED_AT
                       END
                 WHERE ENTITLEMENT_ID = :entitlement_id
                """,
                {
                    "entitlement_id": entitlement_id,
                    "status": status,
                    "data_grant_name": data_grant_name or None,
                    "sql_checksum": sql_checksum or None,
                    "error_message": error_message[:2000] or "-",
                },
            )
            if cursor.rowcount == 0:
                raise SecurityNotFound("データ権限が見つかりません。")
            conn.commit()

    def clear_deepsec_entitlement_apply_states(self) -> None:
        with self.connection("NL2SQL_APP_DATA_ENTITLEMENTS") as conn, conn.cursor() as cursor:
            cursor.execute("""
                UPDATE NL2SQL_APP_DATA_ENTITLEMENTS
                   SET APPLY_STATUS = 'PENDING',
                       SQL_CHECKSUM = NULL,
                       APPLY_ERROR_MESSAGE = '-',
                       APPLIED_AT = NULL,
                       UPDATED_AT = SYSTIMESTAMP
                """)
            conn.commit()

    @staticmethod
    def _assert_role_ids(
        cursor: Any,
        role_ids: list[str],
        *,
        allow_inactive_role_ids: set[str] | None = None,
    ) -> None:
        allowed = allow_inactive_role_ids or set()
        for role_id in dict.fromkeys(role_ids):
            cursor.execute(
                "SELECT COUNT(*) FROM NL2SQL_APP_ROLES WHERE ROLE_ID = :role_id AND ARCHIVED = 0",
                {"role_id": role_id},
            )
            if int(cursor.fetchone()[0]) == 1:
                continue
            if role_id in allowed:
                continue
            raise SecurityNotFound("指定された有効なロールが見つかりません。")

    @staticmethod
    def _raise_not_found_or_conflict(cursor: Any, table: str, column: str, value: str) -> None:
        queries = {
            ("NL2SQL_APP_USERS", "USER_UUID"): (
                "SELECT COUNT(*) FROM NL2SQL_APP_USERS WHERE USER_UUID = :value"
            ),
            ("NL2SQL_APP_ROLES", "ROLE_ID"): (
                "SELECT COUNT(*) FROM NL2SQL_APP_ROLES WHERE ROLE_ID = :value"
            ),
        }
        sql = queries.get((table, column))
        if sql is None:
            raise SecurityStoreError("安全でない競合確認です。")
        cursor.execute(sql, {"value": value})
        if int(cursor.fetchone()[0]) == 0:
            raise SecurityNotFound("対象が見つかりません。")
        raise SecurityConflict("別の操作で更新されています。最新情報を再読込してください。")
