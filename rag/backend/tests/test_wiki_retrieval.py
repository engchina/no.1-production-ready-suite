"""Wiki の検索の経路（#4941・#4986・#5044・#5731）を、合成の案内と決定論のスタブで確かめる。

DB・外部モデルは使わない。
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any, Literal
from unittest.mock import AsyncMock

import pytest
from pydantic import BaseModel

from app.clients import oci_enterprise_ai
from app.clients.wiki_store import WikiScope, WikiStore
from app.config import Settings
from app.rag.answer_engine import AnswerEngine
from app.rag.cross_references import reference_name_key
from app.rag.wiki_models import WikiContext, WikiDependency, WikiPage, WikiUnit, fingerprint
from app.rag.wiki_retrieval import WikiRetriever, WikiSourceChanged
from app.schemas.search import RetrievedChunk

QUESTION = "合成の手続の条件を教えて"


def page(
    doc: str, name: str, path: list[str], *, deps: tuple[str, ...] = (), version: str = "cs"
) -> WikiPage:
    text = f"{name} の合成の本文。"
    unit = WikiUnit(
        unit_id=f"{doc}-{name}",
        document_id=doc,
        chunk_set_id=f"{doc}-{version}",
        source_chunk_ids=[f"{doc}-{name}-chunk"],
        section_id=fingerprint(path),
        section_path=path,
        title=path[-1] if path else doc,
        text=text,
        source_hash=fingerprint(text),
    )
    return WikiPage(
        unit=unit,
        navigation=WikiContext(
            unit_id=unit.unit_id,
            context=f"{name} の案内",
            possible_questions=[],
            dependencies=[WikiDependency(unit_id=dep, relation="exception") for dep in deps],
            unresolved=[],
        ),
        input_fingerprint="v",
    )


PAGES = [
    page("doc-a", "a1", ["章1"]),
    page("doc-a", "a2", ["章1", "節1"], deps=("doc-a-a4",)),
    page("doc-a", "a3", ["章1", "節2"]),
    page("doc-a", "a4", ["章2"]),
    page("doc-b", "b1", ["章1"]),
]
CATALOG: list[dict[str, Any]] = [
    {
        "document_id": "doc-a",
        "title": "文書A",
        "source_identities": [
            {"source_key": "S-A", "revision_key": "R-2", "effective_date": "2026-01-01"}
        ],
    },
    {"document_id": "doc-b", "title": "文書B", "source_identities": []},
    {"document_id": "doc-c", "title": "文書C", "source_identities": []},
    {"document_id": "doc-d", "title": "文書D", "source_identities": []},
]


class Store:
    def __init__(self, pages: list[WikiPage], *, missing: set[str] = frozenset()) -> None:  # type: ignore[assignment]
        self.by_id = {p.unit.unit_id: p for p in pages}
        self.missing = missing
        self.calls: list[tuple[str, Any]] = []
        # 文書をまたぐ関連（起点, 終点, 関係）。両端が今の範囲で読めるものだけを返す（#5492）。
        self.link_rows: list[tuple[str, str, str]] = []

        # resolve で決めた範囲（後の読取がこれだけを受け取ることを確かめる。#5499）。
        self.scope: WikiScope | None = None
        # 索引（ベクトル検索）の順位の先頭に置く単位。無ければ登録の順。
        self.roots: list[str] = []

    def _ranked(self) -> list[WikiPage]:
        first = [self.by_id[uid] for uid in self.roots]
        return [*first, *(p for uid, p in self.by_id.items() if uid not in self.roots)]

    def _scoped(self, scope: WikiScope) -> None:
        assert scope is self.scope, "範囲は resolve の結果だけで読む"

    def _reference(self, target: str) -> tuple[str, str]:
        """関連の終点を、文書の名前と章節の表記の参照にする（#5784。参照先は名前で決まる）。"""
        unit = self.by_id[target].unit if target in self.by_id else None
        document = target.rsplit("-", 1)[0] if unit is None else unit.document_id
        title = next((row["title"] for row in CATALOG if row["document_id"] == document), document)
        return title, unit.section_path[-1] if unit and unit.section_path else ""

    async def references(
        self, unit_ids: list[str], scope: WikiScope
    ) -> list[tuple[str, str, str, str]]:
        self._scoped(scope)
        self.calls.append(("links", tuple(unit_ids)))
        return [
            (source, *self._reference(target), relation)
            for source, target, relation in self.link_rows
            if source in unit_ids and source in self.by_id
        ]

    async def referencing(
        self, keys: list[str], scope: WikiScope, **kwargs: Any
    ) -> list[tuple[str, str, str, str]]:
        self._scoped(scope)
        found = []
        for source, target, relation in self.link_rows:
            key = reference_name_key(self._reference(target)[0])
            if key is None or source not in self.by_id:
                continue
            if key in keys or any(key in alias for alias in kwargs.get("contained_in", ())):
                found.append((source, self.by_id[source].unit.document_id, key, relation))
        return found

    async def document_headings(self, scope: WikiScope) -> dict[str, list[str]]:
        return {}

    async def resolve(self, filters: dict[str, str]) -> WikiScope:
        self.calls.append(("resolve", filters["knowledge_base_id"]))
        self.scope = WikiScope(
            tuple(
                row
                for row in CATALOG
                if any(p.unit.document_id == row["document_id"] for p in self.by_id.values())
            ),
            tuple(sorted({(p.unit.document_id, p.unit.chunk_set_id, "f") for p in PAGES})),
        )
        return self.scope

    async def pages(self, scope: WikiScope, **kwargs: Any) -> list[WikiPage]:
        self._scoped(scope)
        self.calls.append(("pages", {k: v for k, v in kwargs.items() if k != "query_vector"}))
        scoped = kwargs.get("document_ids") is not None or kwargs.get("unit_ids") is not None
        values = list(self.by_id.values()) if scoped else self._ranked()
        if kwargs.get("document_ids") is not None:
            values = [p for p in values if p.unit.document_id in kwargs["document_ids"]]
        if kwargs.get("unit_ids") is not None:
            values = [self.by_id[uid] for uid in kwargs["unit_ids"] if uid in self.by_id]
        return values[: kwargs["limit"]] if kwargs.get("limit") else values

    async def read_sources(
        self, pages: list[WikiPage], filters: dict[str, str]
    ) -> list[RetrievedChunk]:
        return [
            RetrievedChunk(
                chunk_id=sid,
                document_id=p.unit.document_id,
                file_name=p.unit.document_id,
                text=p.unit.text,
                score=0.0,
                metadata={"chunk_set_id": p.unit.chunk_set_id},
            )
            for p in pages
            for sid in p.unit.source_chunk_ids
            if sid not in self.missing
        ]


