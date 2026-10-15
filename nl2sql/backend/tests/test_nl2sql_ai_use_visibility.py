"""「AI 活用」の下の機能のデータの見え方（#1889・#1928・#1954）。

system_admin は全員のデータに本人と同じすべての操作（見る・名前の変更・削除・続きの質問・
SQL の実行・回答前の確認・停止・回答の評価）ができ、それ以外の利用者は自分のデータだけを扱う。
他人のデータの詳細・操作は 404（存在を漏らさない）。system_admin の操作は system_admin 自身として
実行し（ジョブの actor・SQL の実行の接続〔#904〕・実行履歴・評価した人）、会話の持ち主は変えない。
"""

from __future__ import annotations

from collections.abc import Callable
from types import SimpleNamespace
from typing import Any, cast

import pytest
from fastapi import HTTPException, Request
from pr_backend_core.api import OffsetParams
from pr_system_settings.auth.domain import SYSTEM_ADMIN_ROLE_CODE, UserIdentity
from test_nl2sql_job_runtime import _PROFILE_ID, _request
from test_sql_chat import ChatFixture, chat, request  # noqa: F401 - fixture を使う

from app.features.nl2sql import router
from app.features.nl2sql.models import (
    AllowedObjects,
    AnalyzeData,
    FeedbackRating,
    FeedbackRequest,
    JobCreateRequest,
    JobOrigin,
    JobStatus,
    QueryResults,
    SafetyReport,
    SqlChatUpdateRequest,
)
from app.features.nl2sql.service import Nl2SqlService
from app.security.domain import Principal
from app.security.permissions import (
    FEEDBACK_MANAGE_PERMISSION,
    QUERY_GENERATE_PERMISSION,
    SQL_EXECUTE_PERMISSION,
)
from app.security.request_actor import current_actor_context


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
    """共通の表示名（`gone` は削除済みの利用者で、表示名を引けない）。"""

    def display_identities(self, user_uuids: list[str]) -> dict[str, UserIdentity]:
        return {
            uuid: UserIdentity(user_uuid=uuid, login_user_id=uuid, display_name=f"{uuid} さん")
            for uuid in user_uuids
            if uuid != "gone"
        }


ADMIN = _principal("admin", admin=True)
ALICE = _principal("alice")
# 製品の管理権限（フィードバック管理）を持っても、「AI 活用」では全員を見ない。
BOB = _principal("bob", permissions={FEEDBACK_MANAGE_PERMISSION})
PAGE = OffsetParams(limit=10, offset=0)


def _chat_turn(question: str, previous: str | None = None) -> JobCreateRequest:
    """今の画面が送るチャットのターン（生成と実行。#1176）。"""
    return _request().model_copy(
        update={"question": question, "chat": True, "previous_job_id": previous}
    )


@pytest.fixture
def chats(chat: ChatFixture, monkeypatch: pytest.MonkeyPatch) -> dict[str, str]:  # noqa: F811
    service = chat[0]
    monkeypatch.setattr(router, "nl2sql_service", service)
    monkeypatch.setattr(router, "get_security_service", lambda: _Names())
    ids: dict[str, str] = {}
    for owner in ("alice", "bob"):
        created = service.start_job(
            _chat_turn(f"{owner} の注文"), actor_user_uuid=owner, actor_is_system_admin=False
        )
        assert service.run_next_nl2sql_job(worker_id="visibility", job_id=created.job_id)
        ids[owner] = created.job_id
    return ids


