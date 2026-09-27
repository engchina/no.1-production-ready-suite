"""production mode（共通認証）の API テスト補助（#215）。

InMemory の store で共通認証の service を差し替え、DB ユーザーと構成管理者でログインする。
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Any
from uuid import uuid4

import anyio
import httpx
from pr_system_settings.auth.domain import SYSTEM_ADMIN_ROLE_ID, UserRecord
from pytest import MonkeyPatch

from app.main import app
from app.security.domain import RoleRecord
from app.security.service import SecurityService, set_security_service
from app.security.store import InMemorySecurityStore
from app.settings import get_settings

CONFIGURED_ADMIN_PASSWORD = "AgentSystemPass2026"  # nosec B105 - テスト用
USER_PASSWORD = "UserPassword!2026"  # nosec B105 - テスト用
SESSION_COOKIE = "agent_session"
CSRF_COOKIE = "agent_csrf"


class AsgiTestClient:
    """ASGITransport で 1 リクエストずつ同期的に実行する最小のテストクライアント。"""

    def request(self, method: str, url: str, **kwargs: Any) -> httpx.Response:
        async def run_request() -> httpx.Response:
            transport = httpx.ASGITransport(app=app)
            async with httpx.AsyncClient(
                transport=transport, base_url="http://testserver"
            ) as async_client:
                return await async_client.request(method, url, **kwargs)

        return anyio.run(run_request)

    def get(self, url: str, **kwargs: Any) -> httpx.Response:
        return self.request("GET", url, **kwargs)

    def post(self, url: str, **kwargs: Any) -> httpx.Response:
        return self.request("POST", url, **kwargs)

    def put(self, url: str, **kwargs: Any) -> httpx.Response:
        return self.request("PUT", url, **kwargs)

    def patch(self, url: str, **kwargs: Any) -> httpx.Response:
        return self.request("PATCH", url, **kwargs)


client = AsgiTestClient()


@dataclass
class ProductionAuth:
    """production mode のテスト用の service と store。"""

    service: SecurityService
    store: InMemorySecurityStore
    _counter: int = field(default=0)

    def create_role(
        self,
        permissions: Iterable[str] = (),
        *,
        agent_ids: Iterable[str] = (),
        business_view_ids: Iterable[str] = (),
        role_code: str | None = None,
    ) -> RoleRecord:
        self._counter += 1
        role = RoleRecord(
            role_id=str(uuid4()),
            role_code=role_code or f"ROLE_{self._counter}",
            display_name=f"ロール {self._counter}",
            description="",
            is_built_in=False,
            archived=False,
            version=1,
            permissions=set(permissions),
            agent_ids=set(agent_ids),
            business_view_ids=set(business_view_ids),
        )
        return self.store.create_role(role)

    def create_user(
        self,
        login_user_id: str,
        roles: Iterable[RoleRecord] = (),
        *,
        force_password_change: bool = False,
        system_admin: bool = False,
    ) -> UserRecord:
        role_ids = [role.role_id for role in roles]
        if system_admin:
            role_ids.append(SYSTEM_ADMIN_ROLE_ID)
        return self.store.create_user(
            UserRecord(
                user_uuid=str(uuid4()),
                login_user_id=login_user_id,
                display_name=login_user_id,
                password_hash=self.service._hash_password(USER_PASSWORD),
                status="ACTIVE",
                force_password_change=force_password_change,
                failed_login_count=0,
                locked_until=None,
                version=1,
                role_ids=role_ids,
            )
        )

    def user_with_permissions(
        self,
        login_user_id: str,
        permissions: Iterable[str],
        *,
        agent_ids: Iterable[str] = (),
        business_view_ids: Iterable[str] = (),
    ) -> UserRecord:
        role = self.create_role(
            permissions, agent_ids=agent_ids, business_view_ids=business_view_ids
        )
        return self.create_user(login_user_id, [role])


def enable_production_auth(
    monkeypatch: MonkeyPatch, *, rbac_enabled: bool = False
) -> ProductionAuth:
    """AGENT_AUTH_MODE=production と InMemory の共通認証 service にする。"""
    settings = get_settings()
    monkeypatch.setattr(settings, "auth_mode", "production")
    monkeypatch.setattr(settings, "agent_rbac_enabled", rbac_enabled)
    monkeypatch.setattr(settings, "app_admin_login_user_id", "system_admin")
    monkeypatch.setattr(settings, "app_admin_login_user_password", CONFIGURED_ADMIN_PASSWORD)
    monkeypatch.setattr(settings, "app_auth_cookie_secure", False)
    # テストを速くするため Argon2 のコストを下げる（本番の既定値は変えない）。
    monkeypatch.setattr(settings, "app_auth_argon2_time_cost", 1)
    monkeypatch.setattr(settings, "app_auth_argon2_memory_kib", 8192)
    monkeypatch.setattr(settings, "app_auth_argon2_parallelism", 1)
    store = InMemorySecurityStore()
    store._ensure_system_admin_role()
    service = SecurityService(store, settings)
    set_security_service(service)
    return ProductionAuth(service=service, store=store)


def session_headers(response: httpx.Response) -> dict[str, str]:
    """ログイン応答の Cookie から、以降の request の Cookie / CSRF header を作る。"""
    session = response.cookies.get(SESSION_COOKIE)
    csrf = response.cookies.get(CSRF_COOKIE)
    assert session and csrf, response.text
    return {"cookie": f"{SESSION_COOKIE}={session}; {CSRF_COOKIE}={csrf}", "X-CSRF-Token": csrf}


def login(login_user_id: str, password: str = USER_PASSWORD) -> dict[str, str]:
    response = client.post(
        "/api/auth/login", json={"login_user_id": login_user_id, "password": password}
    )
    assert response.status_code == 200, response.text
    return session_headers(response)


def login_configured_admin() -> dict[str, str]:
    return login("system_admin", CONFIGURED_ADMIN_PASSWORD)


IDENTITY_HMAC_SECRET = "identity-hmac-secret-for-tests"  # nosec B105 - テスト用


def enable_signed_identity(monkeypatch: MonkeyPatch) -> None:
    """外部連携の信頼できる identity（HMAC 署名 header）を設定する。"""
    monkeypatch.setattr(get_settings(), "agent_rbac_identity_hmac_secret", IDENTITY_HMAC_SECRET)


def signed_identity_headers(**claims: Any) -> dict[str, str]:
    """`x-agent-identity` の HMAC 署名 header（router の署名 header の検証と同じ形）。"""
    payload = (
        base64.urlsafe_b64encode(json.dumps(claims).encode("utf-8")).decode("ascii").rstrip("=")
    )
    signature = hmac.new(
        IDENTITY_HMAC_SECRET.encode("utf-8"), payload.encode("utf-8"), hashlib.sha256
    ).hexdigest()
    return {"x-agent-identity": f"{payload}.{signature}"}
