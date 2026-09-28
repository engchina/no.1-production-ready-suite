"""Agent の認証/RBAC の API テスト（#215）。

- production mode のログイン（DB ユーザー・構成管理者）・CSRF・強制パスワード変更
- 権限の既定拒否（manifest 未登録・権限なし 403）と manifest の完全性
- production で Cookie がないリクエスト（AGENT_RBAC_ENABLED の有無、`POST /mcp/{binding_id}`）
- `/security/*`（権限カタログ・対象の一覧・ロールの権限と対象範囲の保存と昇格防止）
- `agent_security_migrate` の DDL と廃止した権限コードの削除（fake connection）
- 廃止した権限コード（`menu.dashboard`。#262）が DB に残っていても壊れないこと
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager, suppress
from types import SimpleNamespace
from typing import Any

import pytest
from pytest import MonkeyPatch
from security_support import (
    CONFIGURED_ADMIN_PASSWORD,
    USER_PASSWORD,
    ProductionAuth,
    client,
    enable_production_auth,
    enable_signed_identity,
    login,
    login_configured_admin,
    session_headers,
    signed_identity_headers,
)

import app.features.agent.router as agent_router
from app.cli import agent_security_migrate
from app.features.agent.control_plane import RuntimeBinding, runtime_binding_registry
from app.features.agent.runtime import AgentProfile, RunCreateRequest, runtime_repository
from app.main import app
from app.security import dependencies as security_dependencies
from app.security.migrations import AGENT_SECURITY_DDL
from app.security.permissions import (
    ALL_PERMISSION_CODES,
    AUTHENTICATED_WITHOUT_PERMISSION,
    CAPABILITY_ROLES,
    EXTERNAL_ROLE_READ_MENUS,
    PERMISSION_CATALOG,
    PUBLIC_API_PATHS,
    RETIRED_PERMISSION_CODES,
    ROUTE_PERMISSIONS,
    UNCLASSIFIED_PERMISSION,
    WEBSOCKET_PERMISSIONS,
    expand_permissions,
    permission_for_route,
    permissions_for_roles,
)
from app.security.service import set_security_service
from app.settings import get_settings

HTTP_METHODS = {"GET", "POST", "PUT", "PATCH", "DELETE"}


@pytest.fixture
def auth(monkeypatch: MonkeyPatch) -> Iterator[ProductionAuth]:
    yield enable_production_auth(monkeypatch)
    set_security_service(None)


@pytest.fixture
def auth_with_external_rbac(monkeypatch: MonkeyPatch) -> Iterator[ProductionAuth]:
    yield enable_production_auth(monkeypatch, rbac_enabled=True)
    set_security_service(None)


@contextmanager
def _agent(agent_id: str, name: str | None = None) -> Iterator[AgentProfile]:
    agent = runtime_repository.create_agent(AgentProfile(id=agent_id, name=name or agent_id))
    try:
        yield agent
    finally:
        with suppress(KeyError, ValueError):
            runtime_repository.delete_agent(agent_id)


# ---------------------------------------------------------------------------
# manifest
# ---------------------------------------------------------------------------


def _api_operations() -> list[tuple[str, str]]:
    operations: list[tuple[str, str]] = []
    for path, methods in app.openapi()["paths"].items():
        if not path.startswith("/api"):
            continue
        for method in methods:
            if method.upper() in HTTP_METHODS:
                operations.append((method.upper(), path.removeprefix("/api")))
    return operations


def test_every_api_route_is_classified_by_manifest() -> None:
    """全 API（method × path）が manifest に登録されている（登録外は既定で拒否）。"""
    operations = _api_operations()
    assert len(operations) > 120
    unclassified = [
        f"{method} {path}"
        for method, path in operations
        if (permissions := permission_for_route(method, path)) is not None
        and UNCLASSIFIED_PERMISSION in permissions
    ]
    assert unclassified == []
    open_routes = {
        path for method, path in operations if permission_for_route(method, path) is None
    }
    assert open_routes == set(PUBLIC_API_PATHS) | set(AUTHENTICATED_WITHOUT_PERMISSION)


def test_manifest_entries_match_existing_routes_and_known_permissions() -> None:
    """manifest の明示登録は実在する route だけで、権限コードはカタログにあるもの。"""
    operations = set(_api_operations())
    assert set(ROUTE_PERMISSIONS) - operations == set()
    for permissions in [*ROUTE_PERMISSIONS.values(), *WEBSOCKET_PERMISSIONS.values()]:
        assert permissions
        assert permissions <= ALL_PERMISSION_CODES
    websocket_paths = {
        str(getattr(route, "path", ""))
        for route in agent_router.router.routes
        if type(route).__name__ == "APIWebSocketRoute"
    }
    assert websocket_paths == set(WEBSOCKET_PERMISSIONS)


def test_manifest_denies_unknown_routes_by_default() -> None:
    assert permission_for_route("GET", "/unknown") == frozenset({UNCLASSIFIED_PERMISSION})
    assert permission_for_route("DELETE", "/runs") == frozenset({UNCLASSIFIED_PERMISSION})
    assert permission_for_route("POST", "/security/permissions") == frozenset(
        {UNCLASSIFIED_PERMISSION}
    )


def _perm(method: str, path: str) -> set[str]:
    return set(permission_for_route(method, path) or ())


def test_manifest_key_assignments() -> None:
    """変更系は capability（従来のロール）、読み取りは画面のメニュー権限。"""
    assert _perm("POST", "/agents") == {"agent.admin"}
    assert _perm("PATCH", "/settings/oci") == {"menu.settings_oci"}
    assert _perm("POST", "/settings/oci/object-storage/namespace") == {
        "menu.settings_oci",
        "menu.settings_upload_storage",
    }
    assert _perm("PATCH", "/settings/model") == {"menu.settings_model"}
    assert _perm("POST", "/settings/database/adb/start") == {"menu.settings_database"}
    assert _perm("PATCH", "/settings/external-rag") == {"agent.admin"}
    assert _perm("PATCH", "/settings/tool-policy") == {"agent.admin"}
    assert _perm("POST", "/runs") == {"agent.runs.operate", "agent.admin"}
    assert _perm("POST", "/runs/{run_id}/cancel") == {"agent.runs.operate", "agent.admin"}
    assert _perm("POST", "/approvals/{approval_id}/decision") == {
        "agent.approvals.decide",
        "agent.admin",
    }
    assert _perm("GET", "/runs") == {"menu.runs", "menu.approvals"}
    assert _perm("GET", "/tools") == {"menu.audit", "agent.admin"}
    assert _perm("GET", "/observability/status") == {"menu.audit"}
    assert _perm("GET", "/settings/external-rag") == {"menu.settings_external_rag"}
    assert _perm("GET", "/audit/tool-calls") == {"menu.audit"}
    assert _perm("GET", "/settings/oci") == {"menu.settings_oci"}
    assert _perm("GET", "/security/roles/{role_id}") == {
        "menu.security_users",
        "menu.security_roles",
        "menu.security_permissions",
    }
    assert _perm("POST", "/security/roles") == {"menu.security_roles"}
    assert _perm("PATCH", "/security/users/{user_uuid}") == {"menu.security_users"}
    assert _perm("PUT", "/security/roles/{role_id}/access") == {"menu.security_permissions"}
    assert permission_for_route("POST", "/mcp/{binding_id}") is None


def test_retired_permission_codes_are_not_in_catalog_or_manifest() -> None:
    """廃止した `menu.dashboard`（#262）はカタログ・implies・manifest・外部ロールに現れない。"""
    assert RETIRED_PERMISSION_CODES == ("menu.dashboard",)
    retired = set(RETIRED_PERMISSION_CODES)
    assert not retired & ALL_PERMISSION_CODES
    assert all(not retired & set(item.implies) for item in PERMISSION_CATALOG)
    for permissions in [*ROUTE_PERMISSIONS.values(), *WEBSOCKET_PERMISSIONS.values()]:
        assert not retired & permissions
    assert not retired & EXTERNAL_ROLE_READ_MENUS
    # 外部連携のロールも廃止コードを受け取らない。
    assert not retired & permissions_for_roles({"admin", "viewer"})
    assert expand_permissions({"menu.dashboard"}) == set()


