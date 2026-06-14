"""ログイン・ログアウト API のテスト。"""

from pytest import MonkeyPatch

from app.auth import AUTH_CONFIG_ERROR_MESSAGE
from app.config import get_settings
from app.main import app
from tests.support import AsgiTestClient

client = AsgiTestClient(app)


def test_auth_me_local_mode_is_authenticated_without_login(monkeypatch: MonkeyPatch) -> None:
    settings = get_settings()
    monkeypatch.setattr(settings, "auth_mode", "local")

    resp = client.get("/api/auth/me")

    assert resp.status_code == 200
    data = resp.json()["data"]
    assert data["auth_required"] is False
    assert data["authenticated"] is True
    assert data["user"]["name"] == "local"


def test_production_mode_rejects_protected_api_without_session(
    monkeypatch: MonkeyPatch,
) -> None:
    _configure_auth(monkeypatch)

    resp = client.get("/api/dashboard/summary")

    assert resp.status_code == 401
    assert resp.json()["error_messages"] == ["ログインしてください。"]


def test_production_login_sets_cookie_and_logout_clears_session(
    monkeypatch: MonkeyPatch,
) -> None:
    _configure_auth(monkeypatch)

    login_resp = client.post(
        "/api/auth/login",
        json={"username": "admin", "password": "secret", "remember_me": True},
    )

    assert login_resp.status_code == 200
    assert login_resp.json()["data"]["authenticated"] is True
    cookie_header = login_resp.headers["set-cookie"].split(";", 1)[0]

    me_resp = client.get("/api/auth/me", headers={"cookie": cookie_header})
    assert me_resp.status_code == 200
    assert me_resp.json()["data"]["user"]["name"] == "admin"

    protected_resp = client.get("/api/dashboard/summary", headers={"cookie": cookie_header})
    assert protected_resp.status_code == 200

    logout_resp = client.post("/api/auth/logout", headers={"cookie": cookie_header})
    assert logout_resp.status_code == 200
    assert logout_resp.json()["data"]["authenticated"] is False
    assert "Max-Age=0" in logout_resp.headers["set-cookie"]

    rejected_resp = client.get("/api/dashboard/summary")
    assert rejected_resp.status_code == 401


def test_production_login_rejects_invalid_credentials(monkeypatch: MonkeyPatch) -> None:
    _configure_auth(monkeypatch)

    resp = client.post(
        "/api/auth/login",
        json={"username": "admin", "password": "wrong", "remember_me": True},
    )

    assert resp.status_code == 401
    assert resp.json()["error_messages"] == ["ユーザー名またはパスワードが正しくありません。"]


def test_production_auth_requires_complete_config(monkeypatch: MonkeyPatch) -> None:
    settings = get_settings()
    monkeypatch.setattr(settings, "auth_mode", "production")
    monkeypatch.setattr(settings, "auth_username", "")
    monkeypatch.setattr(settings, "auth_password", "")
    monkeypatch.setattr(settings, "auth_session_secret", "")

    resp = client.get("/api/dashboard/summary")

    assert resp.status_code == 503
    assert resp.json()["error_messages"] == [AUTH_CONFIG_ERROR_MESSAGE]


def test_expired_session_is_rejected(monkeypatch: MonkeyPatch) -> None:
    _configure_auth(monkeypatch, timeout_seconds=-1)

    login_resp = client.post(
        "/api/auth/login",
        json={"username": "admin", "password": "secret", "remember_me": True},
    )
    cookie_header = login_resp.headers["set-cookie"].split(";", 1)[0]

    monkeypatch.setattr(get_settings(), "auth_session_timeout_seconds", 60)
    resp = client.get("/api/dashboard/summary", headers={"cookie": cookie_header})

    assert resp.status_code == 401


def _configure_auth(monkeypatch: MonkeyPatch, timeout_seconds: int = 60 * 60) -> None:
    settings = get_settings()
    monkeypatch.setattr(settings, "auth_mode", "production")
    monkeypatch.setattr(settings, "auth_username", "admin")
    monkeypatch.setattr(settings, "auth_password", "secret")
    monkeypatch.setattr(settings, "auth_session_secret", "test-session-secret")
    monkeypatch.setattr(settings, "auth_session_timeout_seconds", timeout_seconds)
    monkeypatch.setattr(settings, "auth_cookie_secure", False)
