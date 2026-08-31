"""Application auth/RBAC の回帰テスト。"""

from __future__ import annotations

import asyncio
import re
from collections.abc import AsyncGenerator, Callable, Iterator, Mapping
from contextlib import contextmanager
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import cast

import httpx
import pytest
from fastapi import HTTPException, Request, Response

from app.cli.app_security_migrate import main as security_migrate_main
from app.cli.app_security_migrate import split_ddl
from app.features.nl2sql import router as nl2sql_router
from app.features.nl2sql.models import HistoryItem, Nl2SqlEngine, Nl2SqlProfile
from app.features.nl2sql.service import Nl2SqlService
from app.features.nl2sql.store import MemoryNl2SqlStore
from app.main import app
from app.security import dependencies as security_dependencies
from app.security.dependencies import authorize_api_request, local_debug_principal
from app.security.domain import (
    SYSTEM_ADMIN_ROLE_ID,
    DataEntitlementRecord,
    DataEntitlementScopeFilter,
    Principal,
    RoleRecord,
    UserRecord,
    scope_filters_scope_code,
)
from app.security.passwords import PasswordPolicyError, hash_password, validate_password
from app.security.permissions import (
    ALL_PERMISSION_CODES,
    FEEDBACK_MANAGE_PERMISSION,
    FEEDBACK_WRITE_PERMISSION,
    LEARNING_MATERIAL_MANAGE_PERMISSION,
    PERMISSION_CATALOG,
    PERSISTENCE_RECOVER_PERMISSION,
    PROFILE_MANAGE_PERMISSION,
    PROFILE_READ_PERMISSION,
    QUERY_GENERATE_PERMISSION,
    SAMPLE_DATA_MANAGE_PERMISSION,
    SCHEMA_READ_PERMISSION,
    SCHEMA_REFRESH_PERMISSION,
    SELECT_AI_ASSETS_MANAGE_PERMISSION,
    SELECT_AI_ASSETS_READ_PERMISSION,
    SELECT_AI_ASSETS_REFRESH_PERMISSION,
    SQL_EXECUTE_PERMISSION,
    SYSTEM_STATUS_READ_PERMISSION,
    UNCLASSIFIED_PERMISSION,
    permission_for_route,
)
from app.security.router import (
    change_password,
    logout,
    me,
)
from app.security.schemas import (
    CurrentUserData,
    DataEntitlementInput,
    PasswordChangeRequest,
    RoleData,
    UserCreateRequest,
    UserData,
)
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
    SecurityMigrationRequired,
    SecurityStore,
)
from app.settings import Settings, get_settings


def _settings() -> Settings:
    return Settings.model_construct(
        oracle_user="DBADMIN",
        oracle_password="DbAdminPass!123",
        app_admin_login_user_id="system_admin",
        app_admin_login_user_password="AppAdminPass123",
        oracle_dsn="test",
        nl2sql_persistence_mode="memory",
        app_auth_enabled=True,
        app_auth_failed_login_limit=5,
        app_auth_lockout_minutes=15,
        app_auth_idle_timeout_minutes=60,
        app_auth_absolute_timeout_hours=12,
        app_auth_password_min_length=12,
        app_auth_password_max_length=128,
    )


def _patch_app_admin_env(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    *,
    login_user_id: str = "system_admin",
    password: str = "AppAdminPass123",
) -> Path:
    env_file = tmp_path / ".env"
    env_file.write_text(
        f"APP_ADMIN_LOGIN_USER_ID={login_user_id}\n"
        f"APP_ADMIN_LOGIN_USER_PASSWORD={password}\n"
        "APP_AUTH_ENABLED=true\n",
        encoding="utf-8",
    )
    monkeypatch.setattr("app.security.service._BACKEND_ENV_FILE", env_file)
    return env_file


