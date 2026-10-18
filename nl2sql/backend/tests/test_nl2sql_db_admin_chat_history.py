"""DB 管理チャットの会話の履歴（#2319）: 保存・開き直し・続きの文脈・実行の要約・見え方。

LLM（OCI Enterprise AI）と Oracle は決定論のスタブで置き換える（memory の保存先）。表・列の名前は
合成のもの。route の関数は画面の request の代わりに principal だけを持つ値で呼ぶ。
"""

from __future__ import annotations

import uuid
from types import SimpleNamespace
from typing import Any, cast

import anyio
import pytest
from fastapi import HTTPException, Request
from pr_backend_core.api import OffsetParams
from pr_system_settings.auth.domain import SYSTEM_ADMIN_ROLE_CODE, UserIdentity
from test_nl2sql_db_admin_chat import _catalog_service, _FakeAdapter, _FakeEnterpriseAi

from app.features.nl2sql import router
from app.features.nl2sql.models import (
    DbAdminChatGenerateRequest,
    DbAdminChatPlanUpdateRequest,
    DbAdminChatTurnExecuteRequest,
    SqlChatUpdateRequest,
)
from app.features.nl2sql.service import (
    DB_ADMIN_CHAT_TURN_LIMIT,
    DbAdminChatConversationFullError,
    Nl2SqlService,
)
from app.security.domain import Principal

PAGE = OffsetParams(limit=25, offset=0)


def _principal(user_uuid: str, *, admin: bool = False) -> Any:
    return Principal(
        user_uuid=user_uuid,
        login_user_id=user_uuid,
        display_name=f"{user_uuid} さん",
        status="ACTIVE",
        force_password_change=False,
        role_codes=[SYSTEM_ADMIN_ROLE_CODE] if admin else ["DBA"],
        permissions={"menu.db_admin_chat"},
        data_entitlements=[],
        allowed_profile_ids=set(),
        session_id=f"session-{user_uuid}",
        csrf_token_hash="csrf",  # nosec B106 - テスト用
    )


def _http(principal: Any, *, disconnected: bool = False) -> Request:
    async def is_disconnected() -> bool:
        return disconnected

    return cast(
        Request,
        SimpleNamespace(
            state=SimpleNamespace(principal=principal), is_disconnected=is_disconnected
        ),
    )


class _Names:
    def display_identities(self, user_uuids: list[str]) -> dict[str, UserIdentity]:
        return {
            uuid_: UserIdentity(user_uuid=uuid_, login_user_id=uuid_, display_name=f"{uuid_} さん")
            for uuid_ in user_uuids
        }


ADMIN = _principal("admin", admin=True)
ALICE = _principal("alice")
BOB = _principal("bob")


def _answer(sql: str, explanation: str = "説明") -> dict[str, str]:
    return {"sql": sql, "explanation": explanation}


@pytest.fixture
def setup(monkeypatch: pytest.MonkeyPatch) -> tuple[Nl2SqlService, _FakeAdapter, _FakeEnterpriseAi]:
    adapter = _FakeAdapter([{"index": 1, "statement_type": "DELETE", "status": "success"}])
    ai = _FakeEnterpriseAi([])
    service = _catalog_service(adapter, ai)
    monkeypatch.setattr(router, "nl2sql_service", service)
    monkeypatch.setattr(router, "get_security_service", lambda: _Names())
    return service, adapter, ai


def _send(principal: Any, question: str, conversation_id: str = "", turn_id: str = "") -> Any:
    data = router.db_admin_chat_generate(
        DbAdminChatGenerateRequest(
            question=question,
            conversation_id=conversation_id,
            client_turn_id=uuid.UUID(turn_id) if turn_id else uuid.uuid4(),
        ),
        _http(principal),
    ).data
    assert data is not None
    return data


