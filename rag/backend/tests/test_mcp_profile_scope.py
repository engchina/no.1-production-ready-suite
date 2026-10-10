"""業務 Agent のデータの範囲（サービストークンの claim `profile_ids`。#1379）の MCP のテスト。

Agent の Runtime を通さずに `POST /api/mcp` を直接呼び、呼び先の RAG が「利用者の権限 ∩ claim」で
判定することを確かめる。Oracle・LLM は既存の fake / スタブ（範囲で絞る fake の検索・回答
プロファイル）に差し替え、実サービスは呼ばない。
"""

from __future__ import annotations

from typing import Any

import pytest
from pr_system_settings.auth.service_token import PROFILE_SCOPE_FORBIDDEN_CODE, issue_service_token
from pytest import MonkeyPatch

from app.api.routes import search_answer_profiles as search_answer_profiles_route
from app.config import get_settings
from app.main import app
from app.mcp import profile_scope
from app.mcp import tools as mcp_tools
from app.rag.request_context import AuditRequestContext, current_audit_request_context
from app.rag.search_answer_profile_config import SearchAnswerProfileConfig
from app.security.permissions import KNOWLEDGE_BASES_MANAGE
from tests.security_support import ProductionAuth, enable_production_auth
from tests.support import AsgiTestClient
from tests.test_mcp_api import _chunk
from tests.test_search_search_answer_profile import RecordingPipeline
from tests.test_security_api import ScopedFakeOracle
from tests.test_security_scope import (
    ScopedViewOracle,
    _captured_knowledge_base_ids,
    _install_search,
)

client = AsgiTestClient(app)
SECRET = "rag-mcp-scope-test-secret-0123456789abcdef"  # nosec B105 - テスト用
# _install_search と同じ検索・回答プロファイル → 参照先のナレッジベース。
VIEWS = {
    "bv-1": SearchAnswerProfileConfig(knowledge_base_ids=["kb-1", "kb-2"]),
    "bv-2": SearchAnswerProfileConfig(knowledge_base_ids=["kb-3"]),
    "bv-3": SearchAnswerProfileConfig(knowledge_base_ids=["kb-8", "kb-9"]),
}


@pytest.fixture
def auth(monkeypatch: MonkeyPatch) -> ProductionAuth:
    production = enable_production_auth(monkeypatch)
    monkeypatch.setattr(get_settings(), "app_service_token_secret", SECRET)
    _install_search(monkeypatch)
    monkeypatch.setattr(profile_scope, "OracleClient", lambda *_a, **_k: ScopedViewOracle(VIEWS))
    return production


def _headers(user_uuid: str, profile_ids: list[str] | None = None) -> dict[str, str]:
    token = issue_service_token(
        SECRET,
        subject=user_uuid,
        audience="rag",
        issuer="agent",
        claims={"run_id": "run-1", "agent_id": "agent-1"},
        profile_ids=profile_ids,
    )
    return {"Authorization": f"Bearer {token}"}


def _call(name: str, arguments: dict[str, Any], headers: dict[str, str]) -> dict[str, Any]:
    response = client.post(
        "/api/mcp",
        json={
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {"name": name, "arguments": arguments},
        },
        headers=headers,
    )
    assert response.status_code == 200, response.text
    result: dict[str, Any] = response.json()["result"]
    return result


def _forbidden(result: dict[str, Any]) -> str:
    assert result["isError"] is True, result
    content = result["structuredContent"]
    assert (content["status"], content["error_code"]) == (403, PROFILE_SCOPE_FORBIDDEN_CODE)
    message: str = content["message"]
    return message


@pytest.mark.parametrize("tool", ["rag_search", "rag_retrieve_evidence"])
def test_claim_limits_search_to_scoped_profiles_and_their_knowledge_bases(
    auth: ProductionAuth, tool: str
) -> None:
    user = auth.user_with_permissions(
        "searcher",
        ["menu.search"],
        search_answer_profile_ids=["bv-1", "bv-2"],
        knowledge_base_ids=["kb-1", "kb-2", "kb-3"],
    )
    scoped = _headers(user.user_uuid, ["bv-1"])

    # 利用者が使えても、範囲の外の検索・回答プロファイルは 403。
    assert "bv-2" in _forbidden(
        _call(tool, {"query": "規程", "search_answer_profile_id": "bv-2"}, scoped)
    )
    # 検索・回答プロファイルの無い knowledge_base_ids だけの検索は拒否する。
    assert "search_answer_profile_id" in _forbidden(
        _call(tool, {"query": "規程", "knowledge_base_ids": ["kb-1"]}, scoped)
    )
    _forbidden(_call(tool, {"query": "規程"}, scoped))
    # 指定したプロファイルの参照先の外のナレッジベース（kb-3 は bv-2 の参照先）は 403。
    assert "kb-3" in _forbidden(
        _call(
            tool,
            {"query": "規程", "search_answer_profile_id": "bv-1", "knowledge_base_ids": ["kb-3"]},
            scoped,
        )
    )
    assert RecordingPipeline.captured_request is None

    narrowed = _call(
        tool,
        {"query": "規程", "search_answer_profile_id": "bv-1", "knowledge_base_ids": ["kb-2"]},
        scoped,
    )
    assert narrowed["isError"] is False, narrowed
    assert _captured_knowledge_base_ids() == ["kb-2"]
    ok = _call(tool, {"query": "規程", "search_answer_profile_id": "bv-1"}, scoped)
    assert ok["isError"] is False, ok
    assert _captured_knowledge_base_ids() == ["kb-1", "kb-2"]

    # claim の無い token は今までどおり（範囲の判定をしない）。
    plain = _headers(user.user_uuid)
    assert (
        _call(tool, {"query": "規程", "search_answer_profile_id": "bv-2"}, plain)["isError"]
        is False
    )
    assert _captured_knowledge_base_ids() == ["kb-3"]
    assert _call(tool, {"query": "規程", "knowledge_base_ids": ["kb-1"]}, plain)["isError"] is False