def retriever(
    store: Store, *, enabled: bool = True, settings: Settings | None = None, scores: Any = None
) -> WikiRetriever:
    profile = SimpleNamespace(
        status="ACTIVE",
        config=SimpleNamespace(query=SimpleNamespace(wiki_search_enabled=enabled)),
        knowledge_bases=[
            SimpleNamespace(id="kb-a", status="ACTIVE"),
            SimpleNamespace(id="kb-x", status="ARCHIVED"),
        ],
    )

    class Oracle:
        async def get_search_answer_profile(self, profile_id: str) -> Any:
            return profile

    return FixedRetriever(settings or Settings(), Oracle(), GenAi(scores), store=store)  # type: ignore[arg-type]


class GenAi:
    """質問の embedding と rerank のスタブ。rerank は指定が無ければ候補の順を保つ。"""

    def __init__(self, scores: Any = None) -> None:
        self.scores = scores
        self.reranked: list[list[str]] = []

    async def embed(self, texts: list[str], **kwargs: Any) -> list[list[float]]:
        return [[0.1] * 4 for _ in texts]

    async def rerank(self, query: str, documents: list[str], top_n: int) -> list[tuple[int, float]]:
        self.reranked.append(list(documents))
        if isinstance(self.scores, Exception):
            raise self.scores
        scores = self.scores or [0.9 - index * 0.001 for index in range(len(documents))]
        return [(index, score) for index, score in enumerate(scores[: len(documents)])]


class FixedRetriever(WikiRetriever):
    """起点数（``top_k``）を決めておく検索の経路。指定が無ければ既定の起点数。"""

    top_k: int | None = None

    async def search(self, question: str, **kwargs: Any) -> Any:
        return await super().search(question, **{"top_k": self.top_k, **kwargs})


def rooted(store: Store, *roots: str, **settings: Any) -> WikiRetriever:
    """``roots`` の単位だけを索引の上位（辿りの起点）にした検索の経路。"""
    store.roots = list(roots)
    wiki = retriever(store, settings=Settings(**settings))
    wiki.top_k = len(roots)  # type: ignore[attr-defined]
    return wiki


A1, A2 = "doc-a-a1", "doc-a-a2"


