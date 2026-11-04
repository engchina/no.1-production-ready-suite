"""版付き明示参照の永続化。内容・埋め込みと検索索引を更新しない。"""

from __future__ import annotations

import json
from collections import defaultdict
from collections.abc import Mapping
from typing import Any

from app.rag.graph_index import _clean_label, graph_display_name_sql
from app.rag.reference_records import GraphReferenceRecord
from app.rag.reference_resolution import ReferenceDocument


def graph_reference_schema_sql() -> str:
    return """
CREATE TABLE rag_graph_references (
    reference_id CHAR(64) PRIMARY KEY,
    knowledge_base_id VARCHAR2(64) NOT NULL,
    source_document_id VARCHAR2(64) NOT NULL,
    source_chunk_set_id VARCHAR2(64) NOT NULL,
    source_chunk_id VARCHAR2(128) NOT NULL,
    source_section_path VARCHAR2(4000 CHAR),
    label VARCHAR2(2000 CHAR) NOT NULL,
    kind VARCHAR2(24) NOT NULL,
    reference_key VARCHAR2(2000 CHAR),
    document_title VARCHAR2(2000 CHAR),
    resolution_status VARCHAR2(32) NOT NULL,
    target_document_id VARCHAR2(64),
    target_chunk_set_id VARCHAR2(64),
    target_section_path VARCHAR2(4000 CHAR),
    created_at TIMESTAMP WITH TIME ZONE DEFAULT SYSTIMESTAMP NOT NULL,
    CONSTRAINT rag_graph_refs_kb_fk FOREIGN KEY (knowledge_base_id)
        REFERENCES rag_knowledge_bases(knowledge_base_id) ON DELETE CASCADE,
    CONSTRAINT rag_graph_refs_source_fk FOREIGN KEY (source_chunk_set_id)
        REFERENCES rag_chunk_sets(chunk_set_id) ON DELETE CASCADE
);
CREATE INDEX rag_graph_refs_source_idx ON rag_graph_references
    (knowledge_base_id,source_chunk_set_id);
CREATE INDEX rag_graph_refs_target_idx ON rag_graph_references
    (knowledge_base_id,target_chunk_set_id);
""".strip()


def _column_section_path(path: str | None) -> str | None:
    """section path の列（VARCHAR2(4000 CHAR)）へ書く値。

    4000 バイト以上は CLOB の bind になり、列を超えると ORA-22835・ORA-64203 で構築全体が
    失敗する。そのようなパスは図のノードの表示名で保存する（図のノードとは表示名で結ぶ）。
    """
    from app.clients.oracle import _is_large_text

    if path is None or not _is_large_text(path):
        return path
    return _clean_label(path)


def _column_row(record: GraphReferenceRecord) -> dict[str, Any]:
    row = record.to_dict()
    for column in ("source_section_path", "target_section_path"):
        row[column] = _column_section_path(row[column])
    return row


def write_graph_references(
    connection: Any,
    *,
    knowledge_base_id: str,
    chunk_set_id: str,
    records: list[GraphReferenceRecord],
) -> None:
    """グラフのpublishと同じtransactionで、source成果物の参照を置換する。"""
    from app.clients.oracle import _execute, _executemany

    _execute(
        connection,
        """DELETE FROM rag_graph_references
        WHERE knowledge_base_id=:kb AND source_chunk_set_id=:cs""",
        {"kb": knowledge_base_id, "cs": chunk_set_id},
    )
    if records:
        for offset in range(0, len(records), 250):
            _executemany(
                connection,
                """INSERT INTO rag_graph_references
                (reference_id,knowledge_base_id,source_document_id,source_chunk_set_id,source_chunk_id,
                 source_section_path,label,kind,reference_key,document_title,resolution_status,
                 target_document_id,target_chunk_set_id,target_section_path)
                VALUES (:reference_id,:knowledge_base_id,:source_document_id,:source_chunk_set_id,
                :source_chunk_id,:source_section_path,:label,:kind,:key,:document_title,:status,
                :target_document_id,:target_chunk_set_id,:target_section_path)""",
                [_column_row(record) for record in records[offset : offset + 250]],
            )


