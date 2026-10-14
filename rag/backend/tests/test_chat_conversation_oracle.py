"""チャットの会話・メッセージの保存を、実 Oracle AI Database で確かめる。

未到達なら oracle_db fixture が skip する。作った会話はテストの中で削除する
（メッセージは FK の ON DELETE CASCADE で消える）。
"""

from datetime import UTC, datetime
from uuid import uuid4

import pytest

from app.clients.oracle import OracleClient, StoredMessage


@pytest.mark.usefixtures("oracle_db")
async def test_user_message_clarification_round_trips_on_real_oracle() -> None:
    """USER のメッセージの「回答前に確認した内容」を保存し、読み直せる（#1877）。"""
    client = OracleClient()
    profile = await client.ensure_default_search_answer_profile()
    conversation = await client.create_conversation(search_answer_profile_id=profile.id)
    clarification = {
        "outcome": "answered",
        "entries": [{"prompt": "権限の付与先は？", "selected": ["グループ"], "other_text": ""}],
    }
    clarified_id = uuid4().hex
    plain_id = uuid4().hex
    try:
        await client.append_message(
            StoredMessage(
                id=clarified_id,
                conversation_id=conversation.id,
                role="USER",
                content="アクセス権限を付与したい",
                created_at=datetime.now(UTC),
                clarification=clarification,
            )
        )
        await client.append_message(
            StoredMessage(
                id=plain_id,
                conversation_id=conversation.id,
                role="USER",
                content="確認の無い質問",
                created_at=datetime.now(UTC),
            )
        )

        messages = await client.list_messages(conversation.id)
    finally:
        await client.delete_conversation(conversation.id)

    # 同じ時刻の行の並びは決まらないので、ID で引いて確かめる。
    by_id = {message.id: message.clarification for message in messages}
    assert by_id == {clarified_id: clarification, plain_id: None}
