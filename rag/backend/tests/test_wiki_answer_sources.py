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
            return WikiSearchResult(trace={"status": "profile_disabled"})
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
                "wiki_origin": "root",
                "wiki_rank": 3,
                "wiki_rerank_score": 0.8,
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
    retriever = Retriever()
    stored = await engine(retriever)._wiki_sources(request(), state)
    assert [chunk.chunk_id for chunk in stored] == ["c1"]
    # 起点数は通常検索と同じ定義（要求の top_k）。
    assert retriever.calls[0]["top_k"] == request().top_k
    metadata = stored[0].metadata
    assert metadata["wiki_source"] is True and metadata["source_identities"] == [IDENTITY]
    assert metadata["wiki_relations"] == [RELATION]
    assert metadata["adb_hybrid"] == {"retrieval_channels": ["wiki"]}
    # 起点の由来・Wiki のベクトル検索の順位と、原文で測った関連度を rag_engine へ渡す（#5831）。
    # rerank の順位は、通常検索の候補と合わせるときに rag_engine が決める。
    assert (metadata["wiki_origin"], metadata["wiki_rank"]) == ("root", 3)
    assert metadata["rerank"]["relevance_score"] == 0.8 and "rank" not in metadata["rerank"]
    assert state.wiki_pages == ("page",) and "c1" in state.chunks
    # Wiki だけが見つけた原文は chunk をそのまま入れ、印は別にも持つ（#5831）。
    assert state.wiki_only_chunks == {"c1"} and state.wiki_marks == {"c1": WIKI_MARKS}
    # 検索の経路の記録に、全体の秒と段ごとの秒を足す（#5518）。
    elapsed = state.wiki_trace.pop("elapsed_seconds")
    assert isinstance(elapsed, float) and elapsed >= 0
    assert state.wiki_trace == {
        "status": "ready",
        "stage_seconds": {"Wiki: 目録の読み込み": 0.25},
    }


def search_hit() -> RetrievedChunk:
    """通常検索で当たった同じ原文（ベクトル・全文の順位を持ち、Wiki の印は無い）。"""
    return RetrievedChunk(
        chunk_id="c1",
        document_id="doc",
        file_name="合成.md",
        text="合成の原文。",
        score=0.5,
        metadata={"chunk_set_id": "cs", "vector_rank": 3, "keyword_rank": 1},
    )


WIKI_MARKS = {
    "wiki_source": True,
    "wiki_origin": "root",
    "wiki_rank": 3,
    "source_identities": [IDENTITY],
    "wiki_relations": [RELATION],
}


@pytest.mark.asyncio
@pytest.mark.parametrize("wiki_first", [False, True], ids=["search_first", "wiki_first"])
async def test_same_original_keeps_the_search_ranks_and_the_wiki_marks(wiki_first: bool) -> None:
    """通常検索と Wiki が同じ原文を見つけたら、どちらが先でも両方の経路の値を残す（#5831）。"""
    state = _SearchState()
    hit = search_hit()
    if wiki_first:
        await engine(Retriever())._wiki_sources(request(), state)
        assert state.wiki_only_chunks == {"c1"} and "vector_rank" not in state.chunks["c1"].metadata
        state.add_search_hit(hit)
    else:
        state.add_search_hit(hit)
        await engine(Retriever())._wiki_sources(request(), state)
    # chunk は通常検索の結果（Wiki の印を持たない）。Wiki の印は別に持つ。
    assert state.chunks["c1"] is hit and state.wiki_only_chunks == set()
    assert state.wiki_marks == {"c1": WIKI_MARKS}
    # 通常検索の結果に置き換えた後は、先に入れたものを残す（他の検索と同じ）。
    state.add_search_hit(search_hit())
    assert state.chunks["c1"] is hit


class EngineResult:
    """rag_engine の回答の結果（``_outcome_from_result`` が読む属性だけ）。"""

    def __init__(self, children: list[dict[str, Any]]) -> None:
        self.evidence_items = [{"chunk_id": "p", "children": children}]
        self.generation_trace: dict[str, Any] = {}
        self.execution_steps: list[Any] = []
        self.generated_queries: list[str] = []
        self.text_search_tokens: list[str] = []
        self.crag_attempts: list[Any] = []
        self.envelope: dict[str, Any] = {}
        self.external_data_items: list[Any] = []
        self.question_type: list[str] = []
        self.answer_text = "回答"

    def __getattr__(self, name: str) -> Any:
        return None