@pytest.mark.asyncio
async def test_chat_reads_ranked_wiki_roots_and_dependencies_without_a_planner(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = Store([PAGES[1], PAGES[3], PAGES[-1]])
    store.roots = ["doc-a-a2", "doc-b-b1"]
    clients = retriever(store)
    engine = AnswerEngine(clients.settings, oracle=clients.oracle, genai=clients.genai)
    monkeypatch.setattr(
        oci_enterprise_ai,
        "OciEnterpriseAiClient",
        lambda **_: pytest.fail("Wiki が生成クライアントを作成"),
    )
    wiki = engine._wiki_retriever()
    wiki.store = store
    seconds: dict[str, float] = {}
    result = await wiki.search(
        QUESTION, profile_id="p", filters={}, timeout=None, stage_seconds=seconds, top_k=2
    )

    assert result.trace["selected_documents"] == 2
    assert result.trace["source_chunk_ids_by_origin"] == {
        "selected": ["doc-a-a2-chunk", "doc-b-b1-chunk"],
        "dependency": ["doc-a-a4-chunk"],
        "link": [],
    }
    # 候補はベクトル検索の順位の 1 回だけで決める（全文検索は呼ばない。#5825）。
    # 起点と辿った単位の由来を原文に付ける（合わせ込みの順と画面のバッジ。#5831）。
    assert {c.chunk_id: c.metadata["wiki_origin"] for c in result.chunks} == {
        "doc-a-a2-chunk": "root",
        "doc-b-b1-chunk": "root",
        "doc-a-a4-chunk": "dependency",
    }
    # 件数と類似度の下限は通常検索のベクトル検索と同じ設定（#5831）。
    assert [args for name, args in store.calls if name == "pages"][0] == {
        "limit": clients.settings.rag_retrieval_query_limit,
        "min_similarity": clients.settings.rag_min_similarity,
    }
    assert all("document_ids" not in args for name, args in store.calls if name == "pages")
    assert all("モデル" not in stage for stage in seconds)


def _root_ids(result: Any) -> list[str]:
    return [c.chunk_id for c in result.chunks if c.metadata.get("wiki_origin") == "root"]


@pytest.mark.asyncio
async def test_roots_are_the_reranked_originals_above_the_relevance_floor() -> None:
    """起点は、原文の rerank の点数の順で関連度の下限以上のものを、起点数まで（#5831）。"""
    # ベクトルの順は a1, a2, a3, a4, b1。rerank は a3 > b1 > a1 > a2（下限未満）> a4（下限未満）。
    store = Store(PAGES)
    wiki = retriever(
        store, settings=Settings(rag_wiki_link_depth=0), scores=[0.5, 0.29, 0.9, 0.1, 0.7]
    )
    wiki.top_k = 2  # type: ignore[attr-defined]
    result = await wiki.search(QUESTION, profile_id="p", filters={}, timeout=None)

    assert wiki.genai.reranked == [[page.unit.text for page in PAGES]]  # type: ignore[attr-defined]
    assert _root_ids(result) == ["doc-a-a3-chunk", "doc-b-b1-chunk"]
    assert [(c.metadata["wiki_rank"], c.metadata["wiki_rerank_score"]) for c in result.chunks] == [
        (3, 0.9),
        (5, 0.7),
    ]
    assert result.trace["rerank"] == "completed"
    assert (result.trace["reranked_units"], result.trace["relevant_units"]) == (5, 3)
    assert result.trace["selected_units"] == 2

    # 起点数が候補より多ければ、下限以上の全部が点数の順に起点になる。
    wiki.top_k = 50  # type: ignore[attr-defined]
    result = await wiki.search(QUESTION, profile_id="p", filters={}, timeout=None)
    assert _root_ids(result) == ["doc-a-a3-chunk", "doc-b-b1-chunk", "doc-a-a1-chunk"]


@pytest.mark.asyncio
async def test_roots_follow_the_per_document_cap_before_the_root_count() -> None:
    """起点は通常検索と同じく、下限 → 文書ごとの上限 → 起点数の順で決める（#5839）。"""
    # rerank の順は a3 > b1 > a1 > a2 > a4。文書ごとに 1 件なら doc-a は a3 だけ。
    scores = [0.5, 0.4, 0.9, 0.35, 0.7]
    store = Store(PAGES)
    wiki = retriever(
        store,
        settings=Settings(rag_wiki_link_depth=0, rag_max_anchors_per_document=1),
        scores=scores,
    )
    wiki.top_k = 50  # type: ignore[attr-defined]
    result = await wiki.search(QUESTION, profile_id="p", filters={}, timeout=None)
    assert _root_ids(result) == ["doc-a-a3-chunk", "doc-b-b1-chunk"]
    # 下限以上の数は上限の前の数。
    assert result.trace["relevant_units"] == 5

    wiki = retriever(store, settings=Settings(rag_wiki_link_depth=0), scores=scores)
    wiki.top_k = 50  # type: ignore[attr-defined]
    result = await wiki.search(QUESTION, profile_id="p", filters={}, timeout=None)
    assert len(_root_ids(result)) == 5


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("scores", "expected"),
    [
        # 1 件も下限（0.3）に届かないときだけ、下限を比（0.5）で 1 回下げる。
        ([0.2, 0.1, 0.16, 0.0, 0.14], ["doc-a-a1-chunk", "doc-a-a3-chunk"]),
        # 下げた下限にも届かなければ起点なし。
        ([0.1, 0.1, 0.1, 0.1, 0.1], []),
    ],
)
async def test_relevance_floor_is_lowered_once_only_when_nothing_reaches_it(
    scores: list[float], expected: list[str]
) -> None:
    wiki = retriever(Store(PAGES), settings=Settings(rag_wiki_link_depth=0), scores=scores)
    result = await wiki.search(QUESTION, profile_id="p", filters={}, timeout=None)
    assert _root_ids(result) == expected
    assert result.trace["status"] == "ready"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("settings", "scores", "state", "calls"),
    [
        (Settings(rag_wiki_link_depth=0, rag_rerank_enabled=False), None, "disabled", 0),
        (Settings(rag_wiki_link_depth=0), RuntimeError("rerank"), "failed", 1),
    ],
)
async def test_roots_keep_the_vector_order_when_rerank_is_off_or_fails(
    settings: Settings, scores: Any, state: str, calls: int
) -> None:
    """rerank が無効・失敗のときは、通常検索と同じくベクトルの順のまま起点数で切る（#5831）。"""
    wiki = retriever(Store(PAGES), settings=settings, scores=scores)
    wiki.top_k = 2  # type: ignore[attr-defined]
    result = await wiki.search(QUESTION, profile_id="p", filters={}, timeout=None)
    assert _root_ids(result) == ["doc-a-a1-chunk", "doc-a-a2-chunk"]
    assert result.trace["rerank"] == state
    assert len(wiki.genai.reranked) == calls  # type: ignore[attr-defined]
    assert all("wiki_rerank_score" not in c.metadata for c in result.chunks)


