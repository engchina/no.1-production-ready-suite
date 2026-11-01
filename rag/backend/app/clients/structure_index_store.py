"""文書構造索引の補建状態。Oracleの行ロックで二重投入と停止の競合を防ぐ。"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any, cast

from app.clients.oracle import (
    OracleClient,
    _execute,
    _fetch_one,
    _json_bind,
    _json_input_sizes,
    _json_loads,
    _require_active_knowledge_base,
)
from app.rag.request_context import current_audit_request_context

ACTIVE = {"RUNNING", "STOPPING"}


def structure_index_schema_sql() -> str:
    return """
CREATE TABLE rag_structure_index_jobs (
    knowledge_base_id VARCHAR2(64) PRIMARY KEY,
    job_id VARCHAR2(64) NOT NULL,
    user_id_hash CHAR(64),
    status VARCHAR2(24) NOT NULL,
    state_json CLOB CHECK (state_json IS JSON),
    heartbeat_at TIMESTAMP WITH TIME ZONE DEFAULT SYSTIMESTAMP NOT NULL,
    CONSTRAINT rag_structure_jobs_kb_fk FOREIGN KEY (knowledge_base_id)
        REFERENCES rag_knowledge_bases(knowledge_base_id) ON DELETE CASCADE
);
CREATE TABLE rag_structure_index_builds (
    knowledge_base_id VARCHAR2(64) NOT NULL,
    document_id VARCHAR2(64) NOT NULL,
    source_fingerprint CHAR(64) NOT NULL,
    quality VARCHAR2(24) NOT NULL,
    result_json CLOB CHECK (result_json IS JSON),
    built_at TIMESTAMP WITH TIME ZONE DEFAULT SYSTIMESTAMP NOT NULL,
    PRIMARY KEY (knowledge_base_id, document_id),
    CONSTRAINT rag_structure_builds_kb_fk FOREIGN KEY (knowledge_base_id)
        REFERENCES rag_knowledge_bases(knowledge_base_id) ON DELETE CASCADE,
    CONSTRAINT rag_structure_builds_doc_fk FOREIGN KEY (document_id)
        REFERENCES rag_documents(document_id) ON DELETE CASCADE
);
""".strip()


class StructureIndexStore:
    def __init__(self, oracle: OracleClient) -> None:
        self.oracle = oracle

    async def get(self, kb_id: str) -> dict[str, Any] | None:
        row = await self.oracle._fetch_one(
            """SELECT state_json,user_id_hash,
            CASE WHEN heartbeat_at < SYSTIMESTAMP - INTERVAL '120' SECOND
                THEN 1 ELSE 0 END AS stale
            FROM rag_structure_index_jobs
            WHERE knowledge_base_id=:id""",
            {"id": kb_id},
        )
        if not row:
            return None
        context = current_audit_request_context()
        if not context.all_users_visible and row.get("user_id_hash") != context.user_id_hash:
            return None
        state = cast(dict[str, Any], dict(_json_loads(row["state_json"]) or {}))
        if row.get("stale") and state["status"] in ACTIVE:
            state["status"] = "INTERRUPTED"
            for item in state["items"]:
                if item["status"] == "RUNNING":
                    item.update(status="FAILED", reason="interrupted")
        return state

    async def visible_state(
        self, kb_id: str, state: dict[str, Any] | None
    ) -> dict[str, Any] | None:
        """過去の投入時の範囲を信用せず、現在見える所属文書だけを返す。"""
        from app.clients.oracle import _oracle_access_predicate_sql, _with_tenant_bind

        if state is None:
            return None
        rows = await self.oracle._fetch_all(
            f"""SELECT d.document_id,d.file_name FROM rag_document_knowledge_bases m
            JOIN rag_documents d ON d.document_id=m.document_id
            WHERE m.knowledge_base_id=:kb AND {_oracle_access_predicate_sql(alias="d")}""",
            _with_tenant_bind({"kb": kb_id}),
        )
        names = {str(row["document_id"]): str(row["file_name"]) for row in rows}
        items = [
            dict(item, name=names[item["document_id"]])
            for item in state["items"]
            if item["document_id"] in names
        ]
        return dict(state, items=items, partial_visibility=len(items) != len(state["items"]))

    async def mutate(
        self,
        kb_id: str,
        change: Callable[[dict[str, Any] | None], dict[str, Any]],
        *,
        start: bool = False,
    ) -> dict[str, Any]:
        """KB lockで投入を直列化し、別プロセスからの停止要求を上書きしない。"""
        context = current_audit_request_context()

        def operation(connection: Any) -> dict[str, Any]:
            _require_active_knowledge_base(connection, kb_id)
            _fetch_one(
                connection,
                """SELECT knowledge_base_id FROM rag_knowledge_bases
                WHERE knowledge_base_id=:id FOR UPDATE""",
                {"id": kb_id},
            )
            row = _fetch_one(
                connection,
                """SELECT state_json,user_id_hash,
                CASE WHEN heartbeat_at < SYSTIMESTAMP - INTERVAL '120' SECOND
                THEN 1 ELSE 0 END AS stale
                FROM rag_structure_index_jobs WHERE knowledge_base_id=:id FOR UPDATE""",
                {"id": kb_id},
            )
            state = (
                cast(dict[str, Any], dict(_json_loads(row["state_json"]) or {})) if row else None
            )
            if state and state["status"] in ACTIVE and row and row["stale"]:
                state["status"] = "INTERRUPTED"
                for item in state["items"]:
                    if item["status"] == "RUNNING":
                        item["status"] = "FAILED"
                        item["reason"] = "interrupted"
            if start and state and state["status"] in ACTIVE:
                raise ValueError("このKBでは文書構造索引を構築中です。")
            if (
                not start
                and row
                and not context.all_users_visible
                and row.get("user_id_hash") != context.user_id_hash
            ):
                raise KeyError("構築の記録が見つかりません。")
            result = change(state)
            _execute(
                connection,
                """MERGE INTO rag_structure_index_jobs t USING (SELECT :id AS id FROM dual) s
                ON (t.knowledge_base_id=s.id)
                WHEN MATCHED THEN UPDATE SET job_id=:job,status=:status,state_json=:state,
                    user_id_hash=:owner,heartbeat_at=SYSTIMESTAMP
                WHEN NOT MATCHED THEN INSERT
                    (knowledge_base_id,job_id,status,state_json,user_id_hash,heartbeat_at)
                    VALUES (:id,:job,:status,:state,:owner,SYSTIMESTAMP)""",
                {
                    "id": kb_id,
                    "job": result["job_id"],
                    "status": result["status"],
                    "state": _json_bind(result),
                    "owner": context.user_id_hash if start else (row or {}).get("user_id_hash"),
                },
                input_sizes=_json_input_sizes("state"),
            )
            return result

        return await self.oracle._run_transaction(operation)

    async def builds(self, kb_id: str) -> dict[str, dict[str, Any]]:
        rows = await self.oracle._fetch_all(
            """SELECT document_id,source_fingerprint,quality FROM rag_structure_index_builds
            WHERE knowledge_base_id=:id""",
            {"id": kb_id},
        )
        return {str(row["document_id"]): dict(row) for row in rows}

    async def publish(
        self,
        kb_id: str,
        document_id: str,
        fingerprint: str,
        source: list[str],
        graphs: dict[str, Any],
        quality: str,
        result: dict[str, Any],
        *,
        expected_job_id: str | None = None,
    ) -> None:
        from app.clients.oracle import (
            _fetch_all,
            _select_document_for_update,
            _write_document_graph_index,
        )
        from app.rag.structure_index import source_fingerprint

        def operation(connection: Any) -> None:
            # 所属の変更も同じ親行lockを使う。版確認と全レシピの置換を一つのcommitにする。
            _require_active_knowledge_base(connection, kb_id)
            _fetch_one(
                connection,
                """SELECT knowledge_base_id FROM rag_knowledge_bases
                WHERE knowledge_base_id=:kb FOR UPDATE""",
                {"kb": kb_id},
            )
            if expected_job_id is not None:
                job = _fetch_one(
                    connection,
                    """SELECT job_id,status FROM rag_structure_index_jobs
                    WHERE knowledge_base_id=:kb
                    AND heartbeat_at >= SYSTIMESTAMP - INTERVAL '120' SECOND FOR UPDATE""",
                    {"kb": kb_id},
                )
                if not job or job["job_id"] != expected_job_id or job["status"] not in ACTIVE:
                    raise ValueError("interrupted")
            _require_active_knowledge_base(connection, kb_id)
            if _select_document_for_update(connection, document_id) is None:
                raise KeyError("文書が見つかりません。")
            rows = _fetch_all(
                connection,
                """SELECT cs.chunk_set_id FROM rag_chunk_sets cs
                JOIN rag_document_knowledge_bases m ON m.document_id=cs.document_id
                WHERE cs.document_id=:doc AND m.knowledge_base_id=:kb AND cs.is_active=1
                ORDER BY cs.chunk_set_id FOR UPDATE OF cs.is_active""",
                {"doc": document_id, "kb": kb_id},
            )
            current = sorted(str(row["chunk_set_id"]) for row in rows)
            if current != source or source_fingerprint(current) != fingerprint:
                raise ValueError("source_changed")
            for chunk_set_id, graph in graphs.items():
                _write_document_graph_index(
                    connection,
                    document_id,
                    graph,
                    chunk_set_id=chunk_set_id,
                    knowledge_base_ids=[kb_id],
                )
            _execute(
                connection,
                """MERGE INTO rag_structure_index_builds t
                USING (SELECT :kb AS kb,:doc AS doc FROM dual) s
                ON (t.knowledge_base_id=s.kb AND t.document_id=s.doc)
                WHEN MATCHED THEN UPDATE SET source_fingerprint=:fp,quality=:quality,
                    result_json=:result,built_at=SYSTIMESTAMP
                WHEN NOT MATCHED THEN INSERT
                    (knowledge_base_id,document_id,source_fingerprint,quality,result_json)
                    VALUES (:kb,:doc,:fp,:quality,:result)""",
                {
                    "kb": kb_id,
                    "doc": document_id,
                    "fp": fingerprint,
                    "quality": quality,
                    "result": _json_bind(result),
                },
                input_sizes=_json_input_sizes("result"),
            )

        await self.oracle._run_transaction(operation)