def _send(service: Nl2SqlService, previous: str, principal: Any, question: str = "続き") -> str:
    """画面の route でチャットの続きを送り、worker で処理する。"""
    created = router.create_job(_chat_turn(question, previous), _http(principal)).data
    assert created is not None
    assert service.run_next_nl2sql_job(worker_id="visibility", job_id=created.job_id)
    return created.job_id


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
    history_id = router.get_job(other, _http(BOB)).data.result.history_id  # type: ignore[union-attr]
    for call in (
        lambda: router.get_sql_chat(other, _http(ALICE)),
        lambda: router.rename_sql_chat(other, SqlChatUpdateRequest(title="x"), _http(ALICE)),
        lambda: router.delete_sql_chat(other, _http(ALICE)),
        lambda: router.get_job(other, _http(ALICE)),
        lambda: router.get_job_progress(other, _http(ALICE)),
        lambda: router.cancel_job(other, _http(ALICE)),
        lambda: router.execute_chat_turn(other, _http(ALICE)),
        lambda: router.take_chat_execution_result(other, _http(ALICE)),
        lambda: router.create_job(_chat_turn("続き", other), _http(ALICE)),
        lambda: router.feedback(
            FeedbackRequest(history_id=history_id, rating=FeedbackRating.GOOD), _http(ALICE)
        ),
    ):
        with pytest.raises(HTTPException) as denied:
            call()
        assert denied.value.status_code == 404
    own = router.get_sql_chat(chats["alice"], _http(ALICE)).data
    assert own is not None and own.conversation.owned