@pytest.mark.asyncio
async def test_empty_rankings_do_not_reopen_documents_from_the_catalog(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = Store(PAGES)
    pages = AsyncMock(return_value=[])
    monkeypatch.setattr(store, "pages", pages)
    result = await retriever(store).search(QUESTION, profile_id="p", filters={}, timeout=None)

    assert result.trace["catalog_documents"] == 2 and result.trace["candidate_units"] == 0
    assert result.chunks == ()
    assert all("document_ids" not in call.kwargs for call in pages.await_args_list)


@pytest.mark.asyncio
async def test_profile_without_wiki_search_calls_nothing() -> None:
    store = Store(PAGES)
    result = await retriever(store, enabled=False).search(
        QUESTION, profile_id="p", filters={}, timeout=None
    )
    assert result.trace == {"status": "profile_disabled"}
    assert store.calls == []


@pytest.mark.asyncio
async def test_no_ready_wiki_returns_not_ready() -> None:
    result = await retriever(Store([])).search(QUESTION, profile_id="p", filters={}, timeout=None)
    assert result.trace["status"] == "not_ready" and result.chunks == ()


@pytest.mark.asyncio
async def test_ranked_roots_read_dependencies_with_version_attributes() -> None:
    store = Store(PAGES)
    result = await rooted(store, A1, A2).search(
        QUESTION, profile_id="p", filters={"knowledge_base_id": "kb-a,kb-x"}, timeout=None
    )

    assert result.trace["status"] == "ready"
    # 有効な KB だけで探す。
    assert store.calls[0] == ("resolve", "kb-a")
    # 範囲を決めるのは最初の 1 回だけ（#5499）。
    assert [name for name, _ in store.calls].count("resolve") == 1
    # 起点の単位と案内の依存（a2 → a4）の原文を、版の属性つきで返す（#5044）。
    ids = {chunk.chunk_id for chunk in result.chunks}
    assert {"doc-a-a2-chunk", "doc-a-a4-chunk"} <= ids and not any(
        i.startswith("doc-b") for i in ids
    )
    assert all(chunk.metadata["wiki_source"] is True for chunk in result.chunks)
    assert all(
        chunk.metadata["source_identities"] == CATALOG[0]["source_identities"]
        for chunk in result.chunks
    )
    assert "navigation_unresolved" not in result.trace


@pytest.mark.asyncio
async def test_changed_source_is_not_returned() -> None:
    store = Store(PAGES, missing={"doc-a-a4-chunk"})
    with pytest.raises(WikiSourceChanged):
        await rooted(store, A2).search(QUESTION, profile_id="p", filters={}, timeout=None)


@pytest.mark.asyncio
async def test_current_sources_fails_when_the_profile_turns_wiki_off() -> None:
    with pytest.raises(WikiSourceChanged):
        await retriever(Store(PAGES), enabled=False).current_sources("p", {}, PAGES[:1])


# ---- 依存と文書をまたぐ関連の辿り（#5492） ----

LINKED = [*PAGES, page("doc-c", "c1", ["通知"]), page("doc-d", "d1", ["別表"])]


def _units(result: Any) -> list[str]:
    return [p.unit.unit_id for p in result.pages]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("depth", "expected", "hops"),
    [
        (0, set(), []),
        (1, {"doc-a-a4", "doc-c-c1"}, [{"depth": 1, "dependency_units": 1, "link_units": 1}]),
        (
            2,
            {"doc-a-a4", "doc-c-c1", "doc-d-d1"},
            [
                {"depth": 1, "dependency_units": 1, "link_units": 1},
                {"depth": 2, "dependency_units": 0, "link_units": 1},
            ],
        ),
    ],
)
async def test_outgoing_links_are_followed_up_to_the_configured_depth(
    depth: int, expected: set[str], hops: list[dict[str, int]]
) -> None:
    """出る向きの関連（a2 → c1）を辿り、2 段のときだけ c1 → d1 を読む。"""
    store = Store(LINKED)
    store.link_rows = [
        ("doc-a-a2", "doc-c-c1", "exception"),
        ("doc-c-c1", "doc-d-d1", "reference"),
    ]
    result = await rooted(store, A1, A2, rag_wiki_link_depth=depth).search(
        QUESTION, profile_id="p", filters={}, timeout=None
    )
    followed = set(_units(result)) - {"doc-a-a1", "doc-a-a2"}
    assert followed == expected
    assert result.trace["link_hops"] == hops
    assert result.trace["dependency_units"] == len(expected)
    if depth == 0:
        assert not [call for call in store.calls if call[0] == "links"]