def _endpoint_entity_sql(side: str) -> str:
    """保存した section path のノード。表示名で保存した端点の ID はパスから求まらない。"""
    return f"""(SELECT CASE WHEN COUNT(*)=1 THEN MIN(e.entity_id) END FROM rag_graph_entities e
            WHERE e.chunk_set_id=r.{side}_chunk_set_id
            AND e.knowledge_base_id=r.knowledge_base_id
            AND e.canonical_name=r.{side}_section_path) AS {side}_entity_id"""


def _catalog_section_path(row: Mapping[str, object]) -> str:
    """文字列・配列のsection_pathを目録の文字列にする。JSON nullなどは章節パスなしと同じ。"""
    if row.get("path"):
        return str(row["path"])
    parts = json.loads(str(row.get("path_json") or "null"))
    return " > ".join(str(part) for part in parts) if isinstance(parts, list) else ""


class GraphReferenceStore:
    def __init__(self, oracle: Any) -> None:
        self.oracle = oracle

    async def documents(self, kb_id: str) -> tuple[ReferenceDocument, ...]:
        """現行の許可範囲から本文を含まない参照先目録を得る。"""
        from app.clients.oracle import SECTION_PATH_OVERFLOW_SQL, _oracle_retrieval_where

        where, binds = _oracle_retrieval_where({"knowledge_base_id": kb_id}, effective_period=False)
        # DISTINCT は CLOB に使えないので、通常は VARCHAR2 で読む。4000 バイトを超えるパスは
        # どちらの関数も NULL を返すので印を付け、その行だけを CLOB で読み直す。
        overflow = SECTION_PATH_OVERFLOW_SQL
        rows = await self.oracle._fetch_all(
            f"""SELECT DISTINCT c.document_id,c.chunk_set_id,d.file_name,
            JSON_VALUE(c.metadata_json,'$.section_path' RETURNING VARCHAR2(4000)) AS path,
            JSON_QUERY(c.metadata_json,'$.section_path' RETURNING VARCHAR2(4000)) AS path_json,
            CASE WHEN {overflow} THEN 1 ELSE 0 END AS path_overflow
            FROM rag_chunks c JOIN rag_documents d ON d.document_id=c.document_id
            WHERE {where} ORDER BY c.document_id,c.chunk_set_id,path""",
            binds,
        )
        overflowed = any(row.get("path_overflow") for row in rows)
        rows = [row for row in rows if not row.get("path_overflow")]
        if overflowed:
            rows += await self.oracle._fetch_all(
                f"""SELECT c.document_id,c.chunk_set_id,d.file_name,
                JSON_VALUE(c.metadata_json,'$.section_path' RETURNING CLOB) AS path,
                JSON_QUERY(c.metadata_json,'$.section_path' RETURNING CLOB) AS path_json
                FROM rag_chunks c JOIN rag_documents d ON d.document_id=c.document_id
                WHERE {where} AND {overflow}""",
                binds,
            )
        grouped: dict[tuple[str, str, str], dict[str, None]] = defaultdict(dict)
        for row in rows:
            grouped[
                (str(row["document_id"]), str(row.get("chunk_set_id") or ""), str(row["file_name"]))
            ][_catalog_section_path(row)] = None
        return tuple(
            ReferenceDocument(did, cs or None, name, tuple(paths))
            for (did, cs, name), paths in grouped.items()
        )

    async def node(
        self, kb_id: str, node_id: str, reference_id: str | None = None
    ) -> dict[str, Any] | None:
        """graph nodeを現行の権限・所属・成果物で確認する。"""
        from app.clients.oracle import (
            _oracle_access_predicate_sql,
            _with_tenant_bind,
        )

        rows = await self.oracle._fetch_all(
            f"""SELECT e.entity_id,e.canonical_name,e.entity_type,e.chunk_set_id,
            d.document_id,d.file_name FROM rag_graph_entities e
            JOIN rag_chunk_sets cs ON cs.chunk_set_id=e.chunk_set_id AND cs.is_active=1
            JOIN rag_documents d ON d.document_id=cs.document_id
            JOIN rag_document_knowledge_bases m ON m.document_id=d.document_id
                AND m.knowledge_base_id=e.knowledge_base_id
            WHERE e.knowledge_base_id=:kb AND e.entity_id=:node_id
            AND {_oracle_access_predicate_sql(alias="d")}""",
            _with_tenant_bind({"kb": kb_id, "node_id": node_id}),
        )
        if rows and reference_id is None:
            return dict(rows[0])
        if reference_id is None:
            return None
        # 参照先の構造図が未構築でも、許可された実在の参照位置だけを読み込める。
        records = await self.oracle._fetch_all(
            f"""SELECT r.*,s.file_name AS source_name,t.file_name AS target_name,
            ts.chunk_set_id AS active_target_set,
            {_endpoint_entity_sql("source")},{_endpoint_entity_sql("target")}
            FROM rag_graph_references r
            JOIN rag_chunk_sets ss ON ss.chunk_set_id=r.source_chunk_set_id AND ss.is_active=1
            JOIN rag_documents s ON s.document_id=r.source_document_id
            JOIN rag_document_knowledge_bases sm ON sm.document_id=s.document_id
                AND sm.knowledge_base_id=r.knowledge_base_id
            LEFT JOIN rag_documents t ON t.document_id=r.target_document_id
                AND {_oracle_access_predicate_sql(alias="t")}
                AND EXISTS (SELECT 1 FROM rag_document_knowledge_bases tm
                    WHERE tm.document_id=t.document_id AND tm.knowledge_base_id=r.knowledge_base_id)
            LEFT JOIN rag_chunk_sets ts ON ts.document_id=t.document_id
                AND ts.chunk_set_id=r.target_chunk_set_id AND ts.is_active=1
            WHERE r.knowledge_base_id=:kb AND r.reference_id=:reference_id
            AND {_oracle_access_predicate_sql(alias="s")}""",
            _with_tenant_bind({"kb": kb_id, "reference_id": reference_id}),
        )
        if not records:
            return None
        view = self._view(kb_id, records[0])
        for endpoint in (view["source"], view["target"]):
            if endpoint and endpoint["id"] == node_id:
                return {
                    "entity_id": node_id,
                    "canonical_name": endpoint["name"],
                    "entity_type": endpoint["type"],
                    "reference_location": True,
                    "document_id": endpoint["document_id"],
                    "chunk_set_id": endpoint["chunk_set_id"],
                }
        return None

    async def page(
        self,
        kb_id: str,
        node_id: str,
        *,
        direction: str,
        offset: int,
        limit: int,
        reference_id: str | None = None,
    ) -> dict[str, Any]:
        """参照元・先を独立にページングする。未許可の対象名・識別子を返さない。"""
        from app.clients.oracle import _oracle_access_predicate_sql, _with_tenant_bind

        node = await self.node(kb_id, node_id, reference_id)
        if node is None:
            raise KeyError("対象が見つかりません。")
        path = None if node["entity_type"] == "document" else node["canonical_name"]
        side = "target" if direction == "incoming" else "source"
        where = f"r.{side}_document_id=:node_doc AND r.{side}_chunk_set_id=:node_set"
        if path is not None:
            if node.get("reference_location"):
                where += f" AND r.{side}_section_path=:node_path"
            elif direction == "outgoing":
                # 表示名は切り詰められていても、紐づく原文Chunkで参照元を特定する。
                where += """ AND EXISTS (SELECT 1 FROM rag_graph_entity_chunks nc
                    WHERE nc.entity_id=:node_id AND nc.chunk_id=r.source_chunk_id)"""
            else:
                # 図のノードは完全なパスを持たず表示名だけを保存するので、表示名で照合する。
                # 補建前の図は先頭で切り詰めた表示名を持つため、その集合も返す。
                where += f""" AND ({graph_display_name_sql("r.target_section_path")}=:node_path
                    OR SUBSTR(TRIM(REGEXP_REPLACE(r.target_section_path,
                    '[[:space:]]+', ' ')),1,512)=:node_path)"""
        if direction == "incoming":
            where += " AND t.document_id IS NOT NULL AND ts.chunk_set_id IS NOT NULL"
        # 図と一覧に出すのは解決済みと候補が複数の参照だけ。KB の外の参照は表示しない。
        # 件数がページごとにずれないよう、読み取りで状態を下げる条件も SQL で絞る。
        where += """ AND (r.resolution_status='ambiguous' OR (r.resolution_status='resolved'
            AND t.document_id IS NOT NULL AND ts.chunk_set_id IS NOT NULL))"""
        location_binds: dict[str, Any] = {}
        if ":node_path" in where:
            location_binds["node_path"] = path
        if ":node_id" in where:
            location_binds["node_id"] = node_id
        rows = await self.oracle._fetch_all(
            f"""SELECT r.*,s.file_name AS source_name,t.file_name AS target_name,
            ts.chunk_set_id AS active_target_set,
            {_endpoint_entity_sql("source")},{_endpoint_entity_sql("target")}
            FROM rag_graph_references r
            JOIN rag_chunk_sets ss ON ss.chunk_set_id=r.source_chunk_set_id AND ss.is_active=1
            JOIN rag_documents s ON s.document_id=r.source_document_id
            JOIN rag_document_knowledge_bases sm ON sm.document_id=s.document_id
                AND sm.knowledge_base_id=r.knowledge_base_id
            LEFT JOIN rag_documents t ON t.document_id=r.target_document_id
                AND {_oracle_access_predicate_sql(alias="t")}
                AND EXISTS (SELECT 1 FROM rag_document_knowledge_bases tm
                    WHERE tm.document_id=t.document_id AND tm.knowledge_base_id=r.knowledge_base_id)
            LEFT JOIN rag_chunk_sets ts ON ts.document_id=t.document_id
                AND ts.chunk_set_id=r.target_chunk_set_id AND ts.is_active=1
            WHERE r.knowledge_base_id=:kb AND {_oracle_access_predicate_sql(alias="s")}
                AND {where}
            ORDER BY r.source_chunk_id,r.reference_id
            OFFSET :offset ROWS FETCH NEXT :take ROWS ONLY""",
            _with_tenant_bind(
                {
                    "kb": kb_id,
                    "node_doc": node["document_id"],
                    "node_set": node["chunk_set_id"],
                    **location_binds,
                    "offset": offset,
                    "take": limit + 1,
                }
            ),
        )
        return {
            "items": [self._view(kb_id, row) for row in rows[:limit]],
            "offset": offset,
            "has_more": len(rows) > limit,
            "direction": direction,
        }

    @staticmethod
    def _view(kb_id: str, row: Any) -> dict[str, Any]:
        from rag_parser_core.language import current_pack

        from app.rag.graph_index import graph_section_node_id

        def endpoint(side: str) -> dict[str, Any]:
            path = row.get(f"{side}_section_path")
            name = (
                path or f"{current_pack().text('labels', 'whole_document')}: {row[f'{side}_name']}"
            )
            return {
                "id": row.get(f"{side}_entity_id")
                or graph_section_node_id(row[f"{side}_chunk_set_id"], kb_id, name),
                "name": name,
                "type": "section" if path else "document",
                "confidence": 1.0,
                "reference_id": row["reference_id"],
                "document_name": row[f"{side}_name"],
                "document_id": row[f"{side}_document_id"],
                "chunk_set_id": row[f"{side}_chunk_set_id"],
                "section_path": path,
            }

        status = row["resolution_status"]
        if status == "resolved" and not row.get("target_name"):
            status = "not_found"
        elif status == "resolved" and not row.get("active_target_set"):
            status = "version_unconfirmed"
        return {
            "id": row["reference_id"],
            "label": row["label"],
            "status": status,
            "source": endpoint("source"),
            "target": endpoint("target") if status == "resolved" else None,
        }