def test_capabilities_map_to_legacy_roles_and_imply_menus() -> None:
    assert CAPABILITY_ROLES == {
        "agent.runs.view": "viewer",
        "agent.runs.operate": "operator",
        "agent.approvals.decide": "approver",
        "agent.audit.view": "auditor",
        "agent.admin": "admin",
    }
    assert expand_permissions({"agent.runs.view"}) == {"agent.runs.view", "menu.runs"}
    assert "menu.approvals" in expand_permissions({"agent.approvals.decide"})
    assert "menu.audit" in expand_permissions({"agent.audit.view"})
    admin = expand_permissions({"agent.admin"})
    assert {"menu.agents", "menu.settings_oci", "menu.settings_runtime_snapshot"} <= admin
    # ユーザーとロール・権限管理は agent.admin に含めない。
    assert not {"menu.security_users", "menu.security_roles", "menu.security_permissions"} & admin
    # メニュー権限は capability を暗黙に含まない（昇格しない）。
    assert expand_permissions({"menu.runs"}) == {"menu.runs"}
    assert expand_permissions({"unknown.code"}) == set()
    # 外部連携のロールは capability と Control Plane の読み取りメニュー。未知のロールは空。
    assert "agent.runs.operate" in permissions_for_roles({"Operator"})
    assert "menu.security_users" not in permissions_for_roles({"admin"})
    assert permissions_for_roles({"guest"}) == set()


def test_unclassified_route_is_denied_even_for_system_admin(
    monkeypatch: MonkeyPatch, auth: ProductionAuth
) -> None:
    """manifest が未登録を返す route は、ログイン済みでも 403（SYSTEM_ADMIN も同じ）。"""
    headers = login_configured_admin()
    assert client.get("/api/runs", headers=headers).status_code == 200
    monkeypatch.setattr(
        security_dependencies,
        "permission_for_route",
        lambda method, path: frozenset({UNCLASSIFIED_PERMISSION}),
    )
    response = client.get("/api/runs", headers=headers)
    assert response.status_code == 403
    assert response.json()["error_messages"] == ["この API は権限一覧に登録されていません。"]


# ---------------------------------------------------------------------------
# production mode のログイン
# ---------------------------------------------------------------------------


def test_production_without_cookie_and_rbac_disabled_is_401(auth: ProductionAuth) -> None:
    """Cookie がなく外部連携の RBAC も無効なら、どの保護 API も 401（全開放にしない）。"""
    for method, path in (
        ("GET", "/api/runs"),
        ("GET", "/api/agents"),
        ("GET", "/api/tools"),
        ("GET", "/api/observability/status"),
        ("GET", "/api/memory/search"),
        ("GET", "/api/settings/external-rag"),
        ("GET", "/api/settings/oci"),
        ("GET", "/api/security/permissions"),
        ("POST", "/api/runs"),
    ):
        response = client.request(method, path, headers={"X-Agent-Roles": "admin"})
        assert response.status_code == 401, (method, path, response.text)
    assert client.get("/api/runs").json()["error_messages"] == ["ログインしてください。"]


def test_production_public_paths_do_not_require_login(
    auth: ProductionAuth, monkeypatch: MonkeyPatch
) -> None:
    assert client.get("/api/health").status_code == 200
    assert client.get("/api/ready").status_code == 200
    # DB の状態 API は画面の DB ゲートがログイン前に使う（#325）。接続設定が無ければ接続を試さない。
    monkeypatch.setattr(
        agent_router,
        "get_settings",
        lambda: SimpleNamespace(oracle_user="", oracle_dsn="", oracle_wallet_dir=""),
    )
    response = client.get("/api/ready/database")
    assert response.status_code == 200
    assert response.json()["data"]["status"] == "not_configured"
    # 未定義の path は認証の前に 404。
    assert client.get("/api/not-defined").status_code == 404