@pytest.mark.parametrize(
    ("depth", "roots", "expected", "links_read"),
    [
        (0, ["doc-a-a2"], ["doc-a-a2"], []),
        (1, ["doc-a-a2"], ["doc-a-a2", "doc-a-a4"], [("doc-a-a2",)]),
        (
            2,
            ["doc-a-a2"],
            ["doc-a-a2", "doc-a-a4", "doc-b-b1"],
            [("doc-a-a2",), ("doc-a-a4",)],
        ),
        (
            1,
            ["doc-a-a2", "doc-a-a4"],
            ["doc-a-a2", "doc-a-a4", "doc-b-b1"],
            [("doc-a-a2", "doc-a-a4")],
        ),
        (
            1,
            ["doc-a-a2", "doc-a-a3"],
            ["doc-a-a2", "doc-a-a3", "doc-a-a4", "doc-b-b1"],
            [("doc-a-a2", "doc-a-a3")],
        ),
        # 入る向き（#5784）: b1 の文書（文書B）を名前で参照する a4・a3 も辿る。
        (1, ["doc-b-b1"], ["doc-b-b1", "doc-a-a4", "doc-a-a3"], [("doc-b-b1",)]),
    ],
)
@pytest.mark.asyncio
async def test_only_ranked_roots_start_at_depth_zero(
    depth: int, roots: list[str], expected: list[str], links_read: list[tuple[str, ...]]
) -> None:
    # 同文書の a2 → a4、別文書への a4 → b1。起点は索引の上位の単位だけ。
    store = Store(PAGES)
    store.link_rows = [("doc-a-a4", "doc-b-b1", "reference"), ("doc-a-a3", "doc-b-b1", "reference")]
    result = await rooted(store, *roots, rag_wiki_link_depth=depth).search(
        QUESTION, profile_id="p", filters={}, timeout=None
    )
    assert _units(result) == expected
    assert [chunk.chunk_id for chunk in result.chunks] == [f"{uid}-chunk" for uid in expected]
    assert [value for name, value in store.calls if name == "links"] == links_read


@pytest.mark.parametrize(
    ("depth", "link_chunks"),
    [(1, ["doc-b-b1-chunk"]), (2, ["doc-b-b1-chunk", "doc-c-c1-chunk"])],
)
@pytest.mark.asyncio
async def test_reference_target_among_the_candidates_keeps_its_distance(
    depth: int, link_chunks: list[str]
) -> None:
    store = Store(LINKED)
    store.link_rows = [
        ("doc-a-a1", "doc-b-b1", "reference"),
        ("doc-b-b1", "doc-c-c1", "reference"),
    ]
    result = await rooted(store, A1, rag_wiki_link_depth=depth).search(
        QUESTION, profile_id="p", filters={}, timeout=None
    )
    assert result.trace["source_chunk_ids_by_origin"] == {
        "selected": ["doc-a-a1-chunk"],
        "dependency": [],
        "link": link_chunks,
    }
    assert result.trace["source_chunk_ids"] == ["doc-a-a1-chunk", *link_chunks]


