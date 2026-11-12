"""独立検索の範囲・軽量経路・章節読取・原文だけの境界を確認する。"""

import json
from types import SimpleNamespace
from typing import Any, cast

import pytest

from app.config import Settings
from app.rag.answer_engine import AnswerEngine, _SearchState
from app.rag.wiki_models import WikiContext, WikiDependency, WikiPage
from app.rag.wiki_retrieval import WikiRetriever, WikiSourceChanged
from app.schemas.search import RetrievedChunk, SearchRequest
from tests.test_answer_engine import FakeGenAi, FakeOracle, _chunk
from tests.test_wiki_compiler import units


class Store:
    def __init__(self) -> None:
        self.ready = True
        self.reads: list[Any] = []
        self.pages = [
            WikiPage(
                unit=unit,
                navigation=WikiContext(
                    unit_id=unit.unit_id,
                    context="原文ではない案内。",
                    possible_questions=[],
                    unresolved=["構築時だけの未確認事項。"],
                    dependencies=[WikiDependency(unit_id="unit-1", relation="warning")]
                    if unit.unit_id == "unit-0"
                    else [],
                ),
                input_fingerprint="input-a",
            )
            for unit in units()
        ]

    async def catalog(self, filters: Any, compiler: str) -> Any:
        assert filters["knowledge_base_id"] == "kb-a"
        return [{"document_id": "document-a", "title": "架空の資料"}] if self.ready else []

    async def available_pages(self, filters: Any, **kwargs: Any) -> Any:
        if kwargs.get("query_vector"):
            return self.pages[:1]
        if "unit_ids" in kwargs:
            return [p for p in self.pages if p.unit.unit_id in kwargs["unit_ids"]]
        return self.pages

    async def text_pages(self, question: str, filters: Any, **kwargs: Any) -> Any:
        return []

    async def read_sources(self, pages: Any, filters: Any) -> Any:
        self.reads.append(filters)
        return (
            [
                RetrievedChunk(
                    chunk_id=p.unit.source_chunk_ids[0],
                    document_id=p.unit.document_id,
                    text=p.unit.text,
                    score=0.0,
                    file_name="synthetic.txt",
                    metadata={"chunk_set_id": p.unit.chunk_set_id},
                )
                for p in pages
            ]
            if self.ready
            else []
        )


class Oracle:
    enabled = True

    async def get_search_answer_profile(self, pid: str) -> Any:
        return SimpleNamespace(
            status="ACTIVE",
            config=SimpleNamespace(query=SimpleNamespace(wiki_search_enabled=self.enabled)),
            knowledge_bases=[SimpleNamespace(id="kb-a", status="ACTIVE")],
        )


class Genai:
    async def embed(self, texts: Any, **kwargs: Any) -> Any:
        return [[1.0, 0.0]]


class Model:
    def __init__(self) -> None:
        self.calls: list[Any] = []

    async def generate(self, prompt: str, context: str, **kwargs: Any) -> str:
        data = json.loads(context)
        self.calls.append(data)
        if "files" in data:
            return json.dumps(
                {"document_ids": ["document-a"], "search_expressions": [], "unresolved": []}
            )
        return json.dumps(
            {"unit_ids": [], "section_ids": [data["sections"][0]["section_id"]], "unresolved": []}
        )


def retriever() -> Any:
    store, oracle, model = Store(), Oracle(), Model()
    search = WikiRetriever(
        Settings(rag_wiki_search_enabled=True),
        cast(Any, oracle),
        cast(Any, Genai()),
        store=cast(Any, store),
        model=model,
    )
    return search, store, oracle, model


@pytest.mark.asyncio
async def test_light_path_reads_original_dependencies_without_generation() -> None:
    search, store, _, model = retriever()
    result = await search.search(
        "架空の質問",
        profile_id="profile-a",
        filters={"knowledge_base_id": "kb-a,other"},
        allow_planning=False,
    )
    assert {c.chunk_id for c in result.chunks} == {"chunk-0", "chunk-1"}
    assert result.trace["model_calls"] == 0 and not model.calls
    assert all("案内" not in c.text and "未確認" not in c.text for c in result.chunks)
    assert all(f["knowledge_base_id"] == "kb-a" for f in store.reads)


@pytest.mark.asyncio
async def test_quality_path_locates_file_then_reads_full_section() -> None:
    search, store, _, model = retriever()
    result = await search.search(
        "版と前提を含む架空の質問", profile_id="profile-a", filters={}, allow_planning=True
    )
    assert len(result.chunks) == 4 and len(model.calls) == 2
    assert model.calls[0]["question"] == model.calls[1]["question"]
    assert "units" not in model.calls[0] and "files" not in model.calls[1]
    assert [c.text for c in result.chunks] == [p.unit.text for p in store.pages]


@pytest.mark.asyncio
async def test_disabled_and_not_ready_do_not_call_models() -> None:
    search, store, oracle, model = retriever()
    oracle.enabled = False
    result = await search.search("質問", profile_id="profile-a", filters={}, allow_planning=True)
    assert result.trace["status"] == "profile_disabled" and not store.reads
    oracle.enabled, store.ready = True, False
    result = await search.search("質問", profile_id="profile-a", filters={}, allow_planning=True)
    assert result.trace["status"] == "not_ready" and not model.calls


@pytest.mark.asyncio
async def test_source_or_profile_change_invalidates_evidence() -> None:
    search, store, oracle, _ = retriever()
    store.ready = False
    with pytest.raises(WikiSourceChanged):
        await search.assert_current("profile-a", {}, store.pages)
    store.ready, oracle.enabled = True, False
    with pytest.raises(WikiSourceChanged):
        await search.assert_current("profile-a", {}, store.pages)


@pytest.mark.asyncio
async def test_auxiliary_failure_leaves_native_state_intact(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fail(*args: Any, **kwargs: Any) -> Any:
        raise TimeoutError("非公開の本文は記録しない")

    monkeypatch.setattr(WikiRetriever, "search", fail)
    engine: Any = AnswerEngine.__new__(AnswerEngine)
    engine._settings, engine._oracle, engine._genai = Settings(), Oracle(), Genai()
    engine._caller_queries = None
    state = _SearchState()
    result = await engine._wiki_sources(
        SearchRequest(query="架空の質問"), state, allow_planning=True
    )
    assert result == [] and state.wiki_trace == {
        "status": "degraded",
        "failure_code": "TimeoutError",
    }


@pytest.mark.asyncio
async def test_product_evidence_flow_unions_originals_without_calling_a_planner(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.rag.wiki_retrieval import WikiSearchResult

    calls = []
    extra = _chunk("wiki-only", "架空の追加原文。", 10)
    extra = extra.model_copy(update={"metadata": {**extra.metadata, "wiki_source": True}})

    async def search(self: Any, question: str, **options: Any) -> Any:
        calls.append(options)
        return WikiSearchResult(chunks=(extra,), trace={"status": "ready", "model_calls": 0})

    monkeypatch.setattr(WikiRetriever, "search", search)
    engine = AnswerEngine(
        Settings(rag_wiki_search_enabled=True),
        oracle=cast(Any, FakeOracle()),
        genai=cast(Any, FakeGenAi()),
        complete_evidence=False,
    )
    outcome = await engine.run(SearchRequest(query="架空の操作を確認する", generate_answer=False))
    assert "wiki-only" in {c.chunk_id for c in outcome.citations}, (calls, outcome.diagnostics)
    assert len(outcome.citations) > 1
    assert outcome.diagnostics["wiki"]["model_calls"] == 0
    assert calls[0]["allow_planning"] is False