def test_first_question_starts_a_conversation_and_is_reopened(
    setup: tuple[Nl2SqlService, _FakeAdapter, _FakeEnterpriseAi],
) -> None:
    _service, adapter, ai = setup
    ai.responses.append(_answer("SELECT QTY FROM APP.ITEM_STOCK", "在庫の数量です"))
    turn_id = str(uuid.uuid4())
    sent = _send(ALICE, "ITEM_STOCK の QTY を見る", turn_id=turn_id)
    # 会話の ID は最初のターンの ID。SQL は生成するだけで実行しない。
    assert sent.conversation.id == turn_id
    assert sent.conversation.title == "ITEM_STOCK の QTY を見る"
    assert sent.turn.plan.read_only is True
    assert sent.turn.context_tables == ["APP.ITEM_STOCK"]
    assert adapter.select_calls == [] and adapter.statement_calls == []

    listed = router.list_db_admin_chats(_http(ALICE), PAGE).data
    assert listed is not None
    assert [item.id for item in listed.items] == [turn_id]
    assert listed.total == 1 and listed.items[0].owned

    reopened = router.get_db_admin_chat(turn_id, _http(ALICE)).data
    assert reopened is not None
    [turn] = reopened.turns
    assert turn.question == "ITEM_STOCK の QTY を見る"
    assert turn.plan.sql == "SELECT QTY FROM APP.ITEM_STOCK"
    assert turn.explanation == "在庫の数量です"
    assert turn.last_execution is None
    assert turn.sender_user_uuid is None


def test_resend_with_the_same_turn_id_does_not_generate_twice(
    setup: tuple[Nl2SqlService, _FakeAdapter, _FakeEnterpriseAi],
) -> None:
    _service, _adapter, ai = setup
    ai.responses.append(_answer("SELECT 1 FROM DUAL"))
    turn_id = str(uuid.uuid4())
    first = _send(ALICE, "版", turn_id=turn_id)
    # 応答が届かずに送り直しても（会話の ID はまだ空のまま）、保存したターンを返す。
    again = _send(ALICE, "版", turn_id=turn_id)
    assert again.turn.turn_id == first.turn.turn_id
    assert len(ai.calls) == 1
    reopened = router.get_db_admin_chat(turn_id, _http(ALICE)).data
    assert reopened is not None and len(reopened.turns) == 1


def test_follow_up_uses_saved_turns_and_execution_outcome_as_context(
    setup: tuple[Nl2SqlService, _FakeAdapter, _FakeEnterpriseAi],
) -> None:
    _service, adapter, ai = setup
    ai.responses.append(_answer("SELECT QTY FROM APP.ITEM_STOCK"))
    first = _send(ALICE, "ITEM_STOCK の数量")
    conversation_id = first.conversation.id
    executed = router.db_admin_chat_execute(
        DbAdminChatTurnExecuteRequest(conversation_id=conversation_id, turn_id=first.turn.turn_id),
        _http(ALICE),
    ).data
    assert executed is not None and executed.executed is True
    # 実行は画面から SQL を受け取らず、ターンに保存した SQL を使う。
    assert adapter.select_calls[-1][0] == "SELECT QTY FROM APP.ITEM_STOCK"

    ai.responses.append(_answer("DELETE FROM APP.ITEM_STOCK WHERE QTY = 0"))
    second = _send(ALICE, "そのうち 0 の行を消す", conversation_id=conversation_id)
    assert second.conversation.id == conversation_id
    context = ai.calls[-1]["context"]
    assert "ITEM_STOCK の数量" in context
    assert "SELECT QTY FROM APP.ITEM_STOCK" in context
    assert "成功（1 行）" in context
    reopened = router.get_db_admin_chat(conversation_id, _http(ALICE)).data
    assert reopened is not None
    assert [turn.question for turn in reopened.turns] == [
        "ITEM_STOCK の数量",
        "そのうち 0 の行を消す",
    ]