@pytest.mark.parametrize("tool", ["rag_search", "rag_retrieve_evidence"])
def test_including_superseded_keeps_the_claim_scope(auth: ProductionAuth, tool: str) -> None:
    """旧版を含めても（include_superseded。#1392）、範囲の判定と検索の KB は変わらない。"""
    user = auth.user_with_permissions(
        "searcher",
        ["menu.search"],
        search_answer_profile_ids=["bv-1", "bv-2"],
        knowledge_base_ids=["kb-1", "kb-2", "kb-3"],
    )
    scoped = _headers(user.user_uuid, ["bv-1"])
    old = {"query": "旧版との違い", "include_superseded": True}

    # 範囲の外の検索・回答プロファイル・KB と、プロファイルの無い検索は、旧版を含めても 403。
    _forbidden(_call(tool, {**old, "search_answer_profile_id": "bv-2"}, scoped))
    _forbidden(_call(tool, {**old, "knowledge_base_ids": ["kb-3"]}, scoped))
    _forbidden(_call(tool, old, scoped))
    assert "kb-3" in _forbidden(
        _call(
            tool,
            {**old, "search_answer_profile_id": "bv-1", "knowledge_base_ids": ["kb-3"]},
            scoped,
        )
    )
    assert RecordingPipeline.captured_request is None

    ok = _call(tool, {**old, "search_answer_profile_id": "bv-1"}, scoped)
    assert ok["isError"] is False, ok
    captured = RecordingPipeline.captured_request
    assert captured is not None
    assert _captured_knowledge_base_ids() == ["kb-1", "kb-2"]
    # 検索の要求の旧版の扱いは、範囲（KB）の述語と一緒に Oracle の検索へ渡る。
    assert captured.filters == {"include_superseded": "true", "knowledge_base_id": "kb-1,kb-2"}


def test_claim_does_not_widen_user_permissions(auth: ProductionAuth) -> None:
    """claim にあっても利用者が使えない検索・回答プロファイルは、今までどおり 404。"""
    user = auth.user_with_permissions(
        "searcher", ["menu.search"], search_answer_profile_ids=["bv-1"]
    )
    scoped = _headers(user.user_uuid, ["bv-1", "bv-2"])

    missing = _call("rag_search", {"query": "規程", "search_answer_profile_id": "bv-2"}, scoped)
    assert (missing["isError"], missing["structuredContent"]["status"]) == (True, 404)
    assert RecordingPipeline.captured_request is None


def test_claim_limits_profile_list(auth: ProductionAuth, monkeypatch: MonkeyPatch) -> None:
    fake = ScopedFakeOracle()
    monkeypatch.setattr(search_answer_profiles_route, "OracleClient", lambda *_a, **_k: fake)
    user = auth.user_with_permissions(
        "searcher", ["menu.search"], search_answer_profile_ids=["bv-1", "bv-2"]
    )

    scoped = _call(
        "rag_list_search_answer_profiles", {"limit": 10}, _headers(user.user_uuid, ["bv-1", "bv-3"])
    )
    assert [item["id"] for item in scoped["structuredContent"]["search_answer_profiles"]] == [
        "bv-1"
    ]
    plain = _call("rag_list_search_answer_profiles", {"limit": 10}, _headers(user.user_uuid))
    assert [item["id"] for item in plain["structuredContent"]["search_answer_profiles"]] == [
        "bv-1",
        "bv-2",
    ]


