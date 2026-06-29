-- migration: 20260615_001_ingestion_jobs_attempt_counters
DECLARE
    v_column_count NUMBER;
    v_nullable VARCHAR2(1);
BEGIN
    SELECT COUNT(*)
    INTO v_column_count
    FROM user_tab_columns
    WHERE table_name = 'RAG_INGESTION_JOBS'
      AND column_name = 'ATTEMPT_COUNT';

    IF v_column_count = 0 THEN
        EXECUTE IMMEDIATE
            'ALTER TABLE rag_ingestion_jobs ADD '
            || '(attempt_count NUMBER(5) DEFAULT 0 NOT NULL)';
    ELSE
        SELECT nullable
        INTO v_nullable
        FROM user_tab_columns
        WHERE table_name = 'RAG_INGESTION_JOBS'
          AND column_name = 'ATTEMPT_COUNT';

        EXECUTE IMMEDIATE
            'UPDATE rag_ingestion_jobs SET attempt_count = 0 '
            || 'WHERE attempt_count IS NULL';
        IF v_nullable = 'Y' THEN
            EXECUTE IMMEDIATE
                'ALTER TABLE rag_ingestion_jobs MODIFY '
                || '(attempt_count DEFAULT 0 NOT NULL)';
        ELSE
            EXECUTE IMMEDIATE
                'ALTER TABLE rag_ingestion_jobs MODIFY '
                || '(attempt_count DEFAULT 0)';
        END IF;
    END IF;
END;
/

DECLARE
    v_column_count NUMBER;
    v_nullable VARCHAR2(1);
BEGIN
    SELECT COUNT(*)
    INTO v_column_count
    FROM user_tab_columns
    WHERE table_name = 'RAG_INGESTION_JOBS'
      AND column_name = 'MAX_ATTEMPTS';

    IF v_column_count = 0 THEN
        EXECUTE IMMEDIATE
            'ALTER TABLE rag_ingestion_jobs ADD '
            || '(max_attempts NUMBER(5) DEFAULT 3 NOT NULL)';
    ELSE
        SELECT nullable
        INTO v_nullable
        FROM user_tab_columns
        WHERE table_name = 'RAG_INGESTION_JOBS'
          AND column_name = 'MAX_ATTEMPTS';

        EXECUTE IMMEDIATE
            'UPDATE rag_ingestion_jobs SET max_attempts = 3 '
            || 'WHERE max_attempts IS NULL';
        IF v_nullable = 'Y' THEN
            EXECUTE IMMEDIATE
                'ALTER TABLE rag_ingestion_jobs MODIFY '
                || '(max_attempts DEFAULT 3 NOT NULL)';
        ELSE
            EXECUTE IMMEDIATE
                'ALTER TABLE rag_ingestion_jobs MODIFY '
                || '(max_attempts DEFAULT 3)';
        END IF;
    END IF;
END;
/

DECLARE
    v_constraint_count NUMBER;
BEGIN
    SELECT COUNT(*)
    INTO v_constraint_count
    FROM user_constraints
    WHERE table_name = 'RAG_INGESTION_JOBS'
      AND constraint_name = 'RAG_INGESTION_JOBS_ATTEMPTS_CK';

    IF v_constraint_count > 0 THEN
        EXECUTE IMMEDIATE
            'ALTER TABLE rag_ingestion_jobs DROP CONSTRAINT '
            || 'rag_ingestion_jobs_attempts_ck';
    END IF;

    EXECUTE IMMEDIATE
        'ALTER TABLE rag_ingestion_jobs ADD CONSTRAINT '
        || 'rag_ingestion_jobs_attempts_ck CHECK '
        || '(attempt_count >= 0 AND max_attempts >= 1)';
END;
/

-- migration: 20260616_001_search_audit_search_mode
DECLARE
    v_mode_count NUMBER;
    v_search_mode_count NUMBER;
    v_constraint_count NUMBER;
BEGIN
    SELECT COUNT(*)
    INTO v_mode_count
    FROM user_tab_columns
    WHERE table_name = 'RAG_SEARCH_AUDIT'
      AND column_name = 'MODE';

    SELECT COUNT(*)
    INTO v_search_mode_count
    FROM user_tab_columns
    WHERE table_name = 'RAG_SEARCH_AUDIT'
      AND column_name = 'SEARCH_MODE';

    IF v_mode_count > 0 AND v_search_mode_count = 0 THEN
        EXECUTE IMMEDIATE
            'ALTER TABLE rag_search_audit RENAME COLUMN mode TO search_mode';
    ELSIF v_mode_count = 0 AND v_search_mode_count = 0 THEN
        EXECUTE IMMEDIATE
            'ALTER TABLE rag_search_audit ADD '
            || '(search_mode VARCHAR2(16) DEFAULT ''hybrid'' NOT NULL)';
    END IF;

    SELECT COUNT(*)
    INTO v_constraint_count
    FROM user_constraints
    WHERE table_name = 'RAG_SEARCH_AUDIT'
      AND constraint_name IN ('RAG_SEARCH_AUDIT_MODE_CK', 'RAG_SEARCH_AUDIT_SEARCH_MODE_CK');

    IF v_constraint_count > 0 THEN
        BEGIN
            EXECUTE IMMEDIATE
                'ALTER TABLE rag_search_audit DROP CONSTRAINT rag_search_audit_mode_ck';
        EXCEPTION
            WHEN OTHERS THEN NULL;
        END;
        BEGIN
            EXECUTE IMMEDIATE
                'ALTER TABLE rag_search_audit DROP CONSTRAINT rag_search_audit_search_mode_ck';
        EXCEPTION
            WHEN OTHERS THEN NULL;
        END;
    END IF;

    EXECUTE IMMEDIATE
        'ALTER TABLE rag_search_audit ADD CONSTRAINT '
        || 'rag_search_audit_search_mode_ck CHECK '
        || '(search_mode IN (''hybrid'', ''vector'', ''keyword''))';
END;
/

-- migration: 20260616_002_evaluation_runs_result_sha256
DECLARE
    v_column_count NUMBER;
    v_index_count NUMBER;
BEGIN
    SELECT COUNT(*)
    INTO v_column_count
    FROM user_tab_columns
    WHERE table_name = 'RAG_EVALUATION_RUNS'
      AND column_name = 'RESULT_SHA256';

    IF v_column_count = 0 THEN
        EXECUTE IMMEDIATE
            'ALTER TABLE rag_evaluation_runs ADD '
            || '(result_sha256 CHAR(64) DEFAULT '''
            || RPAD('0', 64, '0')
            || ''' NOT NULL)';
    END IF;

    SELECT COUNT(*)
    INTO v_index_count
    FROM user_indexes
    WHERE index_name = 'RAG_EVALUATION_RUNS_RESULT_HASH_IDX';

    IF v_index_count = 0 THEN
        EXECUTE IMMEDIATE
            'CREATE INDEX rag_evaluation_runs_result_hash_idx '
            || 'ON rag_evaluation_runs (result_sha256)';
    END IF;
END;
/

-- migration: 20260616_003_ingestion_jobs_cancelled_status
DECLARE
    v_constraint_count NUMBER;
BEGIN
    SELECT COUNT(*)
    INTO v_constraint_count
    FROM user_constraints
    WHERE table_name = 'RAG_INGESTION_JOBS'
      AND constraint_name = 'RAG_INGESTION_JOBS_STATUS_CK';

    IF v_constraint_count > 0 THEN
        EXECUTE IMMEDIATE
            'ALTER TABLE rag_ingestion_jobs DROP CONSTRAINT '
            || 'rag_ingestion_jobs_status_ck';
    END IF;

    EXECUTE IMMEDIATE
        'ALTER TABLE rag_ingestion_jobs ADD CONSTRAINT '
        || 'rag_ingestion_jobs_status_ck CHECK '
        || '(status IN (''QUEUED'', ''RUNNING'', ''SUCCEEDED'', ''FAILED'', '
        || '''SKIPPED'', ''CANCELLED''))';
