"""チャットの回答の作成を接続から切り離すテスト（#1175）。

SSE の接続が切れても作成が最後まで進んで保存されること、再購読で続きを受け取れること、
明示の取消で止まること、作成していたプロセスが止まった作成中の回答が取得時に失敗になることを、
OracleClient の fake と決定論の pipeline の stub で確かめる（LLM・Oracle は呼ばない）。
"""

import asyncio
import json
from collections.abc import Callable
from datetime import UTC, datetime, timedelta

import httpx
import pytest
from pytest import MonkeyPatch

from app.api.routes import chat as chat_route
from app.clients.oracle import StoredMessage
from app.config import get_settings
from app.main import app
from app.rag import chat_answer_runs
from app.rag.chat_answer_runs import (
    CHAT_ANSWER_CANCELLED_MESSAGE,
    CHAT_ANSWER_INTERRUPTED_MESSAGE,
    ChatAnswerRunService,
)
from app.rag.pipeline import SearchStageProgress
from tests.support import TEST_REQUEST_HEADERS
from tests.test_chat_api import (
    FakeChatOracle,
    _chat_conversation,
    _FakePipeline,
    _sse_events,
    _stub_stream,
)

CLIENT_MESSAGE_ID = "0123456789abcdef0123456789abcdef"


class _GatedPipeline(_FakePipeline):
    """段階を 1 つ送った後、`release` が set されるまで回答を返さない pipeline。"""

    release: asyncio.Event | None = None
    started: asyncio.Event | None = None
    cancelled: list[bool] = []

    async def run(  # type: ignore[no-untyped-def]
        self,
        request,
        trace_id=None,
        progress_callback=None,
        token_callback=None,
        *,
        history=None,
        query_guardrail_result=None,
    ):
        assert progress_callback is not None
        await progress_callback(
            SearchStageProgress(
                trace_id=trace_id or "trace",
                stage="answer_step:文書検索",
                outcome="started",
                elapsed_ms=0.0,
                attributes={},
            )
        )
        assert _GatedPipeline.started is not None and _GatedPipeline.release is not None
        _GatedPipeline.started.set()
        try:
            await _GatedPipeline.release.wait()
        except asyncio.CancelledError:
            _GatedPipeline.cancelled.append(True)
            raise
        return await super().run(  # type: ignore[no-untyped-call]
            request,
            trace_id,
            None,
            token_callback,
            history=history,
            query_guardrail_result=query_guardrail_result,
        )


@pytest.fixture
def service(monkeypatch: MonkeyPatch) -> ChatAnswerRunService:
    fresh = ChatAnswerRunService(worker_id="pytest-worker")
    monkeypatch.setattr(chat_answer_runs, "_SERVICE", fresh)
    return fresh


class _Gate:
    """テストから操作する、_GatedPipeline の合図。"""

    def __init__(self) -> None:
        self.release = asyncio.Event()
        self.started = asyncio.Event()
        self.cancelled: list[bool] = []


@pytest.fixture
def gated(monkeypatch: MonkeyPatch) -> _Gate:
    gate = _Gate()
    monkeypatch.setattr(_GatedPipeline, "release", gate.release)
    monkeypatch.setattr(_GatedPipeline, "started", gate.started)
    monkeypatch.setattr(_GatedPipeline, "cancelled", gate.cancelled)
    monkeypatch.setattr(chat_route, "RagPipeline", _GatedPipeline)
    return gate


@pytest.fixture
def fake(monkeypatch: MonkeyPatch) -> FakeChatOracle:
    oracle = FakeChatOracle()
    _chat_conversation(oracle, "conv-r")
    _stub_stream(monkeypatch, oracle, ["m1"])
    return oracle


