"""主検索は 1 回: ベクトル検索は原質問、全文検索は原質問に語を足した文(#5159)。

- embedding は原質問の 1 回だけ。
- 原生方式は 1 回の hybrid の検索(``DBMS_HYBRID_VECTOR.SEARCH`` に原質問の embedding と
  語を足した文の式)。
- 個別 SQL 方式はベクトルの SQL(原質問)と CONTAINS の SQL(語を足した文の式)を 1 本ずつ。
- 足した語は全文検索の語の上限で優先する(#2163)。
"""

from types import SimpleNamespace
from typing import Any

import pytest
from rag_engine.resources.tracing import bind_trace_observer

from app.clients import oracle as oracle_module
from app.clients.oracle import OracleClient
from app.config import Settings
from app.rag.answer_engine import AnswerEngine, _SearchState
from app.schemas.search import RetrievedChunk, SearchMode, SearchRequest

ORIGINAL = "合成の質問"
EXPANDED = "合成の質問 合成語 別名甲"


def _hit(chunk_id: str, mode: str) -> RetrievedChunk:
    return RetrievedChunk(
        document_id="doc-1",
        chunk_id=chunk_id,
        text=chunk_id,
        score=1.0,
        metadata={"chunk_index": int(chunk_id.rsplit("-", 1)[1]), "retrieval_mode": mode},
    )


class _RecordingOracle:
    """``search_many`` に渡された検索と、そのときの全文検索の優先語を記録する Oracle の代役。"""

    def __init__(self) -> None:
        self.searches: list[tuple[str, list[float], SearchMode]] = []
        self.keywords: list[dict[str, Any]] = []

    async def search_many(
        self,
        searches: list[tuple[str, list[float], SearchMode]],
        top_k: int,
        filters: dict[str, str] | None = None,
        reuse: Any = None,
    ) -> list[list[RetrievedChunk]]:
        self.searches.extend(searches)
        self.keywords.append(dict(oracle_module._TEXT_QUERY_KEYWORDS.get() or {}))
        return [
            [_hit("c-1" if mode != SearchMode.KEYWORD else "c-2", mode.value)]
            for _, _, mode in searches
        ]

    async def chunk_set_first_page_contexts(self, chunk_set_ids: list[str]) -> dict[str, Any]:
        return {}

    async def context_group_siblings(self, anchors: list[RetrievedChunk], **_: Any) -> list[Any]:
        return []


class _RecordingGenAi:
    def __init__(self) -> None:
        self.embedded: list[list[str]] = []

    async def embed(
        self, texts: list[str], *, input_type: str = "SEARCH_DOCUMENT"
    ) -> list[list[float]]:
        self.embedded.append(list(texts))
        return [[float(len(text))] for text in texts]


class _Observation:
    def __init__(self, records: list[dict[str, Any]]) -> None:
        self._records = records

    def request(self, value: object, *, schema: object = None) -> None:
        pass

    def response(self, value: object) -> None:
        if isinstance(value, dict) and "lists" in value:
            self._records.append(value)

    def usage(self, value: object) -> None:
        pass

    def display(self, *args: object, **kwargs: object) -> None:
        pass


async def _main_search(
    backend: str,
) -> tuple[_RecordingOracle, _RecordingGenAi, list[dict[str, Any]]]:
    oracle = _RecordingOracle()
    genai = _RecordingGenAi()
    engine = AnswerEngine(
        Settings(rag_hybrid_search_backend=backend),
        oracle=oracle,  # type: ignore[arg-type]
        genai=genai,  # type: ignore[arg-type]
    )
    settings = SimpleNamespace(
        retrieval_query_sources={ORIGINAL: ("原質問",), EXPANDED: ("原質問 + 用語・同義語",)},
        retrieval_query_keywords={EXPANDED: ("合成語", "別名甲")},
    )
    fusion: list[dict[str, Any]] = []

    def observer(operation: str, name: str) -> Any:
        from contextlib import contextmanager

        @contextmanager
        def scope() -> Any:
            yield _Observation(fusion)

        return scope()

    with bind_trace_observer(observer):
        await engine._search(
            SearchRequest(query=ORIGINAL, filters={"knowledge_base_id": "kb-1"}),
            _SearchState(),
            retrieval_queries=[ORIGINAL],
            text_queries={ORIGINAL: EXPANDED},
            settings=settings,
            candidate_limit=6,
            expand_references=False,
        )
    return oracle, genai, fusion


