"""Wiki の検索の経路（#4941・#4986・#5036・#5044）を、合成の案内と決定論のスタブで確かめる。

DB・外部モデルは使わない。
"""

from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Any

import pytest

from app.clients.wiki_store import WikiScope, WikiStore
from app.config import Settings
from app.rag.wiki_models import WikiContext, WikiDependency, WikiPage, WikiUnit, fingerprint
from app.rag.wiki_navigation import section_frontier
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

    def _scoped(self, scope: WikiScope) -> None:
        assert scope is self.scope, "範囲は resolve の結果だけで読む"

    async def links(self, unit_ids: list[str], scope: WikiScope) -> list[tuple[str, str, str]]:
        self._scoped(scope)
        self.calls.append(("links", tuple(unit_ids)))
        return [
            row
            for row in self.link_rows
            if (row[0] in unit_ids or row[1] in unit_ids)
            and row[0] in self.by_id
            and row[1] in self.by_id
        ]

    async def resolve(self, filters: dict[str, str], compiler: str) -> WikiScope:
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
        values = list(self.by_id.values())
        if kwargs.get("document_ids") is not None:
            values = [p for p in values if p.unit.document_id in kwargs["document_ids"]]
        if kwargs.get("unit_ids") is not None:
            values = [self.by_id[uid] for uid in kwargs["unit_ids"] if uid in self.by_id]
        return values[: kwargs["limit"]] if kwargs.get("limit") else values

    async def text_rankings(self, expression: str, scope: WikiScope, **kwargs: Any) -> Any:
        self._scoped(scope)
        self.calls.append(("text_rankings", (expression, tuple(kwargs.get("document_ids") or ()))))
        documents = kwargs.get("document_ids")
        return [
            (p.unit.document_id, uid)
            for uid, p in self.by_id.items()
            if documents is None or p.unit.document_id in documents
        ]

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


class Llm:
    def __init__(self, files: list[str], units: list[str] = (), sections: int = 0) -> None:  # type: ignore[assignment]
        self.files, self.units, self.sections = files, list(units), sections
        self.inputs: list[dict[str, Any]] = []

    async def generate(self, prompt: str, data: str, **kwargs: Any) -> str:
        payload = json.loads(data)
        self.inputs.append(payload)
        if "files" in payload:
            handles = {row["title"]: row["document_id"] for row in payload["files"]}
            return json.dumps(
                {
                    "document_ids": [handles[t] for t in self.files],
                    "search_expressions": ["合成の表現"],
                    "unresolved": [],
                }
            )
        units = {u["context"]: u["unit_id"] for u in payload["units"]}
        return json.dumps(
            {
                "unit_ids": [units[f"{name} の案内"] for name in self.units],
                "section_ids": [s["section_id"] for s in payload["sections"][: self.sections]],
                "unresolved": ["合成の未確認"],
            }
        )