async def _post_stream_until_disconnect(
    path: str,
    body: dict[str, object],
    *,
    disconnect_when: Callable[[str], bool],
) -> tuple[int, str]:
    """ASGI で SSE を送り、`disconnect_when(受信した本文)` が真になったら接続を切る。

    httpx の ASGITransport は応答を最後まで読むので、接続の切断（`http.disconnect`）を
    送れるよう ASGI を直接呼ぶ（uvicorn の切断と同じく Starlette が generator を止める）。
    """
    received: list[str] = []
    status: list[int] = []
    disconnect = asyncio.Event()
    body_bytes = json.dumps(body).encode("utf-8")
    sent = False

    async def receive() -> dict[str, object]:
        nonlocal sent
        if not sent:
            sent = True
            return {"type": "http.request", "body": body_bytes, "more_body": False}
        await disconnect.wait()
        return {"type": "http.disconnect"}

    async def send(message: dict[str, object]) -> None:
        if message["type"] == "http.response.start":
            status.append(int(message["status"]))  # type: ignore[call-overload]
        elif message["type"] == "http.response.body":
            chunk = message.get("body", b"")
            assert isinstance(chunk, bytes)
            received.append(chunk.decode("utf-8"))
            if disconnect_when("".join(received)):
                disconnect.set()

    headers = [
        (b"host", b"testserver"),
        (b"content-type", b"application/json"),
        (b"accept", b"text/event-stream"),
        *[(key.lower().encode(), value.encode()) for key, value in TEST_REQUEST_HEADERS.items()],
    ]
    scope = {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": "POST",
        "scheme": "http",
        "path": path,
        "raw_path": path.encode(),
        "query_string": b"",
        "root_path": "",
        "headers": headers,
        "client": ("127.0.0.1", 50000),
        "server": ("testserver", 80),
    }
    await asyncio.wait_for(app(scope, receive, send), timeout=10)  # type: ignore[arg-type]
    return status[0], "".join(received)


def _client() -> httpx.AsyncClient:
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://testserver",
        headers=TEST_REQUEST_HEADERS,
    )


def _assistant(fake: FakeChatOracle, conversation_id: str = "conv-r") -> StoredMessage:
    replies = [m for m in fake.messages[conversation_id] if m.role == "ASSISTANT"]
    assert len(replies) == 1
    return replies[0]


async def test_disconnect_does_not_stop_answer_and_saves_it(
    fake: FakeChatOracle, gated: _Gate, service: ChatAnswerRunService
) -> None:
    """SSE を途中で切っても作成は最後まで進み、回答と処理の経過が保存される。"""
    status, text = await _post_stream_until_disconnect(
        "/api/chat/conversations/conv-r/messages/stream",
        {"content": "経費の上限は?", "client_message_id": CLIENT_MESSAGE_ID},
        disconnect_when=lambda body: "event: start" in body,
    )
    assert status == 200
    assert "event: start" in text
    assert "event: all_done" not in text
    # 切断の後も、作成中の回答は STREAMING のまま残り、作成は止まっていない。
    await asyncio.wait_for(gated.started.wait(), timeout=5)
    assert _assistant(fake).status == "STREAMING"
    assert service.is_running(CLIENT_MESSAGE_ID)

    gated.release.set()
    await asyncio.wait_for(service.wait_for(CLIENT_MESSAGE_ID), timeout=5)

    user, assistant = fake.messages["conv-r"]
    assert user.id == CLIENT_MESSAGE_ID
    assert (assistant.status, assistant.content) == ("COMPLETE", "回答")
    assert assistant.reply_to_message_id == CLIENT_MESSAGE_ID
    assert assistant.citations
    assert gated.cancelled == []
    # 処理の経過（終わった段階）も保存する（再読込の後も回答の上に出せる）。
    assert assistant.progress is not None
    assert {step["status"] for step in assistant.progress} <= {"done", "skipped"}


async def test_start_event_carries_the_streaming_message_ids(
    fake: FakeChatOracle, gated: _Gate, service: ChatAnswerRunService
) -> None:
    """`start` は作成中のメッセージの id を列ごとに持ち、event には再購読の連番が付く。"""
    _status, text = await _post_stream_until_disconnect(
        "/api/chat/conversations/conv-r/messages/stream",
        {"content": "経費の上限は?"},
        disconnect_when=lambda body: "event: start" in body,
    )
    assert text.startswith("id: 1\nevent: start\n")
    start = _sse_events(text, "start")[0]
    columns = start["columns"]
    assert isinstance(columns, list)
    assert columns[0]["message_id"] == _assistant(fake).id
    gated.release.set()
    user_id = fake.messages["conv-r"][0].id
    await asyncio.wait_for(service.wait_for(user_id), timeout=5)


