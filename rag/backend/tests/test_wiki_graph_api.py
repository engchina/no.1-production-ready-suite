"""KB 詳細の Wiki の図（#5502）: 範囲の条件・段階的に開く木・関係の向き・API の 404。

SQL は実 Oracle のテスト（test_wiki_graph_store_oracle.py）で確かめ、
ここは合成の行で組み立てを確かめる。
"""

from __future__ import annotations

import json
from typing import Any

import pytest
from fastapi import HTTPException

from app.api.routes import wiki_graph as routes
from app.clients import wiki_graph_store as module
from app.clients.wiki_graph_store import WikiGraphStore, graph_scope, section_id
from app.rag.wiki_models import WikiContext, WikiDependency, WikiPage, WikiUnit, fingerprint

KB = "kb-a"


def _unit(uid: str, document: str, path: list[str], title: str) -> WikiUnit:
    return WikiUnit(
        unit_id=uid,
        document_id=document,
        chunk_set_id=f"set-{document}",
        source_chunk_ids=[f"chunk-{uid[:6]}"],
        section_id=fingerprint(path),
        section_path=path,
        title=title,
        text="合成の原文。",
        source_hash=fingerprint(uid),
    )


def _page(unit: WikiUnit, deps: list[tuple[str, str]] = ()) -> WikiPage:  # type: ignore[assignment]
    return WikiPage(
        unit=unit,
        navigation=WikiContext(
            unit_id=unit.unit_id,
            context=f"{unit.title} の合成の案内。",
            possible_questions=["合成の質問？"],
            dependencies=[
                WikiDependency(unit_id=target, relation=relation) for target, relation in deps
            ],
            unresolved=["合成の未確認の点。"],
        ),
        input_fingerprint="input-a",
    )


U = {name: fingerprint(name) for name in ("a1", "a2", "a3", "a4", "b1", "c1")}
PAGES = {
    # 文書 A: 第 1 章 > 第 1 節 に a1・a2、第 1 章の直下に a3、章の無い a4。
    "a1": _page(
        _unit(U["a1"], "doc-a", ["第 1 章", "第 1 節"], "単位 a1"), [(U["a2"], "exception")]
    ),
    "a2": _page(
        _unit(U["a2"], "doc-a", ["第 1 章", "第 1 節"], "単位 a2"),
        # 今の保存版に無い単位・自分への依存は引かない。
        [(fingerprint("gone"), "reference"), (U["a2"], "warning")],
    ),
    "a3": _page(_unit(U["a3"], "doc-a", ["第 1 章"], "単位 a3"), [(U["a1"], "prerequisite")]),
    "a4": _page(_unit(U["a4"], "doc-a", [], "単位 a4")),
    "b1": _page(_unit(U["b1"], "doc-b", ["付録"], "単位 b1")),
    # 範囲の外の文書（関連の終点が見えない）。
    "c1": _page(_unit(U["c1"], "doc-c", [], "単位 c1")),
}
VISIBLE = {"a1", "a2", "a3", "a4", "b1"}
DOC_NAMES = {"doc-a": "synthetic-a.pdf", "doc-b": "synthetic-b.pdf", "doc-c": "synthetic-c.pdf"}
# a1 → b1（b1 は a1 の例外）、b1 → a3（a3 は b1 の前提）、a2 → c1（c1 は範囲の外）。
LINKS = [
    (U["a1"], U["b1"], "doc-a", "doc-b", "exception", "合成の理由"),
    (U["b1"], U["a3"], "doc-b", "doc-a", "prerequisite", ""),
]


