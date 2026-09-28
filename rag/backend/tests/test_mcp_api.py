"""RAG の MCP サーバー（`POST /api/mcp`。#232）のテスト。

production mode（InMemory の共通認証）で、Agent が作るサービストークンの利用者として呼ぶ。
Oracle・LLM は既存の fake / スタブ（検索: 範囲で絞る fake の業務ビュー、チャット: インメモリの
会話と固定回答の pipeline）に差し替え、実サービスは呼ばない。
"""

from __future__ import annotations

from typing import Any

import pytest
from pr_system_settings.auth.service_token import issue_service_token
from pytest import MonkeyPatch

from app.api.routes import business_views as business_views_route
from app.api.routes import chat as chat_route
from app.api.routes import search as search_route
from app.clients.oracle import StoredConversation
from app.config import get_settings
from app.main import app
from app.rag import request_context
from app.rag.request_context import AuditRequestContext, current_audit_request_context
from app.schemas.search import RetrievedChunk, SearchRequest, SearchResponse
from app.security.permissions import SCOPE_FORBIDDEN_CODE, permission_for_route
from tests.security_support import ProductionAuth, enable_production_auth, login
from tests.support import AsgiTestClient
from tests.test_chat_api import FakeChatOracle, _stub_stream
from tests.test_search_business_view import RecordingPipeline
from tests.test_security_api import ScopedFakeOracle
from tests.test_security_scope import _captured_knowledge_base_ids, _install_search

client = AsgiTestClient(app)
SECRET = "rag-mcp-test-secret-0123456789abcdef"  # nosec B105 - テスト用
ALL_TOOLS = [
    "rag_list_business_views",
    "rag_search",
    "rag_chat_send_message",
    "rag_chat_get_conversation",
]


@pytest.fixture
def auth(monkeypatch: MonkeyPatch) -> ProductionAuth:
    production = enable_production_auth(monkeypatch)
    monkeypatch.setattr(get_settings(), "app_service_token_secret", SECRET)
    return production


def _token(
    user_uuid: str,
    *,
    audience: str = "rag",
    secret: str = SECRET,
    claims: dict[str, Any] | None = None,
) -> dict[str, str]:
    token = issue_service_token(
        secret,
        subject=user_uuid,
        audience=audience,
        issuer="agent",
        claims=claims if claims is not None else {"run_id": "run-1", "agent_id": "agent-1"},
    )
    return {"Authorization": f"Bearer {token}"}


def _rpc(method: str, params: dict[str, Any] | None = None, **kwargs: Any) -> Any:
    return client.post(
        "/api/mcp",
        json={"jsonrpc": "2.0", "id": 1, "method": method, "params": params or {}},
        **kwargs,
    )


def _call(name: str, arguments: dict[str, Any], headers: dict[str, str]) -> dict[str, Any]:
    response = _rpc("tools/call", {"name": name, "arguments": arguments}, headers=headers)
    assert response.status_code == 200, response.text
    result: dict[str, Any] = response.json()["result"]
    return result


def _tool_names(headers: dict[str, str]) -> list[str]:
    response = _rpc("tools/list", headers=headers)
    assert response.status_code == 200, response.text
    return [tool["name"] for tool in response.json()["result"]["tools"]]


# ---------------------------------------------------------------------------
# 認証・manifest・tools/list
# ---------------------------------------------------------------------------


def test_mcp_is_classified_as_authenticated_only() -> None:
    """`/mcp` は manifest で「認証済みなら通す」（権限はツールごと）。"""
    assert permission_for_route("POST", "/mcp") is None


def test_mcp_requires_valid_service_token(auth: ProductionAuth) -> None:
    user = auth.user_with_permissions("agent-user", ["menu.search"])
    assert _rpc("tools/list").status_code == 401
    assert _rpc("tools/list", headers={"Authorization": "Bearer broken"}).status_code == 401
    assert _rpc("tools/list", headers=_token(user.user_uuid, audience="nl2sql")).status_code == 401
    wrong_secret = _token(user.user_uuid, secret="another-secret-0123456789abcdefghij")
    assert _rpc("tools/list", headers=wrong_secret).status_code == 401
    # 画面の session Cookie では MCP を呼べない（サービストークンだけ）。
    session = login(client, "agent-user")
    assert _rpc("tools/list", headers=session).status_code == 401
    assert _rpc("tools/list", headers=_token(user.user_uuid)).status_code == 200


