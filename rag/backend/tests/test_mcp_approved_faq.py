"""MCP の rag_search の回答の前の承認済み FAQ の候補と、選んだ FAQ の呼び直し（#5107）。"""

from __future__ import annotations

from typing import Any

import pytest
from pytest import MonkeyPatch
from rag_engine.knowledge.approved_faq import ApprovedFaqRecord, ApprovedFaqSuggestion

from app.api.routes import search as search_route
from app.mcp import tools as mcp_tools
from app.schemas.search import SearchDiagnostics, SearchRequest, SearchResponse
from tests.test_mcp_api import _call, _token, auth  # noqa: F401 - fixture
from tests.test_search_answer_profile_domain_keywords import FakeKnowledgeOracle

FAQ = ApprovedFaqRecord(
    id="faq-1", question="合成の質問 A", approved_answer="合成の回答 A", status="approved"
)


@pytest.fixture
def searched(monkeypatch: MonkeyPatch) -> list[SearchRequest]:
    """検索・回答の pipeline（呼んだ要求を記録する）と、FAQ の候補の照会の stub。"""
    requests: list[SearchRequest] = []

    async def fake_run(request: SearchRequest, **_: Any) -> SearchResponse:
        requests.append(request)
        answer: dict[str, Any] = {"outcome": "answered"}
        if request.approved_faq_id:
            answer = {
                "answer_source": "approved_faq",
                "outcome": "answered",
                "approved_faq_id": request.approved_faq_id,
                "approved_faq_question": FAQ.question,
            }
        diagnostics = SearchDiagnostics.model_validate({"answer": answer})
        return SearchResponse(
            answer=FAQ.approved_answer if request.approved_faq_id else "資料の回答",
            citations=[],
            trace_id="trace-1",
            elapsed_ms=1.0,
            diagnostics=diagnostics,
        )

    monkeypatch.setattr(search_route, "_run_search_with_timeout", fake_run)
    monkeypatch.setattr(mcp_tools, "OracleClient", FakeKnowledgeOracle)
    return requests


def _candidates(monkeypatch: MonkeyPatch, suggestions: list[ApprovedFaqSuggestion]) -> list[str]:
    queries: list[str] = []

    async def candidates(
        _oracle: object, profile_id: str, query: str, _settings: object
    ) -> tuple[list[ApprovedFaqSuggestion], None]:
        assert profile_id == "bv-1"
        queries.append(query)
        return suggestions, None

    monkeypatch.setattr(mcp_tools, "approved_faq_candidates", candidates)
    return queries


def test_rag_search_returns_faq_suggestions_before_searching(
    auth: Any,  # noqa: F811
    monkeypatch: MonkeyPatch,
    searched: list[SearchRequest],
) -> None:
    queries = _candidates(monkeypatch, [ApprovedFaqSuggestion(record=FAQ, score=0.97)])
    user = auth.user_with_permissions("searcher", ["menu.search"])
    headers = _token(user.user_uuid)

    body = _call(
        "rag_search", {"query": "合成の質問 A", "search_answer_profile_id": "bv-1"}, headers
    )["structuredContent"]

    assert queries == ["合成の質問 A"]
    assert searched == []
    assert body["outcome"] == "approved_faq_suggestions"
    assert body["faq_suggestions"] == [
        {"id": "faq-1", "question": "合成の質問 A", "answer": "合成の回答 A", "score": 0.97}
    ]
    assert body["next_step"] == mcp_tools.APPROVED_FAQ_SUGGESTIONS_NEXT_STEP
    assert body["answer"] == "" and body["evidence"] == []

    # 選んだ Q&A を指定して呼び直すと、候補を照会せずに Q&A の回答をそのまま返す。
    chosen = _call(
        "rag_search",
        {"query": "合成の質問 A", "search_answer_profile_id": "bv-1", "approved_faq_id": "faq-1"},
        headers,
    )["structuredContent"]
    assert queries == ["合成の質問 A"]
    assert [request.approved_faq_id for request in searched] == ["faq-1"]
    assert chosen["outcome"] == "answered"
    assert chosen["answer"] == "合成の回答 A"
    assert chosen["approved_faq"] == {"id": "faq-1", "question": "合成の質問 A"}
    assert chosen["faq_suggestions"] == [] and chosen["next_step"] is None

    # どれも当たらなければ断って呼び直す。候補を出さずに資料を検索して回答する。
    declined = _call(
        "rag_search",
        {
            "query": "合成の質問 A",
            "search_answer_profile_id": "bv-1",
            "approved_faq_declined": True,
        },
        headers,
    )["structuredContent"]
    assert queries == ["合成の質問 A"]
    assert searched[-1].approved_faq_id is None
    assert declined["answer"] == "資料の回答"
    assert declined["approved_faq"] is None


def test_rag_search_without_candidates_answers_as_usual(
    auth: Any,  # noqa: F811
    monkeypatch: MonkeyPatch,
    searched: list[SearchRequest],
) -> None:
    queries = _candidates(monkeypatch, [])
    user = auth.user_with_permissions("searcher", ["menu.search"])

    body = _call(
        "rag_search",
        {"query": "合成の別の質問", "search_answer_profile_id": "bv-1"},
        _token(user.user_uuid),
    )["structuredContent"]

    assert queries == ["合成の別の質問"]
    assert len(searched) == 1
    assert body["outcome"] == "answered" and body["answer"] == "資料の回答"


def test_candidate_failure_falls_back_to_the_normal_answer(
    auth: Any,  # noqa: F811
    monkeypatch: MonkeyPatch,
    searched: list[SearchRequest],
) -> None:
    async def broken(*_args: object) -> None:
        raise RuntimeError("faq store unavailable")

    monkeypatch.setattr(mcp_tools, "approved_faq_candidates", broken)
    user = auth.user_with_permissions("searcher", ["menu.search"])

    body = _call(
        "rag_search",
        {"query": "合成の質問 A", "search_answer_profile_id": "bv-1"},
        _token(user.user_uuid),
    )["structuredContent"]

    assert len(searched) == 1
    assert body["answer"] == "資料の回答"


def test_retrieve_evidence_has_no_faq_arguments(
    auth: Any,  # noqa: F811
    searched: list[SearchRequest],
) -> None:
    """根拠の収集（回答を作らない）は FAQ の候補・選択を受け付けない。"""
    user = auth.user_with_permissions("searcher", ["menu.search"])

    result = _call(
        "rag_retrieve_evidence",
        {"query": "合成の質問 A", "search_answer_profile_id": "bv-1", "approved_faq_id": "faq-1"},
        _token(user.user_uuid),
    )

    assert result["isError"] is True
    assert searched == []
    schema = mcp_tools.RetrieveEvidenceInput.model_json_schema()["properties"]
    assert "approved_faq_id" not in schema and "approved_faq_declined" not in schema
