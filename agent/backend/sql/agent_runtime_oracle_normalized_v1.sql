-- Agent Runtime Oracle normalized projection schema v1.
-- This artifact is the migration source of truth for production Oracle review.
-- Runtime auto-create uses the same logical table set; production can adapt the
-- optional partition clauses below before applying with a DBA-owned migration tool.

CREATE TABLE AGENT_RUNTIME_RUNS (
    run_id VARCHAR2(128) PRIMARY KEY,
    agent_id VARCHAR2(128) NOT NULL,
    status VARCHAR2(32) NOT NULL,
    goal CLOB NOT NULL,
    metadata_json CLOB,
    pending_tool_calls_json CLOB,
    created_at TIMESTAMP WITH TIME ZONE NOT NULL,
    updated_at TIMESTAMP WITH TIME ZONE NOT NULL
);

CREATE TABLE AGENT_RUNTIME_EVENTS (
    event_id VARCHAR2(128) PRIMARY KEY,
    run_id VARCHAR2(128) NOT NULL,
    event_type VARCHAR2(128) NOT NULL,
    message CLOB NOT NULL,
    payload_json CLOB,
    created_at TIMESTAMP WITH TIME ZONE NOT NULL
);

CREATE TABLE AGENT_RUNTIME_STEPS (
    step_id VARCHAR2(128) PRIMARY KEY,
    run_id VARCHAR2(128) NOT NULL,
    kind VARCHAR2(64) NOT NULL,
    status VARCHAR2(32) NOT NULL,
    tool_name VARCHAR2(256),
    approval_id VARCHAR2(128),
    tool_call_json CLOB,
    tool_result_json CLOB,
    started_at TIMESTAMP WITH TIME ZONE,
    completed_at TIMESTAMP WITH TIME ZONE
);

CREATE TABLE AGENT_RUNTIME_APPROVALS (
    approval_id VARCHAR2(128) PRIMARY KEY,
    run_id VARCHAR2(128) NOT NULL,
    step_id VARCHAR2(128) NOT NULL,
    tool_name VARCHAR2(256) NOT NULL,
    status VARCHAR2(32) NOT NULL,
    reason CLOB NOT NULL,
    decided_by VARCHAR2(256),
    decided_at TIMESTAMP WITH TIME ZONE,
    created_at TIMESTAMP WITH TIME ZONE NOT NULL,
    tool_call_json CLOB NOT NULL
);

CREATE TABLE AGENT_RUNTIME_ARTIFACTS (
    artifact_id VARCHAR2(128) PRIMARY KEY,
    run_id VARCHAR2(128) NOT NULL,
    name VARCHAR2(512) NOT NULL,
    kind VARCHAR2(128) NOT NULL,
    content_json CLOB NOT NULL,
    created_at TIMESTAMP WITH TIME ZONE NOT NULL
);

CREATE TABLE AGENT_RUNTIME_MEMORY (
    memory_id VARCHAR2(128) PRIMARY KEY,
    kind VARCHAR2(64) NOT NULL,
    content CLOB NOT NULL,
    metadata_json CLOB,
    created_at TIMESTAMP WITH TIME ZONE NOT NULL
);

CREATE INDEX AGENT_RUNTIME_RUNS_STATUS_CREATED_IX
    ON AGENT_RUNTIME_RUNS (status, created_at);

CREATE INDEX AGENT_RUNTIME_EVENTS_RUN_TYPE_CREATED_IX
    ON AGENT_RUNTIME_EVENTS (run_id, event_type, created_at);

CREATE INDEX AGENT_RUNTIME_STEPS_RUN_TOOL_STATUS_IX
    ON AGENT_RUNTIME_STEPS (run_id, tool_name, status, completed_at);

CREATE INDEX AGENT_RUNTIME_STEPS_ERROR_CODE_IX
    ON AGENT_RUNTIME_STEPS (
        JSON_VALUE(tool_result_json, '$.error_code' RETURNING VARCHAR2(128))
    );

CREATE INDEX AGENT_RUNTIME_APPROVALS_RUN_STATUS_IX
    ON AGENT_RUNTIME_APPROVALS (run_id, status, created_at);

CREATE INDEX AGENT_RUNTIME_ARTIFACTS_RUN_KIND_IX
    ON AGENT_RUNTIME_ARTIFACTS (run_id, kind, created_at);

CREATE INDEX AGENT_RUNTIME_MEMORY_KIND_CREATED_IX
    ON AGENT_RUNTIME_MEMORY (kind, created_at);

-- Optional production partitioning template:
-- Use interval range partitioning on created_at for append-heavy tables:
--   AGENT_RUNTIME_EVENTS(created_at), AGENT_RUNTIME_APPROVALS(created_at),
--   AGENT_RUNTIME_ARTIFACTS(created_at), AGENT_RUNTIME_MEMORY(created_at).
-- Use completed_at for AGENT_RUNTIME_STEPS when most audit queries are terminal
-- tool calls; keep AGENT_RUNTIME_RUNS unpartitioned unless run volume demands it.
--
-- Example:
-- PARTITION BY RANGE (created_at)
-- INTERVAL (NUMTODSINTERVAL(1, 'DAY'))
-- (PARTITION p_initial VALUES LESS THAN (TIMESTAMP '2026-01-01 00:00:00 UTC'));
--
-- Retention:
-- AGENT_RUNTIME_ORACLE_PROJECTION_RETENTION_DAYS deletes from child-like
-- projection tables before runs. Checkpoint CLOB rows are intentionally retained
-- for recovery and should have a separate backup/retention policy.