def test_database_user_login_me_and_logout(auth: ProductionAuth) -> None:
    user = auth.user_with_permissions(
        "operator1",
        ["agent.runs.operate", "menu.agents"],
        agent_ids=["default"],
        business_view_ids=["bv-1"],
    )
    login_response = client.post(
        "/api/auth/login", json={"login_user_id": "operator1", "password": USER_PASSWORD}
    )
    assert login_response.status_code == 200
    data = login_response.json()["data"]
    assert data["user_uuid"] == user.user_uuid
    assert data["is_system_admin"] is False
    # implies を展開した実効権限。
    assert set(data["permissions"]) == {
        "agent.runs.operate",
        "menu.agents",
        "menu.runs",
    }
    assert data["allowed_agent_ids"] == ["default"]
    assert data["allowed_business_view_ids"] == ["bv-1"]
    assert data["debug_mode"] is False
    assert data["password_change_allowed"] is True
    headers = session_headers(login_response)
    set_cookie = login_response.headers.get_list("set-cookie")
    assert any(item.startswith("agent_session=") and "HttpOnly" in item for item in set_cookie)
    assert any(item.startswith("agent_csrf=") for item in set_cookie)

    me = client.get("/api/auth/me", headers=headers)
    assert me.status_code == 200
    assert me.json()["data"]["login_user_id"] == "operator1"

    logout = client.post("/api/auth/logout", headers=headers)
    assert logout.status_code == 200
    assert client.get("/api/auth/me", headers=headers).status_code == 401


def test_login_rejects_invalid_credentials(auth: ProductionAuth) -> None:
    auth.user_with_permissions("viewer1", ["agent.runs.view"])
    response = client.post(
        "/api/auth/login", json={"login_user_id": "viewer1", "password": "wrong-password"}
    )
    assert response.status_code == 401
    assert response.json()["error_messages"] == [
        "ログインユーザーIDまたはパスワードを確認してください。"
    ]
    assert response.json()["error_code"] == "SECURITY_AUTHENTICATION_REQUIRED"


def test_configured_system_admin_login_has_all_permissions_and_no_scope(
    auth: ProductionAuth,
) -> None:
    response = client.post(
        "/api/auth/login",
        json={"login_user_id": "system_admin", "password": CONFIGURED_ADMIN_PASSWORD},
    )
    assert response.status_code == 200
    data = response.json()["data"]
    assert data["is_system_admin"] is True
    assert set(data["permissions"]) == set(ALL_PERMISSION_CODES)
    assert data["allowed_agent_ids"] is None
    assert data["allowed_business_view_ids"] is None
    # 構成管理者の token は Agent 固有の接頭辞（他製品の token と混ざらない）。
    assert str(response.cookies.get("agent_session")).startswith("agent-system-admin-v1.")
    headers = session_headers(response)
    assert client.get("/api/runs", headers=headers).status_code == 200
    assert client.get("/api/runtime/snapshot", headers=headers).status_code == 200


def test_invalid_session_cookie_is_401_even_with_external_rbac(
    auth_with_external_rbac: ProductionAuth,
) -> None:
    """Cookie があるときは Cookie で判定し、header の RBAC に切り替えない。"""
    response = client.get(
        "/api/runs",
        headers={"cookie": "agent_session=tampered", "X-Agent-Roles": "admin"},
    )
    assert response.status_code == 401


def test_permission_denied_returns_403(auth: ProductionAuth) -> None:
    auth.user_with_permissions("auditor1", ["agent.audit.view"])
    auth.user_with_permissions("admin1", ["agent.admin"])
    auditor = login("auditor1")
    response = client.get("/api/settings/external-mcp", headers=auditor)
    assert response.status_code == 403
    assert response.json()["error_messages"] == ["この機能を利用する権限がありません。"]
    assert client.get("/api/audit/tool-calls", headers=auditor).status_code == 200
    admin = login("admin1")
    assert client.get("/api/settings/external-mcp", headers=admin).status_code == 200


def test_menu_without_capability_can_open_page_but_not_read_runs(auth: ProductionAuth) -> None:
    """Run 画面のメニューだけでは Run の実データは読めない（capability が必要）。"""
    auth.user_with_permissions("menu-only", ["menu.runs"])
    headers = login("menu-only")
    response = client.get("/api/runs", headers=headers)
    assert response.status_code == 403
    assert "requires one of roles" in response.json()["error_messages"][0]


def test_user_without_roles_is_denied_everything_except_auth(auth: ProductionAuth) -> None:
    """ロールのない利用者は既定で何もできない（ログイン系だけ使える）。"""
    auth.create_user("nobody")
    headers = login("nobody")
    assert client.get("/api/auth/me", headers=headers).status_code == 200
    for method, path in (
        ("GET", "/api/runs"),
        ("GET", "/api/agents"),
        ("GET", "/api/skills"),
        ("GET", "/api/tools"),
        ("GET", "/api/memory/search"),
        ("GET", "/api/security/permissions"),
        ("GET", "/api/security/users"),
        ("GET", "/api/settings/oci"),
    ):
        assert client.request(method, path, headers=headers).status_code == 403, path


def test_state_changing_request_requires_csrf_token(auth: ProductionAuth) -> None:
    auth.user_with_permissions("security-admin", ["menu.security_roles"])
    headers = login("security-admin")
    body = {"role_code": "READER", "display_name": "閲覧者", "description": ""}
    without_csrf = {"cookie": headers["cookie"]}
    response = client.post("/api/security/roles", json=body, headers=without_csrf)
    assert response.status_code == 403
    assert "安全性" in response.json()["error_messages"][0]
    wrong_csrf = {**headers, "X-CSRF-Token": "tampered"}
    assert client.post("/api/security/roles", json=body, headers=wrong_csrf).status_code == 403
    created = client.post("/api/security/roles", json=body, headers=headers)
    assert created.status_code == 200
    data = created.json()["data"]
    assert data["permissions"] == []
    assert data["agent_ids"] == []
    assert data["business_view_ids"] == []


