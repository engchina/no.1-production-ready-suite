"""原生索引の整理要求。業務DMLと同じtransactionに小さい記録だけを残す。"""

EVENTS_TABLE = "RAG_NATIVE_INDEX_EVENTS"
STATE_TABLE = "RAG_NATIVE_INDEX_MAINTENANCE"
MAINTENANCE_TRIGGER = "RAG_CHUNKS_NATIVE_MAINT"
STATE_KEY = "hybrid_vectors"
# FULL の整理を最後に終えた時刻の行(``last_completed_at``)。要求の lease とは別に持つ。
FULL_STATE_KEY = "hybrid_vectors_full"


def native_maintenance_tables_sql() -> str:
    return f"""
CREATE TABLE {STATE_TABLE} (
    state_key VARCHAR2(64) PRIMARY KEY,
    lease_owner VARCHAR2(128),
    lease_token VARCHAR2(32),
    lease_until TIMESTAMP WITH TIME ZONE,
    next_attempt_at TIMESTAMP WITH TIME ZONE DEFAULT SYSTIMESTAMP NOT NULL,
    attempt_count NUMBER(10) DEFAULT 0 NOT NULL,
    bootstrap_requested NUMBER(1) DEFAULT 0 NOT NULL,
    last_error_type VARCHAR2(128),
    last_completed_at TIMESTAMP WITH TIME ZONE,
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT SYSTIMESTAMP NOT NULL
);

CREATE TABLE {EVENTS_TABLE} (
    event_id VARCHAR2(32) PRIMARY KEY,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT SYSTIMESTAMP NOT NULL
);

CREATE INDEX RAG_NATIVE_INDEX_EVENTS_TIME ON {EVENTS_TABLE} (created_at, event_id);
""".strip()


def native_maintenance_activation_sql() -> str:
    return f"""
BEGIN
    EXECUTE IMMEDIATE q'~CREATE OR REPLACE TRIGGER {MAINTENANCE_TRIGGER}
FOR DELETE OR UPDATE ON rag_chunks
COMPOUND TRIGGER
    v_dirty BOOLEAN := FALSE;
    BEFORE EACH ROW IS
    BEGIN
        IF (DELETING OR UPDATING('SEARCH_TEXT') OR UPDATING('EMBEDDING')
            OR UPDATING('HYBRID_PAYLOAD')) AND :OLD.embedding IS NOT NULL THEN
            v_dirty := TRUE;
        END IF;
    END BEFORE EACH ROW;
    AFTER STATEMENT IS
    BEGIN
        IF v_dirty THEN
            INSERT INTO {EVENTS_TABLE} (event_id) VALUES (RAWTOHEX(SYS_GUID()));
        END IF;
    END AFTER STATEMENT;
END;~';
END;
/

MERGE INTO {STATE_TABLE} s
USING (SELECT '{STATE_KEY}' state_key FROM dual) v ON (s.state_key=v.state_key)
WHEN NOT MATCHED THEN INSERT (state_key) VALUES (v.state_key);

INSERT INTO {EVENTS_TABLE} (event_id)
SELECT RAWTOHEX(SYS_GUID()) FROM {STATE_TABLE}
WHERE state_key='{STATE_KEY}' AND bootstrap_requested=0;

UPDATE {STATE_TABLE} SET bootstrap_requested=1
WHERE state_key='{STATE_KEY}' AND bootstrap_requested=0;
""".strip()
