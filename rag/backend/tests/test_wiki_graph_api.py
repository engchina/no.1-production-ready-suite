"""KB 詳細の Wiki の図（#5502）: 範囲の条件・段階的に開く木・関係の向き・API の 404。

SQL は実 Oracle のテスト（test_wiki_graph_store_oracle.py）で確かめ、
ここは合成の行で組み立てを確かめる。
"""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest
from fastapi import FastAPI, HTTPException

from app.api.routes import wiki_graph as routes
from app.clients.wiki_graph_store import WikiGraphStore, graph_scope, section_id
from app.clients.wiki_store import WIKI_PUBLISHED_SQL
from app.rag.cross_references import reference_name_key
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
# 別の文書への参照（#5784。文書名と章節の表記）。a1 → 文書 B の「付録」（例外）、b1 → 文書 A の
# 「第 1 章」（前提）、a2 → 範囲の外の文書 C（参照先が決まらない）。
REFERENCES = [
    (U["a1"], "synthetic-b.pdf", "付録", "exception"),
    (U["b1"], "synthetic-a.pdf", "第 1 章", "prerequisite"),
    (U["a2"], "synthetic-c.pdf", "", "reference"),
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
        if "FROM rag_documents doc" in statement:
            documents = sorted({page.unit.document_id for _, page in pages})
            if statement.startswith("SELECT COUNT(*) AS total"):
                return [{"total": len(documents)}]
            start = binds["graph_offset"]
            return [
                {"document_id": did, "file_name": DOC_NAMES[did]}
                for did in documents[start : start + binds["graph_limit"]]
            ]
        by_id = {page.unit.unit_id: page for _, page in pages}
        if "selected_page AS" in statement:
            uid = binds["graph_unit"]
            result = []
            for page in by_id.values():
                for dep in page.navigation.dependencies:
                    src, dst = page.unit.unit_id, dep.unit_id
                    if uid not in (src, dst) or src == dst or dst not in by_id:
                        continue
                    result.append(
                        {
                            "direction": "outgoing" if src == uid else "incoming",
                            "relation": dep.relation,
                            "page_id": dst if src == uid else src,
                        }
                    )
            return result
        if "SELECT DISTINCT p.document_id,d.file_name" in statement:
            return [
                {
                    "document_id": page.unit.document_id,
                    "file_name": DOC_NAMES[page.unit.document_id],
                    "heading": None,
                }
                for page in by_id.values()
            ]
        if "u.unit_id=:graph_unit" in statement:
            return [
                {"target_name": name, "target_section": section, "relation": relation}
                for source, name, section, relation in REFERENCES
                if source == binds["graph_unit"] and source in by_id
            ]
        if "r.target_key" in statement:
            keys = {value for key, value in binds.items() if key.startswith("graph_ref_key")}
            aliases = [value for key, value in binds.items() if key.startswith("graph_ref_alias")]
            return [
                {
                    "relation": relation,
                    "page_id": source,
                    "target_key": reference_name_key(name),
                    "target_section": section,
                    "document_id": by_id[source].unit.document_id,
                }
                for source, name, section, relation in REFERENCES
                if source in by_id
                and (
                    reference_name_key(name) in keys
                    or any(str(reference_name_key(name)) in alias for alias in aliases)
                )
                and by_id[source].unit.document_id != binds["graph_doc"]
            ]
        if "AS target_path" in statement:
            wanted = {v for k, v in binds.items() if k.startswith("graph_target")}
            return [
                {
                    "unit_id": p.unit.unit_id,
                    "document_id": p.unit.document_id,
                    "target_path": p.unit.section_path,
                }
                for p in by_id.values()
                if p.unit.document_id in wanted
            ]
        if "p.page_id IN" in statement or "graph_pages" in "".join(binds):
            wanted = {value for key, value in binds.items() if key.startswith("graph_pages")}
            return [
                {
                    "page_id": page.unit.unit_id,
                    "unit_id": page.unit.unit_id,
                    "document_id": page.unit.document_id,
                    "file_name": DOC_NAMES[page.unit.document_id],
                    "title": page.unit.title,
                    "section_path": json.dumps(page.unit.section_path),
                }
                for page in by_id.values()
                if page.unit.unit_id in wanted
            ]
        if "rag_wiki_graph_nodes" in statement:
            doc = binds["graph_doc"]
            children: dict[str, list[str]] = {}
            nodes = {}
            for _, page in pages:
                if page.unit.document_id != doc:
                    continue
                path = page.unit.section_path
                parent = doc
                for depth in range(1, len(path) + 2):
                    leaf = depth > len(path)
                    nid = page.unit.unit_id if leaf else section_id(doc, path[:depth])
                    siblings = children.setdefault(parent, [])
                    if nid not in nodes:
                        nodes[nid] = {
                            "node_id": nid,
                            "parent_id": parent,
                            "sibling_index": len(siblings),
                            "path_depth": -1 if leaf else depth,
                            "section_path": json.dumps(path),
                            "title": page.unit.title if leaf else None,
                            "summary": page.navigation.context if leaf else None,
                            "file_name": DOC_NAMES[doc],
                            "unit_count": 0,
                        }
                        siblings.append(nid)
                    nodes[nid]["unit_count"] += 1
                    parent = nid
            if "graph_node" in binds:
                return [nodes[binds["graph_node"]]] if binds["graph_node"] in nodes else []
            ids = children.get(binds["graph_parent"], [])
            return [
                dict(nodes[nid], total=len(ids), child_count=len(children.get(nid, [])))
                for nid in ids[binds["graph_offset"] :][: binds["graph_limit"]]
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
                    "page_id": page.unit.unit_id,
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


async def test_overview_reads_only_one_document_page_without_link_counts() -> None:
    oracle = FakeOracle()
    store = WikiGraphStore(oracle)  # type: ignore[arg-type]
    first = await store.overview(KB, limit=1)
    assert first == {
        "status": "ok",
        "has_more": True,
        "documents": [{"id": "doc-a", "document_id": "doc-a", "name": DOC_NAMES["doc-a"]}],
    }
    assert len(oracle.statements) == 1 and "rag_wiki_links" not in oracle.statements[0][0]
    assert await store.document_count(KB) == {"total": 2}
    last = await store.overview(KB, offset=1, limit=1)
    assert not last["has_more"] and last["documents"][0]["id"] == "doc-b"


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
    level = await store.children(KB, "doc-a", parent_id=chapter, offset=0, limit=100)
    section = section_id("doc-a", ["第 1 章", "第 1 節"])
    assert [item["id"] for item in level["items"]] == [section, U["a3"]]
    units = await store.children(KB, "doc-a", parent_id=section, offset=1, limit=1)
    assert units["total"] == 2 and [item["id"] for item in units["items"]] == [U["a2"]]
    first = (await store.children(KB, "doc-a", parent_id=section, offset=0, limit=1))["items"][0]
    assert first["summary"] == "単位 a1 の合成の案内。" and level["items"][0]["summary"] == ""
    assert "outgoing" not in first
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
    items = (await store.relations(KB, U["a1"], 0, 25))["items"]
    relations = {
        (item["direction"], item["scope"], item["relation"], item["unit"]["id"]) for item in items
    }
    assert relations == {
        ("outgoing", "same_document", "exception", U["a2"]),
        ("incoming", "same_document", "prerequisite", U["a3"]),
        # 別の文書への参照（#5784）: 出る向きは参照先の章節の単位、
        # 入る向きは実際に指す章節でのみ表示する。
        ("outgoing", "cross_document", "exception", U["b1"]),
    }
    incoming = (await store.relations(KB, U["a3"], 0, 25))["items"]
    assert any(i["unit"]["id"] == U["b1"] and i["direction"] == "incoming" for i in incoming)
    cross = next(
        item
        for item in items
        if item["scope"] == "cross_document" and item["direction"] == "outgoing"
    )
    assert cross["unit"]["document_name"] == "synthetic-b.pdf"
    assert (cross["target_name"], cross["target_section"]) == ("synthetic-b.pdf", "付録")
    # 章節の表記（第 1 章）は、見出しが完全に一致する単位（a3）に当たる。
    outgoing = (await store.relations(KB, U["b1"], 0, 25))["items"]
    assert [item["unit"]["id"] for item in outgoing if item["direction"] == "outgoing"] == [U["a3"]]
    # 参照先が範囲に無い参照は、原文の表記だけを返す（開けない）。
    dangling = [
        item
        for item in (await store.relations(KB, U["a2"], 0, 25))["items"]
        if item["scope"] == "cross_document" and item["direction"] == "outgoing"
    ]
    assert [(item["unit"], item["target_name"]) for item in dangling] == [(None, "synthetic-c.pdf")]
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
    overview = await routes.get_wiki_graph_overview(KB, offset=0, limit=25)
    assert overview.data is not None and overview.data.status == "ok"
    unit = await routes.get_wiki_graph_unit(KB, U["a1"])
    assert unit.data is not None and unit.data.relations == []
    with pytest.raises(HTTPException) as caught:
        await routes.locate_wiki_graph_node(KB, "doc-a", node=U["c1"])
    assert caught.value.status_code == 404
    visible["value"] = False
    with pytest.raises(HTTPException) as caught:
        await routes.get_wiki_graph_overview(KB, offset=0, limit=25)
    assert caught.value.status_code == 404
    assert WIKI_PUBLISHED_SQL in graph_scope(KB)[0]


@pytest.mark.asyncio
async def test_overview_prompts_unpaged_client_to_reload(monkeypatch: pytest.MonkeyPatch) -> None:
    class Oracle(FakeOracle):
        async def knowledge_base_is_visible(self, kb: str) -> bool:
            return True

    monkeypatch.setattr(routes, "OracleClient", Oracle)
    app = FastAPI()
    app.include_router(routes.router)
    path = f"/{KB}/wiki/graph"
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.get(path)
        assert response.status_code == 409
        assert "ブラウザーでページを再読み込み" in response.json()["detail"]
        for params in ({"offset": 0}, {"limit": 25}, {"offset": 0, "limit": 25}):
            response = await client.get(path, params=params)
            assert response.status_code == 200
            assert response.json()["data"]["documents"][0]["id"] == "doc-a"