def _service() -> SecurityService:
    store = InMemorySecurityStore()
    assert (
        store.bootstrap(
            login_user_id="admin",
            display_name="ADMIN（システム管理者）",
            password_hash=hash_password("BootstrapPass!123"),
        )
        is True
    )
    assert (
        store.bootstrap(
            login_user_id="admin",
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


def _configure_memory_api_auth(monkeypatch: pytest.MonkeyPatch) -> SecurityService:
    _patch_security_threadpools(monkeypatch)
    settings = get_settings()
    monkeypatch.setattr(settings, "app_auth_enabled", True)
    monkeypatch.setattr(settings, "app_auth_cookie_secure", False)
    monkeypatch.setattr(settings, "oracle_user", "DBADMIN")
    monkeypatch.setattr(settings, "oracle_password", "DbAdminPass!123")
    monkeypatch.setattr(settings, "app_admin_login_user_id", "system_admin")
    monkeypatch.setattr(settings, "app_admin_login_user_password", "AppAdminPass123")
    monkeypatch.setattr(settings, "nl2sql_persistence_mode", "memory")
    reset_security_service()
    from app.security.service import get_security_service

    service = get_security_service()
    assert isinstance(service.store, InMemorySecurityStore)
    assert service.store.bootstrap(
        login_user_id="ADMIN",
        display_name="ADMIN（システム管理者）",
        password_hash=hash_password("BootstrapPass!123"),
    )
    return service


async def _login_api(
    client: httpx.AsyncClient,
    login_user_id: str,
    password: str,
) -> tuple[dict[str, object], str]:
    response = await client.post(
        "/api/auth/login",
        json={"login_user_id": login_user_id, "password": password},
    )
    assert response.status_code == 200
    csrf = client.cookies.get("nl2sql_csrf")
    assert csrf
    return cast(dict[str, object], response.json()["data"]), csrf


def _create_active_user(
    service: SecurityService,
    actor: Principal,
    *,
    login_user_id: str,
    display_name: str,
    role_ids: list[str],
    password: str,
) -> UserRecord:
    user, _ = service.create_user(
        login_user_id=login_user_id,
        display_name=display_name,
        role_ids=role_ids,
        temporary_password=password,
        actor=actor,
    )
    service.store.set_password(user.user_uuid, hash_password(password), force_change=False)
    active = service.store.get_user(user.user_uuid)
    assert active is not None
    return active


class _NoAuthTableStore:
    def __init__(self) -> None:
        self.calls: list[str] = []

    def __getattr__(self, name: str) -> object:
        self.calls.append(name)
        raise AssertionError(f"auth table store must not be used for env admin: {name}")


class _MissingSecuritySchemaStore:
    def get_user_by_login_user_id(self, _normalized_login_user_id: str) -> UserRecord | None:
        raise RuntimeError('ORA-00942: table or view "ADMIN"."NL2SQL_APP_USERS" does not exist')

    def list_users(self) -> list[UserRecord]:
        raise RuntimeError('ORA-00942: table or view "ADMIN"."NL2SQL_APP_USERS" does not exist')


class _MissingRoleProfilesStore(InMemorySecurityStore):
    def list_roles(self, *, include_archived: bool = False) -> list[RoleRecord]:
        _ = include_archived
        raise RuntimeError(
            'ORA-00942: table or view "ADMIN"."NL2SQL_APP_ROLE_PROFILES" does not exist'
        )

    def get_role(self, role_id: str) -> RoleRecord | None:
        _ = role_id
        raise RuntimeError(
            'ORA-00942: table or view "ADMIN"."NL2SQL_APP_ROLE_PROFILES" does not exist'
        )


class _RecordingCursor:
    def __init__(self, fetchone_rows: list[tuple[int]] | None = None) -> None:
        self.executed: list[tuple[str, dict[str, object]]] = []
        self._fetchone_rows = list(fetchone_rows or [])

    def execute(self, sql: str, params: Mapping[str, object] | None = None) -> None:
        self.executed.append((sql, dict(params or {})))

    def fetchone(self) -> tuple[int]:
        return self._fetchone_rows.pop(0) if self._fetchone_rows else (0,)


class _BareMissingTableCursor:
    def __enter__(self) -> _BareMissingTableCursor:
        return self

    def __exit__(self, *_args: object) -> None:
        return None

    def execute(self, *_args: object, **_kwargs: object) -> None:
        raise RuntimeError("ORA-00942: table or view does not exist")


class _BareMissingTableConnection:
    def cursor(self) -> _BareMissingTableCursor:
        return _BareMissingTableCursor()


class _BareMissingTableAdapter:
    @contextmanager
    def connection(self) -> Iterator[_BareMissingTableConnection]:
        yield _BareMissingTableConnection()


def test_oracle_store_maps_bare_ora_00942_to_operation_security_object(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = OracleSecurityStore(_settings())
    monkeypatch.setattr(store, "_adapter", _BareMissingTableAdapter())
    operations = [
        (store.list_users, "NL2SQL_APP_USERS"),
        (store.list_roles, "NL2SQL_APP_ROLES"),
        (lambda: store.get_session_by_token_hash("missing"), "NL2SQL_AUTH_SESSIONS"),
        (store.get_deepsec_states, "NL2SQL_DEEPSEC_MIGRATIONS"),
    ]

    for operation, expected_object in operations:
        with pytest.raises(SecurityMigrationRequired) as exc_info:
            operation()
        assert exc_info.value.object_name == expected_object


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
            "user_uuid": "00000000-0000-0000-0000-000000000000",
            "login_user_id": "local-debug",
            "display_name": "ローカル DEBUG 管理者",
            "status": "ACTIVE",
            "force_password_change": False,
            "role_codes": ["SYSTEM_ADMIN"],
            "is_system_admin": True,
            "permissions": sorted(ALL_PERMISSION_CODES),
            "data_entitlements": [],
            "allowed_profile_ids": [],
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
    assert service.authenticate_session(token).user_uuid == principal.user_uuid
    service.verify_csrf(principal, csrf, csrf)
    with pytest.raises(SecurityApiError, match="安全性"):
        service.verify_csrf(principal, csrf, "different")

    service.change_password(principal, "BootstrapPass!123", "IndependentPass!456")
    with pytest.raises(SecurityApiError, match="ログイン"):
        service.authenticate_session(token)
    changed, _, _ = service.login("ADMIN", "IndependentPass!456")
    assert changed.force_password_change is False


def test_current_user_data_marks_non_reserved_login_with_system_admin_role() -> None:
    service = _service()
    principal, _, _ = _login(service)

    current = CurrentUserData.from_principal(principal)

    assert current.login_user_id == "admin"
    assert current.is_system_admin is True
    assert current.permissions == sorted(principal.permissions)
    assert principal.has_permission("menu.security_users") is True


def test_login_session_uses_sixty_minute_default_idle_timeout(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    now = datetime(2026, 8, 20, 9, 0, tzinfo=UTC)
    monkeypatch.setattr("app.security.service._now", lambda: now)
    service = _service()

    principal, token, _csrf = _login(service)

    store = cast(InMemorySecurityStore, service.store)
    session = next(iter(store.sessions.values()))
    assert principal.session_id == session.session_id
    assert session.idle_expires_at == now + timedelta(minutes=60)
    assert session.absolute_expires_at == now + timedelta(hours=12)
    assert service.authenticate_session(token).user_uuid == principal.user_uuid


def test_session_activity_refreshes_idle_timeout_without_exceeding_absolute(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    login_at = datetime(2026, 8, 20, 9, 0, tzinfo=UTC)
    current_time = {"value": login_at}
    monkeypatch.setattr("app.security.service._now", lambda: current_time["value"])
    service = _service()
    _principal, token, _csrf = _login(service)
    store = cast(InMemorySecurityStore, service.store)
    session_id = next(iter(store.sessions))
    store.sessions[session_id].idle_expires_at = login_at + timedelta(hours=12)

    current_time["value"] = login_at + timedelta(hours=11, minutes=30)
    authenticated = service.authenticate_session(token)

    refreshed = store.sessions[session_id]
    assert authenticated.session_id == session_id
    assert refreshed.last_seen_at == current_time["value"]
    assert refreshed.idle_expires_at == refreshed.absolute_expires_at
    assert refreshed.absolute_expires_at == login_at + timedelta(hours=12)


@pytest.mark.parametrize("expired_field", ["idle_expires_at", "absolute_expires_at"])
def test_expired_session_is_revoked(
    expired_field: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    now = datetime(2026, 8, 20, 9, 0, tzinfo=UTC)
    monkeypatch.setattr("app.security.service._now", lambda: now)
    service = _service()
    _principal, token, _csrf = _login(service)
    store = cast(InMemorySecurityStore, service.store)
    session_id = next(iter(store.sessions))
    setattr(store.sessions[session_id], expired_field, now - timedelta(seconds=1))

    with pytest.raises(SecurityApiError, match="セッションの有効期限"):
        service.authenticate_session(token)

    assert store.sessions[session_id].revoked_at is not None


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
    assert principal.login_user_id == "system_admin"
    assert token.startswith("nl2sql-system-admin-v1.")
    assert csrf
    authenticated = service.authenticate_session(token)
    assert authenticated.is_system_admin
    assert authenticated.user_uuid == principal.user_uuid
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


@pytest.mark.parametrize("login_user_id", ["System_Admin", "SYSTEM_ADMIN"])
def test_system_admin_case_variants_do_not_fall_back_to_db_users(
    login_user_id: str,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _patch_app_admin_env(monkeypatch, tmp_path)
    store = _NoAuthTableStore()
    service = SecurityService(cast(SecurityStore, store), _settings())

    with pytest.raises(LoginFailed):
        service.login(login_user_id, "AppAdminPass123")
    assert store.calls == []


def test_configured_system_admin_requires_fixed_login_user_id(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _patch_app_admin_env(monkeypatch, tmp_path, login_user_id="app-admin")
    service = SecurityService(cast(SecurityStore, _NoAuthTableStore()), _settings())

    with pytest.raises(SecurityApiError) as error:
        service.login("system_admin", "AppAdminPass123")
    assert error.value.status_code == 503
    assert "system_admin" in error.value.public_message


def test_configured_system_admin_accepts_legacy_app_admin_password_key(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """旧キー APP_ADMIN_PASSWORD だけの既存 .env でもログインできる(後方互換)。"""
    env_file = tmp_path / ".env"
    env_file.write_text(
        "APP_ADMIN_LOGIN_USER_ID=system_admin\n"
        "APP_ADMIN_PASSWORD=AppAdminPass123\n"
        "APP_AUTH_ENABLED=true\n",
        encoding="utf-8",
    )
    monkeypatch.setattr("app.security.service._BACKEND_ENV_FILE", env_file)
    service = SecurityService(cast(SecurityStore, _NoAuthTableStore()), _settings())

    principal, _token, _csrf = service.login("system_admin", "AppAdminPass123")

    assert principal.login_user_id == "system_admin"


def test_configured_system_admin_password_change_migrates_legacy_key(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """password 変更時に旧キー行は除去され、新キーだけが残る。"""
    env_file = tmp_path / ".env"
    env_file.write_text(
        "APP_ADMIN_LOGIN_USER_ID=system_admin\n"
        "APP_ADMIN_PASSWORD=AppAdminPass123\n"
        "APP_AUTH_ENABLED=true\n",
        encoding="utf-8",
    )
    monkeypatch.setattr("app.security.service._BACKEND_ENV_FILE", env_file)
    service = SecurityService(cast(SecurityStore, _NoAuthTableStore()), _settings())
    principal, _token, _csrf = service.login("system_admin", "AppAdminPass123")

    service.change_password(principal, "AppAdminPass123", "UpdatedPass123A")

    env_text = env_file.read_text(encoding="utf-8")
    assert "APP_ADMIN_LOGIN_USER_PASSWORD=UpdatedPass123A" in env_text
    assert "APP_ADMIN_PASSWORD=" not in env_text
    relogged, _, _ = service.login("system_admin", "UpdatedPass123A")
    assert relogged.login_user_id == "system_admin"


def test_configured_system_admin_password_change_updates_env_without_auth_tables(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    env_file = _patch_app_admin_env(monkeypatch, tmp_path)
    store = _NoAuthTableStore()
    service = SecurityService(cast(SecurityStore, store), _settings())
    principal, token, _csrf = service.login("system_admin", "AppAdminPass123")

    changed = service.change_password(principal, "AppAdminPass123", "UpdatedPass123A")

    assert changed.user_uuid == principal.user_uuid
    env_text = env_file.read_text(encoding="utf-8")
    assert "APP_ADMIN_LOGIN_USER_ID=system_admin" in env_text
    assert "APP_ADMIN_LOGIN_USER_PASSWORD=UpdatedPass123A" in env_text
    assert env_text.index("APP_ADMIN_LOGIN_USER_ID=system_admin") < env_text.index(
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
    assert error.value.status_code == 409
    assert error.value.code == "SECURITY_SCHEMA_MIGRATION_REQUIRED"
    assert "app_security_migrate" in error.value.public_message


def test_table_user_login_reports_migration_required_when_auth_table_is_missing() -> None:
    service = SecurityService(cast(SecurityStore, _MissingSecuritySchemaStore()), _settings())

    with pytest.raises(SecurityApiError) as error:
        service.login("app.user", "AppUserPass!123")
    assert error.value.status_code == 409
    assert error.value.code == "SECURITY_SCHEMA_MIGRATION_REQUIRED"
    assert "app_security_migrate" in error.value.public_message


def test_list_users_reports_security_migration_required() -> None:
    service = SecurityService(cast(SecurityStore, _MissingSecuritySchemaStore()), _settings())

    with pytest.raises(SecurityApiError) as error:
        service.list_users()

    assert error.value.status_code == 409
    assert error.value.code == "SECURITY_SCHEMA_MIGRATION_REQUIRED"
    assert "NL2SQL_APP_USERS" not in error.value.public_message


def test_missing_role_profiles_table_reports_security_migration_required() -> None:
    service = SecurityService(cast(SecurityStore, _MissingRoleProfilesStore()), _settings())

    with pytest.raises(SecurityApiError) as list_error:
        service.list_roles(include_archived=True)
    with pytest.raises(SecurityApiError) as get_error:
        service.get_role("role-id")

    for error in (list_error.value, get_error.value):
        assert error.status_code == 409
        assert "app_security_migrate" in error.public_message
        assert "--skip-bootstrap" in error.public_message


def test_password_policy_requires_all_character_classes_without_expiry() -> None:
    with pytest.raises(PasswordPolicyError):
        validate_password("onlylowercase", login_user_id="user", min_length=12, max_length=128)
    with pytest.raises(PasswordPolicyError, match="推測"):
        validate_password("Password123!", login_user_id="user", min_length=12, max_length=128)
    validate_password("StrongPass!234", login_user_id="user", min_length=12, max_length=128)


def test_password_policy_ignores_login_user_id_similarity_for_short_names() -> None:
    validate_password("AaaStrong!234", login_user_id="a", min_length=12, max_length=128)
    validate_password("Number1!2345", login_user_id="1", min_length=12, max_length=128)
    with pytest.raises(PasswordPolicyError, match="推測"):
        validate_password("AbcStrong!234", login_user_id="abc", min_length=12, max_length=128)


def test_security_migration_splitter_never_executes_comment_only_buffers() -> None:
    assert split_ddl("-- header\nCREATE TABLE EXAMPLE (ID NUMBER);\n-- trailing") == [
        "CREATE TABLE EXAMPLE (ID NUMBER)"
    ]


def test_deepsec_entitlement_migration_uses_compressed_table_safe_column_type() -> None:
    migration_path = (
        Path(__file__).resolve().parents[1]
        / "migrations"
        / "010_deepsec_real_data_entitlements.sql"
    )
    sql = migration_path.read_text(encoding="utf-8")
    scope_filters_migration = (
        Path(__file__).resolve().parents[1] / "migrations" / "012_deepsec_scope_filters.sql"
    ).read_text(encoding="utf-8")

    assert "COLUMN_NAMES VARCHAR2(4000)" in sql
    assert "COLUMN_NAMES CLOB" not in sql
    assert "TARGET_TYPE VARCHAR2(32)" in sql
    assert "SCOPE_FILTERS VARCHAR2(4000)" in scope_filters_migration
    assert "SCOPE_FILTERS CLOB" not in scope_filters_migration
    assert "'FILTERS'" in scope_filters_migration

    base_migration = (
        Path(__file__).resolve().parents[1] / "migrations" / "004_app_security_rbac.sql"
    )
    base_sql = base_migration.read_text(encoding="utf-8")
    assert "TARGET_TYPE VARCHAR2(32)" in base_sql
    assert "SCOPE_FILTERS VARCHAR2(4000)" in base_sql
    assert "'FILTERS'" in base_sql


def test_role_profile_access_migration_backfills_active_profile_read_roles() -> None:
    migration_path = (
        Path(__file__).resolve().parents[1] / "migrations" / "016_app_role_profiles.sql"
    )
    sql = migration_path.read_text(encoding="utf-8")

    assert "CREATE TABLE NL2SQL_APP_ROLE_PROFILES" in sql
    assert "PRIMARY KEY (ROLE_ID, PROFILE_ID)" in sql
    assert "NL2SQL_APP_ROLE_PERMISSIONS" in sql
    assert "nl2sql.profiles.read" in sql
    assert "r.ARCHIVED = 0" in sql
    assert "p.ARCHIVED = 0" in sql
    assert "r.ROLE_CODE <> 'SYSTEM_ADMIN'" in sql


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
        login_user_id="stale.user",
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
            resource_code="HR.EMPLOYEES",
            scope_code="SALES",
            capability="ARBITRARY_SQL",
        )


def test_data_entitlement_input_generates_scope_filter_code() -> None:
    record = DataEntitlementInput(
        resource_code="HR.ORDERS",
        capability="SELECT",
        target_owner="HR",
        target_object="ORDERS",
        column_names=["REGION_CODE"],
        scope_mode="FILTERS",
        scope_filters=[
            {
                "column_name": "REGION_CODE",
                "operator": "EQ",
                "value_type": "TEXT",
                "value": "SALES",
            }
        ],
    ).to_record("role-sales")

    assert record.scope_code == scope_filters_scope_code(record.scope_filters)
    assert record.scope_code.startswith("FILTERS:")
    assert record.scope_filters[0].column_name == "REGION_CODE"


def test_data_entitlement_input_accepts_number_app_user_id_scope_filter() -> None:
    record = DataEntitlementInput(
        resource_code="HR.ORDERS",
        capability="SELECT",
        target_owner="HR",
        target_object="ORDERS",
        column_names=["APP_OWNER_USER_ID"],
        scope_mode="FILTERS",
        scope_filters=[
            {
                "column_name": "APP_OWNER_USER_ID",
                "operator": "EQ",
                "value_type": "NUMBER",
                "value_source": "LOGIN_USER_ID",
                "value": "123",
            }
        ],
    ).to_record("role-sales")

    assert record.scope_filters[0].value_source == "LOGIN_USER_ID"
    assert record.scope_filters[0].value == ""
    assert record.scope_filters[0].value_type == "NUMBER"


@pytest.mark.parametrize("value", ["1.5", "-1", "0", ""])
def test_data_entitlement_input_rejects_number_eq_non_positive_integer(
    value: str,
) -> None:
    with pytest.raises(ValueError, match="正整数"):
        DataEntitlementInput(
            resource_code="HR.ORDERS",
            capability="SELECT",
            target_owner="HR",
            target_object="ORDERS",
            column_names=["APP_OWNER_USER_ID"],
            scope_mode="FILTERS",
            scope_filters=[
                {
                    "column_name": "APP_OWNER_USER_ID",
                    "operator": "EQ",
                    "value_type": "NUMBER",
                    "value": value,
                }
            ],
        )


def test_deepsec_entitlement_update_preserves_role_metadata_and_permissions() -> None:
    service = _service()
    actor, _, _ = _login(service)
    role = service.create_role(
        role_code="QUERY_VIEWER",
        display_name="検索閲覧",
        description="メニュー権限は DeepSec 画面から変更しない",
        permissions={"menu.query"},
        entitlements=[("HR.EMPLOYEES", "SALES", "SELECT")],
        actor=actor,
    )

    updated = service.update_role_data_entitlements(
        role.role_id,
        expected_version=role.version,
        entitlements=[
            ("HR.EMPLOYEES", "SALES", "SELECT"),
            ("HR.EMPLOYEES", "HR", "SELECT"),
        ],
        actor=actor,
    )

    assert updated.version == role.version + 1
    assert updated.role_code == role.role_code
    assert updated.display_name == role.display_name
    assert updated.description == role.description
    assert updated.permissions == role.permissions
    assert {(item.scope_code, item.capability) for item in updated.entitlements} == {
        ("SALES", "SELECT"),
        ("HR", "SELECT"),
    }
    with pytest.raises(SecurityApiError, match="更新"):
        service.update_role_data_entitlements(
            role.role_id,
            expected_version=role.version,
            entitlements=[],
            actor=actor,
        )
    with pytest.raises(SecurityApiError, match="SYSTEM_ADMIN"):
        service.update_role_data_entitlements(
            SYSTEM_ADMIN_ROLE_ID,
            expected_version=1,
            entitlements=[],
            actor=actor,
        )


def test_deepsec_entitlement_update_preserves_applied_state_when_policy_unchanged() -> None:
    service = _service()
    store = cast(InMemorySecurityStore, service.store)
    actor, _, _ = _login(service)
    role = service.create_role(
        role_code="QUERY_VIEWER",
        display_name="検索閲覧",
        description="",
        permissions={"menu.query"},
        entitlements=[],
        actor=actor,
    )
    applied_at = datetime(2026, 8, 23, 1, 0, tzinfo=UTC)
    entitlement = DataEntitlementRecord(
        entitlement_id="entitlement-1",
        role_id=role.role_id,
        resource_code="HR.EMPLOYEES",
        scope_code="*",
        capability="SELECT",
        target_owner="HR",
        target_object="EMPLOYEES",
        target_type="TABLE",
        column_names=["EMPLOYEE_ID", "DISPLAY_NAME"],
        scope_mode="ALL",
        data_grant_name="NL2SQL_DG_APPLIED",
        sql_checksum="abc123",
        apply_status="APPLIED",
        applied_at=applied_at,
    )
    store.roles[role.role_id] = RoleRecord(
        role_id=role.role_id,
        role_code=role.role_code,
        display_name=role.display_name,
        description=role.description,
        is_built_in=role.is_built_in,
        archived=role.archived,
        version=role.version,
        permissions=set(role.permissions),
        entitlements=[entitlement],
    )

    updated = service.update_role_data_entitlements(
        role.role_id,
        expected_version=role.version,
        entitlements=[replace(entitlement)],
        actor=actor,
    )

    stored = updated.entitlements[0]
    assert stored.apply_status == "APPLIED"
    assert stored.apply_error_message == ""
    assert stored.data_grant_name == "NL2SQL_DG_APPLIED"
    assert stored.sql_checksum == "abc123"
    assert stored.applied_at == applied_at


def test_deepsec_entitlement_update_resets_apply_state_when_policy_changes() -> None:
    service = _service()
    store = cast(InMemorySecurityStore, service.store)
    actor, _, _ = _login(service)
    role = service.create_role(
        role_code="QUERY_VIEWER",
        display_name="検索閲覧",
        description="",
        permissions={"menu.query"},
        entitlements=[],
        actor=actor,
    )
    applied_at = datetime(2026, 8, 23, 1, 0, tzinfo=UTC)
    entitlement = DataEntitlementRecord(
        entitlement_id="entitlement-1",
        role_id=role.role_id,
        resource_code="HR.EMPLOYEES",
        scope_code="*",
        capability="SELECT",
        target_owner="HR",
        target_object="EMPLOYEES",
        target_type="TABLE",
        column_names=["EMPLOYEE_ID", "DISPLAY_NAME"],
        scope_mode="ALL",
        data_grant_name="NL2SQL_DG_APPLIED",
        sql_checksum="abc123",
        apply_status="APPLIED",
        applied_at=applied_at,
    )
    store.roles[role.role_id] = RoleRecord(
        role_id=role.role_id,
        role_code=role.role_code,
        display_name=role.display_name,
        description=role.description,
        is_built_in=role.is_built_in,
        archived=role.archived,
        version=role.version,
        permissions=set(role.permissions),
        entitlements=[entitlement],
    )
    changed = replace(
        entitlement,
        column_names=["EMPLOYEE_ID"],
        apply_error_message="client supplied stale error",
    )

    updated = service.update_role_data_entitlements(
        role.role_id,
        expected_version=role.version,
        entitlements=[changed],
        actor=actor,
    )

    stored = updated.entitlements[0]
    assert stored.apply_status == "PENDING"
    assert stored.apply_error_message == ""
    assert stored.data_grant_name == "NL2SQL_DG_APPLIED"
    assert stored.sql_checksum == ""
    assert stored.applied_at is None


def test_deepsec_entitlement_update_resets_apply_state_when_scope_filters_change() -> None:
    service = _service()
    store = cast(InMemorySecurityStore, service.store)
    actor, _, _ = _login(service)
    role = service.create_role(
        role_code="QUERY_VIEWER",
        display_name="検索閲覧",
        description="",
        permissions={"menu.query"},
        entitlements=[],
        actor=actor,
    )
    filters = [
        DataEntitlementScopeFilter(
            column_name="REGION_CODE",
            operator="EQ",
            value_type="TEXT",
            value="SALES",
        )
    ]
    entitlement = DataEntitlementRecord(
        entitlement_id="entitlement-1",
        role_id=role.role_id,
        resource_code="HR.ORDERS",
        scope_code=scope_filters_scope_code(filters),
        capability="SELECT",
        target_owner="HR",
        target_object="ORDERS",
        target_type="TABLE",
        column_names=["REGION_CODE"],
        scope_mode="FILTERS",
        scope_filters=filters,
        data_grant_name="NL2SQL_DG_APPLIED",
        sql_checksum="abc123",
        apply_status="APPLIED",
        applied_at=datetime(2026, 8, 23, 1, 0, tzinfo=UTC),
    )
    store.roles[role.role_id] = RoleRecord(
        role_id=role.role_id,
        role_code=role.role_code,
        display_name=role.display_name,
        description=role.description,
        is_built_in=role.is_built_in,
        archived=role.archived,
        version=role.version,
        permissions=set(role.permissions),
        entitlements=[entitlement],
    )
    changed_filters = [replace(filters[0], value="HR")]

    updated = service.update_role_data_entitlements(
        role.role_id,
        expected_version=role.version,
        entitlements=[
            replace(
                entitlement,
                scope_code=scope_filters_scope_code(changed_filters),
                scope_filters=changed_filters,
            )
        ],
        actor=actor,
    )

    stored = updated.entitlements[0]
    assert stored.scope_code == scope_filters_scope_code(changed_filters)
    assert stored.apply_status == "PENDING"
    assert stored.sql_checksum == ""
    assert stored.applied_at is None


def test_deepsec_entitlement_update_rejects_client_supplied_applied_state_for_new_policy() -> None:
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
    forged = DataEntitlementRecord(
        entitlement_id="entitlement-forged",
        role_id=role.role_id,
        resource_code="HR.EMPLOYEES",
        scope_code="*",
        capability="SELECT",
        target_owner="HR",
        target_object="EMPLOYEES",
        target_type="TABLE",
        column_names=["EMPLOYEE_ID"],
        scope_mode="ALL",
        data_grant_name="NL2SQL_DG_FORGED",
        sql_checksum="forged",
        apply_status="APPLIED",
        applied_at=datetime(2026, 8, 23, 1, 0, tzinfo=UTC),
    )

    updated = service.update_role_data_entitlements(
        role.role_id,
        expected_version=role.version,
        entitlements=[forged],
        actor=actor,
    )

    stored = updated.entitlements[0]
    assert stored.apply_status == "PENDING"
    assert stored.sql_checksum == ""
    assert stored.applied_at is None


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
                resource_code="HR.EMPLOYEES",
                scope_code="SALES",
                capability="SELECT",
                target_owner="HR",
                target_object="EMPLOYEES",
                target_type="TABLE",
                column_names=["EMPLOYEE_ID", "DISPLAY_NAME"],
                scope_mode="COLUMN_EQUALS",
                scope_column="DEPARTMENT_CODE",
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
        "resource_code": "HR.EMPLOYEES",
        "scope_code": "SALES",
        "capability_code": "SELECT",
        "target_owner": "HR",
        "target_object": "EMPLOYEES",
        "target_type": "TABLE",
        "column_names": '["EMPLOYEE_ID","DISPLAY_NAME"]',
        "scope_mode": "COLUMN_EQUALS",
        "scope_column": "DEPARTMENT_CODE",
        "scope_filters": "[]",
        "data_grant_name": None,
        "sql_checksum": None,
        "apply_status": "PENDING",
        "apply_error_message": "-",
        "applied_at": None,
    }


def test_bootstrap_system_admin_has_no_fake_probe_entitlement() -> None:
    service = _service()
    service.bootstrap()

    system_admin = service.store.get_role(SYSTEM_ADMIN_ROLE_ID)

    assert system_admin is not None
    assert system_admin.entitlements == []


def test_multiple_roles_union_permissions_and_data_entitlements() -> None:
    service = _service()
    actor, _, _ = _login(service)
    role_a = service.create_role(
        role_code="QUERY_VIEWER",
        display_name="検索閲覧",
        description="",
        permissions={"search.view"},
        entitlements=[("HR.EMPLOYEES", "SALES", "SELECT")],
        allowed_profile_ids={"sales-profile"},
        actor=actor,
    )
    role_b = service.create_role(
        role_code="QUERY_RUNNER",
        display_name="検索実行",
        description="",
        permissions={"search.execute"},
        entitlements=[("FIN.INVOICES", "APPROVED", "SELECT")],
        allowed_profile_ids={"finance-profile"},
        actor=actor,
    )
    user, password = service.create_user(
        login_user_id="query.user",
        display_name="検索ユーザー",
        role_ids=[role_a.role_id, role_b.role_id],
        temporary_password="QueryUserPass!123",
        actor=actor,
    )
    assert password == "QueryUserPass!123"
    principal, _, _ = service.login(user.login_user_id, password)
    assert principal.permissions >= {
        "menu.query",
        "menu.direct_sql",
        "menu.sql_to_question",
        "menu.history",
    }
    assert {(item.scope_code, item.capability) for item in principal.data_entitlements} == {
        ("SALES", "SELECT"),
        ("APPROVED", "SELECT"),
    }
    assert principal.allowed_profile_ids == {"sales-profile", "finance-profile"}


def test_role_profile_access_is_exposed_on_role_and_current_user_data() -> None:
    service = _service()
    actor, _, _ = _login(service)
    role = service.create_role(
        role_code="PROFILE_SCOPED_QUERY",
        display_name="業務 profile 制限",
        description="",
        permissions={"menu.query"},
        entitlements=[],
        allowed_profile_ids={"finance", "sales"},
        actor=actor,
    )
    user, password = service.create_user(
        login_user_id="profile.scoped",
        display_name="業務 profile 制限ユーザー",
        role_ids=[role.role_id],
        temporary_password="ProfileScopedPass!123",
        actor=actor,
    )

    principal, _, _ = service.login(user.login_user_id, password)

    assert RoleData.from_record(role).allowed_profile_ids == ["finance", "sales"]
    assert CurrentUserData.from_principal(principal).allowed_profile_ids == [
        "finance",
        "sales",
    ]


def test_non_system_admin_cannot_change_role_profile_access() -> None:
    service = _service()
    actor, _, _ = _login(service)
    role_manager = service.create_role(
        role_code="ROLE_MANAGER",
        display_name="ロール管理",
        description="",
        permissions={"menu.security_roles"},
        entitlements=[],
        actor=actor,
    )
    target = service.create_role(
        role_code="PROFILE_TARGET",
        display_name="業務 profile 対象",
        description="",
        permissions={"menu.query"},
        entitlements=[],
        allowed_profile_ids={"default"},
        actor=actor,
    )
    manager_user, password = service.create_user(
        login_user_id="role.manager",
        display_name="ロール管理者",
        role_ids=[role_manager.role_id],
        temporary_password="RoleManagerPass!123",
        actor=actor,
    )
    manager, _, _ = service.login(manager_user.login_user_id, password)

    with pytest.raises(SecurityApiError) as clear_error:
        service.update_role(
            target.role_id,
            expected_version=target.version,
            display_name=target.display_name,
            description=target.description,
            permissions=set(target.permissions),
            entitlements=[],
            allowed_profile_ids=set(),
            actor=manager,
        )
    assert clear_error.value.status_code == 403

    unchanged = service.update_role(
        target.role_id,
        expected_version=target.version,
        display_name="業務 profile 対象更新",
        description=target.description,
        permissions=set(target.permissions),
        entitlements=[],
        allowed_profile_ids=None,
        actor=manager,
    )

    assert unchanged.allowed_profile_ids == {"default"}


def test_last_system_admin_cannot_be_disabled_or_unassigned() -> None:
    service = _service()
    actor, _, _ = _login(service)
    admin = service.store.get_user(actor.user_uuid)
    assert admin is not None
    assert admin.role_ids == [SYSTEM_ADMIN_ROLE_ID]
    with pytest.raises(SecurityApiError, match="最後のシステム管理者"):
        service.update_user(
            admin.user_uuid,
            expected_version=admin.version,
            display_name=admin.display_name,
            status="DISABLED",
            role_ids=admin.role_ids,
            actor=actor,
        )
    with pytest.raises(SecurityConflict, match="最後のシステム管理者"):
        service.store.update_user(
            admin.user_uuid,
            expected_version=admin.version,
            display_name=admin.display_name,
            status="DISABLED",
            role_ids=admin.role_ids,
        )


def test_bootstrap_user_is_marked_in_user_response() -> None:
    service = _service()
    actor, _, _ = _login(service)
    admin = service.store.get_user(actor.user_uuid)

    assert admin is not None
    assert admin.is_bootstrap_admin is True
    assert UserData.from_record(admin).is_bootstrap_admin is True


def test_disabled_user_can_be_deleted_with_sessions_and_login_id_reused() -> None:
    service = _service()
    actor, _, _ = _login(service)
    role = service.create_role(
        role_code="DELETE_USER_ROLE",
        display_name="削除対象ユーザー用",
        description="",
        permissions={"menu.query"},
        entitlements=[],
        actor=actor,
    )
    user, password = service.create_user(
        login_user_id="delete.target",
        display_name="削除対象",
        role_ids=[role.role_id],
        temporary_password="DeleteTargetPass!123",
        actor=actor,
    )
    service.login(user.login_user_id, password)
    disabled = service.update_user(
        user.user_uuid,
        expected_version=user.version,
        display_name=user.display_name,
        status="DISABLED",
        role_ids=user.role_ids,
        actor=actor,
    )

    deleted = service.delete_user(
        user.user_uuid,
        expected_version=disabled.version,
        actor=actor,
    )

    assert deleted.user_uuid == user.user_uuid
    assert service.store.get_user(user.user_uuid) is None
    assert isinstance(service.store, InMemorySecurityStore)
    assert all(session.user_uuid != user.user_uuid for session in service.store.sessions.values())
    recreated, _ = service.create_user(
        login_user_id="delete.target",
        display_name="再作成ユーザー",
        role_ids=[role.role_id],
        temporary_password="RecreatedPass!123",
        actor=actor,
    )
    assert recreated.user_uuid != user.user_uuid


def test_user_delete_rejects_active_self_bootstrap_and_stale_version() -> None:
    service = _service()
    actor, _, _ = _login(service)
    role = service.create_role(
        role_code="DELETE_USER_GUARDS",
        display_name="削除ガード",
        description="",
        permissions={"menu.query"},
        entitlements=[],
        actor=actor,
    )
    user, _ = service.create_user(
        login_user_id="delete.guards",
        display_name="削除ガード対象",
        role_ids=[role.role_id],
        temporary_password="DeleteGuardsPass!123",
        actor=actor,
    )

    with pytest.raises(SecurityApiError) as active_error:
        service.delete_user(user.user_uuid, expected_version=user.version, actor=actor)
    assert active_error.value.code == "SECURITY_USER_DELETE_REQUIRES_DISABLED"

    bootstrap = service.store.get_user(actor.user_uuid)
    assert bootstrap is not None
    with pytest.raises(SecurityApiError) as self_error:
        service.delete_user(bootstrap.user_uuid, expected_version=bootstrap.version, actor=actor)
    assert self_error.value.code == "SECURITY_USER_DELETE_SELF_FORBIDDEN"

    other_admin = replace(actor, user_uuid="configured-system-admin")
    with pytest.raises(SecurityApiError) as protected_error:
        service.delete_user(
            bootstrap.user_uuid,
            expected_version=bootstrap.version,
            actor=other_admin,
        )
    assert protected_error.value.code == "SECURITY_USER_DELETE_PROTECTED"

    disabled = service.update_user(
        user.user_uuid,
        expected_version=user.version,
        display_name=user.display_name,
        status="DISABLED",
        role_ids=user.role_ids,
        actor=actor,
    )
    with pytest.raises(SecurityApiError) as stale_error:
        service.delete_user(
            user.user_uuid,
            expected_version=disabled.version - 1,
            actor=actor,
        )
    assert stale_error.value.code == "SECURITY_STATE_CONFLICT"
    assert service.store.get_user(user.user_uuid) is not None


def test_archived_unassigned_role_without_entitlements_can_be_deleted() -> None:
    service = _service()
    actor, _, _ = _login(service)
    role = service.create_role(
        role_code="DELETE_ROLE_TARGET",
        display_name="削除対象ロール",
        description="削除対象",
        permissions={"menu.query"},
        entitlements=[],
        allowed_profile_ids={"default"},
        actor=actor,
    )
    archived = service.archive_role(
        role.role_id,
        expected_version=role.version,
        actor=actor,
    )

    deleted = service.delete_role(
        role.role_id,
        expected_version=archived.version,
        actor=actor,
    )

    assert deleted.role_code == "DELETE_ROLE_TARGET"
    assert service.store.get_role(role.role_id) is None


def test_role_delete_rejects_active_built_in_assigned_entitled_and_stale_targets() -> None:
    service = _service()
    actor, _, _ = _login(service)
    active = service.create_role(
        role_code="DELETE_ROLE_ACTIVE",
        display_name="有効ロール",
        description="",
        permissions={"menu.query"},
        entitlements=[],
        actor=actor,
    )
    with pytest.raises(SecurityApiError) as active_error:
        service.delete_role(active.role_id, expected_version=active.version, actor=actor)
    assert active_error.value.code == "SECURITY_ROLE_DELETE_REQUIRES_ARCHIVED"

    system_role = service.store.get_role(SYSTEM_ADMIN_ROLE_ID)
    assert system_role is not None
    with pytest.raises(SecurityApiError) as built_in_error:
        service.delete_role(
            system_role.role_id,
            expected_version=system_role.version,
            actor=actor,
        )
    assert built_in_error.value.code == "SECURITY_ROLE_DELETE_PROTECTED"

    assigned = service.create_role(
        role_code="DELETE_ROLE_ASSIGNED",
        display_name="割当済みロール",
        description="",
        permissions={"menu.query"},
        entitlements=[],
        actor=actor,
    )
    service.create_user(
        login_user_id="assigned.delete.role",
        display_name="割当ユーザー",
        role_ids=[assigned.role_id],
        temporary_password="AssignedDeletePass!123",
        actor=actor,
    )
    assigned_archived = service.archive_role(
        assigned.role_id,
        expected_version=assigned.version,
        actor=actor,
    )
    with pytest.raises(SecurityApiError) as assigned_error:
        service.delete_role(
            assigned.role_id,
            expected_version=assigned_archived.version,
            actor=actor,
        )
    assert assigned_error.value.code == "SECURITY_ROLE_DELETE_ASSIGNED"

    entitled = service.create_role(
        role_code="DELETE_ROLE_ENTITLED",
        display_name="データ権限あり",
        description="",
        permissions={"menu.query"},
        entitlements=[("HR.EMPLOYEES", "*", "SELECT")],
        actor=actor,
    )
    entitled_archived = service.archive_role(
        entitled.role_id,
        expected_version=entitled.version,
        actor=actor,
    )
    with pytest.raises(SecurityApiError) as entitlement_error:
        service.delete_role(
            entitled.role_id,
            expected_version=entitled_archived.version,
            actor=actor,
        )
    assert entitlement_error.value.code == "SECURITY_ROLE_DELETE_ENTITLEMENTS_PRESENT"

    stale = service.create_role(
        role_code="DELETE_ROLE_STALE",
        display_name="競合ロール",
        description="",
        permissions=set(),
        entitlements=[],
        actor=actor,
    )
    stale_archived = service.archive_role(
        stale.role_id,
        expected_version=stale.version,
        actor=actor,
    )
    with pytest.raises(SecurityApiError) as stale_error:
        service.delete_role(
            stale.role_id,
            expected_version=stale_archived.version - 1,
            actor=actor,
        )
    assert stale_error.value.code == "SECURITY_STATE_CONFLICT"
    assert service.store.get_role(stale.role_id) is not None


def test_system_admin_role_cannot_be_assigned_to_new_user() -> None:
    service = _service()
    actor, _, _ = _login(service)

    with pytest.raises(SecurityApiError, match="初期システム管理者"):
        service.create_user(
            login_user_id="extra.admin",
            display_name="追加管理者",
            role_ids=[SYSTEM_ADMIN_ROLE_ID],
            temporary_password="ExtraAdminPass!123",
            actor=actor,
        )

    assert service.store.get_user_by_login_user_id("extra.admin") is None


@pytest.mark.parametrize("login_user_id", ["system_admin", "System_Admin", "SYSTEM_ADMIN"])
def test_reserved_system_admin_login_user_id_cannot_be_created(login_user_id: str) -> None:
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
            login_user_id=login_user_id,
            display_name="予約名",
            role_ids=[role.role_id],
            temporary_password="ReservedPass123A",
            actor=actor,
        )


_SHORT_LOGIN_USER_IDS = ["0", "1", "2", "00", "01", "a", "b", "aa", "bb", "a1", "001"]


@pytest.mark.parametrize("login_user_id", _SHORT_LOGIN_USER_IDS)
def test_user_create_request_accepts_short_login_user_ids(login_user_id: str) -> None:
    payload = UserCreateRequest(
        login_user_id=login_user_id,
        display_name=f"user_{login_user_id}",
        role_ids=["role-user"],
        temporary_password="ShortLoginPass!789",
    )

    assert payload.login_user_id == login_user_id


@pytest.mark.parametrize("login_user_id", ["", " ", ".", "_", "-", "__", ".-_", "a" * 65])
def test_user_create_request_rejects_empty_or_punctuation_only_login_user_ids(
    login_user_id: str,
) -> None:
    with pytest.raises(ValueError):
        UserCreateRequest(
            login_user_id=login_user_id,
            display_name="短いログインユーザーID",
            role_ids=["role-user"],
            temporary_password="ShortLoginPass!789",
        )


@pytest.mark.parametrize("login_user_id", _SHORT_LOGIN_USER_IDS)
def test_short_login_user_ids_can_be_created_and_login(login_user_id: str) -> None:
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
    password = "ShortLoginPass!789"

    user, returned_password = service.create_user(
        login_user_id=login_user_id,
        display_name=f"user_{login_user_id}",
        role_ids=[role.role_id],
        temporary_password=password,
        actor=actor,
    )
    principal, _token, _csrf = service.login(login_user_id, password)

    assert user.login_user_id == login_user_id
    assert returned_password == password
    assert principal.user_uuid == user.user_uuid


def test_security_users_api_accepts_one_character_login_user_id(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _patch_app_admin_env(monkeypatch, tmp_path)
    _configure_memory_api_auth(monkeypatch)

    async def exercise() -> None:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            _current, csrf = await _login_api(client, "system_admin", "AppAdminPass123")
            role_response = await client.post(
                "/api/security/roles",
                headers={"X-CSRF-Token": csrf},
                json={
                    "role_code": "SHORT_LOGIN_USER",
                    "display_name": "短いログインユーザーID",
                    "permissions": ["menu.query"],
                    "data_entitlements": [],
                },
            )
            assert role_response.status_code == 200
            role_id = role_response.json()["data"]["role_id"]

            user_response = await client.post(
                "/api/security/users",
                headers={"X-CSRF-Token": csrf},
                json={
                    "login_user_id": "1",
                    "display_name": "1文字ログイン",
                    "temporary_password": "ShortApiPass!789",
                    "role_ids": [role_id],
                },
            )

            assert user_response.status_code == 200
            assert user_response.json()["data"]["user"]["login_user_id"] == "1"

    try:
        asyncio.run(exercise())
    finally:
        reset_security_service()


def test_system_admin_role_cannot_be_added_to_non_bootstrap_user() -> None:
    service = _service()
    actor, _, _ = _login(service)
    user, _password = service.create_user(
        login_user_id="query.user",
        display_name="検索ユーザー",
        role_ids=[],
        temporary_password="QueryUserPass!123",
        actor=actor,
    )

    with pytest.raises(SecurityApiError, match="初期システム管理者"):
        service.update_user(
            user.user_uuid,
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
            user_uuid="legacy-admin-user",
            login_user_id="legacy.admin",
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
        legacy_admin.user_uuid,
        expected_version=legacy_admin.version,
        display_name=legacy_admin.display_name,
        status="ACTIVE",
        role_ids=[],
        actor=actor,
    )

    assert removed.role_ids == []
    with pytest.raises(SecurityApiError, match="初期システム管理者"):
        service.update_user(
            removed.user_uuid,
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
        assert (
            error.value.public_message == "ログインユーザーIDまたはパスワードを確認してください。"
        )
    user = service.store.get_user_by_login_user_id("admin")
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
                assert not (permission and UNCLASSIFIED_PERMISSION in permission), (
                    f"unclassified route: {method.upper()} {path}"
                )

    assert permission_for_route("POST", "/nl2sql/execute") == frozenset({SQL_EXECUTE_PERMISSION})
    assert permission_for_route("POST", "/nl2sql/jobs") == frozenset({QUERY_GENERATE_PERMISSION})
    assert permission_for_route("POST", "/nl2sql/rewrite") == frozenset({QUERY_GENERATE_PERMISSION})
    assert permission_for_route("POST", "/nl2sql/analyze") == frozenset({SQL_EXECUTE_PERMISSION})
    assert permission_for_route("POST", "/nl2sql/db-admin/execute") == frozenset({"menu.admin_sql"})
    assert permission_for_route("GET", "/nl2sql/profiles/search") == frozenset(
        {PROFILE_READ_PERMISSION}
    )
    assert permission_for_route("GET", "/nl2sql/profiles/{profile_id}/usage-context") == frozenset(
        {PROFILE_READ_PERMISSION}
    )
    assert permission_for_route("GET", "/nl2sql/profiles/{profile_id}/ontology-view") == frozenset(
        {PROFILE_MANAGE_PERMISSION}
    )
    assert permission_for_route("GET", "/nl2sql/profiles/{profile_id}") == frozenset(
        {PROFILE_MANAGE_PERMISSION}
    )
    assert permission_for_route("POST", "/schema/refresh-jobs") == frozenset(
        {SCHEMA_REFRESH_PERMISSION}
    )
    assert permission_for_route("GET", "/schema/refresh-jobs/active") == frozenset(
        {SCHEMA_READ_PERMISSION}
    )
    assert permission_for_route("GET", "/schema/objects") == frozenset({SCHEMA_READ_PERMISSION})
    assert permission_for_route("GET", "/nl2sql/feedback") == frozenset(
        {FEEDBACK_MANAGE_PERMISSION}
    )
    assert permission_for_route("POST", "/nl2sql/feedback") == frozenset(
        {FEEDBACK_WRITE_PERMISSION, FEEDBACK_MANAGE_PERMISSION}
    )
    assert permission_for_route("GET", "/nl2sql/select-ai/db-profiles") == frozenset(
        {SELECT_AI_ASSETS_READ_PERMISSION}
    )
    assert permission_for_route("POST", "/nl2sql/select-ai/db-profiles/refresh-jobs") == frozenset(
        {SELECT_AI_ASSETS_REFRESH_PERMISSION}
    )
    assert permission_for_route("POST", "/nl2sql/select-ai/db-profiles") == frozenset(
        {SELECT_AI_ASSETS_MANAGE_PERMISSION}
    )
    assert permission_for_route("GET", "/nl2sql/legacy-learning-material") == frozenset(
        {LEARNING_MATERIAL_MANAGE_PERMISSION}
    )
    assert permission_for_route("POST", "/nl2sql/sample-data/import") == frozenset(
        {SAMPLE_DATA_MANAGE_PERMISSION}
    )
    assert permission_for_route("GET", "/nl2sql/diagnostics") == frozenset(
        {SYSTEM_STATUS_READ_PERMISSION}
    )
    assert permission_for_route("GET", "/nl2sql/persistence") is None
    assert permission_for_route("POST", "/nl2sql/persistence/recover") == frozenset(
        {PERSISTENCE_RECOVER_PERMISSION}
    )
    assert permission_for_route("GET", "/security/roles") == frozenset(
        {"menu.security_users", "menu.security_roles"}
    )
    assert permission_for_route("GET", "/security/roles/{role_id}") == frozenset(
        {"menu.security_users", "menu.security_roles"}
    )
    assert permission_for_route("POST", "/security/roles") == frozenset({"menu.security_roles"})
    assert permission_for_route("POST", "/security/roles/{role_id}/restore") == frozenset(
        {"menu.security_roles"}
    )
    assert permission_for_route("GET", "/security/permissions") == frozenset(
        {"menu.security_roles"}
    )
    assert permission_for_route("GET", "/security/profile-access/profiles") == frozenset(
        {"menu.security_roles"}
    )


def test_security_audit_permission_and_api_are_removed() -> None:
    catalog_codes = {item.code for item in PERMISSION_CATALOG}

    assert "menu.security_audit" not in catalog_codes
    assert "security.audit.view" not in ALL_PERMISSION_CODES
    assert permission_for_route("GET", "/security/audit/page") == frozenset(
        {UNCLASSIFIED_PERMISSION}
    )
    assert not any(path.startswith("/api/security/audit") for path in app.openapi()["paths"])


def test_schema_refresh_active_static_route_precedes_job_id_route() -> None:
    from app.features.schema.router import router as schema_router

    schema_paths = [
        path
        for route in schema_router.routes
        if (path := getattr(route, "path", "")).startswith("/schema/refresh-jobs/")
    ]

    assert schema_paths.index("/schema/refresh-jobs/active") < schema_paths.index(
        "/schema/refresh-jobs/{job_id}"
    )


def test_sql_use_roles_can_read_profile_usage_context_without_profile_management(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = _configure_memory_api_auth(monkeypatch)
    admin, _, _ = service.login("ADMIN", "BootstrapPass!123")
    admin_user = service.store.get_user(admin.user_uuid)
    assert admin_user is not None
    service.store.set_password(
        admin_user.user_uuid,
        hash_password("BootstrapPass!123"),
        force_change=False,
    )
    query_role = service.create_role(
        role_code="QUERY_ONLY",
        display_name="SQL 生成のみ",
        description="",
        permissions={"menu.query"},
        entitlements=[],
        allowed_profile_ids={"default"},
        actor=admin,
    )
    reverse_role = service.create_role(
        role_code="SQL_TO_QUESTION_ONLY",
        display_name="SQL から質問生成のみ",
        description="",
        permissions={"menu.sql_to_question"},
        entitlements=[],
        allowed_profile_ids={"default"},
        actor=admin,
    )
    profile_manager_role = service.create_role(
        role_code="PROFILE_MANAGER",
        display_name="業務プロファイル管理",
        description="",
        permissions={"menu.profiles"},
        entitlements=[],
        allowed_profile_ids={"default"},
        actor=admin,
    )
    _create_active_user(
        service,
        admin,
        login_user_id="query.only",
        display_name="SQL 生成のみ",
        role_ids=[query_role.role_id],
        password="QueryOnlyPass!123",
    )
    _create_active_user(
        service,
        admin,
        login_user_id="reverse.only",
        display_name="SQL から質問生成のみ",
        role_ids=[reverse_role.role_id],
        password="ReverseOnlyPass!123",
    )
    _create_active_user(
        service,
        admin,
        login_user_id="profile.manager",
        display_name="業務プロファイル管理",
        role_ids=[profile_manager_role.role_id],
        password="ProfileManagePass!123",
    )

    async def exercise() -> None:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            current, csrf = await _login_api(client, "query.only", "QueryOnlyPass!123")
            assert current["role_codes"] == ["QUERY_ONLY"]
            assert set(current["permissions"]) == {
                "menu.query",
                FEEDBACK_WRITE_PERMISSION,
                PROFILE_READ_PERMISSION,
                QUERY_GENERATE_PERMISSION,
                SCHEMA_READ_PERMISSION,
                SQL_EXECUTE_PERMISSION,
            }

            profile_search = await client.get("/api/nl2sql/profiles/search")
            assert profile_search.status_code == 200
            usage_context = await client.get("/api/nl2sql/profiles/default/usage-context")
            assert usage_context.status_code == 200
            usage_payload = usage_context.json()["data"]
            assert usage_payload["id"] == "default"
            assert "few_shot_examples" not in usage_payload
            assert "select_ai_config" not in usage_payload
            assert (await client.get("/api/schema/objects")).status_code == 200
            active_schema_refresh = await client.get("/api/schema/refresh-jobs/active")
            assert active_schema_refresh.status_code == 200
            assert "active_job" in active_schema_refresh.json()["data"]
            assert (await client.get("/api/nl2sql/profiles/default")).status_code == 403
            ontology_view = await client.get("/api/nl2sql/profiles/default/ontology-view")
            assert ontology_view.status_code == 403
            denied_profile_create = await client.post(
                "/api/nl2sql/profiles",
                headers={"X-CSRF-Token": csrf},
                json={"name": "NO_ACCESS"},
            )
            assert denied_profile_create.status_code == 403
            denied_schema_refresh = await client.post(
                "/api/schema/refresh-jobs",
                headers={"X-CSRF-Token": csrf},
            )
            assert denied_schema_refresh.status_code == 403

            _, csrf = await _login_api(client, "reverse.only", "ReverseOnlyPass!123")
            reverse_current = await client.get("/api/auth/me")
            assert reverse_current.status_code == 200
            assert set(reverse_current.json()["data"]["permissions"]) == {
                "menu.sql_to_question",
                PROFILE_READ_PERMISSION,
                SCHEMA_READ_PERMISSION,
            }
            reverse_response = await client.post(
                "/api/nl2sql/reverse/deep",
                headers={"X-CSRF-Token": csrf},
                json={"sql": "SELECT EMPLOYEE_NAME FROM EMPLOYEE", "profile_id": "default"},
            )
            assert reverse_response.status_code == 200
            assert (await client.get("/api/nl2sql/profiles/default")).status_code == 403

            await _login_api(client, "profile.manager", "ProfileManagePass!123")
            full_profile = await client.get("/api/nl2sql/profiles/default")
            assert full_profile.status_code == 200
            full_payload = full_profile.json()["data"]
            assert "few_shot_examples" in full_payload
            assert "select_ai_config" in full_payload

    try:
        asyncio.run(exercise())
    finally:
        reset_security_service()


def test_nl2sql_profiles_are_filtered_and_used_by_role_profile_access(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = _configure_memory_api_auth(monkeypatch)
    admin, _, _ = service.login("ADMIN", "BootstrapPass!123")
    admin_user = service.store.get_user(admin.user_uuid)
    assert admin_user is not None
    service.store.set_password(
        admin_user.user_uuid,
        hash_password("BootstrapPass!123"),
        force_change=False,
    )
    query_role = service.create_role(
        role_code="QUERY_DEFAULT_PROFILE",
        display_name="標準 profile SQL 生成",
        description="",
        permissions={"menu.query"},
        entitlements=[],
        allowed_profile_ids={"default"},
        actor=admin,
    )
    profile_manager_role = service.create_role(
        role_code="PROFILE_DEFAULT_MANAGER",
        display_name="標準 profile 管理",
        description="",
        permissions={"menu.profiles"},
        entitlements=[],
        allowed_profile_ids={"default"},
        actor=admin,
    )
    blocked_role = service.create_role(
        role_code="QUERY_NO_PROFILE",
        display_name="profile 未付与 SQL 生成",
        description="",
        permissions={"menu.query"},
        entitlements=[],
        actor=admin,
    )
    _create_active_user(
        service,
        admin,
        login_user_id="query.default.profile",
        display_name="標準 profile SQL 生成",
        role_ids=[query_role.role_id],
        password="QueryDefaultPass!123",
    )
    _create_active_user(
        service,
        admin,
        login_user_id="profile.default.manager",
        display_name="標準 profile 管理",
        role_ids=[profile_manager_role.role_id],
        password="ProfileDefaultPass!123",
    )
    _create_active_user(
        service,
        admin,
        login_user_id="query.no.profile",
        display_name="profile 未付与 SQL 生成",
        role_ids=[blocked_role.role_id],
        password="QueryNoProfilePass!123",
    )

    feature_service = Nl2SqlService(store=MemoryNl2SqlStore())
    feature_service.create_profile(
        Nl2SqlProfile(
            id="finance",
            name="財務プロファイル",
            category="会計",
            description="財務部門向け",
        )
    )
    monkeypatch.setattr(nl2sql_router, "nl2sql_service", feature_service)

    async def exercise() -> None:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            current, csrf = await _login_api(
                client,
                "query.default.profile",
                "QueryDefaultPass!123",
            )
            assert current["allowed_profile_ids"] == ["default"]
            profile_search = await client.get("/api/nl2sql/profiles/search")
            assert profile_search.status_code == 200
            assert [item["id"] for item in profile_search.json()["data"]["items"]] == ["default"]
            allowed_etag = profile_search.headers["ETag"]
            default_usage = await client.get("/api/nl2sql/profiles/default/usage-context")
            assert default_usage.status_code == 200
            finance_usage = await client.get("/api/nl2sql/profiles/finance/usage-context")
            assert finance_usage.status_code == 403
            preview = await client.post(
                "/api/nl2sql/preview",
                headers={"X-CSRF-Token": csrf},
                json={"question": "社員一覧", "profile_id": "default"},
            )
            assert preview.status_code != 403
            denied_preview = await client.post(
                "/api/nl2sql/preview",
                headers={"X-CSRF-Token": csrf},
                json={"question": "売上一覧", "profile_id": "finance"},
            )
            assert denied_preview.status_code == 403
            denied_rewrite = await client.post(
                "/api/nl2sql/rewrite",
                headers={"X-CSRF-Token": csrf},
                json={"question": "売上一覧", "profile_id": "finance"},
            )
            assert denied_rewrite.status_code == 403
            denied_evaluation = await client.get(
                "/api/nl2sql/quality-evaluations/capabilities?profile_id=finance"
            )
            assert denied_evaluation.status_code == 403

            await _login_api(client, "profile.default.manager", "ProfileDefaultPass!123")
            assert (await client.get("/api/nl2sql/profiles/default")).status_code == 200
            assert (await client.get("/api/nl2sql/profiles/finance")).status_code == 403

            _, csrf = await _login_api(client, "query.no.profile", "QueryNoProfilePass!123")
            empty_search = await client.get("/api/nl2sql/profiles/search")
            assert empty_search.status_code == 200
            assert empty_search.json()["data"]["items"] == []
            assert empty_search.headers["ETag"] != allowed_etag
            blocked_default_usage = await client.get("/api/nl2sql/profiles/default/usage-context")
            assert blocked_default_usage.status_code == 403
            denied_default_preview = await client.post(
                "/api/nl2sql/preview",
                headers={"X-CSRF-Token": csrf},
                json={"question": "社員一覧"},
            )
            assert denied_default_preview.status_code == 403

            await _login_api(client, "ADMIN", "BootstrapPass!123")
            admin_search = await client.get("/api/nl2sql/profiles/search")
            assert admin_search.status_code == 200
            assert {item["id"] for item in admin_search.json()["data"]["items"]} >= {
                "default",
                "finance",
            }
            admin_finance_usage = await client.get("/api/nl2sql/profiles/finance/usage-context")
            assert admin_finance_usage.status_code == 200

    try:
        asyncio.run(exercise())
    finally:
        reset_security_service()


def test_nl2sql_capability_boundaries_and_feedback_ownership(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = _configure_memory_api_auth(monkeypatch)
    admin, _, _ = service.login("ADMIN", "BootstrapPass!123")
    query_role = service.create_role(
        role_code="QUERY_CAPABILITY_ONLY",
        display_name="SQL 生成のみ",
        description="",
        permissions={"menu.query"},
        entitlements=[],
        actor=admin,
    )
    direct_role = service.create_role(
        role_code="DIRECT_SQL_ONLY",
        display_name="SELECT SQL 実行のみ",
        description="",
        permissions={"menu.direct_sql"},
        entitlements=[],
        actor=admin,
    )
    feedback_role = service.create_role(
        role_code="FEEDBACK_MANAGER",
        display_name="フィードバック管理",
        description="",
        permissions={"menu.feedback_management"},
        entitlements=[],
        actor=admin,
    )
    data_role = service.create_role(
        role_code="DATA_MANAGER",
        display_name="データ管理",
        description="",
        permissions={"menu.data_management"},
        entitlements=[],
        actor=admin,
    )
    query_user = _create_active_user(
        service,
        admin,
        login_user_id="query.capability",
        display_name="SQL 生成ユーザー",
        role_ids=[query_role.role_id],
        password="QueryCapabilityPass!123",
    )
    _create_active_user(
        service,
        admin,
        login_user_id="direct.only",
        display_name="SELECT SQL 実行ユーザー",
        role_ids=[direct_role.role_id],
        password="DirectOnlyPass!123",
    )
    _create_active_user(
        service,
        admin,
        login_user_id="feedback.manager",
        display_name="フィードバック管理者",
        role_ids=[feedback_role.role_id],
        password="FeedbackManagerPass!123",
    )
    _create_active_user(
        service,
        admin,
        login_user_id="data.manager",
        display_name="データ管理者",
        role_ids=[data_role.role_id],
        password="DataManagerPass!123",
    )

    feature_service = Nl2SqlService(store=MemoryNl2SqlStore())
    feature_service._history = [  # noqa: SLF001 - actor boundary fixture
        HistoryItem(
            id="query-history",
            question="社員一覧",
            engine=Nl2SqlEngine.SELECT_AI,
            generated_sql="SELECT EMPLOYEE_ID FROM EMPLOYEE",
            created_at="2026-08-20T00:00:00+00:00",
            actor_user_uuid=query_user.user_uuid,
        ),
        HistoryItem(
            id="other-history",
            question="部署一覧",
            engine=Nl2SqlEngine.SELECT_AI,
            generated_sql="SELECT DEPARTMENT_ID FROM DEPARTMENT",
            created_at="2026-08-20T00:01:00+00:00",
            actor_user_uuid="other-user",
        ),
    ]
    monkeypatch.setattr(nl2sql_router, "nl2sql_service", feature_service)

    async def exercise() -> None:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            assert (await client.get("/api/nl2sql/persistence")).status_code == 401
            _, csrf = await _login_api(client, "query.capability", "QueryCapabilityPass!123")
            persistence = await client.get("/api/nl2sql/persistence")
            assert persistence.status_code == 200
            assert persistence.json()["data"]["ready"] is True
            assert (await client.get("/api/nl2sql/diagnostics")).status_code == 403
            assert (
                await client.post(
                    "/api/nl2sql/persistence/recover",
                    headers={"X-CSRF-Token": csrf},
                )
            ).status_code == 403
            own_feedback = await client.post(
                "/api/nl2sql/feedback",
                headers={"X-CSRF-Token": csrf},
                json={
                    "history_id": "query-history",
                    "rating": "good",
                    "comment": "自分の履歴",
                },
            )
            assert own_feedback.status_code == 200
            other_feedback = await client.post(
                "/api/nl2sql/feedback",
                headers={"X-CSRF-Token": csrf},
                json={
                    "history_id": "other-history",
                    "rating": "bad",
                    "comment": "他人の履歴",
                },
            )
            assert other_feedback.status_code == 403
            assert (await client.get("/api/nl2sql/feedback")).status_code == 403
            assert (
                await client.post(
                    "/api/nl2sql/sample-data/import",
                    headers={"X-CSRF-Token": csrf},
                    json={"step": "all", "confirmation": "SQL_ASSIST_SAMPLE"},
                )
            ).status_code == 403
            assert (await client.get("/api/nl2sql/legacy-learning-material")).status_code == 403
            assert (
                await client.post(
                    "/api/nl2sql/select-ai/db-profiles/refresh-jobs",
                    headers={"X-CSRF-Token": csrf},
                )
            ).status_code == 403

            _, csrf = await _login_api(client, "direct.only", "DirectOnlyPass!123")
            analyze = await client.post(
                "/api/nl2sql/analyze",
                headers={"X-CSRF-Token": csrf},
                json={"sql": "SELECT EMPLOYEE_ID FROM EMPLOYEE"},
            )
            assert analyze.status_code == 200
            assert (
                await client.post(
                    "/api/nl2sql/jobs",
                    headers={"X-CSRF-Token": csrf},
                    json={"question": "社員一覧", "profile_id": "default"},
                )
            ).status_code == 403
            assert (
                await client.post(
                    "/api/nl2sql/rewrite",
                    headers={"X-CSRF-Token": csrf},
                    json={"question": "社員一覧", "profile_id": "default"},
                )
            ).status_code == 403

            _, csrf = await _login_api(
                client,
                "feedback.manager",
                "FeedbackManagerPass!123",
            )
            feedback_list = await client.get("/api/nl2sql/feedback")
            assert feedback_list.status_code == 200
            assert {item["id"] for item in feedback_list.json()["data"]["items"]} == {
                "query-history",
                "other-history",
            }
            manager_feedback = await client.post(
                "/api/nl2sql/feedback",
                headers={"X-CSRF-Token": csrf},
                json={
                    "history_id": "other-history",
                    "rating": "good",
                    "comment": "管理者は全履歴を更新可能",
                },
            )
            assert manager_feedback.status_code == 200
            assert (await client.get("/api/nl2sql/feedback-index")).status_code == 200

            await _login_api(client, "data.manager", "DataManagerPass!123")
            assert (await client.get("/api/nl2sql/select-ai/db-profiles")).status_code == 200

    try:
        asyncio.run(exercise())
    finally:
        reset_security_service()


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
    assert "migration=012" in output
    assert "migration=016" in output


def test_security_migration_user_uuid_rename_ignores_missing_constraint() -> None:
    """014 can be re-run after constraint renames have already been applied."""

    source = (
        Path(__file__).resolve().parents[1] / "app" / "cli" / "app_security_migrate.py"
    ).read_text(encoding="utf-8")
    block = re.search(
        r"user_uuid_rename_results = oracle_statement_executor\.execute\((?P<body>.*?)\n\s*\)",
        source,
        flags=re.S,
    )

    assert block is not None
    assert "ORA-23292" in block.group("body")


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
    monkeypatch.setattr(settings, "app_admin_login_user_id", "system_admin")
    monkeypatch.setattr(settings, "app_admin_login_user_password", "BootstrapPass123")
    monkeypatch.setattr(settings, "nl2sql_persistence_mode", "memory")
    reset_security_service()

    async def exercise() -> None:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            unauthenticated = await client.get("/api/auth/me")
            assert unauthenticated.status_code == 401

            login = await client.post(
                "/api/auth/login",
                json={"login_user_id": "system_admin", "password": "BootstrapPass123"},
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
            assert data["data_user"] == "DEEPSEC_DATA_USER"
            assert data["has_data_user_password"] is True
            assert "DeepSecret!456" not in response.text

    try:
        asyncio.run(exercise())
    finally:
        reset_security_service()

    assert settings.oracle_deepsec_enabled is True
    assert settings.oracle_deepsec_data_user == "DEEPSEC_DATA_USER"
    assert settings.oracle_deepsec_data_user_password == "DeepSecret!456"
    assert closed == [True]
    env_text = env_file.read_text(encoding="utf-8")
    assert "ORACLE_DEEPSEC_ENABLED=true" in env_text
    assert "ORACLE_DEEPSEC_DATA_USER=DEEPSEC_DATA_USER" in env_text
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
    monkeypatch.setattr(settings, "app_admin_login_user_id", "system_admin")
    monkeypatch.setattr(settings, "app_admin_login_user_password", "AppAdminPass123")
    monkeypatch.setattr(settings, "nl2sql_persistence_mode", "memory")
    reset_security_service()
    from app.security.service import get_security_service

    service = get_security_service()
    assert isinstance(service.store, InMemorySecurityStore)
    assert service.store.bootstrap(
        login_user_id="ADMIN",
        display_name="ADMIN（システム管理者）",
        password_hash=hash_password("BootstrapPass!123"),
    )

    async def exercise() -> None:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            await client.post(
                "/api/auth/login",
                json={"login_user_id": "ADMIN", "password": "BootstrapPass!123"},
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
                json={"login_user_id": "ADMIN", "password": "IndependentPass!456"},
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
            deepsec_role_response = await client.post(
                "/api/security/roles",
                headers={"X-CSRF-Token": csrf},
                json={
                    "role_code": "DEEPSEC_MANAGER",
                    "display_name": "DeepSec 管理",
                    "permissions": ["menu.security_deepsec"],
                    "data_entitlements": [],
                },
            )
            assert deepsec_role_response.status_code == 200
            deepsec_role_id = deepsec_role_response.json()["data"]["role_id"]
            user_response = await client.post(
                "/api/security/users",
                headers={"X-CSRF-Token": csrf},
                json={
                    "login_user_id": "viewer.user",
                    "display_name": "履歴閲覧ユーザー",
                    "temporary_password": "ViewerStart!123",
                    "role_ids": [role_id],
                },
            )
            assert user_response.status_code == 200
            deepsec_user_response = await client.post(
                "/api/security/users",
                headers={"X-CSRF-Token": csrf},
                json={
                    "login_user_id": "deepsec.user",
                    "display_name": "DeepSec 管理ユーザー",
                    "temporary_password": "DeepSecStart!123",
                    "role_ids": [deepsec_role_id],
                },
            )
            assert deepsec_user_response.status_code == 200
            await client.post("/api/auth/logout", headers={"X-CSRF-Token": csrf})

            await client.post(
                "/api/auth/login",
                json={"login_user_id": "viewer.user", "password": "ViewerStart!123"},
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
                json={"login_user_id": "viewer.user", "password": "ViewerActive!456"},
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
            assert (await client.get("/api/security/deepsec/data-entitlements")).status_code == 403

            await client.post("/api/auth/logout", headers={"X-CSRF-Token": csrf})
            await client.post(
                "/api/auth/login",
                json={"login_user_id": "deepsec.user", "password": "DeepSecStart!123"},
            )
            csrf = client.cookies.get("nl2sql_csrf")
            assert csrf
            await client.post(
                "/api/auth/password/change",
                headers={"X-CSRF-Token": csrf},
                json={
                    "current_password": "DeepSecStart!123",
                    "new_password": "DeepSecActive!456",
                },
            )
            await client.post(
                "/api/auth/login",
                json={"login_user_id": "deepsec.user", "password": "DeepSecActive!456"},
            )
            csrf = client.cookies.get("nl2sql_csrf")
            assert csrf
            assert (await client.get("/api/security/roles")).status_code == 403
            entitlement_response = await client.get("/api/security/deepsec/data-entitlements")
            assert entitlement_response.status_code == 200
            assert any(
                item["role_code"] == "DEEPSEC_MANAGER"
                for item in entitlement_response.json()["data"]
            )
            patch_response = await client.patch(
                f"/api/security/deepsec/data-entitlements/{deepsec_role_id}",
                headers={"X-CSRF-Token": csrf},
                json={
                    "version": deepsec_role_response.json()["data"]["version"],
                    "data_entitlements": [
                        {
                            "resource_code": "HR.EMPLOYEES",
                            "scope_code": "SALES",
                            "capability": "SELECT",
                            "target_owner": "HR",
                            "target_object": "EMPLOYEES",
                            "target_type": "TABLE",
                            "column_names": ["EMPLOYEE_ID", "DISPLAY_NAME"],
                            "scope_mode": "COLUMN_EQUALS",
                            "scope_column": "DEPARTMENT_CODE",
                        }
                    ],
                },
            )
            assert patch_response.status_code == 200
            assert patch_response.json()["data"]["data_entitlements"][0]["scope_code"] == "SALES"
            stored_role = service.store.get_role(deepsec_role_id)
            assert stored_role is not None
            assert stored_role.display_name == "DeepSec 管理"
            assert stored_role.permissions == {"menu.security_deepsec"}

    try:
        asyncio.run(exercise())
    finally:
        reset_security_service()


def test_user_manager_can_load_role_options_but_not_manage_roles(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = _configure_memory_api_auth(monkeypatch)
    admin, _, _ = service.login("ADMIN", "BootstrapPass!123")
    user_manager_role = service.create_role(
        role_code="USER_MANAGER",
        display_name="ユーザー管理",
        description="",
        permissions={"menu.security_users"},
        entitlements=[],
        actor=admin,
    )
    role_manager_role = service.create_role(
        role_code="ROLE_MANAGER",
        display_name="ロール管理",
        description="",
        permissions={"menu.security_roles"},
        entitlements=[],
        actor=admin,
    )
    query_role = service.create_role(
        role_code="QUERY_VIEWER_FOR_USER_MANAGER",
        display_name="SQL 生成",
        description="",
        permissions={"menu.query"},
        entitlements=[],
        actor=admin,
    )
    archived_role = service.create_role(
        role_code="ARCHIVED_USER_MANAGER",
        display_name="廃止ユーザー管理",
        description="",
        permissions={"menu.security_users"},
        entitlements=[],
        actor=admin,
    )
    service.archive_role(
        archived_role.role_id,
        expected_version=archived_role.version,
        actor=admin,
    )
    _create_active_user(
        service,
        admin,
        login_user_id="user.manager",
        display_name="ユーザー管理者",
        role_ids=[user_manager_role.role_id],
        password="UserManagerPass!123",
    )

    async def exercise() -> None:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            _, csrf = await _login_api(client, "user.manager", "UserManagerPass!123")

            users_response = await client.get("/api/security/users")
            assert users_response.status_code == 200

            roles_response = await client.get("/api/security/roles?include_archived=true")
            assert roles_response.status_code == 200
            assert [item["role_code"] for item in roles_response.json()["data"]] == ["USER_MANAGER"]

            own_role = await client.get(f"/api/security/roles/{user_manager_role.role_id}")
            assert own_role.status_code == 200
            hidden_role = await client.get(f"/api/security/roles/{query_role.role_id}")
            assert hidden_role.status_code == 404

            create_role = await client.post(
                "/api/security/roles",
                headers={"X-CSRF-Token": csrf},
                json={
                    "role_code": "SHOULD_NOT_CREATE",
                    "display_name": "作成不可",
                    "permissions": ["menu.security_users"],
                    "data_entitlements": [],
                },
            )
            assert create_role.status_code == 403

            update_role = await client.patch(
                f"/api/security/roles/{user_manager_role.role_id}",
                headers={"X-CSRF-Token": csrf},
                json={
                    "version": user_manager_role.version,
                    "display_name": "変更不可",
                    "description": "",
                    "permissions": ["menu.security_users"],
                    "data_entitlements": [],
                },
            )
            assert update_role.status_code == 403

            archive_role = await client.post(
                f"/api/security/roles/{role_manager_role.role_id}/archive",
                headers={"X-CSRF-Token": csrf},
                json={"version": role_manager_role.version},
            )
            assert archive_role.status_code == 403
            restore_role = await client.post(
                f"/api/security/roles/{archived_role.role_id}/restore",
                headers={"X-CSRF-Token": csrf},
                json={"version": archived_role.version},
            )
            assert restore_role.status_code == 403
            delete_role = await client.delete(
                f"/api/security/roles/{archived_role.role_id}",
                headers={"X-CSRF-Token": csrf, "If-Match": '"1"'},
            )
            assert delete_role.status_code == 403
            assert (await client.get("/api/security/permissions")).status_code == 403

    try:
        asyncio.run(exercise())
    finally:
        reset_security_service()


def test_security_delete_api_requires_csrf_and_if_match_and_deletes_targets(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = _configure_memory_api_auth(monkeypatch)
    admin, _, _ = service.login("ADMIN", "BootstrapPass!123")
    admin_user = service.store.get_user(admin.user_uuid)
    assert admin_user is not None
    service.store.set_password(
        admin_user.user_uuid,
        hash_password("BootstrapPass!123"),
        force_change=False,
    )
    role = service.create_role(
        role_code="DELETE_API_ROLE",
        display_name="削除 API ロール",
        description="",
        permissions={"menu.query"},
        entitlements=[],
        actor=admin,
    )
    user, _ = service.create_user(
        login_user_id="delete.api.user",
        display_name="削除 API ユーザー",
        role_ids=[role.role_id],
        temporary_password="DeleteApiUserPass!123",
        actor=admin,
    )
    disabled = service.update_user(
        user.user_uuid,
        expected_version=user.version,
        display_name=user.display_name,
        status="DISABLED",
        role_ids=user.role_ids,
        actor=admin,
    )
    archived = service.archive_role(
        role.role_id,
        expected_version=role.version,
        actor=admin,
    )

    async def exercise() -> None:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            _, csrf = await _login_api(client, "ADMIN", "BootstrapPass!123")
            user_path = f"/api/security/users/{user.user_uuid}"

            no_csrf = await client.delete(
                user_path,
                headers={"If-Match": f'"{disabled.version}"'},
            )
            assert no_csrf.status_code == 403

            missing_version = await client.delete(
                user_path,
                headers={"X-CSRF-Token": csrf},
            )
            assert missing_version.status_code == 428
            assert missing_version.json()["problem"]["code"] == "SECURITY_VERSION_REQUIRED"

            invalid_version = await client.delete(
                user_path,
                headers={"X-CSRF-Token": csrf, "If-Match": f'W/"{disabled.version}"'},
            )
            assert invalid_version.status_code == 400
            assert invalid_version.json()["problem"]["code"] == "SECURITY_VERSION_INVALID"

            stale = await client.delete(
                user_path,
                headers={"X-CSRF-Token": csrf, "If-Match": f'"{disabled.version - 1}"'},
            )
            assert stale.status_code == 409
            assert stale.json()["problem"]["code"] == "SECURITY_STATE_CONFLICT"

            deleted_user = await client.delete(
                user_path,
                headers={"X-CSRF-Token": csrf, "If-Match": f'"{disabled.version}"'},
            )
            assert deleted_user.status_code == 200
            assert deleted_user.json()["data"] == {
                "deleted": True,
                "user_uuid": user.user_uuid,
                "login_user_id": "delete.api.user",
            }
            deleted_user_again = await client.delete(
                user_path,
                headers={"X-CSRF-Token": csrf, "If-Match": f'"{disabled.version}"'},
            )
            assert deleted_user_again.status_code == 404
            assert deleted_user_again.json()["problem"]["code"] == "SECURITY_RESOURCE_NOT_FOUND"

            role_path = f"/api/security/roles/{role.role_id}"
            role_missing_version = await client.delete(
                role_path,
                headers={"X-CSRF-Token": csrf},
            )
            assert role_missing_version.status_code == 428

            deleted_role = await client.delete(
                role_path,
                headers={"X-CSRF-Token": csrf, "If-Match": f'"{archived.version}"'},
            )
            assert deleted_role.status_code == 200
            assert deleted_role.json()["data"] == {
                "deleted": True,
                "role_id": role.role_id,
                "role_code": "DELETE_API_ROLE",
            }
            deleted_role_again = await client.delete(
                role_path,
                headers={"X-CSRF-Token": csrf, "If-Match": f'"{archived.version}"'},
            )
            assert deleted_role_again.status_code == 404
            assert deleted_role_again.json()["problem"]["code"] == "SECURITY_RESOURCE_NOT_FOUND"

    try:
        asyncio.run(exercise())
    finally:
        reset_security_service()


def test_security_conflicts_and_validation_use_problem_contract(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = _configure_memory_api_auth(monkeypatch)
    admin_user = service.store.get_user_by_login_user_id("admin")
    assert admin_user is not None
    service.store.set_password(
        admin_user.user_uuid,
        hash_password("BootstrapPass!123"),
        force_change=False,
    )

    async def exercise() -> None:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            _, csrf = await _login_api(client, "ADMIN", "BootstrapPass!123")
            headers = {
                "X-CSRF-Token": csrf,
                "X-Request-ID": "security-problem-contract-test",
            }
            role_payload = {
                "role_code": "DATA_USER",
                "display_name": "データユーザー",
                "permissions": ["menu.query"],
                "data_entitlements": [],
            }
            created_role = await client.post(
                "/api/security/roles",
                headers=headers,
                json=role_payload,
            )
            assert created_role.status_code == 200
            role_id = created_role.json()["data"]["role_id"]

            duplicate_role = await client.post(
                "/api/security/roles",
                headers=headers,
                json=role_payload,
            )
            assert_problem_contract(
                duplicate_role,
                code="SECURITY_ROLE_CODE_CONFLICT",
                pointer="/role_code",
            )

            user_payload = {
                "login_user_id": "duplicate.user",
                "display_name": "重複確認ユーザー",
                "temporary_password": "DuplicatePass!123",
                "role_ids": [role_id],
            }
            created_user = await client.post(
                "/api/security/users",
                headers=headers,
                json=user_payload,
            )
            assert created_user.status_code == 200

            duplicate_user = await client.post(
                "/api/security/users",
                headers=headers,
                json=user_payload,
            )
            assert_problem_contract(
                duplicate_user,
                code="SECURITY_USER_LOGIN_ID_CONFLICT",
                pointer="/login_user_id",
            )
            assert duplicate_user.json()["problem"]["detail"] == (
                "このログインユーザーIDは既に使用されています。別のIDを入力してください。"
            )

            invalid_user = await client.post(
                "/api/security/users",
                headers=headers,
                json={"login_user_id": "", "display_name": ""},
            )
            assert invalid_user.status_code == 422
            invalid_body = invalid_user.json()
            assert invalid_body["error_code"] == "REQUEST_VALIDATION_FAILED"
            assert {item["pointer"] for item in invalid_body["problem"]["field_errors"]} == {
                "/login_user_id",
                "/display_name",
            }

    try:
        asyncio.run(exercise())
    finally:
        reset_security_service()


def test_missing_users_table_uses_409_problem_contract_without_oracle_detail(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = _configure_memory_api_auth(monkeypatch)
    admin_user = service.store.get_user_by_login_user_id("admin")
    assert admin_user is not None
    service.store.set_password(
        admin_user.user_uuid,
        hash_password("BootstrapPass!123"),
        force_change=False,
    )

    async def exercise() -> None:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            login_response = await client.post(
                "/api/auth/login",
                json={"login_user_id": "ADMIN", "password": "BootstrapPass!123"},
            )
            assert login_response.status_code == 200

            def missing_users() -> list[UserRecord]:
                raise RuntimeError(
                    'ORA-00942: table or view "ADMIN"."NL2SQL_APP_USERS" does not exist'
                )

            monkeypatch.setattr(service.store, "list_users", missing_users)
            response = await client.get(
                "/api/security/users",
                headers={"X-Request-ID": "security-migration-request"},
            )

        assert response.status_code == 409
        body = response.json()
        assert body["error_code"] == "SECURITY_SCHEMA_MIGRATION_REQUIRED"
        assert body["problem"]["code"] == "SECURITY_SCHEMA_MIGRATION_REQUIRED"
        assert body["problem"]["request_id"] == "security-migration-request"
        assert body["problem"]["retryable"] is False
        assert "app_security_migrate" in body["problem"]["detail"]
        assert "ORA-00942" not in response.text
        assert "NL2SQL_APP_USERS" not in response.text

    try:
        asyncio.run(exercise())
    finally:
        reset_security_service()


def assert_problem_contract(
    response: httpx.Response,
    *,
    code: str,
    pointer: str,
) -> None:
    assert response.status_code == 409
    body = response.json()
    problem = body["problem"]
    assert body["error_code"] == code
    assert body["error_messages"][0] == problem["detail"]
    assert problem["code"] == code
    assert problem["status"] == 409
    assert problem["type"] == f"urn:nl2sql:problem:{code.lower().replace('_', '-')}"
    assert problem["retryable"] is False
    assert problem["field_errors"][0]["pointer"] == pointer
    assert problem["request_id"] == "security-problem-contract-test"
    assert response.headers["X-Request-ID"] == problem["request_id"]


def test_role_manager_cannot_manage_users(monkeypatch: pytest.MonkeyPatch) -> None:
    service = _configure_memory_api_auth(monkeypatch)
    admin, _, _ = service.login("ADMIN", "BootstrapPass!123")
    role_manager_role = service.create_role(
        role_code="ROLE_MANAGER_ONLY",
        display_name="ロール管理のみ",
        description="",
        permissions={"menu.security_roles"},
        entitlements=[],
        actor=admin,
    )
    query_role = service.create_role(
        role_code="QUERY_ROLE_FOR_ROLE_MANAGER",
        display_name="SQL 生成",
        description="",
        permissions={"menu.query"},
        entitlements=[],
        actor=admin,
    )
    target = _create_active_user(
        service,
        admin,
        login_user_id="query.target",
        display_name="検索ユーザー",
        role_ids=[query_role.role_id],
        password="QueryTargetPass!123",
    )
    _create_active_user(
        service,
        admin,
        login_user_id="role.manager",
        display_name="ロール管理者",
        role_ids=[role_manager_role.role_id],
        password="RoleManagerPass!123",
    )

    async def exercise() -> None:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            _, csrf = await _login_api(client, "role.manager", "RoleManagerPass!123")

            roles_response = await client.get("/api/security/roles")
            assert roles_response.status_code == 200
            assert any(
                item["role_code"] == "ROLE_MANAGER_ONLY" for item in roles_response.json()["data"]
            )
            assert (await client.get("/api/security/permissions")).status_code == 200

            create_role = await client.post(
                "/api/security/roles",
                headers={"X-CSRF-Token": csrf},
                json={
                    "role_code": "ROLE_MANAGER_CREATED",
                    "display_name": "作成可",
                    "permissions": ["menu.history"],
                    "data_entitlements": [],
                },
            )
            assert create_role.status_code == 200

            assert (await client.get("/api/security/users")).status_code == 403
            create_user = await client.post(
                "/api/security/users",
                headers={"X-CSRF-Token": csrf},
                json={
                    "login_user_id": "blocked.user",
                    "display_name": "作成不可",
                    "temporary_password": "BlockedUserPass!123",
                    "role_ids": [query_role.role_id],
                },
            )
            assert create_user.status_code == 403

            update_user = await client.patch(
                f"/api/security/users/{target.user_uuid}",
                headers={"X-CSRF-Token": csrf},
                json={
                    "version": target.version,
                    "display_name": target.display_name,
                    "status": target.status,
                    "role_ids": target.role_ids,
                },
            )
            assert update_user.status_code == 403
            delete_user = await client.delete(
                f"/api/security/users/{target.user_uuid}",
                headers={"X-CSRF-Token": csrf, "If-Match": f'"{target.version}"'},
            )
            assert delete_user.status_code == 403

    try:
        asyncio.run(exercise())
    finally:
        reset_security_service()


def test_user_manager_cannot_assign_role_outside_own_permissions(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = _configure_memory_api_auth(monkeypatch)
    admin, _, _ = service.login("ADMIN", "BootstrapPass!123")
    user_manager_role = service.create_role(
        role_code="LIMITED_USER_MANAGER",
        display_name="限定ユーザー管理",
        description="",
        permissions={"menu.security_users"},
        entitlements=[],
        actor=admin,
    )
    role_manager_role = service.create_role(
        role_code="FORBIDDEN_ROLE_MANAGER",
        display_name="ロール管理",
        description="",
        permissions={"menu.security_roles"},
        entitlements=[],
        actor=admin,
    )
    admin_sql_role = service.create_role(
        role_code="FORBIDDEN_ADMIN_SQL",
        display_name="管理 SQL",
        description="",
        permissions={"menu.admin_sql"},
        entitlements=[],
        actor=admin,
    )
    target = _create_active_user(
        service,
        admin,
        login_user_id="managed.user",
        display_name="管理対象",
        role_ids=[user_manager_role.role_id],
        password="ManagedUserPass!123",
    )
    high_privilege_user = _create_active_user(
        service,
        admin,
        login_user_id="high.user",
        display_name="高権限ユーザー",
        role_ids=[role_manager_role.role_id],
        password="HighUserPass!123",
    )
    _create_active_user(
        service,
        admin,
        login_user_id="limited.manager",
        display_name="限定管理者",
        role_ids=[user_manager_role.role_id],
        password="LimitedManagerPass!123",
    )

    async def exercise() -> None:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            _, csrf = await _login_api(client, "limited.manager", "LimitedManagerPass!123")

            denied_role_manager = await client.post(
                "/api/security/users",
                headers={"X-CSRF-Token": csrf},
                json={
                    "login_user_id": "blocked.role.manager",
                    "display_name": "ロール管理付与不可",
                    "temporary_password": "BlockedRolePass!123",
                    "role_ids": [role_manager_role.role_id],
                },
            )
            assert denied_role_manager.status_code == 403

            denied_admin_sql = await client.post(
                "/api/security/users",
                headers={"X-CSRF-Token": csrf},
                json={
                    "login_user_id": "blocked.admin.sql",
                    "display_name": "管理 SQL 付与不可",
                    "temporary_password": "BlockedAdminPass!123",
                    "role_ids": [admin_sql_role.role_id],
                },
            )
            assert denied_admin_sql.status_code == 403

            denied_update = await client.patch(
                f"/api/security/users/{target.user_uuid}",
                headers={"X-CSRF-Token": csrf},
                json={
                    "version": target.version,
                    "display_name": target.display_name,
                    "status": target.status,
                    "role_ids": [role_manager_role.role_id],
                },
            )
            assert denied_update.status_code == 403

            denied_reset = await client.post(
                f"/api/security/users/{high_privilege_user.user_uuid}/reset-password",
                headers={"X-CSRF-Token": csrf},
                json={"temporary_password": "TakeoverPass!123"},
            )
            assert denied_reset.status_code == 403

    try:
        asyncio.run(exercise())
    finally:
        reset_security_service()


def test_user_manager_can_assign_subset_role_and_runtime_access_matches(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = _configure_memory_api_auth(monkeypatch)
    admin, _, _ = service.login("ADMIN", "BootstrapPass!123")
    manager_role = service.create_role(
        role_code="USER_AND_QUERY_MANAGER",
        display_name="ユーザー管理と SQL 生成",
        description="",
        permissions={"menu.security_users", "menu.query"},
        entitlements=[],
        actor=admin,
    )
    query_role = service.create_role(
        role_code="QUERY_SUBSET",
        display_name="SQL 生成のみ",
        description="",
        permissions={"menu.query"},
        entitlements=[],
        actor=admin,
    )
    direct_sql_role = service.create_role(
        role_code="DIRECT_SQL_NOT_ASSIGNABLE",
        display_name="SELECT SQL 実行",
        description="",
        permissions={"menu.direct_sql"},
        entitlements=[],
        actor=admin,
    )
    _create_active_user(
        service,
        admin,
        login_user_id="query.manager",
        display_name="SQL 生成ユーザー管理者",
        role_ids=[manager_role.role_id],
        password="QueryManagerPass!123",
    )

    async def exercise() -> None:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            _, csrf = await _login_api(client, "query.manager", "QueryManagerPass!123")

            roles_response = await client.get("/api/security/roles")
            assert roles_response.status_code == 200
            role_codes = {item["role_code"] for item in roles_response.json()["data"]}
            assert {"USER_AND_QUERY_MANAGER", "QUERY_SUBSET"} <= role_codes
            assert direct_sql_role.role_code not in role_codes

            created = await client.post(
                "/api/security/users",
                headers={"X-CSRF-Token": csrf},
                json={
                    "login_user_id": "query.subset.user",
                    "display_name": "SQL 生成利用者",
                    "temporary_password": "SubsetStartPass!123",
                    "role_ids": [query_role.role_id],
                },
            )
            assert created.status_code == 200

            _, csrf = await _login_api(client, "query.subset.user", "SubsetStartPass!123")
            changed = await client.post(
                "/api/auth/password/change",
                headers={"X-CSRF-Token": csrf},
                json={
                    "current_password": "SubsetStartPass!123",
                    "new_password": "SubsetActivePass!456",
                },
            )
            assert changed.status_code == 200

            current, _ = await _login_api(
                client,
                "query.subset.user",
                "SubsetActivePass!456",
            )
            assert current["role_codes"] == ["QUERY_SUBSET"]
            assert set(current["permissions"]) == {
                "menu.query",
                FEEDBACK_WRITE_PERMISSION,
                PROFILE_READ_PERMISSION,
                QUERY_GENERATE_PERMISSION,
                SCHEMA_READ_PERMISSION,
                SQL_EXECUTE_PERMISSION,
            }
            assert (await client.get("/api/nl2sql/history")).status_code == 200
            assert (await client.get("/api/security/users")).status_code == 403

    try:
        asyncio.run(exercise())
    finally:
        reset_security_service()


def test_role_permission_change_reflects_on_existing_session_next_request(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = _configure_memory_api_auth(monkeypatch)
    admin, _, _ = service.login("ADMIN", "BootstrapPass!123")
    dynamic_role = service.create_role(
        role_code="DYNAMIC_RUNTIME_ROLE",
        display_name="動的反映ロール",
        description="",
        permissions={"menu.query"},
        entitlements=[],
        actor=admin,
    )
    _create_active_user(
        service,
        admin,
        login_user_id="dynamic.user",
        display_name="動的反映ユーザー",
        role_ids=[dynamic_role.role_id],
        password="DynamicUserPass!123",
    )

    async def exercise() -> None:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            await _login_api(client, "dynamic.user", "DynamicUserPass!123")
            assert (await client.get("/api/nl2sql/history")).status_code == 200
            assert (await client.get("/api/security/users")).status_code == 403

            service.update_role(
                dynamic_role.role_id,
                expected_version=dynamic_role.version,
                display_name=dynamic_role.display_name,
                description=dynamic_role.description,
                permissions={"menu.security_users"},
                entitlements=[],
                actor=admin,
            )

            assert (await client.get("/api/security/users")).status_code == 200
            assert (await client.get("/api/nl2sql/history")).status_code == 403

    try:
        asyncio.run(exercise())
    finally:
        reset_security_service()


def test_archived_or_unassigned_role_removes_runtime_access(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = _configure_memory_api_auth(monkeypatch)
    admin, _, _ = service.login("ADMIN", "BootstrapPass!123")
    archived_runtime_role = service.create_role(
        role_code="ARCHIVED_RUNTIME_QUERY",
        display_name="アーカイブ反映",
        description="",
        permissions={"menu.query"},
        entitlements=[],
        actor=admin,
    )
    unassigned_runtime_role = service.create_role(
        role_code="UNASSIGNED_RUNTIME_QUERY",
        display_name="解除反映",
        description="",
        permissions={"menu.query"},
        entitlements=[],
        actor=admin,
    )
    archived_user = _create_active_user(
        service,
        admin,
        login_user_id="archived.runtime.user",
        display_name="アーカイブ反映ユーザー",
        role_ids=[archived_runtime_role.role_id],
        password="ArchivedRuntimePass!123",
    )
    unassigned_user = _create_active_user(
        service,
        admin,
        login_user_id="unassigned.runtime.user",
        display_name="解除反映ユーザー",
        role_ids=[unassigned_runtime_role.role_id],
        password="UnassignedRuntimePass!123",
    )

    async def exercise() -> None:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            await _login_api(client, archived_user.login_user_id, "ArchivedRuntimePass!123")
            assert (await client.get("/api/nl2sql/history")).status_code == 200
            service.archive_role(
                archived_runtime_role.role_id,
                expected_version=archived_runtime_role.version,
                actor=admin,
            )
            assert (await client.get("/api/nl2sql/history")).status_code == 403
            me_after_archive = await client.get("/api/auth/me")
            assert me_after_archive.status_code == 200
            assert me_after_archive.json()["data"]["permissions"] == []

            await _login_api(client, unassigned_user.login_user_id, "UnassignedRuntimePass!123")
            assert (await client.get("/api/nl2sql/history")).status_code == 200
            service.update_user(
                unassigned_user.user_uuid,
                expected_version=unassigned_user.version,
                display_name=unassigned_user.display_name,
                status=unassigned_user.status,
                role_ids=[],
                actor=admin,
            )
            assert (await client.get("/api/nl2sql/history")).status_code == 403
            me_after_unassign = await client.get("/api/auth/me")
            assert me_after_unassign.status_code == 200
            assert me_after_unassign.json()["data"]["permissions"] == []

    try:
        asyncio.run(exercise())
    finally:
        reset_security_service()


def test_restore_role_reactivates_existing_session_and_assigned_role_metadata(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = _configure_memory_api_auth(monkeypatch)
    admin, _, _ = service.login("ADMIN", "BootstrapPass!123")
    admin_user = service.store.get_user(admin.user_uuid)
    assert admin_user is not None
    service.store.set_password(
        admin_user.user_uuid,
        hash_password("BootstrapPass!123"),
        force_change=False,
    )
    runtime_role = service.create_role(
        role_code="RESTORE_RUNTIME_QUERY",
        display_name="復元反映",
        description="",
        permissions={"menu.query"},
        entitlements=[],
        actor=admin,
    )
    assigned_user = _create_active_user(
        service,
        admin,
        login_user_id="restore.runtime.user",
        display_name="復元反映ユーザー",
        role_ids=[runtime_role.role_id],
        password="RestoreRuntimePass!123",
    )
    archived = service.archive_role(
        runtime_role.role_id,
        expected_version=runtime_role.version,
        actor=admin,
    )

    def assigned_role_archived(payload: dict[str, object]) -> bool:
        assert payload["role_ids"] == [runtime_role.role_id]
        assigned_roles = cast(list[dict[str, object]], payload["assigned_roles"])
        assert assigned_roles[0]["role_code"] == "RESTORE_RUNTIME_QUERY"
        return bool(assigned_roles[0]["archived"])

    async def exercise() -> None:
        transport = httpx.ASGITransport(app=app)
        async with (
            httpx.AsyncClient(transport=transport, base_url="http://test") as user_client,
            httpx.AsyncClient(transport=transport, base_url="http://test") as admin_client,
        ):
            await _login_api(user_client, assigned_user.login_user_id, "RestoreRuntimePass!123")
            assert (await user_client.get("/api/nl2sql/history")).status_code == 403
            before_me = await user_client.get("/api/auth/me")
            assert before_me.status_code == 200
            assert before_me.json()["data"]["permissions"] == []

            _, csrf = await _login_api(admin_client, "ADMIN", "BootstrapPass!123")
            before_users = await admin_client.get("/api/security/users")
            assert before_users.status_code == 200
            before_user = next(
                item
                for item in before_users.json()["data"]
                if item["user_uuid"] == assigned_user.user_uuid
            )
            assert assigned_role_archived(before_user) is True

            restored = await admin_client.post(
                f"/api/security/roles/{runtime_role.role_id}/restore",
                headers={"X-CSRF-Token": csrf},
                json={"version": archived.version},
            )
            assert restored.status_code == 200
            restored_data = restored.json()["data"]
            assert restored_data["archived"] is False
            assert restored_data["version"] == archived.version + 1

            after_users = await admin_client.get("/api/security/users")
            assert after_users.status_code == 200
            after_user = next(
                item
                for item in after_users.json()["data"]
                if item["user_uuid"] == assigned_user.user_uuid
            )
            assert assigned_role_archived(after_user) is False

            after_me = await user_client.get("/api/auth/me")
            assert after_me.status_code == 200
            assert after_me.json()["data"]["role_codes"] == ["RESTORE_RUNTIME_QUERY"]
            assert set(after_me.json()["data"]["permissions"]) == {
                "menu.query",
                FEEDBACK_WRITE_PERMISSION,
                PROFILE_READ_PERMISSION,
                QUERY_GENERATE_PERMISSION,
                SCHEMA_READ_PERMISSION,
                SQL_EXECUTE_PERMISSION,
            }
            assert (await user_client.get("/api/nl2sql/history")).status_code == 200

    try:
        asyncio.run(exercise())
    finally:
        reset_security_service()


def test_restore_role_api_rejects_invalid_targets(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = _configure_memory_api_auth(monkeypatch)
    admin, _, _ = service.login("ADMIN", "BootstrapPass!123")
    admin_user = service.store.get_user(admin.user_uuid)
    assert admin_user is not None
    service.store.set_password(
        admin_user.user_uuid,
        hash_password("BootstrapPass!123"),
        force_change=False,
    )
    active_role = service.create_role(
        role_code="RESTORE_ACTIVE_TARGET",
        display_name="復元不要",
        description="",
        permissions={"menu.query"},
        entitlements=[],
        actor=admin,
    )
    archived_role = service.create_role(
        role_code="RESTORE_CONFLICT_TARGET",
        display_name="復元競合",
        description="",
        permissions={"menu.query"},
        entitlements=[],
        actor=admin,
    )
    archived = service.archive_role(
        archived_role.role_id,
        expected_version=archived_role.version,
        actor=admin,
    )
    system_role = service.store.get_role(SYSTEM_ADMIN_ROLE_ID)
    assert system_role is not None

    async def exercise() -> None:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            _, csrf = await _login_api(client, "ADMIN", "BootstrapPass!123")

            built_in = await client.post(
                f"/api/security/roles/{SYSTEM_ADMIN_ROLE_ID}/restore",
                headers={"X-CSRF-Token": csrf},
                json={"version": system_role.version},
            )
            assert built_in.status_code == 409

            missing = await client.post(
                "/api/security/roles/missing-role/restore",
                headers={"X-CSRF-Token": csrf},
                json={"version": 1},
            )
            assert missing.status_code == 404

            not_archived = await client.post(
                f"/api/security/roles/{active_role.role_id}/restore",
                headers={"X-CSRF-Token": csrf},
                json={"version": active_role.version},
            )
            assert not_archived.status_code == 409

            conflict = await client.post(
                f"/api/security/roles/{archived_role.role_id}/restore",
                headers={"X-CSRF-Token": csrf},
                json={"version": archived.version - 1},
            )
            assert conflict.status_code == 409

    try:
        asyncio.run(exercise())
    finally:
        reset_security_service()


def test_user_api_includes_archived_assigned_role_metadata_without_runtime_permission(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = _configure_memory_api_auth(monkeypatch)
    admin, _, _ = service.login("ADMIN", "BootstrapPass!123")
    admin_user = service.store.get_user(admin.user_uuid)
    assert admin_user is not None
    service.store.set_password(
        admin_user.user_uuid,
        hash_password("BootstrapPass!123"),
        force_change=False,
    )
    archived_role = service.create_role(
        role_code="ARCHIVED_METADATA_ROLE",
        display_name="アーカイブ表示ロール",
        description="",
        permissions={"menu.query"},
        entitlements=[],
        actor=admin,
    )
    assigned_user = _create_active_user(
        service,
        admin,
        login_user_id="archived.metadata.user",
        display_name="アーカイブ表示ユーザー",
        role_ids=[archived_role.role_id],
        password="ArchivedMetadataPass!123",
    )
    service.archive_role(
        archived_role.role_id,
        expected_version=archived_role.version,
        actor=admin,
    )

    def assert_archived_assignment(payload: dict[str, object]) -> None:
        assert payload["role_ids"] == [archived_role.role_id]
        assert payload["assigned_roles"] == [
            {
                "role_id": archived_role.role_id,
                "role_code": "ARCHIVED_METADATA_ROLE",
                "display_name": "アーカイブ表示ロール",
                "is_built_in": False,
                "archived": True,
            }
        ]

    async def exercise() -> None:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            _, csrf = await _login_api(client, "ADMIN", "BootstrapPass!123")
            users_response = await client.get("/api/security/users")
            assert users_response.status_code == 200
            user_rows = users_response.json()["data"]
            user_row = next(
                item for item in user_rows if item["user_uuid"] == assigned_user.user_uuid
            )
            assert_archived_assignment(user_row)

            detail = await client.get(f"/api/security/users/{assigned_user.user_uuid}")
            assert detail.status_code == 200
            assert_archived_assignment(detail.json()["data"])

            updated = await client.patch(
                f"/api/security/users/{assigned_user.user_uuid}",
                headers={"X-CSRF-Token": csrf},
                json={
                    "version": assigned_user.version,
                    "display_name": "アーカイブ表示ユーザー更新",
                    "status": "ACTIVE",
                    "role_ids": [archived_role.role_id],
                },
            )
            assert updated.status_code == 200
            updated_user = updated.json()["data"]
            assert updated_user["display_name"] == "アーカイブ表示ユーザー更新"
            assert_archived_assignment(updated_user)

            disabled = await client.post(
                f"/api/security/users/{assigned_user.user_uuid}/disable",
                headers={"X-CSRF-Token": csrf},
                json={"version": updated_user["version"]},
            )
            assert disabled.status_code == 200
            assert_archived_assignment(disabled.json()["data"])

            enabled = await client.post(
                f"/api/security/users/{assigned_user.user_uuid}/enable",
                headers={"X-CSRF-Token": csrf},
                json={"version": disabled.json()["data"]["version"]},
            )
            assert enabled.status_code == 200
            assert_archived_assignment(enabled.json()["data"])

            reset = await client.post(
                f"/api/security/users/{assigned_user.user_uuid}/reset-password",
                headers={"X-CSRF-Token": csrf},
                json={"temporary_password": "ArchivedMetadataReset!123"},
            )
            assert reset.status_code == 200
            assert_archived_assignment(reset.json()["data"]["user"])

            current, _ = await _login_api(
                client,
                "archived.metadata.user",
                "ArchivedMetadataReset!123",
            )
            assert current["role_codes"] == []
            assert current["permissions"] == []

    try:
        asyncio.run(exercise())
    finally:
        reset_security_service()


def test_user_data_marks_unresolved_assigned_role_as_inactive() -> None:
    user = UserRecord(
        user_uuid="missing-role-user",
        login_user_id="missing.role",
        display_name="ロール不明ユーザー",
        password_hash="unused",
        status="ACTIVE",
        force_password_change=False,
        failed_login_count=0,
        locked_until=None,
        version=1,
        role_ids=["missing-role-id"],
    )

    data = UserData.from_record(user)

    assert data.role_ids == ["missing-role-id"]
    assert data.assigned_roles[0].role_id == "missing-role-id"
    assert data.assigned_roles[0].display_name == "missing-role-id"
    assert data.assigned_roles[0].archived is True


def test_history_api_scopes_items_to_actor_except_system_admin(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = get_settings()
    monkeypatch.setattr(settings, "app_auth_enabled", True)
    monkeypatch.setattr(settings, "app_auth_cookie_secure", False)
    monkeypatch.setattr(settings, "oracle_user", "DBADMIN")
    monkeypatch.setattr(settings, "oracle_password", "DbAdminPass!123")
    monkeypatch.setattr(settings, "app_admin_login_user_id", "system_admin")
    monkeypatch.setattr(settings, "app_admin_login_user_password", "AppAdminPass123")
    monkeypatch.setattr(settings, "nl2sql_persistence_mode", "memory")
    _patch_security_threadpools(monkeypatch)
    reset_security_service()
    from app.security.service import get_security_service

    service = get_security_service()
    assert isinstance(service.store, InMemorySecurityStore)
    assert service.store.bootstrap(
        login_user_id="ADMIN",
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
                json={"login_user_id": "ADMIN", "password": "BootstrapPass!123"},
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
                json={"login_user_id": "ADMIN", "password": "IndependentPass!456"},
            )
            assert admin_login.status_code == 200
            admin_user_id = admin_login.json()["data"]["user_uuid"]
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
                    "login_user_id": "owner.viewer",
                    "display_name": "履歴所有者",
                    "temporary_password": "ViewerStart!123",
                    "role_ids": [role_id],
                },
            )
            assert user_response.status_code == 200
            viewer_user_id = user_response.json()["data"]["user"]["user_uuid"]

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
                    actor_user_uuid=admin_user_id,
                ),
                HistoryItem(
                    id="viewer-history",
                    question="自分の履歴",
                    engine=Nl2SqlEngine.SELECT_AI,
                    generated_sql="SELECT 1 FROM DUAL",
                    created_at="2026-08-17T00:02:00+00:00",
                    actor_user_uuid=viewer_user_id,
                ),
                HistoryItem(
                    id="other-history",
                    question="他人の履歴",
                    engine=Nl2SqlEngine.SELECT_AI,
                    generated_sql="SELECT 1 FROM DUAL",
                    created_at="2026-08-17T00:03:00+00:00",
                    actor_user_uuid="other-user",
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
                json={"login_user_id": "owner.viewer", "password": "ViewerStart!123"},
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
                json={"login_user_id": "owner.viewer", "password": "ReadOnlyPass!456"},
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