async def test_native_main_search_is_one_hybrid_search_with_separate_inputs() -> None:
    oracle, genai, fusion = await _main_search("oracle_hybrid_index")

    # embedding は原質問の 1 回だけ。
    assert genai.embedded == [[ORIGINAL]]
    # 1 回の hybrid の検索: 全文は語を足した文、ベクトルは原質問の embedding。
    assert oracle.searches == [(EXPANDED, [float(len(ORIGINAL))], SearchMode.HYBRID)]
    # 足した語は全文検索の語の上限で優先する(#2163)。
    assert oracle.keywords == [{EXPANDED: ("合成語", "別名甲")}]
    (record,) = fusion
    (ranking,) = record["lists"]
    assert (ranking["query"], ranking["channel"], ranking["text_query"]) == (
        ORIGINAL,
        "hybrid",
        EXPANDED,
    )
    assert ranking["sources"] == ["原質問 + 用語・同義語"]


async def test_separate_sql_main_search_reads_vector_by_original_and_text_by_expanded() -> None:
    oracle, genai, fusion = await _main_search("oracle_separate_sql")

    assert genai.embedded == [[ORIGINAL]]
    assert oracle.searches == [
        (ORIGINAL, [float(len(ORIGINAL))], SearchMode.VECTOR),
        (EXPANDED, [float(len(ORIGINAL))], SearchMode.KEYWORD),
    ]
    assert oracle.keywords == [{EXPANDED: ("合成語", "別名甲")}]
    (record,) = fusion
    assert [(r["query"], r["channel"], r["text_query"], r["sources"]) for r in record["lists"]] == [
        (ORIGINAL, "vector", "", ["原質問"]),
        (ORIGINAL, "keyword", EXPANDED, ["原質問 + 用語・同義語"]),
    ]
    # 主検索は 1 本なので、原質問の予算がすべて(原質問と追加検索文の比重は使わない)。
    assert [r["weight"] for r in record["lists"]] == pytest.approx([0.7, 0.3])


async def test_separate_sql_reads_vector_with_original_embedding_and_contains_with_expanded_text(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``search_many`` の個別 SQL は、ベクトルの SQL に原質問の embedding、CONTAINS に
    語を足した文の式を渡す。"""
    client = OracleClient(
        settings=Settings.model_construct(
            oci_genai_embedding_dim=1,
            rag_rrf_k=60,
            rag_hybrid_search_backend="oracle_separate_sql",
            rag_domain_keywords=[],
            rag_text_search_max_tokens=24,
            rag_text_search_max_query_chars=3000,
        )
    )
    vectors: list[object] = []
    texts: list[str] = []

    async def ready() -> None:
        return None

    async def vector_branch(embedding_bind: object, **kwargs: Any) -> None:
        vectors.append(list(embedding_bind))  # type: ignore[call-overload]

    async def keyword_branch(text_query: str, **kwargs: Any) -> None:
        texts.append(text_query)

    monkeypatch.setattr(client, "_ensure_native_hybrid_ready", ready)
    monkeypatch.setattr(client, "_read_vector_branch", vector_branch)
    monkeypatch.setattr(client, "_read_keyword_branch", keyword_branch)
    with oracle_module.bind_text_query_keywords({EXPANDED: ("別名甲",)}):
        await client.search_many(
            [
                (ORIGINAL, [5.0], SearchMode.VECTOR),
                (EXPANDED, [5.0], SearchMode.KEYWORD),
            ],
            5,
        )
        expected = oracle_module._oracle_text_query(EXPANDED, settings=client._settings)
    assert vectors == [[5.0]]
    assert texts == [expected]
    assert "{別名甲}" in texts[0]
    assert texts[0] != oracle_module._oracle_text_query(ORIGINAL, settings=client._settings)
