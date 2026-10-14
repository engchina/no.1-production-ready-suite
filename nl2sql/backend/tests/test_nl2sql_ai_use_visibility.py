"""「AI 活用」の下の機能のデータの見え方（#1889）。

system_admin は全員のデータ、それ以外の利用者は自分のデータだけを、一覧・詳細・操作で扱う。
他人のデータの詳細・操作は 404（存在を漏らさない）。system_admin の会話の一覧には利用者の表示名。
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any, cast

import pytest
from fastapi import HTTPException, Request
from pr_backend_core.api import CursorParams
from pr_system_settings.auth.domain import SYSTEM_ADMIN_ROLE_CODE, UserIdentity
from test_nl2sql_job_runtime import _PROFILE_ID
from test_sql_chat import ChatFixture, chat, request  # noqa: F401 - fixture を使う

from app.features.nl2sql import router
from app.features.nl2sql.models import SqlChatUpdateRequest
from app.security.domain import Principal
from app.security.permissions import (
    FEEDBACK_MANAGE_PERMISSION,
    QUERY_GENERATE_PERMISSION,
    SQL_EXECUTE_PERMISSION,
)


def _principal(user_uuid: str, *, admin: bool = False, permissions: set[str] | None = None) -> Any:
    return Principal(
        user_uuid=user_uuid,
        login_user_id=user_uuid,
        display_name=f"{user_uuid} さん",
        status="ACTIVE",
        force_password_change=False,
        role_codes=[SYSTEM_ADMIN_ROLE_CODE] if admin else ["ANALYST"],
        permissions={QUERY_GENERATE_PERMISSION, SQL_EXECUTE_PERMISSION, *(permissions or set())},
        data_entitlements=[],
        allowed_profile_ids={_PROFILE_ID},
        session_id=f"session-{user_uuid}",
        csrf_token_hash="csrf",  # nosec B106 - テスト用
    )


def _http(principal: Any) -> Request:
    return cast(Request, SimpleNamespace(state=SimpleNamespace(principal=principal)))


class _Names:
    def display_identities(self, user_uuids: list[str]) -> dict[str, UserIdentity]:
        return {
            uuid: UserIdentity(user_uuid=uuid, login_user_id=uuid, display_name=f"{uuid} さん")
            for uuid in user_uuids
        }


ADMIN = _principal("admin", admin=True)
ALICE = _principal("alice")
# 製品の管理権限（フィードバック管理）を持っても、「AI 活用」では全員を見ない。
BOB = _principal("bob", permissions={FEEDBACK_MANAGE_PERMISSION})
PAGE = CursorParams(cursor=None, limit=10)


@pytest.fixture
def chats(chat: ChatFixture, monkeypatch: pytest.MonkeyPatch) -> dict[str, str]:  # noqa: F811
    service, _, _ = chat
    monkeypatch.setattr(router, "nl2sql_service", service)
    monkeypatch.setattr(router, "get_security_service", lambda: _Names())
    ids: dict[str, str] = {}
    for owner in ("alice", "bob"):
        created = service.start_job(
            request(f"{owner} の注文"), actor_user_uuid=owner, actor_is_system_admin=False
        )
        assert service.run_next_nl2sql_job(worker_id="visibility", job_id=created.job_id)
        ids[owner] = created.job_id
    return ids


def test_chat_list_is_own_for_users_and_everyone_for_system_admin(chats: dict[str, str]) -> None:
    alice = router.list_sql_chats(_http(ALICE), PAGE).data
    assert alice is not None
    assert [item.id for item in alice.items] == [chats["alice"]]
    assert alice.items[0].owned and alice.items[0].actor_display_name == ""
    bob = router.list_sql_chats(_http(BOB), PAGE).data
    assert bob is not None and [item.id for item in bob.items] == [chats["bob"]]

    admin = router.list_sql_chats(_http(ADMIN), PAGE).data
    assert admin is not None
    assert {item.id for item in admin.items} == {chats["alice"], chats["bob"]}
    assert {item.actor_display_name for item in admin.items} == {"alice さん", "bob さん"}
    assert not any(item.owned for item in admin.items)
    # 持ち主の UUID は応答に出さない。
    assert "actor_user_uuid" not in admin.items[0].model_dump()


def test_chat_detail_and_operations_hide_other_users_chats(chats: dict[str, str]) -> None:
    other = chats["bob"]
    for call in (
        lambda: router.get_sql_chat(other, _http(ALICE)),
        lambda: router.rename_sql_chat(other, SqlChatUpdateRequest(title="x"), _http(ALICE)),
        lambda: router.delete_sql_chat(other, _http(ALICE)),
        lambda: router.get_job(other, _http(ALICE)),
        lambda: router.get_job_progress(other, _http(ALICE)),
        lambda: router.cancel_job(other, _http(ALICE)),
        lambda: router.execute_chat_turn(other, _http(ALICE)),
        lambda: router.take_chat_execution_result(other, _http(ALICE)),
        lambda: router.create_job(request("続き", other), _http(ALICE)),
    ):
        with pytest.raises(HTTPException) as denied:
            call()
        assert denied.value.status_code == 404
    own = router.get_sql_chat(chats["alice"], _http(ALICE)).data
    assert own is not None and own.conversation.owned


def test_system_admin_sees_and_manages_other_users_chats(chats: dict[str, str]) -> None:
    other = chats["alice"]
    data = router.get_sql_chat(other, _http(ADMIN)).data
    assert data is not None
    assert not data.conversation.owned
    assert data.conversation.actor_display_name == "alice さん"
    assert [turn.job_id for turn in data.turns] == [other]
    job = router.get_job(other, _http(ADMIN)).data
    assert job is not None and job.job_id == other
    assert router.get_job_progress(other, _http(ADMIN)).data is not None

    # 続き・SQL の実行は本人だけ（system_admin は会話を見られるので、理由つきの 403）。
    for call in (
        lambda: router.create_job(request("続き", other), _http(ADMIN)),
        lambda: router.execute_chat_turn(other, _http(ADMIN)),
    ):
        with pytest.raises(HTTPException) as denied:
            call()
        assert denied.value.status_code == 403

    renamed = router.rename_sql_chat(other, SqlChatUpdateRequest(title="確認済み"), _http(ADMIN))
    assert renamed.data is not None and renamed.data.title == "確認済み"
    assert renamed.data.actor_display_name == "alice さん"
    router.delete_sql_chat(other, _http(ADMIN))
    alice = router.list_sql_chats(_http(ALICE), PAGE).data
    assert alice is not None and alice.items == []
