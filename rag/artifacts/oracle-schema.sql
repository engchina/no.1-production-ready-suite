-- section: system_schema_control
CREATE TABLE rag_schema_operations (
    operation_key    VARCHAR2(64) PRIMARY KEY,
    status           VARCHAR2(16) DEFAULT 'IDLE' NOT NULL,
    operation_kind   VARCHAR2(16),
    lease_owner      VARCHAR2(64),
    lease_expires_at TIMESTAMP WITH TIME ZONE,
    last_error_code  VARCHAR2(64),
    schema_epoch     NUMBER(19) DEFAULT 0 NOT NULL,
    updated_at       TIMESTAMP WITH TIME ZONE DEFAULT SYSTIMESTAMP NOT NULL,
    CONSTRAINT rag_schema_operations_status_ck
        CHECK (status IN ('IDLE', 'RUNNING', 'FAILED')),
    CONSTRAINT rag_schema_operations_kind_ck
        CHECK (operation_kind IS NULL OR operation_kind IN ('INITIALIZE', 'RECREATE'))
);

CREATE TABLE rag_schema_migrations (
    migration_name VARCHAR2(64) PRIMARY KEY,
    description    VARCHAR2(512) NOT NULL,
    checksum       CHAR(64) NOT NULL,
    applied_at     TIMESTAMP WITH TIME ZONE DEFAULT SYSTIMESTAMP NOT NULL
);

MERGE INTO rag_schema_operations target
USING (
    SELECT 'system_schema' AS operation_key
    FROM dual
) source
ON (target.operation_key = source.operation_key)
WHEN NOT MATCHED THEN INSERT (
    operation_key,
    status,
    schema_epoch,
    updated_at
) VALUES (
    source.operation_key,
    'IDLE',
    0,
    SYSTIMESTAMP
);

-- section: language_pack_releases
CREATE TABLE rag_language_pack_releases (
    release_id VARCHAR2(64) PRIMARY KEY,
    version NUMBER(19) NOT NULL UNIQUE,
    effective_json JSON NOT NULL,
    content_hash VARCHAR2(64) NOT NULL,
    ingest_hash VARCHAR2(64) NOT NULL,
    published_at TIMESTAMP WITH TIME ZONE NOT NULL,
    published_by VARCHAR2(256),
    previous_id VARCHAR2(64),
    restored_from VARCHAR2(64),
    change_note VARCHAR2(2000),
    CONSTRAINT rag_lang_release_version_ck CHECK (version >= 1),
    CONSTRAINT rag_lang_release_previous_fk FOREIGN KEY (previous_id)
        REFERENCES rag_language_pack_releases (release_id),
    CONSTRAINT rag_lang_release_restore_fk FOREIGN KEY (restored_from)
        REFERENCES rag_language_pack_releases (release_id)
);
CREATE TABLE rag_language_pack_state (
    singleton_id NUMBER(1) PRIMARY KEY,
    draft_revision NUMBER(19) NOT NULL,
    draft_effective_json JSON NOT NULL,
    current_release_id VARCHAR2(64),
    CONSTRAINT rag_lang_state_singleton_ck CHECK (singleton_id = 1),
    CONSTRAINT rag_lang_state_revision_ck CHECK (draft_revision >= 1),
    CONSTRAINT rag_lang_state_current_fk FOREIGN KEY (current_release_id)
        REFERENCES rag_language_pack_releases (release_id)
);
CREATE INDEX rag_lang_release_previous_idx ON rag_language_pack_releases (previous_id);
CREATE INDEX rag_lang_release_restore_idx ON rag_language_pack_releases (restored_from);
CREATE INDEX rag_lang_state_current_idx ON rag_language_pack_state (current_release_id);