class FakeOracle:
    """SQL の形で合成の行を返す。範囲の外の案内（c1）は照会の結果に出さない。"""

    def __init__(self, *, empty: bool = False) -> None:
        self.statements: list[tuple[str, dict[str, Any]]] = []
        self.visible = set() if empty else set(VISIBLE)

    def _pages(self) -> list[tuple[int, WikiPage]]:
        return [(index, PAGES[name]) for index, name in enumerate(PAGES) if name in self.visible]

    async def _fetch_one(self, statement: str, binds: Any = None) -> Any:
        rows = await self._fetch_all(statement, binds)
        return rows[0] if rows else None

    async def _fetch_all(self, statement: str, binds: Any = None) -> list[dict[str, Any]]:
        binds = dict(binds or {})
        self.statements.append((statement, binds))
        pages = self._pages()
        if "GROUP BY p.document_id" in statement:
            docs: dict[str, int] = {}
            for _, page in pages:
                docs[page.unit.document_id] = docs.get(page.unit.document_id, 0) + 1
            return [
                {"document_id": did, "file_name": DOC_NAMES[did], "unit_count": n}
                for did, n in sorted(docs.items())
            ]
        if "FROM rag_wiki_links l" in statement:
            visible_units = {page.unit.unit_id for _, page in pages}
            rows = [link for link in LINKS if link[0] in visible_units and link[1] in visible_units]
            if "graph_doc" in binds:
                rows = [link for link in rows if binds["graph_doc"] in (link[2], link[3])]
            if "graph_unit" in binds:
                rows = [link for link in rows if binds["graph_unit"] in (link[0], link[1])]
            if "GROUP BY l.source_document_id" in statement:
                return [
                    {"source_document_id": s, "target_document_id": t, "link_count": 1}
                    for _, _, s, t, _, _ in rows
                ]
            return [
                {
                    "source_unit_id": s,
                    "target_unit_id": t,
                    "source_document_id": sd,
                    "target_document_id": td,
                    "relation": relation,
                    "reason": reason,
                }
                for s, t, sd, td, relation, reason in rows
            ]
        if "'$.navigation.dependencies'" in statement:
            return [
                {
                    "unit_id": page.unit.unit_id,
                    "chunk_index": index,
                    "file_name": DOC_NAMES[page.unit.document_id],
                    "title": page.unit.title,
                    "context": page.navigation.context,
                    "section_path": json.dumps(page.unit.section_path, ensure_ascii=False),
                    "dependencies": json.dumps(
                        [item.model_dump() for item in page.navigation.dependencies]
                    ),
                }
                for index, page in pages
                if page.unit.document_id == binds["graph_doc"]
            ]
        if "COUNT(*) OVER ()" in statement:
            needle = str(binds["graph_pattern"]).strip("%")
            found = [
                page
                for _, page in pages
                if needle in page.unit.title.casefold() or needle in page.navigation.context
            ]
            return [
                {
                    "unit_id": page.unit.unit_id,
                    "document_id": page.unit.document_id,
                    "file_name": DOC_NAMES[page.unit.document_id],
                    "title": page.unit.title,
                    "total_count": len(found),
                }
                for page in found
            ]
        if "p.page_json" in statement:
            wanted = {value for key, value in binds.items() if key.startswith("graph_units")}
            return [
                {
                    "unit_id": page.unit.unit_id,
                    "document_id": page.unit.document_id,
                    "file_name": DOC_NAMES[page.unit.document_id],
                    "page_json": page.model_dump_json(),
                }
                for _, page in pages
                if page.unit.unit_id in wanted
            ]
        if "FROM rag_chunks c WHERE" in statement:
            return [
                {"chunk_id": value, "chunk_index": 0}
                for key, value in binds.items()
                if key.startswith("graph_chunks")
            ]
        raise AssertionError(statement)


def test_scope_reads_saved_guidance_of_kb_documents_without_requiring_enabled_build() -> None:
    where, binds = graph_scope(KB)
    # 所属・権限は検索と同じ判定（KB の ID を束縛）。Wiki の構築の指定は条件にしない。
    assert KB in binds.values()
    assert "rag_document_knowledge_bases" in where
    assert "wiki_build_enabled" not in where
    # 公開済みで今の原文の保存版の案内だけ。構築の条件の指紋（モデル）は問わない。
    assert "w.published_fingerprint=w.source_fingerprint" in where
    assert "current_set.is_active=1" in where
    assert "wiki_compiler" not in where


async def test_overview_counts_units_and_cross_document_links_in_both_directions() -> None:
    oracle = FakeOracle()
    data = await WikiGraphStore(oracle).overview(KB)  # type: ignore[arg-type]
    assert data["status"] == "ok"
    docs = {doc["document_id"]: doc for doc in data["documents"]}
    assert docs["doc-a"]["unit_count"] == 4 and docs["doc-b"]["unit_count"] == 1
    assert (docs["doc-a"]["outgoing_links"], docs["doc-a"]["incoming_links"]) == (1, 1)
    assert (docs["doc-b"]["outgoing_links"], docs["doc-b"]["incoming_links"]) == (1, 1)
    # 関連の両端を、範囲の中の公開済みの案内に限る。
    link_sql = [sql for sql, _ in oracle.statements if "rag_wiki_links" in sql]
    assert link_sql and all("JOIN visible s" in sql and "JOIN visible t" in sql for sql in link_sql)
    assert (await WikiGraphStore(FakeOracle(empty=True)).overview(KB))["status"] == "empty"  # type: ignore[arg-type]


