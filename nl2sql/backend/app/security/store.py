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
    AuditRecord,
    DataEntitlementRecord,
    RoleRecord,
    SessionRecord,
    UserRecord,
)


class SecurityStoreError(RuntimeError):
    """認証 store の基底例外。"""


class SecurityNotFound(SecurityStoreError):
    pass


class SecurityConflict(SecurityStoreError):
    pass


class SecurityStore(Protocol):
    def bootstrap(self, *, login_name: str, display_name: str, password_hash: str) -> bool: ...
    def get_user_by_login(self, normalized_login: str) -> UserRecord | None: ...
    def get_user(self, user_id: str) -> UserRecord | None: ...
    def list_users(self) -> list[UserRecord]: ...
    def create_user(self, user: UserRecord) -> UserRecord: ...
    def update_user(
        self,
        user_id: str,
        *,
        expected_version: int,
        display_name: str,
        status: str,
        role_ids: list[str],
    ) -> UserRecord: ...
    def set_password(self, user_id: str, password_hash: str, *, force_change: bool) -> None: ...
    def record_login_failure(
        self, user_id: str, *, failed_count: int, locked_until: datetime | None
    ) -> None: ...
    def record_login_success(self, user_id: str, *, password_hash: str | None = None) -> None: ...
    def list_roles(self, *, include_archived: bool = False) -> list[RoleRecord]: ...
    def get_role(self, role_id: str) -> RoleRecord | None: ...
    def create_role(self, role: RoleRecord) -> RoleRecord: ...
    def update_role(self, role: RoleRecord, *, expected_version: int) -> RoleRecord: ...
    def archive_role(self, role_id: str, *, expected_version: int) -> RoleRecord: ...
    def count_active_system_admins(self) -> int: ...
    def create_session(self, session: SessionRecord) -> None: ...
    def get_session_by_token_hash(self, token_hash: str) -> SessionRecord | None: ...
    def touch_session(
        self, session_id: str, *, last_seen_at: datetime, idle_expires_at: datetime
    ) -> None: ...
    def revoke_session(self, session_id: str) -> None: ...
    def revoke_user_sessions(self, user_id: str) -> None: ...
    def write_audit(
        self,
        *,
        actor_user_id: str | None,
        event_type: str,
        target_type: str,
        target_id: str,
        outcome: str,
        detail: dict[str, object],
        request_id: str,
        client_ip: str,
    ) -> None: ...
    def list_audit(self, *, limit: int = 200) -> list[AuditRecord]: ...
    def page_audit(
        self, *, page: int, page_size: int
    ) -> tuple[list[AuditRecord], int, int]: ...
    def list_audit_between(
        self, *, start_at: datetime, end_at: datetime
    ) -> list[AuditRecord]: ...
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
        executed_by: str | None,
    ) -> None: ...


def _now() -> datetime:
    return datetime.now(UTC)


