"""MCP の rag_search の承認済み FAQ（登録済みの Q&A）: 候補・選んだ Q&A の回答・候補の省略（#5134）。"""

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
        text = FAQ.approved_answer if "answer_source" in answer else "検索した回答"
        return SearchResponse(
            answer=text,
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


def _searcher(auth: Any) -> dict[str, str]:
    user = auth.user_with_permissions("searcher", ["menu.search"])
    return _token(user.user_uuid)


def test_returns_candidates_only_and_then_the_chosen_answer_verbatim(
    auth: Any,  # noqa: F811
    monkeypatch: MonkeyPatch,
    searched: list[SearchRequest],
) -> None:
    queries = _candidates(monkeypatch, [ApprovedFaqSuggestion(record=FAQ, score=0.97)])
    headers = _searcher(auth)
    question = {
        "query": "合成の語",
        "original_question": "合成の質問 A",
        "search_answer_profile_id": "bv-1",
    }

    body = _call("rag_search", question, headers)["structuredContent"]

    # 候補だけを返し、検索・回答はしない（照合は利用者の元の質問）。
    assert queries == ["合成の質問 A"]
    assert searched == []
    assert body["status"] == "approved_faq_candidates"
    assert body["approved_faq_candidates"] == [
        {"id": "faq-1", "question": "合成の質問 A", "score": 0.97}
    ]
    assert body["outcome"] is None
    assert body["answer"] == "" and body["evidence"] == []
    assert body["approved_faq"] is None

    chosen = _call("rag_search", {**question, "approved_faq_id": "faq-1"}, headers)[
        "structuredContent"
    ]

    # 選んだ Q&A は検索・回答と同じ入口を通し（候補は照会し直さない）、回答をそのまま返す。
    assert queries == ["合成の質問 A"]
    assert [(r.approved_faq_id, r.search_answer_profile_id) for r in searched] == [
        ("faq-1", "bv-1")
    ]
    assert chosen["status"] == "completed"
    assert chosen["answer"] == "合成の回答 A"
    assert chosen["outcome"] == "answered"
    assert chosen["approved_faq"] == {"id": "faq-1", "question": "合成の質問 A"}
    assert chosen["approved_faq_candidates"] == []
    assert chosen["trace_id"] == "trace-1"


def test_skip_approved_faq_searches_without_candidates(
    auth: Any,  # noqa: F811
    monkeypatch: MonkeyPatch,
    searched: list[SearchRequest],
) -> None:
    queries = _candidates(monkeypatch, [ApprovedFaqSuggestion(record=FAQ, score=0.97)])

    body = _call(
        "rag_search",
        {"query": "合成の質問 A", "search_answer_profile_id": "bv-1", "skip_approved_faq": True},
        _searcher(auth),
    )["structuredContent"]

    assert queries == []
    assert [r.approved_faq_id for r in searched] == [None]
    assert body["status"] == "completed"
    assert body["answer"] == "検索した回答"
    assert body["approved_faq"] is None


def test_no_candidates_searches_as_before(
    auth: Any,  # noqa: F811
    monkeypatch: MonkeyPatch,
    searched: list[SearchRequest],
) -> None:
    queries = _candidates(monkeypatch, [])

    body = _call(
        "rag_search",
        {"query": "合成の別の質問", "search_answer_profile_id": "bv-1"},
        _searcher(auth),
    )["structuredContent"]

    assert queries == ["合成の別の質問"]
    assert len(searched) == 1
    assert body["status"] == "completed" and body["outcome"] == "answered"
    assert body["approved_faq_candidates"] == []


def test_search_without_profile_does_not_look_up_candidates(
    auth: Any,  # noqa: F811
    monkeypatch: MonkeyPatch,
    searched: list[SearchRequest],
) -> None:
    queries = _candidates(monkeypatch, [ApprovedFaqSuggestion(record=FAQ, score=0.97)])

    body = _call("rag_search", {"query": "合成の質問 A"}, _searcher(auth))["structuredContent"]

    assert queries == []
    assert len(searched) == 1
    assert body["status"] == "completed"


def test_blocked_question_with_chosen_faq_does_not_return_the_faq_answer(
    auth: Any,  # noqa: F811
    monkeypatch: MonkeyPatch,
    searched: list[SearchRequest],
) -> None:
    _candidates(monkeypatch, [])

    body = _call(
        "rag_search",
        {"query": "攻撃の文", "search_answer_profile_id": "bv-1", "approved_faq_id": "faq-1"},
        _searcher(auth),
    )["structuredContent"]

    assert body["approved_faq"] is None
    assert "合成の回答 A" not in str(body)


@pytest.mark.parametrize(
    "arguments",
    [
        {"search_answer_profile_id": "bv-1", "approved_faq_id": "faq-1", "skip_approved_faq": True},
        {"approved_faq_id": "faq-1"},
    ],
    ids=["both_choices", "without_profile"],
)
def test_invalid_faq_arguments_are_tool_errors(
    auth: Any,  # noqa: F811
    monkeypatch: MonkeyPatch,
    searched: list[SearchRequest],
    arguments: dict[str, Any],
) -> None:
    _candidates(monkeypatch, [])

    result = _call("rag_search", {"query": "合成の質問 A", **arguments}, _searcher(auth))

    assert result["isError"] is True
    assert result["structuredContent"]["error_code"] == "MCP_TOOL_ARGUMENTS_INVALID"
    assert searched == []


def test_approved_faq_arguments_are_rag_search_only() -> None:
    """承認済み FAQ の引数は rag_search だけ（根拠の取得は候補を返さない）。"""
    search = mcp_tools.SearchInput.model_json_schema()["properties"]
    retrieve = mcp_tools.RetrieveEvidenceInput.model_json_schema()["properties"]
    assert {"approved_faq_id", "skip_approved_faq"} <= set(search)
    assert not {"approved_faq_id", "skip_approved_faq"} & set(retrieve)
