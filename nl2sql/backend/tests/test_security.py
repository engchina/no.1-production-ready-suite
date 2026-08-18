"""Application auth/RBAC の回帰テスト。"""

from __future__ import annotations

import asyncio
import re
from collections.abc import AsyncGenerator, Callable, Mapping
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import cast

import httpx
import pytest
from fastapi import HTTPException, Request, Response

from app.cli.app_security_migrate import main as security_migrate_main
from app.cli.app_security_migrate import split_ddl
from app.features.nl2sql import router as nl2sql_router
from app.features.nl2sql.models import HistoryItem, Nl2SqlEngine
from app.features.nl2sql.service import Nl2SqlService
from app.features.nl2sql.store import MemoryNl2SqlStore
from app.main import app
from app.security import dependencies as security_dependencies
from app.security.dependencies import authorize_api_request, local_debug_principal
from app.security.domain import (
    SYSTEM_ADMIN_ROLE_ID,
    DataEntitlementRecord,
    Principal,
    RoleRecord,
    UserRecord,
)
from app.security.passwords import PasswordPolicyError, hash_password, validate_password
from app.security.permissions import (
    ALL_PERMISSION_CODES,
    PERMISSION_CATALOG,
    UNCLASSIFIED_PERMISSION,
    permission_for_route,
)
from app.security.router import (
    change_password,
    logout,
    me,
)
from app.security.schemas import DataEntitlementInput, PasswordChangeRequest, RoleData, UserData
from app.security.service import (
    LoginFailed,
    SecurityApiError,
    SecurityService,
    reset_security_service,
)
from app.security.store import (
    InMemorySecurityStore,
    OracleSecurityStore,
    SecurityConflict,
    SecurityStore,
)
from app.settings import Settings, get_settings


def _settings() -> Settings:
    return Settings.model_construct(
        oracle_user="DBADMIN",
        oracle_password="DbAdminPass!123",
        app_admin_username="system_admin",
        app_admin_password="AppAdminPass123",
        oracle_dsn="test",
        nl2sql_persistence_mode="memory",
        app_auth_enabled=True,
        app_auth_failed_login_limit=5,
        app_auth_lockout_minutes=15,
        app_auth_idle_timeout_minutes=30,
        app_auth_absolute_timeout_hours=12,
        app_auth_password_min_length=12,
        app_auth_password_max_length=128,
    )


def _patch_app_admin_env(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    *,
    username: str = "system_admin",
    password: str = "AppAdminPass123",
) -> Path:
    env_file = tmp_path / ".env"
    env_file.write_text(
        f"APP_ADMIN_USERNAME={username}\n"
        f"APP_ADMIN_PASSWORD={password}\n"
        "APP_AUTH_ENABLED=true\n",
        encoding="utf-8",
    )
    monkeypatch.setattr("app.security.service._BACKEND_ENV_FILE", env_file)
    return env_file


def _service() -> SecurityService:
    store = InMemorySecurityStore()
    assert (
        store.bootstrap(
            login_name="admin",
            display_name="ADMIN（システム管理者）",
            password_hash=hash_password("BootstrapPass!123"),
        )
        is True
    )
    assert (
        store.bootstrap(
            login_name="admin",
            display_name="ADMIN（システム管理者）",
            password_hash=hash_password("BootstrapPass!123"),
        )
        is False
    )
    service = SecurityService(store, _settings())
    return service


def _login(service: SecurityService) -> tuple[Principal, str, str]:
    return service.login("admin", "BootstrapPass!123")


async def _inline_threadpool(
    function: Callable[..., object],
    *args: object,
    **kwargs: object,
) -> object:
    """AnyIO worker が使えない sandbox でも API 契約だけを検証する。"""
    return function(*args, **kwargs)