def test_system_admin_continues_and_executes_other_users_chat_as_admin(
    chat: ChatFixture,  # noqa: F811
    chats: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """system_admin は他の利用者の会話で続きを送り、SQL を実行できる（#1928）。

    ジョブの actor・SQL の実行の接続（#904）・実行履歴は system_admin 自身で、会話の持ち主は
    変わらない。持ち主の画面にも system_admin が送ったターンが送り手の表示名つきで出る。
    """
    service = chat[0]
    actors: list[tuple[str, bool]] = []
    execute = service.execute_sql

    def record(
        sql: str,
        allowed: AllowedObjects,
        row_limit: int | None,
        *,
        analysis: AnalyzeData | None = None,
    ) -> tuple[SafetyReport, str, QueryResults]:
        context = current_actor_context()
        assert context is not None
        actors.append((context.user_uuid, context.is_system_admin))
        return execute(sql, allowed, row_limit, analysis=analysis)

    monkeypatch.setattr(service, "execute_sql", record)
    alice_chat = chats["alice"]
    admin_turn = _send(service, alice_chat, ADMIN, "多い順にして")
    # 送信のジョブの中の実行は system_admin の接続（#904）。
    assert actors == [("admin", True)]

    for viewer in (ALICE, ADMIN):
        data = router.get_sql_chat(alice_chat, _http(viewer)).data
        assert data is not None
        assert [turn.job_id for turn in data.turns] == [alice_chat, admin_turn]
        assert [turn.sender_display_name for turn in data.turns] == [None, "admin さん"]
        assert data.turns[1].status == JobStatus.DONE
        # 送り手の UUID は応答に出さない。
        assert "sender_user_uuid" not in data.turns[1].model_dump()
    # 会話の持ち主は変わらない（持ち主の一覧に残り、system_admin の一覧は持ち主の名前）。
    alice_list = router.list_sql_chats(_http(ALICE), PAGE).data
    assert alice_list is not None and [item.id for item in alice_list.items] == [alice_chat]
    admin_list = router.list_sql_chats(_http(ADMIN), PAGE).data
    assert admin_list is not None
    assert {item.actor_display_name for item in admin_list.items if item.id == alice_chat} == {
        "alice さん"
    }

    # 実行の結果の行は送った system_admin だけが受け取る（持ち主は「もう一度実行」）。
    with pytest.raises(HTTPException) as not_runner:
        router.take_chat_execution_result(admin_turn, _http(ALICE))
    assert not_runner.value.status_code == 404
    received = router.take_chat_execution_result(admin_turn, _http(ADMIN)).data
    assert received is not None and received.status == "done"
    # 実行履歴（監査）の利用者は system_admin。
    job = service.get_job(admin_turn, actor_user_uuid="admin")
    assert job is not None and job.last_execution is not None
    history = service._history_by_id(job.last_execution.history_id)  # noqa: SLF001
    assert history is not None and history.actor_user_uuid == "admin"

    # 持ち主のターンの SQL を system_admin が実行する（system_admin の接続）。持ち主も system_admin
    # が送ったターンを、自分の接続で実行できる。
    executed = router.execute_chat_turn(alice_chat, _http(ADMIN)).data
    assert executed is not None and executed.status == "done"
    executed = router.execute_chat_turn(admin_turn, _http(ALICE)).data
    assert executed is not None and executed.status == "done"
    assert actors[1:] == [("admin", True), ("alice", False)]
    executed_by_admin = service._history_by_id(  # noqa: SLF001
        router.get_job(alice_chat, _http(ADMIN)).data.last_execution.history_id  # type: ignore[union-attr]
    )
    assert executed_by_admin is not None
    # 持ち主のターンを system_admin が実行した記録は、実行した system_admin。
    assert executed_by_admin.actor_user_uuid == "admin"

    # 持ち主は system_admin のターンの後に続けられる。一般の利用者は続けられない（404）。
    alice_turn = _send(service, admin_turn, ALICE, "上位 5 件だけ")
    data = router.get_sql_chat(alice_chat, _http(ALICE)).data
    assert data is not None
    assert [turn.job_id for turn in data.turns] == [alice_chat, admin_turn, alice_turn]
    assert [turn.sender_display_name for turn in data.turns] == [None, "admin さん", None]
    for previous in (alice_turn, admin_turn):
        with pytest.raises(HTTPException) as denied:
            router.create_job(_chat_turn("続き", previous), _http(BOB))
        assert denied.value.status_code == 404


def test_system_admin_cancels_answers_and_rates_other_users_chat(
    chat: ChatFixture,  # noqa: F811
    chats: dict[str, str],
) -> None:
    """停止・回答前の確認の会話の履歴・回答の評価も system_admin ができる（#1928）。"""
    service = chat[0]
    alice_chat = chats["alice"]

    # 回答前の確認（チャット）に渡す前のターン: 持ち主と system_admin は読め、一般の利用者は
    # 読めない。
    def history(actor: str, *, admin: bool) -> list[dict[str, str]]:
        return service.chat_requirement_history(
            question="多い順にして",
            profile_id=_PROFILE_ID,
            previous_job_id=alice_chat,
            actor_user_uuid=actor,
            actor_is_system_admin=admin,
        )

    assert [turn["query"] for turn in history("admin", admin=True)] == ["alice の注文"]
    with pytest.raises(PermissionError):
        history("bob", admin=False)

    # system_admin が送って処理中のターンは、持ち主も system_admin も見て止められる。
    created = router.create_job(_chat_turn("多い順にして", alice_chat), _http(ADMIN)).data
    assert created is not None
    pending = created.job_id
    assert router.get_job_progress(pending, _http(ALICE)).data is not None
    hidden: tuple[Callable[[], object], ...] = (
        lambda: router.get_job(pending, _http(BOB)),
        lambda: router.cancel_job(pending, _http(BOB)),
    )
    for call in hidden:
        with pytest.raises(HTTPException) as denied:
            call()
        assert denied.value.status_code == 404
    cancelled = router.cancel_job(pending, _http(ADMIN)).data
    assert cancelled is not None and cancelled.job_id == pending
    assert router.cancel_job(pending, _http(ALICE)).data is not None

    # 持ち主の回答を system_admin が評価する。評価した人（system_admin）を記録する。
    alice_job = router.get_job(alice_chat, _http(ALICE)).data
    assert alice_job is not None and alice_job.result is not None
    history_id = alice_job.result.history_id
    saved = router.feedback(
        FeedbackRequest(history_id=history_id, rating=FeedbackRating.GOOD), _http(ADMIN)
    ).data
    assert saved is not None and saved.saved
    stored = service._history_by_id(history_id)  # noqa: SLF001
    assert stored is not None
    assert stored.feedback_rating == FeedbackRating.GOOD
    assert stored.feedback_actor_user_uuid == "admin"
    # 実行した人（実行履歴の利用者）は変えない。
    assert stored.actor_user_uuid == "alice"


def test_system_admin_renames_and_deletes_other_users_chat_with_admin_turns(
    chat: ChatFixture,  # noqa: F811
    chats: dict[str, str],
) -> None:
    other = chats["alice"]
    admin_turn = _send(chat[0], other, ADMIN)
    data = router.get_sql_chat(other, _http(ADMIN)).data
    assert data is not None
    assert not data.conversation.owned
    assert data.conversation.actor_display_name == "alice さん"

    renamed = router.rename_sql_chat(other, SqlChatUpdateRequest(title="確認済み"), _http(ADMIN))
    assert renamed.data is not None and renamed.data.title == "確認済み"
    assert renamed.data.actor_display_name == "alice さん"
    # 会話を消すと、system_admin が送ったターンも会話から外れる。
    router.delete_sql_chat(other, _http(ADMIN))
    alice = router.list_sql_chats(_http(ALICE), PAGE).data
    assert alice is not None and alice.items == []
    with pytest.raises(HTTPException) as gone:
        router.create_job(_chat_turn("続き", admin_turn), _http(ADMIN))
    assert gone.value.status_code == 400


def test_turn_sender_of_deleted_user_is_empty(
    chat: ChatFixture,  # noqa: F811
    chats: dict[str, str],
) -> None:
    """表示名を引けない送り手（削除済みの利用者）は空（画面が「削除済みのユーザー」と出す）。"""
    gone = _principal("gone", admin=True)
    _send(chat[0], chats["alice"], gone)
    data = router.get_sql_chat(chats["alice"], _http(ALICE)).data
    assert data is not None
    assert [turn.sender_display_name for turn in data.turns] == [None, ""]


def _history_page(principal: Any, executor: str = "all") -> Any:
    page = router.history(_http(principal), PAGE, executor=executor).data  # type: ignore[arg-type]
    assert page is not None
    return page


def _result_history_id(service: Nl2SqlService, job_id: str) -> str:
    job = service.get_job(job_id, actor_user_uuid="admin")
    assert job is not None and job.result is not None and job.result.history_id
    return job.result.history_id


def test_history_shows_runs_to_executor_and_conversation_owner(
    chat: ChatFixture,  # noqa: F811
    chats: dict[str, str],
) -> None:
    """実行履歴の行は、実行した人と会話の持ち主の両方に見える（system_admin は全員。#1954）。

    system_admin が alice の会話で実行した往復は alice の一覧にも出る（`executed_by_me=False`・
    実行した人の表示名）。bob には出ない。会話の無い実行（SQL 生成の画面）は実行した人だけ。
    """
    service = chat[0]
    admin_turn = _send(service, chats["alice"], ADMIN, "多い順にして")
    standalone = router.create_job(_request(), _http(ADMIN)).data
    assert standalone is not None
    assert service.run_next_nl2sql_job(worker_id="visibility", job_id=standalone.job_id)
    from_admin_id = _result_history_id(service, admin_turn)
    standalone_id = _result_history_id(service, standalone.job_id)

    # 保存した行には、実行した人と会話の持ち主を残す（会話の無い実行は持ち主が空）。
    stored = service._history_by_id(from_admin_id)  # noqa: SLF001
    assert stored is not None
    assert (stored.actor_user_uuid, stored.conversation_owner_user_uuid) == ("admin", "alice")
    stored_standalone = service._history_by_id(standalone_id)  # noqa: SLF001
    assert stored_standalone is not None
    assert (stored_standalone.actor_user_uuid, stored_standalone.conversation_owner_user_uuid) == (
        "admin",
        "",
    )

    alice = _history_page(ALICE)
    assert alice.total == alice.unfiltered_total == 2
    by_id = {item.id: item for item in alice.items}
    assert standalone_id not in by_id
    from_admin = by_id[from_admin_id]
    assert from_admin.executed_by_me is False
    assert from_admin.executor_display_name == "admin さん"
    assert from_admin.conversation_owner_display_name == "alice さん"
    assert from_admin.owned_by_me is True
    # 管理者向けのログイン ID・利用者の列は一般の利用者に返さない。
    assert from_admin.actor_login_user_id == from_admin.actor_display_name == ""
    own = next(item for item in alice.items if item.id != from_admin_id)
    assert (own.executed_by_me, own.owned_by_me, own.executor_display_name) == (
        True,
        True,
        "alice さん",
    )

    # 「実行者」の絞り込み。件数（total）も同じ条件で、「全 M 件」は見られる範囲。
    mine = _history_page(ALICE, "mine")
    assert [item.id for item in mine.items] == [own.id]
    assert (mine.total, mine.unfiltered_total) == (1, 2)
    others = _history_page(ALICE, "others")
    assert [item.id for item in others.items] == [from_admin_id]
    assert (others.total, others.unfiltered_total) == (1, 2)

    # bob（別の一般の利用者）には alice の会話の往復も、system_admin の実行も出ない。
    bob = _history_page(BOB)
    assert bob.total == 1
    assert all(item.executed_by_me for item in bob.items)
    assert _history_page(BOB, "others").total == 0

    # system_admin は全員の行。会話の無い実行は持ち主が None。
    admin = _history_page(ADMIN)
    assert admin.total == 4
    admin_by_id = {item.id: item for item in admin.items}
    assert admin_by_id[from_admin_id].executed_by_me is True
    assert admin_by_id[from_admin_id].owned_by_me is False
    assert admin_by_id[from_admin_id].actor_display_name == "admin さん"
    assert admin_by_id[standalone_id].conversation_owner_display_name is None
    assert admin_by_id[standalone_id].owned_by_me is None
    assert _history_page(ADMIN, "mine").total == 2
    admin_others = _history_page(ADMIN, "others")
    assert {item.executor_display_name for item in admin_others.items} == {
        "alice さん",
        "bob さん",
    }
    assert (admin_others.total, admin_others.unfiltered_total) == (2, 4)


def test_job_origin_is_kept_in_the_snapshot_and_the_history_row(
    chat: ChatFixture,  # noqa: F811
    chats: dict[str, str],
) -> None:
    """MCP の入口が決めたきっかけは、ジョブの snapshot（worker）を通って実行履歴の行に残る。

    #1986。画面のジョブ（きっかけを渡さない）は空のまま。
    """
    service = chat[0]
    origin = JobOrigin(run_source="evaluation", agent_run_id="run-eval-1")
    created = service.start_job(
        _request(), actor_user_uuid="admin", actor_is_system_admin=True, origin=origin
    )
    stored_job = service._jobs[created.job_id]  # noqa: SLF001
    snapshot = service._job_to_snapshot(stored_job)  # noqa: SLF001
    assert service._job_from_snapshot(snapshot).origin == origin  # noqa: SLF001
    # #1986 より前のジョブ（項目の無い snapshot）と知らない値は画面の実行とみなす。
    legacy = {key: value for key, value in snapshot.items() if not key.startswith("origin_")}
    assert service._job_from_snapshot(legacy).origin == JobOrigin()  # noqa: SLF001
    assert JobOrigin.from_snapshot({"origin_run_source": "admin"}) == JobOrigin()

    assert service.run_next_nl2sql_job(worker_id="origin", job_id=created.job_id)
    history_id = _result_history_id(service, created.job_id)
    row = service._history_by_id(history_id)  # noqa: SLF001
    assert row is not None
    assert (row.run_source, row.agent_run_id, row.api_key_name) == ("evaluation", "run-eval-1", "")
    screen = router.create_job(_request(), _http(ADMIN)).data
    assert screen is not None
    assert service.run_next_nl2sql_job(worker_id="origin", job_id=screen.job_id)
    screen_row = service._history_by_id(_result_history_id(service, screen.job_id))  # noqa: SLF001
    assert screen_row is not None
    assert screen_row.run_source == ""


def test_history_owner_sees_admin_execution_of_owner_turn(
    chat: ChatFixture,  # noqa: F811
    chats: dict[str, str],
) -> None:
    """持ち主のターンを system_admin が「もう一度実行」した記録も、持ち主の一覧に出る（#1954）。"""
    service = chat[0]
    executed = router.execute_chat_turn(chats["alice"], _http(ADMIN)).data
    assert executed is not None and executed.history_id
    alice = _history_page(ALICE, "others")
    assert [item.id for item in alice.items] == [executed.history_id]
    assert alice.items[0].executor_display_name == "admin さん"
    stored = service._history_by_id(executed.history_id)  # noqa: SLF001
    assert stored is not None and stored.actor_user_uuid == "admin"
    assert _history_page(BOB, "others").total == 0


def test_history_rejects_unknown_executor_filter(chats: dict[str, str]) -> None:
    with pytest.raises(HTTPException) as invalid:
        _history_page(ALICE, "everyone")
    assert invalid.value.status_code == 422