async def test_tree_opens_documents_sections_and_units_step_by_step() -> None:
    store = WikiGraphStore(FakeOracle())  # type: ignore[arg-type]
    root = await store.children(KB, "doc-a", parent_id=None, offset=0, limit=100)
    chapter = section_id("doc-a", ["第 1 章"])
    assert [(item["id"], item["kind"]) for item in root["items"]] == [
        (chapter, "section"),
        (U["a4"], "unit"),
    ]
    # 章節は配下の単位の数と、配下の文書をまたぐ関連の数を持つ。
    assert root["items"][0]["unit_count"] == 3 and root["items"][0]["child_count"] == 2
    assert (root["items"][0]["outgoing_links"], root["items"][0]["incoming_links"]) == (1, 1)
    level = await store.children(KB, "doc-a", parent_id=chapter, offset=0, limit=100)
    section = section_id("doc-a", ["第 1 章", "第 1 節"])
    assert [item["id"] for item in level["items"]] == [section, U["a3"]]
    units = await store.children(KB, "doc-a", parent_id=section, offset=1, limit=1)
    assert units["total"] == 2 and [item["id"] for item in units["items"]] == [U["a2"]]
    first = (await store.children(KB, "doc-a", parent_id=section, offset=0, limit=1))["items"][0]
    assert first["summary"] == "単位 a1 の合成の案内。" and level["items"][0]["summary"] == ""
    # 単位の線: 同じ文書の依存と、文書をまたぐ関連（どちらも起点 → 終点）。
    assert first["outgoing"] == [
        {"target": U["a2"], "relation": "exception", "scope": "same_document"},
        {"target": U["b1"], "relation": "exception", "scope": "cross_document"},
    ]
    # 範囲の外への関連・今の保存版に無い単位・自分への依存は引かない。
    assert units["items"][0]["outgoing"] == []
    with pytest.raises(KeyError):
        await store.children(KB, "doc-a", parent_id=U["a1"], offset=0, limit=100)
    with pytest.raises(KeyError):
        await store.children(KB, "doc-c", parent_id=None, offset=0, limit=100)


async def test_locate_returns_steps_from_document_to_node() -> None:
    store = WikiGraphStore(FakeOracle())  # type: ignore[arg-type]
    located = await store.locate(KB, "doc-a", U["a2"])
    assert located["path"] == [
        {"id": section_id("doc-a", ["第 1 章"]), "index": 0},
        {"id": section_id("doc-a", ["第 1 章", "第 1 節"]), "index": 0},
        {"id": U["a2"], "index": 1},
    ]
    with pytest.raises(KeyError):
        await store.locate(KB, "doc-a", U["b1"])


async def test_unit_detail_separates_directions_and_hides_relations_outside_scope() -> None:
    store = WikiGraphStore(FakeOracle())  # type: ignore[arg-type]
    detail = await store.unit(KB, U["a1"])
    assert detail["context"] == "単位 a1 の合成の案内。"
    assert detail["possible_questions"] == ["合成の質問？"]
    assert detail["unresolved"] == ["合成の未確認の点。"]
    assert detail["source_chunks"] == [{"chunk_id": f"chunk-{U['a1'][:6]}", "chunk_index": 0}]
    relations = {
        (item["direction"], item["scope"], item["relation"], item["unit"]["id"])
        for item in detail["relations"]
    }
    assert relations == {
        ("outgoing", "same_document", "exception", U["a2"]),
        ("incoming", "same_document", "prerequisite", U["a3"]),
        ("outgoing", "cross_document", "exception", U["b1"]),
    }
    cross = next(item for item in detail["relations"] if item["scope"] == "cross_document")
    assert cross["unit"]["document_name"] == "synthetic-b.pdf" and cross["reason"] == "合成の理由"
    incoming = await store.unit(KB, U["a3"])
    assert ("incoming", "cross_document", U["b1"]) in {
        (item["direction"], item["scope"], item["unit"]["id"]) for item in incoming["relations"]
    }
    # 終点が範囲の外の関連は出さない。範囲の外の単位は 404。
    assert all(
        item["unit"]["id"] != U["c1"] for item in (await store.unit(KB, U["a2"]))["relations"]
    )
    with pytest.raises(KeyError):
        await store.unit(KB, U["c1"])


async def test_search_matches_unit_titles_and_guidance() -> None:
    store = WikiGraphStore(FakeOracle())  # type: ignore[arg-type]
    found = await store.search(KB, "単位 A3", offset=0, limit=25)
    assert found["total"] == 1 and found["items"][0]["id"] == U["a3"]
    assert (await store.search(KB, "  ", offset=0, limit=25))["total"] == 0


async def test_routes_hide_invisible_knowledge_base_and_unknown_nodes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    visible = {"value": True}

    class Oracle(FakeOracle):
        async def knowledge_base_is_visible(self, kb: str) -> bool:
            return visible["value"]

    monkeypatch.setattr(routes, "OracleClient", Oracle)
    tree = await routes.get_wiki_graph_tree(KB, "doc-a", parent=None, offset=0, limit=100)
    assert tree.data is not None and tree.data.total == 2
    overview = await routes.get_wiki_graph_overview(KB)
    assert overview.data is not None and overview.data.status == "ok"
    unit = await routes.get_wiki_graph_unit(KB, U["a1"])
    assert unit.data is not None and len(unit.data.relations) == 3
    with pytest.raises(HTTPException) as caught:
        await routes.locate_wiki_graph_node(KB, "doc-a", node=U["c1"])
    assert caught.value.status_code == 404
    visible["value"] = False
    with pytest.raises(HTTPException) as caught:
        await routes.get_wiki_graph_overview(KB)
    assert caught.value.status_code == 404
    assert module.GRAPH_PUBLISHED_SQL in graph_scope(KB)[0]
