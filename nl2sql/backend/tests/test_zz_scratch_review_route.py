"""一時的なレビュー用の確認（コミットしない）。"""

import asyncio

import httpx
import pytest
from test_security import _patch_security_threadpools  # type: ignore

from app.main import app
from app.security.passwords import hash_password
from app.security.service import reset_security_service
from app.security.store import InMemorySecurityStore
from app.settings import get_settings


def test_scratch_detail_route(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_security_threadpools(monkeypatch)
    settings = get_settings()
    monkeypatch.setattr(settings, "app_auth_enabled", True)
    monkeypatch.setattr(settings, "app_auth_cookie_secure", False)
    monkeypatch.setattr(settings, "nl2sql_persistence_mode", "memory")
    reset_security_service()
    from app.security.service import get_security_service

    service = get_security_service()
    assert isinstance(service.store, InMemorySecurityStore)
    service.store.bootstrap(
        login_user_id="ADMIN", display_name="ADMIN", password_hash=hash_password("BootstrapPass!123")
    )
    called = {}

    def fake_detail(self, **kwargs):
        called.update(kwargs)
        return {"name": "EMPLOYEES", "owner": "HR", "object_type": "TABLE"}

    monkeypatch.setattr(
        "app.security.deepsec.DeepSecService.target_object_detail", fake_detail
    )

    async def exercise() -> None:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            await client.post(
                "/api/auth/login", json={"login_user_id": "ADMIN", "password": "BootstrapPass!123"}
            )
            csrf = client.cookies.get("nl2sql_csrf")
            await client.post(
                "/api/auth/password/change",
                headers={"X-CSRF-Token": csrf},
                json={"current_password": "BootstrapPass!123", "new_password": "IndependentPass!456"},
            )
            await client.post(
                "/api/auth/login",
                json={"login_user_id": "ADMIN", "password": "IndependentPass!456"},
            )
            r = await client.get("/api/security/deepsec/target-objects/HR/EMPLOYEES")
            print("STATUS", r.status_code, r.text[:300], called)

    asyncio.run(exercise())
