"""回答の経路（#5332）。合成の質問と stub の依存で確かめる。

RAG 検索（画面の `/api/search`・`/stream`・検索だけ）は基本の RAG（⑤⑥ 検索と文脈の補完 →
⑧ 回答）で、業務ガイドの照合（③）・承認済み FAQ・⑦ 根拠の拡張をしない。RAG チャット・品質評価・
業務ガイドの下書きの試答・MCP は ③・FAQ・⑦ を含む経路。⑤⑥ と ⑧ は両方の経路で同じ関数を通る。
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Callable
from typing import Any

import pytest
import rag_engine.generation.answering as answering
from fastapi import HTTPException
from pytest import MonkeyPatch

from app.api.routes import search as search_route
from app.config import get_settings
from app.rag import evaluation as evaluation_module
from app.rag.answer_engine import AnswerEngine
from app.rag.pipeline import BASIC_FLOW, EXPANDED_FLOW, AnswerFlow
from app.schemas.evaluation import EvaluationCase, EvaluationTurn
from app.schemas.search import SearchRequest, SearchResponse
from tests.support import AsgiTestClient
from tests.test_answer_engine import _run_with_progress
from tests.test_entry_order import _install

pytestmark = pytest.mark.usefixtures("system_access")

QUESTION = "合成の登録方法は？"


def _response() -> SearchResponse:
    return SearchResponse(answer="", citations=[], trace_id="trace-flow", elapsed_ms=1.0)


def test_search_route_uses_the_basic_flow(monkeypatch: MonkeyPatch) -> None:
    from app.main import app

    captured: list[object] = []

    async def fake_run(request: SearchRequest, **options: Any) -> SearchResponse:
        captured.append(options.get("flow"))
        return _response()

    monkeypatch.setattr(search_route, "_run_search_with_timeout", fake_run)
    response = AsgiTestClient(app).post("/api/search", json={"query": QUESTION})

    assert response.status_code == 200
    assert captured == [BASIC_FLOW]


def test_search_stream_route_uses_the_basic_flow(monkeypatch: MonkeyPatch) -> None:
    from app.main import app

    captured: list[tuple[str, object]] = []

    async def fake_checked(request: SearchRequest, settings: Any, **options: Any) -> Any:
        captured.append(("resolve", options.get("flow")))
        return (request, settings, None, None), None

    async def fake_stream(_resolved: Any, **options: Any) -> AsyncIterator[str]:
        captured.append(("pipeline", options.get("flow")))
        yield "event: done\ndata: {}\n\n"

    monkeypatch.setattr(search_route, "_checked_query_context", fake_checked)
    monkeypatch.setattr(search_route, "_stream_search_events_with_timeout", fake_stream)
    response = AsgiTestClient(app).post("/api/search/stream", json={"query": QUESTION})

    assert response.status_code == 200
    assert captured == [("resolve", BASIC_FLOW), ("pipeline", BASIC_FLOW)]


async def test_guide_preview_uses_the_expanded_flow(monkeypatch: MonkeyPatch) -> None:
    captured: list[object] = []

    async def fake_run(request: SearchRequest, **options: Any) -> SearchResponse:
        captured.append(options.get("flow"))
        return _response()

    monkeypatch.setattr(search_route, "_run_search_with_timeout", fake_run)
    await search_route.run_guide_preview(SearchRequest(query=QUESTION), object())  # type: ignore[arg-type]

    assert captured == [EXPANDED_FLOW]


async def test_search_with_timeout_passes_the_flow_to_the_entry_and_the_pipeline(
    monkeypatch: MonkeyPatch,
) -> None:
    """時間の上限つきの実行は、入口の解決と pipeline に同じ経路を渡す（MCP・後続の入口も同じ）。"""
    captured: list[tuple[str, object]] = []

    async def fake_checked(request: SearchRequest, settings: Any, **options: Any) -> Any:
        captured.append(("resolve", options.get("flow")))
        captured.append(("interactive", options.get("interactive")))
        return (request, settings, None, None), None

    class Pipeline:
        def __init__(self, **kwargs: Any) -> None:
            captured.append(("pipeline", kwargs.get("flow")))

        async def run(self, *_args: Any, **_kwargs: Any) -> SearchResponse:
            return _response()

    monkeypatch.setattr(search_route, "_checked_query_context", fake_checked)
    monkeypatch.setattr(search_route, "RagPipeline", Pipeline)
    for flow in (BASIC_FLOW, EXPANDED_FLOW):
        captured.clear()
        await search_route._run_search_with_timeout(
            SearchRequest(query=QUESTION), flow=flow, interactive=True
        )
        assert captured == [("resolve", flow), ("interactive", True), ("pipeline", flow)]


@pytest.mark.parametrize(
    ("answer_mode", "expected_flow"),
    [("chat", EXPANDED_FLOW), ("search", BASIC_FLOW)],
    ids=["chat", "search"],
)
async def test_evaluation_answer_mode_selects_one_flow(
    monkeypatch: MonkeyPatch, answer_mode: Any, expected_flow: AnswerFlow
) -> None:
    """品質評価の回答の方法（#5340）は 1 つの経路を選び、入口の解決と pipeline に同じ値を渡す。"""
    captured: list[tuple[str, object]] = []

    async def fake_resolve(request: SearchRequest, settings: Any, **options: Any) -> Any:
        captured.append(("resolve", options.get("flow")))
        return request, settings, None, None

    class Pipeline:
        def __init__(self, **kwargs: Any) -> None:
            captured.append(("pipeline", kwargs.get("flow")))

        async def run(self, *_args: Any, **_kwargs: Any) -> SearchResponse:
            return _response()

    async def no_same_text(_ids: Any) -> dict[str, list[str]]:
        return {}

    monkeypatch.setattr(search_route, "_resolve_query_context", fake_resolve)
    monkeypatch.setattr(evaluation_module, "RagPipeline", Pipeline)
    runner = evaluation_module.EvaluationRunner(same_text_documents=no_same_text)
    metrics = await runner.run(
        cases=[EvaluationCase(id="row-2", query=QUESTION)],
        top_k=5,
        search_answer_profile_id="bv-1",
        answer_mode=answer_mode,
    )

    assert metrics.error_count == 0
    assert {flow for _, flow in captured} == {expected_flow}
    assert ("resolve", expected_flow) in captured
    assert ("pipeline", expected_flow) in captured


