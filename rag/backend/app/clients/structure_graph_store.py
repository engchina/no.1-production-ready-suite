"""文書構造グラフの読み取り（総覧・章節の木・参照・検索。#4309）。

どの読み取りも KB の所属・有効な版・文書の権限を確かめる。件数も権限の範囲だけで数える。
"""

from __future__ import annotations

import json
import unicodedata
from typing import Any

from app.clients.oracle import (
    SECTION_PATH_OVERFLOW_SQL,
    _graph_visible_document_predicate,
    _oracle_access_predicate_sql,
    _oracle_in_predicate,
    _with_tenant_bind,
)
from app.rag.graph_index import GRAPH_NAME_ELISION
from app.rag.structure_tree import (
    StructureChunkRow,
    StructureTree,
    build_structure_tree,
    section_chain,
)


def _reference_scope_sql() -> str:
    """両端の文書が有効な版で KB に属し、権限の範囲にある解決済みの参照だけ。"""
    return f"""FROM rag_graph_references r
        JOIN rag_chunk_sets ss ON ss.chunk_set_id=r.source_chunk_set_id AND ss.is_active=1
        JOIN rag_chunk_sets ts ON ts.chunk_set_id=r.target_chunk_set_id AND ts.is_active=1
        JOIN rag_documents s ON s.document_id=r.source_document_id
        JOIN rag_documents t ON t.document_id=r.target_document_id
        WHERE r.knowledge_base_id=:kb AND r.resolution_status='resolved'
        AND {_oracle_access_predicate_sql(alias="s")}
        AND {_oracle_access_predicate_sql(alias="t")}
        AND EXISTS (SELECT 1 FROM rag_document_knowledge_bases sm
            WHERE sm.document_id=s.document_id AND sm.knowledge_base_id=:kb)
        AND EXISTS (SELECT 1 FROM rag_document_knowledge_bases tm
            WHERE tm.document_id=t.document_id AND tm.knowledge_base_id=:kb)"""


def _section_path_text(row: dict[str, Any]) -> str | None:
    """文字列・配列の section_path を、木を作る文字列にする。"""
    if row.get("path"):
        return str(row["path"])
    parts = json.loads(str(row.get("path_json") or "null"))
    return " > ".join(str(part) for part in parts) if isinstance(parts, list) else None


def _like_pattern(value: str) -> str:
    escaped = value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{escaped}%"