class _RecordingReadOracle:
    """文書を読むツールの、Oracle を読むときの対象範囲（監査 context）を残す fake。"""

    contexts: list[AuditRequestContext] = []

    async def retrievable_chunk(self, document_id: str, chunk_id: str) -> Any:
        self.contexts.append(current_audit_request_context())
        return _chunk(chunk_id, text="本文")

    async def chunk_set_extraction_recipe_ids(self, chunk_set_ids: list[str]) -> dict[str, str]:
        return {}

    async def document_reading_index(self, document_id: str) -> tuple[None, list[Any]]:
        self.contexts.append(current_audit_request_context())
        return None, []

    async def accessible_document_exists(self, document_id: str) -> bool:
        return False


@pytest.mark.parametrize(
    ("user_knowledge_bases", "expected"),
    [(None, {"kb-1", "kb-2"}), (["kb-1", "kb-3"], {"kb-1"})],
    ids=["user-unrestricted", "user-restricted"],
)
def test_claim_limits_reading_tools_to_scoped_knowledge_bases(
    auth: ProductionAuth,
    monkeypatch: MonkeyPatch,
    user_knowledge_bases: list[str] | None,
    expected: set[str],
) -> None:
    """文書を読むツールは、範囲のプロファイルの参照先の KB（∩ 利用者の KB）だけを読む。

    読み取りは検索と同じ見え方の条件（監査 context の KB の範囲）で Oracle を読むので、範囲の外の
    文書は「見つからない」（source_not_found）になる。
    """
    _RecordingReadOracle.contexts = []
    monkeypatch.setattr(mcp_tools, "OracleClient", _RecordingReadOracle)
    user = auth.user_with_permissions(
        "searcher",
        # ナレッジベース管理はすべての KB を使える（利用者の KB の範囲の制限なし）。
        ["menu.search"]
        if user_knowledge_bases is not None
        else ["menu.search", KNOWLEDGE_BASES_MANAGE],
        search_answer_profile_ids=["bv-1", "bv-2"],
        knowledge_base_ids=user_knowledge_bases or (),
    )
    scoped = _headers(user.user_uuid, ["bv-1"])

    read = _call("rag_read_source", {"document_id": "d1", "chunk_id": "c1"}, scoped)
    assert read["isError"] is False, read
    outline = _call("rag_outline", {"document_id": "d1"}, scoped)
    assert outline["structuredContent"]["error_code"] == mcp_tools.SOURCE_NOT_FOUND_CODE
    document = _call("rag_read_document", {"document_id": "d1"}, scoped)
    assert document["structuredContent"]["error_code"] == mcp_tools.SOURCE_NOT_FOUND_CODE
    assert [context.allowed_knowledge_base_ids for context in _RecordingReadOracle.contexts] == [
        frozenset(expected)
    ] * 3
    assert {
        context.allowed_search_answer_profile_ids for context in _RecordingReadOracle.contexts
    } == {frozenset({"bv-1"})}

    # claim の無い token は利用者の範囲のまま。
    _RecordingReadOracle.contexts = []
    _call("rag_read_source", {"document_id": "d1", "chunk_id": "c1"}, _headers(user.user_uuid))
    assert _RecordingReadOracle.contexts[-1].allowed_knowledge_base_ids == (
        frozenset(user_knowledge_bases) if user_knowledge_bases is not None else None
    )


def test_claim_limits_guides_and_answer_validation(auth: ProductionAuth) -> None:
    user = auth.user_with_permissions(
        "searcher", ["menu.search"], search_answer_profile_ids=["bv-1", "bv-2"]
    )
    scoped = _headers(user.user_uuid, ["bv-1"])

    _forbidden(
        _call("rag_lookup_guides", {"query": "精算", "search_answer_profile_id": "bv-2"}, scoped)
    )
    _forbidden(
        _call(
            "rag_validate_answer",
            {
                "query": "精算",
                "answer": "期限は月末です。",
                "evidence": [{"document_id": "d1", "chunk_id": "c1"}],
                "guide": {"search_answer_profile_id": "bv-2", "guide_id": "g-1", "revision": 1},
            },
            scoped,
        )
    )


def test_scope_that_cannot_be_loaded_is_not_treated_as_no_scope(
    auth: ProductionAuth, monkeypatch: MonkeyPatch
) -> None:
    """範囲のプロファイルを読めないときは 503（範囲なしとして通さない）。"""

    class _BrokenOracle:
        async def get_search_answer_profile(self, search_answer_profile_id: str) -> None:
            raise RuntimeError("DPY-6005")

    monkeypatch.setattr(profile_scope, "OracleClient", _BrokenOracle)
    user = auth.user_with_permissions(
        "searcher", ["menu.search"], search_answer_profile_ids=["bv-1"]
    )

    result = _call(
        "rag_search",
        {"query": "規程", "search_answer_profile_id": "bv-1"},
        _headers(user.user_uuid, ["bv-1"]),
    )
    assert (result["isError"], result["structuredContent"]["status"]) == (True, 503)
    assert RecordingPipeline.captured_request is None