def test_forced_password_change_blocks_other_apis(auth: ProductionAuth) -> None:
    role = auth.create_role(["agent.runs.view"])
    auth.create_user("new-user", [role], force_password_change=True)
    headers = login("new-user")
    assert client.get("/api/auth/me", headers=headers).status_code == 200
    response = client.get("/api/runs", headers=headers)
    assert response.status_code == 403
    assert response.json()["error_messages"] == ["初回パスワード変更を完了してください。"]
    changed = client.post(
        "/api/auth/password/change",
        json={"current_password": USER_PASSWORD, "new_password": "NewPassword!2026x"},
        headers=headers,
    )
    assert changed.status_code == 200
    relogin = login("new-user", "NewPassword!2026x")
    assert client.get("/api/runs", headers=relogin).status_code == 200


def test_local_mode_uses_all_permissions_without_login() -> None:
    """local は全権限・対象範囲の制限なしのローカル利用者（ログイン不要）。"""
    assert get_settings().auth_mode == "local"
    response = client.get("/api/auth/me")
    assert response.status_code == 200
    data = response.json()["data"]
    assert data["debug_mode"] is True
    assert data["is_system_admin"] is True
    assert set(data["permissions"]) == set(ALL_PERMISSION_CODES)
    assert data["allowed_agent_ids"] is None
    assert data["allowed_business_view_ids"] is None
    assert data["password_change_allowed"] is False
    # 従来どおり API はログインなしで使える。
    assert client.get("/api/runs").status_code == 200


# ---------------------------------------------------------------------------
# production で Cookie のないリクエスト（外部連携）
# ---------------------------------------------------------------------------


def test_production_without_cookie_rejects_self_declared_headers(
    monkeypatch: MonkeyPatch, auth_with_external_rbac: ProductionAuth
) -> None:
    """信頼できる identity がなければ、AGENT_RBAC_ENABLED=true でも自己申告の header は 401。"""
    monkeypatch.setattr(
        get_settings(),
        "agent_rbac_actor_policies_json",
        '{"ops": {"roles": ["admin"]}}',
    )
    for headers in (
        {},
        {"X-Agent-Roles": "admin"},
        {"X-Agent-Roles": "viewer", "X-Agent-Business-Views": "*"},
        {"X-Agent-Actor": "ops"},
    ):
        for method, path in (("GET", "/api/runs"), ("GET", "/api/settings/oci")):
            response = client.request(method, path, headers=headers)
            assert response.status_code == 401, (headers, path)
    assert (
        client.post("/api/runs", json={"goal": "x"}, headers={"X-Agent-Roles": "admin"}).status_code
        == 401
    )


def test_untrusted_external_rbac_warning() -> None:
    from types import SimpleNamespace

    from app.security.dependencies import untrusted_external_rbac_warning

    def settings(**overrides: object) -> SimpleNamespace:
        values: dict[str, object] = {
            "app_auth_enabled": True,
            "agent_rbac_enabled": True,
            "agent_rbac_identity_hmac_secret": None,
            "agent_rbac_jwt_bearer_enabled": False,
            "agent_rbac_policy_url": None,
        }
        values.update(overrides)
        return SimpleNamespace(**values)

    warning = untrusted_external_rbac_warning(settings())
    assert warning is not None and "AGENT_RBAC_IDENTITY_HMAC_SECRET" in warning
    assert untrusted_external_rbac_warning(settings(app_auth_enabled=False)) is None
    assert untrusted_external_rbac_warning(settings(agent_rbac_enabled=False)) is None
    assert untrusted_external_rbac_warning(settings(agent_rbac_identity_hmac_secret="s")) is None
    assert untrusted_external_rbac_warning(settings(agent_rbac_jwt_bearer_enabled=True)) is None
    assert untrusted_external_rbac_warning(settings(agent_rbac_policy_url="https://p")) is None


def test_production_without_cookie_uses_signed_identity_when_enabled(
    monkeypatch: MonkeyPatch, auth_with_external_rbac: ProductionAuth
) -> None:
    """Cookie がなく信頼できる identity があれば、そのロールで判定する（外部連携用）。"""
    enable_signed_identity(monkeypatch)
    assert client.get("/api/runs").status_code == 401
    # 署名のない自己申告は使わない。
    assert client.get("/api/runs", headers={"X-Agent-Roles": "admin"}).status_code == 401
    assert (
        client.get("/api/runs", headers=signed_identity_headers(roles=["guest"])).status_code == 401
    )
    viewer = signed_identity_headers(sub="svc-viewer", roles=["viewer"], business_view_ids=["*"])
    assert client.get("/api/runs", headers=viewer).status_code == 200
    assert client.get("/api/agents", headers=viewer).status_code == 200
    # 変更系は capability（admin / operator）が必要。
    assert client.patch("/api/settings/tool-policy", json={}, headers=viewer).status_code == 403
    assert client.get("/api/settings/oci", headers=viewer).status_code == 403
    operator = {**signed_identity_headers(roles=["operator"]), "X-Agent-API-Version": "1"}
    created = client.post(
        "/api/runs", json={"goal": "外部連携の Run", "metadata": {}}, headers=operator
    )
    assert created.status_code == 200, created.text
    admin = signed_identity_headers(roles=["admin"])
    assert client.get("/api/settings/oci", headers=admin).status_code == 200
    # ログインだけで使える API とユーザー管理は Cookie のセッションが必要。
    assert client.get("/api/auth/me", headers=admin).status_code == 401
    assert client.get("/api/security/users", headers=admin).status_code == 403


