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


def engine(retriever: Any) -> AnswerEngine:
    value = AnswerEngine(
        Settings(rag_wiki_search_enabled=True),
        oracle=cast(Any, object()),
        genai=cast(Any, object()),
    )
    value._wiki_retriever = lambda: retriever  # type: ignore[method-assign]
    return value


class Retriever:
    def __init__(self, error: Exception | None = None) -> None:
        self.error = error
        self.calls: list[dict[str, Any]] = []

    async def search(self, question: str, **kwargs: Any) -> WikiSearchResult:
        self.calls.append(kwargs)
        if self.error:
            raise self.error
        chunk = RetrievedChunk(
            chunk_id="c1",
            document_id="doc",
            file_name="合成.md",
            text="合成の原文。",
            score=0.0,
            metadata={"chunk_set_id": "cs", "wiki_source": True, "source_identities": [IDENTITY]},
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
    assert metadata["adb_hybrid"] == {"retrieval_channels": ["wiki"]}
    assert (
        state.wiki_pages == ("page",)
        and state.wiki_trace == {"status": "ready"}
        and "c1" in state.chunks
    )


@pytest.mark.asyncio
async def test_wiki_failure_is_recorded_and_normal_search_continues() -> None:
    state = _SearchState()
    stored = await engine(Retriever(RuntimeError("合成の失敗")))._wiki_sources(request(), state)
    assert stored == [] and state.wiki_pages == ()
    assert state.wiki_trace == {"status": "degraded", "failure_code": "RuntimeError"}
    assert [step["kind"] for step in state.degraded_steps()] == ["wiki"]


@pytest.mark.asyncio
async def test_wiki_is_not_started_when_the_deadline_only_leaves_the_generation() -> None:
    retriever = Retriever()
    state = _SearchState()
    state.deadline = Deadline(1.0)
    stored = await engine(retriever)._wiki_sources(request(), state)
    assert stored == [] and retriever.calls == []
    assert state.wiki_trace["failure_code"] == "TimeoutError"
