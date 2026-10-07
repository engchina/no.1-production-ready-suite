"""RAG の MCP サーバー（`POST /api/mcp`。#232）のテスト。

production mode（InMemory の共通認証）で、Agent が作るサービストークンの利用者として呼ぶ。
Oracle・LLM は既存の fake / スタブ（範囲で絞る f
ake の検索・回答プロファイル）に差し替え、実サービスは呼ばない。

RAG のチャットは MCP で提供しない（#787）。
"""

from __future__ import annotations

from typing import Any

import pytest
from pr_system_settings.auth.service_token import issue_service_token
from pytest import MonkeyPatch

from app.api.routes import search as search_route
from app.api.routes import search_answer_profiles as search_answer_profiles_route
from app.config import get_settings
from app.main import app
from app.mcp import tools as mcp_tools
from app.rag import request_context
from app.rag.request_context import AuditRequestContext, current_audit_request_context
from app.schemas.search import RetrievedChunk, SearchDiagnostics, SearchRequest, SearchResponse
from app.security.permissions import SCOPE_FORBIDDEN_CODE, permission_for_route
from tests.security_support import ProductionAuth, enable_production_auth, login
from tests.support import AsgiTestClient
from tests.test_search_search_answer_profile import RecordingPipeline
from tests.test_security_api import ScopedFakeOracle
from tests.test_security_scope import _captured_knowledge_base_ids, _install_search

client = AsgiTestClient(app)
SECRET = "rag-mcp-test-secret-0123456789abcdef"  # nosec B105 - テスト用
ALL_TOOLS = ["rag_list_search_answer_profiles", "rag_search", "rag_read_source"]


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

    assert _tool_names(_token(searcher.user_uuid)) == [
        "rag_list_search_answer_profiles",
        "rag_search",
        "rag_read_source",
    ]
    # チャットは MCP で提供しない（#787）。チャット
    # だけの利用者は検索・回答プロファイルの一覧だけを使える。
    #
    assert _tool_names(_token(chatter.user_uuid)) == ["rag_list_search_answer_profiles"]
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
# rag_list_search_answer_profiles
# ---------------------------------------------------------------------------


def test_list_search_answer_profiles_is_scoped_to_user(
    auth: ProductionAuth, monkeypatch: MonkeyPatch
) -> None:
    fake = ScopedFakeOracle()
    monkeypatch.setattr(search_answer_profiles_route, "OracleClient", lambda *_a, **_k: fake)
    user = auth.user_with_permissions(
        "searcher", ["menu.search"], search_answer_profile_ids=["bv-1", "bv-3"]
    )
    result = _call("rag_list_search_answer_profiles", {"limit": 10}, _token(user.user_uuid))
    assert result["isError"] is False
    views = result["structuredContent"]["search_answer_profiles"]
    assert [view["id"] for view in views] == ["bv-1", "bv-3"]
    assert views[0] == {
        "id": "bv-1",
        "name": "検索・回答プロファイル bv-1",
        "description": None,
        "status": "ACTIVE",
        "knowledge_base_count": 0,
    }

    invalid = _call("rag_list_search_answer_profiles", {"limit": 0}, _token(user.user_uuid))
    assert invalid["structuredContent"]["error_code"] == "MCP_TOOL_ARGUMENTS_INVALID"


# ---------------------------------------------------------------------------
# rag_search
# ---------------------------------------------------------------------------


def test_search_uses_search_answer_profile_and_user_scope(
    auth: ProductionAuth, monkeypatch: MonkeyPatch
) -> None:
    _install_search(monkeypatch)
    user = auth.user_with_permissions(
        "searcher",
        ["menu.search"],
        search_answer_profile_ids=["bv-1", "bv-3"],
        knowledge_base_ids=["kb-1"],
    )
    headers = _token(user.user_uuid)

    ok = _call("rag_search", {"query": "規程", "search_answer_profile_id": "bv-1"}, headers)
    assert ok["isError"] is False, ok
    assert ok["structuredContent"]["answer"] == "ok"
    assert ok["structuredContent"]["evidence"] == []
    assert _captured_knowledge_base_ids() == ["kb-1"]

    # 範囲外の検索・回答プロファイルは存在しないものとして 404。
    RecordingPipeline.captured_request = None
    missing = _call("rag_search", {"query": "規程", "search_answer_profile_id": "bv-2"}, headers)
    assert missing["isError"] is True
    assert missing["structuredContent"]["status"] == 404
    # 検索・回答プロファイルの KB が 1 つも許可されていなければ 403（RAG_SCOPE_FORBIDDEN）。
    no_kb = _call("rag_search", {"query": "規程", "search_answer_profile_id": "bv-3"}, headers)
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


