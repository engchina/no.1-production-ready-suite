"""Wiki の保存・原文の読み取り。すべての利用経路を現在の検索範囲で絞る。"""

from __future__ import annotations

from array import array
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any
from uuid import uuid4

from app.clients.oracle import (
    OracleClient,
    _execute,
    _fetch_one,
    _filter_id_values,
    _int_value,
    _json_bind,
    _json_input_sizes,
    _json_loads,
    _oracle_in_predicate,
    _oracle_knowledge_base_access_predicate_sql,
    _oracle_retrieval_where,
    _oracle_text_query,
    _require_active_knowledge_base,
    _retrieved_chunk_from_row,
    _with_tenant_bind,
    oracle_text_index_parameters_sql,
)
from app.rag.request_context import current_audit_request_context
from app.rag.wiki_models import WIKI_BUILD_KEY, WikiPage, WikiUnit, fingerprint
from app.schemas.search import RetrievedChunk


def wiki_schema_sql() -> str:
    return f"""
CREATE TABLE rag_wiki_documents (
    document_id VARCHAR2(64) PRIMARY KEY,
    source_fingerprint CHAR(64) NOT NULL,
    compiler_fingerprint CHAR(64),
    revision_fingerprint CHAR(64),
    chunk_sets_json JSON,
    owner_user_uuid VARCHAR2(64),
    published_fingerprint CHAR(64),
    status VARCHAR2(16) DEFAULT 'QUEUED' NOT NULL,
    lease_token VARCHAR2(64),
    lease_until TIMESTAMP WITH TIME ZONE,
    failure_code VARCHAR2(64),
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT SYSTIMESTAMP NOT NULL,
    CONSTRAINT rag_wiki_doc_fk FOREIGN KEY (document_id)
        REFERENCES rag_documents(document_id) ON DELETE CASCADE,
    CONSTRAINT rag_wiki_status_ck CHECK (status IN ('QUEUED','RUNNING','READY','FAILED'))
);
CREATE TABLE rag_wiki_pages (
    page_id VARCHAR2(64) PRIMARY KEY,
    document_id VARCHAR2(64) NOT NULL,
    chunk_set_id VARCHAR2(64) NOT NULL,
    source_chunk_id VARCHAR2(128) NOT NULL,
    unit_id VARCHAR2(64) NOT NULL,
    source_fingerprint CHAR(64) NOT NULL,
    page_json CLOB CHECK (page_json IS JSON),
    search_text CLOB,
    embedding VECTOR(1536, FLOAT32),
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT SYSTIMESTAMP NOT NULL,
    CONSTRAINT rag_wiki_page_doc_fk FOREIGN KEY (document_id)
        REFERENCES rag_documents(document_id) ON DELETE CASCADE,
    CONSTRAINT rag_wiki_page_set_fk FOREIGN KEY (chunk_set_id)
        REFERENCES rag_chunk_sets(chunk_set_id) ON DELETE CASCADE,
    CONSTRAINT rag_wiki_page_chunk_fk FOREIGN KEY (source_chunk_id)
        REFERENCES rag_chunks(chunk_id) ON DELETE CASCADE
);
CREATE INDEX rag_wiki_page_doc_idx ON rag_wiki_pages(document_id,source_fingerprint);
CREATE INDEX rag_wiki_page_set_idx ON rag_wiki_pages(chunk_set_id);
CREATE INDEX rag_wiki_page_chunk_idx ON rag_wiki_pages(source_chunk_id);
CREATE INDEX rag_wiki_queue_idx ON rag_wiki_documents(status,lease_until);
CREATE INDEX rag_wiki_page_text_idx ON rag_wiki_pages(search_text)
    INDEXTYPE IS CTXSYS.CONTEXT {oracle_text_index_parameters_sql()};
""".strip()


@dataclass(frozen=True)
class WikiSnapshot:
    document_id: str
    source_fingerprint: str
    units: tuple[WikiUnit, ...]
    compiler_fingerprint: str = ""
    revision_fingerprint: str = ""


