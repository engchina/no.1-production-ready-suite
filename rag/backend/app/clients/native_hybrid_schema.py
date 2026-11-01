"""既存の検索本文と埋め込みを原生hybrid索引へ同期する管理DDL。"""

from rag_engine.adapters.oracle.native_hybrid import (
    NATIVE_HYBRID_INDEX,
    NATIVE_HYBRID_PREFERENCE,
)

NATIVE_HYBRID_TRIGGER = "RAG_CHUNKS_HYBRID_SYNC"


def _native_hybrid_setup_sql(
    vector_type: str = "ivf", *, sync_changed_columns: bool = False
) -> str:
    """DDL管理からだけ呼ぶ。既存migrationのchecksumを保つため旧SQLを既定にする。"""
    guard = (
        "    IF INSERTING OR UPDATING('SEARCH_TEXT') OR UPDATING('EMBEDDING') THEN\n"
        if sync_changed_columns
        else ""
    )
    end_guard = "    END IF;\n" if sync_changed_columns else ""
    return f"""
DECLARE
    v_count NUMBER;
BEGIN
    SELECT COUNT(*) INTO v_count FROM ctx_user_preferences
    WHERE pre_name = '{NATIVE_HYBRID_PREFERENCE}';
    IF v_count = 0 THEN
        DBMS_VECTOR_CHAIN.CREATE_PREFERENCE(
            '{NATIVE_HYBRID_PREFERENCE}', DBMS_VECTOR_CHAIN.VECTORIZER,
            JSON('{{"vector_idxtype":"{vector_type}","distance":"COSINE","paths":[{{"type":"VECTOR","path_list":["$.embedding"]}}]}}')
        );
    END IF;
    SELECT COUNT(*) INTO v_count FROM user_tab_columns
    WHERE table_name = 'RAG_CHUNKS' AND column_name = 'HYBRID_PAYLOAD';
    IF v_count = 1 THEN
        EXECUTE IMMEDIATE q'~CREATE OR REPLACE TRIGGER {NATIVE_HYBRID_TRIGGER}
BEFORE INSERT OR UPDATE ON rag_chunks FOR EACH ROW
BEGIN
{guard}    SELECT JSON_OBJECT(
        'text' VALUE :NEW.search_text,
        'embedding' VALUE JSON(VECTOR_SERIALIZE(:NEW.embedding RETURNING CLOB))
        ABSENT ON NULL RETURNING JSON
    ) INTO :NEW.hybrid_payload FROM dual;
{end_guard}END;~';
    END IF;
END;
/
""".strip()


def native_hybrid_objects_sql() -> str:
    """管理対象のpreference・同期trigger・原生索引を作る。"""
    return f"""
{_native_hybrid_setup_sql(sync_changed_columns=True)}

CREATE HYBRID VECTOR INDEX {NATIVE_HYBRID_INDEX}
    ON rag_chunks (hybrid_payload)
    PARAMETERS ('VECTORIZER {NATIVE_HYBRID_PREFERENCE}
        LEXER RAG_TEXT_WORLD_LEXER STOPLIST RAG_TEXT_STOPLIST
        MAINTENANCE MANUAL SYNC (ON COMMIT)');
""".strip()


def native_hybrid_migration_sql() -> str:
    """既存chunkの本文・ベクトルを保持し、検索用JSONだけ追加する。"""
    return f"""
DECLARE
    v_count NUMBER;
BEGIN
    SELECT COUNT(*) INTO v_count FROM user_tab_columns
    WHERE table_name = 'RAG_CHUNKS' AND column_name = 'HYBRID_PAYLOAD';
    IF v_count = 0 THEN
        EXECUTE IMMEDIATE 'ALTER TABLE rag_chunks ADD (hybrid_payload JSON)';
    END IF;
END;
/

UPDATE rag_chunks SET hybrid_payload = JSON_OBJECT(
    'text' VALUE search_text,
    'embedding' VALUE JSON(VECTOR_SERIALIZE(embedding RETURNING CLOB))
    ABSENT ON NULL RETURNING JSON
) WHERE hybrid_payload IS NULL;

{_native_hybrid_setup_sql("hnsw")}
""".strip()


def native_hybrid_ivf_migration_sql() -> str:
    """追加HNSWのvector memory競合を避け、派生索引だけをIVFへ作り直す。"""
    return f"""
DECLARE
    v_count NUMBER;
BEGIN
    SELECT COUNT(*) INTO v_count FROM user_indexes
    WHERE index_name = '{NATIVE_HYBRID_INDEX}';
    IF v_count = 1 THEN
        EXECUTE IMMEDIATE 'DROP INDEX {NATIVE_HYBRID_INDEX} FORCE';
    END IF;
    SELECT COUNT(*) INTO v_count FROM ctx_user_preferences
    WHERE pre_name = '{NATIVE_HYBRID_PREFERENCE}';
    IF v_count = 1 THEN
        DBMS_VECTOR_CHAIN.DROP_PREFERENCE('{NATIVE_HYBRID_PREFERENCE}');
    END IF;
END;
/

{_native_hybrid_setup_sql()}
""".strip()


_NATIVE_SCOPE_TABLE_SQL = """
CREATE TABLE rag_native_search_scopes (
    chunk_set_id VARCHAR2(64) PRIMARY KEY,
    native_scope_id NUMBER GENERATED ALWAYS AS IDENTITY UNIQUE,
    CONSTRAINT RAG_NATIVE_SCOPE_CHUNK_SET_FK FOREIGN KEY (chunk_set_id)
        REFERENCES rag_chunk_sets(chunk_set_id) ON DELETE CASCADE
)
""".strip()