@pytest.mark.asyncio
async def test_budget_keeps_exceptions_and_prerequisites_before_references() -> None:
    store = Store(LINKED)
    store.link_rows = [
        ("doc-a-a2", "doc-c-c1", "reference"),
        ("doc-a-a2", "doc-d-d1", "prerequisite"),
    ]
    result = await rooted(store, A1, A2, rag_wiki_link_max_units=2).search(
        QUESTION, profile_id="p", filters={}, timeout=None
    )
    followed = [uid for uid in _units(result) if uid not in {"doc-a-a1", "doc-a-a2"}]
    # 例外（同じ文書の依存 a4）と前提（d1）を先に足し、参照（c1）は上限で外す。
    assert followed == ["doc-a-a4", "doc-d-d1"]
    assert result.trace["dependency_units"] == 2


@pytest.mark.asyncio
async def test_cycles_stop_and_each_unit_is_read_once() -> None:
    store = Store(LINKED)
    store.link_rows = [
        ("doc-a-a2", "doc-c-c1", "prerequisite"),
        ("doc-c-c1", "doc-a-a2", "exception"),
        ("doc-c-c1", "doc-d-d1", "warning"),
        ("doc-d-d1", "doc-c-c1", "warning"),
        ("doc-d-d1", "doc-a-a2", "reference"),
    ]
    result = await rooted(store, A1, A2, rag_wiki_link_depth=3).search(
        QUESTION, profile_id="p", filters={}, timeout=None
    )
    units = _units(result)
    assert len(units) == len(set(units))
    assert {"doc-c-c1", "doc-d-d1", "doc-a-a4"} <= set(units)
    # 3 段目は新しい単位が無いので、辿りはそこで止まる。
    assert len([call for call in store.calls if call[0] == "links"]) <= 3


@pytest.mark.asyncio
async def test_unreadable_link_targets_are_not_returned_or_counted_against_the_budget() -> None:
    """今の範囲で読めない終点（別の KB・未公開・古い保存版）は足さず、読めなかった数に残す。"""

    class OutOfScope(Store):
        async def references(
            self, unit_ids: list[str], scope: WikiScope
        ) -> list[tuple[str, str, str, str]]:
            # 今の範囲に無い文書（文書Z）への参照は、参照先が決まらず辿らない（#5784）。
            return [
                ("doc-a-a2", "文書Z", "", "exception"),
                ("doc-a-a2", "文書C", "通知", "reference"),
            ]

    store = OutOfScope(LINKED)
    result = await rooted(store, A1, A2, rag_wiki_link_depth=1, rag_wiki_link_max_units=2).search(
        QUESTION, profile_id="p", filters={}, timeout=None
    )
    units = _units(result)
    assert not any(uid.startswith("doc-z") for uid in units) and "doc-c-c1" in units
    assert result.trace["missing_dependency_units"] == 0


# ---- 範囲の重い条件は 1 問に 1 回（#5499） ----

# 範囲の重い条件の目印: 公開・今の保存版の条件（構築の保存版の集合）と、有効な KB の所属の EXISTS。
PUBLISHED_MARK = "w.chunk_sets_json"
MEMBERSHIP_MARK = "rag_document_knowledge_bases wm"


class SqlOracle:
    """SQL を記録し、合成の行を返す Oracle の代役（実 SQL は test_wiki_store_oracle.py）。"""

    def __init__(self, pages: list[WikiPage]) -> None:
        self.by_id = {p.unit.unit_id: p for p in pages}
        self.sql: list[str] = []

    async def get_search_answer_profile(self, profile_id: str) -> Any:
        return SimpleNamespace(
            status="ACTIVE",
            config=SimpleNamespace(query=SimpleNamespace(wiki_search_enabled=True)),
            knowledge_bases=[SimpleNamespace(id="kb-a", status="ACTIVE")],
        )

    @staticmethod
    def _values(binds: dict[str, Any], prefix: str) -> list[str] | None:
        values = [v for k, v in binds.items() if k.startswith(f"{prefix}_")]
        return values or None

    async def _fetch_all(self, sql: str, binds: dict[str, Any]) -> list[dict[str, Any]]:
        self.sql.append(sql)
        pages = list(self.by_id.values())
        if "wiki_chunk_set_id" in sql:
            return [
                {
                    "document_id": p.unit.document_id,
                    "file_name": "文書A" if p.unit.document_id == "doc-a" else p.unit.document_id,
                    "wiki_chunk_set_id": p.unit.chunk_set_id,
                    "wiki_fingerprint": "f",
                }
                for p in pages
            ]
        if "rag_wiki_references" in sql:
            return []
        if "VECTOR_DISTANCE" in sql:
            return [{"unit_id": uid} for uid in self.by_id]
        if "p.page_json" in sql:
            units, documents = self._values(binds, "wiki_unit"), self._values(binds, "wiki_doc")
            return [
                {"page_json": p.model_dump()}
                for p in pages
                if (units is None or p.unit.unit_id in units)
                and (documents is None or p.unit.document_id in documents)
            ]
        assert "c.chunk_text" in sql, sql
        wanted = set(self._values(binds, "wiki_source") or [])
        return [
            {
                "document_id": p.unit.document_id,
                "chunk_id": sid,
                "chunk_set_id": p.unit.chunk_set_id,
                "chunk_text": p.unit.text,
                "chunk_index": 0,
            }
            for p in pages
            for sid in p.unit.source_chunk_ids
            if sid in wanted
        ]


