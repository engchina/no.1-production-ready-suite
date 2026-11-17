"""MCP の rag_chat（RAG チャットの経路で 1 問に答える。#5339）。

経路・確認・登録済みQ&A・プロファイルの自動の選択・権限・Chat の無効・通しの期限を、検索・回答の
pipeline を差し替えて確かめる（工程そのものは #5337 の経路のテストが確かめる）。
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest
from pytest import MonkeyPatch
from rag_engine.knowledge.approved_faq import ApprovedFaqRecord, ApprovedFaqSuggestion

from app.api.routes import search as search_route
from app.config import get_settings
from app.mcp import tools as mcp_tools
from app.rag.answer_timeout import run_answer_with_timeout
from app.rag.pipeline import EXPANDED_FLOW
from app.schemas.search import SearchDiagnostics, SearchRequest, SearchResponse
from tests.test_mcp_api import _call, _token, auth  # noqa: F401 - fixture
from tests.test_search_answer_profile_domain_keywords import FakeKnowledgeOracle

FAQ = ApprovedFaqRecord(
    id="faq-1", question="合成の質問 A", approved_answer="合成の回答 A", status="approved"
)
PROFILE = {"search_answer_profile_id": "bv-1"}


@pytest.fixture
def runs(monkeypatch: MonkeyPatch) -> list[tuple[SearchRequest, dict[str, Any]]]:
    """検索・回答の pipeline（呼んだ要求と引数を記録する）。"""
    calls: list[tuple[SearchRequest, dict[str, Any]]] = []

    async def fake_run(request: SearchRequest, **options: Any) -> SearchResponse:
        calls.append((request, options))
        answer: dict[str, Any] = {"outcome": "answered"}
        if request.approved_faq_id:
            answer = {
                "answer_source": "approved_faq",
                "outcome": "answered",
                "approved_faq_id": request.approved_faq_id,
                "approved_faq_question": FAQ.question,
            }
        elif "条件" in request.query and not options.get("interactive"):
            answer = {
                "envelope": {
                    "outcome": "needs_clarification",
                    "clarifications": [
                        {
                            "condition_id": "guide:c1",
                            "label": "対象",
                            "question": "対象はどれですか。",
                            "options": ["A", "B"],
                        }
                    ],
                }
            }
        text = FAQ.approved_answer if "answer_source" in answer else "チャットの回答"
        return SearchResponse(
            answer=text,
            citations=[],
            trace_id="trace-chat",
            elapsed_ms=1.0,
            diagnostics=SearchDiagnostics.model_validate({"answer": answer}),
        )

    monkeypatch.setattr(search_route, "_run_search_with_timeout", fake_run)
    monkeypatch.setattr(mcp_tools, "OracleClient", FakeKnowledgeOracle)
    return calls


def _no_candidates(monkeypatch: MonkeyPatch, suggestions: list[ApprovedFaqSuggestion]) -> None:
    async def candidates(
        _oracle: object, _profile_id: str, _query: str, _settings: object
    ) -> tuple[list[ApprovedFaqSuggestion], None]:
        return suggestions, None

    monkeypatch.setattr(mcp_tools, "approved_faq_candidates", candidates)


def _chatter(production_auth: Any, permissions: list[str] | None = None) -> dict[str, str]:
    user = production_auth.user_with_permissions("chatter", permissions or ["menu.chat"])
    return _token(user.user_uuid)


def test_chat_uses_the_expanded_flow_without_history(
    auth: Any,  # noqa: F811
    monkeypatch: MonkeyPatch,
    runs: list[tuple[SearchRequest, dict[str, Any]]],
) -> None:
    _no_candidates(monkeypatch, [])

    result = _call(
        "rag_chat",
        {"query": "合成の質問", "original_question": "合成の元の質問", **PROFILE},
        _chatter(auth),
    )

    assert result["isError"] is False, result
    body = result["structuredContent"]
    assert body["answer"] == "チャットの回答"
    assert body["status"] == "completed" and body["outcome"] == "answered"
    [(request, options)] = runs
    # Chat の経路（_run_search が EXPANDED_FLOW を渡す）を、確認を返す形（interactive=False）で
    # 呼び、通しの期限を渡す。
    assert options["interactive"] is False
    assert isinstance(options["deadline"], float)
    assert request.query == "合成の質問"
    assert request.original_question == "合成の元の質問"
    assert request.search_answer_profile_id == "bv-1"
    assert request.knowledge_base_ids == [] and request.filters == {}


def test_chat_passes_the_expanded_flow_to_the_search(monkeypatch: MonkeyPatch) -> None:
    seen: dict[str, Any] = {}

    async def fake_run(_request: SearchRequest, **options: Any) -> SearchResponse:
        seen.update(options)
        return SearchResponse(answer="", citations=[], trace_id="t", elapsed_ms=0.0)

    monkeypatch.setattr(search_route, "_run_search_with_timeout", fake_run)
    asyncio.run(mcp_tools._run_search(SearchRequest(query="合成"), interactive=True))
    assert seen["flow"] == EXPANDED_FLOW
    assert seen["interactive"] is True
    assert seen["deadline"] is None  # 期限の外（ツールの handler の外）では渡さない


def test_chat_returns_clarification_and_skip_answers_by_branches(
    auth: Any,  # noqa: F811
    monkeypatch: MonkeyPatch,
    runs: list[tuple[SearchRequest, dict[str, Any]]],
) -> None:
    _no_candidates(monkeypatch, [])
    headers = _chatter(auth)

    asked = _call("rag_chat", {"query": "条件で変わる質問", **PROFILE}, headers)[
        "structuredContent"
    ]
    assert asked["outcome"] == "needs_clarification"
    assert asked["clarifications"][0]["condition_id"] == "guide:c1"

    answered = _call(
        "rag_chat",
        {"query": "条件で変わる質問", **PROFILE, "conditions": {"guide:c1": "A"}},
        headers,
    )["structuredContent"]
    assert runs[-1][0].conditions == {"guide:c1": "A"}

    skipped = _call(
        "rag_chat", {"query": "条件で変わる質問", **PROFILE, "skip_clarification": True}, headers
    )["structuredContent"]
    # 確認を返さずに、チャットの画面と同じく残った条件を分岐で答える。
    assert runs[-1][1]["interactive"] is True
    assert skipped["outcome"] == "answered"
    assert answered["status"] == "completed"


def test_chat_returns_approved_faq_candidates_then_the_chosen_answer(
    auth: Any,  # noqa: F811
    monkeypatch: MonkeyPatch,
    runs: list[tuple[SearchRequest, dict[str, Any]]],
) -> None:
    _no_candidates(monkeypatch, [ApprovedFaqSuggestion(record=FAQ, score=0.97)])
    headers = _chatter(auth)

    candidates = _call("rag_chat", {"query": "合成の質問 A", **PROFILE}, headers)[
        "structuredContent"
    ]
    assert runs == []
    assert candidates["status"] == "approved_faq_candidates"
    assert candidates["approved_faq_candidates"] == [
        {"id": "faq-1", "question": "合成の質問 A", "score": 0.97}
    ]
    assert candidates["outcome"] is None

    chosen = _call(
        "rag_chat", {"query": "合成の質問 A", **PROFILE, "approved_faq_id": "faq-1"}, headers
    )["structuredContent"]
    assert [request.approved_faq_id for request, _ in runs] == ["faq-1"]
    assert chosen["answer"] == "合成の回答 A"
    assert chosen["approved_faq"] == {"id": "faq-1", "question": "合成の質問 A"}

    skipped = _call(
        "rag_chat", {"query": "合成の質問 A", **PROFILE, "skip_approved_faq": True}, headers
    )["structuredContent"]
    assert skipped["status"] == "completed" and skipped["answer"] == "チャットの回答"


def test_chat_selects_the_profile_when_omitted(
    auth: Any,  # noqa: F811
    monkeypatch: MonkeyPatch,
    runs: list[tuple[SearchRequest, dict[str, Any]]],
) -> None:
    _no_candidates(monkeypatch, [])
    questions: list[str] = []

    async def select(question: str) -> tuple[Any, list[Any], list[Any]]:
        questions.append(question)
        selected = mcp_tools.AutoSelectedProfile(
            id="bv-1", name="プロファイル A", method="recommended"
        )
        return selected, [], []

    monkeypatch.setattr(mcp_tools, "_select_profile", select)

    body = _call(
        "rag_chat", {"query": "短い語", "original_question": "合成の元の質問"}, _chatter(auth)
    )["structuredContent"]

    # 省略も自動の選択（範囲は検索・回答プロファイルだけ。rag_search の省略は全体の既定のまま）。
    assert questions == ["合成の元の質問"]
    assert [request.search_answer_profile_id for request, _ in runs] == ["bv-1"]
    assert body["auto_selected_search_answer_profile"] == {
        "id": "bv-1",
        "name": "プロファイル A",
        "requested_id": None,
        "method": "recommended",
    }


@pytest.mark.parametrize(
    "extra",
    [{"knowledge_base_ids": ["kb-1"]}, {"filters": {"category_name": "x"}}, {"top_k": 5}],
    ids=["knowledge_base_ids", "filters", "top_k"],
)
def test_chat_rejects_search_scope_arguments(
    auth: Any,  # noqa: F811
    runs: list[tuple[SearchRequest, dict[str, Any]]],
    extra: dict[str, Any],
) -> None:
    result = _call("rag_chat", {"query": "合成の質問", **PROFILE, **extra}, _chatter(auth))
    assert result["isError"] is True
    assert result["structuredContent"]["error_code"] == "MCP_TOOL_ARGUMENTS_INVALID"
    assert runs == []


def test_chat_requires_the_chat_permission(
    auth: Any,  # noqa: F811
    runs: list[tuple[SearchRequest, dict[str, Any]]],
) -> None:
    searcher = _chatter(auth, ["menu.search"])
    denied = _call("rag_chat", {"query": "合成の質問", **PROFILE}, searcher)
    assert denied["isError"] is True
    assert denied["structuredContent"]["error_code"] == "MCP_TOOL_FORBIDDEN"
    assert runs == []


def test_chat_is_unavailable_when_chat_is_disabled(
    auth: Any,  # noqa: F811
    monkeypatch: MonkeyPatch,
    runs: list[tuple[SearchRequest, dict[str, Any]]],
) -> None:
    monkeypatch.setattr(get_settings(), "rag_chat_enabled", False)

    result = _call("rag_chat", {"query": "合成の質問", **PROFILE}, _chatter(auth))

    assert result["isError"] is True
    error = result["structuredContent"]
    assert error["error_code"] == mcp_tools.CHAT_DISABLED_CODE
    assert error["status"] == 403
    assert error["details"]["next_tools"] == ["rag_search"]
    assert runs == []


@pytest.mark.parametrize("tool", ["rag_chat", "rag_search"])
def test_answer_tools_stop_at_one_deadline_around_the_whole_handler(
    auth: Any,  # noqa: F811
    monkeypatch: MonkeyPatch,
    tool: str,
) -> None:
    """登録済みQ&Aの照会（回答生成の前）で期限を過ぎても、同じ時間切れ（504）にする。"""
    monkeypatch.setattr(mcp_tools, "OracleClient", FakeKnowledgeOracle)
    monkeypatch.setattr(mcp_tools, "answer_timeout_seconds", lambda _settings: 0.05)
    monkeypatch.setattr(mcp_tools, "ANSWER_DEADLINE_GRACE_SECONDS", 0.0)

    async def slow_candidates(*_args: object) -> tuple[list[ApprovedFaqSuggestion], None]:
        await asyncio.sleep(5)
        return [], None

    monkeypatch.setattr(mcp_tools, "approved_faq_candidates", slow_candidates)
    headers = _chatter(auth, ["menu.chat", "menu.search"])

    result = _call(tool, {"query": "合成の質問", **PROFILE}, headers)

    assert result["isError"] is True
    error = result["structuredContent"]
    assert error["status"] == 504
    assert "回答の生成が上限の" in error["message"]


def test_answer_generation_uses_the_remaining_time_and_reports_the_whole_limit() -> None:
    """回答生成は期限の残りで打ち切り、文言には通しの上限を出す。"""
    settings = get_settings()

    async def run() -> None:
        loop = asyncio.get_running_loop()
        options = search_route._deadline_options(loop.time() + 0.05, settings)
        assert options["timeout_seconds"] <= 0.05
        assert options["limit_seconds"] == settings.rag_answer_timeout_seconds

        async def slow(_tracker: object) -> None:
            await asyncio.sleep(5)

        with pytest.raises(TimeoutError) as raised:
            await run_answer_with_timeout(slow, settings, None, **options)
        minutes = int(settings.rag_answer_timeout_seconds // 60)
        assert f"上限の {minutes} 分以内に" in str(raised.value)

    asyncio.run(run())
    assert search_route._deadline_options(None, settings) == {}