_NATIVE_SCOPE_TRIGGER_SQL = """
BEGIN
    EXECUTE IMMEDIATE q'~CREATE OR REPLACE TRIGGER RAG_CHUNKS_NATIVE_SCOPE_SYNC
BEFORE INSERT OR UPDATE OF chunk_set_id, native_scope_id ON rag_chunks FOR EACH ROW
DECLARE
    v_known_set NUMBER;
BEGIN
    IF :NEW.chunk_set_id IS NULL THEN
        :NEW.native_scope_id := NULL;
    ELSE
        SELECT COUNT(*) INTO v_known_set FROM rag_chunk_sets
        WHERE chunk_set_id = :NEW.chunk_set_id;
        IF v_known_set = 0 THEN
            :NEW.native_scope_id := NULL;
            RETURN;
        END IF;
        BEGIN
            SELECT native_scope_id INTO :NEW.native_scope_id FROM rag_native_search_scopes
            WHERE chunk_set_id = :NEW.chunk_set_id;
        EXCEPTION WHEN NO_DATA_FOUND THEN
            BEGIN
                INSERT INTO rag_native_search_scopes (chunk_set_id) VALUES (:NEW.chunk_set_id)
                RETURNING native_scope_id INTO :NEW.native_scope_id;
            EXCEPTION WHEN DUP_VAL_ON_INDEX THEN
                SELECT native_scope_id INTO :NEW.native_scope_id FROM rag_native_search_scopes
                WHERE chunk_set_id = :NEW.chunk_set_id;
            END;
        END;
    END IF;
END;~';
END;
/
""".strip()

_NATIVE_SCOPE_COLUMN_SQL = """
DECLARE
    v_count NUMBER;
BEGIN
    SELECT COUNT(*) INTO v_count FROM user_tab_columns
    WHERE table_name = 'RAG_CHUNKS' AND column_name = 'NATIVE_SCOPE_ID';
    IF v_count = 0 THEN
        EXECUTE IMMEDIATE 'ALTER TABLE rag_chunks ADD (native_scope_id NUMBER)';
    END IF;
END;
/
""".strip()

_NATIVE_SCOPE_INDEX_SQL = "CREATE INDEX RAG_CHUNKS_NATIVE_SCOPE_IDX ON rag_chunks(native_scope_id)"


def native_hybrid_scope_objects_sql() -> str:
    """新規作成は schema manifest が検査できる通常の DDL とする。"""
    return (
        f"{_NATIVE_SCOPE_TABLE_SQL};\n\n{_NATIVE_SCOPE_COLUMN_SQL}\n\n"
        f"{_NATIVE_SCOPE_INDEX_SQL};\n\n{_NATIVE_SCOPE_TRIGGER_SQL}"
    )


def native_hybrid_scope_migration_sql() -> str:
    """短い安定した数値へ対応付ける。既存の圧縮表へidentityを追加しない。"""
    return f"""
DECLARE
    v_count NUMBER;
BEGIN
    SELECT COUNT(*) INTO v_count FROM user_tables WHERE table_name = 'RAG_NATIVE_SEARCH_SCOPES';
    IF v_count = 0 THEN
        EXECUTE IMMEDIATE q'~{_NATIVE_SCOPE_TABLE_SQL}~';
    END IF;
END;
/

{_NATIVE_SCOPE_COLUMN_SQL}

INSERT INTO rag_native_search_scopes (chunk_set_id)
SELECT cs.chunk_set_id FROM rag_chunk_sets cs WHERE NOT EXISTS (
    SELECT 1 FROM rag_native_search_scopes s WHERE s.chunk_set_id = cs.chunk_set_id
);

UPDATE rag_chunks c SET native_scope_id = (
    SELECT s.native_scope_id FROM rag_native_search_scopes s WHERE s.chunk_set_id = c.chunk_set_id
) WHERE c.chunk_set_id IS NOT NULL AND c.native_scope_id IS NULL;

DECLARE
    v_count NUMBER;
BEGIN
    SELECT COUNT(*) INTO v_count FROM user_indexes WHERE index_name = 'RAG_CHUNKS_NATIVE_SCOPE_IDX';
    IF v_count = 0 THEN
        EXECUTE IMMEDIATE '{_NATIVE_SCOPE_INDEX_SQL}';
    END IF;
END;
/

{_NATIVE_SCOPE_TRIGGER_SQL}
""".strip()


def native_hybrid_missing_scope_migration_sql() -> str:
    """登録済みの親集合を持つ欠落行だけを補完する。本文と埋め込みは更新しない。"""
    return """
INSERT INTO rag_native_search_scopes (chunk_set_id)
SELECT cs.chunk_set_id FROM rag_chunk_sets cs
WHERE EXISTS (
    SELECT 1 FROM rag_chunks c
    WHERE c.chunk_set_id = cs.chunk_set_id AND c.native_scope_id IS NULL
) AND NOT EXISTS (
    SELECT 1 FROM rag_native_search_scopes s WHERE s.chunk_set_id = cs.chunk_set_id
);

UPDATE rag_chunks c SET native_scope_id = (
    SELECT s.native_scope_id FROM rag_native_search_scopes s WHERE s.chunk_set_id = c.chunk_set_id
) WHERE c.native_scope_id IS NULL AND EXISTS (
    SELECT 1 FROM rag_native_search_scopes s
    JOIN rag_chunk_sets cs ON cs.chunk_set_id = s.chunk_set_id
    WHERE s.chunk_set_id = c.chunk_set_id
);
""".strip()


def native_hybrid_sync_columns_migration_sql() -> str:
    """保存済みvectorを扱うため、UPDATE OF句ではなく更新述語で入力列を判定する。"""
    return _native_hybrid_setup_sql(sync_changed_columns=True)