-- section: documents
CREATE TABLE rag_documents (
    document_id              VARCHAR2(64) PRIMARY KEY,
    file_name                VARCHAR2(512) NOT NULL,
    status                   VARCHAR2(32) NOT NULL,
    tenant_id_hash           CHAR(64),
    category_name            VARCHAR2(256),
    object_storage_path      VARCHAR2(1024),
    classification           JSON,
    content_type             VARCHAR2(255),
    file_size_bytes          NUMBER(19),
    content_sha256           CHAR(64),
    duplicate_of_document_id VARCHAR2(64),
    error_message            VARCHAR2(2000),
    uploaded_at              TIMESTAMP WITH TIME ZONE DEFAULT SYSTIMESTAMP NOT NULL,
    indexed_at               TIMESTAMP WITH TIME ZONE,
    -- 文書の版(#1248)。この文書を置き換えた新しい版の文書と、置き換えた日時。NULL は今有効な版。
    -- 自己参照の FK は持たず、存在・自己参照・循環はアプリで検証する。
    superseded_by_document_id VARCHAR2(64),
    superseded_at            TIMESTAMP WITH TIME ZONE,
    CONSTRAINT rag_documents_status_ck
        CHECK (status IN (
            'UPLOADED', 'PREPROCESSING', 'PREPROCESSED', 'INGESTING', 'REVIEW',
            'CHUNKING', 'CHUNKED', 'INDEXING', 'INDEXED', 'ERROR'
        )),
    CONSTRAINT rag_documents_duplicate_fk
        FOREIGN KEY (duplicate_of_document_id) REFERENCES rag_documents (document_id)
);

CREATE INDEX rag_documents_duplicate_idx ON rag_documents (duplicate_of_document_id);

CREATE INDEX rag_documents_content_sha256_idx
    ON rag_documents (content_sha256);

CREATE INDEX rag_documents_status_uploaded_idx
    ON rag_documents (status, uploaded_at DESC);

CREATE INDEX rag_documents_tenant_status_uploaded_idx
    ON rag_documents (tenant_id_hash, status, uploaded_at DESC);

-- section: document_recipes
CREATE TABLE rag_document_recipes (
    recipe_id                   VARCHAR2(64) PRIMARY KEY,
    document_id                 VARCHAR2(64) NOT NULL,
    slot_no                     NUMBER(1) NOT NULL,
    tenant_id_hash              CHAR(64),
    processing_config           JSON,
    status                      VARCHAR2(32) DEFAULT 'UPLOADED' NOT NULL,
    failed_phase                VARCHAR2(16),
    preprocess_artifact         JSON,
    active_extraction_recipe_id VARCHAR2(64),
    config_revision             NUMBER(10) DEFAULT 1 NOT NULL,
    materialized_revision       NUMBER(10),
    error_message               VARCHAR2(2000),
    started_at                  TIMESTAMP WITH TIME ZONE,
    finished_at                 TIMESTAMP WITH TIME ZONE,
    created_at                  TIMESTAMP WITH TIME ZONE DEFAULT SYSTIMESTAMP NOT NULL,
    updated_at                  TIMESTAMP WITH TIME ZONE DEFAULT SYSTIMESTAMP NOT NULL,
    CONSTRAINT rag_document_recipes_document_fk
        FOREIGN KEY (document_id) REFERENCES rag_documents (document_id) ON DELETE CASCADE,
    CONSTRAINT rag_document_recipes_slot_ck CHECK (slot_no BETWEEN 1 AND 3),
    CONSTRAINT rag_document_recipes_status_ck CHECK (
        status IN ('UPLOADED', 'PREPROCESSING', 'PREPROCESSED', 'INGESTING', 'REVIEW',
                   'CHUNKING', 'CHUNKED', 'INDEXING', 'INDEXED', 'ERROR')
    ),
    CONSTRAINT rag_document_recipes_phase_ck CHECK (
        failed_phase IS NULL OR failed_phase IN ('PREPROCESS', 'EXTRACT', 'CHUNK', 'INDEX')
    ),
    CONSTRAINT rag_document_recipes_revision_ck CHECK (
        config_revision >= 1
        AND (materialized_revision IS NULL OR materialized_revision >= 1)
    ),
    CONSTRAINT rag_document_recipes_slot_uq UNIQUE (document_id, slot_no)
);

CREATE INDEX rag_document_recipes_status_idx
    ON rag_document_recipes (tenant_id_hash, status, updated_at DESC);

-- section: knowledge_bases
CREATE TABLE rag_knowledge_bases (
    knowledge_base_id     VARCHAR2(64) PRIMARY KEY,
    tenant_id_hash        CHAR(64),
    name                  VARCHAR2(256) NOT NULL,
    description           VARCHAR2(2000),
    status                VARCHAR2(32) DEFAULT 'ACTIVE' NOT NULL,
    default_search_mode   VARCHAR2(16) DEFAULT 'hybrid' NOT NULL,
    retrieval_config      JSON,
    wiki_build_actor_uuid VARCHAR2(64),
    extraction_fields     JSON,
    created_at            TIMESTAMP WITH TIME ZONE DEFAULT SYSTIMESTAMP NOT NULL,
    updated_at            TIMESTAMP WITH TIME ZONE DEFAULT SYSTIMESTAMP NOT NULL,
    archived_at           TIMESTAMP WITH TIME ZONE,
    CONSTRAINT rag_knowledge_bases_status_ck
        CHECK (status IN ('ACTIVE', 'ARCHIVED')),
    CONSTRAINT rag_knowledge_bases_mode_ck
        CHECK (default_search_mode IN ('hybrid', 'vector', 'keyword'))
);

CREATE UNIQUE INDEX rag_knowledge_bases_tenant_name_uidx
    ON rag_knowledge_bases (
        NVL(tenant_id_hash, '__GLOBAL__'),
        LOWER(name)
    );

CREATE INDEX rag_knowledge_bases_tenant_status_idx
    ON rag_knowledge_bases (tenant_id_hash, status, updated_at DESC);

CREATE TABLE rag_document_knowledge_bases (
    knowledge_base_id        VARCHAR2(64) NOT NULL,
    document_id              VARCHAR2(64) NOT NULL,
    tenant_id_hash           CHAR(64),
    assigned_at              TIMESTAMP WITH TIME ZONE DEFAULT SYSTIMESTAMP NOT NULL,
    assigned_by_user_id_hash CHAR(64),
    PRIMARY KEY (knowledge_base_id, document_id),
    CONSTRAINT rag_document_knowledge_bases_kb_fk
        FOREIGN KEY (knowledge_base_id)
        REFERENCES rag_knowledge_bases (knowledge_base_id)
        ON DELETE CASCADE,
    CONSTRAINT rag_document_knowledge_bases_doc_fk
        FOREIGN KEY (document_id)
        REFERENCES rag_documents (document_id)
        ON DELETE CASCADE
);

CREATE INDEX rag_document_knowledge_bases_document_idx
    ON rag_document_knowledge_bases (document_id, knowledge_base_id);

CREATE INDEX rag_document_knowledge_bases_tenant_kb_idx
    ON rag_document_knowledge_bases (tenant_id_hash, knowledge_base_id, assigned_at DESC);

-- section: structure_index_jobs
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

-- section: search_answer_profiles
CREATE TABLE rag_search_answer_profiles (
    search_answer_profile_id   VARCHAR2(64) PRIMARY KEY,
    tenant_id_hash     CHAR(64),
    name               VARCHAR2(256) NOT NULL,
    description        VARCHAR2(2000),
    status             VARCHAR2(32) DEFAULT 'ACTIVE' NOT NULL,
    profile_config        JSON,
    created_at         TIMESTAMP WITH TIME ZONE DEFAULT SYSTIMESTAMP NOT NULL,
    updated_at         TIMESTAMP WITH TIME ZONE DEFAULT SYSTIMESTAMP NOT NULL,
    archived_at        TIMESTAMP WITH TIME ZONE,
    CONSTRAINT rag_search_answer_profiles_status_ck
        CHECK (status IN ('ACTIVE', 'ARCHIVED'))
);

CREATE UNIQUE INDEX rag_search_answer_profiles_tenant_name_uidx
    ON rag_search_answer_profiles (
        NVL(tenant_id_hash, '__GLOBAL__'),
        LOWER(name)
    );

CREATE INDEX rag_search_answer_profiles_tenant_status_idx
    ON rag_search_answer_profiles (tenant_id_hash, status, updated_at DESC);

-- section: answer_records
CREATE TABLE rag_answer_records (
    trace_id            VARCHAR2(64) PRIMARY KEY,
    search_answer_profile_id    VARCHAR2(64),
    surface             VARCHAR2(16) NOT NULL,
    answer_engine       VARCHAR2(32) NOT NULL,
    question            CLOB NOT NULL,
    rewritten_question  CLOB,
    answer              CLOB NOT NULL,
    citations_json      JSON NOT NULL,
    diagnostics_json    JSON NOT NULL,
    evaluation_input_json JSON,
    evaluation_json     JSON,
    answer_notice       CLOB,
    user_id_hash        CHAR(64),
    created_at          TIMESTAMP WITH TIME ZONE DEFAULT SYSTIMESTAMP NOT NULL,
    CONSTRAINT rag_answer_records_surface_ck CHECK (surface IN ('search', 'chat'))
);

CREATE INDEX rag_answer_records_profile_idx
    ON rag_answer_records (search_answer_profile_id, created_at DESC);

CREATE INDEX rag_answer_records_owner_idx
    ON rag_answer_records (user_id_hash, search_answer_profile_id, created_at DESC);

-- section: answer_prompts
CREATE TABLE rag_answer_prompts (
    prompt_key  VARCHAR2(64) PRIMARY KEY,
    content     CLOB NOT NULL,
    updated_at  TIMESTAMP WITH TIME ZONE DEFAULT SYSTIMESTAMP NOT NULL
)

-- section: run_history
CREATE TABLE rag_run_history (
    run_id                    VARCHAR2(64) PRIMARY KEY,
    surface                   VARCHAR2(16) NOT NULL,
    search_answer_profile_id  VARCHAR2(64),
    tenant_id_hash            CHAR(64),
    user_id_hash              CHAR(64),
    user_uuid                 VARCHAR2(256),
    question                  CLOB,
    answer_generated          NUMBER(1) DEFAULT 0 NOT NULL,
    status                    VARCHAR2(16) NOT NULL,
    answer_outcome            VARCHAR2(32),
    verification              VARCHAR2(16),
    confidence                VARCHAR2(16),
    needs_human_review        NUMBER(1),
    citation_count            NUMBER(10) DEFAULT 0 NOT NULL,
    elapsed_ms                NUMBER(12, 3),
    error_stage               VARCHAR2(128),
    error_type                VARCHAR2(128),
    model_id                  VARCHAR2(256),
    conversation_id           VARCHAR2(64),
    message_id                VARCHAR2(64),
    stages_json               JSON,
    citations_json            JSON,
    created_at                TIMESTAMP WITH TIME ZONE DEFAULT SYSTIMESTAMP NOT NULL,
    CONSTRAINT rag_run_history_surface_ck CHECK (surface IN ('search', 'chat')),
    CONSTRAINT rag_run_history_status_ck CHECK (status IN ('success', 'blocked', 'error')),
    CONSTRAINT rag_run_history_verification_ck
        CHECK (verification IS NULL OR verification IN ('verified', 'partial', 'unverified'))
);

CREATE INDEX rag_run_history_created_idx
    ON rag_run_history (created_at DESC, run_id);

CREATE INDEX rag_run_history_owner_idx
    ON rag_run_history (user_id_hash, created_at DESC);

CREATE INDEX rag_run_history_conversation_idx
    ON rag_run_history (conversation_id);

-- section: document_categories
CREATE TABLE rag_document_categories (
    category_id     VARCHAR2(64) PRIMARY KEY,
    parent_id       VARCHAR2(64),
    category_level  NUMBER(1) NOT NULL,
    name            VARCHAR2(200 CHAR) NOT NULL,
    sort_order      NUMBER(10) DEFAULT 0 NOT NULL,
    created_at      TIMESTAMP WITH TIME ZONE DEFAULT SYSTIMESTAMP NOT NULL,
    updated_at      TIMESTAMP WITH TIME ZONE DEFAULT SYSTIMESTAMP NOT NULL,
    CONSTRAINT rag_document_categories_level_ck CHECK (category_level BETWEEN 1 AND 3),
    CONSTRAINT rag_document_categories_parent_fk FOREIGN KEY (parent_id) REFERENCES rag_document_categories (category_id)
);

CREATE UNIQUE INDEX rag_document_categories_name_ux
    ON rag_document_categories (NVL(parent_id, '-'), name);

CREATE INDEX rag_document_categories_parent_idx
    ON rag_document_categories (parent_id, sort_order);

-- section: document_cleanups
CREATE TABLE rag_document_cleanups (
    cleanup_id      VARCHAR2(64) PRIMARY KEY,
    document_id     VARCHAR2(64) NOT NULL,
    file_name       VARCHAR2(512) NOT NULL,
    tenant_id_hash  CHAR(64),
    user_id_hash    CHAR(64),
    targets         JSON NOT NULL,
    attempt_count   NUMBER(10) DEFAULT 0 NOT NULL,
    last_error_type VARCHAR2(128),
    created_at      TIMESTAMP WITH TIME ZONE DEFAULT SYSTIMESTAMP NOT NULL,
    updated_at      TIMESTAMP WITH TIME ZONE DEFAULT SYSTIMESTAMP NOT NULL
);

CREATE INDEX rag_document_cleanups_document_idx
    ON rag_document_cleanups (document_id);

CREATE INDEX rag_document_cleanups_updated_idx
    ON rag_document_cleanups (updated_at);

-- section: search_answer_profile_knowledge
CREATE TABLE rag_search_answer_profile_knowledge (
    search_answer_profile_id  VARCHAR2(64) NOT NULL,
    kind              VARCHAR2(32) NOT NULL,
    payload_json      JSON NOT NULL,
    revision          NUMBER(19) DEFAULT 1 NOT NULL,
    updated_at        TIMESTAMP WITH TIME ZONE DEFAULT SYSTIMESTAMP NOT NULL,
    CONSTRAINT rag_search_answer_profile_knowledge_pk PRIMARY KEY (search_answer_profile_id, kind),
    CONSTRAINT rag_search_answer_profile_knowledge_kind_ck CHECK (
        kind IN ('domain_keywords', 'approved_faq', 'runtime_knowledge')
    )
);

-- section: support_guides
CREATE TABLE rag_support_guides (
    guide_id                  VARCHAR2(64) PRIMARY KEY,
    search_answer_profile_id  VARCHAR2(64) NOT NULL,
    status                    VARCHAR2(16) DEFAULT 'active' NOT NULL,
    title                     VARCHAR2(400) NOT NULL,
    draft_json                JSON NOT NULL,
    draft_sha256              CHAR(64) NOT NULL,
    draft_revision            NUMBER(19) DEFAULT 1 NOT NULL,
    published_revision        NUMBER(19),
    published_sha256          CHAR(64),
    created_at                TIMESTAMP WITH TIME ZONE DEFAULT SYSTIMESTAMP NOT NULL,
    updated_at                TIMESTAMP WITH TIME ZONE DEFAULT SYSTIMESTAMP NOT NULL,
    updated_by                VARCHAR2(256),
    CONSTRAINT rag_support_guides_status_ck CHECK (status IN ('active', 'archived'))
);
CREATE INDEX rag_support_guides_profile_idx ON rag_support_guides (search_answer_profile_id, status);

-- section: support_guide_revisions
CREATE TABLE rag_support_guide_revisions (
    guide_id        VARCHAR2(64) NOT NULL,
    revision        NUMBER(19) NOT NULL,
    title           VARCHAR2(400) NOT NULL,
    content_json    JSON NOT NULL,
    content_sha256  CHAR(64) NOT NULL,
    published_at    TIMESTAMP WITH TIME ZONE DEFAULT SYSTIMESTAMP NOT NULL,
    published_by    VARCHAR2(256),
    rollback_from   NUMBER(19),
    CONSTRAINT rag_support_guide_revisions_pk PRIMARY KEY (guide_id, revision),
    CONSTRAINT rag_support_guide_revisions_guide_fk FOREIGN KEY (guide_id)
        REFERENCES rag_support_guides (guide_id) ON DELETE CASCADE
);

-- section: support_guide_embeddings
CREATE TABLE rag_support_guide_embeddings (
    guide_id         VARCHAR2(64) NOT NULL,
    revision         NUMBER(19) NOT NULL,
    model_id         VARCHAR2(256) NOT NULL,
    dimension        NUMBER(10) NOT NULL,
    examples_sha256  CHAR(64) NOT NULL,
    vectors_json     CLOB NOT NULL,
    created_at       TIMESTAMP WITH TIME ZONE DEFAULT SYSTIMESTAMP NOT NULL,
    CONSTRAINT rag_support_guide_embeddings_pk PRIMARY KEY (guide_id, revision),
    CONSTRAINT rag_support_guide_embeddings_rev_fk FOREIGN KEY (guide_id, revision)
        REFERENCES rag_support_guide_revisions (guide_id, revision) ON DELETE CASCADE
);

-- section: document_sections
CREATE TABLE rag_document_sections (
    document_id    VARCHAR2(64) NOT NULL,
    sections_json  JSON NOT NULL,
    revision       NUMBER(19) DEFAULT 1 NOT NULL,
    updated_at     TIMESTAMP WITH TIME ZONE DEFAULT SYSTIMESTAMP NOT NULL,
    CONSTRAINT rag_document_sections_pk PRIMARY KEY (document_id),
    CONSTRAINT rag_document_sections_document_fk FOREIGN KEY (document_id)
        REFERENCES rag_documents (document_id) ON DELETE CASCADE
);

-- section: document_section_revisions
CREATE TABLE rag_document_section_revisions (
    document_id                VARCHAR2(64) NOT NULL,
    revision_no                NUMBER(19) NOT NULL,
    parent_revision_no         NUMBER(19),
    restored_from_revision_no  NUMBER(19),
    revision_kind              VARCHAR2(16) NOT NULL,
    base_extraction_recipe_id  VARCHAR2(64),
    base_outline_sha256        VARCHAR2(64),
    changed_section_count      NUMBER(10) DEFAULT 0 NOT NULL,
    sections_json              JSON NOT NULL,
    created_at                 TIMESTAMP WITH TIME ZONE DEFAULT SYSTIMESTAMP NOT NULL,
    CONSTRAINT rag_doc_sec_rev_pk PRIMARY KEY (document_id, revision_no),
    CONSTRAINT rag_doc_sec_rev_document_fk FOREIGN KEY (document_id)
        REFERENCES rag_documents (document_id) ON DELETE CASCADE,
    CONSTRAINT rag_doc_sec_rev_kind_ck
        CHECK (revision_kind IN ('EXTRACTED', 'EDITED', 'RESTORED', 'RESET'))
);

-- section: conversations
CREATE TABLE rag_conversations (
    conversation_id    VARCHAR2(64) DEFAULT RAWTOHEX(SYS_GUID()) PRIMARY KEY,
    search_answer_profile_id   VARCHAR2(64) NOT NULL,
    tenant_id_hash     CHAR(64),
    user_id_hash       CHAR(64),
    title              VARCHAR2(400),
    status             VARCHAR2(16) DEFAULT 'ACTIVE' NOT NULL,
    message_count      NUMBER(10) DEFAULT 0 NOT NULL,
    created_at         TIMESTAMP WITH TIME ZONE DEFAULT SYSTIMESTAMP NOT NULL,
    updated_at         TIMESTAMP WITH TIME ZONE DEFAULT SYSTIMESTAMP NOT NULL,
    CONSTRAINT rag_conversations_status_ck
        CHECK (status IN ('ACTIVE', 'ARCHIVED')),
    CONSTRAINT rag_conversations_search_answer_profile_fk
        FOREIGN KEY (search_answer_profile_id)
        REFERENCES rag_search_answer_profiles (search_answer_profile_id)
        ON DELETE CASCADE
);

CREATE INDEX rag_conversations_tenant_profile_updated_idx
    ON rag_conversations (tenant_id_hash, search_answer_profile_id, updated_at DESC);

CREATE INDEX rag_conversations_search_answer_profile_idx
    ON rag_conversations (search_answer_profile_id);

-- section: messages
CREATE TABLE rag_messages (
    message_id           VARCHAR2(64) DEFAULT RAWTOHEX(SYS_GUID()) PRIMARY KEY,
    conversation_id      VARCHAR2(64) NOT NULL,
    reply_to_message_id  VARCHAR2(64),
    tenant_id_hash       CHAR(64),
    user_id_hash         CHAR(64),
    role                 VARCHAR2(16) NOT NULL,
    model                VARCHAR2(160),
    content              CLOB,
    citations_json       JSON,
    guardrail_warnings   JSON,
    trace_id             VARCHAR2(64),
    status               VARCHAR2(16) DEFAULT 'COMPLETE' NOT NULL,
    elapsed_ms           NUMBER(12, 3),
    progress_json        JSON,
    lease_owner          VARCHAR2(128),
    heartbeat_at         TIMESTAMP WITH TIME ZONE,
    clarification_json   CLOB,
    answer_notice        CLOB,
    attachments_json     CLOB,
    created_at           TIMESTAMP WITH TIME ZONE DEFAULT SYSTIMESTAMP NOT NULL,
    CONSTRAINT rag_messages_role_ck
        CHECK (role IN ('USER', 'ASSISTANT', 'SYSTEM')),
    CONSTRAINT rag_messages_status_ck
        CHECK (status IN ('STREAMING', 'COMPLETE', 'ERROR', 'CANCELLED')),
    CONSTRAINT rag_messages_conversation_fk
        FOREIGN KEY (conversation_id)
        REFERENCES rag_conversations (conversation_id)
        ON DELETE CASCADE
);

CREATE INDEX rag_messages_conversation_created_idx
    ON rag_messages (conversation_id, created_at);

CREATE INDEX rag_messages_tenant_created_idx
    ON rag_messages (tenant_id_hash, created_at DESC);

CREATE INDEX rag_messages_trace_idx
    ON rag_messages (trace_id);

CREATE INDEX rag_messages_reply_to_idx
    ON rag_messages (reply_to_message_id);

-- section: ingestion_jobs
CREATE TABLE rag_ingestion_jobs (
    job_id           VARCHAR2(64) PRIMARY KEY,
    document_id      VARCHAR2(64) NOT NULL,
    recipe_id        VARCHAR2(64),
    recipe_revision  NUMBER(10),
    language_pack_release_id VARCHAR2(64),
    tenant_id_hash   CHAR(64),
    status           VARCHAR2(32) NOT NULL,
    phase            VARCHAR2(16) DEFAULT 'PREPROCESS' NOT NULL,
    parser_profile   VARCHAR2(80) NOT NULL,
    quality_warnings JSON,
    settings_overrides JSON,
    skip_reason      VARCHAR2(256),
    error_message    VARCHAR2(2000),
    attempt_count    NUMBER(5) DEFAULT 0 NOT NULL,
    max_attempts     NUMBER(5) DEFAULT 3 NOT NULL,
    queued_at        TIMESTAMP WITH TIME ZONE DEFAULT SYSTIMESTAMP NOT NULL,
    started_at       TIMESTAMP WITH TIME ZONE,
    finished_at      TIMESTAMP WITH TIME ZONE,
    lease_owner      VARCHAR2(128),
    heartbeat_at     TIMESTAMP WITH TIME ZONE,
    progress_json    CLOB,
    CONSTRAINT rag_ingest_job_lang_release_fk
        FOREIGN KEY (language_pack_release_id) REFERENCES rag_language_pack_releases (release_id),
    CONSTRAINT rag_ingestion_jobs_status_ck
        CHECK (status IN ('QUEUED', 'RUNNING', 'SUCCEEDED', 'FAILED', 'SKIPPED', 'CANCELLED')),
    CONSTRAINT rag_ingestion_jobs_phase_ck
        CHECK (phase IN ('PREPROCESS', 'EXTRACT', 'CHUNK', 'INDEX')),
    CONSTRAINT rag_ingestion_jobs_attempts_ck
        CHECK (attempt_count >= 0 AND max_attempts >= 1),
    CONSTRAINT rag_ingestion_jobs_document_fk
        FOREIGN KEY (document_id)
        REFERENCES rag_documents (document_id)
        ON DELETE CASCADE,
    CONSTRAINT rag_ingestion_jobs_recipe_fk
        FOREIGN KEY (recipe_id)
        REFERENCES rag_document_recipes (recipe_id)
        ON DELETE CASCADE
);

CREATE INDEX rag_ingestion_jobs_tenant_queued_idx
    ON rag_ingestion_jobs (tenant_id_hash, status, queued_at DESC);

CREATE INDEX rag_ingestion_jobs_document_idx
    ON rag_ingestion_jobs (document_id, queued_at DESC);

CREATE INDEX rag_ingestion_jobs_recipe_idx
    ON rag_ingestion_jobs (recipe_id, status, queued_at DESC);

CREATE INDEX rag_ingestion_jobs_lease_idx
    ON rag_ingestion_jobs (lease_owner, status);

-- section: ingestion_segments
CREATE TABLE rag_ingestion_segments (
    segment_id     VARCHAR2(128) PRIMARY KEY,
    document_id    VARCHAR2(64) NOT NULL,
    recipe_id      VARCHAR2(64),
    tenant_id_hash CHAR(64),
    status         VARCHAR2(32) DEFAULT 'QUEUED' NOT NULL,
    parser_backend VARCHAR2(80) DEFAULT 'enterprise_ai' NOT NULL,
    parser_profile VARCHAR2(80) DEFAULT 'enterprise_ai_generic' NOT NULL,
    page_start     NUMBER(10),
    page_end       NUMBER(10),
    attempt_count  NUMBER(5) DEFAULT 0 NOT NULL,
    artifact_path  VARCHAR2(1024),
    error_code     VARCHAR2(128),
    error_message  VARCHAR2(2000),
    created_at     TIMESTAMP WITH TIME ZONE DEFAULT SYSTIMESTAMP NOT NULL,
    updated_at     TIMESTAMP WITH TIME ZONE DEFAULT SYSTIMESTAMP NOT NULL,
    CONSTRAINT rag_ingestion_segments_status_ck
        CHECK (status IN ('QUEUED', 'RUNNING', 'SUCCEEDED', 'FAILED', 'CANCELLED')),
    CONSTRAINT rag_ingestion_segments_attempts_ck
        CHECK (attempt_count >= 0),
    CONSTRAINT rag_ingestion_segments_page_range_ck
        CHECK (page_start IS NULL OR page_end IS NULL OR page_start <= page_end),
    CONSTRAINT rag_ingestion_segments_document_fk
        FOREIGN KEY (document_id)
        REFERENCES rag_documents (document_id)
        ON DELETE CASCADE,
    CONSTRAINT rag_ingestion_segments_recipe_fk
        FOREIGN KEY (recipe_id)
        REFERENCES rag_document_recipes (recipe_id)
        ON DELETE CASCADE
);

CREATE INDEX rag_ingestion_segments_document_status_idx
    ON rag_ingestion_segments (document_id, status, page_start, page_end);

CREATE INDEX rag_ingestion_segments_tenant_status_idx
    ON rag_ingestion_segments (tenant_id_hash, status, updated_at DESC);

CREATE INDEX rag_ingestion_segments_recipe_status_idx
    ON rag_ingestion_segments (recipe_id, status, updated_at DESC);

-- section: chunks
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

CREATE TABLE rag_chunks (
    chunk_id        VARCHAR2(128) PRIMARY KEY,
    document_id     VARCHAR2(64) NOT NULL,
    tenant_id_hash  CHAR(64),
    chunk_index     NUMBER NOT NULL,
    chunk_text      CLOB NOT NULL,
    search_text     CLOB NOT NULL,
    metadata_json   JSON,
    embedding       VECTOR(1536, FLOAT32),
    hybrid_payload  JSON,
    chunk_set_id    VARCHAR2(64),
    native_scope_id NUMBER,
    created_at      TIMESTAMP DEFAULT SYSTIMESTAMP
);

CREATE VECTOR INDEX rag_chunks_embedding_hnsw_idx
    ON rag_chunks (embedding)
    ORGANIZATION INMEMORY NEIGHBOR GRAPH
    DISTANCE COSINE
    WITH TARGET ACCURACY 98
    PARAMETERS (
        TYPE HNSW,
        NEIGHBORS 48,
        EFCONSTRUCTION 800
    );

CREATE INDEX rag_chunks_text_idx
    ON rag_chunks (search_text)
    INDEXTYPE IS CTXSYS.CONTEXT
    PARAMETERS ('LEXER RAG_TEXT_WORLD_LEXER STOPLIST RAG_TEXT_STOPLIST SYNC (ON COMMIT)');

CREATE INDEX rag_chunks_tenant_document_idx
    ON rag_chunks (tenant_id_hash, document_id, chunk_index);

CREATE INDEX rag_chunks_chunk_set_idx
    ON rag_chunks (chunk_set_id, chunk_index);

-- section: native_hybrid
DECLARE
    v_count NUMBER;
BEGIN
    SELECT COUNT(*) INTO v_count FROM ctx_user_preferences
    WHERE pre_name = 'RAG_CHUNKS_HYBRID_PREF';
    IF v_count = 0 THEN
        DBMS_VECTOR_CHAIN.CREATE_PREFERENCE(
            'RAG_CHUNKS_HYBRID_PREF', DBMS_VECTOR_CHAIN.VECTORIZER,
            JSON('{"vector_idxtype":"ivf","distance":"COSINE","paths":[{"type":"VECTOR","path_list":["$.embedding"]}]}')
        );
    END IF;
    SELECT COUNT(*) INTO v_count FROM user_tab_columns
    WHERE table_name = 'RAG_CHUNKS' AND column_name = 'HYBRID_PAYLOAD';
    IF v_count = 1 THEN
        EXECUTE IMMEDIATE q'~CREATE OR REPLACE TRIGGER RAG_CHUNKS_HYBRID_SYNC
BEFORE INSERT OR UPDATE ON rag_chunks FOR EACH ROW
BEGIN
    IF INSERTING OR UPDATING('SEARCH_TEXT') OR UPDATING('EMBEDDING') THEN
    SELECT JSON_OBJECT(
        'text' VALUE :NEW.search_text,
        'embedding' VALUE JSON(VECTOR_SERIALIZE(:NEW.embedding RETURNING CLOB))
        ABSENT ON NULL RETURNING JSON
    ) INTO :NEW.hybrid_payload FROM dual;
    END IF;
END;~';
    END IF;
END;
/

CREATE HYBRID VECTOR INDEX RAG_CHUNKS_HYBRID_IDX
    ON rag_chunks (hybrid_payload)
    PARAMETERS ('VECTORIZER RAG_CHUNKS_HYBRID_PREF
        LEXER RAG_TEXT_WORLD_LEXER STOPLIST RAG_TEXT_STOPLIST
        SEARCH_ON TEXT MAINTENANCE MANUAL SYNC (ON COMMIT)');

-- section: chunk_sets
CREATE TABLE rag_chunk_sets (
    chunk_set_id    VARCHAR2(64) PRIMARY KEY,
    document_id     VARCHAR2(64) NOT NULL,
    recipe_id       VARCHAR2(64),
    extraction_recipe_id VARCHAR2(64),
    language_pack_release_id VARCHAR2(64),
    language_pack_ingest_hash VARCHAR2(64),
    language_pack_job_id VARCHAR2(64),
    language_pack_built_at TIMESTAMP WITH TIME ZONE,
    tenant_id_hash  CHAR(64),
    recipe_subset   JSON,
    status          VARCHAR2(32) DEFAULT 'INGESTING' NOT NULL,
    chunk_count     NUMBER(10) DEFAULT 0 NOT NULL,
    vector_count    NUMBER(10) DEFAULT 0 NOT NULL,
    is_serving      NUMBER(1) DEFAULT 1 NOT NULL,
    is_active       NUMBER(1) DEFAULT 0 NOT NULL,
    retired_at      TIMESTAMP WITH TIME ZONE,
    metrics_json    JSON,
    first_page_context JSON,
    created_at      TIMESTAMP WITH TIME ZONE DEFAULT SYSTIMESTAMP NOT NULL,
    updated_at      TIMESTAMP WITH TIME ZONE DEFAULT SYSTIMESTAMP NOT NULL,
    CONSTRAINT rag_chunk_set_lang_release_fk FOREIGN KEY (language_pack_release_id)
        REFERENCES rag_language_pack_releases (release_id),
    CONSTRAINT rag_chunk_sets_document_fk
        FOREIGN KEY (document_id) REFERENCES rag_documents (document_id) ON DELETE CASCADE,
    CONSTRAINT rag_chunk_sets_recipe_fk
        FOREIGN KEY (recipe_id) REFERENCES rag_document_recipes (recipe_id) ON DELETE CASCADE,
    CONSTRAINT rag_chunk_sets_status_ck
        CHECK (status IN ('INGESTING', 'CHUNKED', 'INDEXED', 'ERROR')),
    CONSTRAINT rag_chunk_sets_serving_ck CHECK (is_serving IN (0, 1)),
    CONSTRAINT rag_chunk_sets_active_ck CHECK (is_active IN (0, 1))
);

CREATE INDEX rag_chunk_sets_document_idx
    ON rag_chunk_sets (document_id, status);

CREATE INDEX rag_chunk_sets_serving_idx
    ON rag_chunk_sets (document_id, is_serving);

CREATE INDEX rag_chunk_sets_recipe_idx
    ON rag_chunk_sets (recipe_id, status, updated_at DESC);

CREATE UNIQUE INDEX rag_chunk_sets_recipe_active_uidx
    ON rag_chunk_sets (CASE WHEN is_active = 1 THEN recipe_id END);

CREATE INDEX rag_chunk_sets_extraction_idx
    ON rag_chunk_sets (document_id, extraction_recipe_id);

CREATE INDEX rag_chunk_sets_retired_idx
    ON rag_chunk_sets (retired_at);

CREATE TABLE rag_document_extractions (
    document_id          VARCHAR2(64) NOT NULL,
    extraction_recipe_id VARCHAR2(64) NOT NULL,
    source_sha256        CHAR(64),
    tenant_id_hash       CHAR(64),
    recipe_subset        JSON,
    extraction_json      JSON,
    status               VARCHAR2(32) DEFAULT 'planned_only' NOT NULL,
    reason               VARCHAR2(2000),
    metrics_json         JSON,
    created_at           TIMESTAMP WITH TIME ZONE DEFAULT SYSTIMESTAMP NOT NULL,
    updated_at           TIMESTAMP WITH TIME ZONE DEFAULT SYSTIMESTAMP NOT NULL,
    CONSTRAINT rag_document_extractions_pk
        PRIMARY KEY (document_id, extraction_recipe_id),
    CONSTRAINT rag_doc_ext_document_fk
        FOREIGN KEY (document_id) REFERENCES rag_documents (document_id) ON DELETE CASCADE,
    CONSTRAINT rag_doc_ext_status_ck
        CHECK (
            status IN ('not_requested', 'planned_only', 'materialized', 'needs_reingest', 'error')
        )
);

CREATE INDEX rag_doc_ext_status_idx
    ON rag_document_extractions (document_id, status);

CREATE TABLE rag_artifact_layers (
    layer_id            VARCHAR2(64) PRIMARY KEY,
    layer_kind          VARCHAR2(32) NOT NULL,
    parent_chunk_set_id VARCHAR2(64) NOT NULL,
    document_id         VARCHAR2(64) NOT NULL,
    tenant_id_hash      CHAR(64),
    requested           NUMBER(1) DEFAULT 1 NOT NULL,
    status              VARCHAR2(32) DEFAULT 'planned_only' NOT NULL,
    reason              VARCHAR2(2000),
    metrics_json        JSON,
    input_fingerprint   JSON,
    created_at          TIMESTAMP WITH TIME ZONE DEFAULT SYSTIMESTAMP NOT NULL,
    updated_at          TIMESTAMP WITH TIME ZONE DEFAULT SYSTIMESTAMP NOT NULL,
    CONSTRAINT rag_artifact_layers_chunk_set_fk
        FOREIGN KEY (parent_chunk_set_id)
        REFERENCES rag_chunk_sets (chunk_set_id) ON DELETE CASCADE,
    CONSTRAINT rag_artifact_layers_document_fk
        FOREIGN KEY (document_id) REFERENCES rag_documents (document_id) ON DELETE CASCADE,
    CONSTRAINT rag_artifact_layers_requested_ck CHECK (requested IN (0, 1)),
    CONSTRAINT rag_artifact_layers_kind_ck
        CHECK (layer_kind IN ('metadata', 'graph', 'navigation')),
    CONSTRAINT rag_artifact_layers_status_ck
        CHECK (
            status IN ('not_requested', 'planned_only', 'materialized', 'needs_reingest', 'error')
        )
);

CREATE INDEX rag_artifact_layers_document_idx ON rag_artifact_layers (document_id);

CREATE INDEX rag_artifact_layers_parent_idx
    ON rag_artifact_layers (parent_chunk_set_id, layer_kind, status);

-- section: native_maintenance
CREATE TABLE RAG_NATIVE_INDEX_MAINTENANCE (
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

CREATE TABLE RAG_NATIVE_INDEX_EVENTS (
    event_id VARCHAR2(32) PRIMARY KEY,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT SYSTIMESTAMP NOT NULL
);

CREATE INDEX RAG_NATIVE_INDEX_EVENTS_TIME ON RAG_NATIVE_INDEX_EVENTS (created_at, event_id);

BEGIN
    EXECUTE IMMEDIATE q'~CREATE OR REPLACE TRIGGER RAG_CHUNKS_NATIVE_MAINT
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
            INSERT INTO RAG_NATIVE_INDEX_EVENTS (event_id) VALUES (RAWTOHEX(SYS_GUID()));
        END IF;
    END AFTER STATEMENT;
END;~';
END;
/

MERGE INTO RAG_NATIVE_INDEX_MAINTENANCE s
USING (SELECT 'hybrid_vectors' state_key FROM dual) v ON (s.state_key=v.state_key)
WHEN NOT MATCHED THEN INSERT (state_key) VALUES (v.state_key);

INSERT INTO RAG_NATIVE_INDEX_EVENTS (event_id)
SELECT RAWTOHEX(SYS_GUID()) FROM RAG_NATIVE_INDEX_MAINTENANCE
WHERE state_key='hybrid_vectors' AND bootstrap_requested=0;

UPDATE RAG_NATIVE_INDEX_MAINTENANCE SET bootstrap_requested=1
WHERE state_key='hybrid_vectors' AND bootstrap_requested=0;

-- section: native_search_scopes
CREATE TABLE rag_native_search_scopes (
    chunk_set_id VARCHAR2(64) PRIMARY KEY,
    native_scope_id NUMBER GENERATED ALWAYS AS IDENTITY UNIQUE,
    CONSTRAINT RAG_NATIVE_SCOPE_CHUNK_SET_FK FOREIGN KEY (chunk_set_id)
        REFERENCES rag_chunk_sets(chunk_set_id) ON DELETE CASCADE
);

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

CREATE INDEX RAG_CHUNKS_NATIVE_SCOPE_IDX ON rag_chunks(native_scope_id);

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

-- section: search_audit
CREATE TABLE rag_search_audit (
    audit_id              VARCHAR2(64) DEFAULT RAWTOHEX(SYS_GUID()) PRIMARY KEY,
    event_type            VARCHAR2(32) DEFAULT 'rag.search' NOT NULL,
    trace_id              VARCHAR2(64) NOT NULL,
    request_id            VARCHAR2(128),
    tenant_id_hash        CHAR(64),
    user_id_hash          CHAR(64),
    outcome               VARCHAR2(32) NOT NULL,
    search_mode           VARCHAR2(16) NOT NULL,
    query_hash            CHAR(64) NOT NULL,
    query_chars           NUMBER(10) NOT NULL,
    filter_keys           JSON,
    memory_plan_id        VARCHAR2(32),
    top_k                 NUMBER(10),
    rerank_top_n          NUMBER(10),
    query_variant_count   NUMBER(10) DEFAULT 1 NOT NULL,
    guardrail_codes       JSON,
    guardrail_severities  JSON,
    retrieved_count       NUMBER(10) DEFAULT 0 NOT NULL,
    reranked_count        NUMBER(10) DEFAULT 0 NOT NULL,
    deduplicated_count    NUMBER(10) DEFAULT 0 NOT NULL,
    context_diversified_count NUMBER(10) DEFAULT 0 NOT NULL,
    context_group_expanded_count NUMBER(10) DEFAULT 0 NOT NULL,
    context_expanded_count NUMBER(10) DEFAULT 0 NOT NULL,
    context_adaptive_expanded_count NUMBER(10) DEFAULT 0 NOT NULL,
    context_dependency_promoted_count NUMBER(10) DEFAULT 0 NOT NULL,
    context_compressed_count NUMBER(10) DEFAULT 0 NOT NULL,
    context_compression_saved_chars NUMBER(10) DEFAULT 0 NOT NULL,
    agent_memory_retrieved_count NUMBER(10) DEFAULT 0 NOT NULL,
    agent_memory_writeback_count NUMBER(10) DEFAULT 0 NOT NULL,
    agent_memory_writeback_status VARCHAR2(32) DEFAULT 'skipped' NOT NULL,
    evidence_count        NUMBER(10) DEFAULT 0 NOT NULL,
    support_count         NUMBER(10) DEFAULT 0 NOT NULL,
    structure_count       NUMBER(10) DEFAULT 0 NOT NULL,
    history_count         NUMBER(10) DEFAULT 0 NOT NULL,
    resolver_rejected_count NUMBER(10) DEFAULT 0 NOT NULL,
    insufficient_context_count NUMBER(10) DEFAULT 0 NOT NULL,
    citation_count        NUMBER(10) DEFAULT 0 NOT NULL,
    context_chars         NUMBER(10) DEFAULT 0 NOT NULL,
    context_window_chars  NUMBER(10),
    document_ids          JSON,
    knowledge_base_ids    JSON,
    config_fingerprint    CHAR(64),
    elapsed_ms            NUMBER(12, 3) NOT NULL,
    error_stage           VARCHAR2(64),
    error_type            VARCHAR2(128),
    created_at            TIMESTAMP WITH TIME ZONE DEFAULT SYSTIMESTAMP NOT NULL,
    CONSTRAINT rag_search_audit_outcome_ck
        CHECK (outcome IN ('success', 'blocked', 'no_results', 'error')),
    CONSTRAINT rag_search_audit_search_mode_ck
        CHECK (search_mode IN ('hybrid', 'vector', 'keyword')),
    CONSTRAINT rag_search_audit_agent_memory_status_ck
        CHECK (agent_memory_writeback_status IN ('skipped', 'saved', 'failed'))
);

CREATE INDEX rag_search_audit_trace_idx
    ON rag_search_audit (trace_id);

CREATE INDEX rag_search_audit_tenant_created_idx
    ON rag_search_audit (tenant_id_hash, created_at DESC);

CREATE INDEX rag_search_audit_created_outcome_idx
    ON rag_search_audit (created_at DESC, outcome);

CREATE INDEX rag_search_audit_query_hash_idx
    ON rag_search_audit (query_hash);

CREATE INDEX rag_search_audit_config_idx
    ON rag_search_audit (config_fingerprint);

-- section: ingestion_audit
CREATE TABLE rag_ingestion_audit (
    audit_id               VARCHAR2(64) DEFAULT RAWTOHEX(SYS_GUID()) PRIMARY KEY,
    event_type             VARCHAR2(32) DEFAULT 'rag.ingestion' NOT NULL,
    trace_id               VARCHAR2(64) NOT NULL,
    request_id             VARCHAR2(128),
    tenant_id_hash         CHAR(64),
    user_id_hash           CHAR(64),
    document_id            VARCHAR2(64) NOT NULL,
    outcome                VARCHAR2(32) NOT NULL,
    source_sha256          CHAR(64) NOT NULL,
    source_bytes           NUMBER(19) NOT NULL,
    document_type          VARCHAR2(128),
    extraction_confidence  NUMBER(6, 5),
    parser_backend         VARCHAR2(80),
    parser_profile         VARCHAR2(80),
    segment_count          NUMBER(10) DEFAULT 0 NOT NULL,
    fallback_count         NUMBER(10) DEFAULT 0 NOT NULL,
    failed_segment_count   NUMBER(10) DEFAULT 0 NOT NULL,
    chunk_count            NUMBER(10) DEFAULT 0 NOT NULL,
    vector_count           NUMBER(10) DEFAULT 0 NOT NULL,
    elapsed_ms             NUMBER(12, 3) NOT NULL,
    error_type             VARCHAR2(128),
    error_message          VARCHAR2(2000),
    created_at             TIMESTAMP WITH TIME ZONE DEFAULT SYSTIMESTAMP NOT NULL,
    CONSTRAINT rag_ingestion_audit_outcome_ck
        CHECK (outcome IN ('success', 'error'))
);

CREATE INDEX rag_ingestion_audit_trace_idx
    ON rag_ingestion_audit (trace_id);

CREATE INDEX rag_ingestion_audit_tenant_created_idx
    ON rag_ingestion_audit (tenant_id_hash, created_at DESC);

CREATE INDEX rag_ingestion_audit_document_created_idx
    ON rag_ingestion_audit (document_id, created_at DESC);

CREATE INDEX rag_ingestion_audit_parser_created_idx
    ON rag_ingestion_audit (parser_backend, parser_profile, created_at DESC);

CREATE INDEX rag_ingestion_audit_source_sha256_idx
    ON rag_ingestion_audit (source_sha256);

-- section: knowledge_graph
CREATE TABLE rag_graph_entities (
    entity_id          VARCHAR2(64) PRIMARY KEY,
    chunk_set_id       VARCHAR2(64),
    tenant_id_hash     CHAR(64),
    knowledge_base_id  VARCHAR2(64),
    canonical_name     VARCHAR2(512 CHAR) NOT NULL,
    entity_type        VARCHAR2(128),
    description        CLOB,
    confidence         NUMBER(6, 5),
    source_document_ids JSON,
    created_at         TIMESTAMP WITH TIME ZONE DEFAULT SYSTIMESTAMP NOT NULL,
    updated_at         TIMESTAMP WITH TIME ZONE DEFAULT SYSTIMESTAMP NOT NULL
);

CREATE INDEX rag_graph_entities_chunk_set_idx
    ON rag_graph_entities (chunk_set_id);

CREATE INDEX rag_graph_entities_tenant_name_idx
    ON rag_graph_entities (tenant_id_hash, canonical_name);

CREATE TABLE rag_graph_relationships (
    relationship_id    VARCHAR2(64) PRIMARY KEY,
    chunk_set_id       VARCHAR2(64),
    tenant_id_hash     CHAR(64),
    knowledge_base_id  VARCHAR2(64),
    source_entity_id   VARCHAR2(64) NOT NULL,
    target_entity_id   VARCHAR2(64) NOT NULL,
    relationship_type  VARCHAR2(128) NOT NULL,
    description        CLOB,
    confidence         NUMBER(6, 5),
    source_document_ids JSON,
    created_at         TIMESTAMP WITH TIME ZONE DEFAULT SYSTIMESTAMP NOT NULL,
    CONSTRAINT rag_graph_rel_source_fk
        FOREIGN KEY (source_entity_id) REFERENCES rag_graph_entities (entity_id),
    CONSTRAINT rag_graph_rel_target_fk
        FOREIGN KEY (target_entity_id) REFERENCES rag_graph_entities (entity_id)
);

CREATE INDEX rag_graph_rel_source_idx
    ON rag_graph_relationships (tenant_id_hash, source_entity_id);

CREATE INDEX rag_graph_rel_target_idx
    ON rag_graph_relationships (tenant_id_hash, target_entity_id);

CREATE TABLE rag_graph_entity_chunks (
    entity_id          VARCHAR2(64) NOT NULL,
    chunk_id           VARCHAR2(128) NOT NULL,
    chunk_set_id       VARCHAR2(64),
    document_id        VARCHAR2(64) NOT NULL,
    tenant_id_hash     CHAR(64),
    relevance_score    NUMBER(8, 6) DEFAULT 1 NOT NULL,
    created_at         TIMESTAMP WITH TIME ZONE DEFAULT SYSTIMESTAMP NOT NULL,
    CONSTRAINT rag_graph_entity_chunks_pk PRIMARY KEY (entity_id, chunk_id),
    CONSTRAINT rag_graph_entity_chunks_entity_fk
        FOREIGN KEY (entity_id) REFERENCES rag_graph_entities (entity_id)
);

CREATE INDEX rag_graph_entity_chunks_chunk_idx
    ON rag_graph_entity_chunks (tenant_id_hash, chunk_id);

CREATE INDEX rag_graph_entity_chunks_chunk_set_idx
    ON rag_graph_entity_chunks (chunk_set_id);

-- section: graph_references
CREATE TABLE rag_graph_references (
    reference_id CHAR(64) PRIMARY KEY,
    knowledge_base_id VARCHAR2(64) NOT NULL,
    source_document_id VARCHAR2(64) NOT NULL,
    source_chunk_set_id VARCHAR2(64) NOT NULL,
    source_chunk_id VARCHAR2(128) NOT NULL,
    source_section_path VARCHAR2(4000 CHAR),
    label VARCHAR2(2000 CHAR) NOT NULL,
    kind VARCHAR2(24) NOT NULL,
    reference_key VARCHAR2(2000 CHAR),
    document_title VARCHAR2(2000 CHAR),
    resolution_status VARCHAR2(32) NOT NULL,
    target_document_id VARCHAR2(64),
    target_chunk_set_id VARCHAR2(64),
    target_section_path VARCHAR2(4000 CHAR),
    created_at TIMESTAMP WITH TIME ZONE DEFAULT SYSTIMESTAMP NOT NULL,
    CONSTRAINT rag_graph_refs_kb_fk FOREIGN KEY (knowledge_base_id)
        REFERENCES rag_knowledge_bases(knowledge_base_id) ON DELETE CASCADE,
    CONSTRAINT rag_graph_refs_source_fk FOREIGN KEY (source_chunk_set_id)
        REFERENCES rag_chunk_sets(chunk_set_id) ON DELETE CASCADE
);
CREATE INDEX rag_graph_refs_source_idx ON rag_graph_references
    (knowledge_base_id,source_chunk_set_id);
CREATE INDEX rag_graph_refs_target_idx ON rag_graph_references
    (knowledge_base_id,target_chunk_set_id);

-- section: foreign_key_lock_indexes
CREATE INDEX rag_graph_refs_source_set_idx
    ON rag_graph_references (source_chunk_set_id);

CREATE INDEX rag_graph_rel_source_entity_idx
    ON rag_graph_relationships (source_entity_id);

CREATE INDEX rag_graph_rel_target_entity_idx
    ON rag_graph_relationships (target_entity_id);

CREATE INDEX rag_structure_builds_doc_idx
    ON rag_structure_index_builds (document_id);

-- section: document_extraction_revisions
CREATE TABLE rag_document_extraction_revisions (
    recipe_id                  VARCHAR2(64) NOT NULL,
    revision_no                NUMBER(10) NOT NULL,
    parent_revision_no         NUMBER(10),
    base_revision_no           NUMBER(10) NOT NULL,
    restored_from_revision_no  NUMBER(10),
    revision_kind              VARCHAR2(16) NOT NULL,
    extraction_recipe_id       VARCHAR2(64) NOT NULL,
    parse_extraction_recipe_id VARCHAR2(64) NOT NULL,
    changed_element_count      NUMBER(10) DEFAULT 0 NOT NULL,
    extraction_json            JSON NOT NULL,
    created_at                 TIMESTAMP WITH TIME ZONE DEFAULT SYSTIMESTAMP NOT NULL,
    CONSTRAINT rag_doc_ext_rev_pk PRIMARY KEY (recipe_id, revision_no),
    CONSTRAINT rag_doc_ext_rev_recipe_fk FOREIGN KEY (recipe_id)
        REFERENCES rag_document_recipes (recipe_id) ON DELETE CASCADE,
    CONSTRAINT rag_doc_ext_rev_kind_ck
        CHECK (revision_kind IN ('PARSED', 'EDITED', 'RESTORED'))
);

-- section: entities
CREATE TABLE rag_entities (
    entity_id       VARCHAR2(64) PRIMARY KEY,
    chunk_set_id    VARCHAR2(64) NOT NULL,
    document_id     VARCHAR2(64) NOT NULL,
    tenant_id_hash  CHAR(64),
    entity_key      VARCHAR2(400 CHAR) NOT NULL,
    display_name    VARCHAR2(400 CHAR) NOT NULL,
    entity_type     VARCHAR2(16) NOT NULL,
    scope_label     VARCHAR2(200 CHAR),
    created_at      TIMESTAMP WITH TIME ZONE DEFAULT SYSTIMESTAMP NOT NULL,
    CONSTRAINT rag_entities_document_fk
        FOREIGN KEY (document_id) REFERENCES rag_documents (document_id) ON DELETE CASCADE,
    CONSTRAINT rag_entities_type_ck
        CHECK (entity_type IN ('record', 'defined', 'value', 'term'))
);

CREATE INDEX rag_entities_chunk_set_idx
    ON rag_entities (chunk_set_id);

CREATE INDEX rag_entities_document_idx
    ON rag_entities (document_id);

CREATE TABLE rag_entity_aliases (
    entity_id   VARCHAR2(64) NOT NULL,
    alias_key   VARCHAR2(400 CHAR) NOT NULL,
    alias_text  VARCHAR2(400 CHAR) NOT NULL,
    alias_kind  VARCHAR2(16) NOT NULL,
    CONSTRAINT rag_entity_aliases_pk PRIMARY KEY (entity_id, alias_key),
    CONSTRAINT rag_entity_aliases_entity_fk
        FOREIGN KEY (entity_id) REFERENCES rag_entities (entity_id) ON DELETE CASCADE
);

CREATE INDEX rag_entity_aliases_key_idx
    ON rag_entity_aliases (alias_key, entity_id);

CREATE TABLE rag_entity_chunks (
    entity_id       VARCHAR2(64) NOT NULL,
    chunk_id        VARCHAR2(128) NOT NULL,
    chunk_role      VARCHAR2(16) NOT NULL,
    attribute_name  VARCHAR2(200 CHAR),
    CONSTRAINT rag_entity_chunks_pk PRIMARY KEY (entity_id, chunk_id),
    CONSTRAINT rag_entity_chunks_entity_fk
        FOREIGN KEY (entity_id) REFERENCES rag_entities (entity_id) ON DELETE CASCADE,
    CONSTRAINT rag_entity_chunks_chunk_fk
        FOREIGN KEY (chunk_id) REFERENCES rag_chunks (chunk_id) ON DELETE CASCADE,
    CONSTRAINT rag_entity_chunks_role_ck
        CHECK (chunk_role IN ('definition', 'attribute', 'mention'))
);

CREATE INDEX rag_entity_chunks_chunk_idx
    ON rag_entity_chunks (chunk_id, chunk_role);

-- section: section_map
CREATE TABLE rag_chunk_set_sections (
    chunk_set_id       VARCHAR2(64) NOT NULL,
    section_id         VARCHAR2(64) NOT NULL,
    document_id        VARCHAR2(64) NOT NULL,
    section_ordinal    NUMBER(10) NOT NULL,
    section_level      NUMBER(2) NOT NULL,
    parent_section_id  VARCHAR2(64),
    title              VARCHAR2(400 CHAR) NOT NULL,
    page_start         NUMBER(10),
    page_end           NUMBER(10),
    chunk_count        NUMBER(10) DEFAULT 0 NOT NULL,
    char_count         NUMBER(19) DEFAULT 0 NOT NULL,
    first_chunk_index  NUMBER(10),
    last_chunk_index   NUMBER(10),
    created_at         TIMESTAMP WITH TIME ZONE DEFAULT SYSTIMESTAMP NOT NULL,
    CONSTRAINT rag_chunk_set_sections_pk PRIMARY KEY (chunk_set_id, section_id),
    CONSTRAINT rag_chunk_set_sections_document_fk
        FOREIGN KEY (document_id) REFERENCES rag_documents (document_id) ON DELETE CASCADE
);

CREATE INDEX rag_chunk_set_sections_document_idx
    ON rag_chunk_set_sections (document_id);

CREATE TABLE rag_section_chunks (
    chunk_set_id  VARCHAR2(64) NOT NULL,
    section_id    VARCHAR2(64) NOT NULL,
    chunk_id      VARCHAR2(128) NOT NULL,
    document_id   VARCHAR2(64) NOT NULL,
    chunk_order   NUMBER(10) NOT NULL,
    chunk_index   NUMBER(10) NOT NULL,
    is_owner      NUMBER(1) DEFAULT 0 NOT NULL,
    CONSTRAINT rag_section_chunks_pk PRIMARY KEY (chunk_set_id, section_id, chunk_id),
    CONSTRAINT rag_section_chunks_section_fk
        FOREIGN KEY (chunk_set_id, section_id)
        REFERENCES rag_chunk_set_sections (chunk_set_id, section_id) ON DELETE CASCADE,
    CONSTRAINT rag_section_chunks_chunk_fk
        FOREIGN KEY (chunk_id) REFERENCES rag_chunks (chunk_id) ON DELETE CASCADE,
    CONSTRAINT rag_section_chunks_owner_ck CHECK (is_owner IN (0, 1))
);

CREATE INDEX rag_section_chunks_chunk_idx
    ON rag_section_chunks (chunk_id, is_owner);

CREATE INDEX rag_section_chunks_document_idx
    ON rag_section_chunks (document_id);

-- section: recipe_version_log
CREATE TABLE rag_recipe_version_log (
    log_id                    VARCHAR2(64) PRIMARY KEY,
    document_id               VARCHAR2(64) NOT NULL,
    recipe_id                 VARCHAR2(64) NOT NULL,
    chunk_set_id              VARCHAR2(64) NOT NULL,
    parent_chunk_set_id       VARCHAR2(64),
    operation                 VARCHAR2(16) NOT NULL,
    source_sha256             CHAR(64),
    extraction_recipe_id      VARCHAR2(64),
    chunk_set_input_id        VARCHAR2(64),
    recipe_revision           NUMBER(10),
    language_pack_release_id  VARCHAR2(64),
    language_pack_ingest_hash VARCHAR2(64),
    chunk_count               NUMBER(10) NOT NULL,
    job_id                    VARCHAR2(64),
    activated_at              TIMESTAMP WITH TIME ZONE DEFAULT SYSTIMESTAMP NOT NULL,
    CONSTRAINT rag_recipe_version_log_op_ck
        CHECK (operation IN ('INITIAL', 'REPROCESS', 'ROLLBACK')),
    CONSTRAINT rag_recipe_version_log_doc_fk
        FOREIGN KEY (document_id) REFERENCES rag_documents (document_id) ON DELETE CASCADE,
    CONSTRAINT rag_recipe_version_log_rcp_fk
        FOREIGN KEY (recipe_id) REFERENCES rag_document_recipes (recipe_id) ON DELETE CASCADE
);

CREATE INDEX rag_recipe_version_log_rcp_idx
    ON rag_recipe_version_log (recipe_id, activated_at DESC);

CREATE INDEX rag_recipe_version_log_doc_idx
    ON rag_recipe_version_log (document_id);

-- section: document_wiki
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
    INDEXTYPE IS CTXSYS.CONTEXT PARAMETERS ('LEXER RAG_TEXT_WORLD_LEXER STOPLIST RAG_TEXT_STOPLIST SYNC (ON COMMIT)');

-- section: document_summaries
CREATE TABLE rag_document_summaries (
    document_id      VARCHAR2(64) NOT NULL,
    status           VARCHAR2(16) NOT NULL,
    summary_text     CLOB,
    topics_json      JSON,
    input_hash       VARCHAR2(64),
    input_truncated  NUMBER(1) DEFAULT 0 NOT NULL,
    input_chars      NUMBER(10),
    source_chars     NUMBER(10),
    chunk_set_id     VARCHAR2(64),
    failure_code     VARCHAR2(40),
    generating_since TIMESTAMP WITH TIME ZONE,
    generated_at     TIMESTAMP WITH TIME ZONE,
    updated_at       TIMESTAMP WITH TIME ZONE DEFAULT SYSTIMESTAMP NOT NULL,
    CONSTRAINT rag_document_summaries_pk PRIMARY KEY (document_id),
    CONSTRAINT rag_document_summaries_document_fk
        FOREIGN KEY (document_id) REFERENCES rag_documents (document_id) ON DELETE CASCADE,
    CONSTRAINT rag_document_summaries_status_ck
        CHECK (status IN ('PENDING', 'READY', 'FAILED')),
    CONSTRAINT rag_document_summaries_truncated_ck CHECK (input_truncated IN (0, 1))
);

-- section: citation_feedback
CREATE TABLE rag_citation_feedback (
    feedback_id       VARCHAR2(64) DEFAULT RAWTOHEX(SYS_GUID()) PRIMARY KEY,
    trace_id          VARCHAR2(64) NOT NULL,
    search_answer_profile_id  VARCHAR2(64),
    target_type       VARCHAR2(16) DEFAULT 'citation' NOT NULL,
    source_surface    VARCHAR2(16),
    document_id       VARCHAR2(64),
    chunk_id          VARCHAR2(128),
    tenant_id_hash    CHAR(64),
    user_id_hash      CHAR(64),
    rating            VARCHAR2(32) NOT NULL,
    reason            VARCHAR2(64),
    comment_hash      CHAR(64),
    comment_chars     NUMBER(10) DEFAULT 0 NOT NULL,
    created_at        TIMESTAMP WITH TIME ZONE DEFAULT SYSTIMESTAMP NOT NULL,
    CONSTRAINT rag_citation_feedback_rating_ck
        CHECK (rating IN ('helpful', 'not_helpful')),
    CONSTRAINT rag_citation_feedback_target_ck
        CHECK (target_type IN ('answer', 'citation')),
    CONSTRAINT rag_citation_feedback_surface_ck
        CHECK (source_surface IS NULL OR source_surface IN ('search', 'chat')),
    CONSTRAINT rag_citation_feedback_shape_ck
        CHECK (
            (target_type = 'answer' AND document_id IS NULL AND chunk_id IS NULL)
            OR (target_type = 'citation' AND document_id IS NOT NULL AND chunk_id IS NOT NULL)
        ),
    CONSTRAINT rag_citation_feedback_reason_ck
        CHECK (
            (rating = 'helpful' AND reason IS NULL)
            OR (
                rating = 'not_helpful'
                AND (
                    (target_type = 'answer' AND reason IN (
                        'incorrect', 'incomplete', 'not_relevant', 'answer_untrusted',
                        'missing_knowledge', 'outdated_source', 'ambiguous_question'
                    ))
                    OR (target_type = 'citation' AND reason IN (
                        'missing_evidence', 'not_relevant', 'answer_untrusted'
                    ))
                )
            )
        )
);

CREATE INDEX rag_citation_feedback_trace_idx
    ON rag_citation_feedback (trace_id);

CREATE INDEX rag_citation_feedback_tenant_created_idx
    ON rag_citation_feedback (tenant_id_hash, created_at DESC);

CREATE INDEX rag_feedback_business_created_idx
    ON rag_citation_feedback (tenant_id_hash, search_answer_profile_id, created_at DESC);

CREATE INDEX rag_feedback_user_trace_idx
    ON rag_citation_feedback (tenant_id_hash, user_id_hash, trace_id, created_at DESC);

-- section: feedback_details
CREATE TABLE rag_feedback_details (
    feedback_id       VARCHAR2(64) PRIMARY KEY,
    tenant_id_hash    CHAR(64),
    message_id        VARCHAR2(64),
    content_source    VARCHAR2(32) NOT NULL,
    question_text     CLOB,
    answer_text       CLOB,
    comment_text      VARCHAR2(1000 CHAR),
    corrected_answer_text CLOB,
    citations_json    JSON,
    search_text       CLOB,
    created_at        TIMESTAMP WITH TIME ZONE DEFAULT SYSTIMESTAMP NOT NULL,
    CONSTRAINT rag_feedback_details_feedback_fk
        FOREIGN KEY (feedback_id)
        REFERENCES rag_citation_feedback (feedback_id)
        ON DELETE CASCADE,
    CONSTRAINT rag_feedback_details_source_ck
        CHECK (content_source IN ('chat_message', 'search_snapshot'))
);

CREATE INDEX rag_feedback_details_tenant_created_idx
    ON rag_feedback_details (tenant_id_hash, created_at DESC);

CREATE INDEX rag_feedback_details_message_idx
    ON rag_feedback_details (message_id);

CREATE INDEX rag_feedback_details_text_idx
    ON rag_feedback_details (search_text)
    INDEXTYPE IS CTXSYS.CONTEXT
    PARAMETERS ('LEXER RAG_TEXT_WORLD_LEXER STOPLIST RAG_TEXT_STOPLIST SYNC (ON COMMIT)');

-- section: evaluation_artifacts
CREATE TABLE rag_evaluation_runs (
    evaluation_run_id VARCHAR2(64) DEFAULT RAWTOHEX(SYS_GUID()) PRIMARY KEY,
    tenant_id_hash    CHAR(64),
    knowledge_base_ids JSON,
    request_json      JSON NOT NULL,
    result_json       JSON NOT NULL,
    result_sha256     CHAR(64) NOT NULL,
    passed            NUMBER(1) DEFAULT 0 NOT NULL,
    created_at        TIMESTAMP WITH TIME ZONE DEFAULT SYSTIMESTAMP NOT NULL
);

CREATE INDEX rag_evaluation_runs_tenant_created_idx
    ON rag_evaluation_runs (tenant_id_hash, created_at DESC);

CREATE INDEX rag_evaluation_runs_result_hash_idx
    ON rag_evaluation_runs (result_sha256);

-- section: evaluation_source_workbooks
CREATE TABLE rag_evaluation_source_workbooks (
    workbook_id         VARCHAR2(64) PRIMARY KEY,
    tenant_id_hash      VARCHAR2(64),
    user_id_hash        VARCHAR2(64),
    file_name           VARCHAR2(512 CHAR) NOT NULL,
    content_sha256      VARCHAR2(64) NOT NULL,
    byte_size           NUMBER(12) NOT NULL,
    object_storage_path VARCHAR2(1024 CHAR) NOT NULL,
    created_at          TIMESTAMP WITH TIME ZONE DEFAULT SYSTIMESTAMP NOT NULL
);

CREATE INDEX rag_evaluation_source_workbooks_owner_idx
    ON rag_evaluation_source_workbooks (tenant_id_hash, user_id_hash, content_sha256)

-- section: evaluation_jobs
CREATE TABLE rag_evaluation_jobs (
    job_id                  VARCHAR2(64) PRIMARY KEY,
    kind                    VARCHAR2(16) NOT NULL,
    status                  VARCHAR2(16) NOT NULL,
    tenant_id_hash          VARCHAR2(64),
    user_id_hash            VARCHAR2(64),
    total_cases             NUMBER(10) DEFAULT 0 NOT NULL,
    completed_cases         NUMBER(10) DEFAULT 0 NOT NULL,
    current_case_id         VARCHAR2(200 CHAR),
    current_case_started_at TIMESTAMP WITH TIME ZONE,
    lease_owner             VARCHAR2(128),
    heartbeat_at            TIMESTAMP WITH TIME ZONE,
    time_limit_seconds      NUMBER(10) NOT NULL,
    error_message           VARCHAR2(2000 CHAR),
    result_json             JSON,
    evaluation_run_id       VARCHAR2(64),
    source_workbook_id      VARCHAR2(64),
    search_answer_profile_id VARCHAR2(128),
    compare_search_answer_profile_id VARCHAR2(128),
    user_uuid               VARCHAR2(64),
    created_at              TIMESTAMP WITH TIME ZONE DEFAULT SYSTIMESTAMP NOT NULL,
    started_at              TIMESTAMP WITH TIME ZONE,
    finished_at             TIMESTAMP WITH TIME ZONE,
    updated_at              TIMESTAMP WITH TIME ZONE DEFAULT SYSTIMESTAMP NOT NULL,
    CONSTRAINT rag_evaluation_jobs_source_workbook_fk FOREIGN KEY (source_workbook_id)
        REFERENCES rag_evaluation_source_workbooks (workbook_id) ON DELETE SET NULL,
    CONSTRAINT rag_evaluation_jobs_kind_ck CHECK (kind IN ('run')),
    CONSTRAINT rag_evaluation_jobs_status_ck
        CHECK (status IN ('RUNNING', 'SUCCEEDED', 'FAILED', 'CANCELLED'))
);

CREATE INDEX rag_evaluation_jobs_status_idx
    ON rag_evaluation_jobs (status, heartbeat_at);

CREATE INDEX rag_evaluation_jobs_owner_created_idx
    ON rag_evaluation_jobs (tenant_id_hash, user_id_hash, created_at DESC);

CREATE INDEX rag_evaluation_jobs_source_workbook_idx
    ON rag_evaluation_jobs (source_workbook_id)

-- section: evaluation_job_case_results
CREATE TABLE rag_evaluation_job_case_results (
    job_id                   VARCHAR2(64) NOT NULL,
    pass_index               NUMBER(1) NOT NULL,
    case_index               NUMBER(10) NOT NULL,
    case_id                  VARCHAR2(200 CHAR) NOT NULL,
    search_answer_profile_id VARCHAR2(128),
    result_json              JSON NOT NULL,
    created_at               TIMESTAMP WITH TIME ZONE DEFAULT SYSTIMESTAMP NOT NULL,
    CONSTRAINT rag_evaluation_job_case_results_pk PRIMARY KEY (job_id, pass_index, case_index),
    CONSTRAINT rag_evaluation_job_case_results_job_fk FOREIGN KEY (job_id)
        REFERENCES rag_evaluation_jobs (job_id) ON DELETE CASCADE
)

-- section: evaluation_testsets
CREATE TABLE rag_evaluation_testsets (
    draft_id VARCHAR2(64) PRIMARY KEY,
    tenant_id_hash VARCHAR2(64),
    user_id_hash VARCHAR2(64) NOT NULL,
    name VARCHAR2(200 CHAR) NOT NULL,
    revision NUMBER(10) NOT NULL,
    item_count NUMBER(10) NOT NULL,
    confirmed_count NUMBER(10) NOT NULL,
    draft_json JSON NOT NULL,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT SYSTIMESTAMP NOT NULL,
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT SYSTIMESTAMP NOT NULL
);
CREATE INDEX rag_evaluation_testsets_owner_idx
    ON rag_evaluation_testsets (tenant_id_hash, user_id_hash, updated_at DESC)

-- section: role_access
CREATE TABLE rag_role_permissions (
    role_id          VARCHAR2(36) NOT NULL,
    permission_code  VARCHAR2(128) NOT NULL,
    CONSTRAINT rag_role_permissions_pk PRIMARY KEY (role_id, permission_code),
    CONSTRAINT rag_role_permissions_role_fk
        FOREIGN KEY (role_id)
        REFERENCES platform_roles (role_id)
        ON DELETE CASCADE
);

CREATE TABLE rag_role_search_answer_profiles (
    role_id           VARCHAR2(36) NOT NULL,
    search_answer_profile_id  VARCHAR2(64) NOT NULL,
    CONSTRAINT rag_role_search_answer_profiles_pk PRIMARY KEY (role_id, search_answer_profile_id),
    CONSTRAINT rag_role_search_answer_profiles_role_fk
        FOREIGN KEY (role_id)
        REFERENCES platform_roles (role_id)
        ON DELETE CASCADE,
    CONSTRAINT rag_role_search_answer_profiles_view_fk
        FOREIGN KEY (search_answer_profile_id)
        REFERENCES rag_search_answer_profiles (search_answer_profile_id)
        ON DELETE CASCADE
);

CREATE INDEX rag_role_search_answer_profiles_view_idx
    ON rag_role_search_answer_profiles (search_answer_profile_id);

CREATE TABLE rag_role_knowledge_bases (
    role_id            VARCHAR2(36) NOT NULL,
    knowledge_base_id  VARCHAR2(64) NOT NULL,
    CONSTRAINT rag_role_knowledge_bases_pk PRIMARY KEY (role_id, knowledge_base_id),
    CONSTRAINT rag_role_knowledge_bases_role_fk
        FOREIGN KEY (role_id)
        REFERENCES platform_roles (role_id)
        ON DELETE CASCADE,
    CONSTRAINT rag_role_knowledge_bases_kb_fk
        FOREIGN KEY (knowledge_base_id)
        REFERENCES rag_knowledge_bases (knowledge_base_id)
        ON DELETE CASCADE
);

CREATE INDEX rag_role_knowledge_bases_kb_idx
    ON rag_role_knowledge_bases (knowledge_base_id)