def _aware(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value


def _resolved_page(total: int, requested_page: int, page_size: int) -> int:
    total_pages = max(1, (total + page_size - 1) // page_size)
    return min(max(1, requested_page), total_pages)


def _audit_record_from_row(row: Any) -> AuditRecord:
    raw_detail = row[6].read() if hasattr(row[6], "read") else row[6]
    return AuditRecord(
        audit_id=int(row[0]),
        actor_user_id=str(row[1]) if row[1] else None,
        event_type=str(row[2]),
        target_type="" if row[3] in (None, "-") else str(row[3]),
        target_id="" if row[4] in (None, "-") else str(row[4]),
        outcome=str(row[5]),
        detail=json.loads(str(raw_detail or "{}")),
        request_id="" if row[7] in (None, "-") else str(row[7]),
        client_ip="" if row[8] in (None, "-") else str(row[8]),
        created_at=row[9],
    )


def _copy_optional[T](value: T | None) -> T | None:
    return copy.deepcopy(value) if value is not None else None


class InMemorySecurityStore:
    """単体テスト用。production は OracleSecurityStore を使う。"""

    def __init__(self) -> None:
        self.users: dict[str, UserRecord] = {}
        self.roles: dict[str, RoleRecord] = {}
        self.sessions: dict[str, SessionRecord] = {}
        self.audit: list[AuditRecord] = []
        self.deepsec_states: dict[tuple[str, int], dict[str, object]] = {}
        self._lock = threading.RLock()

    def bootstrap(self, *, login_name: str, display_name: str, password_hash: str) -> bool:
        with self._lock:
            self._ensure_system_admin_role()
            if self.users:
                return False
            user = UserRecord(
                user_id=str(uuid4()),
                login_name=login_name,
                display_name=display_name,
                password_hash=password_hash,
                status="ACTIVE",
                force_password_change=True,
                failed_login_count=0,
                locked_until=None,
                version=1,
                role_ids=[SYSTEM_ADMIN_ROLE_ID],
            )
            self.users[user.user_id] = user
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
            entitlements=[
                DataEntitlementRecord(
                    entitlement_id=str(uuid4()),
                    role_id=SYSTEM_ADMIN_ROLE_ID,
                    resource_code="NL2SQL_DEEPSEC_PROBE",
                    scope_code="*",
                    capability="FULL",
                )
            ],
        )

    def get_user_by_login(self, normalized_login: str) -> UserRecord | None:
        with self._lock:
            return _copy_optional(
                next(
                    (
                        user
                        for user in self.users.values()
                        if user.login_name.casefold() == normalized_login.casefold()
                    ),
                    None,
                )
            )

    def get_user(self, user_id: str) -> UserRecord | None:
        with self._lock:
            return _copy_optional(self.users.get(user_id))

    def list_users(self) -> list[UserRecord]:
        with self._lock:
            return [
                copy.deepcopy(item)
                for item in sorted(self.users.values(), key=lambda u: u.login_name)
            ]

    def create_user(self, user: UserRecord) -> UserRecord:
        with self._lock:
            if any(
                item.login_name.casefold() == user.login_name.casefold()
                for item in self.users.values()
            ):
                raise SecurityConflict("同じログイン名のユーザーが既に存在します。")
            self._validate_role_ids(user.role_ids)
            self.users[user.user_id] = copy.deepcopy(user)
            return copy.deepcopy(user)

    def update_user(
        self,
        user_id: str,
        *,
        expected_version: int,
        display_name: str,
        status: str,
        role_ids: list[str],
    ) -> UserRecord:
        with self._lock:
            user = self.users.get(user_id)
            if user is None:
                raise SecurityNotFound("ユーザーが見つかりません。")
            if user.version != expected_version:
                raise SecurityConflict("ユーザーが別の操作で更新されています。")
            self._validate_role_ids(role_ids)
            removes_last_admin = (
                user.status == "ACTIVE"
                and SYSTEM_ADMIN_ROLE_ID in user.role_ids
                and (status != "ACTIVE" or SYSTEM_ADMIN_ROLE_ID not in role_ids)
                and self.count_active_system_admins() <= 1
            )
            if removes_last_admin:
                raise SecurityConflict(
                    "最後のシステム管理者は無効化または権限解除できません。"
                )
            user.display_name = display_name
            user.status = status
            user.role_ids = list(dict.fromkeys(role_ids))
            user.version += 1
            return copy.deepcopy(user)

    def set_password(self, user_id: str, password_hash: str, *, force_change: bool) -> None:
        with self._lock:
            user = self._required_user(user_id)
            user.password_hash = password_hash
            user.force_password_change = force_change
            user.failed_login_count = 0
            user.locked_until = None
            user.version += 1

    def record_login_failure(
        self, user_id: str, *, failed_count: int, locked_until: datetime | None
    ) -> None:
        with self._lock:
            user = self._required_user(user_id)
            user.failed_login_count = failed_count
            user.locked_until = locked_until

    def record_login_success(self, user_id: str, *, password_hash: str | None = None) -> None:
        with self._lock:
            user = self._required_user(user_id)
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
                raise SecurityConflict("同じロールコードが既に存在します。")
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

    def revoke_user_sessions(self, user_id: str) -> None:
        with self._lock:
            for session in self.sessions.values():
                if session.user_id == user_id and session.revoked_at is None:
                    session.revoked_at = _now()

    def write_audit(
        self,
        *,
        actor_user_id: str | None,
        event_type: str,
        target_type: str,
        target_id: str,
        outcome: str,
        detail: dict[str, object],
        request_id: str,
        client_ip: str,
    ) -> None:
        with self._lock:
            self.audit.append(
                AuditRecord(
                    audit_id=len(self.audit) + 1,
                    actor_user_id=actor_user_id,
                    event_type=event_type,
                    target_type=target_type,
                    target_id=target_id,
                    outcome=outcome,
                    detail=copy.deepcopy(detail),
                    request_id=request_id,
                    client_ip=client_ip,
                    created_at=_now(),
                )
            )

    def list_audit(self, *, limit: int = 200) -> list[AuditRecord]:
        with self._lock:
            ordered = sorted(
                self.audit,
                key=lambda item: (_aware(item.created_at), item.audit_id),
                reverse=True,
            )
            return [copy.deepcopy(item) for item in ordered[:limit]]

    def page_audit(
        self, *, page: int, page_size: int
    ) -> tuple[list[AuditRecord], int, int]:
        with self._lock:
            ordered = sorted(
                self.audit,
                key=lambda item: (_aware(item.created_at), item.audit_id),
                reverse=True,
            )
            total = len(ordered)
            resolved_page = _resolved_page(total, page, page_size)
            offset = (resolved_page - 1) * page_size
            records = ordered[offset : offset + page_size]
            return [copy.deepcopy(item) for item in records], total, resolved_page

    def list_audit_between(
        self, *, start_at: datetime, end_at: datetime
    ) -> list[AuditRecord]:
        normalized_start = _aware(start_at)
        normalized_end = _aware(end_at)
        with self._lock:
            records = [
                item
                for item in self.audit
                if normalized_start <= _aware(item.created_at) <= normalized_end
            ]
            records.sort(
                key=lambda item: (_aware(item.created_at), item.audit_id),
                reverse=True,
            )
            return [copy.deepcopy(item) for item in records]

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
        executed_by: str | None,
    ) -> None:
        with self._lock:
            self.deepsec_states[(version, step_no)] = {
                "step_key": step_key,
                "checksum": checksum,
                "status": status,
                "error_message": error_message,
                "executed_by": executed_by,
                "executed_at": _now() if status in {"APPLIED", "FAILED"} else None,
            }

    def _required_user(self, user_id: str) -> UserRecord:
        user = self.users.get(user_id)
        if user is None:
            raise SecurityNotFound("ユーザーが見つかりません。")
        return user

    def _validate_role_ids(self, role_ids: list[str]) -> None:
        if any(role_id not in self.roles or self.roles[role_id].archived for role_id in role_ids):
            raise SecurityNotFound("指定された有効なロールが見つかりません。")

