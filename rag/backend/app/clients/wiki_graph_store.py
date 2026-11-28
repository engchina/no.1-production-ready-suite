"""KB 詳細の Wiki の図の読み取り（#5502）。

保存済みの案内（``rag_wiki_pages``）と別の文書への参照（``rag_wiki_references``）を、
文書 → 章節 → 原文単位の図の形で返す。範囲は Wiki の文書ごとの状態と同じ条件
（今の利用者が読める、この KB に所属する文書）で、Wiki の構築の指定が無効でも保存済みの案内は出す。
案内は今の原文の保存版で公開済みのものだけを出す。
検索・回答の経路（``WikiStore``）の照会とは分け、画面の読み取りだけを置く。
"""

from __future__ import annotations

import asyncio
import unicodedata
from collections import Counter, OrderedDict
from collections.abc import Awaitable, Sequence
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
from app.clients.oracle_read_stats import read_lobs_as_values
from app.clients.wiki_graph_index import section_id as section_id
from app.clients.wiki_reference_index import DocumentNames, ReferenceTargets
from app.clients.wiki_store import WIKI_PAGE_FROM, WIKI_PUBLISHED_SQL
from app.rag.wiki_models import WikiPage


def graph_scope(kb_id: str) -> tuple[str, dict[str, object]]:
    """図に出す案内の条件（``WIKI_PAGE_FROM`` の別名）。KB の所属・利用者の権限は検索と同じ判定。"""
    where, binds = _oracle_retrieval_where({"knowledge_base_id": kb_id})
    return f"({where}) AND {WIKI_PUBLISHED_SQL}", binds


def visible_pages_cte(kb_id: str) -> tuple[str, dict[str, object]]:
    """範囲の中の公開済みの案内の集合（関連の両端の照合に 1 回だけ作る）。"""
    where, binds = graph_scope(kb_id)
    return (
        f"""visible AS (SELECT p.page_id,p.unit_id,p.document_id
        {WIKI_PAGE_FROM} WHERE {where})""",
        binds,
    )


# 図の範囲（公開済みの案内・文書の名前）を左右する表。どれかの行が変わると、件数か
# ORA_ROWSCN の最大が変わる。案内（rag_wiki_pages）・節点・依存・参照は、構築の所有（RUNNING）と
# 公開（READY）で必ず rag_wiki_documents を同じ transaction で更新するので、ここに並べない。
# 利用者・tenant・権限・基準日は範囲の SQL と bind に入るので、キャッシュの鍵で分かれる。
_SCOPE_TABLES = (
    "rag_documents",
    "rag_document_knowledge_bases",
    "rag_knowledge_bases",
    "rag_chunk_sets",
    "rag_document_recipes",
    "rag_wiki_documents",
)
_SCOPE_VERSION_SQL = (
    "SELECT "
    + ",".join(
        f"(SELECT COUNT(*)||':'||MAX(ORA_ROWSCN) FROM {table}) AS v{index}"
        for index, table in enumerate(_SCOPE_TABLES)
    )
    + " FROM dual"
)
# ponytail: プロセスごとの LRU（範囲 8 つ）。変更の直後に同時に来た要求は、それぞれが読み直す
# （1 回の読み込みにまとめない）。範囲の数が増えて読み直しが重なるなら、鍵ごとの読み込みを共有する。
_SCOPE_CACHE_SIZE = 8
_SCOPES: OrderedDict[tuple[object, ...], GraphScope] = OrderedDict()


@dataclass
class GraphScope:
    """範囲の中の公開済みの案内（#5966）。DB の変更（``version``）まで要求をまたいで使い回す。

    関係の照会ごとに範囲を照合し直すと（案内・Wiki の文書・chunk・文書を結ぶ照合）、1 要求で
    数本の照合が重なり、並行の要求で読み取りの上限に近づいた。照合は版ごとに 1 回にし、
    依存・参照は案内の ID で索引から引いて、相手が範囲にあるかをここで確かめる。
    """

    version: tuple[object, ...]
    units: dict[str, tuple[str, str]]  # 単位 → (案内, 文書)
    pages: dict[str, tuple[str, str]]  # 案内 → (単位, 文書)
    names: DocumentNames
    # 参照先の章節の候補。参照先になった文書の分だけ、最初に要るときに読む。
    targets: ReferenceTargets = field(default_factory=lambda: ReferenceTargets([]))


