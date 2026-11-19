"""KB 詳細の Wiki の図の読み取り（#5502）。

保存済みの案内（``rag_wiki_pages``）と文書をまたぐ関連（``rag_wiki_links``）を、
文書 → 章節 → 原文単位の図の形で返す。範囲は Wiki の文書ごとの状態と同じ条件
（今の利用者が読める、この KB に所属する文書）で、Wiki の構築の指定が無効でも保存済みの案内は出す。
案内は今の原文の保存版で公開済みのものだけを出す。
検索・回答の経路（``WikiStore``）の照会とは分け、画面の読み取りだけを置く。
"""

from __future__ import annotations

import json
import unicodedata
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any

from app.clients.oracle import (
    OracleClient,
    _int_value,
    _json_list,
    _json_loads,
    _like_pattern,
    _oracle_in_predicate,
    _oracle_retrieval_where,
)
from app.clients.wiki_store import CURRENT_WIKI_SOURCE_SQL, WIKI_PAGE_FROM
from app.rag.wiki_models import WikiPage, fingerprint

# 公開済みで、今の原文の保存版に結び付く案内。構築の条件（モデル・指示）は問わない: 条件が変わって
# 作り直す前でも、利用者は保存済みの案内を確かめられる（Wiki を無効にしたときも同じ）。
GRAPH_PUBLISHED_SQL = f"""w.status='READY' AND w.published_fingerprint=w.source_fingerprint
AND p.source_fingerprint=w.published_fingerprint AND {CURRENT_WIKI_SOURCE_SQL}"""


def graph_scope(kb_id: str) -> tuple[str, dict[str, object]]:
    """図に出す案内の条件（``WIKI_PAGE_FROM`` の別名）。KB の所属・利用者の権限は検索と同じ判定。"""
    where, binds = _oracle_retrieval_where({"knowledge_base_id": kb_id})
    return f"({where}) AND {GRAPH_PUBLISHED_SQL}", binds


def visible_pages_cte(kb_id: str) -> tuple[str, dict[str, object]]:
    """範囲の中の公開済みの案内の集合（関連の両端の照合に 1 回だけ作る）。"""
    where, binds = graph_scope(kb_id)
    return (
        f"""visible AS (SELECT p.page_id,p.unit_id,p.document_id
        {WIKI_PAGE_FROM} WHERE {where})""",
        binds,
    )


def section_id(document_id: str, path: Sequence[str]) -> str:
    return fingerprint(["wiki-graph-section", document_id, list(path)])


def _dependencies(value: object) -> list[dict[str, Any]]:
    """``navigation.dependencies`` の JSON の配列（読めない値は空）。"""
    try:
        loaded = json.loads(str(value)) if value else []
    except json.JSONDecodeError:
        return []
    return [item for item in loaded if isinstance(item, dict)] if isinstance(loaded, list) else []


@dataclass
class _Node:
    id: str
    kind: str
    label: str
    path: list[str]
    parent_id: str | None
    order: int
    children: list[str] = field(default_factory=list)
    units: int = 0
    outgoing_links: int = 0
    incoming_links: int = 0
    outgoing: list[dict[str, str]] = field(default_factory=list)
    # 単位の案内の要約（同じ章節の単位は見出しが同じなので、図ではこれで見分ける）。
    summary: str = ""


@dataclass
class _DocumentTree:
    document_id: str
    name: str
    nodes: dict[str, _Node]
    root: list[str]
    # 単位 ID → 同じ文書の依存（終点の単位 ID, 関係）。
    dependencies: dict[str, list[tuple[str, str]]]