async def test_resume_stream_continues_after_last_event_id(
    fake: FakeChatOracle, gated: _Gate, service: ChatAnswerRunService
) -> None:
    """再購読（`Last-Event-ID`）では、続きの event から作成の終わりまでを受け取る。"""
    _status, text = await _post_stream_until_disconnect(
        "/api/chat/conversations/conv-r/messages/stream",
        {"content": "経費の上限は?", "client_message_id": CLIENT_MESSAGE_ID},
        disconnect_when=lambda body: "event: start" in body,
    )
    # 切断までに受け取った最後の event の連番。
    last_id = max(int(line[4:]) for line in text.splitlines() if line.startswith("id: "))
    await asyncio.wait_for(gated.started.wait(), timeout=5)
    gated.release.set()
    async with _client() as client:
        resumed = await client.get(
            f"/api/chat/conversations/conv-r/messages/{CLIENT_MESSAGE_ID}/stream",
            headers={"Last-Event-ID": str(last_id)},
        )
    assert resumed.status_code == 200
    assert resumed.headers["content-type"].startswith("text/event-stream")
    body = resumed.text
    assert "event: start" not in body
    # 続きの event から、重複も欠けもなく受け取る。
    resumed_ids = [int(line[4:]) for line in body.splitlines() if line.startswith("id: ")]
    assert resumed_ids == list(range(last_id + 1, last_id + 1 + len(resumed_ids)))
    for name in ("progress", "delta", "citations", "done", "all_done"):
        assert f"event: {name}" in body
    done = _sse_events(body, "done")[0]
    assert done["message_id"] == _assistant(fake).id
    # `after` の指定も同じ（作成が終わった後も、記録を残す間は最初から読める）。
    async with _client() as client:
        replay = await client.get(
            f"/api/chat/conversations/conv-r/messages/{CLIENT_MESSAGE_ID}/stream?after=0"
        )
    assert replay.text.startswith("id: 1\nevent: start\n")
    assert "event: all_done" in replay.text


async def test_resume_stream_is_404_when_not_running_here(
    fake: FakeChatOracle, service: ChatAnswerRunService
) -> None:
    """このプロセスに作成の記録が無ければ 404（画面は保存済みの会話を取り直す）。"""
    async with _client() as client:
        response = await client.get(
            f"/api/chat/conversations/conv-r/messages/{CLIENT_MESSAGE_ID}/stream"
        )
        missing_conversation = await client.get(
            f"/api/chat/conversations/conv-none/messages/{CLIENT_MESSAGE_ID}/stream"
        )
    assert response.status_code == 404
    assert response.json()["error_messages"] == [chat_route.ANSWER_STREAM_NOT_FOUND_MESSAGE]
    assert missing_conversation.status_code == 404


async def test_cancel_stops_answer_and_marks_it_cancelled(
    fake: FakeChatOracle, gated: _Gate, service: ChatAnswerRunService
) -> None:
    """明示の取消で作成を止め、作成中の回答を停止（CANCELLED）として保存する。"""
    await _post_stream_until_disconnect(
        "/api/chat/conversations/conv-r/messages/stream",
        {"content": "経費の上限は?", "client_message_id": CLIENT_MESSAGE_ID},
        disconnect_when=lambda body: "event: start" in body,
    )
    await asyncio.wait_for(gated.started.wait(), timeout=5)
    async with _client() as client:
        response = await client.post(
            f"/api/chat/conversations/conv-r/messages/{CLIENT_MESSAGE_ID}/cancel"
        )
        replay = await client.get(
            f"/api/chat/conversations/conv-r/messages/{CLIENT_MESSAGE_ID}/stream?after=0"
        )
    assert response.status_code == 200
    assert response.json()["data"] == {"cancelled": True}
    assert gated.cancelled == [True]
    assert not service.is_running(CLIENT_MESSAGE_ID)
    assistant = _assistant(fake)
    assert (assistant.status, assistant.content) == ("CANCELLED", CHAT_ANSWER_CANCELLED_MESSAGE)
    # 購読している接続には、停止の error と all_done が届く。
    error = _sse_events(replay.text, "error")[0]
    assert error["cancelled"] is True
    assert "event: all_done" in replay.text
    # 会話の取得では停止として返る（再読込しても作成中にしない）。
    async with _client() as client:
        detail = (await client.get("/api/chat/conversations/conv-r")).json()["data"]
    assert detail["messages"][1]["status"] == "CANCELLED"


async def test_cancel_before_start_cancels_when_the_answer_starts(
    fake: FakeChatOracle, gated: _Gate, service: ChatAnswerRunService
) -> None:
    """送信の直後（`start` の前）の停止も、画面が決めた質問の id で取り消せる。"""
    async with _client() as client:
        response = await client.post(
            f"/api/chat/conversations/conv-r/messages/{CLIENT_MESSAGE_ID}/cancel"
        )
        assert response.json()["data"] == {"cancelled": True}
        stream = await client.post(
            "/api/chat/conversations/conv-r/messages/stream",
            json={"content": "経費の上限は?", "client_message_id": CLIENT_MESSAGE_ID},
        )
    assert stream.status_code == 200
    assert "event: start" in stream.text
    assert _sse_events(stream.text, "error")[0]["cancelled"] is True
    assert "event: all_done" in stream.text
    assert not gated.started.is_set()
    assert _assistant(fake).status == "CANCELLED"


