"""MCP の登録済みの Q&A の照会（rag_approved_faq）: 候補と、選んだ Q&A の回答（#5107）。"""

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
    """検索・回答の pipeline（呼んだ要求を記録する）。選んだ Q&A なら回答をそのまま返す。"""
    requests: list[SearchRequest] = []

    async def fake_run(request: SearchRequest, **_: Any) -> SearchResponse:
        requests.append(request)
        answer: dict[str, Any] = {"outcome": "answered"}
        if request.approved_faq_id and "攻撃" not in request.query:
            answer = {
                "answer_source": "approved_faq",
                "outcome": "answered",
                "approved_faq_id": request.approved_faq_id,
                "approved_faq_question": FAQ.question,
            }
        return SearchResponse(
            answer=FAQ.approved_answer if "answer_source" in answer else "停止しました。",
            citations=[],
            trace_id="trace-1",
            elapsed_ms=1.0,
            diagnostics=SearchDiagnostics.model_validate({"answer": answer}),
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


def test_returns_candidates_and_then_the_chosen_answer_verbatim(
    auth: Any,  # noqa: F811
    monkeypatch: MonkeyPatch,
    searched: list[SearchRequest],
) -> None:
    queries = _candidates(monkeypatch, [ApprovedFaqSuggestion(record=FAQ, score=0.97)])
    user = auth.user_with_permissions("searcher", ["menu.search"])
    headers = _token(user.user_uuid)
    question = {"question": "合成の質問 A", "search_answer_profile_id": "bv-1"}

    body = _call("rag_approved_faq", question, headers)["structuredContent"]

    # 候補だけを返し、検索・回答はしない。
    assert queries == ["合成の質問 A"]
    assert searched == []
    assert body["suggestions"] == [
        {"id": "faq-1", "question": "合成の質問 A", "answer": "合成の回答 A", "score": 0.97}
    ]
    assert body["answer"] is None

    chosen = _call("rag_approved_faq", {**question, "approved_faq_id": "faq-1"}, headers)[
        "structuredContent"
    ]

    # 選んだ Q&A は検索・回答と同じ入口を通し（候補は照会し直さない）、回答をそのまま返す。
    assert queries == ["合成の質問 A"]
    assert [(r.approved_faq_id, r.search_answer_profile_id) for r in searched] == [
        ("faq-1", "bv-1")
    ]
    assert chosen["suggestions"] == []
    assert chosen["answer"] == {
        "id": "faq-1",
        "question": "合成の質問 A",
        "answer": "合成の回答 A",
        "trace_id": "trace-1",
    }


def test_no_candidates_returns_an_empty_list(
    auth: Any,  # noqa: F811
    monkeypatch: MonkeyPatch,
    searched: list[SearchRequest],
) -> None:
    _candidates(monkeypatch, [])
    user = auth.user_with_permissions("searcher", ["menu.search"])

    body = _call(
        "rag_approved_faq",
        {"question": "合成の別の質問", "search_answer_profile_id": "bv-1"},
        _token(user.user_uuid),
    )["structuredContent"]

    assert body["suggestions"] == [] and body["answer"] is None
    assert searched == []


def test_unknown_profile_and_blocked_question_are_tool_errors(
    auth: Any,  # noqa: F811
    monkeypatch: MonkeyPatch,
    searched: list[SearchRequest],
) -> None:
    _candidates(monkeypatch, [])
    user = auth.user_with_permissions("searcher", ["menu.search"])
    headers = _token(user.user_uuid)

    missing = _call(
        "rag_approved_faq", {"question": "合成の質問", "search_answer_profile_id": "bv-9"}, headers
    )
    assert missing["isError"] is True
    blocked = _call(
        "rag_approved_faq",
        {"question": "攻撃の文", "search_answer_profile_id": "bv-1", "approved_faq_id": "faq-1"},
        headers,
    )
    assert blocked["isError"] is True
    assert "合成の回答 A" not in str(blocked)


def test_rag_search_inputs_are_unchanged() -> None:
    """rag_search・rag_retrieve_evidence の入力は変えない（Q&A は別のツール）。"""
    for model in (mcp_tools.SearchInput, mcp_tools.RetrieveEvidenceInput):
        assert "approved_faq_id" not in model.model_json_schema()["properties"]
