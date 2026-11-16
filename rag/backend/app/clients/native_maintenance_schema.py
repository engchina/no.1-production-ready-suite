"""原生索引の整理要求。業務DMLと同じtransactionに小さい記録だけを残す。"""

EVENTS_TABLE = "RAG_NATIVE_INDEX_EVENTS"
STATE_TABLE = "RAG_NATIVE_INDEX_MAINTENANCE"
MAINTENANCE_TRIGGER = "RAG_CHUNKS_NATIVE_MAINT"
STATE_KEY = "hybrid_vectors"
# FULL の整理の行。要求の lease とは別に持つ。``next_attempt_at`` は次に FULL を始めてよい時刻、
# ``attempt_count`` は前回の FULL の完了の後に受けた要求の取得の回数(FULL の借り。0 なら不要)、
# ``last_completed_at`` は FULL を最後に終えた時刻(#4214)。
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


def _maintenance_trigger_sql(dirty_condition: str) -> str:
    return f"""
BEGIN
    EXECUTE IMMEDIATE q'~CREATE OR REPLACE TRIGGER {MAINTENANCE_TRIGGER}
FOR DELETE OR UPDATE ON rag_chunks
COMPOUND TRIGGER
    v_dirty BOOLEAN := FALSE;
    BEFORE EACH ROW IS
    BEGIN
        IF {dirty_condition} THEN
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
""".strip()


_BOOTSTRAP_SQL = f"""
MERGE INTO {STATE_TABLE} s
USING (SELECT '{STATE_KEY}' state_key FROM dual) v ON (s.state_key=v.state_key)
WHEN NOT MATCHED THEN INSERT (state_key) VALUES (v.state_key);

INSERT INTO {EVENTS_TABLE} (event_id)
SELECT RAWTOHEX(SYS_GUID()) FROM {STATE_TABLE}
WHERE state_key='{STATE_KEY}' AND bootstrap_requested=0;

UPDATE {STATE_TABLE} SET bootstrap_requested=1
WHERE state_key='{STATE_KEY}' AND bootstrap_requested=0;
""".strip()

# 旧 schema(埋め込みが rag_chunks の列)の条件。適用済みの migration の checksum を保つ。
_LEGACY_DIRTY_CONDITION = """(DELETING OR UPDATING('SEARCH_TEXT') OR UPDATING('EMBEDDING')
            OR UPDATING('HYBRID_PAYLOAD')) AND :OLD.embedding IS NOT NULL"""
# 埋め込みは別表にある(#5241)。原生索引の JSON に埋め込みがあった行の削除・JSON の書き換えを
# 整理の要求にする(初めて埋め込みを入れる書き換えは要求にしない)。別表は cascade の削除で読めない
# ため JSON を見る。最初の 1 行で決まれば、残りの行の JSON は読まない。
_DIRTY_CONDITION = """NOT v_dirty
            AND (DELETING OR UPDATING('SEARCH_TEXT') OR UPDATING('HYBRID_PAYLOAD'))
            AND JSON_EXISTS(:OLD.hybrid_payload, '$.embedding')"""


def native_maintenance_activation_sql() -> str:
    """旧 schema の migration(``20261102_004``)の SQL。checksum を保つため変えない。"""
    return f"{_maintenance_trigger_sql(_LEGACY_DIRTY_CONDITION)}\n\n{_BOOTSTRAP_SQL}"


def native_maintenance_trigger_sql() -> str:
    return _maintenance_trigger_sql(_DIRTY_CONDITION)


def native_maintenance_current_activation_sql() -> str:
    """作成の DDL(#5241 の schema)。"""
    return f"{native_maintenance_trigger_sql()}\n\n{_BOOTSTRAP_SQL}"
