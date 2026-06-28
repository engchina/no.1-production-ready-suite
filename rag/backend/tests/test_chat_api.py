"""チャット(会話 / マルチモデル比較)API のテスト。

実 Oracle は起動しないため、OracleClient を fake へ差し替えた決定論テスト。DDL は文字列契約、
SSE は pipeline を stub して event 列と永続化を検証する。実 SQL は CI/staging で検証する。
"""

from datetime import UTC, datetime
from uuid import uuid4

import pytest
from pytest import MonkeyPatch

from app.api.routes import chat as chat_route
from app.clients.oracle import StoredConversation, StoredMessage
from app.config import EnterpriseAiConfiguredModel, get_settings
from app.main import app
from app.rag import oracle_schema
from app.rag.pipeline import (
    ChatTurn,
    SearchTokenDelta,
    _format_chat_history,
    _query_with_history,
)
from app.schemas.search import RetrievedChunk, SearchResponse
from tests.support import AsgiTestClient

client = AsgiTestClient(app)


# --------------------------------------------------------------------------- #
# DDL 契約
# --------------------------------------------------------------------------- #


def test_oracle_schema_includes_chat_tables() -> None:
    """schema artifact に会話 / メッセージ table が FK・制約・索引付きで含まれる。"""
    sql = oracle_schema.oracle_schema_sql()
    assert "-- section: conversations" in sql
    assert "CREATE TABLE rag_conversations" in sql
    assert "-- section: messages" in sql
    assert "CREATE TABLE rag_messages" in sql
    # 業務ビュー配下(FK)・状態制約・比較グルーピング列。
    assert "rag_conversations_business_view_fk" in sql
    assert "CHECK (status IN ('ACTIVE', 'ARCHIVED'))" in sql
    assert "rag_messages_conversation_fk" in sql
    assert "CHECK (role IN ('USER', 'ASSISTANT', 'SYSTEM'))" in sql
    assert "reply_to_message_id" in sql
    assert "content              CLOB" in sql
    assert "citations_json       JSON" in sql


def test_chat_sections_ordered_after_business_views() -> None:
    """FK 依存順(business_views → conversations → messages)で並ぶ。"""
    names = [section.name for section in oracle_schema.oracle_schema_sections()]
    assert names.index("business_views") < names.index("conversations")
    assert names.index("conversations") < names.index("messages")


# --------------------------------------------------------------------------- #
# Fake Oracle
# --------------------------------------------------------------------------- #


class FakeChatOracle:
    """chat API テスト用のインメモリ fake。"""

    def __init__(self) -> None:
        self.conversations: dict[str, StoredConversation] = {}
        self.messages: dict[str, list[StoredMessage]] = {}
        self.business_views: dict[str, str] = {"bv-1": "経理アシスタント"}

    async def get_business_view(self, business_view_id: str) -> object | None:
        if business_view_id not in self.business_views:
            return None
        return object()

    async def create_conversation(
        self, *, business_view_id: str, title: str | None = None
    ) -> StoredConversation:
        now = datetime(2026, 1, 1, tzinfo=UTC)
        conversation = StoredConversation(
            id=f"conv-{uuid4().hex[:8]}",
            business_view_id=business_view_id,
            title=title,
            status="ACTIVE",
            message_count=0,
            created_at=now,
            updated_at=now,
        )
        self.conversations[conversation.id] = conversation
        self.messages[conversation.id] = []
        return conversation

    async def list_conversations(
        self, *, business_view_id: str | None = None, limit: int | None = None, offset: int = 0
    ) -> list[StoredConversation]:
        items = [
            c
            for c in self.conversations.values()
            if business_view_id is None or c.business_view_id == business_view_id
        ]
        return items[offset : (offset + limit) if limit is not None else None]

    async def count_conversations(self, *, business_view_id: str | None = None) -> int:
        return len(await self.list_conversations(business_view_id=business_view_id))

    async def get_conversation(self, conversation_id: str) -> StoredConversation | None:
        return self.conversations.get(conversation_id)

    async def archive_conversation(self, conversation_id: str) -> StoredConversation:
        existing = self.conversations.get(conversation_id)
        if existing is None:
            raise KeyError(conversation_id)
        existing.status = "ARCHIVED"
        return existing

    async def append_message(self, message: StoredMessage) -> StoredMessage:
        stored = StoredMessage(
            id=message.id or uuid4().hex,
            conversation_id=message.conversation_id,
            reply_to_message_id=message.reply_to_message_id,
            role=message.role,
            model=message.model,
            content=message.content,
            citations=message.citations,
            guardrail_warnings=message.guardrail_warnings,
            trace_id=message.trace_id,
            status=message.status,
            elapsed_ms=message.elapsed_ms,
            created_at=message.created_at or datetime(2026, 1, 1, tzinfo=UTC),
        )
        self.messages.setdefault(stored.conversation_id, []).append(stored)
        if stored.conversation_id in self.conversations:
            self.conversations[stored.conversation_id].message_count += 1
        return stored

    async def list_messages(
        self, conversation_id: str, *, limit: int | None = None
    ) -> list[StoredMessage]:
        return list(self.messages.get(conversation_id, []))