@pytest.mark.asyncio
@pytest.mark.parametrize("wiki_first", [False, True], ids=["search_first", "wiki_first"])
async def test_citation_carries_the_ranks_of_both_routes(wiki_first: bool) -> None:
    """引用の metadata に、通常検索の順位と Wiki の由来・順位、合わせた rerank の順位を出す。

    保存の形（回答の記録・会話の回答）にも残す（#5831）。
    """
    from app.rag.answer_engine import _outcome_from_result
    from app.rag.stored_answer import stored_citation_metadata

    state = _SearchState()
    if not wiki_first:
        state.add_search_hit(search_hit())
    await engine(Retriever())._wiki_sources(request(), state)
    if wiki_first:
        state.add_search_hit(search_hit())
    # Wiki だけが見つけた原文（関係を辿った先。順位は無い）。
    state.add_wiki_chunk(
        RetrievedChunk(
            chunk_id="c2",
            document_id="doc",
            text="合成の関連の原文。",
            score=0.0,
            metadata={"wiki_source": True, "wiki_origin": "dependency"},
        )
    )
    outcome = _outcome_from_result(
        EngineResult(
            [
                {"chunk_id": "c1", "is_model_used": True, "rerank_score": 0.8, "rerank_rank": 1},
                {"chunk_id": "c2", "is_model_used": False},
            ]
        ),
        state,
    )
    both, followed = outcome.citations
    assert {
        key: both.metadata.get(key)
        for key in ("vector_rank", "keyword_rank", "wiki_origin", "wiki_rank", "rerank_rank")
    } == {
        "vector_rank": 3,
        "keyword_rank": 1,
        "wiki_origin": "root",
        "wiki_rank": 3,
        "rerank_rank": 1,
    }
    assert both.metadata["wiki_relations"] == [RELATION]
    assert followed.metadata["wiki_origin"] == "dependency"
    assert "wiki_rank" not in followed.metadata
    assert "vector_rank" not in followed.metadata
    stored = stored_citation_metadata(both.metadata)
    assert (stored["wiki_origin"], stored["wiki_rank"]) == ("root", 3)
    assert stored_citation_metadata(followed.metadata)["wiki_origin"] == "dependency"


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
    assert state.wiki_trace == {"status": "profile_disabled"}


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


# ---- Wiki 検索で選んだ原文だけで答える（#5981） ----


def _selected(only: bool) -> AnswerEngine:
    from app.rag.wiki_explorer import WikiHandoff

    chunk = RetrievedChunk(
        chunk_id="c1", document_id="doc", file_name="合成.md", text="合成の原文。", score=0.0
    )
    # oracle・genai は属性を持たない代役（触れば AttributeError で落ちる）。
    return AnswerEngine(
        Settings(rag_wiki_search_enabled=True),
        oracle=cast(Any, object()),
        genai=cast(Any, object()),
        wiki_handoff=WikiHandoff("p", (), (chunk,), only=only),
    )


@pytest.mark.asyncio
async def test_selected_only_answers_without_search_or_expansion() -> None:
    value = _selected(only=True)
    # 根拠の拡張（検索拡張・自動の Wiki 検索を含む）をしない。
    assert value._selected_only and not value._expand
    # 検索は DB・埋め込みを呼ばずに空を返す（原文は additional_sources で足す）。
    found = await value._search(request(), _SearchState(), retrieval_queries=["合成の質問"])
    assert found.child_chunks == [] and found.all_chunks == []


def test_selected_sources_with_search_keep_the_expanded_flow() -> None:
    value = _selected(only=False)
    assert not value._selected_only and value._expand


def test_selection_over_the_limit_is_rejected() -> None:
    from fastapi import HTTPException

    from app.rag.wiki_explorer import require_selection_within_limit

    settings = Settings(rag_wiki_selection_max_units=3)
    require_selection_within_limit(3, settings)
    with pytest.raises(HTTPException) as raised:
        require_selection_within_limit(4, settings)
    assert raised.value.status_code == 422 and "3 件まで" in str(raised.value.detail)


@pytest.mark.asyncio
@pytest.mark.parametrize("only", [True, False])
async def test_selected_only_does_not_add_the_first_chunk_of_the_document(
    monkeypatch: pytest.MonkeyPatch, only: bool
) -> None:
    """文書の背景の先頭の chunk は、選んだ原文だけで答えるときは根拠に足さない（#5981）。"""
    import app.rag.document_background as background

    first = RetrievedChunk(
        chunk_id="first", document_id="doc", file_name="合成.md", text="合成の先頭。", score=0.0
    )

    async def rows(*args: Any, **kwargs: Any) -> list[dict[str, Any]]:
        return [{"document_id": "doc", "first_chunk": first}]

    monkeypatch.setattr(background, "selected_document_backgrounds", rows)
    found = await _selected(only=only)._selected_document_backgrounds(request(), _SearchState(), [])
    assert found[0]["document_id"] == "doc"
    assert (found[0]["first_chunk"] is None) is only
