"""既存の検索本文と埋め込みを原生hybrid索引へ同期する管理DDL。"""

from rag_engine.adapters.oracle.native_hybrid import (
    NATIVE_HYBRID_INDEX,
    NATIVE_HYBRID_PREFERENCE,
)

NATIVE_HYBRID_TRIGGER = "RAG_CHUNKS_HYBRID_SYNC"


def _native_hybrid_setup_sql(vector_type: str = "ivf") -> str:
    """DDL管理からだけ呼ぶ。索引作成時の再embeddingは行わない。"""
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
    SELECT JSON_OBJECT(
        'text' VALUE :NEW.search_text,
        'embedding' VALUE JSON(VECTOR_SERIALIZE(:NEW.embedding RETURNING CLOB))
        ABSENT ON NULL RETURNING JSON
    ) INTO :NEW.hybrid_payload FROM dual;
END;~';
    END IF;
END;
/
""".strip()


def native_hybrid_objects_sql() -> str:
    """管理対象のpreference・同期trigger・原生索引を作る。"""
    return f"""
{_native_hybrid_setup_sql()}

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