END;
/

-- migration: 20260616_004_ingestion_segments
DECLARE
    v_table_count NUMBER;
    v_index_count NUMBER;
BEGIN
    SELECT COUNT(*)
    INTO v_table_count
    FROM user_tables
    WHERE table_name = 'RAG_INGESTION_SEGMENTS';

    IF v_table_count = 0 THEN
        EXECUTE IMMEDIATE
            'CREATE TABLE rag_ingestion_segments ('
            || 'segment_id VARCHAR2(128) PRIMARY KEY,'
            || 'document_id VARCHAR2(64) NOT NULL,'
            || 'tenant_id_hash CHAR(64),'
            || 'status VARCHAR2(32) DEFAULT ''QUEUED'' NOT NULL,'
            || 'parser_backend VARCHAR2(80) DEFAULT ''enterprise_ai'' NOT NULL,'
            || 'parser_profile VARCHAR2(80) DEFAULT ''enterprise_ai_generic'' NOT NULL,'
            || 'page_start NUMBER(10),'
            || 'page_end NUMBER(10),'
            || 'attempt_count NUMBER(5) DEFAULT 0 NOT NULL,'
            || 'artifact_path VARCHAR2(1024),'
            || 'error_code VARCHAR2(128),'
            || 'error_message VARCHAR2(2000),'
            || 'created_at TIMESTAMP WITH TIME ZONE DEFAULT SYSTIMESTAMP NOT NULL,'
            || 'updated_at TIMESTAMP WITH TIME ZONE DEFAULT SYSTIMESTAMP NOT NULL,'
            || 'CONSTRAINT rag_ingestion_segments_status_ck CHECK '
            || '(status IN (''QUEUED'', ''RUNNING'', ''SUCCEEDED'', ''FAILED'', ''CANCELLED'')),'
            || 'CONSTRAINT rag_ingestion_segments_attempts_ck CHECK (attempt_count >= 0),'
            || 'CONSTRAINT rag_ingestion_segments_page_range_ck CHECK '
            || '(page_start IS NULL OR page_end IS NULL OR page_start <= page_end),'
            || 'CONSTRAINT rag_ingestion_segments_document_fk FOREIGN KEY (document_id) '
            || 'REFERENCES rag_documents (document_id) ON DELETE CASCADE'
            || ')';
    END IF;

    SELECT COUNT(*)
    INTO v_index_count
    FROM user_indexes
    WHERE index_name IN (
        'RAG_INGESTION_SEGMENTS_DOC_STATUS_IDX',
        'RAG_INGESTION_SEGMENTS_DOCUMENT_STATUS_IDX'
    );

    IF v_index_count = 0 THEN
        EXECUTE IMMEDIATE
            'CREATE INDEX rag_ingestion_segments_document_status_idx '
            || 'ON rag_ingestion_segments (document_id, status, page_start, page_end)';
    END IF;

    SELECT COUNT(*)
    INTO v_index_count
    FROM user_indexes
    WHERE index_name = 'RAG_INGESTION_SEGMENTS_TENANT_STATUS_IDX';

    IF v_index_count = 0 THEN
        EXECUTE IMMEDIATE
            'CREATE INDEX rag_ingestion_segments_tenant_status_idx '
            || 'ON rag_ingestion_segments (tenant_id_hash, status, updated_at DESC)';
    END IF;
END;
/

-- migration: 20260616_005_search_audit_memory_engineering
DECLARE
    PROCEDURE add_column_if_missing(p_column_name VARCHAR2, p_column_ddl VARCHAR2) IS
        v_column_count NUMBER;
    BEGIN
        SELECT COUNT(*)
        INTO v_column_count
        FROM user_tab_columns
        WHERE table_name = 'RAG_SEARCH_AUDIT'
          AND column_name = p_column_name;

        IF v_column_count = 0 THEN
            EXECUTE IMMEDIATE
                'ALTER TABLE rag_search_audit ADD (' || p_column_ddl || ')';
        END IF;
    END;
BEGIN
    add_column_if_missing('MEMORY_PLAN_ID', 'memory_plan_id VARCHAR2(32)');
    add_column_if_missing(
        'EVIDENCE_COUNT',
        'evidence_count NUMBER(10) DEFAULT 0 NOT NULL'
    );
    add_column_if_missing(
        'SUPPORT_COUNT',
        'support_count NUMBER(10) DEFAULT 0 NOT NULL'
    );
    add_column_if_missing(
        'STRUCTURE_COUNT',
        'structure_count NUMBER(10) DEFAULT 0 NOT NULL'
    );
    add_column_if_missing(
        'HISTORY_COUNT',
        'history_count NUMBER(10) DEFAULT 0 NOT NULL'
    );
    add_column_if_missing(
        'RESOLVER_REJECTED_COUNT',
        'resolver_rejected_count NUMBER(10) DEFAULT 0 NOT NULL'
    );
    add_column_if_missing(
        'INSUFFICIENT_CONTEXT_COUNT',
        'insufficient_context_count NUMBER(10) DEFAULT 0 NOT NULL'
    );
    add_column_if_missing(
        'AGENT_MEMORY_RETRIEVED_COUNT',
        'agent_memory_retrieved_count NUMBER(10) DEFAULT 0 NOT NULL'
    );
    add_column_if_missing(
        'AGENT_MEMORY_WRITEBACK_COUNT',
        'agent_memory_writeback_count NUMBER(10) DEFAULT 0 NOT NULL'
    );
    add_column_if_missing(
        'AGENT_MEMORY_WRITEBACK_STATUS',
        'agent_memory_writeback_status VARCHAR2(32) DEFAULT ''skipped'' NOT NULL'
    );
END;
/

-- migration: 20260616_006_agent_memories
DECLARE
    v_table_count NUMBER;
    v_index_count NUMBER;

    PROCEDURE add_column_if_missing(p_column_name VARCHAR2, p_column_ddl VARCHAR2) IS
        v_column_count NUMBER;
    BEGIN
        SELECT COUNT(*)
        INTO v_column_count
        FROM user_tab_columns
        WHERE table_name = 'RAG_AGENT_MEMORIES'
          AND column_name = p_column_name;

        IF v_column_count = 0 THEN
            EXECUTE IMMEDIATE
                'ALTER TABLE rag_agent_memories ADD (' || p_column_ddl || ')';
        END IF;
    END;

    PROCEDURE create_index_if_missing(p_index_name VARCHAR2, p_sql VARCHAR2) IS
    BEGIN
        SELECT COUNT(*)
        INTO v_index_count
        FROM user_indexes
        WHERE index_name = p_index_name;

        IF v_index_count = 0 THEN
            EXECUTE IMMEDIATE p_sql;
        END IF;
    END;