def test_production_without_cookie_uses_jwt_when_enabled(
    monkeypatch: MonkeyPatch, auth_with_external_rbac: ProductionAuth
) -> None:
    import base64
    import hashlib
    import hmac
    import json

    settings = get_settings()
    monkeypatch.setattr(settings, "agent_rbac_jwt_bearer_enabled", True)
    monkeypatch.setattr(settings, "agent_rbac_jwt_hs256_secret", "jwt-secret-for-test")

    def b64(value: bytes) -> str:
        return base64.urlsafe_b64encode(value).decode("ascii").rstrip("=")

    header = b64(json.dumps({"alg": "HS256", "typ": "JWT"}).encode())
    payload = b64(json.dumps({"sub": "svc", "roles": ["auditor"]}).encode())
    signature = b64(
        hmac.new(b"jwt-secret-for-test", f"{header}.{payload}".encode(), hashlib.sha256).digest()
    )
    bearer = {"Authorization": f"Bearer {header}.{payload}.{signature}"}
    assert client.get("/api/audit/tool-calls", headers=bearer).status_code == 200
    assert client.post("/api/runs", json={"goal": "x"}, headers=bearer).status_code == 403
    # 署名が不正ならロールなし（401）。header のロールも信頼しない。
    forged = {"Authorization": f"Bearer {header}.{payload}.bad", "X-Agent-Roles": "admin"}
    assert client.get("/api/audit/tool-calls", headers=forged).status_code == 401


def test_binding_mcp_endpoint_uses_token_without_cookie(
    monkeypatch: MonkeyPatch, auth: ProductionAuth
) -> None:
    """`POST /mcp/{binding_id}` は Cookie・manifest の対象外で、Binding の token だけで通る。"""
    binding = RuntimeBinding(
        id="binding-sec-215",
        agent_id="default",
        runtime_id="hermes-default",
        native_agent_ref="agent",
        enabled=True,
    )
    monkeypatch.setattr(runtime_binding_registry, "get", lambda binding_id: binding)
    monkeypatch.setenv("AGENT_BINDING_MCP_TOKEN_BINDING_SEC_215", "binding-token-215")
    body = {"jsonrpc": "2.0", "id": 1, "method": "initialize"}
    denied = client.post("/api/mcp/binding-sec-215", json=body)
    assert denied.status_code == 401
    allowed = client.post(
        "/api/mcp/binding-sec-215",
        json=body,
        headers={"Authorization": "Bearer binding-token-215"},
    )
    assert allowed.status_code == 200, allowed.text
    assert allowed.json()["result"]["serverInfo"]["name"] == "production-ready-agent-control-plane"


# ---------------------------------------------------------------------------
# 権限管理 API
# ---------------------------------------------------------------------------


def test_permission_catalog_lists_menus_and_capabilities(auth: ProductionAuth) -> None:
    auth.user_with_permissions("perm-admin", ["menu.security_permissions"])
    headers = login("perm-admin")
    response = client.get("/api/security/permissions", headers=headers)
    assert response.status_code == 200
    codes = [item["code"] for item in response.json()["data"]]
    assert codes == [item.code for item in PERMISSION_CATALOG]
    admin = next(item for item in response.json()["data"] if item["code"] == "agent.admin")
    assert admin["group"] == "実行・承認・管理の権限"
    assert "menu.agents" in admin["implies"]


def test_access_targets_lists_agents_and_business_views(auth: ProductionAuth) -> None:
    role = auth.create_role([], business_view_ids=["bv-assigned"])
    del role
    with _agent("agent-target-215", "対象 Agent"):
        runtime_repository.create_run(
            RunCreateRequest(goal="bv を記録する", metadata={"business_view_id": "bv-from-run"})
        )
        headers = login_configured_admin()
        response = client.get("/api/security/access-targets", headers=headers)
        assert response.status_code == 200
        data = response.json()["data"]
        agents = {item["id"]: item for item in data["agents"]}
        assert agents["agent-target-215"]["name"] == "対象 Agent"
        assert agents["agent-target-215"]["status"] == "enabled"
        assert "default" in agents
        view_ids = [item["id"] for item in data["business_views"]]
        assert {"bv-assigned", "bv-from-run"} <= set(view_ids)
        assert all(item["name"] == item["id"] for item in data["business_views"])

        # 範囲が制限された利用者には範囲内だけを見せる。
        auth.user_with_permissions(
            "scoped-perm-admin",
            ["menu.security_permissions"],
            agent_ids=["default"],
            business_view_ids=["bv-from-run"],
        )
        scoped = login("scoped-perm-admin")
        data = client.get("/api/security/access-targets", headers=scoped).json()["data"]
        assert [item["id"] for item in data["agents"]] == ["default"]
        assert [item["id"] for item in data["business_views"]] == ["bv-from-run"]


def _put_access(headers: dict[str, str], role_id: str, **body: Any) -> Any:
    return client.put(f"/api/security/roles/{role_id}/access", json=body, headers=headers)


def test_update_role_access_saves_permissions_and_targets(auth: ProductionAuth) -> None:
    target = auth.create_role([])
    headers = login_configured_admin()
    response = _put_access(
        headers,
        target.role_id,
        version=1,
        permissions=["agent.runs.view", "menu.agents"],
        agent_ids=["default"],
        business_view_ids=["bv-1", "sales:q3"],
    )
    assert response.status_code == 200, response.text
    data = response.json()["data"]
    assert data["permissions"] == ["agent.runs.view", "menu.agents"]
    assert data["agent_ids"] == ["default"]
    assert data["business_view_ids"] == ["bv-1", "sales:q3"]
    assert response.headers["ETag"] == '"2"'
    # agent.admin を含むロールは対象を空に正規化する（制限を受けない）。
    admin_role = _put_access(
        headers,
        target.role_id,
        version=2,
        permissions=["agent.admin"],
        agent_ids=["default"],
        business_view_ids=["bv-1"],
    )
    assert admin_role.status_code == 200
    assert admin_role.json()["data"]["agent_ids"] == []
    assert admin_role.json()["data"]["business_view_ids"] == []
    # GET /security/roles にも Agent の権限と対象が出る。
    listed = client.get("/api/security/roles", headers=headers).json()["data"]
    role = next(item for item in listed if item["role_id"] == target.role_id)
    assert role["permissions"] == ["agent.admin"]


