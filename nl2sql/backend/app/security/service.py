"""アプリケーション認証/RBAC のユースケース。"""

from __future__ import annotations

import hashlib
import hmac
import importlib
import io
import json
import secrets
import threading
from datetime import UTC, datetime, timedelta
from functools import lru_cache
from typing import Any
from uuid import uuid4
from zoneinfo import ZoneInfo

from app.settings import Settings, get_settings

from .domain import (
    SYSTEM_ADMIN_ROLE_CODE,
    AuditRecord,
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
from .permissions import ALL_PERMISSION_CODES, expand_permissions
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


_AUDIT_EXPORT_HEADERS = (
    "監査 ID",
    "日時 (JST)",
    "イベント",
    "実行者",
    "対象種別",
    "対象 ID",
    "結果",
    "リクエスト ID",
    "クライアント IP",
    "詳細 JSON",
)
_EXCEL_MAX_DATA_ROWS_PER_SHEET = 1_048_575
_JST = ZoneInfo("Asia/Tokyo")


def _one_calendar_year_before(value: datetime) -> datetime:
    try:
        return value.replace(year=value.year - 1)
    except ValueError:
        # うるう日の 1 年前は 2 月末として扱う。
        return value.replace(year=value.year - 1, day=28)


class SecurityService:
    def __init__(self, store: SecurityStore, settings: Settings) -> None:
        self.store = store
        self.settings = settings
        self._bootstrap_lock = threading.Lock()
        self._bootstrap_checked = False

    def bootstrap(self) -> bool:
        login_name = self.settings.oracle_user.strip()
        password = self.settings.oracle_password
        if not login_name or not password:
            raise SecurityApiError(
                503,
                "初期システム管理者を作成できません。"
                "ORACLE_USER と ORACLE_PASSWORD を設定してください。",
            )
        return self.store.bootstrap(
            login_name=login_name,
            display_name=f"{login_name}（システム管理者）",
            password_hash=hash_password(password),
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

    def get_audit_page(
        self, *, page: int, page_size: int
    ) -> tuple[list[AuditRecord], int, int]:
        """監査ログを安定した新着順でページ取得する。"""

        return self.store.page_audit(page=page, page_size=page_size)

    def export_audit_log_xlsx(self, *, now: datetime | None = None) -> tuple[str, bytes]:
        """直近 12 か月の監査ログを Excel workbook として出力する。"""

        end_at = _aware(now or _now()).astimezone(UTC)
        start_at = _one_calendar_year_before(end_at)
        records = self.store.list_audit_between(start_at=start_at, end_at=end_at)

        openpyxl = importlib.import_module("openpyxl")
        styles = importlib.import_module("openpyxl.styles")
        write_only_cell = importlib.import_module("openpyxl.cell").WriteOnlyCell
        workbook = openpyxl.Workbook(write_only=True)
        header_font = styles.Font(bold=True)
        header_fill = styles.PatternFill(fill_type="solid", fgColor="E9EEF6")
        header_alignment = styles.Alignment(vertical="center")
        detail_alignment = styles.Alignment(vertical="top", wrap_text=True)

        def text_cell(sheet: Any, value: object) -> Any:
            cell = write_only_cell(sheet, value="" if value is None else str(value))
            # 「=...」等も formula ではなく監査原文の文字列として保存する。
            cell.data_type = "s"
            return cell

        sheet_count = max(
            1,
            (len(records) + _EXCEL_MAX_DATA_ROWS_PER_SHEET - 1)
            // _EXCEL_MAX_DATA_ROWS_PER_SHEET,
        )
        for sheet_index in range(sheet_count):
            offset = sheet_index * _EXCEL_MAX_DATA_ROWS_PER_SHEET
            sheet_records = records[offset : offset + _EXCEL_MAX_DATA_ROWS_PER_SHEET]
            sheet_name = "audit_logs" if sheet_count == 1 else f"audit_logs_{sheet_index + 1:03d}"
            sheet = workbook.create_sheet(sheet_name)
            sheet.freeze_panes = "A2"
            sheet.auto_filter.ref = f"A1:J{len(sheet_records) + 1}"
            for column_letter, width in {
                "A": 14,
                "B": 22,
                "C": 28,
                "D": 40,
                "E": 18,
                "F": 42,
                "G": 14,
                "H": 38,
                "I": 22,
                "J": 64,
            }.items():
                sheet.column_dimensions[column_letter].width = width

            header_cells = [text_cell(sheet, header) for header in _AUDIT_EXPORT_HEADERS]
            for cell in header_cells:
                cell.font = header_font
                cell.fill = header_fill
                cell.alignment = header_alignment
            sheet.append(header_cells)

            for record in sheet_records:
                created_at_cell = write_only_cell(
                    sheet,
                    value=_aware(record.created_at).astimezone(_JST).replace(tzinfo=None),
                )
                created_at_cell.number_format = "yyyy-mm-dd hh:mm:ss"
                detail_cell = text_cell(
                    sheet,
                    json.dumps(
                        record.detail,
                        ensure_ascii=False,
                        sort_keys=True,
                        separators=(",", ":"),
                        default=str,
                    ),
                )
                detail_cell.alignment = detail_alignment
                sheet.append(
                    [
                        record.audit_id,
                        created_at_cell,
                        text_cell(sheet, record.event_type),
                        text_cell(sheet, record.actor_user_id),
                        text_cell(sheet, record.target_type),
                        text_cell(sheet, record.target_id),
                        text_cell(sheet, record.outcome),
                        text_cell(sheet, record.request_id),
                        text_cell(sheet, record.client_ip),
                        detail_cell,
                    ]
                )

        buffer = io.BytesIO()
        workbook.save(buffer)
        start_label = start_at.astimezone(_JST).strftime("%Y%m%d")
        end_label = end_at.astimezone(_JST).strftime("%Y%m%d")
        return f"nl2sql_audit_logs_{start_label}-{end_label}.xlsx", buffer.getvalue()

    def login(
        self,
        login_name: str,
        password: str,
        *,
        request_id: str = "",
        client_ip: str = "",
    ) -> tuple[Principal, str, str]:
        self.ensure_bootstrapped()
        user = self.store.get_user_by_login(login_name.strip().casefold())
        now = _now()
        if user is None:
            self._audit(
                actor=None,
                event="LOGIN_FAILED",
                target_type="USER",
                target_id=login_name.strip().casefold(),
                outcome="DENIED",
                detail={"reason": "invalid_credentials"},
                request_id=request_id,
                client_ip=client_ip,
            )
            raise LoginFailed()
        if user.status != "ACTIVE" or (
            user.locked_until is not None and _aware(user.locked_until) > now
        ):
            self._audit(
                actor=user.user_id,
                event="LOGIN_FAILED",
                target_type="USER",
                target_id=user.user_id,
                outcome="DENIED",
                detail={"reason": "account_unavailable"},
                request_id=request_id,
                client_ip=client_ip,
            )
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
            self._audit(
                actor=user.user_id,
                event="LOGIN_FAILED",
                target_type="USER",
                target_id=user.user_id,
                outcome="DENIED",
                detail={"reason": "invalid_credentials", "locked": locked_until is not None},
                request_id=request_id,
                client_ip=client_ip,
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
        self._audit(
            actor=user.user_id,
            event="LOGIN_SUCCEEDED",
            target_type="USER",
            target_id=user.user_id,
            outcome="SUCCESS",
            detail={},
            request_id=request_id,
            client_ip=client_ip,
        )
        return principal, token, csrf_token

    def authenticate_session(self, token: str) -> Principal:
        if not token:
            raise SecurityApiError(401, "ログインしてください。")
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
        self.store.revoke_session(principal.session_id)
        self._audit(
            actor=principal.user_id,
            event="LOGOUT",
            target_type="SESSION",
            target_id=principal.session_id,
            outcome="SUCCESS",
            detail={},
            request_id=request_id,
            client_ip=client_ip,
        )

    def change_password(
        self,
        principal: Principal,
        current_password: str,
        new_password: str,
        *,
        request_id: str = "",
        client_ip: str = "",
    ) -> Principal:
        user = self.store.get_user(principal.user_id)
        if user is None or not verify_password(current_password, user.password_hash)[0]:
            raise SecurityApiError(400, "現在のパスワードを確認してください。")
        self._validate_new_password(new_password, user.login_name)
        self.store.set_password(user.user_id, hash_password(new_password), force_change=False)
        self.store.revoke_user_sessions(user.user_id)
        self._audit(
            actor=user.user_id,
            event="PASSWORD_CHANGED",
            target_type="USER",
            target_id=user.user_id,
            outcome="SUCCESS",
            detail={},
            request_id=request_id,
            client_ip=client_ip,
        )
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
            role_ids=list(dict.fromkeys(role_ids)),
        )
        try:
            created = self.store.create_user(user)
        except (SecurityConflict, SecurityNotFound) as exc:
            raise self._store_error(exc) from exc
        self._audit_mutation(actor, "USER_CREATED", "USER", created.user_id, request_id, client_ip)
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
        current_roles = [self.store.get_role(role_id) for role_id in current.role_ids]
        is_admin = any(role and role.role_code == SYSTEM_ADMIN_ROLE_CODE for role in current_roles)
        next_roles = [self.store.get_role(role_id) for role_id in role_ids]
        remains_admin = any(
            role and role.role_code == SYSTEM_ADMIN_ROLE_CODE for role in next_roles
        )
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
                role_ids=list(dict.fromkeys(role_ids)),
            )
        except (SecurityConflict, SecurityNotFound) as exc:
            raise self._store_error(exc) from exc
        if status != "ACTIVE":
            self.store.revoke_user_sessions(user_id)
        self._audit_mutation(actor, "USER_UPDATED", "USER", user_id, request_id, client_ip)
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
        self._audit_mutation(actor, "PASSWORD_RESET", "USER", user_id, request_id, client_ip)
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
        self._audit_mutation(actor, "USER_UNLOCKED", "USER", user_id, request_id, client_ip)
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
        self._audit_mutation(actor, "ROLE_CREATED", "ROLE", created.role_id, request_id, client_ip)
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
        self._audit_mutation(actor, "ROLE_UPDATED", "ROLE", role_id, request_id, client_ip)
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
        self._audit_mutation(actor, "ROLE_ARCHIVED", "ROLE", role_id, request_id, client_ip)
        return archived

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
        unknown = permissions - ALL_PERMISSION_CODES
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

    def _audit_mutation(
        self,
        actor: Principal,
        event: str,
        target_type: str,
        target_id: str,
        request_id: str,
        client_ip: str,
    ) -> None:
        self._audit(
            actor=actor.user_id,
            event=event,
            target_type=target_type,
            target_id=target_id,
            outcome="SUCCESS",
            detail={},
            request_id=request_id,
            client_ip=client_ip,
        )

    def _audit(
        self,
        *,
        actor: str | None,
        event: str,
        target_type: str,
        target_id: str,
        outcome: str,
        detail: dict[str, object],
        request_id: str,
        client_ip: str,
    ) -> None:
        self.store.write_audit(
            actor_user_id=actor,
            event_type=event,
            target_type=target_type,
            target_id=target_id,
            outcome=outcome,
            detail=detail,
            request_id=request_id,
            client_ip=client_ip,
        )


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
