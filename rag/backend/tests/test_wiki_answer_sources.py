"""回答の中の Wiki の検索の経路（#4941）: 足す原文の形・失敗の縮退・期限。外部の呼出しは代役。"""

from __future__ import annotations

from typing import Any, cast

import pytest
from rag_engine.resources.deadline import Deadline

from app.config import Settings
from app.rag.answer_engine import AnswerEngine, _SearchState
from app.rag.wiki_retrieval import WikiSearchResult
from app.schemas.search import RetrievedChunk, SearchRequest

IDENTITY = {"source_key": "S-1", "revision_key": "R-1"}
RELATION = {
    "relation": "exception",
    "role_of": "this",
    "document_name": "合成B.md",
    "section_path": ["章1"],
}


def engine(retriever: Any) -> AnswerEngine:
    value = AnswerEngine(
        Settings(rag_wiki_search_enabled=True),
        oracle=cast(Any, object()),
        genai=cast(Any, object()),
    )
    value._wiki_retriever = lambda: retriever  # type: ignore[method-assign]
    return value


class Retriever:
    def __init__(self, error: Exception | None = None, *, disabled: bool = False) -> None:
        self.error = error
        self.disabled = disabled
        self.calls: list[dict[str, Any]] = []

    async def search(self, question: str, **kwargs: Any) -> WikiSearchResult:
        self.calls.append(kwargs)
        if self.disabled:
            return WikiSearchResult(trace={"status": "profile_disabled", "model_calls": 0})
        # 検索の経路は段（span）ごとの秒を渡された記録に書く（#5518）。
        kwargs["stage_seconds"]["Wiki: 目録の読み込み"] = 0.25
        if self.error:
            raise self.error
        chunk = RetrievedChunk(
            chunk_id="c1",
            document_id="doc",
            file_name="合成.md",
            text="合成の原文。",
            score=0.0,
            metadata={
                "chunk_set_id": "cs",
                "wiki_source": True,
                "source_identities": [IDENTITY],
                "wiki_relations": [RELATION],
            },
        )
        return WikiSearchResult((chunk,), ("page",), {"status": "ready"})  # type: ignore[arg-type]


def request() -> SearchRequest:
    return SearchRequest(query="合成の質問", search_answer_profile_id="p")


@pytest.mark.asyncio
async def test_wiki_sources_keep_the_version_attributes_and_mark_the_channel() -> None:
    state = _SearchState()
    stored = await engine(Retriever())._wiki_sources(request(), state)
    assert [chunk.chunk_id for chunk in stored] == ["c1"]
    metadata = stored[0].metadata
    assert metadata["wiki_source"] is True and metadata["source_identities"] == [IDENTITY]
    assert metadata["wiki_relations"] == [RELATION]
    assert metadata["adb_hybrid"] == {"retrieval_channels": ["wiki"]}
    assert state.wiki_pages == ("page",) and "c1" in state.chunks
    # 検索の経路の記録に、全体の秒と段ごとの秒を足す（#5518）。
    elapsed = state.wiki_trace.pop("elapsed_seconds")
    assert isinstance(elapsed, float) and elapsed >= 0
    assert state.wiki_trace == {
        "status": "ready",
        "stage_seconds": {"Wiki: 目録の読み込み": 0.25},
    }


@pytest.mark.asyncio
async def test_wiki_failure_is_recorded_and_normal_search_continues() -> None:
    state = _SearchState()
    stored = await engine(Retriever(RuntimeError("合成の失敗")))._wiki_sources(request(), state)
    assert stored == [] and state.wiki_pages == ()
    # 失敗しても、それまでの段の秒と全体の秒を残す（#5518）。
    assert isinstance(state.wiki_trace.pop("elapsed_seconds"), float)
    assert state.wiki_trace == {
        "status": "degraded",
        "failure_code": "RuntimeError",
        "failure_reason": "unknown",
        "stage_seconds": {"Wiki: 目録の読み込み": 0.25},
    }
    assert [step["kind"] for step in state.degraded_steps()] == ["wiki"]


@pytest.mark.asyncio
async def test_profile_without_wiki_keeps_the_trace_without_measurements() -> None:
    """Wiki を使わないプロファイルは検索していないので、秒を足さない（#5518）。"""
    state = _SearchState()
    stored = await engine(Retriever(disabled=True))._wiki_sources(request(), state)
    assert stored == []
    assert state.wiki_trace == {"status": "profile_disabled", "model_calls": 0}


@pytest.mark.asyncio
async def test_wiki_is_not_started_when_the_deadline_only_leaves_the_generation() -> None:
    retriever = Retriever()
    state = _SearchState()
    state.deadline = Deadline(1.0)
    stored = await engine(retriever)._wiki_sources(request(), state)
    assert stored == [] and retriever.calls == []
    assert state.wiki_trace["failure_code"] == "TimeoutError"
    assert state.wiki_trace["failure_reason"] == "timeout"


def test_wiki_runs_alongside_the_answer_flow_and_is_awaited_or_cancelled() -> None:
    """Wiki の経路は回答の始めに始まり、合わせ込みで待つ。待たずに終われば止める（#5437）。"""
    import asyncio
    import threading

    from app.rag.answer_engine import _start_on_loop, _wait_on_loop

    loop = asyncio.new_event_loop()
    worker = threading.Thread(target=loop.run_forever, daemon=True)
    worker.start()
    try:
        started = threading.Event()

        async def wiki() -> str:
            started.set()
            await asyncio.sleep(0.05)
            return "原文"

        future = _start_on_loop(wiki, loop)
        # 待つ前に始まっている（通常検索の間に進む）。
        assert started.wait(2.0)
        assert _wait_on_loop(future) == "原文"

        blocked = threading.Event()

        async def slow() -> None:
            blocked.set()
            await asyncio.sleep(30)

        pending = _start_on_loop(slow, loop)
        assert blocked.wait(2.0)
        pending.cancel()
        assert pending.cancelled()
        # loop に取り消しを処理させてから止める。
        asyncio.run_coroutine_threadsafe(asyncio.sleep(0.05), loop).result(2.0)
    finally:
        loop.call_soon_threadsafe(loop.stop)
        worker.join(2.0)