@pytest.fixture
def fake_oracle(monkeypatch: MonkeyPatch) -> FakeChatOracle:
    fake = FakeChatOracle()
    monkeypatch.setattr(chat_route, "OracleClient", lambda *a, **k: fake)
    return fake


# --------------------------------------------------------------------------- #
# CRUD
# --------------------------------------------------------------------------- #


def test_create_and_get_conversation(fake_oracle: FakeChatOracle) -> None:
    """業務ビュー配下に会話を作り、詳細を取得できる。"""
    created = client.post(
        "/api/chat/conversations",
        json={"business_view_id": "bv-1", "title": " 経費精算の相談 "},
    )
    assert created.status_code == 200
    data = created.json()["data"]
    assert data["business_view_id"] == "bv-1"
    assert data["title"] == "経費精算の相談"
    assert data["messages"] == []

    detail = client.get(f"/api/chat/conversations/{data['id']}")
    assert detail.status_code == 200
    assert detail.json()["data"]["id"] == data["id"]


def test_create_conversation_rejects_unknown_business_view(fake_oracle: FakeChatOracle) -> None:
    """存在しない業務ビューでは作成できない。"""
    resp = client.post("/api/chat/conversations", json={"business_view_id": "bv-missing"})
    assert resp.status_code == 404


def test_list_and_archive_conversation(fake_oracle: FakeChatOracle) -> None:
    """会話を一覧・アーカイブできる。"""
    created = client.post("/api/chat/conversations", json={"business_view_id": "bv-1"}).json()[
        "data"
    ]
    page = client.get("/api/chat/conversations?business_view_id=bv-1").json()["data"]
    assert page["total"] == 1
    archived = client.post(f"/api/chat/conversations/{created['id']}/archive")
    assert archived.status_code == 200
    assert archived.json()["data"]["status"] == "ARCHIVED"


def test_chat_endpoints_return_404_when_disabled(
    fake_oracle: FakeChatOracle, monkeypatch: MonkeyPatch
) -> None:
    """flag OFF のとき会話 API は 404(運用キルスイッチ)。"""
    monkeypatch.setattr(get_settings(), "rag_chat_enabled", False)
    assert client.get("/api/chat/conversations").status_code == 404
    assert (
        client.post("/api/chat/conversations", json={"business_view_id": "bv-1"}).status_code == 404
    )
    assert client.get("/api/chat/models").status_code == 404


# --------------------------------------------------------------------------- #
# 比較モデル解決 / 履歴
# --------------------------------------------------------------------------- #


