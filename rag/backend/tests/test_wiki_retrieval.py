"""Wiki の検索の経路（#4941・#4986・#5036・#5044）を、合成の案内と決定論のスタブで確かめる。

DB・外部モデルは使わない。
"""

from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Any

import pytest

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

    async def catalog(self, filters: dict[str, str], compiler: str) -> list[dict[str, Any]]:
        self.calls.append(("catalog", filters["knowledge_base_id"]))
        return [
            row
            for row in CATALOG
            if any(p.unit.document_id == row["document_id"] for p in self.by_id.values())
        ]

    async def pages(self, filters: dict[str, str], compiler: str, **kwargs: Any) -> list[WikiPage]:
        self.calls.append(("pages", {k: v for k, v in kwargs.items() if k != "query_vector"}))
        values = list(self.by_id.values())
        if kwargs.get("document_ids") is not None:
            values = [p for p in values if p.unit.document_id in kwargs["document_ids"]]
        if kwargs.get("unit_ids") is not None:
            values = [self.by_id[uid] for uid in kwargs["unit_ids"] if uid in self.by_id]
        return values[: kwargs["limit"]] if kwargs.get("limit") else values

    async def text_rankings(
        self, expression: str, filters: dict[str, str], compiler: str, **kwargs: Any
    ) -> Any:
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


def retriever(store: Store, llm: Llm, *, enabled: bool = True) -> WikiRetriever:
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

    return WikiRetriever(Settings(), Oracle(), GenAi(), llm, store=store)  # type: ignore[arg-type]


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
    assert store.calls[0] == ("catalog", "kb-a")
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
