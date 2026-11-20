"""KB 詳細の Wiki の図の読み取り（#5502）。

保存済みの案内（``rag_wiki_pages``）と文書をまたぐ関連（``rag_wiki_links``）を、
文書 → 章節 → 原文単位の図の形で返す。範囲は Wiki の文書ごとの状態と同じ条件
（今の利用者が読める、この KB に所属する文書）で、Wiki の構築の指定が無効でも保存済みの案内は出す。
案内は今の原文の保存版で公開済みのものだけを出す。
検索・回答の経路（``WikiStore``）の照会とは分け、画面の読み取りだけを置く。
"""

from __future__ import annotations

import unicodedata
from collections.abc import Sequence
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
from app.clients.wiki_graph_index import section_id as section_id
from app.clients.wiki_store import CURRENT_WIKI_SOURCE_SQL, WIKI_PAGE_FROM
from app.rag.wiki_models import WikiPage

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


class WikiGraphStore:
    def __init__(self, oracle: OracleClient) -> None:
        self.oracle = oracle

    # ---- 総覧 ----

    async def overview(self, kb_id: str, *, offset: int = 0, limit: int = 25) -> dict[str, Any]:
        """公開済みの案内がある文書を一画面分だけ読む。"""
        where, binds = graph_scope(kb_id)
        rows = await self.oracle._fetch_all(
            f"""SELECT doc.document_id,doc.file_name FROM rag_documents doc
            WHERE EXISTS (SELECT 1 {WIKI_PAGE_FROM}
                WHERE p.document_id=doc.document_id AND {where})
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
            items.append(
                {
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

    async def relations(self, kb_id: str, unit_id: str, offset: int, limit: int) -> dict[str, Any]:
        """現在の単位と相手の両方が見える関係を一画面だけ返す。"""
        cte, binds = visible_pages_cte(kb_id)
        rows = await self.oracle._fetch_all(
            f"""WITH {cte},
            selected_page AS (SELECT * FROM visible WHERE unit_id=:graph_unit),
            neighbors AS (
                SELECT 'outgoing' AS direction,'same_document' AS scope,e.relation,
                       other.page_id,CAST(NULL AS VARCHAR2(800)) AS reason
                FROM selected_page u JOIN rag_wiki_graph_deps e ON e.page_id=u.page_id
                JOIN visible other ON other.document_id=u.document_id
                    AND other.unit_id=e.target_unit_id
                UNION ALL
                SELECT 'incoming','same_document',e.relation,other.page_id,NULL
                FROM selected_page u JOIN rag_wiki_graph_deps e ON e.target_unit_id=u.unit_id
                JOIN visible other ON other.document_id=u.document_id AND other.page_id=e.page_id
                UNION ALL
                SELECT 'outgoing','cross_document',l.relation,other.page_id,l.reason
                FROM selected_page u JOIN rag_wiki_links l ON l.source_page_id=u.page_id
                JOIN visible other ON other.page_id=l.target_page_id
                UNION ALL
                SELECT 'incoming','cross_document',l.relation,other.page_id,l.reason
                FROM selected_page u JOIN rag_wiki_links l ON l.target_page_id=u.page_id
                JOIN visible other ON other.page_id=l.source_page_id)
            SELECT n.direction,n.scope,n.relation,n.reason,p.unit_id,p.document_id,d.file_name,
                   JSON_VALUE(p.page_json,'$.unit.title') AS title,
                   JSON_QUERY(p.page_json,'$.unit.section_path' RETURNING CLOB) AS section_path
            FROM neighbors n JOIN rag_wiki_pages p ON p.page_id=n.page_id
            JOIN rag_documents d ON d.document_id=p.document_id
            ORDER BY n.direction,n.scope,n.page_id,n.relation
            OFFSET :graph_offset ROWS FETCH NEXT :graph_limit ROWS ONLY""",
            {**binds, "graph_unit": unit_id, "graph_offset": offset, "graph_limit": limit + 1},
        )
        return {
            "has_more": len(rows) > limit,
            "items": [
                {
                    "direction": row["direction"],
                    "scope": row["scope"],
                    "relation": row["relation"],
                    "reason": row.get("reason"),
                    "unit": {
                        "id": row["unit_id"],
                        "document_id": row["document_id"],
                        "document_name": row["file_name"],
                        "label": row.get("title") or "",
                        "path": _json_list(row["section_path"]),
                    },
                }
                for row in rows[:limit]
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