BEGIN
    SELECT COUNT(*)
    INTO v_table_count
    FROM user_tables
    WHERE table_name = 'RAG_AGENT_MEMORIES';

    IF v_table_count = 0 THEN
        EXECUTE IMMEDIATE
            'CREATE TABLE rag_agent_memories ('
            || 'memory_id VARCHAR2(64) PRIMARY KEY,'
            || 'tenant_id_hash CHAR(64),'
            || 'user_id_hash CHAR(64),'
            || 'role_id_hash CHAR(64),'
            || 'agent_id_hash CHAR(64),'
            || 'thread_id_hash CHAR(64),'
            || 'trace_id VARCHAR2(64) NOT NULL,'
            || 'memory_text CLOB NOT NULL,'
            || 'metadata_json JSON,'
            || 'embedding VECTOR(1536, FLOAT32) NOT NULL,'
            || 'usefulness_score NUMBER(8,6) DEFAULT 0.5 NOT NULL,'
            || 'eval_count NUMBER(10) DEFAULT 0 NOT NULL,'
            || 'created_at TIMESTAMP WITH TIME ZONE DEFAULT SYSTIMESTAMP NOT NULL,'
            || 'updated_at TIMESTAMP WITH TIME ZONE DEFAULT SYSTIMESTAMP NOT NULL,'
            || 'CONSTRAINT rag_agent_memories_usefulness_ck CHECK '
            || '(usefulness_score >= 0 AND usefulness_score <= 1),'
            || 'CONSTRAINT rag_agent_memories_eval_count_ck CHECK (eval_count >= 0)'
            || ')';
    END IF;

    add_column_if_missing('ROLE_ID_HASH', 'role_id_hash CHAR(64)');

    create_index_if_missing(
        'RAG_AGENT_MEMORIES_EMBEDDING_HNSW_IDX',
        'CREATE VECTOR INDEX rag_agent_memories_embedding_hnsw_idx '
        || 'ON rag_agent_memories (embedding) '
        || 'ORGANIZATION INMEMORY NEIGHBOR GRAPH DISTANCE COSINE '
        || 'WITH TARGET ACCURACY 95 '
        || 'PARAMETERS (TYPE HNSW, NEIGHBORS 32, EFCONSTRUCTION 500)'
    );
    create_index_if_missing(
        'RAG_AGENT_MEMORIES_TEXT_IDX',
        'CREATE INDEX rag_agent_memories_text_idx '
        || 'ON rag_agent_memories (memory_text) INDEXTYPE IS CTXSYS.CONTEXT'
    );
    create_index_if_missing(
        'RAG_AGENT_MEMORIES_SCOPE_IDX',
        'CREATE INDEX rag_agent_memories_scope_idx '
        || 'ON rag_agent_memories (tenant_id_hash, user_id_hash, '
        || 'role_id_hash, agent_id_hash, thread_id_hash, updated_at DESC)'
    );
    create_index_if_missing(
        'RAG_AGENT_MEMORIES_TRACE_IDX',
        'CREATE INDEX rag_agent_memories_trace_idx ON rag_agent_memories (trace_id)'
    );
END;
/

-- migration: 20260617_001_ingestion_audit_file_processing_metrics
DECLARE
    v_index_count NUMBER;

    PROCEDURE add_column_if_missing(p_column_name VARCHAR2, p_column_ddl VARCHAR2) IS
        v_column_count NUMBER;
    BEGIN
        SELECT COUNT(*)
        INTO v_column_count
        FROM user_tab_columns
        WHERE table_name = 'RAG_INGESTION_AUDIT'
          AND column_name = p_column_name;

        IF v_column_count = 0 THEN
            EXECUTE IMMEDIATE
                'ALTER TABLE rag_ingestion_audit ADD (' || p_column_ddl || ')';
        END IF;
    END;
BEGIN
    add_column_if_missing('PARSER_BACKEND', 'parser_backend VARCHAR2(80)');
    add_column_if_missing('PARSER_PROFILE', 'parser_profile VARCHAR2(80)');
    add_column_if_missing(
        'SEGMENT_COUNT',
        'segment_count NUMBER(10) DEFAULT 0 NOT NULL'
    );
    add_column_if_missing(
        'FALLBACK_COUNT',
        'fallback_count NUMBER(10) DEFAULT 0 NOT NULL'
    );
    add_column_if_missing(
        'FAILED_SEGMENT_COUNT',
        'failed_segment_count NUMBER(10) DEFAULT 0 NOT NULL'
    );

    SELECT COUNT(*)
    INTO v_index_count
    FROM user_indexes
    WHERE index_name = 'RAG_INGESTION_AUDIT_PARSER_CREATED_IDX';

    IF v_index_count = 0 THEN
        EXECUTE IMMEDIATE
            'CREATE INDEX rag_ingestion_audit_parser_created_idx '
            || 'ON rag_ingestion_audit (parser_backend, parser_profile, created_at DESC)';
    END IF;
END;
/

-- migration: 20260617_002_search_audit_adaptive_context
DECLARE
    v_column_count NUMBER;
BEGIN
    SELECT COUNT(*)
    INTO v_column_count
    FROM user_tab_columns
    WHERE table_name = 'RAG_SEARCH_AUDIT'
      AND column_name = 'CONTEXT_ADAPTIVE_EXPANDED_COUNT';

    IF v_column_count = 0 THEN
        EXECUTE IMMEDIATE
            'ALTER TABLE rag_search_audit ADD '
            || '(context_adaptive_expanded_count NUMBER(10) DEFAULT 0 NOT NULL)';
    END IF;
END;
/

-- migration: 20260617_003_search_audit_dependency_context
DECLARE
    v_column_count NUMBER;
BEGIN
    SELECT COUNT(*)
    INTO v_column_count
    FROM user_tab_columns
    WHERE table_name = 'RAG_SEARCH_AUDIT'
      AND column_name = 'CONTEXT_DEPENDENCY_PROMOTED_COUNT';

    IF v_column_count = 0 THEN
        EXECUTE IMMEDIATE
            'ALTER TABLE rag_search_audit ADD '
            || '(context_dependency_promoted_count NUMBER(10) DEFAULT 0 NOT NULL)';
    END IF;
END;
/

-- migration: 20260618_001_documents_review_status
DECLARE
    v_constraint_count NUMBER;
BEGIN
    SELECT COUNT(*)
    INTO v_constraint_count
    FROM user_constraints
    WHERE table_name = 'RAG_DOCUMENTS'
      AND constraint_name = 'RAG_DOCUMENTS_STATUS_CK';

    IF v_constraint_count > 0 THEN
        EXECUTE IMMEDIATE
            'ALTER TABLE rag_documents DROP CONSTRAINT '
            || 'rag_documents_status_ck';
    END IF;

    EXECUTE IMMEDIATE
        'ALTER TABLE rag_documents ADD CONSTRAINT '
        || 'rag_documents_status_ck CHECK '
        || '(status IN (''UPLOADED'', ''PREPROCESSING'', ''PREPROCESSED'', '
        || '''INGESTING'', ''REVIEW'', ''CHUNKING'', ''CHUNKED'', ''INDEXING'', '
        || '''INDEXED'', ''ERROR''))';