def test_resolve_compare_models_caps_and_defaults(monkeypatch: MonkeyPatch) -> None:
    """指定モデルを catalog で絞り、上限を超えない。未指定なら既定 1 系統。"""
    settings = get_settings()
    monkeypatch.setattr(settings, "rag_chat_max_compare_models", 2)
    catalog = [
        EnterpriseAiConfiguredModel(model_id="m1", display_name="モデル1"),
        EnterpriseAiConfiguredModel(model_id="m2", display_name="モデル2"),
        EnterpriseAiConfiguredModel(model_id="m3", display_name="モデル3"),
    ]
    monkeypatch.setattr(chat_route, "enterprise_ai_model_catalog", lambda _s: catalog)
    monkeypatch.setattr(chat_route, "enterprise_ai_default_model_id", lambda _s: "m1")

    from app.schemas.chat import ChatMessageRequest

    selected = chat_route._resolve_compare_models(
        ChatMessageRequest(content="質問", model_ids=["m1", "m2", "m3"]), settings
    )
    assert [c["model_id"] for c in selected] == ["m1", "m2"]

    default_only = chat_route._resolve_compare_models(ChatMessageRequest(content="質問"), settings)
    assert [c["model_id"] for c in default_only] == ["m1"]


def test_build_history_takes_first_assistant_per_turn() -> None:
    """同一ユーザーターンに複数モデル回答があっても履歴は先頭 1 件だけ採用。"""
    now = datetime(2026, 1, 1, tzinfo=UTC)
    messages = [
        StoredMessage(id="u1", conversation_id="c", role="USER", content="質問1", created_at=now),
        StoredMessage(
            id="a1a",
            conversation_id="c",
            role="ASSISTANT",
            content="回答A",
            reply_to_message_id="u1",
            model="m1",
            created_at=now,
        ),
        StoredMessage(
            id="a1b",
            conversation_id="c",
            role="ASSISTANT",
            content="回答B",
            reply_to_message_id="u1",
            model="m2",
            created_at=now,
        ),
    ]
    turns = chat_route._build_history(messages)
    assert [(t.role, t.content) for t in turns] == [("USER", "質問1"), ("ASSISTANT", "回答A")]


def test_format_chat_history_limits_turns_and_chars() -> None:
    """履歴はターン数と 1 ターン文字数で抑制される。"""
    history = [
        ChatTurn(role="USER", content="あ" * 10),
        ChatTurn(role="ASSISTANT", content="い" * 10),
    ]
    text = _format_chat_history(history, max_turns=1, chars_per_turn=3)
    assert text.startswith("アシスタント: ") or text.startswith("アシスタント:")
    assert "いいい…" in text  # 末尾切り詰め
    assert "あ" not in text  # max_turns=1 で先頭ターンは落ちる
    assert _format_chat_history(history, max_turns=0, chars_per_turn=100) == ""


def test_query_with_history_prefixes_question() -> None:
    """履歴ありなら今回の質問を後ろに置いた生成クエリを作る。"""
    built = _query_with_history("ユーザー: 前回の質問", "今回の質問")
    assert "これまでの会話:" in built
    assert built.rstrip().endswith("今回の質問")
    assert _query_with_history("", "そのまま") == "そのまま"


# --------------------------------------------------------------------------- #
# SSE ストリーミング(マルチモデル)
# --------------------------------------------------------------------------- #


class _FakePipeline:
    """retrieval/generation を行わず回答を即返す pipeline stub。"""

    def __init__(self, *args: object, **kwargs: object) -> None:
        self._llm = kwargs.get("llm")

    async def run(  # type: ignore[no-untyped-def]
        self, request, trace_id=None, progress_callback=None, token_callback=None, *, history=None
    ):
        if token_callback is not None:
            delta = SearchTokenDelta(trace_id=trace_id or "t", text="回答")
            await token_callback(delta)
        return SearchResponse(
            answer="回答",
            citations=[RetrievedChunk(document_id="d1", chunk_id="ch1", text="根拠", score=0.9)],
            trace_id=trace_id or "trace",
            elapsed_ms=1.0,
        )


