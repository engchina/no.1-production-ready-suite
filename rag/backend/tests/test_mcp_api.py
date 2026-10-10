"""RAG の MCP サーバー（`POST /api/mcp`。#232）のテスト。

production mode（InMemory の共通認証）で、Agent が作るサービストークンの利用者として呼ぶ。
Oracle・LLM は既存の fake / スタブ（範囲で絞る f
ake の検索・回答プロファイル）に差し替え、実サービスは呼ばない。

RAG のチャットは MCP で提供しない（#787）。
"""

from __future__ import annotations

import base64
import hashlib
import json
from types import SimpleNamespace
from typing import Any

import pytest
from pr_backend_core.api import encode_cursor
from pr_system_settings.auth.service_token import issue_service_token
from pytest import MonkeyPatch

from app.api.routes import search as search_route
from app.api.routes import search_answer_profiles as search_answer_profiles_route
from app.config import get_settings
from app.main import app
from app.mcp import tools as mcp_tools
from app.rag import document_crop, request_context
from app.rag.request_context import AuditRequestContext, current_audit_request_context
from app.rag.search_answer_profile_config import SearchAnswerProfileConfig
from app.schemas.search import RetrievedChunk, SearchDiagnostics, SearchRequest, SearchResponse
from app.security.permissions import SCOPE_FORBIDDEN_CODE, permission_for_route
from tests.security_support import ProductionAuth, enable_production_auth, login
from tests.support import AsgiTestClient
from tests.test_search_search_answer_profile import RecordingPipeline
from tests.test_security_api import ScopedFakeOracle
from tests.test_security_scope import _captured_knowledge_base_ids, _install_search

