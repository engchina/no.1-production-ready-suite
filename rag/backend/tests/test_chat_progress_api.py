"""回答の処理の段階の polling / SSE の endpoint のテスト（3 製品共通の段階のイベント。#1359）。

`GET /api/chat/conversations/{id}/messages/{回答の id}/progress[/stream]` が保存済みの記録
（`progress_json`）から段階のイベントを返し、`since` / `Last-Event-ID` の続きから送ること、
終わった回答は 204、別の worker が記録していても届くこと、作成していたプロセスが止まった回答は
中断の失敗にすること、会話の範囲と権限を、OracleClient の fake で確かめる（LLM・Oracle は
呼ばない）。
"""

import asyncio
import json
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import pytest
from pr_backend_core.chat_progress import (
    ChatProgressRecorder,
    configure_chat_progress_sse,
    dump_chat_progress_events,
    restore_chat_progress_sse,
)
from pytest import MonkeyPatch

from app.api.routes import chat as chat_route
from app.clients.oracle import StoredMessage
from app.main import app
from app.rag import chat_answer_runs
from app.rag.chat_answer_runs import CHAT_ANSWER_INTERRUPTED_MESSAGE, ChatAnswerRunService
from app.rag.chat_progress import CHAT_PROGRESS_STEP_IDS
from app.security.permissions import MENU_CHAT, permission_for_route
from tests.security_support import enable_production_auth, login
from tests.support import TEST_REQUEST_HEADERS, AsgiTestClient
from tests.test_chat_api import FakeChatOracle, _chat_conversation

ANSWER_ID = "b" * 32
PROGRESS_PATH = f"/api/chat/conversations/conv-p/messages/{ANSWER_ID}/progress"

client = AsgiTestClient(app)


@pytest.fixture
def service(monkeypatch: MonkeyPatch) -> ChatAnswerRunService:
    fresh = ChatAnswerRunService(worker_id="pytest-worker")
    monkeypatch.setattr(chat_answer_runs, "_SERVICE", fresh)
    return fresh


@pytest.fixture
def fake(monkeypatch: MonkeyPatch, service: ChatAnswerRunService) -> FakeChatOracle:
    oracle = FakeChatOracle()
    _chat_conversation(oracle, "conv-p")
    monkeypatch.setattr(chat_route, "OracleClient", lambda *a, **k: oracle)
    return oracle


@pytest.fixture(autouse=True)
def short_sse_timing() -> Iterator[None]:
    """SSE の読み直しと heartbeat を短くする（テストの時間。共通の設定で変える）。"""
    previous = configure_chat_progress_sse(poll_seconds=0.01, heartbeat_seconds=0.05)
    yield
    restore_chat_progress_sse(previous)


def _recorder(*, running: str | None = "retrieve") -> ChatProgressRecorder:
    recorder = ChatProgressRecorder(ANSWER_ID)
    recorder.declare(*CHAT_PROGRESS_STEP_IDS)
    if running is not None:
        recorder.start(running)
    return recorder


def _answer(
    fake: FakeChatOracle,
    recorder: ChatProgressRecorder,
    *,
    status: str = "STREAMING",
    lease_owner: str | None = "other-host:1:abc",
    heartbeat_at: datetime | None = None,
) -> StoredMessage:
    now = datetime.now(UTC)
    fake.messages["conv-p"] = [
        StoredMessage(id="u-1", conversation_id="conv-p", role="USER", content="q", created_at=now),
        StoredMessage(
            id=ANSWER_ID,
            conversation_id="conv-p",
            role="ASSISTANT",
            content="",
            status=status,
            reply_to_message_id="u-1",
            lease_owner=lease_owner,
            heartbeat_at=heartbeat_at or now,
            created_at=now,
            progress=dump_chat_progress_events(recorder.events),
        ),
    ]
    return fake.messages["conv-p"][1]


def _sse(text: str) -> list[tuple[int | None, str, dict[str, Any]]]:
    """SSE の本文を (id, event, data) の列にする（`retry:` は除く）。"""
    items: list[tuple[int | None, str, dict[str, Any]]] = []
    for block in text.split("\n\n"):
        lines = block.strip().splitlines()
        data = [line.removeprefix("data: ") for line in lines if line.startswith("data: ")]
        if not data:
            continue
        event_id = next((int(line[4:]) for line in lines if line.startswith("id: ")), None)
        name = next((line[7:] for line in lines if line.startswith("event: ")), "message")
        items.append((event_id, name, json.loads("".join(data))))
    return items


def _async_client() -> httpx.AsyncClient:
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://testserver",
        headers=TEST_REQUEST_HEADERS,
    )


# --------------------------------------------------------------------------- #
# polling
# --------------------------------------------------------------------------- #