async def test_cancel_from_another_process_closes_streaming_answer(
    fake: FakeChatOracle, service: ChatAnswerRunService
) -> None:
    """別のプロセスが作成中の回答も、取消で停止にする（そのプロセスは heartbeat で止まる）。"""
    now = datetime.now(UTC)
    fake.messages["conv-r"] = [
        StoredMessage(id="u-1", conversation_id="conv-r", role="USER", content="q", created_at=now),
        StoredMessage(
            id="a-1",
            conversation_id="conv-r",
            role="ASSISTANT",
            content="",
            status="STREAMING",
            reply_to_message_id="u-1",
            lease_owner="other-host:1:abc",
            heartbeat_at=now,
            created_at=now,
        ),
    ]
    async with _client() as client:
        response = await client.post("/api/chat/conversations/conv-r/messages/u-1/cancel")
    assert response.json()["data"] == {"cancelled": True}
    assert fake.messages["conv-r"][1].status == "CANCELLED"


async def test_heartbeat_stops_the_task_when_cancelled_elsewhere(
    fake: FakeChatOracle, gated: _Gate, monkeypatch: MonkeyPatch
) -> None:
    """別のプロセスで停止にされたら、作成中のプロセスは次の heartbeat で作成を止める。"""
    service = ChatAnswerRunService(worker_id="pytest-worker", heartbeat_seconds=0.05)
    monkeypatch.setattr(chat_answer_runs, "_SERVICE", service)
    await _post_stream_until_disconnect(
        "/api/chat/conversations/conv-r/messages/stream",
        {"content": "経費の上限は?", "client_message_id": CLIENT_MESSAGE_ID},
        disconnect_when=lambda body: "event: start" in body,
    )
    await asyncio.wait_for(gated.started.wait(), timeout=5)
    assistant = _assistant(fake)
    assistant.status = "CANCELLED"
    assistant.content = CHAT_ANSWER_CANCELLED_MESSAGE
    await asyncio.wait_for(service.wait_for(CLIENT_MESSAGE_ID), timeout=5)
    assert gated.cancelled == [True]
    assert _assistant(fake).status == "CANCELLED"


async def test_interrupted_streaming_answers_become_errors_on_get(
    fake: FakeChatOracle, service: ChatAnswerRunService
) -> None:
    """作成していたプロセスが止まった作成中の回答は、会話の取得で中断の失敗にする。

    - このプロセスが作っていたのに動いていない回答: すぐ失敗。
    - 別のプロセスで heartbeat が途絶えた回答: 失敗。
    - 別のプロセスで heartbeat が新しい回答: 作成中のまま。
    """
    now = datetime.now(UTC)

    def streaming(message_id: str, reply_to: str, owner: str, heartbeat: datetime) -> StoredMessage:
        return StoredMessage(
            id=message_id,
            conversation_id="conv-r",
            role="ASSISTANT",
            content="",
            status="STREAMING",
            reply_to_message_id=reply_to,
            lease_owner=owner,
            heartbeat_at=heartbeat,
            created_at=now,
            progress=[{"id": "retrieve", "label": "文書を探しています", "status": "running"}],
        )

    def question(message_id: str) -> StoredMessage:
        return StoredMessage(
            id=message_id, conversation_id="conv-r", role="USER", content="q", created_at=now
        )

    fake.messages["conv-r"] = [
        question("u-1"),
        streaming("a-own", "u-1", "pytest-worker", now),
        question("u-2"),
        streaming("a-stale", "u-2", "other:1:x", now - timedelta(seconds=600)),
        question("u-3"),
        streaming("a-live", "u-3", "other:1:x", now),
    ]
    async with _client() as client:
        detail = (await client.get("/api/chat/conversations/conv-r")).json()["data"]
    by_id = {message["message_id"]: message for message in detail["messages"]}
    assert by_id["a-own"]["status"] == "ERROR"
    assert by_id["a-own"]["content"] == CHAT_ANSWER_INTERRUPTED_MESSAGE
    assert by_id["a-stale"]["status"] == "ERROR"
    assert by_id["a-live"]["status"] == "STREAMING"
    # 作成中の段階は会話の取得でも返す（再読込で「作成中」と今の段階を出す）。
    assert by_id["a-live"]["progress"][0]["status"] == "running"