@pytest.mark.asyncio
async def test_search_evaluates_the_heavy_scope_once_and_rechecks_sources() -> None:
    """範囲（所属・権限・公開・今の保存版）の重い条件は目録の 1 回だけで評価し、後の読取は決めた
    集合だけで絞る。回答に使う原文は read_sources が今の範囲で照合し直す。"""
    oracle = SqlOracle(LINKED)

    wiki = WikiRetriever(
        Settings(rag_wiki_link_depth=2),
        oracle,  # type: ignore[arg-type]
        GenAi(),  # type: ignore[arg-type]
        store=WikiStore(oracle),  # type: ignore[arg-type]
    )
    result = await wiki.search(QUESTION, profile_id="p", filters={}, timeout=None)

    assert result.trace["status"] == "ready" and result.chunks
    published = [sql for sql in oracle.sql if PUBLISHED_MARK in sql]
    membership = [sql for sql in oracle.sql if MEMBERSHIP_MARK in sql]
    # 目録（範囲を決める 1 回）と、回答の前の原文の照合（read_sources）だけ。
    assert len(published) == 1 and "wiki_chunk_set_id" in published[0]
    assert len(membership) == 2 and "c.chunk_text" in membership[1]
    later = [sql for sql in oracle.sql if PUBLISHED_MARK not in sql and MEMBERSHIP_MARK not in sql]
    # 候補・ID の読み込み・関連の辿りは、決めた集合の条件で読む。
    assert len(later) >= 5
    # 全文検索の SQL は出さない（#5825）。
    assert not any("CONTAINS(" in sql for sql in oracle.sql)
    assert all("p.chunk_set_id IN" in sql and "p.source_fingerprint IN" in sql for sql in later)
    assert not any("rag_document_knowledge_bases" in sql for sql in later)
    assert any("rag_wiki_references" in sql for sql in later)


def _relations(result: Any) -> dict[str, Any]:
    return {chunk.chunk_id: chunk.metadata.get("wiki_relations") for chunk in result.chunks}


@pytest.mark.asyncio
async def test_followed_relations_reach_both_sources_with_the_other_location() -> None:
    """辿りで見た関係（#5500）は、両端の原文に、役割を持つ側と相手の文書名・章節つきで付く。"""
    store = Store(LINKED)
    store.link_rows = [("doc-a-a2", "doc-c-c1", "exception"), ("doc-c-c1", "doc-d-d1", "reference")]
    result = await rooted(store, A1, A2, rag_wiki_link_depth=1).search(
        QUESTION, profile_id="p", filters={}, timeout=None
    )
    relations = _relations(result)
    # c1 は a2 の例外。a2 から見ると相手（c1）が役割を持つ。同じ文書の依存（a2 → a4）も載る。
    assert relations["doc-a-a2-chunk"] == [
        {
            "relation": "exception",
            "role_of": "other",
            "document_name": "文書A",
            "section_path": ["章2"],
        },
        {
            "relation": "exception",
            "role_of": "other",
            "document_name": "文書C",
            "section_path": ["通知"],
        },
    ]
    assert relations["doc-c-c1-chunk"] == [
        {
            "relation": "exception",
            "role_of": "this",
            "document_name": "文書A",
            "section_path": ["章1", "節1"],
        }
    ]
    # 段数で辿らなかった d1 は原文に入らないので、c1 → d1 の関係は載せない。
    assert relations["doc-a-a1-chunk"] is None


@pytest.mark.asyncio
async def test_no_relations_are_attached_when_links_are_not_followed() -> None:
    store = Store(LINKED)
    store.link_rows = [("doc-a-a2", "doc-c-c1", "exception")]
    result = await rooted(store, A1, A2, rag_wiki_link_depth=0).search(
        QUESTION, profile_id="p", filters={}, timeout=None
    )
    assert all(value is None for value in _relations(result).values())