END;
/

-- migration: 20260618_002_ingestion_jobs_phase
DECLARE
    v_column_count NUMBER;
    v_constraint_count NUMBER;
BEGIN
    SELECT COUNT(*)
    INTO v_column_count
    FROM user_tab_columns
    WHERE table_name = 'RAG_INGESTION_JOBS'
      AND column_name = 'PHASE';

    IF v_column_count = 0 THEN
        EXECUTE IMMEDIATE
            'ALTER TABLE rag_ingestion_jobs ADD '
            || '(phase VARCHAR2(16) DEFAULT ''PREPROCESS'' NOT NULL)';
    END IF;

    SELECT COUNT(*)
    INTO v_constraint_count
    FROM user_constraints
    WHERE table_name = 'RAG_INGESTION_JOBS'
      AND constraint_name = 'RAG_INGESTION_JOBS_PHASE_CK';

    IF v_constraint_count > 0 THEN
        EXECUTE IMMEDIATE
            'ALTER TABLE rag_ingestion_jobs DROP CONSTRAINT '
            || 'rag_ingestion_jobs_phase_ck';
    END IF;

    EXECUTE IMMEDIATE
        'ALTER TABLE rag_ingestion_jobs ADD CONSTRAINT '
        || 'rag_ingestion_jobs_phase_ck CHECK '
        || '(phase IN (''PREPROCESS'', ''EXTRACT'', ''CHUNK'', ''INDEX''))';
END;
/

-- migration: 20260619_001_business_views
DECLARE
    v_table_count NUMBER;
    v_index_count NUMBER;
BEGIN
    SELECT COUNT(*)
    INTO v_table_count
    FROM user_tables
    WHERE table_name = 'RAG_BUSINESS_VIEWS';

    IF v_table_count = 0 THEN
        EXECUTE IMMEDIATE
            'CREATE TABLE rag_business_views ('
            || 'business_view_id VARCHAR2(64) PRIMARY KEY,'
            || 'tenant_id_hash CHAR(64),'
            || 'name VARCHAR2(256) NOT NULL,'
            || 'description VARCHAR2(2000),'
            || 'status VARCHAR2(32) DEFAULT ''ACTIVE'' NOT NULL,'
            || 'view_config JSON,'
            || 'created_at TIMESTAMP WITH TIME ZONE DEFAULT SYSTIMESTAMP NOT NULL,'
            || 'updated_at TIMESTAMP WITH TIME ZONE DEFAULT SYSTIMESTAMP NOT NULL,'
            || 'archived_at TIMESTAMP WITH TIME ZONE,'
            || 'CONSTRAINT rag_business_views_status_ck CHECK '
            || '(status IN (''ACTIVE'', ''ARCHIVED''))'
            || ')';
    END IF;

    SELECT COUNT(*)
    INTO v_index_count
    FROM user_indexes
    WHERE index_name = 'RAG_BUSINESS_VIEWS_TENANT_NAME_UIDX';

    IF v_index_count = 0 THEN
        EXECUTE IMMEDIATE
            'CREATE UNIQUE INDEX rag_business_views_tenant_name_uidx '
            || 'ON rag_business_views (NVL(tenant_id_hash, ''__GLOBAL__''), LOWER(name))';
    END IF;

    SELECT COUNT(*)
    INTO v_index_count
    FROM user_indexes
    WHERE index_name = 'RAG_BUSINESS_VIEWS_TENANT_STATUS_IDX';

    IF v_index_count = 0 THEN
        EXECUTE IMMEDIATE
            'CREATE INDEX rag_business_views_tenant_status_idx '
            || 'ON rag_business_views (tenant_id_hash, status, updated_at DESC)';
    END IF;
END;
/

-- migration: 20260621_001_chunk_sets
DECLARE
    v_table_count NUMBER;
    v_index_count NUMBER;
    v_col_count   NUMBER;
    v_constraint_count NUMBER;
    PROCEDURE add_column_if_missing(
        p_table_name IN VARCHAR2,
        p_column_name IN VARCHAR2,
        p_definition IN VARCHAR2
    ) IS
    BEGIN
        SELECT COUNT(*) INTO v_col_count
        FROM user_tab_columns
        WHERE table_name = p_table_name AND column_name = p_column_name;

        IF v_col_count = 0 THEN
            EXECUTE IMMEDIATE 'ALTER TABLE ' || p_table_name || ' ADD (' || p_definition || ')';
        END IF;
    END;