class OracleSecurityStore:
    """Oracle 26ai backed security store。"""

    def __init__(self, settings: Settings) -> None:
        self._adapter = OracleNl2SqlAdapter(settings)

    @contextmanager
    def connection(self) -> Iterator[Any]:
        with self._adapter.connection() as connection:
            yield connection

    def bootstrap(self, *, login_name: str, display_name: str, password_hash: str) -> bool:
        with self.connection() as conn, conn.cursor() as cursor:
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
            self._ensure_system_admin_probe_entitlement(cursor)
            if user_count:
                conn.commit()
                return False
            user_id = str(uuid4())
            cursor.execute(
                """
                INSERT INTO NL2SQL_APP_USERS
                  (USER_ID, LOGIN_NAME, LOGIN_NAME_NORMALIZED, DISPLAY_NAME, PASSWORD_HASH,
                   STATUS, FORCE_PASSWORD_CHANGE, FAILED_LOGIN_COUNT, VERSION_NO)
                VALUES
                  (:user_id, :login_name, :normalized, :display_name, :password_hash,
                   'ACTIVE', 1, 0, 1)
                """,
                {
                    "user_id": user_id,
                    "login_name": login_name,
                    "normalized": login_name.casefold(),
                    "display_name": display_name,
                    "password_hash": password_hash,
                },
            )
            cursor.execute(
                "INSERT INTO NL2SQL_APP_USER_ROLES (USER_ID, ROLE_ID) VALUES (:user_id, :role_id)",
                {"user_id": user_id, "role_id": SYSTEM_ADMIN_ROLE_ID},
            )
            conn.commit()
            return True

    @staticmethod
    def _ensure_system_admin_probe_entitlement(cursor: Any) -> None:
        cursor.execute(
            """
            SELECT COUNT(*) FROM NL2SQL_APP_DATA_ENTITLEMENTS
            WHERE ROLE_ID = :role_id AND RESOURCE_CODE = 'NL2SQL_DEEPSEC_PROBE'
              AND SCOPE_CODE = '*' AND CAPABILITY = 'FULL'
            """,
            {"role_id": SYSTEM_ADMIN_ROLE_ID},
        )
        if int(cursor.fetchone()[0]) == 0:
            cursor.execute(
                """
                INSERT INTO NL2SQL_APP_DATA_ENTITLEMENTS
                  (ENTITLEMENT_ID, ROLE_ID, RESOURCE_CODE, SCOPE_CODE, CAPABILITY)
                VALUES (:id, :role_id, 'NL2SQL_DEEPSEC_PROBE', '*', 'FULL')
                """,
                {"id": str(uuid4()), "role_id": SYSTEM_ADMIN_ROLE_ID},
            )

    def get_user_by_login(self, normalized_login: str) -> UserRecord | None:
        with self.connection() as conn, conn.cursor() as cursor:
            cursor.execute(
                self._user_select() + " WHERE LOGIN_NAME_NORMALIZED = :login",
                {"login": normalized_login.casefold()},
            )
            row = cursor.fetchone()
            return self._user_from_row(cursor, row) if row else None

    def get_user(self, user_id: str) -> UserRecord | None:
        with self.connection() as conn, conn.cursor() as cursor:
            cursor.execute(self._user_select() + " WHERE USER_ID = :user_id", {"user_id": user_id})
            row = cursor.fetchone()
            return self._user_from_row(cursor, row) if row else None

    def list_users(self) -> list[UserRecord]:
        with self.connection() as conn, conn.cursor() as cursor:
            cursor.execute(self._user_select() + " ORDER BY LOGIN_NAME_NORMALIZED")
            rows = cursor.fetchall()
            return [self._user_from_row(cursor, row) for row in rows]

    @staticmethod
    def _user_select() -> str:
        return (
            "SELECT USER_ID, LOGIN_NAME, DISPLAY_NAME, PASSWORD_HASH, STATUS, "
            "FORCE_PASSWORD_CHANGE, FAILED_LOGIN_COUNT, LOCKED_UNTIL, VERSION_NO "
            "FROM NL2SQL_APP_USERS"
        )

    def _user_from_row(self, cursor: Any, row: Any) -> UserRecord:
        user_id = str(row[0])
        cursor.execute(
            "SELECT ROLE_ID FROM NL2SQL_APP_USER_ROLES WHERE USER_ID = :user_id ORDER BY ROLE_ID",
            {"user_id": user_id},
        )
        role_ids = [str(item[0]) for item in cursor.fetchall()]
        return UserRecord(
            user_id=user_id,
            login_name=str(row[1]),
            display_name=str(row[2]),
            password_hash=str(row[3]),
            status=str(row[4]),
            force_password_change=bool(row[5]),
            failed_login_count=int(row[6] or 0),
            locked_until=row[7],
            version=int(row[8]),
            role_ids=role_ids,
        )

    def create_user(self, user: UserRecord) -> UserRecord:
        try:
            with self.connection() as conn, conn.cursor() as cursor:
                self._assert_role_ids(cursor, user.role_ids)
                cursor.execute(
                    """
                    INSERT INTO NL2SQL_APP_USERS
                      (USER_ID, LOGIN_NAME, LOGIN_NAME_NORMALIZED, DISPLAY_NAME, PASSWORD_HASH,
                       STATUS, FORCE_PASSWORD_CHANGE, FAILED_LOGIN_COUNT, LOCKED_UNTIL, VERSION_NO)
                    VALUES
                      (:user_id, :login_name, :normalized, :display_name, :password_hash,
                       :status, :force_change, 0, NULL, 1)
                    """,
                    {
                        "user_id": user.user_id,
                        "login_name": user.login_name,
                        "normalized": user.login_name.casefold(),
                        "display_name": user.display_name,
                        "password_hash": user.password_hash,
                        "status": user.status,
                        "force_change": int(user.force_password_change),
                    },
                )
                self._replace_user_roles(cursor, user.user_id, user.role_ids)
                conn.commit()
        except Exception as exc:
            if "ORA-00001" in str(exc):
                raise SecurityConflict("同じログイン名のユーザーが既に存在します。") from exc
            raise
        return self.get_user(user.user_id) or user

    def update_user(
        self,
        user_id: str,
        *,
        expected_version: int,
        display_name: str,
        status: str,
        role_ids: list[str],
    ) -> UserRecord:
        with self.connection() as conn, conn.cursor() as cursor:
            # 最後の管理者判定と更新を一つの DB critical section に置く。
            # 複数 API worker が同時に別の管理者を無効化しても 0 人にはならない。
            cursor.execute("LOCK TABLE NL2SQL_APP_USERS IN SHARE ROW EXCLUSIVE MODE")
            cursor.execute("LOCK TABLE NL2SQL_APP_USER_ROLES IN SHARE ROW EXCLUSIVE MODE")
            self._assert_role_ids(cursor, role_ids)
            cursor.execute(
                "SELECT STATUS FROM NL2SQL_APP_USERS WHERE USER_ID = :user_id",
                {"user_id": user_id},
            )
            current_row = cursor.fetchone()
            if current_row is None:
                raise SecurityNotFound("ユーザーが見つかりません。")
            cursor.execute(
                "SELECT COUNT(*) FROM NL2SQL_APP_USER_ROLES "
                "WHERE USER_ID = :user_id AND ROLE_ID = :role_id",
                {"user_id": user_id, "role_id": SYSTEM_ADMIN_ROLE_ID},
            )
            is_admin = int(cursor.fetchone()[0]) > 0
            removes_admin = is_admin and (
                status != "ACTIVE" or SYSTEM_ADMIN_ROLE_ID not in role_ids
            )
            if removes_admin:
                cursor.execute(
                    """
                    SELECT COUNT(*)
                      FROM NL2SQL_APP_USERS u
                      JOIN NL2SQL_APP_USER_ROLES ur ON ur.USER_ID = u.USER_ID
                     WHERE u.STATUS = 'ACTIVE' AND ur.ROLE_ID = :role_id
                    """,
                    {"role_id": SYSTEM_ADMIN_ROLE_ID},
                )
                if int(cursor.fetchone()[0]) <= 1:
                    raise SecurityConflict(
                        "最後のシステム管理者は無効化または権限解除できません。"
                    )
            cursor.execute(
                """
                UPDATE NL2SQL_APP_USERS
                   SET DISPLAY_NAME = :display_name, STATUS = :status,
                       VERSION_NO = VERSION_NO + 1, UPDATED_AT = SYSTIMESTAMP
                 WHERE USER_ID = :user_id AND VERSION_NO = :expected_version
                """,
                {
                    "display_name": display_name,
                    "status": status,
                    "user_id": user_id,
                    "expected_version": expected_version,
                },
            )
            if cursor.rowcount == 0:
                self._raise_not_found_or_conflict(cursor, "NL2SQL_APP_USERS", "USER_ID", user_id)
            self._replace_user_roles(cursor, user_id, role_ids)
            conn.commit()
        updated = self.get_user(user_id)
        if updated is None:
            raise SecurityNotFound("ユーザーが見つかりません。")
        return updated

    def set_password(self, user_id: str, password_hash: str, *, force_change: bool) -> None:
        with self.connection() as conn, conn.cursor() as cursor:
            cursor.execute(
                """
                UPDATE NL2SQL_APP_USERS
                   SET PASSWORD_HASH = :password_hash, FORCE_PASSWORD_CHANGE = :force_change,
                       FAILED_LOGIN_COUNT = 0, LOCKED_UNTIL = NULL,
                       VERSION_NO = VERSION_NO + 1, UPDATED_AT = SYSTIMESTAMP
                 WHERE USER_ID = :user_id
                """,
                {
                    "password_hash": password_hash,
                    "force_change": int(force_change),
                    "user_id": user_id,
                },
            )
            if cursor.rowcount == 0:
                raise SecurityNotFound("ユーザーが見つかりません。")
            conn.commit()

    def record_login_failure(
        self, user_id: str, *, failed_count: int, locked_until: datetime | None
    ) -> None:
        with self.connection() as conn, conn.cursor() as cursor:
            cursor.execute(
                """
                UPDATE NL2SQL_APP_USERS SET FAILED_LOGIN_COUNT = :failed_count,
                    LOCKED_UNTIL = :locked_until, UPDATED_AT = SYSTIMESTAMP
                WHERE USER_ID = :user_id
                """,
                {"failed_count": failed_count, "locked_until": locked_until, "user_id": user_id},
            )
            conn.commit()

    def record_login_success(self, user_id: str, *, password_hash: str | None = None) -> None:
        with self.connection() as conn, conn.cursor() as cursor:
            if password_hash:
                cursor.execute(
                    """
                    UPDATE NL2SQL_APP_USERS SET FAILED_LOGIN_COUNT = 0, LOCKED_UNTIL = NULL,
                        PASSWORD_HASH = :password_hash, UPDATED_AT = SYSTIMESTAMP
                    WHERE USER_ID = :user_id
                    """,
                    {"password_hash": password_hash, "user_id": user_id},
                )
            else:
                cursor.execute(
                    """
                    UPDATE NL2SQL_APP_USERS SET FAILED_LOGIN_COUNT = 0, LOCKED_UNTIL = NULL,
                        UPDATED_AT = SYSTIMESTAMP WHERE USER_ID = :user_id
                    """,
                    {"user_id": user_id},
                )
            conn.commit()

    def list_roles(self, *, include_archived: bool = False) -> list[RoleRecord]:
        with self.connection() as conn, conn.cursor() as cursor:
            sql = self._role_select()
            if not include_archived:
                sql += " WHERE ARCHIVED = 0"
            sql += " ORDER BY ROLE_CODE"
            cursor.execute(sql)
            return [self._role_from_row(cursor, row) for row in cursor.fetchall()]

    def get_role(self, role_id: str) -> RoleRecord | None:
        with self.connection() as conn, conn.cursor() as cursor:
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
        cursor.execute(
            """
            SELECT ENTITLEMENT_ID, RESOURCE_CODE, SCOPE_CODE, CAPABILITY
              FROM NL2SQL_APP_DATA_ENTITLEMENTS
             WHERE ROLE_ID = :role_id
             ORDER BY RESOURCE_CODE, SCOPE_CODE, CAPABILITY
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
        )

    def create_role(self, role: RoleRecord) -> RoleRecord:
        try:
            with self.connection() as conn, conn.cursor() as cursor:
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
                raise SecurityConflict("同じロールコードが既に存在します。") from exc
            raise
        return self.get_role(role.role_id) or role

    def update_role(self, role: RoleRecord, *, expected_version: int) -> RoleRecord:
        with self.connection() as conn, conn.cursor() as cursor:
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
        with self.connection() as conn, conn.cursor() as cursor:
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

    def count_active_system_admins(self) -> int:
        with self.connection() as conn, conn.cursor() as cursor:
            cursor.execute(
                """
                SELECT COUNT(*)
                  FROM NL2SQL_APP_USERS u
                  JOIN NL2SQL_APP_USER_ROLES ur ON ur.USER_ID = u.USER_ID
                  JOIN NL2SQL_APP_ROLES r ON r.ROLE_ID = ur.ROLE_ID
                 WHERE u.STATUS = 'ACTIVE' AND r.ROLE_CODE = 'SYSTEM_ADMIN' AND r.ARCHIVED = 0
                """
            )
            return int(cursor.fetchone()[0])

    def create_session(self, session: SessionRecord) -> None:
        with self.connection() as conn, conn.cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO NL2SQL_AUTH_SESSIONS
                  (SESSION_ID, USER_ID, TOKEN_HASH, CSRF_TOKEN_HASH, IDLE_EXPIRES_AT,
                   ABSOLUTE_EXPIRES_AT, LAST_SEEN_AT)
                VALUES
                  (:session_id, :user_id, :token_hash, :csrf_hash, :idle_expires,
                   :absolute_expires, :last_seen)
                """,
                {
                    "session_id": session.session_id,
                    "user_id": session.user_id,
                    "token_hash": session.token_hash,
                    "csrf_hash": session.csrf_token_hash,
                    "idle_expires": session.idle_expires_at,
                    "absolute_expires": session.absolute_expires_at,
                    "last_seen": session.last_seen_at,
                },
            )
            conn.commit()

    def get_session_by_token_hash(self, token_hash: str) -> SessionRecord | None:
        with self.connection() as conn, conn.cursor() as cursor:
            cursor.execute(
                """
                SELECT SESSION_ID, USER_ID, TOKEN_HASH, CSRF_TOKEN_HASH, IDLE_EXPIRES_AT,
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
                user_id=str(row[1]),
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
        with self.connection() as conn, conn.cursor() as cursor:
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
        with self.connection() as conn, conn.cursor() as cursor:
            cursor.execute(
                "UPDATE NL2SQL_AUTH_SESSIONS SET REVOKED_AT = SYSTIMESTAMP "
                "WHERE SESSION_ID = :session_id AND REVOKED_AT IS NULL",
                {"session_id": session_id},
            )
            conn.commit()

    def revoke_user_sessions(self, user_id: str) -> None:
        with self.connection() as conn, conn.cursor() as cursor:
            cursor.execute(
                "UPDATE NL2SQL_AUTH_SESSIONS SET REVOKED_AT = SYSTIMESTAMP "
                "WHERE USER_ID = :user_id AND REVOKED_AT IS NULL",
                {"user_id": user_id},
            )
            conn.commit()

    def write_audit(
        self,
        *,
        actor_user_id: str | None,
        event_type: str,
        target_type: str,
        target_id: str,
        outcome: str,
        detail: dict[str, object],
        request_id: str,
        client_ip: str,
    ) -> None:
        payload = json.dumps(detail, ensure_ascii=False, separators=(",", ":"))
        with self.connection() as conn, conn.cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO NL2SQL_AUTH_AUDIT_LOG
                  (ACTOR_USER_ID, EVENT_TYPE, TARGET_TYPE, TARGET_ID, OUTCOME,
                   DETAIL_JSON, REQUEST_ID, CLIENT_IP)
                VALUES
                  (:actor, :event_type, :target_type, :target_id, :outcome,
                   :detail_json, :request_id, :client_ip)
                """,
                {
                    "actor": actor_user_id,
                    "event_type": event_type,
                    "target_type": target_type or "-",
                    "target_id": target_id or "-",
                    "outcome": outcome,
                    "detail_json": payload,
                    "request_id": request_id or "-",
                    "client_ip": client_ip or "-",
                },
            )
            conn.commit()

    def list_audit(self, *, limit: int = 200) -> list[AuditRecord]:
        with self.connection() as conn, conn.cursor() as cursor:
            cursor.execute(
                """
                SELECT AUDIT_ID, ACTOR_USER_ID, EVENT_TYPE, TARGET_TYPE, TARGET_ID,
                       OUTCOME, DETAIL_JSON, REQUEST_ID, CLIENT_IP, CREATED_AT
                  FROM NL2SQL_AUTH_AUDIT_LOG
                 ORDER BY CREATED_AT DESC, AUDIT_ID DESC FETCH FIRST :limit ROWS ONLY
                """,
                {"limit": limit},
            )
            return [_audit_record_from_row(row) for row in cursor.fetchall()]

    def page_audit(
        self, *, page: int, page_size: int
    ) -> tuple[list[AuditRecord], int, int]:
        with self.connection() as conn, conn.cursor() as cursor:
            cursor.execute("SELECT COUNT(*) FROM NL2SQL_AUTH_AUDIT_LOG")
            count_row = cursor.fetchone()
            total = int(count_row[0]) if count_row else 0
            resolved_page = _resolved_page(total, page, page_size)
            offset = (resolved_page - 1) * page_size
            cursor.execute(
                """
                SELECT AUDIT_ID, ACTOR_USER_ID, EVENT_TYPE, TARGET_TYPE, TARGET_ID,
                       OUTCOME, DETAIL_JSON, REQUEST_ID, CLIENT_IP, CREATED_AT
                  FROM NL2SQL_AUTH_AUDIT_LOG
                 ORDER BY CREATED_AT DESC, AUDIT_ID DESC
                OFFSET :offset ROWS FETCH NEXT :page_size ROWS ONLY
                """,
                {"offset": offset, "page_size": page_size},
            )
            records = [_audit_record_from_row(row) for row in cursor.fetchall()]
            return records, total, resolved_page

    def list_audit_between(
        self, *, start_at: datetime, end_at: datetime
    ) -> list[AuditRecord]:
        with self.connection() as conn, conn.cursor() as cursor:
            cursor.execute(
                """
                SELECT AUDIT_ID, ACTOR_USER_ID, EVENT_TYPE, TARGET_TYPE, TARGET_ID,
                       OUTCOME, DETAIL_JSON, REQUEST_ID, CLIENT_IP, CREATED_AT
                  FROM NL2SQL_AUTH_AUDIT_LOG
                 WHERE CREATED_AT >= :start_at AND CREATED_AT <= :end_at
                 ORDER BY CREATED_AT DESC, AUDIT_ID DESC
                """,
                {"start_at": start_at, "end_at": end_at},
            )
            return [_audit_record_from_row(row) for row in cursor.fetchall()]

    def get_deepsec_states(self) -> dict[tuple[str, int], dict[str, object]]:
        with self.connection() as conn, conn.cursor() as cursor:
            cursor.execute(
                """
                SELECT PLAN_VERSION, STEP_NO, STEP_KEY, CHECKSUM, STATUS,
                       ERROR_MESSAGE, EXECUTED_BY, EXECUTED_AT
                  FROM NL2SQL_DEEPSEC_MIGRATIONS
                """
            )
            return {
                (str(row[0]), int(row[1])): {
                    "step_key": str(row[2]),
                    "checksum": str(row[3]),
                    "status": str(row[4]),
                    "error_message": "" if row[5] in (None, "-") else str(row[5]),
                    "executed_by": str(row[6]) if row[6] else None,
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
        executed_by: str | None,
    ) -> None:
        with self.connection() as conn, conn.cursor() as cursor:
            cursor.execute(
                """
                MERGE INTO NL2SQL_DEEPSEC_MIGRATIONS m
                USING (SELECT :version plan_version, :step_no step_no FROM dual) s
                ON (m.PLAN_VERSION = s.plan_version AND m.STEP_NO = s.step_no)
                WHEN MATCHED THEN UPDATE SET
                    STEP_KEY = :step_key, CHECKSUM = :checksum, STATUS = :status,
                    ERROR_MESSAGE = :error_message, EXECUTED_BY = :executed_by,
                    EXECUTED_AT = CASE WHEN :status IN ('APPLIED', 'FAILED')
                                       THEN SYSTIMESTAMP ELSE NULL END,
                    UPDATED_AT = SYSTIMESTAMP
                WHEN NOT MATCHED THEN INSERT
                    (PLAN_VERSION, STEP_NO, STEP_KEY, CHECKSUM, STATUS, ERROR_MESSAGE,
                     EXECUTED_BY, EXECUTED_AT)
                VALUES
                    (:version, :step_no, :step_key, :checksum, :status, :error_message,
                     :executed_by, CASE WHEN :status IN ('APPLIED', 'FAILED')
                                        THEN SYSTIMESTAMP ELSE NULL END)
                """,
                {
                    "version": version,
                    "step_no": step_no,
                    "step_key": step_key,
                    "checksum": checksum,
                    "status": status,
                    "error_message": error_message[:2000] or "-",
                    "executed_by": executed_by,
                },
            )
            conn.commit()

    @staticmethod
    def _replace_user_roles(cursor: Any, user_id: str, role_ids: list[str]) -> None:
        cursor.execute(
            "DELETE FROM NL2SQL_APP_USER_ROLES WHERE USER_ID = :user_id", {"user_id": user_id}
        )
        for role_id in dict.fromkeys(role_ids):
            cursor.execute(
                "INSERT INTO NL2SQL_APP_USER_ROLES (USER_ID, ROLE_ID) VALUES (:user_id, :role_id)",
                {"user_id": user_id, "role_id": role_id},
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
        cursor.execute(
            "DELETE FROM NL2SQL_APP_DATA_ENTITLEMENTS WHERE ROLE_ID = :role_id",
            {"role_id": role.role_id},
        )
        for entitlement in role.entitlements:
            cursor.execute(
                """
                INSERT INTO NL2SQL_APP_DATA_ENTITLEMENTS
                  (ENTITLEMENT_ID, ROLE_ID, RESOURCE_CODE, SCOPE_CODE, CAPABILITY)
                VALUES (:id, :role_id, :resource, :scope, :capability)
                """,
                {
                    "id": entitlement.entitlement_id,
                    "role_id": role.role_id,
                    "resource": entitlement.resource_code,
                    "scope": entitlement.scope_code,
                    "capability": entitlement.capability,
                },
            )

    @staticmethod
    def _assert_role_ids(cursor: Any, role_ids: list[str]) -> None:
        for role_id in dict.fromkeys(role_ids):
            cursor.execute(
                "SELECT COUNT(*) FROM NL2SQL_APP_ROLES WHERE ROLE_ID = :role_id AND ARCHIVED = 0",
                {"role_id": role_id},
            )
            if int(cursor.fetchone()[0]) != 1:
                raise SecurityNotFound("指定された有効なロールが見つかりません。")

    @staticmethod
    def _raise_not_found_or_conflict(cursor: Any, table: str, column: str, value: str) -> None:
        queries = {
            ("NL2SQL_APP_USERS", "USER_ID"): (
                "SELECT COUNT(*) FROM NL2SQL_APP_USERS WHERE USER_ID = :value"
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