def test_answer_reading_rule_names_every_wiki_relation() -> None:
    """回答のモデルへの読み方（rag_engine）は、Wiki の関係の種類をすべて説明する（#5500）。"""
    from typing import get_args

    from rag_engine.generation.grounded import EVIDENCE_READING_PROMPT

    from app.rag.wiki_models import WikiRelation

    line = next(
        line
        for line in EVIDENCE_READING_PROMPT.splitlines()
        if line.startswith("- wiki_relations: ")
    )
    assert all(f"{relation}=" in line for relation in get_args(WikiRelation))


# ---- 回答の診断の測定用の値（#5518） ----

WIKI_STAGES = [
    "Wiki: 目録の読み込み",
    "Wiki: 質問のベクトル化",
    "Wiki: 案内の候補の検索",
    "Wiki: 起点の再ランキング",
    "Wiki: 依存と関連の辿り",
    "Wiki: 原文の読み込み",
]


@pytest.mark.asyncio
async def test_trace_lists_the_source_chunk_ids_by_origin_and_the_stage_seconds() -> None:
    """根拠に渡した原文の chunk ID と、選んだ単位・依存・関連ごとの chunk ID を記録に残す。"""
    store = Store(LINKED)
    store.link_rows = [("doc-a-a2", "doc-c-c1", "exception")]
    seconds: dict[str, float] = {}
    result = await rooted(store, A1, A2, rag_wiki_link_depth=1).search(
        QUESTION, profile_id="p", filters={}, timeout=None, stage_seconds=seconds
    )

    trace = result.trace
    assert trace["source_chunk_ids"] == [chunk.chunk_id for chunk in result.chunks]
    assert len(trace["source_chunk_ids"]) == trace["source_count"]
    assert trace["source_chunk_ids_by_origin"] == {
        "selected": ["doc-a-a1-chunk", "doc-a-a2-chunk"],
        "dependency": ["doc-a-a4-chunk"],
        "link": ["doc-c-c1-chunk"],
    }
    # 由来ごとの chunk ID は根拠の chunk ID と同じ集合（DB を読み足さない）。
    by_origin = trace["source_chunk_ids_by_origin"]
    assert {sid for ids in by_origin.values() for sid in ids} == set(trace["source_chunk_ids"])
    # 段（span）ごとの秒は、渡した記録に段の順で入る。全体の秒は回答の側で測る。
    assert list(seconds) == WIKI_STAGES
    assert all(isinstance(value, float) and value >= 0 for value in seconds.values())
    assert "elapsed_seconds" not in trace and "stage_seconds" not in trace


@pytest.mark.asyncio
async def test_source_chunk_ids_follow_the_diagnostics_limit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.rag import wiki_retrieval

    monkeypatch.setattr(wiki_retrieval, "_SOURCE_DIAGNOSTICS_LIMIT", 1)
    result = await rooted(Store(PAGES), A1, A2).search(
        QUESTION, profile_id="p", filters={}, timeout=None
    )
    trace = result.trace
    assert trace["source_count"] > 1 and len(trace["source_chunk_ids"]) == 1
    assert all(len(ids) <= 1 for ids in trace["source_chunk_ids_by_origin"].values())


@pytest.mark.asyncio
async def test_stage_seconds_keep_the_stage_that_failed() -> None:
    """失敗した段も、それまでの秒を残す（時間切れの段を記録から見る）。"""

    class Broken(Store):
        async def read_sources(
            self, pages: list[WikiPage], filters: dict[str, str]
        ) -> list[RetrievedChunk]:
            raise RuntimeError("合成の失敗")

    seconds: dict[str, float] = {}
    with pytest.raises(RuntimeError):
        await rooted(Broken(PAGES), A1, A2).search(
            QUESTION, profile_id="p", filters={}, timeout=None, stage_seconds=seconds
        )
    assert list(seconds) == WIKI_STAGES


@pytest.mark.asyncio
async def test_profile_without_wiki_search_records_no_measurement() -> None:
    seconds: dict[str, float] = {}
    result = await retriever(Store(PAGES), enabled=False).search(
        QUESTION, profile_id="p", filters={}, timeout=None, stage_seconds=seconds
    )
    assert result.trace == {"status": "profile_disabled"}
    assert seconds == {}


def test_failure_reason_keeps_only_the_kind_and_the_schema_location() -> None:
    """記録に残す理由は種類と、schema の誤りの種類・位置だけ（入力の値・本文を入れない）。"""
    from app.rag.wiki_retrieval import failure_reason

    class Choice(BaseModel):
        document_ids: list[Literal["D1"]]

    with pytest.raises(ValueError) as caught:
        Choice.model_validate({"document_ids": ["合成の値"]})
    reason = failure_reason(caught.value)
    assert reason == "invalid_output:literal_error@document_ids.0"
    assert "合成" not in reason
    assert failure_reason(TimeoutError()) == "timeout"
    assert failure_reason(RuntimeError("合成の失敗")) == "unknown"
