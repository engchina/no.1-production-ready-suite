"""独立検索の範囲・軽量経路・章節読取・原文だけの境界を確認する。"""

import json
from types import SimpleNamespace
from typing import Any, cast

import pytest
from pr_backend_core.observability.model_purpose import model_purpose_attributes

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

    async def text_page_rankings(self, question: str, filters: Any, **kwargs: Any) -> Any:
        return {}

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
        assert model_purpose_attributes() == {"pr.gen_ai.purpose": "document.read"}
        data = json.loads(context)
        self.calls.append(data)
        if "files" in data:
            return json.dumps(
                {
                    "document_ids": [data["files"][0]["document_id"]],
                    "search_expressions": [],
                    "unresolved": [],
                }
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
async def test_read_planner_retains_selected_document_identity_metadata() -> None:
    search, store, _, model = retriever()
    identities = [
        {
            "source_key": "synthetic-source",
            "revision_key": "synthetic-v2",
            "effective_date": "2025-01-01",
            "source_status": "active",
        }
    ]

    async def catalog(filters: Any, compiler: str) -> Any:
        return [{"document_id": "document-a", "title": "合成資料", "source_identities": identities}]

    store.catalog = catalog
    await search.search(
        "合成資料の版を比較する質問", profile_id="profile-a", filters={}, allow_planning=True
    )
    documents = model.calls[1]["documents"]
    assert documents[0]["source_identities"] == identities
    assert documents[0]["title"] == "合成資料"
    assert documents[0]["document_id"] == model.calls[1]["units"][0]["document_id"]


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


@pytest.mark.asyncio
@pytest.mark.parametrize("fail_second", [False, True])
async def test_file_rankings_are_batched_without_reordering(
    fail_second: bool,
) -> None:
    class ConcurrentStore(Store):
        def __init__(self) -> None:
            super().__init__()
            self.pages += [
                page.model_copy(
                    update={
                        "unit": page.unit.model_copy(
                            update={
                                "unit_id": "b-" + page.unit.unit_id,
                                "document_id": "document-b",
                                "source_chunk_ids": [
                                    "b-" + uid for uid in page.unit.source_chunk_ids
                                ],
                            }
                        )
                    }
                )
                for page in self.pages
            ]
            self.queries: list[tuple[str, list[str]]] = []

        async def catalog(self, filters: Any, compiler: str) -> Any:
            return [
                {"document_id": did, "title": "合成資料"} for did in ["document-a", "document-b"]
            ]

        async def text_page_rankings(self, question: str, filters: Any, **kwargs: Any) -> Any:
            self.queries.append((question, kwargs["document_ids"]))
            if fail_second and len(self.queries) == 2:
                raise RuntimeError("合成の読み取り失敗")
            return {
                did: [p.unit.unit_id for p in reversed(self.pages) if p.unit.document_id == did]
                for did in kwargs["document_ids"]
            }

    class TwoFiles(Model):
        async def generate(self, prompt: str, context: str, **kwargs: Any) -> str:
            data = json.loads(context)
            if "files" in data:
                self.calls.append(data)
                return json.dumps(
                    {
                        "document_ids": [row["document_id"] for row in data["files"]],
                        "search_expressions": ["合成の表現A", "合成の表現B"],
                        "unresolved": [],
                    }
                )
            return await super().generate(prompt, context, **kwargs)

    store, model = ConcurrentStore(), TwoFiles()
    search = WikiRetriever(
        Settings(rag_wiki_search_enabled=True),
        cast(Any, Oracle()),
        cast(Any, Genai()),
        store=cast(Any, store),
        model=model,
    )
    if fail_second:
        with pytest.raises(RuntimeError, match="合成の読み取り失敗"):
            await search.search(
                "合成の質問", profile_id="profile-a", filters={}, allow_planning=True
            )
        assert 2 <= len(store.queries) <= 3 and len(model.calls) == 1
    else:
        result = await search.search(
            "合成の質問", profile_id="profile-a", filters={}, allow_planning=True
        )
        assert result.trace["status"] == "ready" and len(model.calls) == 2
        assert store.queries == [
            (text, ["document-a", "document-b"])
            for text in ["合成の質問", "合成の表現A", "合成の表現B"]
        ]
        from app.rag.wiki_retrieval import interleave

        groups = [
            interleave(
                [
                    store.pages[:1] if did == "document-a" else [],
                    [p for p in reversed(store.pages) if p.unit.document_id == did],
                    [p for p in store.pages if p.unit.document_id == did],
                ]
            )
            for did in ["document-a", "document-b"]
        ]
        assert [unit["path"] for unit in model.calls[1]["units"]] == [
            page.unit.section_path for page in interleave(groups)
        ]


@pytest.mark.asyncio
@pytest.mark.parametrize("changed", ["source", "profile"])
async def test_reused_pages_are_revalidated_after_ranking(changed: str) -> None:
    search, store, oracle, model = retriever()

    async def rank(*args: Any, **kwargs: Any) -> Any:
        if changed == "source":
            store.ready = False
        else:
            oracle.enabled = False
        return {}

    store.text_page_rankings = rank
    with pytest.raises(WikiSourceChanged):
        await search.search("合成の質問", profile_id="profile-a", filters={}, allow_planning=True)
    assert len(model.calls) == 1


@pytest.mark.asyncio
async def test_cancelled_batched_ranking_does_not_start_read_planning() -> None:
    import asyncio

    search, store, _, model = retriever()
    started, cancelled = asyncio.Event(), asyncio.Event()

    async def rank(*args: Any, **kwargs: Any) -> Any:
        started.set()
        try:
            await asyncio.Event().wait()
        finally:
            cancelled.set()

    store.text_page_rankings = rank
    task = asyncio.create_task(
        search.search("合成の質問", profile_id="profile-a", filters={}, allow_planning=True)
    )
    await asyncio.wait_for(started.wait(), timeout=1)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert cancelled.is_set() and len(model.calls) == 1


@pytest.mark.asyncio
async def test_read_planner_cannot_drop_every_candidate_from_a_located_file() -> None:
    search, store, _, model = retriever()
    page = store.pages[-1]
    store.pages[-1] = page.model_copy(
        update={"unit": page.unit.model_copy(update={"document_id": "document-b"})}
    )

    async def catalog(filters: Any, compiler: str) -> Any:
        return [{"document_id": did, "title": "合成の資料"} for did in ["document-a", "document-b"]]

    async def omit_all(prompt: str, context: str, **kwargs: Any) -> str:
        data = json.loads(context)
        if "files" in data:
            return json.dumps(
                {
                    "document_ids": [row["document_id"] for row in data["files"]],
                    "search_expressions": [],
                    "unresolved": [],
                }
            )
        return json.dumps({"unit_ids": [], "section_ids": [], "unresolved": []})

    store.catalog = catalog
    model.generate = omit_all
    result = await search.search(
        "合成の質問", profile_id="profile-a", filters={}, allow_planning=True
    )
    assert {c.document_id for c in result.chunks} == {"document-a", "document-b"}
    assert "chunk-3" in {c.chunk_id for c in result.chunks}
    assert result.trace["selected_units"] == 2


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [401, 429, 503])
async def test_planning_retry_is_transient_only_and_shared_across_steps(status: int) -> None:
    import httpx

    search, _, _, model = retriever()
    search.settings = search.settings.model_copy(update={"rag_llm_max_retries": 1})
    original = model.generate
    calls = 0

    async def flaky(prompt: str, context: str, **kwargs: Any) -> str:
        nonlocal calls
        calls += 1
        data = json.loads(context)
        if calls == 1 or "units" in data:
            request = httpx.Request("POST", "https://example.invalid/responses")
            response = httpx.Response(status, request=request)
            raise httpx.HTTPStatusError("合成の通信失敗", request=request, response=response)
        result = await original(prompt, context, **kwargs)
        assert isinstance(result, str)
        return result

    model.generate = flaky
    with pytest.raises(httpx.HTTPStatusError):
        await search.search("合成の質問", profile_id="profile-a", filters={}, allow_planning=True)
    assert calls == (1 if status == 401 else 3)


@pytest.mark.asyncio
async def test_planning_retry_recovers_with_the_exact_same_scoped_input() -> None:
    import httpx

    search, _, _, model = retriever()
    original = model.generate
    attempts = []

    async def flaky(prompt: str, context: str, **kwargs: Any) -> str:
        attempts.append((prompt, context, kwargs))
        if len(attempts) == 1:
            request = httpx.Request("POST", "https://example.invalid/responses")
            raise httpx.HTTPStatusError(
                "合成の一時障害", request=request, response=httpx.Response(503, request=request)
            )
        result = await original(prompt, context, **kwargs)
        assert isinstance(result, str)
        return result

    model.generate = flaky
    result = await search.search(
        "合成の質問", profile_id="profile-a", filters={}, allow_planning=True
    )
    assert attempts[0] == attempts[1]
    assert result.trace["planning_retry_count"] == 1 and result.trace["model_calls"] == 3
    assert len(result.chunks) == 4