def _stub_stream(monkeypatch: MonkeyPatch, fake: FakeChatOracle, models: list[str]) -> None:
    monkeypatch.setattr(chat_route, "OracleClient", lambda *a, **k: fake)
    monkeypatch.setattr(chat_route, "RagPipeline", _FakePipeline)

    async def fake_resolve(request, settings):  # type: ignore[no-untyped-def]
        return request, settings, None, None

    monkeypatch.setattr(chat_route, "_resolve_query_context", fake_resolve)
    catalog = [EnterpriseAiConfiguredModel(model_id=m, display_name=m.upper()) for m in models]
    monkeypatch.setattr(chat_route, "enterprise_ai_model_catalog", lambda _s: catalog)
    monkeypatch.setattr(chat_route, "enterprise_ai_default_model_id", lambda _s: models[0])


def test_stream_message_single_model_persists_and_streams(monkeypatch: MonkeyPatch) -> None:
    """単一モデル: USER/ASSISTANT を永続化し、回答 + 引用を SSE で流す。"""
    fake = FakeChatOracle()
    conv = StoredConversation(
        id="conv-x",
        business_view_id="bv-1",
        status="ACTIVE",
        created_at=datetime(2026, 1, 1, tzinfo=UTC),
        updated_at=datetime(2026, 1, 1, tzinfo=UTC),
    )
    fake.conversations["conv-x"] = conv
    fake.messages["conv-x"] = []
    _stub_stream(monkeypatch, fake, ["m1"])

    resp = client.post(
        "/api/chat/conversations/conv-x/messages/stream", json={"content": "経費の上限は?"}
    )
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/event-stream")
    text = resp.text
    assert "event: start" in text
    assert "event: delta" in text
    assert "event: citations" in text
    assert "event: done" in text
    assert "event: all_done" in text
    # USER + ASSISTANT が永続化される。
    roles = [m.role for m in fake.messages["conv-x"]]
    assert roles == ["USER", "ASSISTANT"]
    assistant = fake.messages["conv-x"][1]
    assert assistant.content == "回答"
    assert assistant.reply_to_message_id == fake.messages["conv-x"][0].id
    assert assistant.citations  # 引用が保存される


def test_stream_message_multi_model_compares_two_columns(monkeypatch: MonkeyPatch) -> None:
    """マルチモデル: 2 カラム分の回答を流し、ASSISTANT を 2 件永続化する。"""
    fake = FakeChatOracle()
    fake.conversations["conv-y"] = StoredConversation(
        id="conv-y",
        business_view_id="bv-1",
        status="ACTIVE",
        created_at=datetime(2026, 1, 1, tzinfo=UTC),
        updated_at=datetime(2026, 1, 1, tzinfo=UTC),
    )
    fake.messages["conv-y"] = []
    _stub_stream(monkeypatch, fake, ["m1", "m2"])

    resp = client.post(
        "/api/chat/conversations/conv-y/messages/stream",
        json={"content": "比較して", "model_ids": ["m1", "m2"]},
    )
    assert resp.status_code == 200
    text = resp.text
    assert text.count("event: done") == 2
    assert '"m1"' in text and '"m2"' in text
    roles = [m.role for m in fake.messages["conv-y"]]
    assert roles.count("USER") == 1
    assert roles.count("ASSISTANT") == 2


def test_stream_message_rejects_archived_conversation(monkeypatch: MonkeyPatch) -> None:
    """アーカイブ済みの会話には送信できない。"""
    fake = FakeChatOracle()
    fake.conversations["conv-z"] = StoredConversation(
        id="conv-z",
        business_view_id="bv-1",
        status="ARCHIVED",
        created_at=datetime(2026, 1, 1, tzinfo=UTC),
        updated_at=datetime(2026, 1, 1, tzinfo=UTC),
    )
    _stub_stream(monkeypatch, fake, ["m1"])
    resp = client.post("/api/chat/conversations/conv-z/messages/stream", json={"content": "送信"})
    assert resp.status_code == 409