def test_mcp_is_unavailable_without_secret(auth: ProductionAuth, monkeypatch: MonkeyPatch) -> None:
    user = auth.user_with_permissions("agent-user", ["menu.search"])
    headers = _token(user.user_uuid)
    monkeypatch.setattr(get_settings(), "app_service_token_secret", "")
    assert _rpc("tools/list", headers=headers).status_code == 503


def test_initialize_and_tools_list_follow_user_permissions(auth: ProductionAuth) -> None:
    searcher = auth.user_with_permissions("searcher", ["menu.search"])
    chatter = auth.user_with_permissions("chatter", ["menu.chat"])
    viewer = auth.user_with_permissions("viewer", ["menu.upload"])
    admin = auth.create_user("admin", system_admin=True)

    initialized = _rpc(
        "initialize", {"protocolVersion": "2025-06-18"}, headers=_token(searcher.user_uuid)
    )
    assert initialized.status_code == 200
    server_info = initialized.json()["result"]["serverInfo"]
    assert server_info == {"name": "production-ready-rag", "version": get_settings().app_version}

    assert _tool_names(_token(searcher.user_uuid)) == ["rag_list_business_views", "rag_search"]
    assert _tool_names(_token(chatter.user_uuid)) == [
        "rag_list_business_views",
        "rag_chat_send_message",
        "rag_chat_get_conversation",
    ]
    assert _tool_names(_token(viewer.user_uuid)) == []
    assert _tool_names(_token(admin.user_uuid)) == ALL_TOOLS

    denied = _call("rag_search", {"query": "規程"}, _token(chatter.user_uuid))
    assert denied["isError"] is True
    assert denied["structuredContent"]["error_code"] == "MCP_TOOL_FORBIDDEN"


def test_inactive_user_token_is_forbidden(auth: ProductionAuth) -> None:
    user = auth.create_user("pending", force_password_change=True)
    assert _rpc("tools/list", headers=_token(user.user_uuid)).status_code == 403


def test_local_mode_uses_local_user_without_token() -> None:
    """local（既定）は従来どおり全権限のローカル利用者（token は見ない）。"""
    assert _tool_names({}) == ALL_TOOLS


# ---------------------------------------------------------------------------
# rag_list_business_views
# ---------------------------------------------------------------------------


def test_list_business_views_is_scoped_to_user(
    auth: ProductionAuth, monkeypatch: MonkeyPatch
) -> None:
    fake = ScopedFakeOracle()
    monkeypatch.setattr(business_views_route, "OracleClient", lambda *_a, **_k: fake)
    user = auth.user_with_permissions(
        "searcher", ["menu.search"], business_view_ids=["bv-1", "bv-3"]
    )
    result = _call("rag_list_business_views", {"limit": 10}, _token(user.user_uuid))
    assert result["isError"] is False
    views = result["structuredContent"]["business_views"]
    assert [view["id"] for view in views] == ["bv-1", "bv-3"]
    assert views[0] == {
        "id": "bv-1",
        "name": "業務ビュー bv-1",
        "description": None,
        "status": "ACTIVE",
        "knowledge_base_count": 0,
    }

    invalid = _call("rag_list_business_views", {"limit": 0}, _token(user.user_uuid))
    assert invalid["structuredContent"]["error_code"] == "MCP_TOOL_ARGUMENTS_INVALID"


# ---------------------------------------------------------------------------
# rag_search
# ---------------------------------------------------------------------------


def test_search_uses_business_view_and_user_scope(
    auth: ProductionAuth, monkeypatch: MonkeyPatch
) -> None:
    _install_search(monkeypatch)
    user = auth.user_with_permissions(
        "searcher",
        ["menu.search"],
        business_view_ids=["bv-1", "bv-3"],
        knowledge_base_ids=["kb-1"],
    )
    headers = _token(user.user_uuid)

    ok = _call("rag_search", {"query": "規程", "business_view_id": "bv-1"}, headers)
    assert ok["isError"] is False, ok
    assert ok["structuredContent"]["answer"] == "ok"
    assert ok["structuredContent"]["citations"] == []
    assert _captured_knowledge_base_ids() == ["kb-1"]

    # 範囲外の業務ビューは存在しないものとして 404。
    RecordingPipeline.captured_request = None
    missing = _call("rag_search", {"query": "規程", "business_view_id": "bv-2"}, headers)
    assert missing["isError"] is True
    assert missing["structuredContent"]["status"] == 404
    # 業務ビューの KB が 1 つも許可されていなければ 403（RAG_SCOPE_FORBIDDEN）。
    no_kb = _call("rag_search", {"query": "規程", "business_view_id": "bv-3"}, headers)
    assert no_kb["structuredContent"]["error_code"] == SCOPE_FORBIDDEN_CODE
    assert no_kb["structuredContent"]["status"] == 403
    # 範囲外の KB の明示も 403。
    explicit = _call("rag_search", {"query": "規程", "knowledge_base_ids": ["kb-3"]}, headers)
    assert explicit["structuredContent"]["error_code"] == SCOPE_FORBIDDEN_CODE
    assert explicit["structuredContent"]["message"] == (
        search_route.REQUEST_KNOWLEDGE_BASES_FORBIDDEN_MESSAGE
    )
    assert RecordingPipeline.captured_request is None

    invalid = _call("rag_search", {"query": "規程", "filters": {"unknown": "x"}}, headers)
    assert invalid["structuredContent"]["error_code"] == "MCP_TOOL_ARGUMENTS_INVALID"


