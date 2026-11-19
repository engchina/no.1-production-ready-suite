"""Wiki の構築の保存と原文の照合。すべての読取を現在の検索範囲と有効な KB で絞る。"""

from __future__ import annotations

import re
from array import array
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any
from uuid import uuid4

from rag_parser_core.extraction import is_derived_content
from rag_parser_core.xml_tree_json import XML_TREE_DOCUMENT_ARTIFACT_KEY

from app.clients.oracle import (
    OracleClient,
    _execute,
    _fetch_one,
    _filter_id_values,
    _int_value,
    _json_bind,
    _json_input_sizes,
    _json_list,
    _json_loads,
    _oracle_in_predicate,
    _oracle_knowledge_base_access_predicate_sql,
    _oracle_retrieval_where,
    _oracle_retrieval_where_split,
    _oracle_text_query,
    _require_active_knowledge_base,
    _retrieved_chunk_from_row,
    _with_tenant_bind,
    oracle_text_index_parameters_sql,
)
from app.rag.request_context import current_audit_request_context
from app.rag.wiki_models import (
    WIKI_BUILD_KEY,
    WIKI_RELATION_PRIORITY,
    WikiPage,
    WikiUnit,
    fingerprint,
)
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


def wiki_link_schema_sql() -> str:
    """文書をまたぐ関連と、文書ごとの関連の構築の状態（#5492）。

    関連は起点と終点の案内（保存版の指紋を含む page_id）を外部キーで持ち、文書・案内の削除で消える。
    """
    relations = ",".join(f"'{relation}'" for relation in WIKI_RELATION_PRIORITY)
    return f"""
CREATE TABLE rag_wiki_link_documents (
    document_id VARCHAR2(64) PRIMARY KEY,
    source_fingerprint CHAR(64) NOT NULL,
    link_fingerprint CHAR(64) NOT NULL,
    done_json JSON,
    status VARCHAR2(16) DEFAULT 'QUEUED' NOT NULL,
    lease_token VARCHAR2(64),
    lease_until TIMESTAMP WITH TIME ZONE,
    failure_code VARCHAR2(64),
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT SYSTIMESTAMP NOT NULL,
    CONSTRAINT rag_wiki_link_doc_fk FOREIGN KEY (document_id)
        REFERENCES rag_documents(document_id) ON DELETE CASCADE,
    CONSTRAINT rag_wiki_link_status_ck
        CHECK (status IN ('QUEUED','RUNNING','READY','FAILED'))
);
CREATE TABLE rag_wiki_links (
    source_page_id VARCHAR2(64) NOT NULL,
    target_page_id VARCHAR2(64) NOT NULL,
    source_document_id VARCHAR2(64) NOT NULL,
    source_unit_id VARCHAR2(64) NOT NULL,
    source_fingerprint CHAR(64) NOT NULL,
    target_document_id VARCHAR2(64) NOT NULL,
    target_unit_id VARCHAR2(64) NOT NULL,
    target_fingerprint CHAR(64) NOT NULL,
    relation VARCHAR2(32) NOT NULL,
    reason VARCHAR2(800),
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT SYSTIMESTAMP NOT NULL,
    CONSTRAINT rag_wiki_link_pk PRIMARY KEY (source_page_id,target_page_id),
    CONSTRAINT rag_wiki_link_source_fk FOREIGN KEY (source_page_id)
        REFERENCES rag_wiki_pages(page_id) ON DELETE CASCADE,
    CONSTRAINT rag_wiki_link_target_fk FOREIGN KEY (target_page_id)
        REFERENCES rag_wiki_pages(page_id) ON DELETE CASCADE,
    CONSTRAINT rag_wiki_link_relation_ck CHECK (relation IN ({relations}))
);
CREATE INDEX rag_wiki_link_target_page_idx ON rag_wiki_links(target_page_id);
CREATE INDEX rag_wiki_link_source_unit_idx ON rag_wiki_links(source_unit_id);
CREATE INDEX rag_wiki_link_target_unit_idx ON rag_wiki_links(target_unit_id);
CREATE INDEX rag_wiki_link_queue_idx ON rag_wiki_link_documents(status,lease_until);
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


# 検索に使える案内: 公開済み・現在の構築条件・現在の原文の保存版の案内だけ。
WIKI_PAGE_FROM = """FROM rag_wiki_pages p
JOIN rag_wiki_documents w ON w.document_id=p.document_id
JOIN rag_chunks c ON c.chunk_id=p.source_chunk_id AND c.chunk_set_id=p.chunk_set_id
JOIN rag_documents d ON d.document_id=c.document_id"""
WIKI_PUBLISHED_SQL = f"""w.status='READY' AND w.compiler_fingerprint=:wiki_compiler
AND w.published_fingerprint=w.source_fingerprint AND p.source_fingerprint=w.published_fingerprint
AND {CURRENT_WIKI_SOURCE_SQL}"""
# 目録に写す文書の版の属性（解析器が抽出に残した値。モデルや規則で推測しない。#5044）。
SOURCE_IDENTITY_FIELDS = {
    "source_key": "document_id",
    "revision_key": "revision_id",
    "effective_date": "revision_date",
    "source_status": "revision_status",
}


def _document_identity(row: dict[str, Any]) -> dict[str, str]:
    """文書の形式に関わらない版の属性（版の状態・分類の有効期間）。"""
    identity = {"version_status": str(row.get("doc_version_status") or "current")}
    for key in ("effective_from", "effective_to"):
        value = row.get(f"doc_{key}")
        if value:
            identity[key] = str(value)
    return identity


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


@dataclass(frozen=True)
class WikiScope:
    """1 問の検索の範囲を ``WikiStore.resolve`` で 1 回だけ決めた結果（#5499）。

    ``sources`` は検索に使える案内の（文書 ID, chunk_set ID, 公開の指紋）。後の読取はこの集合と
    chunk の行の条件（``row_sql``）だけで絞り、``wiki_scope`` と公開の条件を評価し直さない。
    回答に使う原文は ``read_sources`` が範囲・所属・権限・原文の保存版を照合し直す。
    """

    catalog: tuple[dict[str, Any], ...] = ()
    sources: tuple[tuple[str, str, str], ...] = ()
    row_sql: str = "1 = 1"
    row_binds: Mapping[str, object] = field(default_factory=dict)


# 範囲を決めた後の読取: 案内と、行の条件に使う原文の chunk・文書だけを読む（#5499）。
WIKI_SCOPED_FROM = """FROM rag_wiki_pages p
JOIN rag_chunks c ON c.chunk_id=p.source_chunk_id AND c.chunk_set_id=p.chunk_set_id
JOIN rag_documents d ON d.document_id=c.document_id"""
# Oracle の IN の値の並びの上限。
_IN_LIST_LIMIT = 1000


def _uses_bind(sql: str, name: str) -> bool:
    return re.search(rf":{re.escape(name)}(?!\w)", sql) is not None


def _in_many(column: str, prefix: str, values: Sequence[str]) -> tuple[str, dict[str, object]]:
    """上限を超える値の並びを、上限ごとの IN を OR で結んだ条件にする。"""
    clauses: list[str] = []
    binds: dict[str, object] = {}
    for index in range(0, len(values), _IN_LIST_LIMIT):
        clause, added = _oracle_in_predicate(
            column, f"{prefix}{index // _IN_LIST_LIMIT}", values[index : index + _IN_LIST_LIMIT]
        )
        clauses.append(clause)
        binds.update(added)
    return "(" + " OR ".join(clauses) + ")", binds


def _scoped_where(scope: WikiScope) -> tuple[str, dict[str, object]]:
    """``resolve`` で決めた案内の集合と chunk の行の条件（重い範囲の条件は評価し直さない）。

    chunk_set は 1 つの文書に属し、公開の指紋は文書の ID を含む原文の単位から作るので、
    chunk_set と指紋の組み合わせで、範囲を決めたときの（文書・保存版）の案内だけが残る。
    """
    chunk_sets, binds = _in_many(
        "p.chunk_set_id", "wiki_set", sorted({chunk_set for _, chunk_set, _ in scope.sources})
    )
    published, added = _in_many(
        "p.source_fingerprint", "wiki_fp", sorted({value for _, _, value in scope.sources})
    )
    binds.update(added)
    binds.update(scope.row_binds)
    return f"{chunk_sets} AND {published} AND ({scope.row_sql})", binds


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

    @staticmethod
    def _document_states_sql(filters: dict[str, str]) -> tuple[str, dict[str, object]]:
        """所属文書ごとの構築状態の行（文書の一覧と件数で同じ判定を使う）。"""
        # 進んだ時間切れ（`interrupted`）は次の照合で自動で続くので、待機に数える（#5430）。
        where, binds = _oracle_retrieval_where(
            {key: value for key, value in filters.items() if key != "chunk_set_id"}
        )
        return (
            f"""SELECT DISTINCT c.document_id,d.file_name,CASE WHEN w.status='READY'
                AND NOT ({CURRENT_WIKI_SOURCE_SQL}) THEN 'STALE'
                WHEN w.status='FAILED' AND w.failure_code='interrupted' THEN 'QUEUED'
                ELSE NVL(w.status,'NOT_BUILT') END AS state,
            CASE WHEN w.status='FAILED' AND w.failure_code<>'interrupted'
                THEN w.failure_code END AS failure_code,w.updated_at
            FROM rag_chunks c JOIN rag_documents d ON d.document_id=c.document_id
            LEFT JOIN rag_wiki_documents w ON w.document_id=c.document_id
            WHERE {where}""",
            binds,
        )

    async def document_statuses(
        self, filters: dict[str, str], *, limit: int | None = None, offset: int = 0
    ) -> list[dict[str, Any]]:
        """所属文書ごとの構築状態を名前の順に。失敗の理由は再試行の要る失敗だけに付ける（#5466）。"""
        states, binds = self._document_states_sql(filters)
        page = ""
        if limit is not None:
            page = " OFFSET :page_offset ROWS FETCH NEXT :page_limit ROWS ONLY"
            binds = {**binds, "page_offset": offset, "page_limit": limit}
        rows = await self.oracle._fetch_all(
            f"SELECT * FROM ({states}) ORDER BY file_name,document_id{page}", binds
        )
        return [
            {
                "document_id": str(row["document_id"]),
                "file_name": str(row.get("file_name") or ""),
                "status": str(row["state"]),
                "failure_code": row.get("failure_code"),
                "updated_at": row.get("updated_at"),
            }
            for row in rows
        ]

    async def status_counts(self, filters: dict[str, str]) -> dict[str, int]:
        """所属文書の構築状態ごとの件数（ページに関わらず KB 全体。#5466）。"""
        states, binds = self._document_states_sql(filters)
        rows = await self.oracle._fetch_all(
            f"SELECT state,COUNT(*) AS n FROM ({states}) GROUP BY state", binds
        )
        return {str(row["state"]): int(str(row["n"])) for row in rows}

    async def requeue_failed(self, filters: dict[str, str]) -> None:
        """再試行の要る失敗を待機へ戻す。構築は次の照合が進める（#5466）。"""
        where, binds = _oracle_retrieval_where(
            {key: value for key, value in filters.items() if key != "chunk_set_id"}
        )

        def operation(connection: Any) -> None:
            # 文書をまたぐ関連の構築の失敗も同じ操作で戻す（#5492）。
            for table in ("rag_wiki_documents", "rag_wiki_link_documents"):
                _execute(
                    connection,
                    f"""UPDATE {table} SET status='QUEUED',failure_code=NULL,
                    lease_token=NULL,lease_until=NULL,updated_at=SYSTIMESTAMP
                    WHERE status='FAILED' AND document_id IN (SELECT c.document_id
                    FROM rag_chunks c JOIN rag_documents d ON d.document_id=c.document_id
                    WHERE {where})""",
                    binds,
                )

        await self.oracle._run_transaction(operation)

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
        for row in rows:
            metadata = dict(_json_loads(row.get("metadata_json")) or {})
            if not row.get("chunk_set_id") or is_derived_content(
                str(metadata.get("content_kind") or ""), metadata
            ):
                continue
            originals.append((row, metadata))
        parents = {(str(row["chunk_set_id"]), str(row["chunk_id"])) for row, _ in originals}
        groups: dict[tuple[str, str, str], list[tuple[dict[str, Any], dict[str, Any]]]] = {}
        for row, metadata in originals:
            chunk_set = str(row["chunk_set_id"])
            parent = metadata.get("parent_chunk_id")
            if parent and (chunk_set, str(parent)) in parents:
                continue
            if not str(row.get("chunk_text") or "").strip():
                continue
            key = (
                chunk_set,
                "parent" if parent else "chunk",
                str(parent or row["chunk_id"]),
            )
            groups.setdefault(key, []).append((row, metadata))
        units = []
        for (chunk_set, _, group_id), members in groups.items():
            # 仮想親の本文は metadata の写しを信頼せず、保存された構成原文から組み直す。
            texts = [str(row["chunk_text"]) for row, _ in members]
            text = "\n\n".join(texts)
            ids = [str(row["chunk_id"]) for row, _ in members]
            headings = []
            for _, metadata in members:
                path = metadata.get("section_path")
                headings.append(
                    [str(value) for value in path]
                    if isinstance(path, list)
                    else str(path).split(" > ")
                    if path
                    else []
                )
            heading = []
            for level in zip(*headings, strict=False):
                if len(set(level)) != 1:
                    break
                heading.append(level[0])
            units.append(
                WikiUnit(
                    unit_id=fingerprint([chunk_set, group_id]),
                    document_id=document_id,
                    chunk_set_id=chunk_set,
                    source_chunk_ids=ids,
                    source_chunk_hashes={
                        sid: fingerprint(body) for sid, body in zip(ids, texts, strict=True)
                    },
                    section_id=fingerprint([chunk_set, heading]),
                    section_path=heading,
                    title=heading[-1] if heading else str(members[0][0].get("file_name") or ""),
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
        valid: set[str] = set()
        for page in pages:
            unit = page.unit
            hashes = unit.source_chunk_hashes or {unit.source_chunk_ids[0]: unit.source_hash}
            if all(
                sid in found
                and str(found[sid]["document_id"]) == unit.document_id
                and str(found[sid]["chunk_set_id"]) == unit.chunk_set_id
                and fingerprint(str(found[sid]["chunk_text"])) == hashes[sid]
                for sid in unit.source_chunk_ids
            ):
                valid.update(unit.source_chunk_ids)
        return [_retrieved_chunk_from_row(found[sid]) for sid in ids if sid in valid]

    async def resolve(self, filters: dict[str, str], compiler: str) -> WikiScope:
        """検索の範囲を 1 回の SQL で決める（#5499）。

        今の検索範囲（有効な KB・利用者の権限）と、公開済み・今の構築条件・今の原文の保存版の
        条件で、検索に使える案内のある文書の目録（タイトルと保存版の版の属性）と、案内の
        （文書 ID, chunk_set ID, 公開の指紋）の集合を返す。後の読取はこの集合の中だけを読む。
        """
        where, binds = wiki_scope(filters)
        identities = ",".join(
            f"""JSON_VALUE(ex.extraction_json,
            '$.parser_artifacts.{XML_TREE_DOCUMENT_ARTIFACT_KEY}.{path}') {name}"""
            for name, path in SOURCE_IDENTITY_FIELDS.items()
        )
        # どの形式の文書にもある版の属性（分類の有効期間と、新しい版への置き換え）。XML の解析器が
        # 抽出に残した版の属性（上の identities）は別の項目として今のまま渡す。
        rows = await self.oracle._fetch_all(
            f"""SELECT DISTINCT d.document_id,d.file_name,{identities},
            JSON_VALUE(d.classification,'$.effective_from') AS doc_effective_from,
            JSON_VALUE(d.classification,'$.effective_to') AS doc_effective_to,
            CASE WHEN d.superseded_by_document_id IS NULL THEN 'current' ELSE 'superseded'
              END AS doc_version_status,
            p.chunk_set_id AS wiki_chunk_set_id,p.source_fingerprint AS wiki_fingerprint
            {WIKI_PAGE_FROM}
            JOIN rag_chunk_sets catalog_set ON catalog_set.chunk_set_id=c.chunk_set_id
            LEFT JOIN rag_document_extractions ex ON ex.document_id=c.document_id
              AND ex.extraction_recipe_id=catalog_set.extraction_recipe_id
            WHERE {where} AND {WIKI_PUBLISHED_SQL} ORDER BY d.document_id""",
            {**binds, "wiki_compiler": compiler},
        )
        documents: dict[str, dict[str, Any]] = {}
        sources: set[tuple[str, str, str]] = set()
        for row in rows:
            did = str(row["document_id"])
            sources.add((did, str(row["wiki_chunk_set_id"]), str(row["wiki_fingerprint"])))
            card = documents.setdefault(
                did,
                {
                    "document_id": did,
                    "title": str(row.get("file_name") or ""),
                    "source_identities": [_document_identity(row)],
                },
            )
            identity = {key: str(row[key]) for key in SOURCE_IDENTITY_FIELDS if row.get(key)}
            if identity and identity not in card["source_identities"]:
                card["source_identities"].append(identity)
        # chunk の行の条件（内容の種類・ページなど）は集合で表せないので、後の読取でも当てる。
        _, row_sql, row_binds = _oracle_retrieval_where_split(dict(filters))
        return WikiScope(
            tuple(documents.values()),
            tuple(sorted(sources)),
            row_sql,
            {key: value for key, value in row_binds.items() if _uses_bind(row_sql, key)},
        )

    async def pages(
        self,
        scope: WikiScope,
        *,
        document_ids: Sequence[str] | None = None,
        unit_ids: Sequence[str] | None = None,
        query_vector: Sequence[float] | None = None,
        limit: int | None = None,
        exclude_document_ids: Sequence[str] = (),
    ) -> list[WikiPage]:
        """``resolve`` で決めた範囲の案内。

        ``query_vector`` と ``limit`` があれば近い順の上位だけを、ID の順位を決めてから読む。
        ``exclude_document_ids`` の文書は除く（文書をまたぐ関連の候補。#5492）。
        """
        if not scope.sources:
            return []
        where, binds = _scoped_where(scope)
        if exclude_document_ids:
            clause, added = _oracle_in_predicate("c.document_id", "wiki_skip", exclude_document_ids)
            where += f" AND NOT ({clause})"
            binds.update(added)
        for column, prefix, values in (
            ("c.document_id", "wiki_doc", document_ids),
            ("p.unit_id", "wiki_unit", unit_ids),
        ):
            if values is not None:
                if not values:
                    return []
                clause, added = _oracle_in_predicate(column, prefix, values)
                where += " AND " + clause
                binds.update(added)
        if query_vector is not None and limit is not None:
            rows = await self.oracle._fetch_all(
                f"""SELECT p.unit_id {WIKI_SCOPED_FROM} WHERE {where}
                ORDER BY VECTOR_DISTANCE(p.embedding,:wiki_vector,COSINE),p.document_id,p.unit_id
                FETCH FIRST :wiki_limit ROWS ONLY""",
                {**binds, "wiki_vector": array("f", query_vector), "wiki_limit": limit},
            )
            return await self._hydrate([str(row["unit_id"]) for row in rows], scope)
        rows = await self.oracle._fetch_all(
            f"""SELECT p.page_json {WIKI_SCOPED_FROM} WHERE {where}
            ORDER BY p.document_id,p.unit_id""",
            binds,
        )
        return [WikiPage.model_validate(_json_loads(row["page_json"])) for row in rows]

    async def _hydrate(self, ids: list[str], scope: WikiScope) -> list[WikiPage]:
        found = {page.unit.unit_id: page for page in await self.pages(scope, unit_ids=ids)}
        return [found[uid] for uid in ids if uid in found]

    async def text_rankings(
        self,
        expression: str,
        scope: WikiScope,
        *,
        limit: int,
        document_ids: Sequence[str] | None = None,
        exclude_document_ids: Sequence[str] = (),
    ) -> list[tuple[str, str]]:
        """全文検索の順位（文書 ID, 単位 ID）を 1 回の SQL で取る（#4986）。本文は読まない。

        ``document_ids`` が無ければ全体の上位 ``limit`` 件を点数の順に、
        あれば文書ごとの上位 ``limit`` 件を文書ごとに点数の順に返す。
        ``exclude_document_ids`` の文書は除く（#5492）。範囲は ``resolve`` で決めた集合。
        """
        text = _oracle_text_query(expression)
        if (
            not text
            or limit <= 0
            or not scope.sources
            or (document_ids is not None and not document_ids)
        ):
            return []
        where, binds = _scoped_where(scope)
        if exclude_document_ids:
            clause, added = _oracle_in_predicate(
                "c.document_id", "wiki_text_skip", exclude_document_ids
            )
            where += f" AND NOT ({clause})"
            binds.update(added)
        order, tail = "text_score DESC,p.document_id,p.unit_id", "FETCH FIRST :wiki_limit ROWS ONLY"
        if document_ids is not None:
            clause, values = _oracle_in_predicate("c.document_id", "wiki_text_doc", document_ids)
            where += " AND " + clause
            binds.update(values)
            order, tail = "p.document_id,text_score DESC,p.unit_id", ""
        else:
            binds["wiki_limit"] = limit
        rows = await self.oracle._fetch_all(
            f"""SELECT p.document_id,p.unit_id,SCORE(1) AS text_score {WIKI_SCOPED_FROM}
            WHERE CONTAINS(p.search_text,:wiki_text,1)>0 AND {where}
            ORDER BY {order} {tail}""",
            {**binds, "wiki_text": text},
        )
        counts: dict[str, int] = {}
        ranking = []
        for row in rows:
            did = str(row["document_id"])
            counts[did] = counts.get(did, 0) + 1
            if counts[did] <= limit:
                ranking.append((did, str(row["unit_id"])))
        return ranking

    # ---- 文書をまたぐ関連（#5492） ----

    async def link_knowledge_bases(self, document_id: str) -> list[str]:
        """文書が属し、Wiki の構築を指定した、現在の利用者が見られる KB（関連の候補の範囲）。"""
        rows = await self.oracle._fetch_all(
            f"""SELECT DISTINCT kb.knowledge_base_id FROM rag_document_knowledge_bases wm
            JOIN rag_knowledge_bases kb ON kb.knowledge_base_id=wm.knowledge_base_id
            JOIN rag_documents member_doc ON member_doc.document_id=wm.document_id
            WHERE (wm.document_id=:wiki_document
                OR member_doc.duplicate_of_document_id=:wiki_document)
              AND kb.status='ACTIVE' AND {_oracle_knowledge_base_access_predicate_sql(alias="kb")}
              AND JSON_VALUE(kb.retrieval_config,'$.{WIKI_BUILD_KEY}')='true'
            ORDER BY kb.knowledge_base_id""",
            _with_tenant_bind({"wiki_document": document_id}),
        )
        return [str(row["knowledge_base_id"]) for row in rows]

    async def link_state(self, document_id: str) -> dict[str, object] | None:
        return await self.oracle._fetch_one(
            """SELECT source_fingerprint,link_fingerprint,status,failure_code,
            CASE WHEN lease_until>SYSTIMESTAMP THEN 1 ELSE 0 END leased
            FROM rag_wiki_link_documents WHERE document_id=:id""",
            {"id": document_id},
        )

    async def claim_links(
        self,
        document_id: str,
        source_fingerprint: str,
        link_fingerprint: str,
        *,
        lease_seconds: int,
    ) -> tuple[str, list[str]] | None:
        """関連の構築の lease を取る。条件が同じなら判定済みの鍵を返して続きから作る。

        原文の保存版が変われば、この文書の古い保存版の関連を消してから始める。
        """
        token = uuid4().hex

        def operation(connection: Any) -> tuple[str, list[str]] | None:
            if not _fetch_one(
                connection,
                "SELECT document_id FROM rag_documents WHERE document_id=:id FOR UPDATE",
                {"id": document_id},
            ):
                return None
            row = _fetch_one(
                connection,
                """SELECT source_fingerprint,link_fingerprint,status,done_json,
                CASE WHEN lease_until>SYSTIMESTAMP THEN 1 ELSE 0 END AS leased
                FROM rag_wiki_link_documents WHERE document_id=:id FOR UPDATE""",
                {"id": document_id},
            )
            same = bool(
                row
                and row["source_fingerprint"] == source_fingerprint
                and row["link_fingerprint"] == link_fingerprint
            )
            if row and (
                (row["status"] == "RUNNING" and row["leased"])
                or (same and row["status"] == "READY")
            ):
                return None
            done = _json_list(row.get("done_json")) if row and same else []
            if not same:
                _execute(
                    connection,
                    """DELETE FROM rag_wiki_links WHERE
                    (source_document_id=:id AND source_fingerprint<>:fingerprint)
                    OR (target_document_id=:id AND target_fingerprint<>:fingerprint)""",
                    {"id": document_id, "fingerprint": source_fingerprint},
                )
            _execute(
                connection,
                """MERGE INTO rag_wiki_link_documents target
                USING (SELECT :id document_id FROM dual) source
                ON (target.document_id=source.document_id)
                WHEN MATCHED THEN UPDATE SET source_fingerprint=:fingerprint,
                    link_fingerprint=:links,done_json=:done,status='RUNNING',lease_token=:token,
                    lease_until=SYSTIMESTAMP+NUMTODSINTERVAL(:seconds,'SECOND'),
                    failure_code=NULL,updated_at=SYSTIMESTAMP
                WHEN NOT MATCHED THEN INSERT (document_id,source_fingerprint,link_fingerprint,
                    done_json,status,lease_token,lease_until)
                    VALUES (:id,:fingerprint,:links,:done,'RUNNING',:token,
                    SYSTIMESTAMP+NUMTODSINTERVAL(:seconds,'SECOND'))""",
                {
                    "id": document_id,
                    "fingerprint": source_fingerprint,
                    "links": link_fingerprint,
                    "done": _json_bind(done),
                    "token": token,
                    "seconds": lease_seconds,
                },
                input_sizes=_json_input_sizes("done"),
            )
            return token, done

        return await self.oracle._run_transaction(operation)

    @staticmethod
    def _require_link_lease(connection: Any, document_id: str, token: str) -> dict[str, Any]:
        row = _fetch_one(
            connection,
            """SELECT done_json FROM rag_wiki_link_documents
            WHERE document_id=:id AND lease_token=:token AND status='RUNNING'
              AND lease_until>SYSTIMESTAMP FOR UPDATE""",
            {"id": document_id, "token": token},
        )
        if not row:
            raise ValueError("Wiki の関連の構築の所有権が変更されました。")
        return row

    async def save_links(
        self,
        document_id: str,
        token: str,
        *,
        sources: Sequence[str],
        candidates: Sequence[str],
        links: Sequence[tuple[str, str, str, str]],
        done: Sequence[str],
    ) -> None:
        """判定した起点と候補の組の関連を置き換え、判定済みの鍵を足す（checkpoint）。

        ``links`` は（起点の単位 ID, 終点の単位 ID, 関係, 理由）。両方の案内が今の保存版で公開済みの
        ときだけ保存し、別の文書の間の関連だけを持つ。
        """
        allowed_sources, allowed_targets = set(sources), set(candidates)
        if any(
            source not in allowed_sources or target not in allowed_targets
            for source, target, *_ in links
        ):
            raise ValueError("Wiki の関連が判定した起点と候補の組にありません。")

        def operation(connection: Any) -> None:
            row = self._require_link_lease(connection, document_id, token)
            if sources and candidates:
                left, left_binds = _oracle_in_predicate("source_unit_id", "link_source", sources)
                right, right_binds = _oracle_in_predicate(
                    "target_unit_id", "link_target", candidates
                )
                _execute(
                    connection,
                    f"DELETE FROM rag_wiki_links WHERE {left} AND {right}",
                    {**left_binds, **right_binds},
                )
            for source, target, relation, reason in links:
                _execute(
                    connection,
                    """MERGE INTO rag_wiki_links target USING (
                        SELECT sp.page_id source_page_id,tp.page_id target_page_id,
                        sp.document_id source_document_id,sp.unit_id source_unit_id,
                        sp.source_fingerprint source_fingerprint,
                        tp.document_id target_document_id,tp.unit_id target_unit_id,
                        tp.source_fingerprint target_fingerprint
                        FROM rag_wiki_pages sp,rag_wiki_documents sw,
                             rag_wiki_pages tp,rag_wiki_documents tw
                        WHERE sp.unit_id=:source AND tp.unit_id=:target
                          AND sw.document_id=sp.document_id AND sw.status='READY'
                          AND sp.source_fingerprint=sw.published_fingerprint
                          AND tw.document_id=tp.document_id AND tw.status='READY'
                          AND tp.source_fingerprint=tw.published_fingerprint
                          AND sp.document_id<>tp.document_id) found
                    ON (target.source_page_id=found.source_page_id
                        AND target.target_page_id=found.target_page_id)
                    WHEN MATCHED THEN UPDATE SET relation=:relation,reason=:reason,
                        updated_at=SYSTIMESTAMP
                    WHEN NOT MATCHED THEN INSERT (source_page_id,target_page_id,
                        source_document_id,source_unit_id,source_fingerprint,
                        target_document_id,target_unit_id,target_fingerprint,relation,reason)
                    VALUES (found.source_page_id,found.target_page_id,found.source_document_id,
                        found.source_unit_id,found.source_fingerprint,found.target_document_id,
                        found.target_unit_id,found.target_fingerprint,:relation,:reason)""",
                    {"source": source, "target": target, "relation": relation, "reason": reason},
                )
            saved = _json_list(row.get("done_json"))
            _execute(
                connection,
                """UPDATE rag_wiki_link_documents SET done_json=:done,updated_at=SYSTIMESTAMP
                WHERE document_id=:id""",
                {"id": document_id, "done": _json_bind(list(dict.fromkeys([*saved, *done])))},
                input_sizes=_json_input_sizes("done"),
            )

        await self.oracle._run_transaction(operation)

    async def finish_links(
        self, document_id: str, token: str, *, failure_code: str | None = None
    ) -> None:
        def operation(connection: Any) -> None:
            self._require_link_lease(connection, document_id, token)
            _execute(
                connection,
                """UPDATE rag_wiki_link_documents SET status=:status,failure_code=:failure,
                done_json=CASE WHEN :status='READY' THEN NULL ELSE done_json END,
                lease_token=NULL,lease_until=NULL,updated_at=SYSTIMESTAMP
                WHERE document_id=:id""",
                {
                    "id": document_id,
                    "status": "FAILED" if failure_code else "READY",
                    "failure": failure_code,
                },
            )

        await self.oracle._run_transaction(operation)

    async def links(self, unit_ids: Sequence[str], scope: WikiScope) -> list[tuple[str, str, str]]:
        """単位から出る関連と入る関連（起点の単位 ID, 終点の単位 ID, 関係）。

        起点と終点の両方の案内が ``resolve`` で決めた範囲（今の検索範囲・公開済み・今の構築条件・
        今の原文の保存版）にある関連だけを返す。案内は KB をまたいで再利用されるので、所属は
        読むときに範囲で照合する。
        """
        if not unit_ids or not scope.sources:
            return []
        where, binds = _scoped_where(scope)
        outgoing, out_binds = _oracle_in_predicate("l.source_unit_id", "link_from", unit_ids)
        incoming, in_binds = _oracle_in_predicate("l.target_unit_id", "link_to", unit_ids)
        current = f"SELECT 1 {WIKI_SCOPED_FROM} WHERE {where}"
        rows = await self.oracle._fetch_all(
            f"""SELECT l.source_unit_id,l.target_unit_id,l.relation FROM rag_wiki_links l
            WHERE ({outgoing} OR {incoming})
              AND EXISTS ({current} AND p.page_id=l.source_page_id)
              AND EXISTS ({current} AND p.page_id=l.target_page_id)
            ORDER BY l.source_unit_id,l.target_unit_id""",
            {**binds, **out_binds, **in_binds},
        )
        return [
            (str(row["source_unit_id"]), str(row["target_unit_id"]), str(row["relation"]))
            for row in rows
        ]
