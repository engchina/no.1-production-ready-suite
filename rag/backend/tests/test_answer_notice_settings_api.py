"""回答の注意書きの設定 API（3 製品で共通の文。#2405）。"""

from __future__ import annotations

from pr_system_settings.answer_notice import DEFAULT_ANSWER_NOTICE_TEXT
from pytest import MonkeyPatch

from app.main import app
from tests.security_support import enable_production_auth, login
from tests.support import AsgiTestClient

client = AsgiTestClient(app)


def test_menu_permission_can_read_save_and_reset(monkeypatch: MonkeyPatch) -> None:
    auth = enable_production_auth(monkeypatch)
    auth.user_with_permissions("notice_admin", ["menu.settings_answer_notice"])
    headers = login(client, "notice_admin")

    response = client.get("/api/settings/answer-notice", headers=headers)
    assert response.status_code == 200, response.text
    assert response.json()["data"]["effective_text"] == DEFAULT_ANSWER_NOTICE_TEXT

    saved = client.put(
        "/api/settings/answer-notice",
        headers=headers,
        json={"enabled": True, "text": "独自の注意書き"},
    )
    assert saved.status_code == 200, saved.text
    assert saved.json()["data"]["text"] == "独自の注意書き"
    # 次の回答から効く（同じプロセスの cache は保存で捨てる）。
    assert auth.service.answer_notice.current_text() == "独自の注意書き"

    reset = client.put(
        "/api/settings/answer-notice", headers=headers, json={"enabled": True, "text": None}
    )
    assert reset.status_code == 200
    assert reset.json()["data"]["text"] is None
    assert auth.service.answer_notice.current_text() == DEFAULT_ANSWER_NOTICE_TEXT


def test_other_menu_permission_is_forbidden(monkeypatch: MonkeyPatch) -> None:
    auth = enable_production_auth(monkeypatch)
    auth.user_with_permissions("model_admin", ["menu.settings_model"])
    headers = login(client, "model_admin")

    assert client.get("/api/settings/answer-notice", headers=headers).status_code == 403
    response = client.put(
        "/api/settings/answer-notice", headers=headers, json={"enabled": False, "text": None}
    )
    assert response.status_code == 403
    assert auth.service.answer_notice.current_text() == DEFAULT_ANSWER_NOTICE_TEXT