CURRENT_WIKI_SOURCE_SQL = """NOT EXISTS (
    SELECT 1 FROM rag_chunk_sets current_set
    JOIN rag_document_recipes current_recipe ON current_recipe.recipe_id=current_set.recipe_id
    WHERE current_set.document_id=c.document_id AND current_set.is_active=1
      AND current_set.status='INDEXED' AND NOT EXISTS (
        SELECT 1 FROM JSON_TABLE(w.chunk_sets_json,'$[*]'
            COLUMNS (set_id VARCHAR2(64) PATH '$')) saved_set
        WHERE saved_set.set_id=current_set.chunk_set_id)
) AND NOT EXISTS (
    SELECT 1 FROM JSON_TABLE(w.chunk_sets_json,'$[*]'
        COLUMNS (set_id VARCHAR2(64) PATH '$')) saved_set
    WHERE NOT EXISTS (SELECT 1 FROM rag_chunk_sets current_set
        JOIN rag_document_recipes current_recipe
          ON current_recipe.recipe_id=current_set.recipe_id
        WHERE current_set.document_id=c.document_id AND current_set.is_active=1
          AND current_set.status='INDEXED' AND current_set.chunk_set_id=saved_set.set_id)
)"""


def wiki_scope(filters: dict[str, str]) -> tuple[str, dict[str, object]]:
    """元の検索条件に、有効な KB からの Wiki 利用という条件だけを加える。"""
    # 明示した版を現在の版に読み替えない。退役版の指定では Wiki は使わない。
    selected = dict(filters)
    where, binds = _oracle_retrieval_where(selected)
    if selected.get("chunk_set_id", "").strip():
        where += """ AND EXISTS (SELECT 1 FROM rag_chunk_sets wiki_current_set
            JOIN rag_document_recipes wiki_current_recipe
              ON wiki_current_recipe.recipe_id=wiki_current_set.recipe_id
            WHERE wiki_current_set.chunk_set_id=c.chunk_set_id
              AND wiki_current_set.is_active=1 AND wiki_current_set.status='INDEXED')"""
    ids = _filter_id_values(selected.get("knowledge_base_id"))
    requested, extra = (
        _oracle_in_predicate("kb.knowledge_base_id", "wiki_kb", ids) if ids else ("1=1", {})
    )
    binds.update(extra)
    # 重複の文書の正本を検索できる既存の membership と同じ対応を使う。
    enabled = f"""EXISTS (
        SELECT 1 FROM rag_document_knowledge_bases wm
        JOIN rag_knowledge_bases kb ON kb.knowledge_base_id=wm.knowledge_base_id
        JOIN rag_documents member_doc ON member_doc.document_id=wm.document_id
        WHERE (wm.document_id=c.document_id OR member_doc.duplicate_of_document_id=c.document_id)
          AND kb.status='ACTIVE' AND {requested}
          AND {_oracle_knowledge_base_access_predicate_sql(alias="kb")}
          AND JSON_VALUE(kb.retrieval_config, '$.{WIKI_BUILD_KEY}')='true'
    )"""
    return f"({where}) AND {enabled}", binds