BEGIN
    SELECT COUNT(*) INTO v_table_count
    FROM user_tables WHERE table_name = 'RAG_CHUNK_SETS';

    IF v_table_count = 0 THEN
        EXECUTE IMMEDIATE
            'CREATE TABLE rag_chunk_sets ('
            || 'chunk_set_id VARCHAR2(64) PRIMARY KEY,'
            || 'document_id VARCHAR2(64) NOT NULL,'
            || 'extraction_recipe_id VARCHAR2(64),'
            || 'tenant_id_hash CHAR(64),'
            || 'recipe_subset JSON,'
            || 'status VARCHAR2(32) DEFAULT ''INGESTING'' NOT NULL,'
            || 'chunk_count NUMBER(10) DEFAULT 0 NOT NULL,'
            || 'vector_count NUMBER(10) DEFAULT 0 NOT NULL,'
            || 'metrics_json JSON,'
            || 'created_at TIMESTAMP WITH TIME ZONE DEFAULT SYSTIMESTAMP NOT NULL,'
            || 'updated_at TIMESTAMP WITH TIME ZONE DEFAULT SYSTIMESTAMP NOT NULL,'
            || 'CONSTRAINT rag_chunk_sets_document_fk FOREIGN KEY (document_id) '
            || 'REFERENCES rag_documents (document_id) ON DELETE CASCADE,'
            || 'CONSTRAINT rag_chunk_sets_status_ck CHECK '
            || '(status IN (''INGESTING'', ''CHUNKED'', ''INDEXED'', ''ERROR''))'
            || ')';
    END IF;

    SELECT COUNT(*) INTO v_constraint_count
    FROM user_constraints
    WHERE table_name = 'RAG_CHUNK_SETS'
      AND constraint_name = 'RAG_CHUNK_SETS_STATUS_CK';

    IF v_constraint_count > 0 THEN
        EXECUTE IMMEDIATE
            'ALTER TABLE rag_chunk_sets DROP CONSTRAINT rag_chunk_sets_status_ck';
    END IF;

    EXECUTE IMMEDIATE
        'ALTER TABLE rag_chunk_sets ADD CONSTRAINT rag_chunk_sets_status_ck CHECK '
        || '(status IN (''INGESTING'', ''CHUNKED'', ''INDEXED'', ''ERROR''))';

    SELECT COUNT(*) INTO v_index_count
    FROM user_indexes WHERE index_name = 'RAG_CHUNK_SETS_DOCUMENT_IDX';

    IF v_index_count = 0 THEN
        EXECUTE IMMEDIATE
            'CREATE INDEX rag_chunk_sets_document_idx ON rag_chunk_sets (document_id, status)';
    END IF;

    SELECT COUNT(*) INTO v_col_count
    FROM user_tab_columns
    WHERE table_name = 'RAG_CHUNK_SETS' AND column_name = 'EXTRACTION_RECIPE_ID';

    IF v_col_count = 0 THEN
        EXECUTE IMMEDIATE 'ALTER TABLE rag_chunk_sets ADD (extraction_recipe_id VARCHAR2(64))';
    END IF;

    SELECT COUNT(*) INTO v_index_count
    FROM user_indexes WHERE index_name = 'RAG_CHUNK_SETS_EXTRACTION_IDX';

    IF v_index_count = 0 THEN
        EXECUTE IMMEDIATE
            'CREATE INDEX rag_chunk_sets_extraction_idx '
            || 'ON rag_chunk_sets (document_id, extraction_recipe_id)';
    END IF;

    SELECT COUNT(*) INTO v_table_count
    FROM user_tables WHERE table_name = 'RAG_DOCUMENT_EXTRACTIONS';

    IF v_table_count = 0 THEN
        EXECUTE IMMEDIATE
            'CREATE TABLE rag_document_extractions ('
            || 'document_id VARCHAR2(64) NOT NULL,'
            || 'extraction_recipe_id VARCHAR2(64) NOT NULL,'
            || 'source_sha256 CHAR(64),'
            || 'tenant_id_hash CHAR(64),'
            || 'recipe_subset JSON,'
            || 'extraction_json JSON,'
            || 'status VARCHAR2(32) DEFAULT ''planned_only'' NOT NULL,'
            || 'reason VARCHAR2(2000),'
            || 'metrics_json JSON,'
            || 'created_at TIMESTAMP WITH TIME ZONE DEFAULT SYSTIMESTAMP NOT NULL,'
            || 'updated_at TIMESTAMP WITH TIME ZONE DEFAULT SYSTIMESTAMP NOT NULL,'
            || 'CONSTRAINT rag_document_extractions_pk '
            || 'PRIMARY KEY (document_id, extraction_recipe_id),'
            || 'CONSTRAINT rag_doc_ext_document_fk FOREIGN KEY (document_id) '
            || 'REFERENCES rag_documents (document_id) ON DELETE CASCADE,'
            || 'CONSTRAINT rag_doc_ext_status_ck CHECK '
            || '(status IN (''not_requested'', ''planned_only'', ''materialized'', '
            || '''needs_reingest'', ''error''))'
            || ')';
    END IF;

    add_column_if_missing('RAG_DOCUMENT_EXTRACTIONS', 'DOCUMENT_ID', 'document_id VARCHAR2(64)');
    add_column_if_missing(
        'RAG_DOCUMENT_EXTRACTIONS',
        'EXTRACTION_RECIPE_ID',
        'extraction_recipe_id VARCHAR2(64)'
    );
    add_column_if_missing('RAG_DOCUMENT_EXTRACTIONS', 'SOURCE_SHA256', 'source_sha256 CHAR(64)');
    add_column_if_missing('RAG_DOCUMENT_EXTRACTIONS', 'TENANT_ID_HASH', 'tenant_id_hash CHAR(64)');
    add_column_if_missing('RAG_DOCUMENT_EXTRACTIONS', 'RECIPE_SUBSET', 'recipe_subset JSON');
    add_column_if_missing('RAG_DOCUMENT_EXTRACTIONS', 'EXTRACTION_JSON', 'extraction_json JSON');
    add_column_if_missing(
        'RAG_DOCUMENT_EXTRACTIONS',
        'STATUS',
        'status VARCHAR2(32) DEFAULT ''planned_only'''
    );
    add_column_if_missing('RAG_DOCUMENT_EXTRACTIONS', 'REASON', 'reason VARCHAR2(2000)');
    add_column_if_missing('RAG_DOCUMENT_EXTRACTIONS', 'METRICS_JSON', 'metrics_json JSON');
    add_column_if_missing(
        'RAG_DOCUMENT_EXTRACTIONS',
        'CREATED_AT',
        'created_at TIMESTAMP WITH TIME ZONE DEFAULT SYSTIMESTAMP'
    );
    add_column_if_missing(
        'RAG_DOCUMENT_EXTRACTIONS',
        'UPDATED_AT',
        'updated_at TIMESTAMP WITH TIME ZONE DEFAULT SYSTIMESTAMP'
    );

    SELECT COUNT(*) INTO v_col_count
    FROM user_tab_columns
    WHERE table_name = 'RAG_DOCUMENT_EXTRACTIONS' AND column_name = 'EXTRACTION_ID';

    IF v_col_count > 0 THEN
        BEGIN
            EXECUTE IMMEDIATE
                'ALTER TABLE rag_document_extractions '
                || 'MODIFY (extraction_id DEFAULT RAWTOHEX(SYS_GUID()))';
        EXCEPTION
            WHEN OTHERS THEN
                NULL;
        END;
    END IF;

    SELECT COUNT(*) INTO v_constraint_count
    FROM user_constraints
    WHERE table_name = 'RAG_DOCUMENT_EXTRACTIONS'
      AND constraint_name = 'RAG_DOCUMENT_EXTRACTIONS_STATUS_CK';

    IF v_constraint_count > 0 THEN
        EXECUTE IMMEDIATE
            'ALTER TABLE rag_document_extractions '
            || 'DROP CONSTRAINT rag_document_extractions_status_ck';
    END IF;

    SELECT COUNT(*) INTO v_constraint_count
    FROM user_constraints
    WHERE table_name = 'RAG_DOCUMENT_EXTRACTIONS'
      AND constraint_name = 'RAG_DOC_EXT_STATUS_CK';

    IF v_constraint_count = 0 THEN
        BEGIN
            EXECUTE IMMEDIATE
                'ALTER TABLE rag_document_extractions ADD CONSTRAINT rag_doc_ext_status_ck '
                || 'CHECK (status IN (''not_requested'', ''planned_only'', ''materialized'', '
                || '''needs_reingest'', ''error''))';
        EXCEPTION
            WHEN OTHERS THEN
                NULL;
        END;
    END IF;

    SELECT COUNT(*) INTO v_index_count
    FROM user_indexes
    WHERE index_name IN (
        'RAG_DOC_EXT_STATUS_IDX',
        'RAG_DOCUMENT_EXTRACTIONS_DOCUMENT_IDX'
    );

    IF v_index_count = 0 THEN
        EXECUTE IMMEDIATE
            'CREATE INDEX rag_doc_ext_status_idx ON rag_document_extractions (document_id, status)';
    END IF;

    SELECT COUNT(*) INTO v_table_count
    FROM user_tables WHERE table_name = 'RAG_ARTIFACT_LAYERS';

    IF v_table_count = 0 THEN
        EXECUTE IMMEDIATE
            'CREATE TABLE rag_artifact_layers ('
            || 'layer_id VARCHAR2(64) PRIMARY KEY,'
            || 'layer_kind VARCHAR2(32) NOT NULL,'
            || 'parent_chunk_set_id VARCHAR2(64) NOT NULL,'
            || 'document_id VARCHAR2(64) NOT NULL,'
            || 'tenant_id_hash CHAR(64),'
            || 'requested NUMBER(1) DEFAULT 1 NOT NULL,'
            || 'status VARCHAR2(32) DEFAULT ''planned_only'' NOT NULL,'
            || 'reason VARCHAR2(2000),'
            || 'metrics_json JSON,'
            || 'created_at TIMESTAMP WITH TIME ZONE DEFAULT SYSTIMESTAMP NOT NULL,'
            || 'updated_at TIMESTAMP WITH TIME ZONE DEFAULT SYSTIMESTAMP NOT NULL,'
            || 'CONSTRAINT rag_artifact_layers_chunk_set_fk FOREIGN KEY (parent_chunk_set_id) '
            || 'REFERENCES rag_chunk_sets (chunk_set_id) ON DELETE CASCADE,'
            || 'CONSTRAINT rag_artifact_layers_document_fk FOREIGN KEY (document_id) '
            || 'REFERENCES rag_documents (document_id) ON DELETE CASCADE,'
            || 'CONSTRAINT rag_artifact_layers_requested_ck CHECK (requested IN (0, 1)),'
            || 'CONSTRAINT rag_artifact_layers_kind_ck CHECK '
            || '(layer_kind IN (''metadata'', ''graph'', ''navigation'')),'
            || 'CONSTRAINT rag_artifact_layers_status_ck CHECK '
            || '(status IN (''not_requested'', ''planned_only'', ''materialized'', '
            || '''needs_reingest'', ''error''))'
            || ')';
    END IF;

    add_column_if_missing('RAG_ARTIFACT_LAYERS', 'LAYER_ID', 'layer_id VARCHAR2(64)');
    add_column_if_missing('RAG_ARTIFACT_LAYERS', 'LAYER_KIND', 'layer_kind VARCHAR2(32)');
    add_column_if_missing(
        'RAG_ARTIFACT_LAYERS',
        'PARENT_CHUNK_SET_ID',
        'parent_chunk_set_id VARCHAR2(64)'
    );
    add_column_if_missing('RAG_ARTIFACT_LAYERS', 'DOCUMENT_ID', 'document_id VARCHAR2(64)');
    add_column_if_missing('RAG_ARTIFACT_LAYERS', 'TENANT_ID_HASH', 'tenant_id_hash CHAR(64)');
    add_column_if_missing('RAG_ARTIFACT_LAYERS', 'REQUESTED', 'requested NUMBER(1) DEFAULT 1');
    add_column_if_missing(
        'RAG_ARTIFACT_LAYERS',
        'STATUS',
        'status VARCHAR2(32) DEFAULT ''planned_only'''
    );
    add_column_if_missing('RAG_ARTIFACT_LAYERS', 'REASON', 'reason VARCHAR2(2000)');
    add_column_if_missing('RAG_ARTIFACT_LAYERS', 'METRICS_JSON', 'metrics_json JSON');
    add_column_if_missing(
        'RAG_ARTIFACT_LAYERS',
        'CREATED_AT',
        'created_at TIMESTAMP WITH TIME ZONE DEFAULT SYSTIMESTAMP'
    );
    add_column_if_missing(
        'RAG_ARTIFACT_LAYERS',
        'UPDATED_AT',
        'updated_at TIMESTAMP WITH TIME ZONE DEFAULT SYSTIMESTAMP'
    );

    SELECT COUNT(*) INTO v_constraint_count
    FROM user_constraints
    WHERE table_name = 'RAG_ARTIFACT_LAYERS'
      AND constraint_name = 'RAG_ARTIFACT_LAYERS_STATUS_CK';

    IF v_constraint_count > 0 THEN
        EXECUTE IMMEDIATE
            'ALTER TABLE rag_artifact_layers '
            || 'DROP CONSTRAINT rag_artifact_layers_status_ck';
    END IF;

    SELECT COUNT(*) INTO v_constraint_count
    FROM user_constraints
    WHERE table_name = 'RAG_ARTIFACT_LAYERS'
      AND constraint_name = 'RAG_ARTIFACT_LAYERS_KIND_CK';

    IF v_constraint_count > 0 THEN
        EXECUTE IMMEDIATE
            'ALTER TABLE rag_artifact_layers '
            || 'DROP CONSTRAINT rag_artifact_layers_kind_ck';
    END IF;

    SELECT COUNT(*) INTO v_constraint_count
    FROM user_constraints
    WHERE table_name = 'RAG_ARTIFACT_LAYERS'
      AND constraint_name = 'RAG_ARTIFACT_LAYERS_STATUS_CK';

    IF v_constraint_count = 0 THEN
        BEGIN
            EXECUTE IMMEDIATE
                'ALTER TABLE rag_artifact_layers ADD CONSTRAINT rag_artifact_layers_status_ck '
                || 'CHECK (status IN (''not_requested'', ''planned_only'', ''materialized'', '
                || '''needs_reingest'', ''error''))';
        EXCEPTION
            WHEN OTHERS THEN
                NULL;
        END;
    END IF;

    SELECT COUNT(*) INTO v_constraint_count
    FROM user_constraints
    WHERE table_name = 'RAG_ARTIFACT_LAYERS'
      AND constraint_name = 'RAG_ARTIFACT_LAYERS_KIND_CK';

    IF v_constraint_count = 0 THEN
        BEGIN
            EXECUTE IMMEDIATE
                'ALTER TABLE rag_artifact_layers ADD CONSTRAINT rag_artifact_layers_kind_ck '
                || 'CHECK (layer_kind IN (''metadata'', ''graph'', ''navigation''))';
        EXCEPTION
            WHEN OTHERS THEN
                NULL;
        END;
    END IF;

    SELECT COUNT(*) INTO v_index_count
    FROM user_indexes WHERE index_name = 'RAG_ARTIFACT_LAYERS_PARENT_IDX';

    IF v_index_count = 0 THEN
        EXECUTE IMMEDIATE
            'CREATE INDEX rag_artifact_layers_parent_idx '
            || 'ON rag_artifact_layers (parent_chunk_set_id, layer_kind, status)';
    END IF;

    SELECT COUNT(*) INTO v_table_count
    FROM user_tables WHERE table_name = 'RAG_KB_CHUNK_SET_BINDINGS';

    IF v_table_count = 0 THEN
        EXECUTE IMMEDIATE
            'CREATE TABLE rag_kb_chunk_set_bindings ('
            || 'knowledge_base_id VARCHAR2(64) NOT NULL,'
            || 'document_id VARCHAR2(64) NOT NULL,'
            || 'chunk_set_id VARCHAR2(64) NOT NULL,'
            || 'tenant_id_hash CHAR(64),'
            || 'is_serving NUMBER(1) DEFAULT 1 NOT NULL,'
            || 'created_at TIMESTAMP WITH TIME ZONE DEFAULT SYSTIMESTAMP NOT NULL,'
            || 'CONSTRAINT rag_kb_chunk_set_bindings_pk '
            || 'PRIMARY KEY (knowledge_base_id, document_id, chunk_set_id),'
            || 'CONSTRAINT rag_kb_cs_bind_cs_fk FOREIGN KEY (chunk_set_id) '
            || 'REFERENCES rag_chunk_sets (chunk_set_id) ON DELETE CASCADE,'
            || 'CONSTRAINT rag_kb_cs_bind_serving_ck CHECK (is_serving IN (0, 1))'
            || ')';
    END IF;

    SELECT COUNT(*) INTO v_index_count
    FROM user_indexes WHERE index_name = 'RAG_KB_CS_BIND_CS_IDX';

    IF v_index_count = 0 THEN
        EXECUTE IMMEDIATE
            'CREATE INDEX rag_kb_cs_bind_cs_idx ON rag_kb_chunk_set_bindings (chunk_set_id)';
    END IF;

    SELECT COUNT(*) INTO v_col_count
    FROM user_tab_columns
    WHERE table_name = 'RAG_CHUNKS' AND column_name = 'CHUNK_SET_ID';

    IF v_col_count = 0 THEN
        EXECUTE IMMEDIATE 'ALTER TABLE rag_chunks ADD (chunk_set_id VARCHAR2(64))';
    END IF;

    SELECT COUNT(*) INTO v_index_count
    FROM user_indexes WHERE index_name = 'RAG_CHUNKS_CHUNK_SET_IDX';

    IF v_index_count = 0 THEN
        EXECUTE IMMEDIATE
            'CREATE INDEX rag_chunks_chunk_set_idx ON rag_chunks (chunk_set_id, chunk_index)';
    END IF;
END;
/

-- migration: 20260621_002_document_extractions
DECLARE
    v_table_count NUMBER;
    v_index_count NUMBER;
    v_col_count   NUMBER;
BEGIN
    SELECT COUNT(*) INTO v_table_count
    FROM user_tables WHERE table_name = 'RAG_DOCUMENT_EXTRACTIONS';

    IF v_table_count = 0 THEN
        EXECUTE IMMEDIATE
            'CREATE TABLE rag_document_extractions ('
            || 'extraction_id VARCHAR2(64) PRIMARY KEY,'
            || 'document_id VARCHAR2(64) NOT NULL,'
            || 'tenant_id_hash CHAR(64),'
            || 'recipe_subset JSON,'
            || 'extraction_json JSON,'
            || 'status VARCHAR2(32) DEFAULT ''EXTRACTING'' NOT NULL,'
            || 'quality_json JSON,'
            || 'created_at TIMESTAMP WITH TIME ZONE DEFAULT SYSTIMESTAMP NOT NULL,'
            || 'updated_at TIMESTAMP WITH TIME ZONE DEFAULT SYSTIMESTAMP NOT NULL,'
            || 'CONSTRAINT rag_document_extractions_document_fk FOREIGN KEY (document_id) '
            || 'REFERENCES rag_documents (document_id) ON DELETE CASCADE,'
            || 'CONSTRAINT rag_document_extractions_status_ck CHECK '
            || '(status IN (''EXTRACTING'', ''EXTRACTED'', ''ERROR''))'
            || ')';
    END IF;

    SELECT COUNT(*) INTO v_index_count
    FROM user_indexes WHERE index_name = 'RAG_DOCUMENT_EXTRACTIONS_DOCUMENT_IDX';

    IF v_index_count = 0 THEN
        EXECUTE IMMEDIATE
            'CREATE INDEX rag_document_extractions_document_idx '
            || 'ON rag_document_extractions (document_id, status)';
    END IF;

    SELECT COUNT(*) INTO v_col_count
    FROM user_tab_columns
    WHERE table_name = 'RAG_CHUNK_SETS' AND column_name = 'EXTRACTION_ID';

    IF v_col_count = 0 THEN
        EXECUTE IMMEDIATE 'ALTER TABLE rag_chunk_sets ADD (extraction_id VARCHAR2(64))';
    END IF;

    SELECT COUNT(*) INTO v_index_count
    FROM user_indexes WHERE index_name = 'RAG_CHUNK_SETS_EXTRACTION_IDX';

    IF v_index_count = 0 THEN
        EXECUTE IMMEDIATE
            'CREATE INDEX rag_chunk_sets_extraction_idx ON rag_chunk_sets (extraction_id)';
    END IF;
END;
/

-- migration: 20260623_001_nullable_chunk_embeddings
DECLARE
    v_nullable VARCHAR2(1);
BEGIN
    SELECT nullable INTO v_nullable
    FROM user_tab_columns
    WHERE table_name = 'RAG_CHUNKS'
      AND column_name = 'EMBEDDING';

    IF v_nullable = 'N' THEN
        EXECUTE IMMEDIATE 'ALTER TABLE rag_chunks MODIFY (embedding NULL)';
    END IF;
EXCEPTION
    WHEN NO_DATA_FOUND THEN
        NULL;
END;
/

-- migration: 20260625_001_chunks_text_world_lexer
DECLARE
    v_count NUMBER;
BEGIN
    SELECT COUNT(*) INTO v_count
    FROM ctx_user_preferences
    WHERE pre_name = 'RAG_TEXT_WORLD_LEXER'
      AND pre_class = 'LEXER';

    IF v_count = 0 THEN
        CTX_DDL.CREATE_PREFERENCE('RAG_TEXT_WORLD_LEXER', 'WORLD_LEXER');
    END IF;
END;
/

DECLARE
    v_count NUMBER;
    PROCEDURE add_stopword(p_word VARCHAR2) IS
    BEGIN
        CTX_DDL.ADD_STOPWORD('RAG_TEXT_STOPLIST', p_word);
    EXCEPTION
        WHEN OTHERS THEN
            NULL;
    END;
BEGIN
    SELECT COUNT(*) INTO v_count
    FROM ctx_user_stoplists
    WHERE spl_name = 'RAG_TEXT_STOPLIST';

    IF v_count = 0 THEN
        CTX_DDL.CREATE_STOPLIST('RAG_TEXT_STOPLIST', 'BASIC_STOPLIST');
    END IF;

    add_stopword('の');
    add_stopword('は');
    add_stopword('が');
    add_stopword('を');
    add_stopword('に');
    add_stopword('へ');
    add_stopword('で');
    add_stopword('と');
    add_stopword('も');
    add_stopword('か');
    add_stopword('です');
    add_stopword('ます');
    add_stopword('なん');
    add_stopword('んで');
END;
/

DECLARE
    v_table_count NUMBER;
    v_index_count NUMBER;
    v_target_count NUMBER;
BEGIN
    SELECT COUNT(*) INTO v_table_count
    FROM user_tables
    WHERE table_name = 'RAG_CHUNKS';

    IF v_table_count > 0 THEN
        SELECT COUNT(*) INTO v_index_count
        FROM user_indexes
        WHERE index_name = 'RAG_CHUNKS_TEXT_IDX';

        IF v_index_count > 0 THEN
            SELECT COUNT(*) INTO v_target_count
            FROM ctx_user_index_objects o
            JOIN ctx_user_indexes i
              ON i.idx_name = o.ixo_index_name
            WHERE o.ixo_index_name = 'RAG_CHUNKS_TEXT_IDX'
              AND o.ixo_class = 'LEXER'
              AND o.ixo_object = 'WORLD_LEXER'
              AND i.idx_sync_type = 'ON COMMIT';

            IF v_target_count = 0 THEN
                EXECUTE IMMEDIATE 'DROP INDEX rag_chunks_text_idx';
                v_index_count := 0;
            END IF;
        END IF;

        IF v_index_count = 0 THEN
            EXECUTE IMMEDIATE
                'CREATE INDEX rag_chunks_text_idx '
                || 'ON rag_chunks (chunk_text) '
                || 'INDEXTYPE IS CTXSYS.CONTEXT '
                || 'PARAMETERS (''LEXER RAG_TEXT_WORLD_LEXER STOPLIST RAG_TEXT_STOPLIST SYNC (ON COMMIT)'')';
        END IF;
    END IF;
END;
/

-- migration: 20260625_002_preprocess_artifact
DECLARE
    v_column_count NUMBER;
    v_constraint_count NUMBER;
BEGIN
    SELECT COUNT(*)
    INTO v_column_count
    FROM user_tab_columns
    WHERE table_name = 'RAG_DOCUMENTS'
      AND column_name = 'PREPROCESS_ARTIFACT';

    IF v_column_count = 0 THEN
        EXECUTE IMMEDIATE
            'ALTER TABLE rag_documents ADD (preprocess_artifact JSON)';
    END IF;

    SELECT COUNT(*)
    INTO v_constraint_count
    FROM user_constraints
    WHERE table_name = 'RAG_DOCUMENTS'
      AND constraint_name = 'RAG_DOCUMENTS_STATUS_CK';

    IF v_constraint_count > 0 THEN
        EXECUTE IMMEDIATE
            'ALTER TABLE rag_documents DROP CONSTRAINT rag_documents_status_ck';
    END IF;

    EXECUTE IMMEDIATE
        'ALTER TABLE rag_documents ADD CONSTRAINT '
        || 'rag_documents_status_ck CHECK '
        || '(status IN (''UPLOADED'', ''PREPROCESSING'', ''PREPROCESSED'', '
        || '''INGESTING'', ''REVIEW'', ''CHUNKING'', ''CHUNKED'', ''INDEXING'', '
        || '''INDEXED'', ''ERROR''))';
END;
/

-- migration: 20260627_001_documents_preprocessed_status
DECLARE
    v_constraint_count NUMBER;
BEGIN
    SELECT COUNT(*)
    INTO v_constraint_count
    FROM user_constraints
    WHERE table_name = 'RAG_DOCUMENTS'
      AND constraint_name = 'RAG_DOCUMENTS_STATUS_CK';

    IF v_constraint_count > 0 THEN
        EXECUTE IMMEDIATE
            'ALTER TABLE rag_documents DROP CONSTRAINT rag_documents_status_ck';
    END IF;

    EXECUTE IMMEDIATE
        'ALTER TABLE rag_documents ADD CONSTRAINT '
        || 'rag_documents_status_ck CHECK '
        || '(status IN (''UPLOADED'', ''PREPROCESSING'', ''PREPROCESSED'', '
        || '''INGESTING'', ''REVIEW'', ''CHUNKING'', ''CHUNKED'', ''INDEXING'', '
        || '''INDEXED'', ''ERROR''))';
END;
/

-- migration: 20260629_001_chunk_sets_serving
DECLARE
    v_col_count   NUMBER;
    v_constraint_count NUMBER;
    v_index_count NUMBER;
BEGIN
    SELECT COUNT(*) INTO v_col_count
    FROM user_tab_columns
    WHERE table_name = 'RAG_CHUNK_SETS' AND column_name = 'IS_SERVING';

    IF v_col_count = 0 THEN
        EXECUTE IMMEDIATE
            'ALTER TABLE rag_chunk_sets ADD (is_serving NUMBER(1) DEFAULT 1 NOT NULL)';
        -- backfill は列追加直後の一度だけ。動的 SQL(EXECUTE IMMEDIATE)にしないと新列を
        -- 静的参照できず PL/SQL コンパイルに失敗する。同一文書で別 chunk_set が serving
        -- binding を持ち自分は持たない chunk_set だけ 0、それ以外は既定 1(配信を残す安全側)。
        EXECUTE IMMEDIATE
            'UPDATE rag_chunk_sets cs SET is_serving = 0 '
            || 'WHERE EXISTS (SELECT 1 FROM rag_kb_chunk_set_bindings b '
            || 'WHERE b.document_id = cs.document_id AND b.is_serving = 1 '
            || 'AND b.chunk_set_id <> cs.chunk_set_id) '
            || 'AND NOT EXISTS (SELECT 1 FROM rag_kb_chunk_set_bindings b2 '
            || 'WHERE b2.chunk_set_id = cs.chunk_set_id AND b2.is_serving = 1)';
    END IF;

    SELECT COUNT(*) INTO v_constraint_count
    FROM user_constraints
    WHERE table_name = 'RAG_CHUNK_SETS'
      AND constraint_name = 'RAG_CHUNK_SETS_SERVING_CK';

    IF v_constraint_count = 0 THEN
        EXECUTE IMMEDIATE
            'ALTER TABLE rag_chunk_sets ADD CONSTRAINT '
            || 'rag_chunk_sets_serving_ck CHECK (is_serving IN (0, 1))';
    END IF;

    SELECT COUNT(*) INTO v_index_count
    FROM user_indexes WHERE index_name = 'RAG_CHUNK_SETS_SERVING_IDX';

    IF v_index_count = 0 THEN
        EXECUTE IMMEDIATE
            'CREATE INDEX rag_chunk_sets_serving_idx '
            || 'ON rag_chunk_sets (document_id, is_serving)';
    END IF;
END;
/

-- migration: 20260629_002_drop_kb_chunk_set_bindings
DECLARE
    v_table_count NUMBER;
BEGIN
    SELECT COUNT(*) INTO v_table_count
    FROM user_tables WHERE table_name = 'RAG_KB_CHUNK_SET_BINDINGS';

    IF v_table_count > 0 THEN
        EXECUTE IMMEDIATE 'DROP TABLE rag_kb_chunk_set_bindings';
    END IF;
END;
/

-- migration: 20260629_003_ingestion_jobs_settings_overrides
DECLARE
    v_column_count NUMBER;
BEGIN
    SELECT COUNT(*) INTO v_column_count
    FROM user_tab_columns
    WHERE table_name = 'RAG_INGESTION_JOBS'
      AND column_name = 'SETTINGS_OVERRIDES';

    IF v_column_count = 0 THEN
        EXECUTE IMMEDIATE 'ALTER TABLE rag_ingestion_jobs ADD (settings_overrides JSON)';
    END IF;
END;
/

-- migration: 20260629_004_documents_processing_config
DECLARE
    v_column_count NUMBER;
BEGIN
    SELECT COUNT(*) INTO v_column_count
    FROM user_tab_columns
    WHERE table_name = 'RAG_DOCUMENTS'
      AND column_name = 'PROCESSING_CONFIG';

    IF v_column_count = 0 THEN
        EXECUTE IMMEDIATE 'ALTER TABLE rag_documents ADD (processing_config JSON)';
    END IF;
END;
/

-- migration: 20260630_001_default_knowledge_base_name
BEGIN
    UPDATE rag_knowledge_bases current_default
    SET
        name = 'DEFAULT-' || current_default.knowledge_base_id,
        updated_at = SYSTIMESTAMP
    WHERE LOWER(current_default.name) = 'default'
      AND EXISTS (
          SELECT 1
          FROM rag_knowledge_bases legacy_default
          WHERE legacy_default.name = '既定ナレッジベース'
            AND NVL(legacy_default.tenant_id_hash, '__GLOBAL__') =
                NVL(current_default.tenant_id_hash, '__GLOBAL__')
      );

    UPDATE rag_knowledge_bases
    SET
        name = 'DEFAULT',
        updated_at = SYSTIMESTAMP
    WHERE name = '既定ナレッジベース';
END;
/
COMMIT;