async def test_search_answer_mode_joins_turns_into_one_question() -> None:
    """RAG 検索の方法（#5340）は往復を持たないので、最初の質問と返答をつないで 1 問で送る。"""
    sent: list[tuple[str, dict[str, str], object]] = []

    class Pipeline:
        async def run(self, request: SearchRequest, *_args: Any, **kwargs: Any) -> SearchResponse:
            sent.append((request.query, dict(request.conditions), kwargs.get("history")))
            return _response()

    case = EvaluationCase(
        id="row-2",
        query=QUESTION,
        conditions={"region": "A"},
        turns=[EvaluationTurn(reply="合成の返答", conditions={"period": "B"})],
    )
    metrics = await evaluation_module.EvaluationRunner(pipeline=Pipeline()).run(
        cases=[case], top_k=5, answer_mode="search"
    )

    assert sent == [(f"{QUESTION}\n合成の返答", {"region": "A", "period": "B"}, None)]
    assert len(metrics.case_results[0].turn_results) == 1

    sent.clear()
    await evaluation_module.EvaluationRunner(pipeline=Pipeline()).run(
        cases=[case], top_k=5, answer_mode="chat"
    )
    assert [query for query, _, _ in sent] == [QUESTION, "合成の返答"]


async def test_basic_flow_entry_does_not_match_business_guides(monkeypatch: MonkeyPatch) -> None:
    order: list[str] = []
    _install(monkeypatch, order)
    request = SearchRequest(query=QUESTION, search_answer_profile_id="bv-1")

    _, basic_settings, _, applied = await search_route._resolve_query_context(
        request, get_settings(), flow=BASIC_FLOW
    )
    assert applied == "bv-1"
    assert order == []
    assert not basic_settings.rag_support_guide

    await search_route._resolve_query_context(request, get_settings(), flow=EXPANDED_FLOW)
    assert order == [f"guide:{QUESTION}"]


async def test_basic_flow_entry_rejects_a_chosen_approved_faq(monkeypatch: MonkeyPatch) -> None:
    order: list[str] = []
    _install(monkeypatch, order)
    looked_up: list[str] = []

    async def fake_find(*_args: Any) -> Any:
        looked_up.append("faq")
        return None

    monkeypatch.setattr(search_route, "find_approved_faq", fake_find)
    request = SearchRequest(
        query=QUESTION, search_answer_profile_id="bv-1", approved_faq_id="faq-1"
    )

    with pytest.raises(HTTPException) as raised:
        await search_route._resolve_query_context(request, get_settings(), flow=BASIC_FLOW)

    assert raised.value.status_code == 422
    assert raised.value.detail == search_route.APPROVED_FAQ_BASIC_FLOW_MESSAGE
    assert looked_up == [] and order == []


def _spy(monkeypatch: MonkeyPatch, owner: Any, name: str, calls: list[str]) -> None:
    original: Callable[..., Any] = getattr(owner, name)

    def wrapper(*args: Any, **kwargs: Any) -> Any:
        calls.append(name if kwargs.get("rerank_phase", "main_search") == "main_search" else "")
        return original(*args, **kwargs)

    async def async_wrapper(self: Any, *args: Any, **kwargs: Any) -> Any:
        calls.append(name)
        return await original(self, *args, **kwargs)

    import inspect

    monkeypatch.setattr(
        owner, name, async_wrapper if inspect.iscoroutinefunction(original) else wrapper
    )


@pytest.mark.parametrize(
    ("flow", "expanded"),
    [(BASIC_FLOW, False), (EXPANDED_FLOW, True)],
    ids=["basic", "expanded"],
)
async def test_pipeline_calls_the_expansion_only_in_the_expanded_flow(
    monkeypatch: MonkeyPatch, flow: AnswerFlow, expanded: bool
) -> None:
    """⑤⑥（主の検索と文脈の補完）と ⑧（回答）は両方の経路で同じ関数、⑦ は拡張の経路だけ。"""
    calls: list[str] = []
    _spy(monkeypatch, answering, "build_adb_hybrid_answer_context", calls)
    _spy(monkeypatch, answering, "_expand_evidence", calls)
    _spy(monkeypatch, answering, "_answer_from_evidence", calls)
    _spy(monkeypatch, AnswerEngine, "_search_expansions", calls)

    response, steps, _llm, _oracle = await _run_with_progress(
        monkeypatch, SearchRequest(query=QUESTION), flow=flow
    )

    called = [name for name in calls if name]
    assert called.count("build_adb_hybrid_answer_context") == 1
    assert called.count("_answer_from_evidence") == 1
    assert ("_expand_evidence" in called) is expanded
    assert ("_search_expansions" in called) is expanded
    started = {stage for stage, outcome in steps if outcome == "started"}
    assert ("answer_step:章節の拡張" in started) is expanded
    assert response.answer