class WikiStore:
    def __init__(self, oracle: OracleClient) -> None:
        self.oracle = oracle

    async def set_enabled(self, kb_id: str, enabled: bool) -> None:
        def operation(connection: Any) -> None:
            kb = _fetch_one(
                connection,
                f"""SELECT knowledge_base_id,retrieval_config
                FROM rag_knowledge_bases WHERE knowledge_base_id=:kb_id
                AND {_oracle_knowledge_base_access_predicate_sql()} FOR UPDATE""",
                _with_tenant_bind({"kb_id": kb_id}),
            )
            if not kb:
                raise KeyError("ナレッジベースが見つかりません。")
            _require_active_knowledge_base(connection, kb_id)
            settings = dict(_json_loads(kb.get("retrieval_config")) or {})
            settings[WIKI_BUILD_KEY] = enabled
            _execute(
                connection,
                """UPDATE rag_knowledge_bases SET retrieval_config=:settings,
                wiki_build_actor_uuid=:actor,
                updated_at=SYSTIMESTAMP WHERE knowledge_base_id=:kb_id""",
                {
                    "kb_id": kb_id,
                    "settings": _json_bind(settings),
                    "actor": current_audit_request_context().user_uuid,
                },
                input_sizes=_json_input_sizes("settings"),
            )

        await self.oracle._run_transaction(operation)

    async def document_ids(self, filters: dict[str, str]) -> list[str]:
        where, binds = wiki_scope(filters)
        rows = await self.oracle._fetch_all(
            f"""SELECT DISTINCT c.document_id
            FROM rag_chunks c JOIN rag_documents d ON d.document_id=c.document_id
            WHERE {where} ORDER BY c.document_id""",
            binds,
        )
        return [str(row["document_id"]) for row in rows]

    async def enabled_knowledge_bases(self) -> list[tuple[str, str | None]]:
        rows = await self.oracle._fetch_all(
            f"""SELECT kb.knowledge_base_id,
            kb.wiki_build_actor_uuid FROM rag_knowledge_bases kb
            WHERE kb.status='ACTIVE' AND {_oracle_knowledge_base_access_predicate_sql(alias="kb")}
            AND JSON_VALUE(kb.retrieval_config,'$.{WIKI_BUILD_KEY}')='true'
            ORDER BY kb.knowledge_base_id""",
            _with_tenant_bind({}),
        )
        return [
            (
                str(row["knowledge_base_id"]),
                str(row["wiki_build_actor_uuid"]) if row.get("wiki_build_actor_uuid") else None,
            )
            for row in rows
        ]

    async def revision_fingerprint(self, document_id: str) -> str | None:
        where, binds = wiki_scope({"document_id": document_id, "include_superseded": "true"})
        rows = await self.oracle._fetch_all(
            f"""SELECT c.chunk_set_id,
            MAX(d.content_sha256) AS content_sha256,COUNT(*) AS chunk_count FROM rag_chunks c
            JOIN rag_documents d ON d.document_id=c.document_id
            WHERE {where} GROUP BY c.chunk_set_id ORDER BY c.chunk_set_id""",
            binds,
        )
        return (
            fingerprint(
                [
                    [
                        str(row["chunk_set_id"]),
                        str(row.get("content_sha256") or ""),
                        _int_value(row["chunk_count"]),
                    ]
                    for row in rows
                ]
            )
            if rows
            else None
        )

    async def document_state(self, document_id: str) -> dict[str, object] | None:
        # 呼出側は document_ids / source_units で可視性を確認した ID だけを渡す。
        return await self.oracle._fetch_one(
            """SELECT source_fingerprint,compiler_fingerprint,
            revision_fingerprint,status,failure_code,owner_user_uuid,
            CASE WHEN lease_until>SYSTIMESTAMP THEN 1 ELSE 0 END leased
            FROM rag_wiki_documents WHERE document_id=:id""",
            {"id": document_id},
        )

    async def catalog(self, filters: dict[str, str], compiler: str) -> list[dict[str, str]]:
        where, binds = wiki_scope(filters)
        rows = await self.oracle._fetch_all(
            f"""SELECT DISTINCT d.document_id,d.file_name
            FROM rag_chunks c JOIN rag_documents d ON d.document_id=c.document_id
            JOIN rag_wiki_documents w ON w.document_id=c.document_id
            JOIN rag_wiki_pages p ON p.source_chunk_id=c.chunk_id
              AND p.chunk_set_id=c.chunk_set_id AND p.source_fingerprint=w.published_fingerprint
            WHERE {where} AND w.status='READY' AND w.compiler_fingerprint=:wiki_compiler
            AND w.source_fingerprint=w.published_fingerprint AND {CURRENT_WIKI_SOURCE_SQL}
            ORDER BY d.document_id""",
            {**binds, "wiki_compiler": compiler},
        )
        return [
            {"document_id": str(row["document_id"]), "title": str(row.get("file_name") or "")}
            for row in rows
        ]

    async def status_counts(self, filters: dict[str, str]) -> dict[str, int]:
        where, binds = _oracle_retrieval_where(
            {key: value for key, value in filters.items() if key != "chunk_set_id"}
        )
        rows = await self.oracle._fetch_all(
            f"""SELECT state,COUNT(*) AS total FROM (
            SELECT DISTINCT c.document_id,CASE WHEN w.status='READY'
                AND NOT ({CURRENT_WIKI_SOURCE_SQL}) THEN 'STALE'
                ELSE NVL(w.status,'NOT_BUILT') END AS state
            FROM rag_chunks c JOIN rag_documents d ON d.document_id=c.document_id
            LEFT JOIN rag_wiki_documents w ON w.document_id=c.document_id
            WHERE {where}) GROUP BY state""",
            binds,
        )
        return {str(row["state"]): _int_value(row["total"]) for row in rows}

    async def source_units(self, document_id: str) -> WikiSnapshot | None:
        where, binds = wiki_scope({"document_id": document_id, "include_superseded": "true"})
        rows = await self.oracle._fetch_all(
            f"""SELECT c.chunk_id,c.chunk_set_id,c.chunk_index,
            c.chunk_text,c.metadata_json,d.file_name FROM rag_chunks c
            JOIN rag_documents d ON d.document_id=c.document_id
            WHERE {where} ORDER BY c.chunk_set_id,c.chunk_index,c.chunk_id""",
            binds,
        )
        originals = []
        from rag_parser_core.extraction import is_derived_content

        for row in rows:
            metadata = dict(_json_loads(row.get("metadata_json")) or {})
            if not row.get("chunk_set_id") or is_derived_content(
                str(metadata.get("content_kind") or ""), metadata
            ):
                continue
            originals.append((row, metadata))
        parents = {str(row["chunk_id"]) for row, _ in originals}
        units = []
        for row, metadata in originals:
            parent = metadata.get("parent_chunk_id")
            if parent and str(parent) in parents:
                continue
            text = str(row.get("chunk_text") or "")
            if not text.strip():
                continue
            path = metadata.get("section_path")
            heading = (
                [str(value) for value in path]
                if isinstance(path, list)
                else str(path).split(" > ")
                if path
                else []
            )
            chunk_set = str(row["chunk_set_id"])
            chunk_id = str(row["chunk_id"])
            units.append(
                WikiUnit(
                    unit_id=fingerprint([chunk_set, chunk_id]),
                    document_id=document_id,
                    chunk_set_id=chunk_set,
                    source_chunk_ids=[chunk_id],
                    section_id=fingerprint([chunk_set, heading]),
                    section_path=heading,
                    title=heading[-1] if heading else str(row.get("file_name") or ""),
                    text=text,
                    source_hash=fingerprint(text),
                )
            )
        if not units:
            return None
        return WikiSnapshot(
            document_id, fingerprint([unit.model_dump() for unit in units]), tuple(units)
        )

    async def enqueue(self, snapshot: WikiSnapshot) -> bool:
        def operation(connection: Any) -> bool:
            row = _fetch_one(
                connection,
                "SELECT document_id FROM rag_documents WHERE document_id=:id FOR UPDATE",
                {"id": snapshot.document_id},
            )
            if not row:
                return False
            prior = _fetch_one(
                connection,
                """SELECT source_fingerprint,compiler_fingerprint,status
                FROM rag_wiki_documents WHERE document_id=:id""",
                {"id": snapshot.document_id},
            )
            if (
                prior
                and prior["source_fingerprint"] == snapshot.source_fingerprint
                and prior["compiler_fingerprint"] == snapshot.compiler_fingerprint
                and prior["status"] in {"READY", "RUNNING", "QUEUED"}
            ):
                _execute(
                    connection,
                    """UPDATE rag_wiki_documents SET revision_fingerprint=:revision
                    WHERE document_id=:id""",
                    {"id": snapshot.document_id, "revision": snapshot.revision_fingerprint},
                )
                return False
            _execute(
                connection,
                """MERGE INTO rag_wiki_documents target
                USING (SELECT :id document_id FROM dual) source
                ON (target.document_id=source.document_id)
                WHEN MATCHED THEN UPDATE SET source_fingerprint=:fingerprint,
                    compiler_fingerprint=:compiler,revision_fingerprint=:revision,
                    chunk_sets_json=:sets,owner_user_uuid=:actor,status='QUEUED',
                    lease_token=NULL,lease_until=NULL,failure_code=NULL,updated_at=SYSTIMESTAMP
                WHEN NOT MATCHED THEN INSERT (document_id,source_fingerprint,compiler_fingerprint,
                    revision_fingerprint,chunk_sets_json,owner_user_uuid,status)
                    VALUES (:id,:fingerprint,:compiler,:revision,:sets,:actor,'QUEUED')""",
                {
                    "id": snapshot.document_id,
                    "fingerprint": snapshot.source_fingerprint,
                    "compiler": snapshot.compiler_fingerprint,
                    "revision": snapshot.revision_fingerprint,
                    "sets": _json_bind(sorted({u.chunk_set_id for u in snapshot.units})),
                    "actor": current_audit_request_context().user_uuid,
                },
                input_sizes=_json_input_sizes("sets"),
            )
            return True

        return await self.oracle._run_transaction(operation)

    async def claim(self, snapshot: WikiSnapshot, *, lease_seconds: int) -> str | None:
        token = uuid4().hex

        def operation(connection: Any) -> str | None:
            row = _fetch_one(
                connection,
                """SELECT status,source_fingerprint,compiler_fingerprint,
                CASE WHEN lease_until>SYSTIMESTAMP THEN 1 ELSE 0 END AS leased
                FROM rag_wiki_documents WHERE document_id=:id FOR UPDATE""",
                {"id": snapshot.document_id},
            )
            if (
                not row
                or row["source_fingerprint"] != snapshot.source_fingerprint
                or row["compiler_fingerprint"] != snapshot.compiler_fingerprint
                or (row["status"] == "RUNNING" and row["leased"])
                or row["status"] == "READY"
            ):
                return None
            _execute(
                connection,
                """UPDATE rag_wiki_documents SET status='RUNNING',lease_token=:token,
                lease_until=SYSTIMESTAMP+NUMTODSINTERVAL(:seconds,'SECOND'),updated_at=SYSTIMESTAMP
                WHERE document_id=:id""",
                {"token": token, "seconds": lease_seconds, "id": snapshot.document_id},
            )
            return token

        return await self.oracle._run_transaction(operation)

    async def checkpoint(
        self,
        snapshot: WikiSnapshot,
        token: str,
        pages: Sequence[WikiPage],
        *,
        vectors: Sequence[Sequence[float]] = (),
        original_chars: int = 4096,
    ) -> None:
        if vectors and len(vectors) != len(pages):
            raise ValueError("Wiki の埋め込みの件数が一致しません。")
        authoritative = {unit.unit_id: unit for unit in snapshot.units}
        if any(authoritative.get(page.unit.unit_id) != page.unit for page in pages):
            raise ValueError("Wiki の原文と構築対象が一致しません。")

        def operation(connection: Any) -> None:
            self._require_lease(connection, snapshot, token)
            for index, page in enumerate(pages):
                key = fingerprint(
                    [snapshot.document_id, snapshot.source_fingerprint, page.unit.unit_id]
                )
                values: dict[str, object] = {
                    "id": key,
                    "document_id": snapshot.document_id,
                    "chunk_set_id": page.unit.chunk_set_id,
                    "source_chunk_id": page.unit.source_chunk_ids[0],
                    "unit_id": page.unit.unit_id,
                    "fingerprint": snapshot.source_fingerprint,
                    "page": _json_bind(page.model_dump()),
                    "text": page.search_text(original_chars=original_chars),
                }
                _execute(
                    connection,
                    """MERGE INTO rag_wiki_pages target USING (SELECT :id page_id FROM dual) source
                    ON (target.page_id=source.page_id) WHEN MATCHED THEN UPDATE SET page_json=:page,
                    search_text=:text,updated_at=SYSTIMESTAMP WHEN NOT MATCHED THEN INSERT
                    (page_id,document_id,chunk_set_id,source_chunk_id,unit_id,
                     source_fingerprint,page_json,search_text)
                    VALUES (:id,:document_id,:chunk_set_id,:source_chunk_id,:unit_id,
                        :fingerprint,:page,:text)""",
                    values,
                    input_sizes=_json_input_sizes("page", "text"),
                )
                if vectors:
                    _execute(
                        connection,
                        "UPDATE rag_wiki_pages SET embedding=:vector WHERE page_id=:id",
                        {"id": key, "vector": array("f", vectors[index])},
                    )

        await self.oracle._run_transaction(operation)

    @staticmethod
    def _require_lease(connection: Any, snapshot: WikiSnapshot, token: str) -> None:
        row = _fetch_one(
            connection,
            """SELECT document_id FROM rag_wiki_documents
            WHERE document_id=:id AND source_fingerprint=:fingerprint AND lease_token=:token
              AND compiler_fingerprint=:compiler
              AND lease_until>SYSTIMESTAMP AND status='RUNNING' FOR UPDATE""",
            {
                "id": snapshot.document_id,
                "fingerprint": snapshot.source_fingerprint,
                "token": token,
                "compiler": snapshot.compiler_fingerprint,
            },
        )
        if not row:
            raise ValueError("Wiki の原文または構築の所有権が変更されました。")

    async def saved_pages(self, snapshot: WikiSnapshot) -> list[WikiPage]:
        rows = await self.oracle._fetch_all(
            """SELECT page_json FROM rag_wiki_pages
            WHERE document_id=:id AND source_fingerprint=:fingerprint ORDER BY unit_id""",
            {"id": snapshot.document_id, "fingerprint": snapshot.source_fingerprint},
        )
        return [WikiPage.model_validate(_json_loads(row["page_json"])) for row in rows]

    async def finish(
        self, snapshot: WikiSnapshot, token: str, *, failure_code: str | None = None
    ) -> None:
        def operation(connection: Any) -> None:
            self._require_lease(connection, snapshot, token)
            if failure_code is None:
                count = _fetch_one(
                    connection,
                    """SELECT COUNT(*) AS total FROM rag_wiki_pages
                    WHERE document_id=:id AND source_fingerprint=:fingerprint
                    AND embedding IS NOT NULL""",
                    {"id": snapshot.document_id, "fingerprint": snapshot.source_fingerprint},
                )
                if not count or _int_value(count["total"]) != len(snapshot.units):
                    raise ValueError("Wiki の原文単位または埋め込みが未完了です。")
            _execute(
                connection,
                """UPDATE rag_wiki_documents SET status=:status,
                published_fingerprint=CASE WHEN :status='READY'
                    THEN source_fingerprint ELSE published_fingerprint END,
                failure_code=:failure,lease_token=NULL,lease_until=NULL,updated_at=SYSTIMESTAMP
                WHERE document_id=:id""",
                {
                    "id": snapshot.document_id,
                    "status": "FAILED" if failure_code else "READY",
                    "failure": failure_code,
                },
            )

        await self.oracle._run_transaction(operation)

    async def available_pages(
        self,
        filters: dict[str, str],
        *,
        query_vector: Sequence[float] | None = None,
        limit: int | None = None,
        compiler_fingerprint: str | None = None,
        document_ids: Sequence[str] | None = None,
        unit_ids: Sequence[str] | None = None,
    ) -> list[WikiPage]:
        where, binds = wiki_scope(filters)
        for column, values in (("c.document_id", document_ids), ("p.unit_id", unit_ids)):
            if values is not None:
                if not values:
                    return []
                clause, added = _oracle_in_predicate(
                    column, "wiki_" + column.replace(".", "_"), values
                )
                where += " AND " + clause
                binds.update(added)
        if compiler_fingerprint is not None:
            where += " AND w.compiler_fingerprint=:wiki_compiler"
            binds["wiki_compiler"] = compiler_fingerprint
        order = "p.document_id,p.unit_id"
        if query_vector is not None:
            binds["wiki_vector"] = array("f", query_vector)
            order = "VECTOR_DISTANCE(p.embedding,:wiki_vector,COSINE),p.document_id,p.unit_id"
        tail = ""
        if limit is not None:
            binds["wiki_limit"] = limit
            tail = "FETCH FIRST :wiki_limit ROWS ONLY"
        rows = await self.oracle._fetch_all(
            f"""SELECT p.page_json FROM rag_wiki_pages p
            JOIN rag_wiki_documents w ON w.document_id=p.document_id
            JOIN rag_chunks c ON c.chunk_id=p.source_chunk_id AND c.chunk_set_id=p.chunk_set_id
            JOIN rag_documents d ON d.document_id=c.document_id
            WHERE {where} AND w.status='READY' AND w.published_fingerprint=w.source_fingerprint
              AND p.source_fingerprint=w.published_fingerprint
              AND {CURRENT_WIKI_SOURCE_SQL}
            ORDER BY {order} {tail}""",
            binds,
        )
        return [WikiPage.model_validate(_json_loads(row["page_json"])) for row in rows]

    async def text_pages(
        self,
        question: str,
        filters: dict[str, str],
        *,
        compiler: str,
        limit: int,
        document_ids: Sequence[str] | None = None,
    ) -> list[WikiPage]:
        expression = _oracle_text_query(question)
        if not expression:
            return []
        where, binds = wiki_scope(filters)
        if document_ids is not None:
            if not document_ids:
                return []
            clause, values = _oracle_in_predicate("c.document_id", "wiki_text_doc", document_ids)
            where += " AND " + clause
            binds.update(values)
        rows = await self.oracle._fetch_all(
            f"""SELECT /*+ DOMAIN_INDEX_SORT LEADING(p) USE_HASH(w c d) */
            p.unit_id,SCORE(1) AS text_score FROM rag_wiki_pages p
            JOIN rag_wiki_documents w ON w.document_id=p.document_id
            JOIN rag_chunks c ON c.chunk_id=p.source_chunk_id AND c.chunk_set_id=p.chunk_set_id
            JOIN rag_documents d ON d.document_id=c.document_id
            WHERE CONTAINS(p.search_text,:wiki_text,1)>0
              AND {where} AND w.status='READY' AND w.compiler_fingerprint=:wiki_compiler
              AND w.published_fingerprint=w.source_fingerprint
              AND p.source_fingerprint=w.published_fingerprint AND {CURRENT_WIKI_SOURCE_SQL}
            ORDER BY text_score DESC,p.document_id,p.unit_id
            FETCH FIRST :wiki_limit ROWS ONLY""",
            {**binds, "wiki_text": expression, "wiki_compiler": compiler, "wiki_limit": limit},
        )
        if not rows:
            return []
        # SCORE を確定してから別の SQL で本文を読む。結合の後段で再評価しない。
        ids = [str(row["unit_id"]) for row in rows]
        hydrated = await self.available_pages(
            filters, unit_ids=ids, compiler_fingerprint=compiler, document_ids=document_ids
        )
        pages = {page.unit.unit_id: page for page in hydrated}
        return [pages[uid] for uid in ids if uid in pages]

    async def read_sources(
        self, pages: Sequence[WikiPage], filters: dict[str, str]
    ) -> list[RetrievedChunk]:
        if not pages:
            return []
        ids = list(dict.fromkeys(sid for page in pages for sid in page.unit.source_chunk_ids))
        where, binds = wiki_scope(filters)
        condition, values = _oracle_in_predicate("c.chunk_id", "wiki_source", ids)
        rows = await self.oracle._fetch_all(
            f"""SELECT c.document_id,c.chunk_id,c.chunk_text,
            c.metadata_json,c.chunk_index,c.chunk_set_id,d.file_name,d.category_name,
            d.superseded_by_document_id,0 AS score FROM rag_chunks c
            JOIN rag_documents d ON d.document_id=c.document_id
            WHERE {where} AND {condition} ORDER BY c.document_id,c.chunk_index,c.chunk_id""",
            {**binds, **values},
        )
        found = {str(row["chunk_id"]): row for row in rows}
        valid = {
            sid
            for page in pages
            for sid in page.unit.source_chunk_ids
            if sid in found
            and str(found[sid]["document_id"]) == page.unit.document_id
            and str(found[sid]["chunk_set_id"]) == page.unit.chunk_set_id
            and fingerprint(str(found[sid]["chunk_text"])) == page.unit.source_hash
        }
        return [_retrieved_chunk_from_row(found[sid]) for sid in ids if sid in valid]