def test_polling_returns_saved_events_after_since(fake: FakeChatOracle) -> None:
    """保存済みのイベントのうち `since` より後を返す（`last_seq` と終端か）。"""
    recorder = _recorder()
    _answer(fake, recorder)

    page = client.get(PROGRESS_PATH).json()["data"]
    assert page["target_id"] == ANSWER_ID
    assert page["attempt"] == 0
    assert page["last_seq"] == 6
    assert page["terminal"] is False
    assert [event["seq"] for event in page["events"]] == [1, 2, 3, 4, 5, 6]
    assert page["events"][-1]["step_id"] == "retrieve"
    assert page["events"][-1]["status"] == "running"

    resumed = client.get(f"{PROGRESS_PATH}?since=5").json()["data"]
    assert [event["seq"] for event in resumed["events"]] == [6]
    assert client.get(f"{PROGRESS_PATH}?since=6").json()["data"]["events"] == []
    assert client.get(f"{PROGRESS_PATH}?since=-1").status_code == 422


def test_polling_reports_terminal_of_finished_answer(fake: FakeChatOracle) -> None:
    """終わった回答は終端のイベントまで返し、`terminal` が真。"""
    recorder = _recorder()
    recorder.complete("done")
    _answer(fake, recorder, status="COMPLETE", lease_owner=None)

    page = client.get(PROGRESS_PATH).json()["data"]
    assert page["terminal"] is True
    assert page["events"][-1]["type"] == "terminal"
    assert page["events"][-1]["status"] == "done"


def test_polling_treats_finished_answer_without_terminal_as_terminal(
    fake: FakeChatOracle,
) -> None:
    """段階を保存していない・旧形式の完了した回答も、終わったものとして返す（配信を閉じる）。"""
    message = _answer(fake, _recorder(), status="COMPLETE", lease_owner=None)
    message.progress = [{"id": "retrieve", "label": "文書を探しています", "status": "done"}]

    page = client.get(PROGRESS_PATH).json()["data"]
    assert page == {
        "target_id": ANSWER_ID,
        "attempt": 0,
        "events": [],
        "last_seq": 0,
        "terminal": True,
    }


def test_polling_closes_interrupted_answer_with_failed_terminal(fake: FakeChatOracle) -> None:
    """作成していたプロセスが止まった回答は、中断の失敗（終端）にしてから返す。"""
    _answer(fake, _recorder(), heartbeat_at=datetime.now(UTC) - timedelta(seconds=600))

    page = client.get(f"{PROGRESS_PATH}?since=6").json()["data"]
    assert page["terminal"] is True
    retrieve = [event for event in page["events"] if event.get("step_id") == "retrieve"]
    assert [event["status"] for event in retrieve] == ["failed"]
    assert page["events"][-1]["type"] == "terminal"
    assert page["events"][-1]["status"] == "failed"
    assert [event["seq"] for event in page["events"]] == list(range(7, 7 + len(page["events"])))
    message = fake.messages["conv-p"][1]
    assert (message.status, message.content) == ("ERROR", CHAT_ANSWER_INTERRUPTED_MESSAGE)


def test_polling_is_404_outside_the_conversation(fake: FakeChatOracle) -> None:
    """会話が無い・回答が無い・質問（USER）の id は 404。"""
    _answer(fake, _recorder())
    assert (
        client.get(f"/api/chat/conversations/conv-none/messages/{ANSWER_ID}/progress").status_code
        == 404
    )
    missing = client.get("/api/chat/conversations/conv-p/messages/unknown/progress")
    assert missing.status_code == 404
    assert missing.json()["error_messages"] == [chat_route.ANSWER_NOT_FOUND_MESSAGE]
    assert client.get("/api/chat/conversations/conv-p/messages/u-1/progress").status_code == 404
    assert client.get(f"{PROGRESS_PATH}/stream").status_code == 200
    assert (
        client.get("/api/chat/conversations/conv-p/messages/u-1/progress/stream").status_code == 404
    )


# --------------------------------------------------------------------------- #
# SSE
# --------------------------------------------------------------------------- #