def test_search_maps_citations_and_uses_token_user_context(
    auth: ProductionAuth, monkeypatch: MonkeyPatch
) -> None:
    captured: dict[str, Any] = {}

    async def fake_run(request: SearchRequest) -> SearchResponse:
        captured["request"] = request
        captured["context"] = current_audit_request_context()
        return SearchResponse(
            answer="回答",
            citations=[
                RetrievedChunk(
                    document_id="d1",
                    chunk_id="c1",
                    text="長" * 1500,
                    score=0.5,
                    file_name="規程.pdf",
                )
            ],
            trace_id="trace-1",
            guardrail_warnings=["注意"],
            elapsed_ms=1.0,
        )

    monkeypatch.setattr(search_route, "_run_search_with_timeout", fake_run)
    user = auth.user_with_permissions("searcher", ["menu.search"])
    result = _call(
        "rag_search",
        {"query": " 規程 ", "top_k": 3},
        {**_token(user.user_uuid), "X-RAG-Agent-ID": "spoofed"},
    )
    body = result["structuredContent"]
    assert body["answer"] == "回答"
    assert body["trace_id"] == "trace-1"
    assert body["guardrail_warnings"] == ["注意"]
    citation = body["citations"][0]
    assert citation["document_id"] == "d1"
    assert citation["file_name"] == "規程.pdf"
    assert citation["score"] == 0.5
    assert len(citation["text"]) == 1000
    request: SearchRequest = captured["request"]
    assert (request.query, request.top_k, request.rerank_top_n) == ("規程", 3, 3)
    # 利用者・agent・thread は token から決める（header の agent は使わない）。
    context: AuditRequestContext = captured["context"]
    settings = get_settings()
    assert context.user_id_hash == request_context._header_hash(user.user_uuid, settings)
    assert context.agent_id_hash == request_context._header_hash("agent-1", settings)
    assert context.thread_id_hash == request_context._header_hash("run-1", settings)
    assert context.tenant_id_hash is None


# ---------------------------------------------------------------------------
# チャット
# ---------------------------------------------------------------------------


class OwnedChatOracle(FakeChatOracle):
    """会話の持ち主（監査 context の user_id_hash）で絞る fake（Oracle の SQL と同じ）。"""

    def __init__(self) -> None:
        super().__init__()
        self.owners: dict[str, str | None] = {}

    async def create_conversation(
        self, *, business_view_id: str, title: str | None = None
    ) -> StoredConversation:
        conversation = await super().create_conversation(
            business_view_id=business_view_id, title=title
        )
        self.owners[conversation.id] = current_audit_request_context().user_id_hash
        return conversation

    async def get_conversation(self, conversation_id: str) -> StoredConversation | None:
        owner = self.owners.get(conversation_id)
        if owner != current_audit_request_context().user_id_hash:
            return None
        return await super().get_conversation(conversation_id)


@pytest.fixture
def chat_oracle(monkeypatch: MonkeyPatch) -> OwnedChatOracle:
    fake = OwnedChatOracle()
    _stub_stream(monkeypatch, fake, ["m1"])
    return fake