def test_update_role_access_validates_codes_and_targets(auth: ProductionAuth) -> None:
    target = auth.create_role([])
    headers = login_configured_admin()
    unknown_code = _put_access(headers, target.role_id, version=1, permissions=["rag.x"])
    assert unknown_code.status_code == 400
    unknown_agent = _put_access(headers, target.role_id, version=1, agent_ids=["no-such-agent"])
    assert unknown_agent.status_code == 400
    assert "エージェントが見つかりません" in unknown_agent.json()["error_messages"][0]
    invalid_view = _put_access(
        headers, target.role_id, version=1, business_view_ids=["bad view/../x"]
    )
    assert invalid_view.status_code == 400
    stale = _put_access(headers, target.role_id, version=9, permissions=[])
    assert stale.status_code == 409
    builtin = _put_access(
        headers, "00000000-0000-0000-0000-000000000001", version=1, permissions=[]
    )
    assert builtin.status_code == 409


def test_update_role_access_prevents_privilege_escalation(auth: ProductionAuth) -> None:
    """操作者が持たない権限・範囲外の対象をロールに足すと 403。"""
    with _agent("agent-escalation-a"), _agent("agent-escalation-b"):
        auth.user_with_permissions(
            "delegate",
            ["menu.security_permissions", "agent.runs.view"],
            agent_ids=["agent-escalation-a"],
            business_view_ids=["bv-a"],
        )
        target = auth.create_role([])
        headers = login("delegate")
        for body, message in (
            ({"permissions": ["agent.admin"]}, "権限"),
            ({"permissions": ["agent.runs.operate"]}, "権限"),
            ({"permissions": ["menu.security_users"]}, "権限"),
            ({"agent_ids": ["agent-escalation-b"]}, "エージェント"),
            ({"business_view_ids": ["bv-b"]}, "業務ビュー"),
        ):
            response = _put_access(headers, target.role_id, version=1, **body)
            assert response.status_code == 403, body
            assert message in response.json()["error_messages"][0]
        allowed = _put_access(
            headers,
            target.role_id,
            version=1,
            permissions=["agent.runs.view"],
            agent_ids=["agent-escalation-a"],
            business_view_ids=["bv-a"],
        )
        assert allowed.status_code == 200, allowed.text


def test_retired_permission_code_left_in_db_is_ignored(auth: ProductionAuth) -> None:
    """migration 前の DB に `menu.dashboard`（#262）が残っていても、ログイン・権限管理の表示と保存・
    ロールの割り当てが壊れない（未知のコードは実効権限・表示から除き、保存で消える）。"""
    with _agent("agent-retired-a"):
        stale = auth.create_role(
            ["menu.dashboard", "agent.runs.view"], agent_ids=["agent-retired-a"]
        )
        auth.create_user("stale-user", [stale])
        stale_headers = login("stale-user")
        me = client.get("/api/auth/me", headers=stale_headers)
        assert me.status_code == 200
        assert set(me.json()["data"]["permissions"]) == {"agent.runs.view", "menu.runs"}
        assert client.get("/api/runs", headers=stale_headers).status_code == 200

        # 権限管理の画面は廃止コードを表示せず、表示どおりに保存すれば DB からも消える。
        headers = login_configured_admin()
        listed = client.get("/api/security/roles", headers=headers).json()["data"]
        shown = next(item for item in listed if item["role_id"] == stale.role_id)
        assert shown["permissions"] == ["agent.runs.view"]
        saved = _put_access(
            headers,
            stale.role_id,
            version=shown["version"],
            permissions=shown["permissions"],
            agent_ids=shown["agent_ids"],
            business_view_ids=shown["business_view_ids"],
        )
        assert saved.status_code == 200, saved.text
        stored = auth.store.get_role(stale.role_id)
        assert stored is not None
        assert stored.permissions == {"agent.runs.view"}

        # 廃止コードが残るロールも、同じ権限・範囲の操作者なら割り当てられる。
        leftover = auth.create_role(
            ["menu.dashboard", "agent.runs.view"], agent_ids=["agent-retired-a"]
        )
        manager = auth.create_role(
            ["menu.security_users", "agent.runs.view"], agent_ids=["agent-retired-a"]
        )
        auth.create_user("retired-manager", [manager])
        created = client.post(
            "/api/security/users",
            json={
                "login_user_id": "retired-member",
                "display_name": "メンバー",
                "role_ids": [leftover.role_id],
            },
            headers=login("retired-manager"),
        )
        assert created.status_code == 200, created.text


def test_role_assignment_respects_agent_scope(auth: ProductionAuth) -> None:
    """ユーザーへのロール割り当ても、操作者の権限・範囲に収まるロールだけ（共通の昇格防止）。"""
    with _agent("agent-assign-a"), _agent("agent-assign-b"):
        manager = auth.create_role(
            ["menu.security_users", "agent.runs.view"], agent_ids=["agent-assign-a"]
        )
        auth.create_user("user-manager", [manager])
        inside = auth.create_role(["agent.runs.view"], agent_ids=["agent-assign-a"])
        outside = auth.create_role(["agent.runs.view"], agent_ids=["agent-assign-b"])
        headers = login("user-manager")
        body = {"login_user_id": "member1", "display_name": "メンバー", "role_ids": []}
        denied = client.post(
            "/api/security/users", json={**body, "role_ids": [outside.role_id]}, headers=headers
        )
        assert denied.status_code == 403
        created = client.post(
            "/api/security/users", json={**body, "role_ids": [inside.role_id]}, headers=headers
        )
        assert created.status_code == 200, created.text


# ---------------------------------------------------------------------------
# agent_security_migrate
# ---------------------------------------------------------------------------