def test_stopped_generation_is_not_saved(
    setup: tuple[Nl2SqlService, _FakeAdapter, _FakeEnterpriseAi],
) -> None:
    service, _adapter, ai = setup

    def generate(disconnected: bool) -> Any:
        # FastAPI と同じく、sync の route を anyio の worker thread で動かす。
        return anyio.run(
            anyio.to_thread.run_sync,
            lambda: router.db_admin_chat_generate(
                DbAdminChatGenerateRequest(question="版", client_turn_id=uuid.uuid4()),
                _http(ALICE, disconnected=disconnected),
            ),
        )

    ai.responses.append(_answer("SELECT 1 FROM DUAL"))
    with pytest.raises(HTTPException) as stopped:
        generate(True)
    assert stopped.value.status_code == 409
    # 画面が待っている（接続が切れていない）ときは保存する。
    ai.responses.append(_answer("SELECT 1 FROM DUAL"))
    kept = generate(False).data
    assert kept is not None
    router.delete_db_admin_chat(kept.conversation.id, _http(ALICE))
    # 直に呼んだときも、待つのをやめたら保存しない。
    ai.responses.append(_answer("SELECT 1 FROM DUAL"))
    assert (
        service.send_db_admin_chat_question(
            DbAdminChatGenerateRequest(question="版", client_turn_id=uuid.uuid4()),
            actor="alice",
            all_users=False,
            keep_result=lambda: False,
        )
        is None
    )
    listed = router.list_db_admin_chats(_http(ALICE), PAGE).data
    assert listed is not None and listed.total == 0


def test_execution_summary_keeps_counts_but_not_rows(
    setup: tuple[Nl2SqlService, _FakeAdapter, _FakeEnterpriseAi],
) -> None:
    _service, _adapter, ai = setup
    ai.responses.append(_answer("SELECT N FROM APP.ITEM_STOCK"))
    sent = _send(ALICE, "数量")
    router.db_admin_chat_execute(
        DbAdminChatTurnExecuteRequest(
            conversation_id=sent.conversation.id, turn_id=sent.turn.turn_id, row_limit=50
        ),
        _http(ALICE),
    )
    reopened = router.get_db_admin_chat(sent.conversation.id, _http(ALICE)).data
    assert reopened is not None
    last = reopened.turns[0].last_execution
    assert last is not None
    assert last.succeeded is True
    assert (last.row_count, last.column_count, last.has_more) == (1, 1, False)
    assert last.confirmation_given is False
    assert last.outcome == "成功（1 行）"
    # 結果の行はチャットと同じく会話に残さない。
    assert last.result.select_result is None


def test_mutation_waiting_for_confirmation_is_not_recorded_and_confirmed_is(
    setup: tuple[Nl2SqlService, _FakeAdapter, _FakeEnterpriseAi],
) -> None:
    _service, adapter, ai = setup
    ai.responses.append(_answer("DELETE FROM APP.ITEM_STOCK WHERE QTY = 0"))
    sent = _send(ALICE, "0 の行を消す")
    request = DbAdminChatTurnExecuteRequest(
        conversation_id=sent.conversation.id, turn_id=sent.turn.turn_id
    )
    pending = router.db_admin_chat_execute(request, _http(ALICE)).data
    assert pending is not None and pending.executed is False
    assert adapter.statement_calls == []
    reopened = router.get_db_admin_chat(sent.conversation.id, _http(ALICE)).data
    assert reopened is not None and reopened.turns[0].last_execution is None

    confirmed = router.db_admin_chat_execute(
        request.model_copy(update={"confirmation": "ADMIN_EXECUTE"}), _http(ALICE)
    ).data
    assert confirmed is not None and confirmed.committed is True
    reopened = router.get_db_admin_chat(sent.conversation.id, _http(ALICE)).data
    assert reopened is not None
    last = reopened.turns[0].last_execution
    assert last is not None and last.confirmation_given is True and last.succeeded is True
    assert last.result.committed is True


