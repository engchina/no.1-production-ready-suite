"""「AI 活用」のチャットの会話の見え方（#1889・#1928）。

system_admin は全員の会話（利用者の表示名つき）に本人と同じ操作（続きの質問・停止・回答の評価・
名前の変更・削除）ができ、それ以外の利用者は自分の会話だけ（ほかの利用者の会話は 404）。
system_admin が送った質問は送った人（actor）で保存し、会話の中で送った人の表示名を添える。
SQL の条件（`_oracle_conversation_where`）と、条件どおりに絞る fake の上の API を確かめる。
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import replace

import pytest
from pytest import MonkeyPatch

from app.api.routes import chat as chat_route
from app.clients.oracle import (
    StoredConversation,
    StoredMessage,
    _oracle_conversation_access_predicate_sql,
    _oracle_conversation_where,
    _with_conversation_access_bind,
)
from app.main import app
from app.rag.request_context import (
    AuditRequestContext,
    current_audit_request_context,
    reset_audit_request_context,
    set_audit_request_context,
    user_id_hash_for,
)
from app.security.permissions import MENU_CHAT
from app.security.service import set_security_service
from app.security.user_names import display_names_by_user_hash
from tests.security_support import (
    ProductionAuth,
    enable_production_auth,
    login,
    login_configured_admin,
)
from tests.support import AsgiTestClient
from tests.test_chat_api import FakeChatOracle, _stub_stream

client = AsgiTestClient(app)


class OwnedChatOracle(FakeChatOracle):
    """作った利用者を記録し、SQL の会話の条件（system_admin は全員）どおりに絞る fake。"""

    def _visible(self, conversation: StoredConversation) -> bool:
        context = current_audit_request_context()
        return (
            context.user_id_hash is None
            or context.all_users_visible
            or conversation.user_id_hash == context.user_id_hash
        )

    async def create_conversation(
        self, *, search_answer_profile_id: str, title: str | None = None
    ) -> StoredConversation:
        conversation = await super().create_conversation(
            search_answer_profile_id=search_answer_profile_id, title=title
        )
        conversation.user_id_hash = current_audit_request_context().user_id_hash
        return conversation

    async def append_message(self, message: StoredMessage) -> StoredMessage:
        # 発話の利用者は送った人（OracleClient と同じく request の context から。#1928）。
        if await self.get_conversation(message.conversation_id) is None:
            raise KeyError(message.conversation_id)
        stored = await super().append_message(message)
        stored.user_id_hash = current_audit_request_context().user_id_hash
        return stored

    async def list_conversations(
        self,
        *,
        search_answer_profile_id: str | None = None,
        limit: int | None = None,
        offset: int = 0,
    ) -> list[StoredConversation]:
        items = await super().list_conversations(search_answer_profile_id=search_answer_profile_id)
        visible = [item for item in items if self._visible(item)]
        return visible[offset : (offset + limit) if limit is not None else None]

    async def get_conversation(self, conversation_id: str) -> StoredConversation | None:
        conversation = await super().get_conversation(conversation_id)
        return conversation if conversation is not None and self._visible(conversation) else None

    async def delete_conversation(self, conversation_id: str) -> None:
        if await self.get_conversation(conversation_id) is None:
            raise KeyError(conversation_id)
        await super().delete_conversation(conversation_id)

    async def rename_conversation(self, conversation_id: str, title: str) -> StoredConversation:
        if await self.get_conversation(conversation_id) is None:
            raise KeyError(conversation_id)
        return await super().rename_conversation(conversation_id, title)


@pytest.fixture
def auth(monkeypatch: MonkeyPatch) -> Iterator[ProductionAuth]:
    yield enable_production_auth(monkeypatch)
    set_security_service(None)


@pytest.fixture
def oracle(monkeypatch: MonkeyPatch) -> OwnedChatOracle:
    fake = OwnedChatOracle()
    monkeypatch.setattr(chat_route, "OracleClient", lambda *a, **k: fake)
    return fake


def _user(auth: ProductionAuth, login_user_id: str, display_name: str) -> dict[str, str]:
    user = auth.user_with_permissions(
        login_user_id, [MENU_CHAT], search_answer_profile_ids=["bv-1"]
    )
    auth.store.users[user.user_uuid].display_name = display_name
    return login(client, login_user_id)


def test_conversation_sql_drops_owner_predicate_only_for_system_admin() -> None:
    """system_admin（`all_users_visible`）は持ち主で絞らない（発話の追加・評価も同じ条件。#1928）。"""
    base = AuditRequestContext(tenant_id_hash="tenant", user_id_hash="admin-hash")
    token = set_audit_request_context(replace(base, all_users_visible=True))
    try:
        sql, binds = _oracle_conversation_where()
        assert "user_id_hash" not in sql
        assert "conversation_user_id_hash" not in binds
        assert "user_id_hash" not in _oracle_conversation_access_predicate_sql(alias="c")
        assert "conversation_user_id_hash" not in _with_conversation_access_bind({})
    finally:
        reset_audit_request_context(token)
    token = set_audit_request_context(base)
    try:
        sql, binds = _oracle_conversation_where()
        assert "c.user_id_hash = :conversation_user_id_hash" in sql
        assert binds["conversation_user_id_hash"] == "admin-hash"
        access_sql = _oracle_conversation_access_predicate_sql()
        assert "user_id_hash = :conversation_user_id_hash" in access_sql
        assert _with_conversation_access_bind({})["conversation_user_id_hash"] == "admin-hash"
    finally:
        reset_audit_request_context(token)