class _FakeCursor:
    def __init__(self, connection: _FakeConnection) -> None:
        self._connection = connection

    def __enter__(self) -> _FakeCursor:
        return self

    def __exit__(self, *args: object) -> None:
        return None

    rowcount = 0

    def execute(self, statement: str, params: dict[str, Any] | None = None) -> None:
        normalized = " ".join(statement.split())
        self._connection.statements.append(normalized)
        if normalized.startswith("DELETE FROM AGENT_ROLE_PERMISSIONS WHERE PERMISSION_CODE"):
            code = (params or {})["code"]
            matched = {row for row in self._connection.role_permissions if row[1] == code}
            self._connection.role_permissions -= matched
            self.rowcount = len(matched)
            return
        if normalized.startswith("CREATE"):
            name = normalized.split()[2] if normalized.split()[1] == "TABLE" else normalized
            if name in self._connection.objects:
                raise RuntimeError("ORA-00955: name is already used by an existing object")
            self._connection.objects.add(name)


class _FakeConnection:
    def __init__(self) -> None:
        self.statements: list[str] = []
        self.objects: set[str] = set()
        self.commits = 0
        # AGENT_ROLE_PERMISSIONS の行（ROLE_ID, PERMISSION_CODE）。
        self.role_permissions: set[tuple[str, str]] = set()

    def cursor(self) -> _FakeCursor:
        return _FakeCursor(self)

    def commit(self) -> None:
        self.commits += 1


def test_agent_security_migrate_creates_tables_idempotently() -> None:
    connection = _FakeConnection()

    @contextmanager
    def factory() -> Iterator[_FakeConnection]:
        yield connection

    first = agent_security_migrate.run(factory)
    assert "mode=applied" in first
    assert "agent(applied=3 skipped=0)" in first
    tables = [item for item in connection.statements if item.startswith("CREATE TABLE")]
    order = [item.split()[2] for item in tables]
    # PLATFORM_* を先に作り、AGENT_ROLE_* はその後（FK が PLATFORM_ROLES を参照する）。
    assert order.index("PLATFORM_ROLES") < order.index("AGENT_ROLE_PERMISSIONS")
    assert order[-3:] == [
        "AGENT_ROLE_PERMISSIONS",
        "AGENT_ROLE_AGENTS",
        "AGENT_ROLE_BUSINESS_VIEWS",
    ]
    for ddl in AGENT_SECURITY_DDL:
        normalized = " ".join(ddl.split())
        assert "REFERENCES PLATFORM_ROLES (ROLE_ID) ON DELETE CASCADE" in normalized
        assert "PRIMARY KEY (ROLE_ID," in normalized
    agents_ddl = " ".join(AGENT_SECURITY_DDL[1].split())
    assert "AGENT_ID VARCHAR2(128) NOT NULL" in agents_ddl
    views_ddl = " ".join(AGENT_SECURITY_DDL[2].split())
    assert "BUSINESS_VIEW_ID VARCHAR2(64) NOT NULL" in views_ddl
    # 組み込み SYSTEM_ADMIN ロールを MERGE で確認する（ユーザーは作らない）。
    assert any(item.startswith("MERGE INTO PLATFORM_ROLES") for item in connection.statements)
    assert not any("INSERT INTO PLATFORM_USERS" in item for item in connection.statements)

    second = agent_security_migrate.run(factory)
    assert "agent(applied=0 skipped=3)" in second


def test_agent_security_migrate_removes_retired_permission_codes_idempotently() -> None:
    """既存ロールに残る `menu.dashboard`（#262）を削除し、他のコードは残す（冪等）。"""
    connection = _FakeConnection()
    connection.role_permissions = {
        ("role-a", "menu.dashboard"),
        ("role-a", "menu.runs"),
        ("role-b", "menu.dashboard"),
        ("role-b", "agent.runs.view"),
    }

    @contextmanager
    def factory() -> Iterator[_FakeConnection]:
        yield connection

    first = agent_security_migrate.run(factory)
    assert "retired_permission_rows=2" in first
    assert connection.role_permissions == {("role-a", "menu.runs"), ("role-b", "agent.runs.view")}
    deletes = [item for item in connection.statements if item.startswith("DELETE")]
    assert deletes == [
        "DELETE FROM AGENT_ROLE_PERMISSIONS WHERE PERMISSION_CODE = :code",
    ]
    # SYSTEM_ADMIN ロールの確認は削除の後。
    merge_index = next(
        index
        for index, item in enumerate(connection.statements)
        if item.startswith("MERGE INTO PLATFORM_ROLES")
    )
    assert connection.statements.index(deletes[0]) < merge_index

    second = agent_security_migrate.run(factory)
    assert "retired_permission_rows=0" in second
    assert connection.role_permissions == {("role-a", "menu.runs"), ("role-b", "agent.runs.view")}