def _chunk(chunk_id: str, *, text: str = "本文", used: bool | None = None, **metadata: Any) -> Any:
    if used is not None:
        metadata["evidence_model_used"] = used
    return RetrievedChunk(
        document_id="d1",
        chunk_id=chunk_id,
        text=text,
        score=0.5,
        file_name="規程.pdf",
        metadata=metadata,
    )


def test_search_maps_evidence_and_uses_token_user_context(
    auth: ProductionAuth, monkeypatch: MonkeyPatch
) -> None:
    """根拠は場所・版・回答に使ったか付きの evidence で返す（#1219）。"""
    captured: dict[str, Any] = {}

    async def fake_run(request: SearchRequest) -> SearchResponse:
        captured["request"] = request
        captured["context"] = current_audit_request_context()
        return SearchResponse(
            answer="回答",
            citations=[
                _chunk("c-unused", used=False, evidence_role="retrieved_anchor"),
                _chunk(
                    "c1",
                    text="長" * 1500,
                    used=True,
                    evidence_role="retrieved_anchor",
                    section_path="規程 > 第2条（精算の期限）",
                    page_start=3,
                    page_end=4,
                    chunk_set_id="cs-1",
                    recipe_id="r-1",
                    content_kind="text",
                    rerank_score=0.9,
                ),
            ],
            trace_id="trace-1",
            guardrail_warnings=["注意"],
            elapsed_ms=1.0,
            diagnostics=SearchDiagnostics(
                answer={
                    "insufficient_reason": " 期限の例外が資料に無い ",
                    "needs_human_review": True,
                }
            ),
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
    assert body["insufficient_reason"] == "期限の例外が資料に無い"
    assert body["needs_human_review"] is True
    assert body["evidence_omitted"] == 0
    # 回答に使った根拠を先にする。
    assert [item["chunk_id"] for item in body["evidence"]] == ["c1", "c-unused"]
    evidence = body["evidence"][0]
    assert evidence["evidence_id"] == "c1"
    assert evidence["document_id"] == "d1"
    assert evidence["file_name"] == "規程.pdf"
    assert (evidence["chunk_set_id"], evidence["recipe_id"]) == ("cs-1", "r-1")
    assert evidence["content_kind"] == "text"
    assert evidence["locator"] == {
        "section_path": ["規程", "第2条（精算の期限）"],
        "page_start": 3,
        "page_end": 4,
    }
    assert len(evidence["excerpt"]) == 1000
    assert evidence["truncated"] is True
    assert evidence["text_length"] == 1500
    assert evidence["used_in_answer"] is True
    assert evidence["role"] == "retrieved_anchor"
    assert (evidence["score"], evidence["rerank_score"]) == (0.5, 0.9)
    unused = body["evidence"][1]
    assert unused["used_in_answer"] is False
    assert unused["truncated"] is False
    assert unused["locator"] == {"section_path": [], "page_start": None, "page_end": None}
    request: SearchRequest = captured["request"]
    assert (request.query, request.top_k) == ("規程", 3)
    # 利用者・agent・thread は token から決める（header の agent は使わない）。
    context: AuditRequestContext = captured["context"]
    settings = get_settings()
    assert context.user_id_hash == request_context._header_hash(user.user_uuid, settings)
    assert context.agent_id_hash == request_context._header_hash("agent-1", settings)
    assert context.thread_id_hash == request_context._header_hash("run-1", settings)
    assert context.tenant_id_hash is None


def test_search_limits_evidence(auth: ProductionAuth, monkeypatch: MonkeyPatch) -> None:
    async def fake_run(request: SearchRequest) -> SearchResponse:
        del request
        return SearchResponse(
            answer="回答",
            citations=[_chunk(f"c{index}", used=index % 3 == 0) for index in range(6)],
            trace_id="trace-1",
            elapsed_ms=1.0,
        )

    monkeypatch.setattr(search_route, "_run_search_with_timeout", fake_run)
    user = auth.user_with_permissions("searcher", ["menu.search"])
    body = _call("rag_search", {"query": "規程", "evidence_limit": 3}, _token(user.user_uuid))[
        "structuredContent"
    ]
    # 回答に使った c0・c3 を先に（検索の順のまま）、残りの枠に使っていない根拠を入れる。
    assert [item["chunk_id"] for item in body["evidence"]] == ["c0", "c3", "c1"]
    assert body["evidence_omitted"] == 3
    assert (body["insufficient_reason"], body["needs_human_review"]) == (None, False)


# ---------------------------------------------------------------------------
# 根拠の読み取り（rag_read_source。#1219）
# ---------------------------------------------------------------------------


class _SourceOracle:
    """検索で見える chunk（visible）と、見えるが古い版の chunk（stale）だけを持つ fake。"""

    visible: dict[tuple[str, str], RetrievedChunk] = {}
    stale: set[tuple[str, str]] = set()
    contexts: list[AuditRequestContext] = []

    async def retrievable_chunk(self, document_id: str, chunk_id: str) -> RetrievedChunk | None:
        self.contexts.append(current_audit_request_context())
        return self.visible.get((document_id, chunk_id))

    async def accessible_chunk_exists(self, document_id: str, chunk_id: str) -> bool:
        return (document_id, chunk_id) in self.stale


@pytest.fixture
def source_oracle(monkeypatch: MonkeyPatch) -> type[_SourceOracle]:
    _SourceOracle.visible = {
        ("d1", "c1"): _chunk(
            "c1",
            text="あいうえおかきくけこ",
            section_path="規程 > 第2条",
            page_start=2,
            chunk_set_id="cs-1",
            parent_text="親の本文" * 3,
        )
    }
    _SourceOracle.stale = {("d1", "c-old")}
    _SourceOracle.contexts = []
    monkeypatch.setattr(mcp_tools, "OracleClient", _SourceOracle)
    return _SourceOracle


def test_read_source_returns_full_text_in_windows(
    auth: ProductionAuth, source_oracle: type[_SourceOracle]
) -> None:
    user = auth.user_with_permissions("searcher", ["menu.search"], knowledge_base_ids=["kb-1"])
    headers = _token(user.user_uuid)

    first = _call(
        "rag_read_source", {"document_id": "d1", "chunk_id": "c1", "max_chars": 4}, headers
    )
    assert first["isError"] is False, first
    body = first["structuredContent"]
    assert (body["text"], body["offset"], body["text_length"]) == ("あいうえ", 0, 10)
    assert (body["truncated"], body["next_offset"]) == (True, 4)
    assert body["locator"] == {"section_path": ["規程", "第2条"], "page_start": 2, "page_end": 2}
    assert body["chunk_set_id"] == "cs-1"
    assert (body["parent_text"], body["parent_truncated"]) == ("親の本文", True)

    rest = _call(
        "rag_read_source",
        {"document_id": "d1", "chunk_id": "c1", "offset": 4, "max_chars": 100},
        headers,
    )["structuredContent"]
    assert (rest["text"], rest["truncated"], rest["next_offset"]) == ("おかきくけこ", False, None)
    # 読み取りは token の利用者の範囲（利用できるナレッジベース）で行う。
    assert source_oracle.contexts[-1].allowed_knowledge_base_ids == frozenset({"kb-1"})


def test_read_source_distinguishes_missing_and_stale(
    auth: ProductionAuth, source_oracle: type[_SourceOracle]
) -> None:
    del source_oracle
    user = auth.user_with_permissions("searcher", ["menu.search"])
    headers = _token(user.user_uuid)

    missing = _call("rag_read_source", {"document_id": "d1", "chunk_id": "nope"}, headers)
    assert missing["isError"] is True
    assert missing["structuredContent"]["error_code"] == mcp_tools.SOURCE_NOT_FOUND_CODE
    stale = _call("rag_read_source", {"document_id": "d1", "chunk_id": "c-old"}, headers)
    assert stale["structuredContent"]["error_code"] == mcp_tools.SOURCE_STALE_CODE
    # 検索の権限が無い利用者は呼べない。
    viewer = auth.user_with_permissions("viewer", ["menu.upload"])
    denied = _call(
        "rag_read_source", {"document_id": "d1", "chunk_id": "c1"}, _token(viewer.user_uuid)
    )
    assert denied["structuredContent"]["error_code"] == "MCP_TOOL_FORBIDDEN"
    invalid = _call(
        "rag_read_source", {"document_id": "d1", "chunk_id": "c1", "max_chars": 0}, headers
    )
    assert invalid["structuredContent"]["error_code"] == "MCP_TOOL_ARGUMENTS_INVALID"


# ---------------------------------------------------------------------------
# チャット（MCP では提供しない。#787）
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("name", ["rag_chat_send_message", "rag_chat_get_conversation"])
def test_chat_tools_are_not_provided(auth: ProductionAuth, name: str) -> None:
    admin = auth.create_user("admin", system_admin=True)
    response = _rpc(
        "tools/call",
        {"name": name, "arguments": {"content": "質問", "search_answer_profile_id": "bv-1"}},
        headers=_token(admin.user_uuid),
    )
    assert response.status_code == 200, response.text
    error = response.json()["error"]
    assert error["code"] == -32602
    assert name in error["message"]
