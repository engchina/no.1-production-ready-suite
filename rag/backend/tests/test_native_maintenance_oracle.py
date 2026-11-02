"""専用の合成索引で削除残骸、永続要求、worker、SEARCHを結合する。"""

import json
from array import array
from typing import Any, Literal, cast
from uuid import uuid4

import pytest
from rag_engine.adapters.oracle.native_hybrid import native_search_request

from app.clients import native_maintenance_store as store_module
from app.clients.native_index_maintenance import (
    MaintenanceConnection,
    NativeMaintenanceResult,
    maintain_native_vectors,
)
from app.clients.native_maintenance_schema import (
    native_maintenance_activation_sql,
    native_maintenance_tables_sql,
)
from app.clients.oracle import OracleClient, OracleConnectionProtocol
from app.rag.native_index_maintenance import MaintenanceLease, run_maintenance_once
from app.rag.oracle_schema import split_sql_statements


@pytest.mark.usefixtures("oracle_db")
async def test_delete_reinsert_queue_restores_native_candidates(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    oracle = OracleClient()
    prefix = "RAG_T_M_" + uuid4().hex[:8].upper()
    names = {
        "RAG_NATIVE_INDEX_MAINTENANCE": prefix + "_S",
        "RAG_NATIVE_INDEX_EVENTS_TIME": prefix + "_I",
        "RAG_NATIVE_INDEX_EVENTS": prefix + "_E",
        "RAG_CHUNKS_NATIVE_MAINT": prefix + "_T",
        "rag_chunks": prefix + "_C",
    }
    table, index = names["rag_chunks"], prefix + "_H"
    monkeypatch.setattr(store_module, "STATE_TABLE", names["RAG_NATIVE_INDEX_MAINTENANCE"])
    monkeypatch.setattr(store_module, "EVENTS_TABLE", names["RAG_NATIVE_INDEX_EVENTS"])
    store = store_module.NativeMaintenanceStore(oracle)
    made: list[str] = []
    results: list[NativeMaintenanceResult] = []

    def scoped(sql: str) -> str:
        for old, new in names.items():
            sql = sql.replace(old, new)
        return sql

    async def execute(sql: str, binds: dict[str, Any] | None = None) -> None:
        def operation(connection: OracleConnectionProtocol) -> None:
            cursor = connection.cursor()
            try:
                cursor.execute(sql, binds or {})
            finally:
                cursor.close()

        await oracle._run_transaction(operation)

    async def optimize(lease: MaintenanceLease) -> None:
        def operation() -> None:
            connection = cast(store_module.TimedMaintenanceConnection, oracle.acquire_connection())
            try:
                connection.call_timeout = 180_000
                results.append(
                    maintain_native_vectors(
                        cast(MaintenanceConnection, connection), index_name=index
                    )
                )
            finally:
                connection.close()

        await oracle._db_call_runner(operation)

    monkeypatch.setattr(store, "optimize", optimize)
    vector = [0.1] * 1536

    async def search_count(channel: Literal["vector", "hybrid", "keyword"]) -> int:
        spec = native_search_request(
            query="24",
            vector=vector,
            channel=channel,
            limit=5,
            allowed_ids=[],
            allowed_scopes=[1],
            accuracy=100,
        )
        spec["hybrid_index_name"] = index
        rows = await oracle._fetch_all(
            f"SELECT t.item_id FROM {table} t JOIN JSON_TABLE("
            "DBMS_HYBRID_VECTOR.SEARCH(JSON(:spec)), '$[*]' COLUMNS("
            "source_rowid VARCHAR2(128) PATH '$.rowid')) n "
            "ON t.ROWID=CHARTOROWID(n.source_rowid) WHERE t.native_scope_id=1",
            {"spec": json.dumps(spec)},
        )
        return len(rows)

    try:
        for statement in split_sql_statements(scoped(native_maintenance_tables_sql())):
            await execute(statement)
            if statement.startswith("CREATE TABLE"):
                made.append(statement.split()[2])
        await execute(
            f"CREATE TABLE {table}(item_id NUMBER, native_scope_id NUMBER, "
            "search_text CLOB, embedding VECTOR(1536,FLOAT32), hybrid_payload JSON)"
        )
        made.append(table)
        await execute(
            f"CREATE HYBRID VECTOR INDEX {index} ON {table}(hybrid_payload) PARAMETERS ("
            "'VECTORIZER RAG_CHUNKS_HYBRID_PREF LEXER RAG_TEXT_WORLD_LEXER "
            "STOPLIST RAG_TEXT_STOPLIST SEARCH_ON TEXT "
            "MAINTENANCE MANUAL SYNC (ON COMMIT)')"
        )
        for statement in split_sql_statements(scoped(native_maintenance_activation_sql())):
            await execute(statement)
        assert await run_maintenance_once(store, "synthetic-worker") == "complete"
        # rollbackした要求はworkerから見えず、同一leaseの競合は一方だけが取得する。
        connection = oracle.acquire_connection()
        try:
            cursor = connection.cursor()
            try:
                cursor.execute(
                    f"INSERT INTO {names['RAG_NATIVE_INDEX_EVENTS']} (event_id) "
                    "VALUES ('rolled-back')"
                )
                connection.rollback()
            finally:
                cursor.close()
        finally:
            connection.close()
        assert await store.claim("worker-a") is None
        await execute(f"INSERT INTO {names['RAG_NATIVE_INDEX_EVENTS']} (event_id) VALUES ('first')")
        lease = await store.claim("worker-a")
        assert lease is not None
        assert await store.claim("worker-b") is None
        assert await store.heartbeat(lease)
        await execute(f"INSERT INTO {names['RAG_NATIVE_INDEX_EVENTS']} (event_id) VALUES ('later')")
        await store.fail(lease, "SyntheticFailure")
        assert await store.claim("worker-b") is None
        await execute(
            f"UPDATE {names['RAG_NATIVE_INDEX_MAINTENANCE']} SET next_attempt_at=SYSTIMESTAMP"
        )
        replacement = await store.claim("worker-b")
        assert replacement is not None and replacement.token != lease.token
        assert (lease.attempt, replacement.attempt) == (1, 2)
        assert not await store.complete(lease)
        assert not await store.heartbeat(lease)
        assert await store.complete(replacement)
        assert await store.claim("worker-c") is None

        def plan(interval_hours: float) -> str:
            store.full_interval_hours = interval_hours
            connection = oracle.acquire_connection()
            try:
                cursor = connection.cursor()
                try:
                    return store._plan(cursor, MaintenanceLease("worker-d", "token-d", ()))
                finally:
                    cursor.close()
            finally:
                connection.close()

        assert await oracle._db_call_runner(lambda: plan(24)) == "semantic_then_full"
        await store._record_full_completed()
        await store._record_full_completed()
        assert await oracle._db_call_runner(lambda: plan(24)) == "semantic"
        assert await oracle._db_call_runner(lambda: plan(0)) == "semantic_then_full"
        for _generation in range(8):
            await execute(f"DELETE FROM {table}")
            for item in (24, 99):
                await execute(
                    f"INSERT INTO {table}(item_id,native_scope_id,embedding,hybrid_payload) "
                    "VALUES(:id,1,:vector,JSON_OBJECT('text' VALUE :text,"
                    "'embedding' VALUE JSON(:vjson) RETURNING JSON))",
                    {
                        "id": item,
                        "vector": array("f", vector),
                        "text": f"syntheticquantity {item}",
                        "vjson": json.dumps(vector),
                    },
                )
            if _generation == 0:
                assert await search_count("vector") == 2, "initial-vector-baseline"
        assert await run_maintenance_once(store, "synthetic-worker") == "complete"
        assert results[-1].before.stored >= results[-1].before.visible
        assert results[-1].after.stored == results[-1].after.visible == 2
        assert await run_maintenance_once(store, "synthetic-worker") == "idle"
        channel_counts: list[tuple[Literal["vector", "hybrid", "keyword"], int]] = [
            ("vector", 2),
            ("hybrid", 2),
            ("keyword", 1),
        ]
        expected_counts = dict(channel_counts)
        counts = {channel: await search_count(channel) for channel in expected_counts}
        if counts != expected_counts:
            # 件数が合っても SEARCH が足りないことがある。FULL で戻るかも一緒に報告する。
            semantic_result = results[-1]

            def full() -> None:
                connection = oracle.acquire_connection()
                try:
                    maintain_native_vectors(
                        cast(MaintenanceConnection, connection), index_name=index, plan="full"
                    )
                finally:
                    connection.close()

            await oracle._db_call_runner(full)
            after_full = {channel: await search_count(channel) for channel in expected_counts}
            pytest.fail(f"{semantic_result} の後 {counts}、続けて FULL の後 {after_full}")
    finally:
        for name in reversed(made):
            await execute(f"DROP TABLE {name} PURGE")
        remaining = await oracle._fetch_all(
            "SELECT COUNT(*) AS remaining FROM user_objects WHERE object_name LIKE :prefix",
            {"prefix": prefix + "%"},
        )
        assert int(str(remaining[0]["remaining"])) == 0