def retriever(
    store: Store, llm: Llm, *, enabled: bool = True, settings: Settings | None = None
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

    class GenAi:
        async def embed(self, texts: list[str], **kwargs: Any) -> list[list[float]]:
            return [[0.1] * 4 for _ in texts]

    return WikiRetriever(settings or Settings(), Oracle(), GenAi(), llm, store=store)  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_profile_without_wiki_search_calls_nothing() -> None:
    store, llm = Store(PAGES), Llm([])
    result = await retriever(store, llm, enabled=False).search(
        QUESTION, profile_id="p", filters={}, timeout=None
    )
    assert result.trace == {"status": "profile_disabled", "model_calls": 0}
    assert store.calls == [] and llm.inputs == []


@pytest.mark.asyncio
async def test_no_ready_wiki_returns_not_ready_without_model_calls() -> None:
    store, llm = Store([]), Llm([])
    result = await retriever(store, llm).search(QUESTION, profile_id="p", filters={}, timeout=None)
    assert result.trace["status"] == "not_ready" and llm.inputs == []


@pytest.mark.asyncio
async def test_two_plans_read_units_sections_and_dependencies_with_version_attributes() -> None:
    store, llm = Store(PAGES), Llm(["文書A"], units=["a2"])
    result = await retriever(store, llm).search(
        QUESTION, profile_id="p", filters={"knowledge_base_id": "kb-a,kb-x"}, timeout=None
    )

    assert result.trace["status"] == "ready" and result.trace["model_calls"] == 2
    # 有効な KB だけで探す。
    assert store.calls[0] == ("resolve", "kb-a")
    # 範囲を決めるのは最初の 1 回だけ（#5499）。
    assert [name for name, _ in store.calls].count("resolve") == 1
    # モデルへは要求内の短い ID だけを渡す。
    sent = json.dumps(llm.inputs, ensure_ascii=False)
    assert "doc-a" not in sent and "doc-a-a2" not in sent
    # 2 回目の計画にも、選んだ文書の版の属性を渡す（#5044）。
    assert llm.inputs[1]["documents"] == [{**CATALOG[0], "document_id": "D1"}]
    # 選んだ単位・文書の最上位の単位・案内の依存（a2 → a4）の原文を、版の属性つきで返す。
    ids = {chunk.chunk_id for chunk in result.chunks}
    assert {"doc-a-a2-chunk", "doc-a-a4-chunk"} <= ids and not any(
        i.startswith("doc-b") for i in ids
    )
    assert all(chunk.metadata["wiki_source"] is True for chunk in result.chunks)
    assert all(
        chunk.metadata["source_identities"] == CATALOG[0]["source_identities"]
        for chunk in result.chunks
    )
    assert result.trace["navigation_unresolved"] == ["合成の未確認"]


@pytest.mark.asyncio
async def test_selected_documents_are_read_once_and_each_expression_is_ranked_once() -> None:
    """選んだ文書の案内は 1 回で読み、検索表現ごとの順位は全文書を 1 回で取る（#4986）。"""
    store, llm = Store(PAGES), Llm(["文書A", "文書B"], units=["a1"])
    await retriever(store, llm).search(QUESTION, profile_id="p", filters={}, timeout=None)
    rankings = [args for name, args in store.calls if name == "text_rankings"]
    assert rankings == [
        (QUESTION, ()),
        (QUESTION, ("doc-a", "doc-b")),
        ("合成の表現", ("doc-a", "doc-b")),
    ]
    assert [args for name, args in store.calls if name == "pages" and args.get("document_ids")] == [
        {"document_ids": ["doc-a", "doc-b"]}
    ]


@pytest.mark.asyncio
async def test_selected_section_reads_every_unit_of_the_section() -> None:
    store, llm = Store(PAGES), Llm(["文書A"], sections=1)
    result = await retriever(store, llm).search(QUESTION, profile_id="p", filters={}, timeout=None)
    sections = llm.inputs[1]["sections"]
    assert sum(s["original_units"] for s in sections) == 4  # 選んだ文書の全単位を覆う
    assert all(s["original_chars"] > 0 for s in sections)
    assert result.trace["selected_units"] >= sections[0]["original_units"]


@pytest.mark.asyncio
async def test_changed_source_is_not_returned() -> None:
    store, llm = Store(PAGES, missing={"doc-a-a4-chunk"}), Llm(["文書A"], units=["a2"])
    with pytest.raises(WikiSourceChanged):
        await retriever(store, llm).search(QUESTION, profile_id="p", filters={}, timeout=None)


@pytest.mark.asyncio
async def test_current_sources_fails_when_the_profile_turns_wiki_off() -> None:
    with pytest.raises(WikiSourceChanged):
        await retriever(Store(PAGES), Llm([]), enabled=False).current_sources("p", {}, PAGES[:1])


def test_section_candidates_keep_direct_parent_content_apart_from_children() -> None:
    """親に直接属する原文は子の章節と別の候補になる（#5036）。"""
    sections = section_frontier(PAGES[:4], limit=10, ranked_ids={})
    groups = sorted(sorted(units) for _, units, _ in sections)
    assert groups == [["doc-a-a1"], ["doc-a-a2"], ["doc-a-a3"], ["doc-a-a4"]]
    assert {tuple(card["path"]) for _, _, card in sections} == {
        ("章1",),
        ("章1", "節1"),
        ("章1", "節2"),
        ("章2",),
    }


@pytest.mark.parametrize("limit", [1, 2, 3])
def test_section_candidates_cover_every_unit_within_the_limit(limit: int) -> None:
    sections = section_frontier(PAGES[:4], limit=limit, ranked_ids={})
    assert len(sections) <= limit
    members = [uid for _, units, _ in sections for uid in units]
    assert sorted(members) == sorted(p.unit.unit_id for p in PAGES[:4])
    assert all(card["original_units"] == len(units) for _, units, card in sections)


def test_section_candidates_never_mix_versions_and_reject_a_too_small_limit() -> None:
    pages = [page("doc-a", "a1", ["章1"]), page("doc-a", "a1n", ["章1"], version="cs2"), PAGES[4]]
    sections = section_frontier(pages, limit=3, ranked_ids={})
    versions = {p.unit.unit_id: p.unit.chunk_set_id for p in pages}
    assert all(len({versions[uid] for uid in units}) == 1 for _, units, _ in sections)
    assert len(sections) == 3
    with pytest.raises(ValueError):
        section_frontier(pages, limit=2, ranked_ids={})


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
async def test_links_are_followed_both_ways_up_to_the_configured_depth(
    depth: int, expected: set[str], hops: list[dict[str, int]]
) -> None:
    """入る向きの関連（通知 c1 → 本文 a2）も辿り、段数の設定で 2 段目（c1 → d1）まで読む。"""
    store, llm = Store(LINKED), Llm(["文書A"], units=["a2"])
    store.link_rows = [
        ("doc-c-c1", "doc-a-a2", "exception"),
        ("doc-c-c1", "doc-d-d1", "reference"),
    ]
    result = await retriever(store, llm, settings=Settings(rag_wiki_link_depth=depth)).search(
        QUESTION, profile_id="p", filters={}, timeout=None
    )
    followed = set(_units(result)) - {"doc-a-a1", "doc-a-a2"}
    assert followed == expected
    assert result.trace["link_hops"] == hops
    assert result.trace["dependency_units"] == len(expected)
    assert result.trace["model_calls"] == 2  # 辿るのは DB だけ
    if depth == 0:
        assert not [call for call in store.calls if call[0] == "links"]


@pytest.mark.asyncio
async def test_budget_keeps_exceptions_and_prerequisites_before_references() -> None:
    store, llm = Store(LINKED), Llm(["文書A"], units=["a2"])
    store.link_rows = [
        ("doc-a-a2", "doc-c-c1", "reference"),
        ("doc-d-d1", "doc-a-a2", "prerequisite"),
    ]
    result = await retriever(store, llm, settings=Settings(rag_wiki_link_max_units=2)).search(
        QUESTION, profile_id="p", filters={}, timeout=None
    )
    followed = [uid for uid in _units(result) if uid not in {"doc-a-a1", "doc-a-a2"}]
    # 例外（同じ文書の依存 a4）と前提（d1）を先に足し、参照（c1）は上限で外す。
    assert followed == ["doc-a-a4", "doc-d-d1"]
    assert result.trace["dependency_units"] == 2


@pytest.mark.asyncio
async def test_cycles_stop_and_each_unit_is_read_once() -> None:
    store, llm = Store(LINKED), Llm(["文書A"], units=["a2"])
    store.link_rows = [
        ("doc-a-a2", "doc-c-c1", "prerequisite"),
        ("doc-c-c1", "doc-a-a2", "exception"),
        ("doc-c-c1", "doc-d-d1", "warning"),
        ("doc-d-d1", "doc-c-c1", "warning"),
        ("doc-d-d1", "doc-a-a2", "reference"),
    ]
    result = await retriever(store, llm, settings=Settings(rag_wiki_link_depth=3)).search(
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
        async def links(self, unit_ids: list[str], scope: WikiScope) -> list[tuple[str, str, str]]:
            return [("doc-a-a2", "doc-z-z1", "exception"), ("doc-a-a2", "doc-c-c1", "reference")]

    store, llm = OutOfScope(LINKED), Llm(["文書A"], units=["a2"])
    result = await retriever(
        store, llm, settings=Settings(rag_wiki_link_depth=1, rag_wiki_link_max_units=2)
    ).search(QUESTION, profile_id="p", filters={}, timeout=None)
    units = _units(result)
    assert "doc-z-z1" not in units and "doc-c-c1" in units
    assert result.trace["missing_dependency_units"] == 1


# ---- 範囲の重い条件は 1 問に 1 回（#5499） ----

# 範囲の重い条件の目印: 公開・今の保存版の条件（JSON_TABLE）と、有効な KB の所属の EXISTS。
PUBLISHED_MARK = "JSON_TABLE(w.chunk_sets_json"
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
        if "rag_wiki_links" in sql:
            return []
        if "VECTOR_DISTANCE" in sql:
            return [{"unit_id": uid} for uid in self.by_id]
        if "CONTAINS(" in sql:
            documents = self._values(binds, "wiki_text_doc")
            return [
                {"document_id": p.unit.document_id, "unit_id": p.unit.unit_id}
                for p in pages
                if documents is None or p.unit.document_id in documents
            ]
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
    llm = Llm(["文書A"], units=["a2"])

    class GenAi:
        async def embed(self, texts: list[str], **kwargs: Any) -> list[list[float]]:
            return [[0.1] * 4 for _ in texts]

    wiki = WikiRetriever(
        Settings(rag_wiki_link_depth=2),
        oracle,  # type: ignore[arg-type]
        GenAi(),  # type: ignore[arg-type]
        llm,
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
    # 候補・全文の順位・ID の読み込み・関連の辿りは、決めた集合の条件で読む。
    assert len(later) >= 6
    assert all("p.chunk_set_id IN" in sql and "p.source_fingerprint IN" in sql for sql in later)
    assert not any("rag_document_knowledge_bases" in sql for sql in later)
    assert any("rag_wiki_links" in sql for sql in later)


def _relations(result: Any) -> dict[str, Any]:
    return {chunk.chunk_id: chunk.metadata.get("wiki_relations") for chunk in result.chunks}


@pytest.mark.asyncio
async def test_followed_relations_reach_both_sources_with_the_other_location() -> None:
    """辿りで見た関係（#5500）は、両端の原文に、役割を持つ側と相手の文書名・章節つきで付く。"""
    store, llm = Store(LINKED), Llm(["文書A"], units=["a2"])
    store.link_rows = [("doc-a-a2", "doc-c-c1", "exception"), ("doc-c-c1", "doc-d-d1", "reference")]
    result = await retriever(store, llm, settings=Settings(rag_wiki_link_depth=1)).search(
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
            "document_name": "doc-c",
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
    assert result.trace["model_calls"] == 2


@pytest.mark.asyncio
async def test_no_relations_are_attached_when_links_are_not_followed() -> None:
    store, llm = Store(LINKED), Llm(["文書A"], units=["a2"])
    store.link_rows = [("doc-a-a2", "doc-c-c1", "exception")]
    result = await retriever(store, llm, settings=Settings(rag_wiki_link_depth=0)).search(
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
    "Wiki: 読むファイルの選択（1 回目のモデル）",
    "Wiki: 選んだファイルの案内と順位の読み込み",
    "Wiki: 読む単位と章節の選択（2 回目のモデル）",
    "Wiki: 依存と関連の辿り",
    "Wiki: 原文の読み込み",
]


@pytest.mark.asyncio
async def test_trace_lists_the_source_chunk_ids_by_origin_and_the_stage_seconds() -> None:
    """根拠に渡した原文の chunk ID と、選んだ単位・依存・関連ごとの chunk ID を記録に残す。"""
    store, llm = Store(LINKED), Llm(["文書A"], units=["a2"])
    store.link_rows = [("doc-c-c1", "doc-a-a2", "exception")]
    seconds: dict[str, float] = {}
    result = await retriever(store, llm, settings=Settings(rag_wiki_link_depth=1)).search(
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
    store, llm = Store(PAGES), Llm(["文書A"], units=["a2"])
    result = await retriever(store, llm).search(QUESTION, profile_id="p", filters={}, timeout=None)
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

    store, llm = Broken(PAGES), Llm(["文書A"], units=["a2"])
    seconds: dict[str, float] = {}
    with pytest.raises(RuntimeError):
        await retriever(store, llm).search(
            QUESTION, profile_id="p", filters={}, timeout=None, stage_seconds=seconds
        )
    assert list(seconds) == WIKI_STAGES


@pytest.mark.asyncio
async def test_profile_without_wiki_search_records_no_measurement() -> None:
    store, llm = Store(PAGES), Llm([])
    seconds: dict[str, float] = {}
    result = await retriever(store, llm, enabled=False).search(
        QUESTION, profile_id="p", filters={}, timeout=None, stage_seconds=seconds
    )
    assert result.trace == {"status": "profile_disabled", "model_calls": 0}
    assert seconds == {}


def empty_response() -> ValueError:
    """Enterprise AI が HTTP 200 で text も usage も無い本文を返したときの例外（#5528）。"""
    from rag_parser_core.oci_enterprise_ai import _parse_generated_text

    with pytest.raises(ValueError) as caught:
        _parse_generated_text({"object": "response", "status": "completed", "output": []})
    return caught.value


class FlakyLlm(Llm):
    """1 回目の選択（目録）の最初の ``len(failures)`` 回だけ、決めた失敗を返す。"""

    def __init__(self, failures: list[Exception | str], *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.failures = list(failures)

    async def generate(self, prompt: str, data: str, **kwargs: Any) -> str:
        if "files" in json.loads(data) and self.failures:
            self.inputs.append(json.loads(data))
            failure = self.failures.pop(0)
            if isinstance(failure, Exception):
                raise failure
            return failure
        return await super().generate(prompt, data, **kwargs)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "failure",
    [
        pytest.param("empty", id="empty-response"),
        pytest.param(
            json.dumps({"document_ids": ["D99"], "search_expressions": [], "unresolved": []}),
            id="unknown-handle",
        ),
        pytest.param('{"document_ids": [', id="broken-json"),
    ],
)
async def test_invalid_selection_output_is_retried_with_the_same_input(failure: str) -> None:
    """空の応答・schema に合わない出力は、同じ入力で取り直して Wiki の原文を返す（#5528）。"""
    store = Store(PAGES)
    llm = FlakyLlm([empty_response() if failure == "empty" else failure], ["文書A"], units=["a2"])
    result = await retriever(store, llm).search(QUESTION, profile_id="p", filters={}, timeout=None)

    assert result.trace["status"] == "ready" and result.chunks
    # 取り直しも呼び出しの数に入る（1 回目の選択 2 回・2 回目の選択 1 回）。
    assert result.trace["model_calls"] == 3
    assert llm.inputs[0] == llm.inputs[1] and "files" in llm.inputs[0]


@pytest.mark.asyncio
async def test_selection_fails_after_the_retry_budget_with_the_reason() -> None:
    """取り直しは ``RAG_LLM_MAX_RETRIES`` 回まで。使い切ると例外にし、理由を区別できる。"""
    from app.rag.wiki_retrieval import failure_reason

    store = Store(PAGES)
    llm = FlakyLlm([empty_response() for _ in range(5)], ["文書A"])
    with pytest.raises(ValueError) as caught:
        await retriever(store, llm, settings=Settings(rag_llm_max_retries=2)).search(
            QUESTION, profile_id="p", filters={}, timeout=None
        )
    assert len(llm.inputs) == 3
    assert failure_reason(caught.value) == "invalid_output"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "error",
    [
        pytest.param(RuntimeError("合成の失敗"), id="unknown"),
        pytest.param(TimeoutError(), id="timeout"),
        pytest.param(ValueError("OCI Enterprise AI response error: 合成"), id="response-error"),
    ],
)
async def test_other_selection_failures_are_not_retried(error: Exception) -> None:
    store, llm = Store(PAGES), FlakyLlm([error], ["文書A"])
    with pytest.raises(type(error)):
        await retriever(store, llm).search(QUESTION, profile_id="p", filters={}, timeout=None)
    assert len(llm.inputs) == 1


def test_failure_reason_keeps_only_the_kind_and_the_schema_location() -> None:
    """記録に残す理由は種類と、schema の誤りの種類・位置だけ（入力の値・本文を入れない）。"""
    from app.rag.wiki_models import WikiFilePlan, scoped_plan
    from app.rag.wiki_retrieval import failure_reason

    schema = scoped_plan(WikiFilePlan, {"document_ids": (["D1"], 8)})
    with pytest.raises(ValueError) as caught:
        schema.model_validate_json(
            json.dumps({"document_ids": ["合成の値"], "search_expressions": [], "unresolved": []})
        )
    reason = failure_reason(caught.value)
    assert reason == "invalid_output:literal_error@document_ids.0"
    assert "合成" not in reason
    assert failure_reason(TimeoutError()) == "timeout"
    assert failure_reason(RuntimeError("合成の失敗")) == "unknown"