class WikiGraphStore:
    def __init__(self, oracle: OracleClient) -> None:
        self.oracle = oracle

    # ---- 文書をまたぐ関連 ----

    async def _links(
        self,
        kb_id: str,
        *,
        document_id: str | None = None,
        unit_id: str | None = None,
    ) -> list[dict[str, Any]]:
        """両端の案内が範囲にあり公開済みの関連。

        向きは起点 → 終点で、関係は終点が起点に対して持つ役割（#5492 の判定と同じ）。
        """
        cte, binds = visible_pages_cte(kb_id)
        where = "1=1"
        if document_id is not None:
            where = "(l.source_document_id=:graph_doc OR l.target_document_id=:graph_doc)"
            binds = {**binds, "graph_doc": document_id}
        if unit_id is not None:
            where = "(l.source_unit_id=:graph_unit OR l.target_unit_id=:graph_unit)"
            binds = {**binds, "graph_unit": unit_id}
        rows = await self.oracle._fetch_all(
            f"""WITH {cte}
            SELECT l.source_unit_id,l.target_unit_id,l.source_document_id,l.target_document_id,
            l.relation,l.reason FROM rag_wiki_links l
            JOIN visible s ON s.page_id=l.source_page_id
            JOIN visible t ON t.page_id=l.target_page_id
            WHERE {where} ORDER BY l.source_unit_id,l.target_unit_id""",
            binds,
        )
        return [
            {
                "source": str(row["source_unit_id"]),
                "target": str(row["target_unit_id"]),
                "source_document_id": str(row["source_document_id"]),
                "target_document_id": str(row["target_document_id"]),
                "relation": str(row["relation"]),
                "reason": str(row.get("reason") or ""),
            }
            for row in rows
        ]

    # ---- 総覧 ----

    async def overview(self, kb_id: str) -> dict[str, Any]:
        """範囲の中で案内のある文書と、文書ごとの単位の数・文書をまたぐ関連の数。"""
        where, binds = graph_scope(kb_id)
        rows = await self.oracle._fetch_all(
            f"""SELECT p.document_id,MAX(d.file_name) AS file_name,COUNT(*) AS unit_count
            {WIKI_PAGE_FROM} WHERE {where}
            GROUP BY p.document_id ORDER BY MAX(d.file_name),p.document_id""",
            binds,
        )
        if not rows:
            return {"status": "empty", "documents": []}
        cte, link_binds = visible_pages_cte(kb_id)
        counts = await self.oracle._fetch_all(
            f"""WITH {cte}
            SELECT l.source_document_id,l.target_document_id,COUNT(*) AS link_count
            FROM rag_wiki_links l
            JOIN visible s ON s.page_id=l.source_page_id
            JOIN visible t ON t.page_id=l.target_page_id
            GROUP BY l.source_document_id,l.target_document_id""",
            link_binds,
        )
        outgoing: dict[str, int] = {}
        incoming: dict[str, int] = {}
        for row in counts:
            count = _int_value(row["link_count"])
            source, target = str(row["source_document_id"]), str(row["target_document_id"])
            outgoing[source] = outgoing.get(source, 0) + count
            incoming[target] = incoming.get(target, 0) + count
        return {
            "status": "ok",
            "documents": [
                {
                    "id": str(row["document_id"]),
                    "document_id": str(row["document_id"]),
                    "name": str(row.get("file_name") or ""),
                    "unit_count": _int_value(row["unit_count"]),
                    "outgoing_links": outgoing.get(str(row["document_id"]), 0),
                    "incoming_links": incoming.get(str(row["document_id"]), 0),
                }
                for row in rows
            ],
        }

    # ---- 文書の中の木 ----

    async def _document_tree(self, kb_id: str, document_id: str) -> _DocumentTree:
        """文書の公開済みの案内から、章節（``section_path`` の階層）と原文単位の木を作る。

        本文（原文）は読まない。見えない文書・案内の無い文書は ``KeyError``。
        """
        where, binds = graph_scope(kb_id)
        rows = await self.oracle._fetch_all(
            f"""SELECT p.unit_id,c.chunk_index,d.file_name,
            JSON_VALUE(p.page_json,'$.unit.title' RETURNING VARCHAR2(4000)) AS title,
            JSON_VALUE(p.page_json,'$.navigation.context' RETURNING VARCHAR2(4000)) AS context,
            JSON_QUERY(p.page_json,'$.unit.section_path' RETURNING CLOB) AS section_path,
            JSON_QUERY(p.page_json,'$.navigation.dependencies' RETURNING CLOB) AS dependencies
            {WIKI_PAGE_FROM} WHERE {where} AND p.document_id=:graph_doc
            ORDER BY c.chunk_index,p.unit_id""",
            {**binds, "graph_doc": document_id},
        )
        if not rows:
            raise KeyError(document_id)
        nodes: dict[str, _Node] = {}
        root: list[str] = []
        units = {str(row["unit_id"]) for row in rows}
        dependencies: dict[str, list[tuple[str, str]]] = {}
        for order, row in enumerate(rows):
            unit_id = str(row["unit_id"])
            path = _json_list(row.get("section_path"))
            parent: str | None = None
            siblings = root
            for depth in range(len(path)):
                sid = section_id(document_id, path[: depth + 1])
                if sid not in nodes:
                    nodes[sid] = _Node(
                        sid, "section", path[depth], path[: depth + 1], parent, order
                    )
                    siblings.append(sid)
                nodes[sid].units += 1
                parent, siblings = sid, nodes[sid].children
            nodes[unit_id] = _Node(
                unit_id,
                "unit",
                str(row.get("title") or ""),
                path,
                parent,
                order,
                units=1,
                summary=str(row.get("context") or ""),
            )
            siblings.append(unit_id)
            # 終点は同じ文書の公開済みの単位だけ（今の保存版に無い単位への依存は引かない）。
            dependencies[unit_id] = [
                (str(item.get("unit_id")), str(item.get("relation")))
                for item in _dependencies(row.get("dependencies"))
                if str(item.get("unit_id")) in units and str(item.get("unit_id")) != unit_id
            ]
            for target, relation in dependencies[unit_id]:
                nodes[unit_id].outgoing.append(
                    {"target": target, "relation": relation, "scope": "same_document"}
                )
        for link in await self._links(kb_id, document_id=document_id):
            for unit_id, key in (
                (link["source"], "outgoing_links"),
                (link["target"], "incoming_links"),
            ):
                node = nodes.get(unit_id)
                while node is not None:
                    setattr(node, key, getattr(node, key) + 1)
                    node = nodes.get(node.parent_id) if node.parent_id else None
            if link["source"] in nodes:
                nodes[link["source"]].outgoing.append(
                    {
                        "target": link["target"],
                        "relation": link["relation"],
                        "scope": "cross_document",
                    }
                )
        return _DocumentTree(
            document_id, str(rows[0].get("file_name") or ""), nodes, root, dependencies
        )

    @staticmethod
    def _node_view(tree: _DocumentTree, node: _Node) -> dict[str, Any]:
        return {
            "id": node.id,
            "kind": node.kind,
            "label": node.label,
            "path": node.path,
            "document_id": tree.document_id,
            "document_name": tree.name,
            "parent_id": node.parent_id,
            "child_count": len(node.children),
            "unit_count": node.units,
            "outgoing_links": node.outgoing_links,
            "incoming_links": node.incoming_links,
            "outgoing": node.outgoing,
            "summary": node.summary,
        }

    async def children(
        self, kb_id: str, document_id: str, *, parent_id: str | None, offset: int, limit: int
    ) -> dict[str, Any]:
        """木の 1 段（文書の順）。``parent_id`` が無ければ文書の直下。"""
        tree = await self._document_tree(kb_id, document_id)
        if parent_id is None:
            ids = tree.root
        elif parent_id in tree.nodes and tree.nodes[parent_id].kind == "section":
            ids = tree.nodes[parent_id].children
        else:
            raise KeyError(parent_id)
        return {
            "items": [
                self._node_view(tree, tree.nodes[node_id])
                for node_id in ids[offset : offset + limit]
            ],
            "total": len(ids),
            "offset": offset,
        }

    async def locate(self, kb_id: str, document_id: str, node_id: str) -> dict[str, Any]:
        """文書の直下から節点までの段と、各段の中の位置（最後が節点）。"""
        tree = await self._document_tree(kb_id, document_id)
        if node_id not in tree.nodes:
            raise KeyError(node_id)
        path: list[dict[str, Any]] = []
        node: _Node | None = tree.nodes[node_id]
        while node is not None:
            parent = tree.nodes.get(node.parent_id) if node.parent_id else None
            siblings = parent.children if parent else tree.root
            path.append({"id": node.id, "index": siblings.index(node.id)})
            node = parent
        return {"path": list(reversed(path))}

    # ---- 検索 ----

    async def search(self, kb_id: str, query: str, *, offset: int, limit: int) -> dict[str, Any]:
        """原文単位の見出し・章節・案内の部分一致（文書の名前と文書の順）。"""
        text = unicodedata.normalize("NFKC", query).strip().casefold()
        if not text:
            return {"items": [], "total": 0, "offset": offset}
        where, binds = graph_scope(kb_id)
        rows = await self.oracle._fetch_all(
            f"""SELECT matched.*,COUNT(*) OVER () AS total_count FROM (
                SELECT p.unit_id,p.document_id,d.file_name,c.chunk_index,
                JSON_VALUE(p.page_json,'$.unit.title' RETURNING VARCHAR2(4000)) AS title,
                JSON_QUERY(p.page_json,'$.unit.section_path' RETURNING VARCHAR2(4000)) AS path_text,
                JSON_VALUE(p.page_json,'$.navigation.context' RETURNING VARCHAR2(4000)) AS context
                {WIKI_PAGE_FROM} WHERE {where}) matched
            WHERE LOWER(title) LIKE :graph_pattern ESCAPE '\\'
               OR LOWER(path_text) LIKE :graph_pattern ESCAPE '\\'
               OR LOWER(context) LIKE :graph_pattern ESCAPE '\\'
            ORDER BY file_name,document_id,chunk_index,unit_id
            OFFSET :graph_skip ROWS FETCH NEXT :graph_take ROWS ONLY""",
            {
                **binds,
                "graph_pattern": _like_pattern(text),
                "graph_skip": offset,
                "graph_take": limit,
            },
        )
        return {
            "items": [
                {
                    "id": str(row["unit_id"]),
                    "label": str(row.get("title") or ""),
                    "document_id": str(row["document_id"]),
                    "document_name": str(row.get("file_name") or ""),
                }
                for row in rows
            ],
            "total": _int_value(rows[0]["total_count"]) if rows else 0,
            "offset": offset,
        }

    # ---- 単位の案内 ----

    async def _pages(self, kb_id: str, unit_ids: Sequence[str]) -> dict[str, dict[str, Any]]:
        if not unit_ids:
            return {}
        where, binds = graph_scope(kb_id)
        clause, extra = _oracle_in_predicate("p.unit_id", "graph_units", list(unit_ids))
        rows = await self.oracle._fetch_all(
            f"""SELECT p.unit_id,p.document_id,d.file_name,p.page_json
            {WIKI_PAGE_FROM} WHERE {where} AND {clause}""",
            {**binds, **extra},
        )
        return {
            str(row["unit_id"]): {
                "page": WikiPage.model_validate(_json_loads(row["page_json"])),
                "document_name": str(row.get("file_name") or ""),
            }
            for row in rows
        }

    async def unit(self, kb_id: str, unit_id: str) -> dict[str, Any]:
        """単位の案内（要約・想定の質問・本文に無いこと）と、原文の位置と、出る・入る関係。"""
        found = await self._pages(kb_id, [unit_id])
        if unit_id not in found:
            raise KeyError(unit_id)
        page: WikiPage = found[unit_id]["page"]
        document_id = page.unit.document_id
        tree = await self._document_tree(kb_id, document_id)
        relations: list[dict[str, Any]] = []
        for target, relation in tree.dependencies.get(unit_id, []):
            relations.append(
                {
                    "direction": "outgoing",
                    "relation": relation,
                    "scope": "same_document",
                    "unit_id": target,
                }
            )
        for source, items in tree.dependencies.items():
            for target, relation in items:
                if target == unit_id:
                    relations.append(
                        {
                            "direction": "incoming",
                            "relation": relation,
                            "scope": "same_document",
                            "unit_id": source,
                        }
                    )
        for link in await self._links(kb_id, unit_id=unit_id):
            outgoing = link["source"] == unit_id
            relations.append(
                {
                    "direction": "outgoing" if outgoing else "incoming",
                    "relation": link["relation"],
                    "scope": "cross_document",
                    "unit_id": link["target"] if outgoing else link["source"],
                    "reason": link["reason"] or None,
                }
            )
        others = await self._pages(
            kb_id,
            sorted({item["unit_id"] for item in relations if item["unit_id"] not in tree.nodes}),
        )
        for item in relations:
            node = tree.nodes.get(item["unit_id"])
            if node is not None:
                item["unit"] = {
                    "id": node.id,
                    "label": node.label,
                    "path": node.path,
                    "document_id": document_id,
                    "document_name": tree.name,
                }
            elif item["unit_id"] in others:
                other: WikiPage = others[item["unit_id"]]["page"]
                item["unit"] = {
                    "id": other.unit.unit_id,
                    "label": other.unit.title,
                    "path": other.unit.section_path,
                    "document_id": other.unit.document_id,
                    "document_name": others[item["unit_id"]]["document_name"],
                }
        chunks = await self._source_chunks(page.unit.source_chunk_ids)
        return {
            "id": unit_id,
            "label": page.unit.title,
            "path": page.unit.section_path,
            "document_id": document_id,
            "document_name": found[unit_id]["document_name"],
            "context": page.navigation.context,
            "possible_questions": page.navigation.possible_questions,
            "unresolved": page.navigation.unresolved,
            "source_chunks": chunks,
            "relations": [
                {key: value for key, value in item.items() if key != "unit_id"}
                for item in relations
                if "unit" in item
            ],
        }

    async def _source_chunks(self, chunk_ids: Sequence[str]) -> list[dict[str, Any]]:
        """原文の位置（保存順）。案内が範囲にあることを確かめた後に読む。"""
        clause, binds = _oracle_in_predicate("c.chunk_id", "graph_chunks", list(chunk_ids))
        rows = await self.oracle._fetch_all(
            f"""SELECT c.chunk_id,c.chunk_index FROM rag_chunks c WHERE {clause}
            ORDER BY c.chunk_index,c.chunk_id""",
            binds,
        )
        return [
            {"chunk_id": str(row["chunk_id"]), "chunk_index": _int_value(row["chunk_index"])}
            for row in rows
        ]