class StructureGraphStore:
    def __init__(self, oracle: Any) -> None:
        self.oracle = oracle

    async def overview(self, kb_id: str) -> dict[str, Any]:
        """KB の全文書と、文書の組ごとの参照の件数。"""
        nodes = await self.oracle._fetch_all(
            f"""SELECT e.entity_id,e.chunk_set_id,cs.document_id,d.file_name,
            (SELECT COUNT(*) FROM rag_graph_entities c WHERE c.chunk_set_id=e.chunk_set_id
                AND c.knowledge_base_id=e.knowledge_base_id AND c.entity_type<>'document')
                AS section_entities
            FROM rag_graph_entities e
            JOIN rag_chunk_sets cs ON cs.chunk_set_id=e.chunk_set_id AND cs.is_active=1
            JOIN rag_documents d ON d.document_id=cs.document_id
            WHERE e.knowledge_base_id=:kb AND e.entity_type='document'
            AND {_graph_visible_document_predicate("e")}
            ORDER BY d.file_name,e.entity_id""",
            _with_tenant_bind({"kb": kb_id}),
        )
        if not nodes:
            return {"status": "empty", "documents": [], "edges": []}
        by_set = {str(row["chunk_set_id"]): str(row["entity_id"]) for row in nodes}
        rows = await self.oracle._fetch_all(
            f"""SELECT r.source_chunk_set_id AS source_set,r.target_chunk_set_id AS target_set,
            COUNT(*) AS reference_count
            {_reference_scope_sql()}
            AND r.source_document_id<>r.target_document_id
            GROUP BY r.source_chunk_set_id,r.target_chunk_set_id""",
            _with_tenant_bind({"kb": kb_id}),
        )
        edges: list[dict[str, Any]] = []
        outgoing: dict[str, int] = {}
        incoming: dict[str, int] = {}
        for row in rows:
            source = by_set.get(str(row["source_set"]))
            target = by_set.get(str(row["target_set"]))
            if source is None or target is None:
                continue
            count = int(row["reference_count"])
            edges.append(
                {"id": f"{source}:{target}", "source": source, "target": target, "count": count}
            )
            outgoing[source] = outgoing.get(source, 0) + count
            incoming[target] = incoming.get(target, 0) + count
        return {
            "status": "ok",
            "documents": [
                {
                    "id": str(row["entity_id"]),
                    "document_id": str(row["document_id"]),
                    "chunk_set_id": str(row["chunk_set_id"]),
                    "name": str(row["file_name"]),
                    "has_sections": int(row["section_entities"]) > 0,
                    "outgoing_count": outgoing.get(str(row["entity_id"]), 0),
                    "incoming_count": incoming.get(str(row["entity_id"]), 0),
                }
                for row in nodes
            ],
            "edges": edges,
        }

    async def assert_document(
        self, kb_id: str, document_id: str, chunk_set_id: str
    ) -> dict[str, Any]:
        """有効な版で KB に属し、権限の範囲にある文書。そうでなければ KeyError。"""
        rows = await self.oracle._fetch_all(
            f"""SELECT d.file_name FROM rag_documents d
            JOIN rag_chunk_sets cs ON cs.document_id=d.document_id
                AND cs.chunk_set_id=:chunk_set AND cs.is_active=1
            JOIN rag_document_knowledge_bases m ON m.document_id=d.document_id
                AND m.knowledge_base_id=:kb
            WHERE d.document_id=:document_id
            AND {_oracle_access_predicate_sql(alias="d")}""",
            _with_tenant_bind({"kb": kb_id, "document_id": document_id, "chunk_set": chunk_set_id}),
        )
        if not rows:
            raise KeyError(document_id)
        return dict(rows[0])

    async def _tree(self, kb_id: str, chunk_set_id: str) -> StructureTree:
        rows = await self.oracle._fetch_all(
            f"""SELECT c.chunk_index,
            JSON_VALUE(c.metadata_json,'$.section_path' RETURNING VARCHAR2(4000)) AS path,
            JSON_QUERY(c.metadata_json,'$.section_path' RETURNING VARCHAR2(4000)) AS path_json,
            JSON_VALUE(c.metadata_json,'$.section_title' RETURNING VARCHAR2(4000)) AS title,
            JSON_VALUE(c.metadata_json,'$.content_kind' RETURNING VARCHAR2(64)) AS kind,
            CASE WHEN {SECTION_PATH_OVERFLOW_SQL} THEN 1 ELSE 0 END AS path_overflow
            FROM rag_chunks c WHERE c.chunk_set_id=:chunk_set ORDER BY c.chunk_index""",
            {"chunk_set": chunk_set_id},
        )
        overflow: dict[int, str] = {}
        if any(row.get("path_overflow") for row in rows):
            long_rows = await self.oracle._fetch_all(
                f"""SELECT c.chunk_index,
                JSON_VALUE(c.metadata_json,'$.section_path' RETURNING CLOB) AS path
                FROM rag_chunks c WHERE c.chunk_set_id=:chunk_set
                AND {SECTION_PATH_OVERFLOW_SQL}""",
                {"chunk_set": chunk_set_id},
            )
            overflow = {int(row["chunk_index"]): str(row["path"]) for row in long_rows}
        return build_structure_tree(
            (
                StructureChunkRow(
                    overflow.get(int(row["chunk_index"])) or _section_path_text(row),
                    str(row["title"]) if row.get("title") else None,
                    str(row["kind"]) if row.get("kind") else None,
                )
                for row in rows
            ),
            chunk_set_id=chunk_set_id,
            knowledge_base_id=kb_id,
        )

    async def children(
        self,
        kb_id: str,
        document_id: str,
        chunk_set_id: str,
        *,
        parent_id: str | None,
        offset: int,
        limit: int,
    ) -> dict[str, Any]:
        """1 段の子。木に無い親は KeyError。"""
        document = await self.assert_document(kb_id, document_id, chunk_set_id)
        tree = await self._tree(kb_id, chunk_set_id)
        children = tree.children_of(parent_id)
        page = children[offset : offset + limit]
        entities = await self._entity_ids(kb_id, chunk_set_id, page)
        return {
            "items": [
                {
                    "id": item,
                    "name": tree.nodes[item].name,
                    "label": tree.nodes[item].label,
                    "type": tree.nodes[item].type,
                    "document_id": document_id,
                    "document_name": str(document["file_name"]),
                    "chunk_set_id": chunk_set_id,
                    "parent_id": parent_id,
                    "virtual": item not in entities,
                    "child_count": len(tree.nodes[item].children),
                }
                for item in page
            ],
            "total": len(children),
            "offset": offset,
        }

    async def body_chunks(
        self, kb_id: str, document_id: str, chunk_set_id: str, *, offset: int, limit: int
    ) -> dict[str, Any]:
        """本文節点の実Chunkだけを保存順に返す。本文/原文の読取は既存根拠APIが担う。"""
        from app.rag.graph_index import graph_body_node_id

        await self.assert_document(kb_id, document_id, chunk_set_id)
        source_sql = """FROM rag_graph_entity_chunks l
            JOIN rag_graph_entities e ON e.entity_id=l.entity_id
                AND e.chunk_set_id=:chunk_set AND e.knowledge_base_id=:kb AND e.entity_type='body'
            JOIN rag_chunks c ON c.chunk_id=l.chunk_id AND c.chunk_set_id=e.chunk_set_id
                AND c.document_id=:document_id
            WHERE l.entity_id=:body AND l.document_id=:document_id"""
        scope = {
            "kb": kb_id,
            "document_id": document_id,
            "chunk_set": chunk_set_id,
            "body": graph_body_node_id(chunk_set_id, kb_id),
        }
        rows = await self.oracle._fetch_all(
            f"""SELECT c.chunk_id,c.chunk_index,
            JSON_VALUE(c.metadata_json,'$.page_number'
                RETURNING NUMBER NULL ON ERROR) AS page_number,
            COUNT(*) OVER () AS total_count
            {source_sql}
            ORDER BY c.chunk_index,c.chunk_id
            OFFSET :skip ROWS FETCH NEXT :take ROWS ONLY""",
            {**scope, "skip": offset, "take": limit},
        )
        if rows:
            total = int(rows[0]["total_count"])
        else:
            # 末尾を越えた空ページも、同じ範囲の実件数を保持する。
            counted = await self.oracle._fetch_all(f"SELECT COUNT(*) AS total {source_sql}", scope)
            total = int(counted[0]["total"])
        return {
            "items": [
                {
                    "chunk_id": str(row["chunk_id"]),
                    "chunk_index": int(row["chunk_index"]),
                    "page_number": int(row["page_number"]) if row.get("page_number") else None,
                }
                for row in rows
            ],
            "total": total,
            "offset": offset,
        }

    async def _entity_ids(self, kb_id: str, chunk_set_id: str, ids: list[str]) -> set[str]:
        """図の節点のうち、構造索引に実体があるもの（参照の一覧を読める節点）。"""
        if not ids:
            return set()
        in_sql, binds = _oracle_in_predicate("e.entity_id", "graph_tree", ids)
        rows = await self.oracle._fetch_all(
            f"""SELECT e.entity_id FROM rag_graph_entities e
            WHERE e.knowledge_base_id=:kb AND e.chunk_set_id=:chunk_set AND {in_sql}""",
            {"kb": kb_id, "chunk_set": chunk_set_id, **binds},
        )
        return {str(row["entity_id"]) for row in rows}

    async def locate(
        self, kb_id: str, document_id: str, chunk_set_id: str, node_id: str
    ) -> dict[str, Any]:
        """節点までの段の並び。図で開くページを決める。文書の節点は空の並び。"""
        await self.assert_document(kb_id, document_id, chunk_set_id)
        tree = await self._tree(kb_id, chunk_set_id)
        return {"path": tree.locate(node_id)}

    async def references(self, kb_id: str, chunk_set_id: str) -> dict[str, Any]:
        """文書が端にある解決済みの参照。祖先の列を付け、見えている段へ画面でまとめる。"""
        rows = await self.oracle._fetch_all(
            f"""SELECT r.source_chunk_set_id AS source_set,r.target_chunk_set_id AS target_set,
            r.source_section_path AS source_path,r.target_section_path AS target_path,
            COUNT(*) AS reference_count
            {_reference_scope_sql()}
            AND (r.source_chunk_set_id=:chunk_set OR r.target_chunk_set_id=:chunk_set)
            GROUP BY r.source_chunk_set_id,r.target_chunk_set_id,
                r.source_section_path,r.target_section_path""",
            _with_tenant_bind({"kb": kb_id, "chunk_set": chunk_set_id}),
        )
        return {
            "items": [
                {
                    "source_set": str(row["source_set"]),
                    "target_set": str(row["target_set"]),
                    "source_chain": await self._chain(
                        kb_id, str(row["source_set"]), row.get("source_path")
                    ),
                    "target_chain": await self._chain(
                        kb_id, str(row["target_set"]), row.get("target_path")
                    ),
                    "count": int(row["reference_count"]),
                }
                for row in rows
            ]
        }

    async def _chain(self, kb_id: str, chunk_set_id: str, path: object) -> list[str]:
        """保存したパスの各段の節点。列に入らず表示名で保存したパスは、節点だけを引く。"""
        text = str(path) if path else None
        if text is None or GRAPH_NAME_ELISION not in text:
            return section_chain(chunk_set_id, kb_id, text)
        rows = await self.oracle._fetch_all(
            """SELECT e.entity_id FROM rag_graph_entities e
            WHERE e.chunk_set_id=:chunk_set AND e.knowledge_base_id=:kb
            AND e.canonical_name=:name""",
            {"chunk_set": chunk_set_id, "kb": kb_id, "name": text},
        )
        return [str(rows[0]["entity_id"])] if len(rows) == 1 else []

    async def search(self, kb_id: str, query: str, *, offset: int, limit: int) -> dict[str, Any]:
        """文書名と章節名の検索（部分一致）。節の別名だけの節点は出さない。"""
        text = unicodedata.normalize("NFKC", query).strip().casefold()
        if not text:
            return {"items": [], "total": 0, "offset": offset}
        rows = await self.oracle._fetch_all(
            f"""SELECT e.entity_id,e.canonical_name,e.entity_type,e.chunk_set_id,
            cs.document_id,d.file_name,COUNT(*) OVER () AS total_count
            FROM rag_graph_entities e
            JOIN rag_chunk_sets cs ON cs.chunk_set_id=e.chunk_set_id AND cs.is_active=1
            JOIN rag_documents d ON d.document_id=cs.document_id
            WHERE e.knowledge_base_id=:kb
            AND LOWER(e.canonical_name) LIKE :pattern ESCAPE '\\'
            AND {_graph_visible_document_predicate("e")}
            AND NOT EXISTS (SELECT 1 FROM rag_graph_entities p
                WHERE p.chunk_set_id=e.chunk_set_id AND p.knowledge_base_id=e.knowledge_base_id
                AND e.entity_type<>'document'
                AND SUBSTR(p.canonical_name,-LENGTH(e.canonical_name)-3)
                    =' > '||e.canonical_name)
            ORDER BY CASE WHEN e.entity_type='document' THEN 0 ELSE 1 END,
                d.file_name,e.canonical_name,e.entity_id
            OFFSET :skip ROWS FETCH NEXT :take ROWS ONLY""",
            _with_tenant_bind(
                {"kb": kb_id, "pattern": _like_pattern(text), "skip": offset, "take": limit}
            ),
        )
        return {
            "items": [
                {
                    "id": str(row["entity_id"]),
                    "name": str(row["canonical_name"]),
                    "type": str(row["entity_type"]),
                    "document_id": str(row["document_id"]),
                    "document_name": str(row["file_name"]),
                    "chunk_set_id": str(row["chunk_set_id"]),
                }
                for row in rows
            ],
            "total": int(rows[0]["total_count"]) if rows else 0,
            "offset": offset,
        }