def test_chat_create_send_and_get_as_token_user(
    auth: ProductionAuth, chat_oracle: OwnedChatOracle
) -> None:
    chatter = auth.user_with_permissions("chatter", ["menu.chat"])
    other = auth.user_with_permissions("other", ["menu.chat"])
    headers = _token(chatter.user_uuid)

    first = _call(
        "rag_chat_send_message",
        {"content": "経費の上限は?", "business_view_id": "bv-1", "title": "経費"},
        headers,
    )
    assert first["isError"] is False, first
    body = first["structuredContent"]
    conversation_id = body["conversation_id"]
    assert body["answer"] == "回答"
    assert body["citations"] == [
        {"document_id": "d1", "chunk_id": "ch1", "file_name": None, "text": "根拠", "score": 0.9}
    ]
    stored = chat_oracle.messages[conversation_id]
    assert [message.role for message in stored] == ["USER", "ASSISTANT"]
    assert body["message_id"] == stored[1].id
    assert stored[1].model == "m1"
    # 会話の持ち主は token の利用者。
    owner = request_context._header_hash(chatter.user_uuid, get_settings())
    assert chat_oracle.owners[conversation_id] == owner
    assert chat_oracle.conversations[conversation_id].title == "経費"

    second = _call(
        "rag_chat_send_message",
        {"content": "交通費は?", "conversation_id": conversation_id},
        headers,
    )
    assert second["structuredContent"]["conversation_id"] == conversation_id

    detail = _call(
        "rag_chat_get_conversation",
        {"conversation_id": conversation_id, "message_limit": 3},
        headers,
    )["structuredContent"]
    assert detail["conversation_id"] == conversation_id
    assert detail["business_view_id"] == "bv-1"
    assert detail["status"] == "ACTIVE"
    assert [(item["role"], item["content"]) for item in detail["messages"]] == [
        ("ASSISTANT", "回答"),
        ("USER", "交通費は?"),
        ("ASSISTANT", "回答"),
    ]

    # 他の利用者の会話は存在しないものとして 404。
    hidden = _call(
        "rag_chat_get_conversation", {"conversation_id": conversation_id}, _token(other.user_uuid)
    )
    assert hidden["isError"] is True
    assert hidden["structuredContent"]["status"] == 404
    denied_send = _call(
        "rag_chat_send_message",
        {"content": "続き", "conversation_id": conversation_id},
        _token(other.user_uuid),
    )
    assert denied_send["structuredContent"]["status"] == 404


def test_chat_send_validates_target_and_business_view(
    auth: ProductionAuth, chat_oracle: OwnedChatOracle
) -> None:
    headers = _token(auth.user_with_permissions("chatter", ["menu.chat"]).user_uuid)
    missing_target = _call("rag_chat_send_message", {"content": "質問"}, headers)
    assert missing_target["structuredContent"]["error_code"] == "MCP_TOOL_ARGUMENTS_INVALID"
    unknown_view = _call(
        "rag_chat_send_message", {"content": "質問", "business_view_id": "bv-missing"}, headers
    )
    assert unknown_view["structuredContent"]["status"] == 404
    assert chat_oracle.conversations == {}


def test_chat_tools_return_404_when_chat_disabled(
    auth: ProductionAuth, chat_oracle: OwnedChatOracle, monkeypatch: MonkeyPatch
) -> None:
    monkeypatch.setattr(get_settings(), "rag_chat_enabled", False)
    headers = _token(auth.user_with_permissions("chatter", ["menu.chat"]).user_uuid)
    sent = _call("rag_chat_send_message", {"content": "質問", "business_view_id": "bv-1"}, headers)
    assert sent["isError"] is True
    assert sent["structuredContent"]["status"] == 404
    assert sent["structuredContent"]["message"] == chat_route.CHAT_DISABLED_MESSAGE
    got = _call("rag_chat_get_conversation", {"conversation_id": "conv-1"}, headers)
    assert got["structuredContent"]["status"] == 404
    assert chat_oracle.conversations == {}


def test_chat_send_timeout_returns_504_and_saves_error(
    auth: ProductionAuth, chat_oracle: OwnedChatOracle, monkeypatch: MonkeyPatch
) -> None:
    class TimeoutPipeline:
        def __init__(self, *_args: object, **_kwargs: object) -> None:
            pass

        async def run(self, *_args: object, **_kwargs: object) -> SearchResponse:
            raise TimeoutError

    monkeypatch.setattr(chat_route, "RagPipeline", TimeoutPipeline)
    headers = _token(auth.user_with_permissions("chatter", ["menu.chat"]).user_uuid)
    result = _call(
        "rag_chat_send_message", {"content": "質問", "business_view_id": "bv-1"}, headers
    )
    assert result["isError"] is True
    assert result["structuredContent"]["status"] == 504
    assert result["structuredContent"]["message"] == chat_route.CHAT_TIMEOUT_MESSAGE
    (conversation_id,) = chat_oracle.messages.keys()
    # 新しく作った会話の ID を返し、再試行で会話を増やさないようにする（#252）。
    assert result["structuredContent"]["details"] == {"conversation_id": conversation_id}
    (messages,) = chat_oracle.messages.values()
    assert [(message.role, message.status) for message in messages] == [
        ("USER", "COMPLETE"),
        ("ASSISTANT", "ERROR"),
    ]