def test_users_see_own_conversations_and_system_admin_sees_everyone(
    auth: ProductionAuth, oracle: OwnedChatOracle
) -> None:
    alice = _user(auth, "chat-alice", "アリス")
    bob = _user(auth, "chat-bob", "ボブ")
    created = client.post(
        "/api/chat/conversations", json={"search_answer_profile_id": "bv-1"}, headers=alice
    )
    assert created.status_code == 200, created.text
    conversation_id = created.json()["data"]["id"]
    assert created.json()["data"]["owned"] is True

    # ほかの利用者には一覧に出さず、詳細・名前の変更・削除・送信は 404。
    listed = client.get("/api/chat/conversations", headers=bob).json()["data"]["items"]
    assert conversation_id not in {item["id"] for item in listed}
    path = f"/api/chat/conversations/{conversation_id}"
    assert client.get(path, headers=bob).status_code == 404
    assert client.patch(path, json={"title": "x"}, headers=bob).status_code == 404
    assert client.delete(path, headers=bob).status_code == 404
    sent = client.post(f"{path}/messages/stream", json={"content": "続き"}, headers=bob)
    assert sent.status_code == 404

    # 本人には利用者の表示名を出さない。
    own = client.get(path, headers=alice).json()["data"]
    assert (own["owned"], own["actor_display_name"]) == (True, "")

    # system_admin は全員の会話を利用者の表示名つきで見て、名前を変えられる。
    admin = login_configured_admin(client)
    items = client.get("/api/chat/conversations", headers=admin).json()["data"]["items"]
    [item] = [entry for entry in items if entry["id"] == conversation_id]
    assert (item["owned"], item["actor_display_name"]) == (False, "アリス")
    detail = client.get(path, headers=admin).json()["data"]
    assert (detail["owned"], detail["actor_display_name"]) == (False, "アリス")
    renamed = client.patch(path, json={"title": "確認済み"}, headers=admin)
    assert renamed.status_code == 200
    assert renamed.json()["data"]["actor_display_name"] == "アリス"
    assert client.delete(path, headers=admin).status_code == 200