def _patch_security_threadpools(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("app.security.dependencies.run_in_threadpool", _inline_threadpool)
    monkeypatch.setattr("app.security.router.run_in_threadpool", _inline_threadpool)


class _NoAuthTableStore:
    def __init__(self) -> None:
        self.calls: list[str] = []

    def __getattr__(self, name: str) -> object:
        self.calls.append(name)
        raise AssertionError(f"auth table store must not be used for env admin: {name}")


class _MissingSecuritySchemaStore:
    def get_user_by_login(self, _normalized_login: str) -> UserRecord | None:
        raise RuntimeError('ORA-00942: table or view "ADMIN"."NL2SQL_APP_USERS" does not exist')


class _RecordingCursor:
    def __init__(self, fetchone_rows: list[tuple[int]] | None = None) -> None:
        self.executed: list[tuple[str, dict[str, object]]] = []
        self._fetchone_rows = list(fetchone_rows or [])

    def execute(self, sql: str, params: Mapping[str, object] | None = None) -> None:
        self.executed.append((sql, dict(params or {})))

    def fetchone(self) -> tuple[int]:
        return self._fetchone_rows.pop(0) if self._fetchone_rows else (0,)


def test_debug_auth_bypass_is_local_only_and_has_system_admin_permissions() -> None:
    local = Settings.model_construct(debug=True, environment="local")
    production = Settings.model_construct(debug=True, environment="production")
    disabled = Settings.model_construct(debug=False, environment="local")

    assert local.local_debug_enabled is True
    assert production.local_debug_enabled is False
    assert disabled.local_debug_enabled is False

    principal = local_debug_principal()
    assert principal.is_system_admin is True
    assert principal.force_password_change is False
    assert principal.password_change_allowed is False
    assert principal.permissions == set(ALL_PERMISSION_CODES)


def test_local_debug_me_and_logout_need_no_session_or_csrf(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = get_settings()
    monkeypatch.setattr(settings, "debug", True)
    monkeypatch.setattr(settings, "environment", "local")
    monkeypatch.setattr(settings, "app_auth_enabled", True)

    async def exercise() -> None:
        request = Request(
            {
                "type": "http",
                "method": "POST",
                "path": "/api/auth/logout",
                "headers": [],
                "client": ("127.0.0.1", 50000),
            }
        )
        authorization = cast(AsyncGenerator[None, None], authorize_api_request(request))
        await anext(authorization)
        current = me(request)
        assert current.data is not None
        assert current.data.model_dump() == {
            "user_id": "00000000-0000-0000-0000-000000000000",
            "login_name": "local-debug",
            "display_name": "ローカル DEBUG 管理者",
            "status": "ACTIVE",
            "force_password_change": False,
            "role_codes": ["SYSTEM_ADMIN"],
            "permissions": sorted(ALL_PERMISSION_CODES),
            "data_entitlements": [],
            "debug_mode": True,
            "password_change_allowed": False,
        }
        logged_out = logout(request, Response())
        assert logged_out.data == {"logged_out": False}
        with pytest.raises(SecurityApiError, match="DEBUG"):
            change_password(
                PasswordChangeRequest(
                    current_password="unused",
                    new_password="unused",
                ),
                request,
                Response(),
            )
        await authorization.aclose()

    asyncio.run(exercise())


def test_debug_flag_cannot_bypass_auth_outside_local(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = get_settings()
    monkeypatch.setattr(settings, "debug", True)
    monkeypatch.setattr(settings, "environment", "production")
    monkeypatch.setattr(settings, "app_auth_enabled", True)

    class RejectingSecurityService:
        @staticmethod
        def authenticate_session(token: str) -> Principal:
            assert token == ""
            raise SecurityApiError(401, "ログインしてください。")

    async def inline_threadpool(function: Callable[..., object], *args: object) -> object:
        return function(*args)

    monkeypatch.setattr(
        security_dependencies,
        "get_security_service",
        lambda: RejectingSecurityService(),
    )
    monkeypatch.setattr(security_dependencies, "run_in_threadpool", inline_threadpool)

    async def exercise() -> None:
        request = Request(
            {
                "type": "http",
                "method": "GET",
                "path": "/api/auth/me",
                "headers": [],
                "client": ("127.0.0.1", 50000),
                "route": SimpleNamespace(path="/auth/me"),
            }
        )
        authorization = authorize_api_request(request)
        with pytest.raises(HTTPException) as error:
            await anext(authorization)
        assert error.value.status_code == 401

    asyncio.run(exercise())


def test_bootstrap_login_session_and_password_independence() -> None:
    service = _service()
    principal, token, csrf = _login(service)
    assert principal.is_system_admin
    assert principal.force_password_change
    assert service.authenticate_session(token).user_id == principal.user_id
    service.verify_csrf(principal, csrf, csrf)
    with pytest.raises(SecurityApiError, match="安全性"):
        service.verify_csrf(principal, csrf, "different")

    service.change_password(principal, "BootstrapPass!123", "IndependentPass!456")
    with pytest.raises(SecurityApiError, match="ログイン"):
        service.authenticate_session(token)
    changed, _, _ = service.login("ADMIN", "IndependentPass!456")
    assert changed.force_password_change is False


def test_configured_system_admin_login_uses_app_admin_credentials_without_auth_tables(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _patch_app_admin_env(monkeypatch, tmp_path)
    store = _NoAuthTableStore()
    service = SecurityService(cast(SecurityStore, store), _settings())

    principal, token, csrf = service.login("system_admin", "AppAdminPass123")

    assert principal.is_system_admin
    assert principal.force_password_change is False
    assert principal.password_change_allowed is True
    assert principal.login_name == "system_admin"
    assert token.startswith("nl2sql-system-admin-v1.")
    assert csrf
    authenticated = service.authenticate_session(token)
    assert authenticated.is_system_admin
    assert authenticated.user_id == principal.user_id
    assert authenticated.password_change_allowed is True
    service.verify_csrf(authenticated, csrf, csrf)
    service.logout(authenticated)
    assert store.calls == []


def test_configured_system_admin_wrong_password_does_not_touch_auth_tables(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _patch_app_admin_env(monkeypatch, tmp_path)
    store = _NoAuthTableStore()
    service = SecurityService(cast(SecurityStore, store), _settings())

    with pytest.raises(LoginFailed):
        service.login("system_admin", "wrong-password")
    assert store.calls == []


@pytest.mark.parametrize("login_name", ["System_Admin", "SYSTEM_ADMIN"])
def test_system_admin_case_variants_do_not_fall_back_to_db_users(
    login_name: str,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _patch_app_admin_env(monkeypatch, tmp_path)
    store = _NoAuthTableStore()
    service = SecurityService(cast(SecurityStore, store), _settings())

    with pytest.raises(LoginFailed):
        service.login(login_name, "AppAdminPass123")
    assert store.calls == []


def test_configured_system_admin_requires_fixed_username(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _patch_app_admin_env(monkeypatch, tmp_path, username="app-admin")
    service = SecurityService(cast(SecurityStore, _NoAuthTableStore()), _settings())

    with pytest.raises(SecurityApiError) as error:
        service.login("system_admin", "AppAdminPass123")
    assert error.value.status_code == 503
    assert "system_admin" in error.value.public_message


def test_configured_system_admin_password_change_updates_env_without_auth_tables(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    env_file = _patch_app_admin_env(monkeypatch, tmp_path)
    store = _NoAuthTableStore()
    service = SecurityService(cast(SecurityStore, store), _settings())
    principal, token, _csrf = service.login("system_admin", "AppAdminPass123")

    changed = service.change_password(principal, "AppAdminPass123", "UpdatedPass123A")

    assert changed.user_id == principal.user_id
    env_text = env_file.read_text(encoding="utf-8")
    assert "APP_ADMIN_USERNAME=system_admin" in env_text
    assert "APP_ADMIN_PASSWORD=UpdatedPass123A" in env_text
    assert env_text.index("APP_ADMIN_USERNAME=system_admin") < env_text.index(
        "APP_AUTH_ENABLED=true"
    )
    with pytest.raises(SecurityApiError):
        service.authenticate_session(token)
    with pytest.raises(LoginFailed):
        service.login("system_admin", "AppAdminPass123")
    relogged, _, _ = service.login("system_admin", "UpdatedPass123A")
    assert relogged.is_system_admin
    assert store.calls == []


def test_database_credentials_no_longer_bypass_auth_tables() -> None:
    service = SecurityService(cast(SecurityStore, _MissingSecuritySchemaStore()), _settings())

    with pytest.raises(SecurityApiError) as error:
        service.login("DBADMIN", "DbAdminPass!123")
    assert error.value.status_code == 503
    assert "認証テーブル" in error.value.public_message


def test_table_user_login_reports_503_when_auth_table_is_missing() -> None:
    service = SecurityService(cast(SecurityStore, _MissingSecuritySchemaStore()), _settings())

    with pytest.raises(SecurityApiError) as error:
        service.login("app.user", "AppUserPass!123")
    assert error.value.status_code == 503
    assert "認証テーブル" in error.value.public_message


def test_password_policy_requires_all_character_classes_without_expiry() -> None:
    with pytest.raises(PasswordPolicyError):
        validate_password("onlylowercase", login_name="user", min_length=12, max_length=128)
    with pytest.raises(PasswordPolicyError, match="推測"):
        validate_password("Password123!", login_name="user", min_length=12, max_length=128)
    validate_password("StrongPass!234", login_name="user", min_length=12, max_length=128)


def test_security_migration_splitter_never_executes_comment_only_buffers() -> None:
    assert split_ddl("-- header\nCREATE TABLE EXAMPLE (ID NUMBER);\n-- trailing") == [
        "CREATE TABLE EXAMPLE (ID NUMBER)"
    ]


def test_dashboard_permission_is_retired_for_appearance_settings() -> None:
    assert "dashboard.view" not in ALL_PERMISSION_CODES
    assert "menu.settings_appearance" in ALL_PERMISSION_CODES

    service = _service()
    actor, _, _ = _login(service)

    role = service.create_role(
        role_code="LEGACY_DASHBOARD",
        display_name="旧ダッシュボード",
        description="",
        permissions={"dashboard.view"},
        entitlements=[],
        actor=actor,
    )
    assert role.permissions == {"menu.settings_appearance"}


def test_stale_permission_codes_are_hidden_from_role_api_and_principals() -> None:
    service = _service()
    store = service.store
    actor, _, _ = _login(service)

    store.create_role(
        RoleRecord(
            role_id="role-stale",
            role_code="STALE_ROLE",
            display_name="旧権限を含むロール",
            description="",
            is_built_in=False,
            archived=False,
            version=1,
            permissions={"dashboard.view", "menu.settings_appearance", "search.view"},
        )
    )
    service.create_user(
        login_name="stale.user",
        display_name="旧権限ユーザー",
        role_ids=["role-stale"],
        temporary_password="IndependentPass!456",
        actor=actor,
    )

    stale_role = store.get_role("role-stale")
    assert stale_role is not None
    assert "dashboard.view" not in RoleData.from_record(stale_role).permissions

    principal, _, _ = service.login("stale.user", "IndependentPass!456")
    assert "dashboard.view" not in principal.permissions
    assert {"menu.settings_appearance", "menu.query", "menu.direct_sql"} <= principal.permissions


def test_data_entitlement_capability_is_structured() -> None:
    with pytest.raises(ValueError, match="capability"):
        DataEntitlementInput(
            resource_code="NL2SQL_DEEPSEC_PROBE",
            scope_code="SALES",
            capability="ARBITRARY_SQL",
        )


def test_oracle_role_access_uses_safe_data_entitlement_bind_names() -> None:
    cursor = _RecordingCursor()
    role = RoleRecord(
        role_id="role-1",
        role_code="QUERY_VIEWER",
        display_name="検索閲覧",
        description="",
        is_built_in=False,
        archived=False,
        version=1,
        permissions={"search.execute", "search.view"},
        entitlements=[
            DataEntitlementRecord(
                entitlement_id="entitlement-1",
                role_id="role-1",
                resource_code="NL2SQL_DEEPSEC_PROBE",
                scope_code="*",
                capability="ROW_READ",
            )
        ],
    )

    OracleSecurityStore._replace_role_access(cursor, role)

    permission_inserts = [
        item for item in cursor.executed if "INSERT INTO NL2SQL_APP_ROLE_PERMISSIONS" in item[0]
    ]
    assert len(permission_inserts) == 2
    data_inserts = [
        item for item in cursor.executed if "INSERT INTO NL2SQL_APP_DATA_ENTITLEMENTS" in item[0]
    ]
    assert len(data_inserts) == 1
    sql, params = data_inserts[0]
    assert re.search(r":(?:id|resource|scope|capability)\b", sql) is None
    assert params == {
        "entitlement_id": "entitlement-1",
        "role_id": "role-1",
        "resource_code": "NL2SQL_DEEPSEC_PROBE",
        "scope_code": "*",
        "capability_code": "ROW_READ",
    }


def test_oracle_system_admin_probe_entitlement_uses_safe_bind_name() -> None:
    cursor = _RecordingCursor(fetchone_rows=[(0,)])

    OracleSecurityStore._ensure_system_admin_probe_entitlement(cursor)

    data_inserts = [
        item for item in cursor.executed if "INSERT INTO NL2SQL_APP_DATA_ENTITLEMENTS" in item[0]
    ]
    assert len(data_inserts) == 1
    sql, params = data_inserts[0]
    assert re.search(r":id\b", sql) is None
    assert ":entitlement_id" in sql
    assert set(params) == {"entitlement_id", "role_id"}
    assert params["role_id"] == SYSTEM_ADMIN_ROLE_ID
    assert isinstance(params["entitlement_id"], str)


def test_multiple_roles_union_permissions_and_data_entitlements() -> None:
    service = _service()
    actor, _, _ = _login(service)
    role_a = service.create_role(
        role_code="QUERY_VIEWER",
        display_name="検索閲覧",
        description="",
        permissions={"search.view"},
        entitlements=[("NL2SQL_DEEPSEC_PROBE", "SALES", "ROW_READ")],
        actor=actor,
    )
    role_b = service.create_role(
        role_code="QUERY_RUNNER",
        display_name="検索実行",
        description="",
        permissions={"search.execute"},
        entitlements=[("NL2SQL_DEEPSEC_PROBE", "SALES", "SENSITIVE_READ")],
        actor=actor,
    )
    user, password = service.create_user(
        login_name="query.user",
        display_name="検索ユーザー",
        role_ids=[role_a.role_id, role_b.role_id],
        temporary_password="QueryUserPass!123",
        actor=actor,
    )
    assert password == "QueryUserPass!123"
    principal, _, _ = service.login(user.login_name, password)
    assert principal.permissions >= {
        "menu.query",
        "menu.direct_sql",
        "menu.sql_to_question",
        "menu.history",
    }
    assert {(item.scope_code, item.capability) for item in principal.data_entitlements} == {
        ("SALES", "ROW_READ"),
        ("SALES", "SENSITIVE_READ"),
    }


def test_last_system_admin_cannot_be_disabled_or_unassigned() -> None:
    service = _service()
    actor, _, _ = _login(service)
    admin = service.store.get_user(actor.user_id)
    assert admin is not None
    assert admin.role_ids == [SYSTEM_ADMIN_ROLE_ID]
    with pytest.raises(SecurityApiError, match="最後のシステム管理者"):
        service.update_user(
            admin.user_id,
            expected_version=admin.version,
            display_name=admin.display_name,
            status="DISABLED",
            role_ids=admin.role_ids,
            actor=actor,
        )
    with pytest.raises(SecurityConflict, match="最後のシステム管理者"):
        service.store.update_user(
            admin.user_id,
            expected_version=admin.version,
            display_name=admin.display_name,
            status="DISABLED",
            role_ids=admin.role_ids,
        )


def test_bootstrap_user_is_marked_in_user_response() -> None:
    service = _service()
    actor, _, _ = _login(service)
    admin = service.store.get_user(actor.user_id)

    assert admin is not None
    assert admin.is_bootstrap_admin is True
    assert UserData.from_record(admin).is_bootstrap_admin is True


def test_system_admin_role_cannot_be_assigned_to_new_user() -> None:
    service = _service()
    actor, _, _ = _login(service)

    with pytest.raises(SecurityApiError, match="初期システム管理者"):
        service.create_user(
            login_name="extra.admin",
            display_name="追加管理者",
            role_ids=[SYSTEM_ADMIN_ROLE_ID],
            temporary_password="ExtraAdminPass!123",
            actor=actor,
        )

    assert service.store.get_user_by_login("extra.admin") is None


@pytest.mark.parametrize("login_name", ["system_admin", "System_Admin", "SYSTEM_ADMIN"])
def test_reserved_system_admin_login_name_cannot_be_created(login_name: str) -> None:
    service = _service()
    actor, _, _ = _login(service)
    role = service.create_role(
        role_code="QUERY_VIEWER",
        display_name="検索閲覧",
        description="",
        permissions={"menu.query"},
        entitlements=[],
        actor=actor,
    )

    with pytest.raises(SecurityApiError, match="構成管理者専用"):
        service.create_user(
            login_name=login_name,
            display_name="予約名",
            role_ids=[role.role_id],
            temporary_password="ReservedPass123A",
            actor=actor,
        )


def test_system_admin_role_cannot_be_added_to_non_bootstrap_user() -> None:
    service = _service()
    actor, _, _ = _login(service)
    user, _password = service.create_user(
        login_name="query.user",
        display_name="検索ユーザー",
        role_ids=[],
        temporary_password="QueryUserPass!123",
        actor=actor,
    )

    with pytest.raises(SecurityApiError, match="初期システム管理者"):
        service.update_user(
            user.user_id,
            expected_version=user.version,
            display_name=user.display_name,
            status="ACTIVE",
            role_ids=[SYSTEM_ADMIN_ROLE_ID],
            actor=actor,
        )


def test_legacy_non_bootstrap_system_admin_can_be_removed_but_not_reassigned() -> None:
    service = _service()
    actor, _, _ = _login(service)
    legacy_admin = service.store.create_user(
        UserRecord(
            user_id="legacy-admin-user",
            login_name="legacy.admin",
            display_name="旧管理者",
            password_hash="legacy-hash",
            status="ACTIVE",
            force_password_change=False,
            failed_login_count=0,
            locked_until=None,
            version=1,
            role_ids=[SYSTEM_ADMIN_ROLE_ID],
            is_bootstrap_admin=False,
        )
    )

    removed = service.update_user(
        legacy_admin.user_id,
        expected_version=legacy_admin.version,
        display_name=legacy_admin.display_name,
        status="ACTIVE",
        role_ids=[],
        actor=actor,
    )

    assert removed.role_ids == []
    with pytest.raises(SecurityApiError, match="初期システム管理者"):
        service.update_user(
            removed.user_id,
            expected_version=removed.version,
            display_name=removed.display_name,
            status="ACTIVE",
            role_ids=[SYSTEM_ADMIN_ROLE_ID],
            actor=actor,
        )


def test_login_lockout_is_generic() -> None:
    service = _service()
    for _ in range(5):
        with pytest.raises(SecurityApiError) as error:
            service.login("ADMIN", "wrong")
        assert error.value.public_message == "ログイン名またはパスワードを確認してください。"
    user = service.store.get_user_by_login("admin")
    assert user is not None
    assert user.locked_until is not None
    assert user.locked_until > datetime.now(UTC)


def test_every_api_route_is_classified_by_manifest() -> None:
    for path, operations in app.openapi()["paths"].items():
        if not path.startswith("/api"):
            continue
        route_path = path.removeprefix("/api")
        for method in operations:
            if method.upper() not in {"GET", "POST", "PUT", "PATCH", "DELETE"}:
                continue
            # Public/auth-only routes deliberately return None; all others require a code.
            permission = permission_for_route(method, route_path)
            if route_path not in {
                "/health",
                "/ready",
                "/ready/database",
                "/auth/login",
                "/auth/me",
                "/auth/logout",
                "/auth/password/change",
            }:
                assert not (
                    permission and UNCLASSIFIED_PERMISSION in permission
                ), f"unclassified route: {method.upper()} {path}"

    assert permission_for_route("POST", "/nl2sql/execute") == frozenset(
        {"menu.query", "menu.direct_sql"}
    )
    assert permission_for_route("POST", "/nl2sql/db-admin/execute") == frozenset({"menu.admin_sql"})


def test_security_audit_permission_and_api_are_removed() -> None:
    catalog_codes = {item.code for item in PERMISSION_CATALOG}

    assert "menu.security_audit" not in catalog_codes
    assert "security.audit.view" not in ALL_PERMISSION_CODES
    assert permission_for_route("GET", "/security/audit/page") == frozenset(
        {UNCLASSIFIED_PERMISSION}
    )
    assert not any(path.startswith("/api/security/audit") for path in app.openapi()["paths"])


def test_security_migration_preview_includes_audit_cleanup(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr("sys.argv", ["app_security_migrate"])

    assert security_migrate_main() == 0

    output = capsys.readouterr().out
    assert "migration=004" in output
    assert "migration=005" in output
    assert "migration=009" in output


def test_auth_api_sets_http_only_session_and_requires_csrf(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _patch_security_threadpools(monkeypatch)
    _patch_app_admin_env(monkeypatch, tmp_path, password="BootstrapPass123")
    settings = get_settings()
    monkeypatch.setattr(settings, "app_auth_enabled", True)
    monkeypatch.setattr(settings, "app_auth_cookie_secure", False)
    monkeypatch.setattr(settings, "oracle_user", "ADMIN")
    monkeypatch.setattr(settings, "oracle_password", "BootstrapPass!123")
    monkeypatch.setattr(settings, "app_admin_username", "system_admin")
    monkeypatch.setattr(settings, "app_admin_password", "BootstrapPass123")
    monkeypatch.setattr(settings, "nl2sql_persistence_mode", "memory")
    reset_security_service()

    async def exercise() -> None:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            unauthenticated = await client.get("/api/auth/me")
            assert unauthenticated.status_code == 401

            login = await client.post(
                "/api/auth/login",
                json={"login_name": "system_admin", "password": "BootstrapPass123"},
            )
            assert login.status_code == 200
            assert "HttpOnly" in login.headers.get_list("set-cookie")[0]
            me = await client.get("/api/auth/me")
            assert me.status_code == 200
            assert me.json()["data"]["role_codes"] == ["SYSTEM_ADMIN"]
            assert me.json()["data"]["force_password_change"] is False

            no_csrf = await client.post("/api/auth/logout")
            assert no_csrf.status_code == 403
            csrf = client.cookies.get("nl2sql_csrf")
            assert csrf
            logout = await client.post(
                "/api/auth/logout",
                headers={"X-CSRF-Token": csrf},
            )
            assert logout.status_code == 200

    try:
        asyncio.run(exercise())
    finally:
        reset_security_service()


def test_deepsec_config_patch_updates_runtime_without_restart(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _patch_security_threadpools(monkeypatch)
    env_file = tmp_path / ".env"
    env_file.write_text(
        "ORACLE_DEEPSEC_END_USER=NL2SQL_APP_END_USER\n"
        "ORACLE_DEEPSEC_END_USER_PASSWORD=OldSecret123\n",
        encoding="utf-8",
    )
    env_file.chmod(0o600)
    closed: list[bool] = []
    monkeypatch.setattr("app.security.deepsec._BACKEND_ENV_FILE", env_file)
    monkeypatch.setattr("app.security.deepsec.close_oracle_pools", lambda: closed.append(True))
    settings = get_settings()
    monkeypatch.setattr(settings, "environment", "local")
    monkeypatch.setattr(settings, "debug", True)
    monkeypatch.setattr(settings, "app_auth_enabled", True)
    monkeypatch.setattr(settings, "oracle_user", "ADMIN")
    monkeypatch.setattr(settings, "oracle_dsn", "")
    monkeypatch.setattr(settings, "oracle_deepsec_enabled", False)
    monkeypatch.setattr(settings, "oracle_deepsec_data_user", "")
    monkeypatch.setattr(settings, "oracle_deepsec_data_user_password", "")
    monkeypatch.setattr(settings, "nl2sql_persistence_mode", "memory")
    reset_security_service()

    async def exercise() -> None:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            invalid = await client.patch(
                "/api/security/deepsec/config",
                json={"data_user_password": "short"},
            )
            assert invalid.status_code == 422
            assert settings.oracle_deepsec_enabled is False
            assert settings.oracle_deepsec_data_user_password == ""
            assert closed == []

            response = await client.patch(
                "/api/security/deepsec/config",
                json={"data_user_password": "DeepSecret!456"},
            )
            assert response.status_code == 200
            data = response.json()["data"]
            assert data["deepsec_enabled"] is True
            assert data["data_user"] == "NL2SQL_DEEPSEC_DATA_USER"
            assert data["has_data_user_password"] is True
            assert "DeepSecret!456" not in response.text

    try:
        asyncio.run(exercise())
    finally:
        reset_security_service()

    assert settings.oracle_deepsec_enabled is True
    assert settings.oracle_deepsec_data_user == "NL2SQL_DEEPSEC_DATA_USER"
    assert settings.oracle_deepsec_data_user_password == "DeepSecret!456"
    assert closed == [True]
    env_text = env_file.read_text(encoding="utf-8")
    assert "ORACLE_DEEPSEC_ENABLED=true" in env_text
    assert "ORACLE_DEEPSEC_DATA_USER=NL2SQL_DEEPSEC_DATA_USER" in env_text
    assert "ORACLE_DEEPSEC_DATA_USER_PASSWORD=DeepSecret!456" in env_text
    assert "ORACLE_DEEPSEC_END_USER" not in env_text


def test_api_enforces_menu_permissions(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_security_threadpools(monkeypatch)
    settings = get_settings()
    monkeypatch.setattr(settings, "app_auth_enabled", True)
    monkeypatch.setattr(settings, "app_auth_cookie_secure", False)
    monkeypatch.setattr(settings, "oracle_user", "DBADMIN")
    monkeypatch.setattr(settings, "oracle_password", "DbAdminPass!123")
    monkeypatch.setattr(settings, "app_admin_username", "system_admin")
    monkeypatch.setattr(settings, "app_admin_password", "AppAdminPass123")
    monkeypatch.setattr(settings, "nl2sql_persistence_mode", "memory")
    reset_security_service()
    from app.security.service import get_security_service

    service = get_security_service()
    assert isinstance(service.store, InMemorySecurityStore)
    assert service.store.bootstrap(
        login_name="ADMIN",
        display_name="ADMIN（システム管理者）",
        password_hash=hash_password("BootstrapPass!123"),
    )

    async def exercise() -> None:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            await client.post(
                "/api/auth/login",
                json={"login_name": "ADMIN", "password": "BootstrapPass!123"},
            )
            csrf = client.cookies.get("nl2sql_csrf")
            assert csrf
            forced = await client.get("/api/security/users")
            assert forced.status_code == 403
            changed = await client.post(
                "/api/auth/password/change",
                headers={"X-CSRF-Token": csrf},
                json={
                    "current_password": "BootstrapPass!123",
                    "new_password": "IndependentPass!456",
                },
            )
            assert changed.status_code == 200
            await client.post(
                "/api/auth/login",
                json={"login_name": "ADMIN", "password": "IndependentPass!456"},
            )
            csrf = client.cookies.get("nl2sql_csrf")
            assert csrf
            role_response = await client.post(
                "/api/security/roles",
                headers={"X-CSRF-Token": csrf},
                json={
                    "role_code": "HISTORY_VIEWER",
                    "display_name": "履歴閲覧",
                    "permissions": ["menu.history"],
                    "data_entitlements": [],
                },
            )
            assert role_response.status_code == 200
            role_id = role_response.json()["data"]["role_id"]
            user_response = await client.post(
                "/api/security/users",
                headers={"X-CSRF-Token": csrf},
                json={
                    "login_name": "viewer.user",
                    "display_name": "履歴閲覧ユーザー",
                    "temporary_password": "ViewerStart!123",
                    "role_ids": [role_id],
                },
            )
            assert user_response.status_code == 200
            await client.post("/api/auth/logout", headers={"X-CSRF-Token": csrf})

            await client.post(
                "/api/auth/login",
                json={"login_name": "viewer.user", "password": "ViewerStart!123"},
            )
            csrf = client.cookies.get("nl2sql_csrf")
            assert csrf
            await client.post(
                "/api/auth/password/change",
                headers={"X-CSRF-Token": csrf},
                json={
                    "current_password": "ViewerStart!123",
                    "new_password": "ViewerActive!456",
                },
            )
            await client.post(
                "/api/auth/login",
                json={"login_name": "viewer.user", "password": "ViewerActive!456"},
            )
            csrf = client.cookies.get("nl2sql_csrf")
            assert csrf
            assert (await client.get("/api/nl2sql/history")).status_code == 200
            denied_execute = await client.post(
                "/api/nl2sql/preview",
                headers={"X-CSRF-Token": csrf},
                json={"question": "社員一覧"},
            )
            assert denied_execute.status_code == 403
            assert (await client.get("/api/security/users")).status_code == 403

    try:
        asyncio.run(exercise())
    finally:
        reset_security_service()


def test_history_api_scopes_items_to_actor_except_system_admin(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = get_settings()
    monkeypatch.setattr(settings, "app_auth_enabled", True)
    monkeypatch.setattr(settings, "app_auth_cookie_secure", False)
    monkeypatch.setattr(settings, "oracle_user", "DBADMIN")
    monkeypatch.setattr(settings, "oracle_password", "DbAdminPass!123")
    monkeypatch.setattr(settings, "app_admin_username", "system_admin")
    monkeypatch.setattr(settings, "app_admin_password", "AppAdminPass123")
    monkeypatch.setattr(settings, "nl2sql_persistence_mode", "memory")
    _patch_security_threadpools(monkeypatch)
    reset_security_service()
    from app.security.service import get_security_service

    service = get_security_service()
    assert isinstance(service.store, InMemorySecurityStore)
    assert service.store.bootstrap(
        login_name="ADMIN",
        display_name="ADMIN（システム管理者）",
        password_hash=hash_password("BootstrapPass!123"),
    )

    history_service = Nl2SqlService(store=MemoryNl2SqlStore())
    monkeypatch.setattr(nl2sql_router, "nl2sql_service", history_service)

    async def exercise() -> None:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            login = await client.post(
                "/api/auth/login",
                json={"login_name": "ADMIN", "password": "BootstrapPass!123"},
            )
            assert login.status_code == 200
            csrf = client.cookies.get("nl2sql_csrf")
            assert csrf
            changed = await client.post(
                "/api/auth/password/change",
                headers={"X-CSRF-Token": csrf},
                json={
                    "current_password": "BootstrapPass!123",
                    "new_password": "IndependentPass!456",
                },
            )
            assert changed.status_code == 200
            admin_login = await client.post(
                "/api/auth/login",
                json={"login_name": "ADMIN", "password": "IndependentPass!456"},
            )
            assert admin_login.status_code == 200
            admin_user_id = admin_login.json()["data"]["user_id"]
            csrf = client.cookies.get("nl2sql_csrf")
            assert csrf
            role_response = await client.post(
                "/api/security/roles",
                headers={"X-CSRF-Token": csrf},
                json={
                    "role_code": "HISTORY_OWNER_VIEWER",
                    "display_name": "履歴閲覧",
                    "permissions": ["menu.history"],
                    "data_entitlements": [],
                },
            )
            assert role_response.status_code == 200
            role_id = role_response.json()["data"]["role_id"]
            user_response = await client.post(
                "/api/security/users",
                headers={"X-CSRF-Token": csrf},
                json={
                    "login_name": "owner.viewer",
                    "display_name": "履歴所有者",
                    "temporary_password": "ViewerStart!123",
                    "role_ids": [role_id],
                },
            )
            assert user_response.status_code == 200
            viewer_user_id = user_response.json()["data"]["user"]["user_id"]

            history_service._history = [  # noqa: SLF001 - API scope regression fixture
                HistoryItem(
                    id="legacy-history",
                    question="所有者不明",
                    engine=Nl2SqlEngine.SELECT_AI,
                    generated_sql="SELECT 1 FROM DUAL",
                    created_at="2026-08-17T00:00:00+00:00",
                ),
                HistoryItem(
                    id="admin-history",
                    question="管理者履歴",
                    engine=Nl2SqlEngine.SELECT_AI,
                    generated_sql="SELECT 1 FROM DUAL",
                    created_at="2026-08-17T00:01:00+00:00",
                    actor_user_id=admin_user_id,
                ),
                HistoryItem(
                    id="viewer-history",
                    question="自分の履歴",
                    engine=Nl2SqlEngine.SELECT_AI,
                    generated_sql="SELECT 1 FROM DUAL",
                    created_at="2026-08-17T00:02:00+00:00",
                    actor_user_id=viewer_user_id,
                ),
                HistoryItem(
                    id="other-history",
                    question="他人の履歴",
                    engine=Nl2SqlEngine.SELECT_AI,
                    generated_sql="SELECT 1 FROM DUAL",
                    created_at="2026-08-17T00:03:00+00:00",
                    actor_user_id="other-user",
                ),
            ]

            admin_history = await client.get("/api/nl2sql/history")
            assert admin_history.status_code == 200
            assert [item["id"] for item in admin_history.json()["data"]["items"]] == [
                "other-history",
                "viewer-history",
                "admin-history",
                "legacy-history",
            ]
            await client.post("/api/auth/logout", headers={"X-CSRF-Token": csrf})

            await client.post(
                "/api/auth/login",
                json={"login_name": "owner.viewer", "password": "ViewerStart!123"},
            )
            csrf = client.cookies.get("nl2sql_csrf")
            assert csrf
            viewer_changed = await client.post(
                "/api/auth/password/change",
                headers={"X-CSRF-Token": csrf},
                json={
                    "current_password": "ViewerStart!123",
                    "new_password": "ReadOnlyPass!456",
                },
            )
            assert viewer_changed.status_code == 200
            viewer_login = await client.post(
                "/api/auth/login",
                json={"login_name": "owner.viewer", "password": "ReadOnlyPass!456"},
            )
            assert viewer_login.status_code == 200
            scoped_history = await client.get("/api/nl2sql/history")
            assert scoped_history.status_code == 200
            assert [item["id"] for item in scoped_history.json()["data"]["items"]] == [
                "viewer-history"
            ]

    try:
        asyncio.run(exercise())
    finally:
        reset_security_service()