async def test_sse_streams_events_written_by_another_worker_until_terminal(
    fake: FakeChatOracle,
) -> None:
    """別の worker が保存した記録を読み直して送り、終端を送ったら閉じる（`id:` は `seq`）。"""
    recorder = _recorder()
    message = _answer(fake, recorder)

    reads_before = fake.message_reads

    async def other_worker() -> None:
        # 別の worker が記録を進めて保存する（この worker のメモリには無い）。SSE が保存先を
        # 読み直し始めてから、heartbeat の間隔より長く待つ（時間ではなく状態で合わせる。
        # 単独の実行では app の初回の起動が遅く、接続より前に待ちが終わっていた。#1397）。
        while fake.message_reads < reads_before + 2:
            await asyncio.sleep(0.005)
        await asyncio.sleep(0.12)
        recorder.start("rerank", exclusive=True)
        message.progress = dump_chat_progress_events(recorder.events)
        await asyncio.sleep(0.05)
        recorder.complete("done")
        message.progress = dump_chat_progress_events(recorder.events)
        message.status = "COMPLETE"

    writer = asyncio.create_task(other_worker())
    async with _async_client() as http:
        response = await http.get(f"{PROGRESS_PATH}/stream?since=4")
    await writer

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    assert response.headers["x-accel-buffering"] == "no"
    assert response.text.startswith("retry: ")
    items = _sse(response.text)
    progress = [(event_id, data) for event_id, name, data in items if name == "chat_progress"]
    # `since` の続きから、重複も欠けもなく終端まで届く。
    assert [event_id for event_id, _ in progress] == list(range(5, recorder.last_seq + 1))
    assert all(event_id == data["seq"] for event_id, data in progress)
    assert progress[-1][1]["type"] == "terminal"
    # 記録の無い間は heartbeat（`last_seq`）を送る。
    heartbeats = [data for _, name, data in items if name == "heartbeat"]
    assert heartbeats and heartbeats[0] == {"last_seq": 6}
    # 1 回ではなく、保存先を何度も読み直している。
    assert fake.message_reads > 3


async def test_sse_resumes_from_last_event_id(fake: FakeChatOracle) -> None:
    """`Last-Event-ID`（ブラウザの張り直し）は `since` より新しければそちらから送る。"""
    recorder = _recorder()
    recorder.complete("failed")
    _answer(fake, recorder, status="ERROR", lease_owner=None)

    async with _async_client() as http:
        response = await http.get(f"{PROGRESS_PATH}/stream?since=2", headers={"Last-Event-ID": "6"})
    ids = [event_id for event_id, name, _ in _sse(response.text) if name == "chat_progress"]
    assert ids == list(range(7, recorder.last_seq + 1))


async def test_sse_is_204_when_finished_answer_is_requested_from_the_end(
    fake: FakeChatOracle,
) -> None:
    """終わった回答を最後の番号から求めたら 204（`EventSource` は張り直しをやめる）。"""
    recorder = _recorder()
    recorder.complete("done")
    _answer(fake, recorder, status="COMPLETE", lease_owner=None)

    async with _async_client() as http:
        finished = await http.get(
            f"{PROGRESS_PATH}/stream", headers={"Last-Event-ID": str(recorder.last_seq)}
        )
        replay = await http.get(f"{PROGRESS_PATH}/stream?since=0")
    assert finished.status_code == 204
    # 最初からなら、終端まで送って閉じる。
    assert replay.status_code == 200
    names = [name for _, name, _ in _sse(replay.text)]
    assert names.count("chat_progress") == recorder.last_seq


async def test_sse_closes_interrupted_answer_with_failed_terminal(fake: FakeChatOracle) -> None:
    """作成していたプロセスが止まった回答は、中断の失敗の終端を送って閉じる。"""
    _answer(fake, _recorder(), heartbeat_at=datetime.now(UTC) - timedelta(seconds=600))

    async with _async_client() as http:
        response = await http.get(f"{PROGRESS_PATH}/stream?since=6")
    progress = [data for _, name, data in _sse(response.text) if name == "chat_progress"]
    assert progress[-1]["type"] == "terminal"
    assert progress[-1]["status"] == "failed"
    assert fake.messages["conv-p"][1].status == "ERROR"


# --------------------------------------------------------------------------- #
# 権限
# --------------------------------------------------------------------------- #


def test_progress_endpoints_require_chat_menu() -> None:
    """polling と SSE は、チャットのメニュー権限で使う（manifest の登録）。"""
    for path in (
        "/chat/conversations/{conversation_id}/messages/{assistant_message_id}/progress",
        "/chat/conversations/{conversation_id}/messages/{assistant_message_id}/progress/stream",
    ):
        assert permission_for_route("GET", path) == frozenset({MENU_CHAT})


def test_progress_endpoints_deny_user_without_chat_permission(
    fake: FakeChatOracle, monkeypatch: MonkeyPatch
) -> None:
    """チャットの権限の無い利用者は 403、ある利用者は読める。"""
    _answer(fake, _recorder())
    auth = enable_production_auth(monkeypatch)
    auth.user_with_permissions("searcher", ["menu.search"])
    auth.user_with_permissions("chatter", [MENU_CHAT])
    searcher = login(client, "searcher")
    for path in (PROGRESS_PATH, f"{PROGRESS_PATH}/stream"):
        assert client.get(path, headers=searcher).status_code == 403, path
    chatter = login(client, "chatter")
    assert client.get(PROGRESS_PATH, headers=chatter).status_code == 200