def test_system_admin_continues_another_users_conversation_as_self(
    auth: ProductionAuth, oracle: OwnedChatOracle, monkeypatch: MonkeyPatch
) -> None:
    """system_admin は他の利用者の会話にも質問を送って回答を得る（#1928）。

    発話の利用者は送った system_admin（持ち主の権限を借りない）。会話の持ち主は変わらず、持ち主にも
    system_admin の質問と回答が送った人の表示名つきで出る。ほかの一般の利用者は 404 のまま。
    """
    _stub_stream(monkeypatch, oracle, ["m1"])
    alice = _user(auth, "send-alice", "アリス")
    bob = _user(auth, "send-bob", "ボブ")
    created = client.post(
        "/api/chat/conversations", json={"search_answer_profile_id": "bv-1"}, headers=alice
    ).json()["data"]
    path = f"/api/chat/conversations/{created['id']}"
    own = client.post(f"{path}/messages/stream", json={"content": "経費の上限は?"}, headers=alice)
    assert own.status_code == 200, own.text

    admin = login_configured_admin(client)
    sent = client.post(f"{path}/messages/stream", json={"content": "続きの質問"}, headers=admin)
    assert sent.status_code == 200, sent.text
    assert "event: all_done" in sent.text
    messages = oracle.messages[created["id"]]
    assert [(item.role, item.content) for item in messages] == [
        ("USER", "経費の上限は?"),
        ("ASSISTANT", "回答"),
        ("USER", "続きの質問"),
        ("ASSISTANT", "回答"),
    ]
    alice_hash = oracle.conversations[created["id"]].user_id_hash
    admin_hashes = {item.user_id_hash for item in messages[2:]}
    assert alice_hash is not None and admin_hashes and alice_hash not in admin_hashes
    # 会話の持ち主は変わらない。
    assert {item.user_id_hash for item in messages[:2]} == {alice_hash}

    # 持ち主の画面にも system_admin の質問と回答が、送った人の表示名つきで出る。
    detail = client.get(path, headers=alice).json()["data"]
    assert detail["owned"] is True
    assert [
        (item["role"], item["sent_by_owner"], item["sender_display_name"])
        for item in detail["messages"]
    ] == [
        ("USER", True, ""),
        ("ASSISTANT", True, ""),
        ("USER", False, "システム管理者"),
        ("ASSISTANT", True, ""),
    ]
    admin_view = client.get(path, headers=admin).json()["data"]
    assert admin_view["owned"] is False
    assert admin_view["messages"][2]["sender_display_name"] == "システム管理者"

    # 一般の利用者は他人の会話に送れず、停止・購読し直しも 404（存在を漏らさない）。
    user_message_id = messages[2].id
    assert (
        client.post(f"{path}/messages/stream", json={"content": "割り込み"}, headers=bob)
    ).status_code == 404
    assert (
        client.post(f"{path}/messages/{user_message_id}/cancel", headers=bob)
    ).status_code == 404
    assert (client.get(f"{path}/messages/{user_message_id}/stream", headers=bob)).status_code == 404
    # system_admin は他人の会話の回答の停止も呼べる（終わった回答は止めるものが無い）。
    stopped = client.post(f"{path}/messages/{messages[0].id}/cancel", headers=admin)
    assert stopped.status_code == 200, stopped.text


def test_owner_names_are_resolved_from_hashes(auth: ProductionAuth) -> None:
    """会話は利用者を hash でしか持たないので、利用者の UUID を同じ hash にして表示名を引く。"""
    user = auth.user_with_permissions("hash-user", [MENU_CHAT])
    auth.store.users[user.user_uuid].display_name = "ハッシュ 太郎"
    owner_hash = user_id_hash_for(user.user_uuid)
    local_hash = user_id_hash_for("local-user")
    assert owner_hash is not None and local_hash is not None
    names = display_names_by_user_hash({owner_hash, local_hash, "missing-hash"})
    assert names == {owner_hash: "ハッシュ 太郎", local_hash: "ローカル利用者"}
    assert display_names_by_user_hash(set()) == {}
