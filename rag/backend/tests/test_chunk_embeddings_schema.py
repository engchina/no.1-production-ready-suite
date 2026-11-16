"""埋め込みの別表の DDL と移行の SQL（#5241）。"""

from app.clients.native_hybrid_schema import chunk_embedding_trigger_sql, native_hybrid_objects_sql
from app.clients.native_maintenance_schema import (
    native_maintenance_activation_sql,
    native_maintenance_current_activation_sql,
)
from app.rag import oracle_schema


def _migration_sql() -> str:
    sections = {
        section.name: section for section in oracle_schema.oracle_schema_migration_sections()
    }
    section = sections["20261117_5241_chunk_embeddings"]
    assert section.destructive_note
    return section.sql


def test_migration_copies_vectors_before_dropping_the_column() -> None:
    sql = _migration_sql()
    copy = sql.index("INSERT INTO rag_chunk_embeddings (chunk_id, embedding)")
    # 写す間は原生索引の JSON を作り直さない(JSON には埋め込みが既にある)。
    assert sql.index("ALTER TRIGGER RAG_CHUNK_EMBEDDINGS_HYBRID_SYNC DISABLE") < copy
    assert copy < sql.index("DROP INDEX RAG_CHUNKS_EMBEDDING_HNSW_IDX")
    assert copy < sql.index("ALTER TABLE rag_chunks DROP COLUMN embedding")
    # 流し直しても同じ行を二重に入れない。列が無ければ何もしない。
    assert "NOT EXISTS (" in sql and "column_name = 'EMBEDDING'" in sql
    # trigger は列を消した後に作り直して有効にする。
    assert sql.index("ALTER TABLE rag_chunks DROP COLUMN embedding") < sql.index(
        "CREATE OR REPLACE TRIGGER RAG_CHUNK_EMBEDDINGS_HYBRID_SYNC"
    )
    assert "CREATE OR REPLACE TRIGGER RAG_CHUNKS_NATIVE_MAINT" in sql
    # 範囲の解決・距離順の読みが表の行を読まない索引に作り直す。
    assert "(document_id, chunk_index, chunk_set_id, native_scope_id, chunk_id)" in sql
    assert "(native_scope_id, chunk_index, chunk_id, document_id)" in sql
    # 原生索引の内容(JSON)は書き換えない(trigger の本文の UPDATE は除く)。
    statements = oracle_schema.split_sql_statements(sql)
    assert not any(statement.startswith("UPDATE") for statement in statements)
    assert "DROP INDEX RAG_CHUNKS_HYBRID_IDX" not in sql


def test_current_triggers_read_the_embedding_table_and_not_the_removed_column() -> None:
    triggers = chunk_embedding_trigger_sql()
    assert "LEFT JOIN rag_chunk_embeddings e ON e.chunk_id = :NEW.chunk_id" in triggers
    assert "AFTER INSERT OR UPDATE ON rag_chunk_embeddings FOR EACH ROW" in triggers
    # chunk の行の trigger は本文の変更のときだけ別表を読む(JSON だけの更新では読まない)。
    assert "IF INSERTING OR UPDATING('SEARCH_TEXT') THEN" in triggers
    assert ":NEW.embedding" not in triggers.split("RAG_CHUNK_EMBEDDINGS_HYBRID_SYNC")[0]
    assert chunk_embedding_trigger_sql() in native_hybrid_objects_sql()
    current = native_maintenance_current_activation_sql()
    assert "JSON_EXISTS(:OLD.hybrid_payload, '$.embedding')" in current
    assert "embedding IS NOT NULL" not in current
    # 適用済みの旧 migration の SQL は変えない(checksum)。
    assert ":OLD.embedding IS NOT NULL" in native_maintenance_activation_sql()