class WikiGraphStore:
    def __init__(self, oracle: OracleClient) -> None:
        self.oracle = oracle

    async def _scope(self, kb_id: str) -> GraphScope:
        """範囲の案内の集合と文書の名前。DB が前の読み込みから変わっていなければ使い回す。"""
        where, binds = graph_scope(kb_id)
        key = (where, *sorted((name, str(value)) for name, value in binds.items()))
        # 版は範囲より先に読む。間に変更が入っても、新しい範囲に古い版が付くだけで、次の要求で
        # 読み直す（古い範囲を新しい版で使い回すことはない）。
        row = await self.oracle._fetch_one(_SCOPE_VERSION_SQL, {})
        version = tuple(row.values()) if row else ()
        cached = _SCOPES.get(key)
        if cached is not None and version and cached.version == version:
            _SCOPES.move_to_end(key)
            return cached
        cte, cte_binds = visible_pages_cte(kb_id)
        rows, names = await asyncio.gather(
            self.oracle._fetch_all(
                f"WITH {cte} SELECT page_id,unit_id,document_id FROM visible", cte_binds
            ),
            self._document_names(kb_id),
        )
        scope = GraphScope(
            version,
            {str(r["unit_id"]): (str(r["page_id"]), str(r["document_id"])) for r in rows},
            {str(r["page_id"]): (str(r["unit_id"]), str(r["document_id"])) for r in rows},
            names,
        )
        if version:
            _SCOPES[key] = scope
            _SCOPES.move_to_end(key)
            while len(_SCOPES) > _SCOPE_CACHE_SIZE:
                _SCOPES.popitem(last=False)
        return scope

    # ---- 総覧 ----

    @staticmethod
    def _document_scope(kb_id: str) -> tuple[str, dict[str, object]]:
        where, binds = graph_scope(kb_id)
        return (
            f"""FROM rag_documents doc WHERE EXISTS (SELECT 1 {WIKI_PAGE_FROM}
            WHERE p.document_id=doc.document_id AND {where})""",
            binds,
        )

    async def document_count(self, kb_id: str) -> dict[str, Any]:
        scope, binds = self._document_scope(kb_id)
        row = await self.oracle._fetch_one(f"SELECT COUNT(*) AS total {scope}", binds)
        return {"total": _int_value(row["total"]) if row else 0}

    async def overview(self, kb_id: str, *, offset: int = 0, limit: int = 25) -> dict[str, Any]:
        """公開済みの案内がある文書を一画面分だけ読む。"""
        scope, binds = self._document_scope(kb_id)
        rows = await self.oracle._fetch_all(
            f"""SELECT doc.document_id,doc.file_name {scope}
            ORDER BY doc.file_name,doc.document_id
            OFFSET :graph_offset ROWS FETCH NEXT :graph_limit ROWS ONLY""",
            {**binds, "graph_offset": offset, "graph_limit": limit + 1},
        )
        return {
            "status": "ok" if rows else "empty",
            "has_more": len(rows) > limit,
            "documents": [
                {
                    "id": str(row["document_id"]),
                    "document_id": str(row["document_id"]),
                    "name": str(row.get("file_name") or ""),
                }
                for row in rows[:limit]
            ],
        }

    # ---- 文書の中の木 ----

    async def children(
        self, kb_id: str, document_id: str, *, parent_id: str | None, offset: int, limit: int
    ) -> dict[str, Any]:
        """親と原文順の索引から、指定した範囲の直属の子だけを読む。"""
        where, binds = graph_scope(kb_id)
        binds.update(
            graph_doc=document_id,
            graph_parent=parent_id or document_id,
            graph_offset=offset,
            graph_limit=limit,
        )
        rows = await self.oracle._fetch_all(
            f"""SELECT n.node_id,n.path_depth,n.child_count,n.unit_count,d.file_name,
                (SELECT MAX(last_node.sibling_index)+1 FROM rag_wiki_graph_nodes last_node
                 WHERE last_node.document_id=n.document_id
                 AND last_node.parent_id=n.parent_id) AS total,
                JSON_QUERY(p.page_json,'$.unit.section_path' RETURNING CLOB) AS section_path,
                CASE WHEN n.path_depth=-1 THEN JSON_VALUE(p.page_json,'$.unit.title') END AS title,
                CASE WHEN n.path_depth=-1 THEN DBMS_LOB.SUBSTR(c.chunk_text,160,1) END AS excerpt,
                CASE WHEN n.path_depth=-1 THEN c.metadata_json END AS source_metadata,
                CASE WHEN n.path_depth=-1 THEN JSON_VALUE(p.page_json,'$.navigation.context')
                END AS summary
                {WIKI_PAGE_FROM} JOIN rag_wiki_graph_nodes n ON n.page_id=p.page_id
                WHERE n.document_id=:graph_doc AND n.parent_id=:graph_parent
                AND n.sibling_index>=:graph_offset AND {where}
                ORDER BY n.sibling_index FETCH NEXT :graph_limit ROWS ONLY""",
            binds,
        )
        if not rows and not offset:
            raise KeyError(parent_id or document_id)
        items = []
        for row in rows:
            depth = _int_value(row["path_depth"])
            path = _json_list(row["section_path"])
            if depth >= 0:
                path = path[:depth]
            metadata = _json_loads(row["source_metadata"]) if row.get("source_metadata") else {}
            locator = metadata.get("source_locator")
            if isinstance(locator, dict):
                metadata.update(locator)
            items.append(
                {
                    "excerpt": row.get("excerpt") or "",
                    "page": metadata.get("page_start") or metadata.get("page_number"),
                    "line": metadata.get("source_line_start")
                    or metadata.get("line_start")
                    or metadata.get("row_start"),
                    "id": str(row["node_id"]),
                    "kind": "unit" if depth < 0 else "section",
                    "label": str(row.get("title") or "") if depth < 0 else path[-1],
                    "path": path,
                    "document_id": document_id,
                    "document_name": row["file_name"],
                    "parent_id": parent_id,
                    "child_count": _int_value(row["child_count"]),
                    "unit_count": _int_value(row["unit_count"]),
                    "summary": row.get("summary") or "",
                }
            )
        return {
            "items": items,
            "offset": offset,
            "total": _int_value(rows[0]["total"]) if rows else offset,
        }

    async def locate(self, kb_id: str, document_id: str, node_id: str) -> dict[str, Any]:
        where, binds = graph_scope(kb_id)
        path = []
        while node_id != document_id:
            row = await self.oracle._fetch_one(
                f"""SELECT n.parent_id,n.sibling_index
                {WIKI_PAGE_FROM} JOIN rag_wiki_graph_nodes n ON n.page_id=p.page_id
                WHERE n.document_id=:graph_doc AND n.node_id=:graph_node AND {where}""",
                {**binds, "graph_doc": document_id, "graph_node": node_id},
            )
            if not row:
                raise KeyError(node_id)
            path.append({"id": node_id, "index": _int_value(row["sibling_index"])})
            node_id = str(row["parent_id"])
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
               OR LOWER(file_name) LIKE :graph_pattern ESCAPE '\\'
               OR LOWER(unit_id) LIKE :graph_pattern ESCAPE '\\'
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

    async def pages_by_unit(self, kb_id: str, unit_ids: Sequence[str]) -> dict[str, dict[str, Any]]:
        if not unit_ids:
            return {}
        where, binds = graph_scope(kb_id)
        clause, extra = _oracle_in_predicate("p.unit_id", "graph_units", list(unit_ids))
        rows = await self.oracle._fetch_all(
            f"""SELECT p.page_id,p.unit_id,p.document_id,d.file_name,p.page_json
            {WIKI_PAGE_FROM} WHERE {where} AND {clause}""",
            {**binds, **extra},
        )
        return {
            str(row["unit_id"]): {
                "page_id": str(row["page_id"]),
                "page": WikiPage.model_validate(_json_loads(row["page_json"])),
                "document_name": str(row.get("file_name") or ""),
            }
            for row in rows
        }

    async def unit(self, kb_id: str, unit_id: str) -> dict[str, Any]:
        """単位の案内（要約・想定の質問・本文に無いこと）と、原文の位置と、出る・入る関係。"""
        found = await self.pages_by_unit(kb_id, [unit_id])
        if unit_id not in found:
            raise KeyError(unit_id)
        page: WikiPage = found[unit_id]["page"]
        document_id = page.unit.document_id
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
        }

    async def relations(
        self,
        kb_id: str,
        unit_id: str,
        offset: int,
        limit: int,
        *,
        relation: str | None = None,
        direction: str | None = None,
        grouped: bool = False,
    ) -> dict[str, Any]:
        """現在の単位の関係を一画面だけ返す（同じ文書の依存と、別の文書への参照の両方の向き）。

        別の文書への参照（#5784）は、参照先をこの KB の文書の名前で決める。決まった参照先は開ける
        単位を返し、決まらない参照（KB に無い文書）は原文の表記だけを返す（開けない）。
        """
        scope = await self._scope(kb_id)
        if unit_id not in scope.units:
            return {
                "has_more": False,
                "items": [],
                **({"total": 0, "counts": {}, "groups": [], "preview": []} if grouped else {}),
            }
        page_id, document_id = scope.units[unit_id]
        names = scope.names
        # 互いに依存しない読み取りは並行に読む。1 要求で 8 本を順に読むと、DB の 1 本ごとの待ちが
        # 積み重なり、並行の要求で読み取りの上限を超えた（#5966）。
        rows, references = await asyncio.gather(
            self.oracle._fetch_all(
                """SELECT 'outgoing' AS direction,relation,target_unit_id AS other
                FROM rag_wiki_graph_deps WHERE page_id=:graph_page
                UNION ALL
                SELECT 'incoming',relation,page_id FROM rag_wiki_graph_deps
                WHERE target_unit_id=:graph_unit""",
                {"graph_page": page_id, "graph_unit": unit_id},
            ),
            self.oracle._fetch_all(
                """SELECT target_name,target_section,relation FROM rag_wiki_references
                WHERE page_id=:graph_page ORDER BY ordinal""",
                {"graph_page": page_id},
            ),
        )
        items: list[dict[str, Any]] = []
        for row in rows:
            # 相手は範囲の中の同じ文書の案内だけ（出る向きは単位、入る向きは案内で引いた）。
            other = str(row["other"])
            outgoing_dep = row["direction"] == "outgoing"
            found = scope.units.get(other) if outgoing_dep else scope.pages.get(other)
            if found is None or found[1] != document_id:
                continue
            items.append(
                {
                    "direction": row["direction"],
                    "scope": "same_document",
                    "relation": row["relation"],
                    "page_id": found[0] if outgoing_dep else other,
                }
            )
        outgoing = [
            (row, names.resolve(str(row["target_name"]), exclude=document_id)) for row in references
        ]
        targets = {target for _, docs in outgoing for target in docs}
        keys = sorted(names.keys(document_id))
        aliases = sorted(names.aliases(document_id))
        candidates: list[dict[str, Any]] = []
        candidate_query: Awaitable[list[dict[str, Any]]] | None = None
        if keys or aliases:
            # 種類の呼び名で書いた参照も拾い、当たる文書は出る向きと同じ照合で決める（#5795）。
            clauses: list[str] = []
            extra: dict[str, object] = {}
            if keys:
                clause, extra = _oracle_in_predicate("r.target_key", "graph_ref_key", keys)
                clauses.append(clause)
            for index, alias in enumerate(aliases):
                clauses.append(f"INSTR(:graph_ref_alias{index},r.target_key)>0")
                extra[f"graph_ref_alias{index}"] = alias
            candidate_query = self.oracle._fetch_all(
                f"""SELECT r.relation,r.target_key,r.target_section,r.page_id
                FROM rag_wiki_references r
                WHERE ({" OR ".join(clauses)}) AND r.document_id<>:graph_doc
                ORDER BY r.page_id,r.ordinal""",
                {**extra, "graph_doc": document_id},
            )
            # 自分を指す参照の章節は自分の文書の単位で解くので、参照先の候補と一緒に先に読む。
            targets.add(document_id)
        if candidate_query is None:
            paths = await self._reference_targets(scope, sorted(targets))
        else:
            found_rows, paths = await asyncio.gather(
                candidate_query, self._reference_targets(scope, sorted(targets))
            )
            # 参照元は範囲の中の案内だけ。
            candidates = [
                {**row, "document_id": scope.pages[str(row["page_id"])][1]}
                for row in found_rows
                if str(row["page_id"]) in scope.pages
            ]
        incoming = [
            row
            for row in candidates
            if document_id
            in names.resolve_key(str(row["target_key"]), exclude=str(row["document_id"]))
        ]
        for row, docs in outgoing:
            section = str(row.get("target_section") or "")
            matches: list[str | None] = [*docs] or [None]
            for target in matches:
                items.append(
                    {
                        "direction": "outgoing",
                        "scope": "cross_document",
                        "relation": row["relation"],
                        "page_id": paths.resolve(target, section) if target else None,
                        "target_name": str(row["target_name"]),
                        "target_section": section or None,
                    }
                )
        for row in incoming:
            if paths.resolve(document_id, str(row.get("target_section") or "")) == page_id:
                items.append(
                    {
                        "direction": "incoming",
                        "scope": "cross_document",
                        "relation": row["relation"],
                        "page_id": row["page_id"],
                    }
                )
        counts = Counter(item["relation"] for item in items)
        directed: Counter[tuple[str, str]] = Counter()
        targets_by_group: dict[tuple[str, str], set[str]] = {}
        if grouped:
            for item in items:
                key = (item["relation"], item["direction"])
                directed[key] += 1
                if item["page_id"]:
                    targets_by_group.setdefault(key, set()).add(item["page_id"])
        if relation is not None:
            items = [item for item in items if item["relation"] == relation]
        if direction is not None:
            items = [item for item in items if item["direction"] == direction]
        ordered = sorted(
            items,
            key=lambda item: (
                item["direction"],
                item["scope"],
                item["page_id"] or "",
                item.get("target_name") or "",
                item["relation"],
            ),
        )
        page_items = ordered[offset : offset + limit]
        representatives: dict[tuple[str, str, str | None], dict[str, Any]] = {}
        if grouped and offset == 0 and not relation and not direction:
            for item in ordered:
                if item["page_id"]:
                    group = (item["relation"], item["direction"])
                    target = item["page_id"] if len(targets_by_group[group]) <= limit else None
                    representatives.setdefault((*group, target), item)
        preview_ids = {item["page_id"] for item in representatives.values()}
        connection_query: Awaitable[list[dict[str, Any]]] | None = None
        if len(preview_ids) > 1:
            clause, page_binds = _oracle_in_predicate(
                "page_id", "preview_page", sorted(preview_ids)
            )
            connection_query = self.oracle._fetch_all(
                f"""WITH shown_pages AS (
                    SELECT page_id,unit_id,document_id FROM rag_wiki_pages WHERE {clause}
                )
                SELECT s.unit_id AS source_id,t.unit_id AS target_id,e.relation
                FROM shown_pages s JOIN rag_wiki_graph_deps e ON e.page_id=s.page_id
                JOIN shown_pages t ON t.unit_id=e.target_unit_id AND t.document_id=s.document_id
                WHERE s.unit_id<>t.unit_id ORDER BY s.unit_id,t.unit_id,e.relation""",
                page_binds,
            )
        unit_query = self._unit_refs(
            [
                item["page_id"]
                for item in [*page_items, *representatives.values()]
                if item["page_id"]
            ]
        )
        if connection_query is None:
            connections: list[dict[str, Any]] = []
            units = await unit_query
        else:
            connections, units = await asyncio.gather(connection_query, unit_query)

        def entry(item: dict[str, Any]) -> dict[str, Any]:
            return {
                "direction": item["direction"],
                "scope": item["scope"],
                "relation": item["relation"],
                "unit": units.get(item["page_id"]),
                "target_name": item.get("target_name"),
                "target_section": item.get("target_section"),
            }

        return {
            **(
                {
                    "counts": dict(counts),
                    "groups": [
                        {
                            "relation": rel,
                            "direction": way,
                            "count": amount,
                            "neighbor_count": len(targets_by_group.get((rel, way), set())),
                        }
                        for (rel, way), amount in sorted(directed.items())
                    ],
                    "total": len(ordered),
                    "preview": [entry(item) for item in representatives.values()],
                    "connections": connections,
                }
                if grouped
                else {}
            ),
            "has_more": len(ordered) > offset + limit,
            "items": [entry(item) for item in page_items],
        }

    # ---- 図の操作（Neo4j Browser / Bloom 相当の探索。#5916） ----

    async def document_graph(
        self, kb_id: str, *, offset: int, limit: int, document: str | None = None
    ) -> dict[str, Any]:
        """文書の間の参照（別の文書への参照を文書の名前で解いた結果）の多い文書の順の見本。

        ``document`` を渡すと、その文書と参照でつながる文書を参照の多い順に返す。
        返す文書（と ``document``）の間の参照は、向きと種類ごとの件数にまとめて返す。
        """
        scope = await self._scope(kb_id)
        names = scope.names
        documents = sorted({document for _, document in scope.pages.values()})
        rows: list[dict[str, Any]] = []
        if documents:
            clause, binds = _oracle_in_predicate("r.document_id", "graph_ref_doc", documents)
            rows = await self.oracle._fetch_all(
                f"""SELECT r.page_id,r.target_name,r.relation
                FROM rag_wiki_references r WHERE {clause}""",
                binds,
            )
        links: Counter[tuple[str, str, str]] = Counter()
        for row in rows:
            # 参照元は範囲の中の案内だけ（文書の古い版の案内の参照は数えない）。
            if str(row["page_id"]) not in scope.pages:
                continue
            source = scope.pages[str(row["page_id"])][1]
            for target in names.resolve(str(row["target_name"]), exclude=source):
                links[(source, target, str(row["relation"]))] += 1
        degree: Counter[str] = Counter()
        neighbors: Counter[str] = Counter()
        for (source, target, _), count in links.items():
            degree[source] += count
            degree[target] += count
            if document is not None and document in (source, target):
                neighbors[target if source == document else source] += count
        files = names.files
        if document is not None and document not in files:
            raise KeyError(document)
        pool = list(neighbors) if document is not None else list(files)
        weight = neighbors if document is not None else degree
        ordered = sorted(pool, key=lambda did: (-weight[did], files.get(did, ""), did))
        page = ordered[offset : offset + limit]
        shown = set(page) | ({document} if document is not None else set())
        return {
            "documents": [
                {
                    "id": did,
                    "document_id": did,
                    "name": files.get(did, ""),
                    "reference_count": degree[did],
                }
                for did in page
            ],
            "links": [
                {"source_id": source, "target_id": target, "relation": relation, "count": count}
                for (source, target, relation), count in sorted(links.items())
                if source in shown and target in shown
            ],
            "total": len(ordered),
            "offset": offset,
            "has_more": len(ordered) > offset + limit,
        }

    async def connections(self, kb_id: str, unit_ids: Sequence[str]) -> list[dict[str, Any]]:
        """選んだ単位の間の関係（同じ文書の依存と、別の文書への参照）。範囲の外の単位は引かない。"""
        ids = list(dict.fromkeys(unit_ids))
        if len(ids) < 2:
            return []
        scope = await self._scope(kb_id)
        # 範囲の中の選んだ単位。依存と参照は、範囲にあると確かめた案内の ID で引く（#5966）。
        # 線の相手は選んだ単位（範囲の中）だけを採るので、相手の側を範囲で照合し直さない。
        by_page = {
            scope.units[uid][0]: {
                "page_id": scope.units[uid][0],
                "unit_id": uid,
                "document_id": scope.units[uid][1],
            }
            for uid in ids
            if uid in scope.units
        }
        if len(by_page) < 2:
            return []
        documents = {row["unit_id"]: row["document_id"] for row in by_page.values()}
        names = scope.names
        page_clause, page_binds = _oracle_in_predicate("e.page_id", "graph_conn_p", list(by_page))
        source_clause, source_binds = _oracle_in_predicate(
            "r.page_id", "graph_conn_s", list(by_page)
        )
        deps, reference_rows = await asyncio.gather(
            self.oracle._fetch_all(
                f"""SELECT e.page_id,e.target_unit_id AS target_id,e.relation
                FROM rag_wiki_graph_deps e WHERE {page_clause}""",
                page_binds,
            ),
            self.oracle._fetch_all(
                f"""SELECT r.page_id,r.target_name,r.target_section,r.relation
                FROM rag_wiki_references r WHERE {source_clause}""",
                source_binds,
            ),
        )
        found: dict[tuple[str, str, str], str] = {}
        for row in deps:
            source = str(by_page[str(row["page_id"])]["unit_id"])
            target = str(row["target_id"])
            # 案内の依存は同じ文書の中だけ（別の文書は下の参照で解く）。
            if target != source and documents.get(target) == documents[source]:
                found[(source, target, str(row["relation"]))] = "same_document"
        references = [
            {
                **row,
                "source_id": by_page[str(row["page_id"])]["unit_id"],
                "document_id": by_page[str(row["page_id"])]["document_id"],
            }
            for row in reference_rows
        ]
        if references:
            resolved = [
                (row, names.resolve(str(row["target_name"]), exclude=str(row["document_id"])))
                for row in references
            ]
            targets = await self._reference_targets(
                scope, sorted({doc for _, docs in resolved for doc in docs})
            )
            pages = [
                (row, page)
                for row, docs in resolved
                for doc in docs
                if (page := targets.resolve(doc, str(row.get("target_section") or "")))
            ]
            units = await self._unit_refs([page for _, page in pages])
            wanted = set(ids)
            for row, page in pages:
                linked = units.get(page, {}).get("id")
                if linked in wanted and linked != row["source_id"]:
                    found.setdefault(
                        (str(row["source_id"]), str(linked), str(row["relation"])),
                        "cross_document",
                    )
        return [
            {"source_id": source, "target_id": target, "relation": relation, "scope": scope}
            for (source, target, relation), scope in sorted(found.items())
        ]

    async def _document_names(self, kb_id: str) -> DocumentNames:
        """図の範囲の文書の名前の照合（ファイル名と先頭の見出し）。

        先頭の見出しは、図の索引の最上位の節点（先頭の見出しごとに 1 件と、章節の無い単位）の
        案内だけから読む。範囲の案内を全件読むと、要求ごとに案内の数だけ JSON を開く（#5966）。
        """
        where, binds = graph_scope(kb_id)
        rows = await self.oracle._fetch_all(
            f"""SELECT DISTINCT p.document_id,d.file_name,
            JSON_VALUE(p.page_json,'$.unit.section_path[0]') AS heading
            {WIKI_PAGE_FROM}
            JOIN rag_wiki_graph_nodes n ON n.page_id=p.page_id
                AND n.document_id=p.document_id AND n.parent_id=n.document_id
            WHERE {where}""",
            binds,
        )
        catalog: dict[str, dict[str, str]] = {}
        headings: dict[str, list[str]] = {}
        for row in rows:
            did = str(row["document_id"])
            catalog[did] = {"document_id": did, "title": str(row.get("file_name") or "")}
            if row.get("heading"):
                headings.setdefault(did, []).append(str(row["heading"]))
        return DocumentNames(list(catalog.values()), headings)

    async def _reference_targets(self, scope: GraphScope, documents: list[str]) -> ReferenceTargets:
        """参照先の文書の章節の候補（原文の順）。範囲の版の中で、文書ごとに 1 回だけ読む。"""
        missing = [doc for doc in documents if doc not in scope.targets.by_document]
        if missing:
            clause, binds = _oracle_in_predicate("p.document_id", "graph_target", missing)
            with read_lobs_as_values():
                rows = await self.oracle._fetch_all(
                    f"""SELECT p.page_id AS unit_id,p.document_id,
                    JSON_QUERY(p.page_json,'$.unit.section_path' RETURNING CLOB) AS target_path
                    FROM rag_wiki_pages p
                    JOIN rag_wiki_documents w ON w.document_id=p.document_id
                        AND p.source_fingerprint=w.published_fingerprint
                    JOIN rag_chunks c ON c.chunk_id=p.source_chunk_id
                    WHERE {clause} ORDER BY p.document_id,c.chunk_index,p.unit_id""",
                    binds,
                )
            # 範囲の照合は版ごとの 1 回（``_scope``）。ここは範囲の中の案内だけを残す。
            scope.targets.extend(
                missing, [row for row in rows if str(row["unit_id"]) in scope.pages]
            )
        return scope.targets

    async def _unit_refs(self, page_ids: Sequence[str]) -> dict[str, dict[str, Any]]:
        if not page_ids:
            return {}
        clause, binds = _oracle_in_predicate("p.page_id", "graph_pages", sorted(set(page_ids)))
        rows = await self.oracle._fetch_all(
            f"""SELECT p.page_id,p.unit_id,p.document_id,d.file_name,
            JSON_VALUE(p.page_json,'$.unit.title') AS title,
            JSON_QUERY(p.page_json,'$.unit.section_path' RETURNING CLOB) AS section_path
            FROM rag_wiki_pages p JOIN rag_documents d ON d.document_id=p.document_id
            WHERE {clause}""",
            binds,
        )
        return {
            str(row["page_id"]): {
                "id": row["unit_id"],
                "document_id": row["document_id"],
                "document_name": row["file_name"],
                "label": row.get("title") or "",
                "path": _json_list(row["section_path"]),
            }
            for row in rows
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
