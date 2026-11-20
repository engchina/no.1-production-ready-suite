"""公開済み案内の章節と依存を、原文の順で親ごとに読むための索引。"""

from collections import defaultdict
from typing import Any

from app.clients.oracle import _execute, _fetch_all, _fetch_one, _json_list
from app.rag.wiki_models import fingerprint


def section_id(document_id: str, path: list[str]) -> str:
    return fingerprint(["wiki-graph-section", document_id, path])


GRAPH_SCHEMA = """
CREATE TABLE rag_wiki_graph_nodes (
    document_id VARCHAR2(64) NOT NULL,
    node_id VARCHAR2(64) NOT NULL,
    parent_id VARCHAR2(64) NOT NULL,
    sibling_index NUMBER NOT NULL,
    child_count NUMBER NOT NULL,
    unit_count NUMBER NOT NULL,
    path_depth NUMBER NOT NULL,
    page_id VARCHAR2(64) NOT NULL REFERENCES rag_wiki_pages(page_id) ON DELETE CASCADE,
    CONSTRAINT rag_wiki_graph_node_pk PRIMARY KEY (document_id,node_id)
);
CREATE UNIQUE INDEX rag_wiki_graph_parent_idx ON rag_wiki_graph_nodes
    (document_id,parent_id,sibling_index);
CREATE INDEX rag_wiki_graph_page_idx ON rag_wiki_graph_nodes(page_id);
CREATE TABLE rag_wiki_graph_deps (
    page_id VARCHAR2(64) NOT NULL REFERENCES rag_wiki_pages(page_id) ON DELETE CASCADE,
    target_unit_id VARCHAR2(64) NOT NULL,
    relation VARCHAR2(32) NOT NULL,
    CONSTRAINT rag_wiki_graph_dep_pk PRIMARY KEY (page_id,target_unit_id,relation)
);
CREATE INDEX rag_wiki_graph_target_idx ON rag_wiki_graph_deps(target_unit_id);
""".strip()


def index_document(connection: Any, document_id: str, version: str) -> None:
    """公開と同じトランザクションで置換する。本文・埋め込み・関連の相手は読まない。"""
    binds = {"doc": document_id, "version": version}
    _execute(
        connection, "DELETE FROM rag_wiki_graph_nodes WHERE document_id=:doc", {"doc": document_id}
    )
    _execute(
        connection,
        """DELETE FROM rag_wiki_graph_deps WHERE page_id IN
        (SELECT page_id FROM rag_wiki_pages WHERE document_id=:doc)""",
        {"doc": document_id},
    )
    rows = _fetch_all(
        connection,
        """SELECT p.page_id,p.unit_id,
        JSON_QUERY(p.page_json,'$.unit.section_path' RETURNING CLOB) AS path
        FROM rag_wiki_pages p JOIN rag_chunks c ON c.chunk_id=p.source_chunk_id
        WHERE p.document_id=:doc AND p.source_fingerprint=:version
        ORDER BY c.chunk_index,p.unit_id""",
        binds,
    )
    counts: dict[str, int] = defaultdict(int)
    nodes: dict[str, dict[str, Any]] = {}
    for row in rows:
        path = _json_list(row["path"])
        parent = document_id
        for depth in range(1, len(path) + 2):
            is_unit = depth > len(path)
            node = str(row["unit_id"]) if is_unit else section_id(document_id, path[:depth])
            if node not in nodes:
                nodes[node] = {
                    "doc": document_id,
                    "node": node,
                    "parent": parent,
                    "position": counts[parent],
                    "depth": -1 if is_unit else depth,
                    "page": row["page_id"],
                    "units": 0,
                }
                counts[parent] += 1
            nodes[node]["units"] += 1
            parent = node
    if nodes:
        with connection.cursor() as cursor:
            cursor.executemany(
                """INSERT INTO rag_wiki_graph_nodes
                (document_id,node_id,parent_id,sibling_index,child_count,unit_count,path_depth,page_id)
                VALUES (:doc,:node,:parent,:position,:children,:units,:depth,:page)""",
                [dict(values, children=counts[node]) for node, values in nodes.items()],
            )
    _execute(
        connection,
        """INSERT INTO rag_wiki_graph_deps(page_id,target_unit_id,relation)
        SELECT DISTINCT p.page_id,j.target,j.relation FROM rag_wiki_pages p,
        JSON_TABLE(p.page_json,'$.navigation.dependencies[*]' COLUMNS
        (target VARCHAR2(64) PATH '$.unit_id', relation VARCHAR2(32) PATH '$.relation')) j
        WHERE p.document_id=:doc AND p.source_fingerprint=:version AND j.target<>p.unit_id""",
        binds,
    )


def backfill_graph_index(connection: Any) -> None:
    """文書単位で確定し、中断後は完了した版を飛ばす。"""
    documents = _fetch_all(
        connection,
        """SELECT document_id FROM rag_wiki_documents
        WHERE status='READY' ORDER BY document_id""",
        {},
    )
    for doc in documents:
        row = _fetch_one(
            connection,
            """SELECT published_fingerprint FROM rag_wiki_documents
            WHERE document_id=:doc AND status='READY' FOR UPDATE""",
            {"doc": doc["document_id"]},
        )
        if row and not _fetch_one(
            connection,
            """SELECT 1 AS present FROM rag_wiki_graph_nodes n
            JOIN rag_wiki_pages p ON p.page_id=n.page_id WHERE n.document_id=:doc
            AND p.source_fingerprint=:version FETCH FIRST 1 ROW ONLY""",
            {"doc": doc["document_id"], "version": row["published_fingerprint"]},
        ):
            index_document(connection, str(doc["document_id"]), str(row["published_fingerprint"]))
        connection.commit()