async def test_active_answers_per_user_are_limited(
    fake: FakeChatOracle,
    gated: _Gate,
    service: ChatAnswerRunService,
    monkeypatch: MonkeyPatch,
) -> None:
    """同じ利用者の作成中の回答が上限なら、質問を保存せずに 429 を返す。"""
    monkeypatch.setattr(get_settings(), "rag_chat_max_active_answers_per_user", 1)
    await _post_stream_until_disconnect(
        "/api/chat/conversations/conv-r/messages/stream",
        {"content": "1 つ目", "client_message_id": CLIENT_MESSAGE_ID},
        disconnect_when=lambda body: "event: start" in body,
    )
    async with _client() as client:
        second = await client.post(
            "/api/chat/conversations/conv-r/messages/stream", json={"content": "2 つ目"}
        )
    assert second.status_code == 429
    assert "上限の 1 件" in second.json()["error_messages"][0]
    assert [m.content for m in fake.messages["conv-r"] if m.role == "USER"] == ["1 つ目"]
    gated.release.set()
    await asyncio.wait_for(service.wait_for(CLIENT_MESSAGE_ID), timeout=5)


async def test_duplicate_client_message_id_is_rejected(
    fake: FakeChatOracle, service: ChatAnswerRunService
) -> None:
    """同じ質問の id で 2 回送ったら、2 回目は保存せずに 409。"""
    async with _client() as client:
        first = await client.post(
            "/api/chat/conversations/conv-r/messages/stream",
            json={"content": "経費の上限は?", "client_message_id": CLIENT_MESSAGE_ID},
        )
        second = await client.post(
            "/api/chat/conversations/conv-r/messages/stream",
            json={"content": "経費の上限は?", "client_message_id": CLIENT_MESSAGE_ID},
        )
    assert first.status_code == 200
    assert second.status_code == 409
    assert len([m for m in fake.messages["conv-r"] if m.role == "USER"]) == 1


async def test_shutdown_marks_running_answers_interrupted(
    fake: FakeChatOracle, gated: _Gate, service: ChatAnswerRunService
) -> None:
    """backend の停止では、このプロセスの作成を打ち切り、中断の失敗として保存する。"""
    await _post_stream_until_disconnect(
        "/api/chat/conversations/conv-r/messages/stream",
        {"content": "経費の上限は?", "client_message_id": CLIENT_MESSAGE_ID},
        disconnect_when=lambda body: "event: start" in body,
    )
    await asyncio.wait_for(gated.started.wait(), timeout=5)
    await service.shutdown()
    assistant = _assistant(fake)
    assert (assistant.status, assistant.content) == ("ERROR", CHAT_ANSWER_INTERRUPTED_MESSAGE)


def test_message_schema_and_migration_add_answer_run_columns() -> None:
    """作成中の回答の列と CANCELLED の状態を、DDL と migration の両方に持つ。"""
    from app.clients.oracle import oracle_message_schema_sql
    from app.rag import oracle_schema

    ddl = oracle_message_schema_sql()
    for column in ("progress_json        JSON", "lease_owner", "heartbeat_at"):
        assert column in ddl
    assert "'STREAMING', 'COMPLETE', 'ERROR', 'CANCELLED'" in ddl
    migration = oracle_schema.oracle_schema_migration_sections()[-1]
    assert migration.name == "20261005_001_message_answer_runs"
    assert not migration.destructive
    for column in ("PROGRESS_JSON", "LEASE_OWNER", "HEARTBEAT_AT"):
        assert f"column_name = '{column}'" in migration.sql
    assert "''CANCELLED''" in migration.sql


def test_close_streaming_sql_only_touches_streaming_rows(monkeypatch: MonkeyPatch) -> None:
    """閉じる更新は STREAMING の行だけに当て、中断の判定は DB の時刻の heartbeat で行う。"""
    from collections.abc import Mapping

    from app.clients import oracle as oracle_module

    captured: list[tuple[str, Mapping[str, object]]] = []

    def fake_execute_count(_connection: object, statement: str, binds: Mapping[str, object]) -> int:
        captured.append((statement, binds))
        return 1

    class _Client(oracle_module.OracleClient):
        async def _run_transaction(self, operation):  # type: ignore[no-untyped-def]
            return operation(object())

    monkeypatch.setattr(oracle_module, "_execute_count", fake_execute_count)
    client = _Client.__new__(_Client)
    assert asyncio.run(
        client.close_streaming_chat_message(
            "a-1",
            status="ERROR",
            content="中断",
            lease_owner="me",
            stale_seconds=60,
            scoped=False,
        )
    )
    statement, binds = captured[0]
    assert "m.status = 'STREAMING'" in statement
    assert "NUMTODSINTERVAL(:stale_seconds, 'SECOND')" in statement
    assert "m.lease_owner = :lease_owner" in statement
    assert binds["stale_seconds"] == 60.0
    with pytest.raises(ValueError):
        asyncio.run(client.close_streaming_chat_message("a-1", status="COMPLETE", content=""))