def test_corrected_sql_is_reclassified_and_resets_the_execution(
    setup: tuple[Nl2SqlService, _FakeAdapter, _FakeEnterpriseAi],
) -> None:
    _service, _adapter, ai = setup
    ai.responses.append(_answer("SELECT QTY FROM APP.ITEM_STOCK"))
    sent = _send(ALICE, "数量")
    router.db_admin_chat_execute(
        DbAdminChatTurnExecuteRequest(
            conversation_id=sent.conversation.id, turn_id=sent.turn.turn_id
        ),
        _http(ALICE),
    )
    corrected = router.replace_db_admin_chat_turn_sql(
        sent.conversation.id,
        sent.turn.turn_id,
        DbAdminChatPlanUpdateRequest(sql="DROP TABLE APP.ITEM_STOCK PURGE"),
        _http(ALICE),
    ).data
    assert corrected is not None
    assert corrected.revision == 1 and corrected.corrected is True
    assert corrected.last_execution is None
    # サーバーが分類し直す（1 つの対象を消す DROP の確認語は対象の名前）。
    assert corrected.plan.requires_confirmation is True
    assert corrected.plan.confirmation_target == "APP.ITEM_STOCK"
    blocked = router.replace_db_admin_chat_turn_sql(
        sent.conversation.id,
        sent.turn.turn_id,
        DbAdminChatPlanUpdateRequest(sql="ALTER SYSTEM SET OPEN_CURSORS = 300"),
        _http(ALICE),
    ).data
    assert blocked is not None and blocked.plan.blocked is True and blocked.revision == 2


def test_other_users_conversations_are_404_and_system_admin_sees_all(
    setup: tuple[Nl2SqlService, _FakeAdapter, _FakeEnterpriseAi],
) -> None:
    _service, _adapter, ai = setup
    ai.responses.extend([_answer("SELECT 1 FROM DUAL"), _answer("SELECT 2 FROM DUAL")])
    alice = _send(ALICE, "alice の質問")
    bob = _send(BOB, "bob の質問")
    other = bob.conversation.id
    turn_id = bob.turn.turn_id
    for call in (
        lambda: router.get_db_admin_chat(other, _http(ALICE)),
        lambda: router.rename_db_admin_chat(other, SqlChatUpdateRequest(title="x"), _http(ALICE)),
        lambda: router.delete_db_admin_chat(other, _http(ALICE)),
        lambda: router.db_admin_chat_execute(
            DbAdminChatTurnExecuteRequest(conversation_id=other, turn_id=turn_id), _http(ALICE)
        ),
        lambda: router.replace_db_admin_chat_turn_sql(
            other, turn_id, DbAdminChatPlanUpdateRequest(sql="SELECT 3 FROM DUAL"), _http(ALICE)
        ),
        lambda: _send(ALICE, "続き", conversation_id=other),
        # 消した・無い会話への続きの質問も同じ 404。
        lambda: _send(ALICE, "続き", conversation_id="missing-conversation"),
    ):
        with pytest.raises(HTTPException) as hidden:
            call()
        assert hidden.value.status_code == 404
    mine = router.list_db_admin_chats(_http(ALICE), PAGE).data
    assert mine is not None and [item.id for item in mine.items] == [alice.conversation.id]
    assert mine.items[0].actor_display_name == ""

    everyone = router.list_db_admin_chats(_http(ADMIN), PAGE).data
    assert everyone is not None
    assert {item.id for item in everyone.items} == {alice.conversation.id, other}
    assert {item.actor_display_name for item in everyone.items} == {"alice さん", "bob さん"}
    assert not any(item.owned for item in everyone.items)
    assert "actor_user_uuid" not in everyone.items[0].model_dump()