client = AsgiTestClient(app)
SECRET = "rag-mcp-test-secret-0123456789abcdef"  # nosec B105 - テスト用
ALL_TOOLS = [
    "rag_list_search_answer_profiles",
    "rag_search",
    "rag_lookup_guides",
    "rag_retrieve_evidence",
    "rag_validate_answer",
    "rag_read_source",
    "rag_outline",
    "rag_read_document",
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

    assert _tool_names(_token(searcher.user_uuid)) == [
        "rag_list_search_answer_profiles",
        "rag_search",
        "rag_lookup_guides",
        "rag_retrieve_evidence",
        "rag_validate_answer",
        "rag_read_source",
        "rag_outline",
        "rag_read_document",
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


@pytest.mark.parametrize("tool", ["rag_search", "rag_retrieve_evidence"])
def test_include_superseded_is_a_boolean_argument(
    auth: ProductionAuth, monkeypatch: MonkeyPatch, tool: str
) -> None:
    """旧版を含める指定は真偽値の引数 include_superseded で、入力の検証で落ちない（#1392）。"""
    _install_search(monkeypatch)
    user = auth.user_with_permissions(
        "searcher", ["menu.search"], search_answer_profile_ids=["bv-1"], knowledge_base_ids=["kb-1"]
    )
    headers = _token(user.user_uuid)

    def captured_filters() -> dict[str, str]:
        captured = RecordingPipeline.captured_request
        assert captured is not None
        return dict(captured.filters)

    included = _call(
        tool,
        {
            "query": "旧版との違い",
            "search_answer_profile_id": "bv-1",
            "include_superseded": True,
            "filters": {"category_name": "規程"},
        },
        headers,
    )
    assert included["isError"] is False, included
    # 旧版を含めても検索の範囲は検索・回答プロファイルと利用者の KB のまま。
    assert captured_filters() == {
        "category_name": "規程",
        "include_superseded": "true",
        "knowledge_base_id": "kb-1",
    }
    assert _captured_knowledge_base_ids() == ["kb-1"]

    # 既定（省略・false）は今の版だけ（filters に旧版の指定を足さない）。
    for arguments in ({}, {"include_superseded": False}):
        result = _call(
            tool, {"query": "規程", "search_answer_profile_id": "bv-1", **arguments}, headers
        )
        assert result["isError"] is False, result
        assert "include_superseded" not in captured_filters()

    # filters に書いた旧版の指定（真偽値・文字列）は、専用の引数を案内して拒否する。
    RecordingPipeline.captured_request = None
    for value in (True, "true"):
        invalid = _call(
            tool,
            {
                "query": "規程",
                "search_answer_profile_id": "bv-1",
                "filters": {"include_superseded": value},
            },
            headers,
        )
        assert invalid["isError"] is True
        assert invalid["structuredContent"]["error_code"] == "MCP_TOOL_ARGUMENTS_INVALID"
    assert "include_superseded" in json.dumps(invalid["structuredContent"], ensure_ascii=False)
    assert RecordingPipeline.captured_request is None


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
                    # 印刷の頁番号と領域（#1244）。
                    page_label_start="2-1",
                    page_label_end="2-2",
                    bbox="[10, 20, 110, 220]",
                    bbox_unit="absolute",
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
                    # 回答の構造（#1235）。
                    "envelope": {
                        "outcome": "conditional",
                        "requests": [
                            {"id": "Q1", "text": "期限", "status": "addressed"},
                            {"id": "Q2", "text": "例外", "status": "missing"},
                            {"id": "Q3", "text": "壊れた", "status": "other"},
                        ],
                        "conditions": ["承認済みの場合"],
                        "gaps": ["期限の例外"],
                        "confirmations": [],
                    },
                    # 回答を作った設定の版（#1276）。
                    "provenance": {
                        "search_answer_profile": {
                            "id": "sap-1",
                            "updated_at": "2026-10-01T00:00:00+00:00",
                            "config_sha256": "abc",
                        },
                        "prompt_version": "prompt-0123456789abcdef",
                    },
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
    assert body["schema_version"] == mcp_tools.MCP_OUTPUT_SCHEMA_VERSION
    assert body["answer"] == "回答"
    assert body["trace_id"] == "trace-1"
    assert body["provenance"] == {
        "search_answer_profile_id": "sap-1",
        "search_answer_profile_updated_at": "2026-10-01T00:00:00+00:00",
        "search_answer_profile_config_sha256": "abc",
        "prompt_version": "prompt-0123456789abcdef",
    }
    assert body["guardrail_warnings"] == ["注意"]
    assert body["insufficient_reason"] == "期限の例外が資料に無い"
    assert body["needs_human_review"] is True
    assert body["outcome"] == "conditional"
    assert [request["status"] for request in body["requests"]] == ["addressed", "missing"]
    assert (body["conditions"], body["gaps"], body["confirmations"]) == (
        ["承認済みの場合"],
        ["期限の例外"],
        [],
    )
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
        "sheet_name": None,
        "row_start": None,
        "row_end": None,
        "cell_range": None,
        "page_label_start": "2-1",
        "page_label_end": "2-2",
        "bbox": [10.0, 20.0, 110.0, 220.0],
        "bbox_unit": "absolute",
        # 要素の ID の無い chunk は定位子を持たない（#1330）。
        "element_locator": None,
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
    assert unused["locator"]["section_path"] == []
    assert unused["locator"]["page_start"] is None
    request: SearchRequest = captured["request"]
    assert (request.query, request.top_k) == ("規程", 3)
    # 利用者・agent・thread は token から決める（header の agent は使わない）。
    context: AuditRequestContext = captured["context"]
    settings = get_settings()
    assert context.user_id_hash == request_context._header_hash(user.user_uuid, settings)
    assert context.agent_id_hash == request_context._header_hash("agent-1", settings)
    assert context.thread_id_hash == request_context._header_hash("run-1", settings)
    assert context.tenant_id_hash is None


def test_search_evidence_locates_spreadsheet_rows(
    auth: ProductionAuth, monkeypatch: MonkeyPatch
) -> None:
    """Excel の行の記録（#1221）の根拠は、シート・行の範囲・セル範囲で場所を返す。"""

    async def fake_run(request: SearchRequest) -> SearchResponse:
        del request
        return SearchResponse(
            answer="回答",
            citations=[
                _chunk(
                    "c-sheet",
                    used=True,
                    section_path="コード表",
                    sheet_name="コード表",
                    row_start=3,
                    row_end=6,
                    cell_range="A3:C6",
                )
            ],
            trace_id="trace-1",
            elapsed_ms=1.0,
        )

    monkeypatch.setattr(search_route, "_run_search_with_timeout", fake_run)
    user = auth.user_with_permissions("searcher", ["menu.search"])
    body = _call("rag_search", {"query": "費目"}, _token(user.user_uuid))["structuredContent"]
    assert body["evidence"][0]["locator"] == {
        "section_path": ["コード表"],
        "page_start": None,
        "page_end": None,
        "sheet_name": "コード表",
        "row_start": 3,
        "row_end": 6,
        "cell_range": "A3:C6",
        "page_label_start": None,
        "page_label_end": None,
        "bbox": None,
        "bbox_unit": None,
        "element_locator": None,
    }


def test_retrieve_evidence_skips_answer_generation(
    auth: ProductionAuth, monkeypatch: MonkeyPatch
) -> None:
    """rag_retrieve_evidence は回答を作らずに根拠だけを検索の順で返す（#1241）。"""
    captured: dict[str, Any] = {}

    async def fake_run(request: SearchRequest) -> SearchResponse:
        captured["generate_answer"] = request.generate_answer
        captured["profile"] = request.search_answer_profile_id
        return SearchResponse(
            answer="",
            citations=[_chunk(f"c{index}", used=False) for index in range(4)],
            trace_id="trace-r",
            guardrail_warnings=["注意"],
            elapsed_ms=1.0,
        )

    monkeypatch.setattr(search_route, "_run_search_with_timeout", fake_run)
    user = auth.user_with_permissions("searcher", ["menu.search"])
    body = _call(
        "rag_retrieve_evidence",
        {"query": "規程", "search_answer_profile_id": "bv-1", "evidence_limit": 3},
        _token(user.user_uuid),
    )["structuredContent"]
    assert captured == {"generate_answer": False, "profile": "bv-1"}
    assert [item["chunk_id"] for item in body["evidence"]] == ["c0", "c1", "c2"]
    assert body["evidence_omitted"] == 1
    assert (body["trace_id"], body["guardrail_warnings"]) == ("trace-r", ["注意"])
    assert "answer" not in body
    # 検索の権限が無ければ使えない。
    chatter = auth.user_with_permissions("chatter", ["menu.chat"])
    denied = _call("rag_retrieve_evidence", {"query": "規程"}, _token(chatter.user_uuid))
    assert denied["isError"] is True


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
    # 構造の無い回答は、引用があり人の確認が要らなければ answered。
    assert (body["outcome"], body["requests"], body["gaps"]) == ("answered", [], [])


# 承認規程の文書のかたまり（文書の順）と、別の文書のかたまり（#1348 の sh-approval-record-retention
# 型）。答えの「第 5 章 承認の記録」は関連度 1 位で当たったが、かたまりの 6 番目にある。
_APPROVAL_CITATIONS: list[tuple[str, str, int | None]] = [
    ("approval-preface", "parent_context", None),
    ("approval-ch1", "retrieved_anchor", 3),
    ("approval-ch2", "neighbor_context", None),
    ("approval-ch3", "same_page_context", None),
    ("approval-ch4", "neighbor_context", None),
    ("approval-ch5-records", "retrieved_anchor", 1),
    ("other-anchor", "retrieved_anchor", 2),
    ("other-next", "neighbor_context", None),
]


def _approval_citations(used: frozenset[str] = frozenset()) -> list[Any]:
    citations = []
    for chunk_id, role, rank in _APPROVAL_CITATIONS:
        metadata: dict[str, Any] = {"evidence_role": role}
        if rank is not None:
            metadata["evidence_retrieval_rank"] = rank
        citations.append(_chunk(chunk_id, used=chunk_id in used, **metadata))
    return citations


def test_retrieve_evidence_returns_retrieved_anchors_before_context(
    auth: ProductionAuth, monkeypatch: MonkeyPatch
) -> None:
    """当たった chunk を関連度の順に先に、前後の文脈を後ろにしてから切る（#1348）。"""

    async def fake_run(request: SearchRequest) -> SearchResponse:
        del request
        return SearchResponse(
            answer="", citations=_approval_citations(), trace_id="trace-a", elapsed_ms=1.0
        )

    monkeypatch.setattr(search_route, "_run_search_with_timeout", fake_run)
    user = auth.user_with_permissions("searcher", ["menu.search"])
    body = _call(
        "rag_retrieve_evidence",
        {"query": "承認の記録の保存", "evidence_limit": 5},
        _token(user.user_uuid),
    )["structuredContent"]
    # かたまりの 6 番目の「第 5 章」も上限の中に入る。文脈はその後ろに citations の順で入る。
    assert [item["chunk_id"] for item in body["evidence"]] == [
        "approval-ch5-records",
        "other-anchor",
        "approval-ch1",
        "approval-preface",
        "approval-ch2",
    ]
    assert [item["role"] for item in body["evidence"][:3]] == ["retrieved_anchor"] * 3
    assert body["evidence_omitted"] == 3


def _ledger_citations() -> list[Any]:
    """#1335 の再評価の D の取りこぼしの形（#1365）。

    「勤怠管理システム 変更 受付 承認 営業日」の検索で、運用要領の章が関連度 1〜12 位に当たり、
    答えの台帳の行（system-ledger.xlsx の 1 行 = 1 chunk）は 13〜20 位。文書のかたまりの順の
    citations では、運用要領の前後の文脈が当たった chunk の間に入る。
    """
    citations = []
    for index in range(12):
        citations.append(
            _chunk(
                f"guide-{index + 1}",
                evidence_role="retrieved_anchor",
                evidence_retrieval_rank=index + 1,
            )
        )
        citations.append(_chunk(f"guide-context-{index + 1}", evidence_role="neighbor_context"))
    for index in range(8):
        citations.append(
            _chunk(
                f"ledger-row-{index + 1}",
                text=f"システムID: SYS-10{index} / 正式名: 勤怠管理システム{index} / 担当部署: 人",
                evidence_role="retrieved_anchor",
                evidence_retrieval_rank=13 + index,
                content_kind="record",
            )
        )
    return citations


def test_retrieve_evidence_returns_all_retrieved_anchors_by_default(
    auth: ProductionAuth, monkeypatch: MonkeyPatch
) -> None:
    """既定の evidence_limit（20）で、関連度 13〜20 位の台帳の行も返る（#1365）。"""
    captured: list[SearchRequest] = []

    async def fake_run(request: SearchRequest) -> SearchResponse:
        captured.append(request)
        return SearchResponse(
            answer="", citations=_ledger_citations(), trace_id="trace-l", elapsed_ms=1.0
        )

    monkeypatch.setattr(search_route, "_run_search_with_timeout", fake_run)
    user = auth.user_with_permissions("searcher", ["menu.search"])
    body = _call(
        "rag_retrieve_evidence",
        {"query": "勤怠管理システム 変更 受付 承認 営業日"},
        _token(user.user_uuid),
    )["structuredContent"]
    chunk_ids = [item["chunk_id"] for item in body["evidence"]]
    # 当たった chunk を全部（運用要領の 12 件と台帳の行 8 件）、関連度の順に返す。
    assert chunk_ids == [f"guide-{n}" for n in range(1, 13)] + [
        f"ledger-row-{n}" for n in range(1, 9)
    ]
    assert [item["content_kind"] for item in body["evidence"][12:]] == ["record"] * 8
    # 切ったのは前後の文脈だけ。
    assert body["evidence_omitted"] == 12
    # 既定は検索する件数（top_k の既定）と同じ。
    assert SearchRequest.model_fields["top_k"].default == mcp_tools.RETRIEVE_EVIDENCE_LIMIT_DEFAULT
    assert captured[0].top_k == mcp_tools.RETRIEVE_EVIDENCE_LIMIT_DEFAULT


@pytest.mark.parametrize(
    ("arguments", "expected"),
    [
        ({"top_k": 30}, 30),
        ({"top_k": 10}, 20),
        ({"top_k": 100}, mcp_tools.EVIDENCE_LIMIT_MAX),
        ({"top_k": 30, "evidence_limit": 12}, 12),
        ({"evidence_limit": 5}, 5),
    ],
    ids=["top-k-larger", "top-k-smaller", "top-k-over-max", "explicit-limit", "explicit-small"],
)
def test_retrieve_evidence_limit_follows_top_k_unless_given(
    arguments: dict[str, Any], expected: int
) -> None:
    """evidence_limit を省略して top_k を広げたら、当たった chunk を top_k まで返す（#1365）。"""
    parsed = mcp_tools.RetrieveEvidenceInput.model_validate({"query": "台帳", **arguments})
    assert mcp_tools.retrieve_evidence_limit(parsed) == expected


def test_rag_search_keeps_its_evidence_limit_default() -> None:
    """回答を作る rag_search は回答に使った根拠が先に並ぶので、既定は 12 のまま（#1365）。"""
    assert mcp_tools.SearchInput.model_validate({"query": "台帳"}).evidence_limit == 12
    retrieve = mcp_tools.RetrieveEvidenceInput.model_validate({"query": "台帳"})
    assert retrieve.evidence_limit == 20


def test_search_orders_used_then_anchors_then_context(
    auth: ProductionAuth, monkeypatch: MonkeyPatch
) -> None:
    """rag_search は回答に使った根拠、当たった chunk（関連度の順）、前後の文脈の順（#1348）。"""

    async def fake_run(request: SearchRequest) -> SearchResponse:
        del request
        # 回答に使った根拠は citations の先頭（回答に使った順）に来る（answer_engine の並び）。
        citations = _approval_citations(used=frozenset({"approval-ch4", "approval-ch1"}))
        used = [chunk for chunk in citations if chunk.chunk_id == "approval-ch4"]
        used += [chunk for chunk in citations if chunk.chunk_id == "approval-ch1"]
        rest = [chunk for chunk in citations if chunk not in used]
        return SearchResponse(
            answer="回答", citations=used + rest, trace_id="trace-u", elapsed_ms=1.0
        )

    monkeypatch.setattr(search_route, "_run_search_with_timeout", fake_run)
    user = auth.user_with_permissions("searcher", ["menu.search"])
    body = _call(
        "rag_search", {"query": "承認の記録", "evidence_limit": 5}, _token(user.user_uuid)
    )["structuredContent"]
    assert [(item["chunk_id"], item["used_in_answer"]) for item in body["evidence"]] == [
        ("approval-ch4", True),
        ("approval-ch1", True),
        ("approval-ch5-records", False),
        ("other-anchor", False),
        ("approval-preface", False),
    ]
    assert body["evidence_omitted"] == 3


def test_mcp_evidence_order_keeps_unranked_hits_and_unknown_roles() -> None:
    """順位の無い当たり・役割の無い根拠は順位のある当たりの後ろで citations の順（#1348）。"""
    citations = [
        _chunk("context", evidence_role="neighbor_context"),
        _chunk("no-role"),
        _chunk("anchor-unranked", evidence_role="retrieved_anchor"),
        _chunk("anchor-2", evidence_role="retrieved_anchor", evidence_retrieval_rank=2),
        _chunk("anchor-1", evidence_role="retrieved_anchor", evidence_retrieval_rank="1"),
    ]
    assert [chunk.chunk_id for chunk in mcp_tools.mcp_evidence_order(citations)] == [
        "anchor-1",
        "anchor-2",
        "no-role",
        "anchor-unranked",
        "context",
    ]


_FIGURE_PAGE: dict[str, Any] = {
    "page_start": 2,
    "page_end": 2,
    "page_width": 600,
    "page_height": 800,
}


def test_evidence_types_figures_and_points_to_image_region(
    auth: ProductionAuth, monkeypatch: MonkeyPatch
) -> None:
    """根拠の種類を本文・表・図の VLM の説明・OCR に分け、図には元の画像の領域を付ける（#1282）。"""

    async def fake_run(request: SearchRequest) -> SearchResponse:
        del request
        return SearchResponse(
            answer="",
            citations=[
                _chunk(
                    "c-vision",
                    content_kind="figure",
                    figure_text_source="vision",
                    chunk_set_id="cs-1",
                    page_label_start="ii",
                    bbox="[60, 80, 300, 200]",
                    bbox_unit="absolute",
                    **_FIGURE_PAGE,
                ),
                _chunk(
                    "c-ocr",
                    content_kind="figure",
                    figure_text_source="ocr",
                    page_start=3,
                    bbox="[0.1, 0.2, 0.5, 0.6]",
                    bbox_unit="ratio",
                ),
                # 本文の出どころが分からない古い記録は VLM の説明として扱い、頁の大きさが無ければ
                # 画像の領域は出さない。
                _chunk("c-legacy", content_kind="figure", page_start=4, bbox="[1, 2, 30, 40]"),
                _chunk(
                    "c-rotated",
                    content_kind="figure",
                    figure_text_source="vision",
                    page_rotation=90,
                    bbox="[60, 80, 300, 200]",
                    bbox_unit="absolute",
                    **_FIGURE_PAGE,
                ),
                _chunk(
                    "c-outside",
                    content_kind="figure",
                    bbox="[60, 80, 900, 200]",
                    bbox_unit="absolute",
                    **_FIGURE_PAGE,
                ),
                # 親子階層（small-to-big）は image_evidence の図の bbox を使う。
                _chunk(
                    "c-s2b",
                    content_kind="figure",
                    figure_text_source="vision",
                    bbox="[10, 10, 590, 790]",
                    bbox_unit="absolute",
                    engine_metadata_json=json.dumps(
                        {"image_evidence": [{"page": 2, "bbox": [100, 200, 400, 500]}]}
                    ),
                    **_FIGURE_PAGE,
                ),
                _chunk("c-table", content_kind="table", bbox="[1, 2, 3, 4]", **_FIGURE_PAGE),
                _chunk("c-text", content_kind="text"),
                _chunk("c-none"),
            ],
            trace_id="trace-f",
            elapsed_ms=1.0,
        )

    monkeypatch.setattr(search_route, "_run_search_with_timeout", fake_run)
    user = auth.user_with_permissions("searcher", ["menu.search"])
    body = _call("rag_retrieve_evidence", {"query": "承認ボタン"}, _token(user.user_uuid))[
        "structuredContent"
    ]
    evidence = {item["chunk_id"]: item for item in body["evidence"]}
    assert {key: item["evidence_type"] for key, item in evidence.items()} == {
        "c-vision": "figure_description",
        "c-ocr": "ocr",
        "c-legacy": "figure_description",
        "c-rotated": "figure_description",
        "c-outside": "figure_description",
        "c-s2b": "figure_description",
        "c-table": "table",
        "c-text": "text",
        "c-none": "text",
    }
    assert evidence["c-vision"]["image_ref"] == {
        "document_id": "d1",
        "chunk_id": "c-vision",
        "chunk_set_id": "cs-1",
        "page": 2,
        "page_label": "ii",
        "bbox": [60.0, 80.0, 300.0, 200.0],
        "bbox_unit": "absolute",
    }
    assert evidence["c-ocr"]["image_ref"]["bbox_unit"] == "ratio"
    assert evidence["c-s2b"]["image_ref"]["bbox"] == [100.0, 200.0, 400.0, 500.0]
    for key in ("c-legacy", "c-rotated", "c-outside", "c-table", "c-text", "c-none"):
        assert evidence[key]["image_ref"] is None, key


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

    # 要素の定位子（#1330）: 解析の結果 er_1 の要素 el-2 は、作り直した chunk c1 にある。
    async def chunk_set_extraction_recipe_ids(self, chunk_set_ids: list[str]) -> dict[str, str]:
        return {chunk_set_id: "er_1" for chunk_set_id in chunk_set_ids if chunk_set_id == "cs-1"}

    async def retrievable_element_chunk(
        self, document_id: str, extraction_recipe_id: str, element_id: str
    ) -> RetrievedChunk | None:
        self.contexts.append(current_audit_request_context())
        if (document_id, extraction_recipe_id, element_id) == ("d1", "er_1", "el-2"):
            return self.visible[("d1", "c1")]
        return None

    async def accessible_document_exists(self, document_id: str) -> bool:
        return document_id == "d1"


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
            element_ids="el-1,el-2",
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
    assert body["locator"]["section_path"] == ["規程", "第2条"]
    assert (body["locator"]["page_start"], body["locator"]["page_end"]) == (2, 2)
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


def test_read_source_by_locator_returns_current_chunk_of_the_element(
    auth: ProductionAuth, source_oracle: type[_SourceOracle]
) -> None:
    """要素の定位子で読むと、同じ解析の結果の同じ要素を含む今の chunk を返す（#1330）。"""
    user = auth.user_with_permissions("searcher", ["menu.search"], knowledge_base_ids=["kb-1"])
    headers = _token(user.user_uuid)

    by_chunk = _call("rag_read_source", {"document_id": "d1", "chunk_id": "c1"}, headers)
    locator = by_chunk["structuredContent"]["locator"]["element_locator"]
    # 先頭の要素の定位子（chunk_set の解析の結果の ID と、chunk の開始の頁）。
    assert locator == "doc:d1/ext:er_1/page:2/el:el-1"

    result = _call(
        "rag_read_source",
        {"document_id": "d1", "locator": "doc:d1/ext:er_1/page:2/el:el-2", "max_chars": 4},
        headers,
    )
    assert result["isError"] is False, result
    body = result["structuredContent"]
    assert (body["chunk_id"], body["text"]) == ("c1", "あいうえ")
    assert body["locator"]["element_locator"] == "doc:d1/ext:er_1/page:2/el:el-1"
    assert source_oracle.contexts[-1].allowed_knowledge_base_ids == frozenset({"kb-1"})


def test_read_source_by_locator_distinguishes_stale_missing_and_invalid(
    auth: ProductionAuth, source_oracle: type[_SourceOracle]
) -> None:
    del source_oracle
    user = auth.user_with_permissions("searcher", ["menu.search"])
    headers = _token(user.user_uuid)

    stale = _call(
        "rag_read_source", {"document_id": "d1", "locator": "doc:d1/ext:er_old/el:el-2"}, headers
    )
    assert stale["structuredContent"]["error_code"] == mcp_tools.SOURCE_STALE_CODE
    # 見えない（無い・利用できる範囲の外の）文書の定位子。
    missing = _call(
        "rag_read_source", {"document_id": "d9", "locator": "doc:d9/ext:er_1/el:el-2"}, headers
    )
    assert missing["structuredContent"]["error_code"] == mcp_tools.SOURCE_NOT_FOUND_CODE
    for arguments in (
        {"document_id": "d1"},
        {"document_id": "d1", "chunk_id": "c1", "locator": "doc:d1/ext:er_1/el:el-2"},
        {"document_id": "d1", "locator": "doc:d2/ext:er_1/el:el-2"},
        {"document_id": "d1", "locator": "d1:c1"},
    ):
        invalid = _call("rag_read_source", arguments, headers)
        assert invalid["structuredContent"]["error_code"] == "MCP_TOOL_ARGUMENTS_INVALID", arguments


# ---------------------------------------------------------------------------
# 文書を順に読む（rag_outline / rag_read_document。#1332）
# ---------------------------------------------------------------------------


def _doc_chunk(index: int, text: str, section: str, page: int, **metadata: Any) -> RetrievedChunk:
    return RetrievedChunk(
        document_id="d1",
        chunk_id=f"d1:cs-1:{index}",
        text=text,
        score=0.0,
        file_name="規程.pdf",
        metadata={
            "document_id": "d1",
            "chunk_set_id": "cs-1",
            "chunk_index": index,
            "section_path": section,
            "page_start": page,
            "page_end": page,
            "element_ids": f"el-{index}",
            **metadata,
        },
    )


class _DocumentOracle:
    """1 つの文書（有効な chunk_set は cs-1）だけを持つ fake。"""

    chunks: list[RetrievedChunk] = []
    contexts: list[AuditRequestContext] = []

    async def document_reading_index(
        self, document_id: str, *, chunk_set_id: str | None = None, limit: int = 5000
    ) -> tuple[str | None, list[dict[str, object]]]:
        self.contexts.append(current_audit_request_context())
        if document_id != "d1" or chunk_set_id not in {None, "cs-1"}:
            return None, []
        rows: list[dict[str, object]] = [
            {
                "chunk_index": chunk.metadata["chunk_index"],
                "chunk_id": chunk.chunk_id,
                "section_path": chunk.metadata["section_path"],
                "page_start": chunk.metadata["page_start"],
                "page_end": chunk.metadata["page_end"],
                "chars": len(chunk.text),
            }
            for chunk in self.chunks
        ]
        return "cs-1", rows[:limit]

    async def readable_document_chunks(
        self, document_id: str, *, chunk_set_id: str, from_index: int, limit: int
    ) -> list[RetrievedChunk]:
        if document_id != "d1" or chunk_set_id != "cs-1":
            return []
        rows = [
            chunk for chunk in self.chunks if int(str(chunk.metadata["chunk_index"])) >= from_index
        ]
        return rows[:limit]

    async def accessible_document_exists(self, document_id: str) -> bool:
        return document_id == "d1"

    async def chunk_set_extraction_recipe_ids(self, chunk_set_ids: list[str]) -> dict[str, str]:
        return {"cs-1": "er_1"} if "cs-1" in chunk_set_ids else {}

    async def retrievable_element_chunk(
        self, document_id: str, extraction_recipe_id: str, element_id: str
    ) -> RetrievedChunk | None:
        for chunk in self.chunks:
            if (document_id, extraction_recipe_id, chunk.metadata["element_ids"]) == (
                "d1",
                "er_1",
                element_id,
            ):
                return chunk
        return None


@pytest.fixture
def document_oracle(monkeypatch: MonkeyPatch) -> type[_DocumentOracle]:
    _DocumentOracle.chunks = [
        _doc_chunk(0, "総則の本文。", "規程 > 第1章 総則", 1),
        _doc_chunk(1, "目的の本文。", "規程 > 第1章 総則", 1),
        _doc_chunk(2, "申請の本文。" * 3, "規程 > 第2章 申請", 2),
        _doc_chunk(3, "承認の本文。", "規程 > 第3章 承認", 3),
    ]
    _DocumentOracle.contexts = []
    monkeypatch.setattr(mcp_tools, "OracleClient", _DocumentOracle)
    return _DocumentOracle


def _read_all(arguments: dict[str, Any], headers: dict[str, str]) -> list[dict[str, Any]]:
    pages: list[dict[str, Any]] = []
    while True:
        result = _call("rag_read_document", arguments, headers)
        assert result["isError"] is False, result
        pages.append(result["structuredContent"])
        cursor = pages[-1]["next_cursor"]
        if cursor is None:
            return pages
        arguments = {"document_id": "d1", "cursor": cursor, "max_chars": arguments["max_chars"]}


def test_outline_groups_chunks_into_sections_with_cursors(
    auth: ProductionAuth, document_oracle: type[_DocumentOracle]
) -> None:
    user = auth.user_with_permissions("searcher", ["menu.search"], knowledge_base_ids=["kb-1"])
    headers = _token(user.user_uuid)

    result = _call("rag_outline", {"document_id": "d1"}, headers)

    assert result["isError"] is False, result
    body = result["structuredContent"]
    assert (body["file_name"], body["chunk_set_id"], body["chunk_count"]) == ("規程.pdf", "cs-1", 4)
    assert (body["page_start"], body["page_end"]) == (1, 3)
    assert [section["section_path"][-1] for section in body["sections"]] == [
        "第1章 総則",
        "第2章 申請",
        "第3章 承認",
    ]
    assert [section["chunk_count"] for section in body["sections"]] == [2, 1, 1]
    # 節の cursor から読むと、その節の先頭の chunk から始まる。
    second = _call(
        "rag_read_document",
        {"document_id": "d1", "cursor": body["sections"][1]["cursor"], "max_chars": 1000},
        headers,
    )["structuredContent"]
    assert second["chunks"][0]["chunk_id"] == "d1:cs-1:2"
    assert document_oracle.contexts[-1].allowed_knowledge_base_ids == frozenset({"kb-1"})


def test_read_document_reads_whole_document_in_order_without_gaps(
    auth: ProductionAuth, document_oracle: type[_DocumentOracle]
) -> None:
    user = auth.user_with_permissions("searcher", ["menu.search"])
    headers = _token(user.user_uuid)

    whole = _read_all({"document_id": "d1", "max_chars": 1000}, headers)
    assert len(whole) == 1
    text = whole[0]["text"]
    assert text.startswith("--- p.1 ---\n総則の本文。\n\n目的の本文。\n\n--- p.2 ---\n申請の本文。")
    assert text.endswith("--- p.3 ---\n承認の本文。")
    for chunk in whole[0]["chunks"]:
        source = next(item for item in document_oracle.chunks if item.chunk_id == chunk["chunk_id"])
        assert text[chunk["start"] : chunk["end"]] == source.text
    assert whole[0]["chunks"][0]["element_locator"] == "doc:d1/ext:er_1/page:1/el:el-0"

    # 小さい上限で続けて読むと、本文を重複も欠けも無く読み切る（長い chunk は途中で切って続ける）。
    pages = _read_all({"document_id": "d1", "max_chars": 14}, headers)
    assert len(pages) > 3
    read = "".join(page["text"] for page in pages)
    for source in document_oracle.chunks:
        assert source.text in read.replace("\n", "").replace("--- p.1 ---", "").replace(
            "--- p.2 ---", ""
        ).replace("--- p.3 ---", "")
    assert all(len(page["text"]) <= 14 for page in pages)


def test_read_document_starts_from_page_section_or_locator(
    auth: ProductionAuth, document_oracle: type[_DocumentOracle]
) -> None:
    del document_oracle
    user = auth.user_with_permissions("searcher", ["menu.search"])
    headers = _token(user.user_uuid)

    def first_chunk(arguments: dict[str, Any]) -> str:
        body = _call("rag_read_document", {"document_id": "d1", **arguments}, headers)
        return str(body["structuredContent"]["chunks"][0]["chunk_id"])

    assert first_chunk({"page": 3}) == "d1:cs-1:3"
    assert first_chunk({"section": "規程 > 第2章 申請"}) == "d1:cs-1:2"
    assert first_chunk({"locator": "doc:d1/ext:er_1/el:el-1"}) == "d1:cs-1:1"


def test_read_document_errors(auth: ProductionAuth, document_oracle: type[_DocumentOracle]) -> None:
    del document_oracle
    user = auth.user_with_permissions("searcher", ["menu.search"])
    headers = _token(user.user_uuid)

    def error(arguments: dict[str, Any]) -> str:
        body = _call("rag_read_document", arguments, headers)
        assert body["isError"] is True, body
        return str(body["structuredContent"]["error_code"])

    stale_cursor = encode_cursor({"cs": "cs-old", "i": 0, "o": 0})
    assert error({"document_id": "d1", "cursor": stale_cursor}) == mcp_tools.SOURCE_STALE_CODE
    assert error({"document_id": "d1", "cursor": "not-a-cursor"}) == mcp_tools.CURSOR_INVALID_CODE
    assert error({"document_id": "d9"}) == mcp_tools.SOURCE_NOT_FOUND_CODE
    assert error({"document_id": "d1", "page": 9}) == mcp_tools.SOURCE_NOT_FOUND_CODE
    assert (
        error({"document_id": "d1", "page": 1, "section": "規程"}) == "MCP_TOOL_ARGUMENTS_INVALID"
    )
    assert (
        _call("rag_outline", {"document_id": "d9"}, headers)["structuredContent"]["error_code"]
        == mcp_tools.SOURCE_NOT_FOUND_CODE
    )


# ---------------------------------------------------------------------------
# 図の元の画像（rag_read_source の include_image。#1282）
# ---------------------------------------------------------------------------


def _figure_pdf() -> bytes:
    import fitz  # type: ignore[import-untyped]

    document = fitz.open()
    document.new_page(width=600, height=800)
    # 図は 2 頁目にある。
    page = document.new_page(width=600, height=800)
    page.draw_rect(fitz.Rect(60, 80, 300, 200), color=(0, 0, 1), fill=(0, 0, 1))
    data: bytes = document.tobytes()
    document.close()
    return data


class _FigureSourceOracle(_SourceOracle):
    """図の chunk と、処理レシピのファイル準備後の artifact を持つ fake。"""

    async def get_document(self, document_id: str) -> object | None:
        if document_id != "d1":
            return None
        return SimpleNamespace(object_storage_path="docs/d1/source.pdf", preprocess_artifact=None)

    async def get_document_recipe(self, document_id: str, recipe_id: str) -> object | None:
        del document_id
        if recipe_id != "r-1":
            return None
        return {
            "preprocess_artifact": {
                "derivation_id": "dv-1",
                "profile": "pdf_normalize",
                "converted": True,
                "object_storage_path": "docs/d1/r-1/prepared.pdf",
                "content_type": "application/pdf",
                "file_name": "prepared.pdf",
            }
        }


class _FigureStorage:
    files: dict[str, bytes] = {}
    reads: list[str] = []

    async def get(self, path: str) -> bytes:
        self.reads.append(path)
        if path not in self.files:
            raise FileNotFoundError(path)
        return self.files[path]


@pytest.fixture
def figure_source(monkeypatch: MonkeyPatch) -> type[_FigureSourceOracle]:
    _FigureSourceOracle.visible = {
        ("d1", "c-fig"): _chunk(
            "c-fig",
            text="申請画面。右上の「承認」ボタンを押す。",
            content_kind="figure",
            figure_text_source="vision",
            chunk_set_id="cs-1",
            recipe_id="r-1",
            bbox="[60, 80, 300, 200]",
            bbox_unit="absolute",
            **_FIGURE_PAGE,
        ),
        ("d1", "c-text"): _chunk("c-text", content_kind="text", page_start=1),
    }
    _FigureSourceOracle.stale = set()
    _FigureSourceOracle.contexts = []
    _FigureStorage.files = {"docs/d1/r-1/prepared.pdf": _figure_pdf()}
    _FigureStorage.reads = []
    monkeypatch.setattr(mcp_tools, "OracleClient", _FigureSourceOracle)
    monkeypatch.setattr(document_crop, "ObjectStorageClient", _FigureStorage)
    return _FigureSourceOracle


def _png_size(data: bytes) -> tuple[int, int]:
    import fitz

    pixmap = fitz.Pixmap(data)
    return pixmap.width, pixmap.height


def test_read_source_returns_bounded_figure_image_rechecked_on_every_read(
    auth: ProductionAuth, figure_source: type[_FigureSourceOracle]
) -> None:
    """図の画像は content の image で返し、読むたびに今の見え方の条件で chunk を読み直す。"""
    user = auth.user_with_permissions("searcher", ["menu.search"], knowledge_base_ids=["kb-1"])
    headers = _token(user.user_uuid)

    text_only = _call("rag_read_source", {"document_id": "d1", "chunk_id": "c-fig"}, headers)
    assert [block["type"] for block in text_only["content"]] == ["text"]
    assert text_only["structuredContent"]["image"] is None
    assert text_only["structuredContent"]["evidence_type"] == "figure_description"
    assert text_only["structuredContent"]["image_ref"]["page"] == 2
    # 画像を求めなければ元のファイルは読まない。
    assert _FigureStorage.reads == []

    result = _call(
        "rag_read_source",
        {"document_id": "d1", "chunk_id": "c-fig", "include_image": True},
        headers,
    )
    assert result["isError"] is False, result
    body = result["structuredContent"]
    image_block = result["content"][body["image"]["content_index"]]
    assert (image_block["type"], image_block["mimeType"]) == ("image", "image/png")
    png = base64.b64decode(image_block["data"])
    assert body["image"] == {
        "mime_type": "image/png",
        "width": _png_size(png)[0],
        "height": _png_size(png)[1],
        "byte_size": len(png),
        "sha256": hashlib.sha256(png).hexdigest(),
        "content_index": 1,
    }
    assert max(_png_size(png)) <= mcp_tools.IMAGE_MAX_EDGE_PX
    assert len(png) <= mcp_tools.IMAGE_MAX_BYTES
    # 画像のデータは structuredContent（呼び出し側のモデルの文脈）に入れない。
    assert image_block["data"] not in json.dumps(body)
    assert image_block["data"] not in result["content"][0]["text"]
    # レシピが解析したファイル（ファイル準備後の artifact）から切り出す。
    assert _FigureStorage.reads == ["docs/d1/r-1/prepared.pdf"]
    # 読むたびに token の利用者の範囲で chunk を読み直す。
    assert len(figure_source.contexts) == 2
    assert figure_source.contexts[-1].allowed_knowledge_base_ids == frozenset({"kb-1"})

    # 版が変わった（chunk_set が有効でない）根拠は、画像も返さず source_stale。
    figure_source.visible = {}
    figure_source.stale = {("d1", "c-fig")}
    stale = _call(
        "rag_read_source",
        {"document_id": "d1", "chunk_id": "c-fig", "include_image": True},
        headers,
    )
    assert stale["structuredContent"]["error_code"] == mcp_tools.SOURCE_STALE_CODE
    assert [block["type"] for block in stale["content"]] == ["text"]
    assert _FigureStorage.reads == ["docs/d1/r-1/prepared.pdf"]
    # 見えなくなった根拠も同じ（ID を知っていても読めない）。
    figure_source.stale = set()
    gone = _call(
        "rag_read_source",
        {"document_id": "d1", "chunk_id": "c-fig", "include_image": True},
        headers,
    )
    assert gone["structuredContent"]["error_code"] == mcp_tools.SOURCE_NOT_FOUND_CODE


def test_read_source_image_rejects_client_regions_and_unauthorized_users(
    auth: ProductionAuth, figure_source: type[_FigureSourceOracle]
) -> None:
    del figure_source
    user = auth.user_with_permissions("searcher", ["menu.search"])
    # 領域はサーバーが保存した場所から決める。利用者は座標・パスを渡せない。
    for extra in ({"bbox": [0, 0, 600, 800]}, {"page": 1}, {"path": "docs/other.pdf"}):
        invalid = _call(
            "rag_read_source",
            {"document_id": "d1", "chunk_id": "c-fig", "include_image": True, **extra},
            _token(user.user_uuid),
        )
        assert invalid["structuredContent"]["error_code"] == "MCP_TOOL_ARGUMENTS_INVALID"
    viewer = auth.user_with_permissions("viewer", ["menu.upload"])
    denied = _call(
        "rag_read_source",
        {"document_id": "d1", "chunk_id": "c-fig", "include_image": True},
        _token(viewer.user_uuid),
    )
    assert denied["structuredContent"]["error_code"] == "MCP_TOOL_FORBIDDEN"
    assert _FigureStorage.reads == []


def test_read_source_image_errors_are_distinguishable(
    auth: ProductionAuth,
    figure_source: type[_FigureSourceOracle],
    monkeypatch: MonkeyPatch,
) -> None:
    user = auth.user_with_permissions("searcher", ["menu.search"])
    headers = _token(user.user_uuid)
    scopes: list[str] = []
    monkeypatch.setattr(
        mcp_tools, "enforce_rate_limit", lambda scope, _request: scopes.append(scope)
    )

    def read(chunk_id: str) -> dict[str, Any]:
        body: dict[str, Any] = _call(
            "rag_read_source",
            {"document_id": "d1", "chunk_id": chunk_id, "include_image": True},
            headers,
        )["structuredContent"]
        return body

    # 図でない根拠。
    assert read("c-text")["error_code"] == mcp_tools.IMAGE_NOT_AVAILABLE_CODE
    # 上限まで縮めても大きすぎる画像。
    monkeypatch.setattr(mcp_tools, "IMAGE_MAX_BYTES", 10)
    too_large = read("c-fig")
    assert (too_large["error_code"], too_large["status"]) == (mcp_tools.IMAGE_TOO_LARGE_CODE, 413)
    # 元のファイルが無い（「根拠が無い」とは区別する）。
    _FigureStorage.files = {}
    missing = read("c-fig")
    assert (missing["error_code"], missing["status"]) == (
        mcp_tools.IMAGE_SOURCE_MISSING_CODE,
        404,
    )
    # 画像の読み取りは検索と同じ rate limit で守る（本文だけの読み取りは数えない）。
    _call("rag_read_source", {"document_id": "d1", "chunk_id": "c-fig"}, headers)
    assert scopes == ["search", "search", "search"]
    del figure_source


# ---------------------------------------------------------------------------
# チャット（MCP では提供しない。#787）
# ---------------------------------------------------------------------------


class _GuideProfileOracle:
    """rag_lookup_guides 用の検索・回答プロファイルだけを返す fake（状態を変えられる）。"""

    def __init__(self, status: str) -> None:
        self.status = status

    async def get_search_answer_profile(self, profile_id: str) -> Any:
        if profile_id != "bv-1":
            return None
        return SimpleNamespace(
            id="bv-1",
            status=SimpleNamespace(value=self.status),
            config=SearchAnswerProfileConfig(knowledge_base_ids=["kb-1"]),
        )


def test_lookup_guides_refuses_archived_profile_and_reports_load_failure(
    auth: ProductionAuth, monkeypatch: MonkeyPatch
) -> None:
    """アーカイブ済みのプロファイルは 409、ガイドを読めなければ 503（0 件と区別する。#1278）。"""
    loaded: list[str] = []
    failed = {"value": False}

    async def published(_oracle: object, profile_id: str) -> tuple[list[Any], bool]:
        loaded.append(profile_id)
        return [], failed["value"]

    monkeypatch.setattr(search_route, "load_published_guides", published)
    headers = _token(auth.user_with_permissions("searcher", ["menu.search"]).user_uuid)
    arguments = {"query": "権限を付与したい", "search_answer_profile_id": "bv-1"}

    monkeypatch.setattr(mcp_tools, "OracleClient", lambda: _GuideProfileOracle("ARCHIVED"))
    archived = _call("rag_lookup_guides", arguments, headers)
    assert archived["isError"] is True
    assert archived["structuredContent"]["status"] == 409
    assert "アーカイブ済み" in archived["structuredContent"]["message"]
    assert loaded == []

    monkeypatch.setattr(mcp_tools, "OracleClient", lambda: _GuideProfileOracle("ACTIVE"))
    ok = _call("rag_lookup_guides", arguments, headers)
    assert ok["isError"] is False
    assert ok["structuredContent"]["guides"] == []

    failed["value"] = True
    unavailable = _call("rag_lookup_guides", arguments, headers)
    assert unavailable["isError"] is True
    assert unavailable["structuredContent"]["status"] == 503
    assert unavailable["structuredContent"]["message"] == mcp_tools.GUIDES_UNAVAILABLE_MESSAGE


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