def test_agent_security_migrate_dry_run_and_failure(
    monkeypatch: MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    assert agent_security_migrate.main(["--dry-run"]) == 0
    preview = capsys.readouterr().out
    assert "mode=preview" in preview
    assert "retired_permission_codes=1" in preview

    @contextmanager
    def broken() -> Iterator[Any]:
        raise RuntimeError("DPY-6005: cannot connect")
        yield  # pragma: no cover

    monkeypatch.setattr(agent_security_migrate, "platform_oracle_connection", broken)
    assert agent_security_migrate.main([]) == 1
    assert "DPY-6005" in capsys.readouterr().err


def test_migration_required_is_reported(auth: ProductionAuth, monkeypatch: MonkeyPatch) -> None:
    """production で認証のテーブルがなければ、migration の案内（409）を返す。"""
    from pr_system_settings.auth.errors import SecurityMigrationRequired

    auth.user_with_permissions("viewer-mig", ["agent.runs.view"])

    def missing(*_args: object, **_kwargs: object) -> None:
        raise SecurityMigrationRequired("PLATFORM_USERS")

    monkeypatch.setattr(auth.store, "get_user_by_login_user_id", missing)
    response = client.post(
        "/api/auth/login", json={"login_user_id": "viewer-mig", "password": USER_PASSWORD}
    )
    assert response.status_code == 409
    body = response.json()
    assert body["error_code"] == "SECURITY_SCHEMA_MIGRATION_REQUIRED"
    assert "agent_security_migrate" in body["error_messages"][0]


def test_router_helpers_do_not_use_local_debug_principal() -> None:
    """local のローカル利用者は router の RBAC に使わない（従来の header 判定のまま）。"""
    from types import SimpleNamespace

    from app.security.dependencies import local_debug_principal, session_principal

    connection = SimpleNamespace(state=SimpleNamespace(principal=local_debug_principal()))
    assert session_principal(connection) is None
    assert agent_router._session_actor_policy(connection) is None


class _RecordingCursor:
    def __init__(self, rows: dict[str, list[tuple[str]]]) -> None:
        self.rows = rows
        self.executed: list[tuple[str, dict[str, Any]]] = []
        self._last = ""

    def execute(self, statement: str, params: dict[str, Any] | None = None) -> None:
        self._last = statement
        self.executed.append((statement, dict(params or {})))

    def fetchall(self) -> list[tuple[str]]:
        for table, rows in self.rows.items():
            if table in self._last:
                return rows
        return []


def test_oracle_store_reads_and_replaces_agent_role_details() -> None:
    from app.security.domain import RoleRecord
    from app.security.store import OracleSecurityStore

    store = OracleSecurityStore(connection_factory=lambda: None)  # type: ignore[arg-type,return-value]
    base = RoleRecord(
        role_id="role-1",
        role_code="OPS",
        display_name="運用",
        description="",
        is_built_in=False,
        archived=False,
        version=3,
    )
    cursor = _RecordingCursor(
        {
            "AGENT_ROLE_PERMISSIONS": [("agent.runs.view",)],
            "AGENT_ROLE_AGENTS": [("default",)],
            "AGENT_ROLE_BUSINESS_VIEWS": [("bv-1",)],
        }
    )
    loaded = store._role_details(cursor, base)
    assert loaded.permissions == {"agent.runs.view"}
    assert loaded.agent_ids == {"default"}
    assert loaded.business_view_ids == {"bv-1"}
    assert all(params == {"role_id": "role-1"} for _, params in cursor.executed)

    cursor = _RecordingCursor({})
    loaded.agent_ids = {"a-2", "a-1"}
    store._replace_role_details(cursor, loaded)
    statements = [" ".join(sql.split()) for sql, _ in cursor.executed]
    assert statements[:3] == [
        "DELETE FROM AGENT_ROLE_PERMISSIONS WHERE ROLE_ID = :role_id",
        "DELETE FROM AGENT_ROLE_AGENTS WHERE ROLE_ID = :role_id",
        "DELETE FROM AGENT_ROLE_BUSINESS_VIEWS WHERE ROLE_ID = :role_id",
    ]
    inserted_agents = [
        params["agent_id"]
        for sql, params in cursor.executed
        if "INSERT INTO AGENT_ROLE_AGENTS" in sql
    ]
    assert inserted_agents == ["a-1", "a-2"]


def test_platform_oracle_connection_uses_platform_settings() -> None:
    from app.oracle_connection import platform_oracle_connect_kwargs
    from app.settings import Settings

    settings = Settings(
        _env_file=None,
        oracle_user="ADMIN",
        oracle_password="secret",  # nosec B106 - テスト用
        oracle_dsn="suiteadb_high",
        oracle_client_lib_dir="/opt/instantclient",
        oracle_wallet_password="wallet-secret",  # nosec B106 - テスト用
        agent_runtime_oracle_dsn="runtime_high",
    )
    kwargs = platform_oracle_connect_kwargs(settings)
    assert kwargs["user"] == "ADMIN"
    assert kwargs["dsn"] == "suiteadb_high"
    assert kwargs["wallet_location"] == "/opt/instantclient/network/admin"
    assert kwargs["wallet_password"] == "wallet-secret"  # nosec B105 - テスト用
    with pytest.raises(RuntimeError, match="PLATFORM_ORACLE_DSN"):
        platform_oracle_connect_kwargs(Settings(_env_file=None, oracle_dsn="", oracle_user=""))


def _read_missing_oci_config(headers: dict[str, str], tmp_path: Any) -> int:
    response = client.post(
        "/api/settings/oci/config/read",
        json={"config_file": str(tmp_path / "missing-config"), "profile": "DEFAULT"},
        headers=headers,
    )
    return response.status_code


def test_system_settings_actions_use_menu_permissions_for_session(
    monkeypatch: MonkeyPatch, auth: ProductionAuth, tmp_path: Any
) -> None:
    """共通のシステム設定の保存・操作は Cookie ではメニュー権限で許可（RAG / NL2SQL と同じ）。"""
    auth.user_with_permissions("oci-admin", ["menu.settings_oci"])
    auth.user_with_permissions("model-admin", ["menu.settings_model"])
    oci_admin = login("oci-admin")
    model_admin = login("model-admin")
    # 権限があれば認可を通り、処理の結果（ファイルがない）になる。
    assert _read_missing_oci_config(oci_admin, tmp_path) not in {401, 403}
    assert _read_missing_oci_config(model_admin, tmp_path) == 403
    # Agent 固有の設定は agent.admin のまま。
    assert client.patch("/api/settings/tool-policy", json={}, headers=oci_admin).status_code == 403

    # router 側の判定（require_system_settings_write）も同じ権限を確認する。
    monkeypatch.setattr(security_dependencies, "permission_for_route", lambda *_args: None)
    assert _read_missing_oci_config(model_admin, tmp_path) == 403
    assert _read_missing_oci_config(oci_admin, tmp_path) not in {401, 403}


def test_system_settings_actions_stay_admin_for_external_roles(
    monkeypatch: MonkeyPatch, auth_with_external_rbac: ProductionAuth, tmp_path: Any
) -> None:
    """header / JWT の経路のシステム設定の保存・操作は従来どおり admin だけ。"""
    enable_signed_identity(monkeypatch)
    operator = signed_identity_headers(roles=["operator", "viewer", "auditor", "approver"])
    assert _read_missing_oci_config(operator, tmp_path) == 403
    admin = signed_identity_headers(roles=["admin"])
    assert _read_missing_oci_config(admin, tmp_path) not in {401, 403}
