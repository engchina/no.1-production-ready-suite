"""「AI 活用」のチャットの会話の見え方（#1889）。

system_admin は全員の会話（利用者の表示名つき）、それ以外の利用者は自分の会話だけ。ほかの利用者の
会話は 404 で、続きの質問・回答の評価は system_admin でも会話をした本人だけ。
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
from tests.test_chat_api import FakeChatOracle

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


def test_conversation_sql_drops_owner_only_for_system_admin() -> None:
    """system_admin（`all_users_visible`）は持ち主で絞らない。`owner_only` は本人だけ。"""
    base = AuditRequestContext(tenant_id_hash="tenant", user_id_hash="admin-hash")
    token = set_audit_request_context(replace(base, all_users_visible=True))
    try:
        sql, binds = _oracle_conversation_where()
        assert "user_id_hash" not in sql
        assert "conversation_user_id_hash" not in binds
        owner_sql = _oracle_conversation_access_predicate_sql(owner_only=True)
        owner_binds = _with_conversation_access_bind({}, owner_only=True)
        assert "user_id_hash = :conversation_user_id_hash" in owner_sql
        assert owner_binds["conversation_user_id_hash"] == "admin-hash"
    finally:
        reset_audit_request_context(token)
    token = set_audit_request_context(base)
    try:
        sql, binds = _oracle_conversation_where()
        assert "c.user_id_hash = :conversation_user_id_hash" in sql
        assert binds["conversation_user_id_hash"] == "admin-hash"
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
    # 続きの質問は会話をした本人だけ（system_admin は会話を見られるので、理由つきの 403）。
    refused = client.post(f"{path}/messages/stream", json={"content": "続き"}, headers=admin)
    assert refused.status_code == 403
    assert client.delete(path, headers=admin).status_code == 200


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
