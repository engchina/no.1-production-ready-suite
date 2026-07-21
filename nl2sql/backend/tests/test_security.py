"""Application auth/RBAC の回帰テスト。"""

from __future__ import annotations

import asyncio
import io
from collections.abc import AsyncGenerator, Callable
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import cast

import httpx
import pytest
from fastapi import HTTPException, Request, Response

from app.cli.app_security_migrate import split_ddl
from app.main import app
from app.security import dependencies as security_dependencies
from app.security.dependencies import authorize_api_request, local_debug_principal
from app.security.domain import SYSTEM_ADMIN_ROLE_ID, AuditRecord, Principal
from app.security.passwords import PasswordPolicyError, validate_password
from app.security.permissions import (
    ALL_PERMISSION_CODES,
    UNCLASSIFIED_PERMISSION,
    permission_for_route,
)
from app.security.router import (
    audit_log_page,
    change_password,
    export_audit_log_xlsx,
    logout,
    me,
)
from app.security.schemas import DataEntitlementInput, PasswordChangeRequest
from app.security.service import SecurityApiError, SecurityService, reset_security_service
from app.security.store import InMemorySecurityStore, SecurityConflict
from app.settings import Settings, get_settings


def _settings() -> Settings:
    return Settings.model_construct(
        oracle_user="ADMIN",
        oracle_password="BootstrapPass!123",
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


def _service() -> SecurityService:
    service = SecurityService(InMemorySecurityStore(), _settings())
    assert service.bootstrap() is True
    assert service.bootstrap() is False
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
    monkeypatch.setattr("app.main.run_in_threadpool", _inline_threadpool)
    monkeypatch.setattr("app.security.dependencies.run_in_threadpool", _inline_threadpool)
    monkeypatch.setattr("app.security.router.run_in_threadpool", _inline_threadpool)


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
        current = await me(request)
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
        }
        logged_out = await logout(request, Response())
        assert logged_out.data == {"logged_out": False}
        with pytest.raises(SecurityApiError, match="DEBUG"):
            await change_password(
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

    async def inline_threadpool(
        function: Callable[..., object], *args: object
    ) -> object:
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


def test_data_entitlement_capability_is_structured() -> None:
    with pytest.raises(ValueError, match="capability"):
        DataEntitlementInput(
            resource_code="NL2SQL_DEEPSEC_PROBE",
            scope_code="SALES",
            capability="ARBITRARY_SQL",
        )


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
    assert principal.permissions >= {"search.view", "search.execute"}
    assert {
        (item.scope_code, item.capability) for item in principal.data_entitlements
    } == {("SALES", "ROW_READ"), ("SALES", "SENSITIVE_READ")}


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


def test_audit_store_pages_ten_rows_and_clamps_to_last_page() -> None:
    store = InMemorySecurityStore()
    base = datetime(2026, 7, 20, tzinfo=UTC)
    store.audit = [
        AuditRecord(
            audit_id=index,
            actor_user_id=f"actor-{index}",
            event_type=f"EVENT_{index}",
            target_type="USER",
            target_id=f"target-{index}",
            outcome="SUCCESS",
            detail={},
            request_id=f"request-{index}",
            client_ip="127.0.0.1",
            created_at=base + timedelta(minutes=index),
        )
        for index in range(1, 13)
    ]

    first_page, total, resolved_page = store.page_audit(page=1, page_size=10)
    assert [record.audit_id for record in first_page] == list(range(12, 2, -1))
    assert total == 12
    assert resolved_page == 1

    last_page, total, resolved_page = store.page_audit(page=99, page_size=10)
    assert [record.audit_id for record in last_page] == [2, 1]
    assert total == 12
    assert resolved_page == 2

    empty_page, total, resolved_page = InMemorySecurityStore().page_audit(
        page=99, page_size=10
    )
    assert empty_page == []
    assert total == 0
    assert resolved_page == 1


def test_audit_export_contains_rolling_year_full_fields_and_literal_text() -> None:
    service = SecurityService(InMemorySecurityStore(), _settings())
    store = cast(InMemorySecurityStore, service.store)
    now = datetime(2026, 7, 20, 3, 4, 5, tzinfo=UTC)
    start_at = datetime(2025, 7, 20, 3, 4, 5, tzinfo=UTC)
    store.audit = [
        AuditRecord(
            audit_id=1,
            actor_user_id=None,
            event_type="BOUNDARY_EVENT",
            target_type="SYSTEM",
            target_id="NL2SQL",
            outcome="SUCCESS",
            detail={"境界": True},
            request_id="boundary-request",
            client_ip="127.0.0.1",
            created_at=start_at,
        ),
        AuditRecord(
            audit_id=2,
            actor_user_id="actor-2",
            event_type='=HYPERLINK("https://invalid.example")',
            target_type="USER",
            target_id="target-2",
            outcome="DENIED",
            detail={"message": "日本語"},
            request_id="request-2",
            client_ip="192.0.2.10",
            created_at=now,
        ),
        AuditRecord(
            audit_id=3,
            actor_user_id="old",
            event_type="TOO_OLD",
            target_type="USER",
            target_id="old",
            outcome="SUCCESS",
            detail={},
            request_id="old",
            client_ip="192.0.2.11",
            created_at=start_at - timedelta(microseconds=1),
        ),
        AuditRecord(
            audit_id=4,
            actor_user_id="future",
            event_type="FUTURE",
            target_type="USER",
            target_id="future",
            outcome="SUCCESS",
            detail={},
            request_id="future",
            client_ip="192.0.2.12",
            created_at=now + timedelta(microseconds=1),
        ),
    ]

    filename, content = service.export_audit_log_xlsx(now=now)
    assert filename == "nl2sql_audit_logs_20250720-20260720.xlsx"

    openpyxl = pytest.importorskip("openpyxl")
    workbook = openpyxl.load_workbook(io.BytesIO(content), data_only=False)
    sheet = workbook["audit_logs"]
    assert [cell.value for cell in sheet[1]] == [
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
    ]
    assert sheet.max_row == 3
    assert sheet["A2"].value == 2
    assert sheet["B2"].value == datetime(2026, 7, 20, 12, 4, 5)
    assert sheet["B2"].number_format == "yyyy-mm-dd hh:mm:ss"
    assert sheet["C2"].value == '=HYPERLINK("https://invalid.example")'
    assert sheet["C2"].data_type == "s"
    assert sheet["J2"].value == '{"message":"日本語"}'
    assert sheet["A3"].value == 1
    assert sheet.freeze_panes == "A2"
    assert sheet.auto_filter.ref == "A1:J3"


def test_audit_page_and_export_router_contract(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = SecurityService(InMemorySecurityStore(), _settings())
    store = cast(InMemorySecurityStore, service.store)
    now = datetime.now(UTC)
    store.audit = [
        AuditRecord(
            audit_id=index,
            actor_user_id=None,
            event_type=f"EVENT_{index}",
            target_type="SYSTEM",
            target_id="NL2SQL",
            outcome="SUCCESS",
            detail={},
            request_id=f"request-{index}",
            client_ip="127.0.0.1",
            created_at=now + timedelta(seconds=index),
        )
        for index in range(1, 13)
    ]
    monkeypatch.setattr("app.security.router.get_security_service", lambda: service)
    monkeypatch.setattr("app.security.router.run_in_threadpool", _inline_threadpool)

    page_response = asyncio.run(audit_log_page(page=99, page_size=10))
    assert page_response.data is not None
    assert page_response.data.page == 2
    assert page_response.data.page_size == 10
    assert page_response.data.total == 12
    assert page_response.data.total_pages == 2
    assert [record.audit_id for record in page_response.data.items] == [2, 1]

    export_response = asyncio.run(export_audit_log_xlsx())
    assert export_response.media_type == (
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    )
    assert export_response.headers["content-disposition"].startswith(
        'attachment; filename="nl2sql_audit_logs_'
    )
    assert bytes(export_response.body).startswith(b"PK")


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
                assert permission != UNCLASSIFIED_PERMISSION, (
                    f"unclassified route: {method.upper()} {path}"
                )


def test_auth_api_sets_http_only_session_and_requires_csrf(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_security_threadpools(monkeypatch)
    settings = get_settings()
    monkeypatch.setattr(settings, "app_auth_enabled", True)
    monkeypatch.setattr(settings, "app_auth_cookie_secure", False)
    monkeypatch.setattr(settings, "oracle_user", "ADMIN")
    monkeypatch.setattr(settings, "oracle_password", "BootstrapPass!123")
    monkeypatch.setattr(settings, "nl2sql_persistence_mode", "memory")
    reset_security_service()
    from app.security.service import get_security_service

    get_security_service().bootstrap()

    async def exercise() -> None:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            unauthenticated = await client.get("/api/auth/me")
            assert unauthenticated.status_code == 401

            login = await client.post(
                "/api/auth/login",
                json={"login_name": "ADMIN", "password": "BootstrapPass!123"},
            )
            assert login.status_code == 200
            assert "HttpOnly" in login.headers.get_list("set-cookie")[0]
            me = await client.get("/api/auth/me")
            assert me.status_code == 200
            assert me.json()["data"]["role_codes"] == ["SYSTEM_ADMIN"]

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


def test_api_enforces_view_and_execute_permissions_independently(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_security_threadpools(monkeypatch)
    settings = get_settings()
    monkeypatch.setattr(settings, "app_auth_enabled", True)
    monkeypatch.setattr(settings, "app_auth_cookie_secure", False)
    monkeypatch.setattr(settings, "oracle_user", "ADMIN")
    monkeypatch.setattr(settings, "oracle_password", "BootstrapPass!123")
    monkeypatch.setattr(settings, "nl2sql_persistence_mode", "memory")
    reset_security_service()

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
                    "role_code": "QUERY_VIEWER",
                    "display_name": "検索閲覧",
                    "permissions": ["search.view"],
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
                    "display_name": "検索閲覧ユーザー",
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
            assert (await client.get("/api/security/audit/page")).status_code == 403
            assert (await client.get("/api/security/audit/export.xlsx")).status_code == 403

    try:
        asyncio.run(exercise())
    finally:
        reset_security_service()