def test_system_admin_continues_other_users_conversation_as_admin(
    setup: tuple[Nl2SqlService, _FakeAdapter, _FakeEnterpriseAi],
) -> None:
    _service, _adapter, ai = setup
    ai.responses.extend([_answer("SELECT 1 FROM DUAL"), _answer("SELECT 2 FROM DUAL")])
    bob = _send(BOB, "bob の質問")
    conversation_id = bob.conversation.id
    sent = _send(ADMIN, "管理者の続き", conversation_id=conversation_id)
    assert sent.turn.sender_user_uuid == "admin"
    assert sent.turn.sender_display_name == "admin さん"
    assert sent.conversation.owned is False
    # 会話の持ち主は変えない（持ち主の一覧に残り、持ち主にも管理者のターンが見える）。
    owner_view = router.get_db_admin_chat(conversation_id, _http(BOB)).data
    assert owner_view is not None
    assert owner_view.conversation.owned is True
    assert [turn.sender_display_name for turn in owner_view.turns] == [None, "admin さん"]
    executed = router.db_admin_chat_execute(
        DbAdminChatTurnExecuteRequest(conversation_id=conversation_id, turn_id=sent.turn.turn_id),
        _http(ADMIN),
    ).data
    assert executed is not None and executed.executed is True


def test_rename_and_delete_conversation(
    setup: tuple[Nl2SqlService, _FakeAdapter, _FakeEnterpriseAi],
) -> None:
    _service, _adapter, ai = setup
    ai.responses.append(_answer("SELECT 1 FROM DUAL"))
    sent = _send(ALICE, "版")
    renamed = router.rename_db_admin_chat(
        sent.conversation.id, SqlChatUpdateRequest(title="  新しい名前  "), _http(ALICE)
    ).data
    assert renamed is not None and renamed.title == "新しい名前"
    router.delete_db_admin_chat(sent.conversation.id, _http(ALICE))
    with pytest.raises(HTTPException) as gone:
        router.get_db_admin_chat(sent.conversation.id, _http(ALICE))
    assert gone.value.status_code == 404
    listed = router.list_db_admin_chats(_http(ALICE), PAGE).data
    assert listed is not None and listed.total == 0


def test_conversation_turn_limit(
    setup: tuple[Nl2SqlService, _FakeAdapter, _FakeEnterpriseAi],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service, _adapter, ai = setup
    monkeypatch.setattr("app.features.nl2sql.service.DB_ADMIN_CHAT_TURN_LIMIT", 2)
    ai.responses.extend([_answer("SELECT 1 FROM DUAL"), _answer("SELECT 2 FROM DUAL")])
    first = _send(ALICE, "1")
    _send(ALICE, "2", conversation_id=first.conversation.id)
    with pytest.raises(DbAdminChatConversationFullError):
        service.send_db_admin_chat_question(
            DbAdminChatGenerateRequest(
                question="3", conversation_id=first.conversation.id, client_turn_id=uuid.uuid4()
            ),
            actor="alice",
            all_users=False,
        )
    with pytest.raises(HTTPException) as full:
        _send(ALICE, "3", conversation_id=first.conversation.id)
    assert full.value.status_code == 409
    assert DB_ADMIN_CHAT_TURN_LIMIT == 50


def test_history_pages_are_newest_first(
    setup: tuple[Nl2SqlService, _FakeAdapter, _FakeEnterpriseAi],
) -> None:
    _service, _adapter, ai = setup
    ai.responses.extend([_answer(f"SELECT {index} FROM DUAL") for index in range(3)])
    ids = [_send(ALICE, f"質問 {index}").conversation.id for index in range(3)]
    first = router.list_db_admin_chats(_http(ALICE), OffsetParams(limit=2, offset=0)).data
    second = router.list_db_admin_chats(_http(ALICE), OffsetParams(limit=2, offset=2)).data
    assert first is not None and second is not None
    assert [item.id for item in first.items] == [ids[2], ids[1]]
    assert [item.id for item in second.items] == [ids[0]]
    assert first.total == second.total == 3
