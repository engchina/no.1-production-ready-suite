"""Oracle schema artifact CLI のテスト。"""

import hashlib
import json
from pathlib import Path

from pytest import CaptureFixture

from app.rag import oracle_schema


def test_oracle_schema_sql_contains_required_rag_tables() -> None:
    """RAG 本番運用に必要な Oracle table / vector 契約を artifact に含める。"""
    sql = oracle_schema.oracle_schema_sql()

    assert "-- section: documents" in sql
    assert "CREATE TABLE rag_documents" in sql
    assert "-- section: knowledge_bases" in sql
    assert "CREATE TABLE rag_knowledge_bases" in sql
    assert "CREATE TABLE rag_document_knowledge_bases" in sql
    assert "-- section: business_views" in sql
    assert "CREATE TABLE rag_business_views" in sql
    assert "-- section: ingestion_jobs" in sql
    assert "CREATE TABLE rag_ingestion_jobs" in sql
    assert "-- section: ingestion_segments" in sql
    assert "CREATE TABLE rag_ingestion_segments" in sql
    assert "CREATE TABLE rag_chunks" in sql
    assert "embedding       VECTOR(1536, FLOAT32)" in sql
    assert "chunk_set_id    VARCHAR2(64)" in sql
    assert "CREATE VECTOR INDEX rag_chunks_embedding_hnsw_idx" in sql
    assert "CTX_DDL.CREATE_PREFERENCE('RAG_TEXT_WORLD_LEXER', 'WORLD_LEXER')" in sql
    assert "CTX_DDL.CREATE_STOPLIST('RAG_TEXT_STOPLIST', 'BASIC_STOPLIST')" in sql
    assert (
        "PARAMETERS ('LEXER RAG_TEXT_WORLD_LEXER STOPLIST RAG_TEXT_STOPLIST SYNC (ON COMMIT)')"
        in sql
    )
    assert "CREATE INDEX rag_chunks_chunk_set_idx" in sql
    assert "TYPE HNSW" in sql
    assert "WITH TARGET ACCURACY 95" in sql
    assert "-- section: chunk_sets" in sql
    assert "CREATE TABLE rag_chunk_sets" in sql
    assert "extraction_recipe_id VARCHAR2(64)" in sql
    assert "CREATE TABLE rag_document_extractions" in sql
    assert "CREATE TABLE rag_artifact_layers" in sql
    # 3 層モデル: per-KB binding 表は base schema から退役済み(membership + is_serving に一本化)。
    assert "CREATE TABLE rag_kb_chunk_set_bindings" not in sql
    assert "rag_chunk_sets_document_fk" in sql
    assert "CREATE TABLE rag_search_audit" in sql
    assert "memory_plan_id        VARCHAR2(32)" in sql
    assert "resolver_rejected_count NUMBER(10) DEFAULT 0 NOT NULL" in sql
    assert "CREATE TABLE rag_ingestion_audit" in sql
    assert "parser_backend         VARCHAR2(80)" in sql
    assert "segment_count          NUMBER(10) DEFAULT 0 NOT NULL" in sql
    assert "failed_segment_count   NUMBER(10) DEFAULT 0 NOT NULL" in sql
    assert "-- section: knowledge_graph" in sql
    assert "CREATE TABLE rag_graph_entities" in sql
    assert "-- section: agent_memory" in sql
    assert "CREATE TABLE rag_agent_memories" in sql
    assert "role_id_hash     CHAR(64)" in sql
    assert "embedding        VECTOR(1536, FLOAT32) NOT NULL" in sql
    assert "CREATE VECTOR INDEX rag_agent_memories_embedding_hnsw_idx" in sql
    assert "-- section: citation_feedback" in sql
    assert "CREATE TABLE rag_citation_feedback" in sql
    assert "-- section: evaluation_artifacts" in sql
    assert "CREATE TABLE rag_evaluation_runs" in sql
    assert "result_sha256     CHAR(64) NOT NULL" in sql
    assert "SELECT AI" not in sql.upper()


def test_oracle_schema_manifest_is_deterministic() -> None:
    """manifest は時刻を入れず、SQL の hash / statement 数を検証できる形にする。"""
    sql = oracle_schema.oracle_schema_sql()
    manifest = oracle_schema.oracle_schema_manifest()

    assert manifest == oracle_schema.oracle_schema_manifest()
    assert "generated_at" not in manifest
    assert manifest["schema_name"] == "production-ready-rag-oracle-26ai"
    assert manifest["schema_version"] == "1"
    assert manifest["vector_contract"] == "VECTOR(1536, FLOAT32)"
    assert manifest["vector_index"] == {
        "distance": "COSINE",
        "efconstruction": 500,
        "neighbors": 32,
        "target_accuracy": 95,
        "type": "HNSW",
    }
    assert manifest["sha256"] == hashlib.sha256(sql.encode("utf-8")).hexdigest()
    assert manifest["statement_count"] == len(oracle_schema.split_sql_statements(sql))
    assert [section["name"] for section in manifest["sections"]] == [
        "documents",
        "document_recipes",
        "knowledge_bases",
        "business_views",
        "conversations",
        "messages",
        "ingestion_jobs",
        "ingestion_segments",
        "chunks",
        "chunk_sets",
        "document_extractions",
        "search_audit",
        "ingestion_audit",
        "knowledge_graph",
        "agent_memory",
        "citation_feedback",
        "evaluation_artifacts",
    ]
    assert all(section["statement_count"] > 0 for section in manifest["sections"])


def test_oracle_schema_migration_sql_adds_ingestion_job_attempt_counters() -> None:
    """migration artifact は旧 ingestion queue table を現行 DDL 契約へ寄せる。"""
    sql = oracle_schema.oracle_schema_migration_sql()
    statements = oracle_schema.split_sql_statements(sql)

    assert "-- migration: 20260615_001_ingestion_jobs_attempt_counters" in sql
    assert "FROM user_tab_columns" in sql
    assert "column_name = 'ATTEMPT_COUNT'" in sql
    assert "column_name = 'MAX_ATTEMPTS'" in sql
    assert "ALTER TABLE rag_ingestion_jobs ADD" in sql
    assert "(attempt_count NUMBER(5) DEFAULT 0 NOT NULL)" in sql
    assert "UPDATE rag_ingestion_jobs SET attempt_count = 0" in sql
    assert "WHERE attempt_count IS NULL" in sql
    assert "ALTER TABLE rag_ingestion_jobs MODIFY" in sql
    assert "ALTER TABLE rag_ingestion_jobs ADD" in sql
    assert "(max_attempts NUMBER(5) DEFAULT 3 NOT NULL)" in sql
    assert "UPDATE rag_ingestion_jobs SET max_attempts = 3" in sql
    assert "WHERE max_attempts IS NULL" in sql
    assert "ALTER TABLE rag_ingestion_jobs MODIFY" in sql
    assert "DROP CONSTRAINT" in sql
    assert "rag_ingestion_jobs_attempts_ck" in sql
    assert "CHECK" in sql
    assert "(attempt_count >= 0 AND max_attempts >= 1)" in sql
    assert "-- migration: 20260616_001_search_audit_search_mode" in sql
    assert "table_name = 'RAG_SEARCH_AUDIT'" in sql
    assert "column_name = 'MODE'" in sql
    assert "column_name = 'SEARCH_MODE'" in sql
    assert "RENAME COLUMN mode TO search_mode" in sql
    assert "rag_search_audit_search_mode_ck" in sql
    assert "(search_mode IN (''hybrid'', ''vector'', ''keyword''))" in sql
    assert "-- migration: 20260616_002_evaluation_runs_result_sha256" in sql
    assert "table_name = 'RAG_EVALUATION_RUNS'" in sql
    assert "column_name = 'RESULT_SHA256'" in sql
    assert "rag_evaluation_runs_result_hash_idx" in sql
    assert "-- migration: 20260616_003_ingestion_jobs_cancelled_status" in sql
    assert "rag_ingestion_jobs_status_ck" in sql
    assert "''CANCELLED''" in sql
    assert "-- migration: 20260616_004_ingestion_segments" in sql
    assert "CREATE TABLE rag_ingestion_segments" in sql
    assert "RAG_INGESTION_SEGMENTS_DOCUMENT_STATUS_IDX" in sql
    assert "-- migration: 20260616_005_search_audit_memory_engineering" in sql
    assert "column_name = p_column_name" in sql
    assert "MEMORY_PLAN_ID" in sql
    assert "RESOLVER_REJECTED_COUNT" in sql
    assert "AGENT_MEMORY_RETRIEVED_COUNT" in sql
    assert "-- migration: 20260616_006_agent_memories" in sql
    assert "CREATE TABLE rag_agent_memories" in sql
    assert "ROLE_ID_HASH" in sql
    assert "CREATE VECTOR INDEX rag_agent_memories_embedding_hnsw_idx" in sql
    assert "-- migration: 20260617_001_ingestion_audit_file_processing_metrics" in sql
    assert "table_name = 'RAG_INGESTION_AUDIT'" in sql
    assert "PARSER_BACKEND" in sql
    assert "PARSER_PROFILE" in sql
    assert "SEGMENT_COUNT" in sql
    assert "FAILED_SEGMENT_COUNT" in sql
    assert "rag_ingestion_audit_parser_created_idx" in sql
    assert "-- migration: 20260617_002_search_audit_adaptive_context" in sql
    assert "table_name = 'RAG_SEARCH_AUDIT'" in sql
    assert "CONTEXT_ADAPTIVE_EXPANDED_COUNT" in sql
    assert "-- migration: 20260617_003_search_audit_dependency_context" in sql
    assert "CONTEXT_DEPENDENCY_PROMOTED_COUNT" in sql
    assert "-- migration: 20260618_001_documents_review_status" in sql
    assert "rag_documents_status_ck" in sql
    assert "''REVIEW''" in sql
    assert "''CHUNKING''" in sql
    assert "''CHUNKED''" in sql
    assert "''INDEXING''" in sql
    assert "-- migration: 20260618_002_ingestion_jobs_phase" in sql
    assert "rag_ingestion_jobs_phase_ck" in sql
    assert "ALTER TABLE rag_ingestion_jobs DROP CONSTRAINT" in sql
    assert "(phase IN (''PREPROCESS'', ''EXTRACT'', ''CHUNK'', ''INDEX''))" in sql
    assert "-- migration: 20260619_001_business_views" in sql
    assert "table_name = 'RAG_BUSINESS_VIEWS'" in sql
    assert "rag_business_views_status_ck" in sql
    assert "-- migration: 20260621_001_chunk_sets" in sql
    assert "CREATE TABLE rag_chunk_sets" in sql
    assert "RAG_DOCUMENT_EXTRACTIONS_DOCUMENT_IDX" in sql
    assert "(status IN (''INGESTING'', ''CHUNKED'', ''INDEXED'', ''ERROR''))" in sql
    assert "RAG_DOCUMENT_EXTRACTIONS" in sql
    assert "RAG_ARTIFACT_LAYERS" in sql
    assert "CREATE TABLE rag_kb_chunk_set_bindings" in sql
    assert "column_name = 'EXTRACTION_RECIPE_ID'" in sql
    assert "column_name = 'CHUNK_SET_ID'" in sql
    assert "ALTER TABLE rag_chunks ADD (chunk_set_id VARCHAR2(64))" in sql
    assert "-- migration: 20260621_002_document_extractions" in sql
    assert "CREATE TABLE rag_document_extractions" in sql
    assert "ALTER TABLE rag_chunk_sets ADD (extraction_id VARCHAR2(64))" in sql
    assert "-- migration: 20260623_001_nullable_chunk_embeddings" in sql
    assert "ALTER TABLE rag_chunks MODIFY (embedding NULL)" in sql
    assert "-- migration: 20260625_001_chunks_text_world_lexer" in sql
    assert "CTX_DDL.CREATE_PREFERENCE('RAG_TEXT_WORLD_LEXER', 'WORLD_LEXER')" in sql
    assert "CTX_DDL.CREATE_STOPLIST('RAG_TEXT_STOPLIST', 'BASIC_STOPLIST')" in sql
    assert "DROP INDEX rag_chunks_text_idx" in sql
    assert "FROM ctx_user_index_objects" in sql
    assert "ixo_object = 'WORLD_LEXER'" in sql
    assert "'CREATE INDEX rag_chunks_text_idx '" in sql
    assert "|| 'ON rag_chunks (chunk_text) '" in sql
    assert "|| 'INDEXTYPE IS CTXSYS.CONTEXT '" in sql
    assert (
        "|| 'PARAMETERS (''LEXER RAG_TEXT_WORLD_LEXER STOPLIST RAG_TEXT_STOPLIST "
        "SYNC (ON COMMIT)'')'" in sql
    )
    assert "-- migration: 20260625_002_preprocess_artifact" in sql
    assert "column_name = 'PREPROCESS_ARTIFACT'" in sql
    assert "ALTER TABLE rag_documents ADD (preprocess_artifact JSON)" in sql
    assert "''PREPROCESSING''" in sql
    assert "-- migration: 20260627_001_documents_preprocessed_status" in sql
    assert "''PREPROCESSED''" in sql
    assert "-- migration: 20260629_001_chunk_sets_serving" in sql
    assert "-- migration: 20260629_002_drop_kb_chunk_set_bindings" in sql
    assert "DROP TABLE rag_kb_chunk_set_bindings" in sql
    assert "-- migration: 20260629_003_ingestion_jobs_settings_overrides" in sql
    assert "ALTER TABLE rag_ingestion_jobs ADD (settings_overrides JSON)" in sql
    assert "-- migration: 20260629_004_documents_processing_config" in sql
    assert "ALTER TABLE rag_documents ADD (processing_config JSON)" in sql
    assert "-- migration: 20260630_001_default_knowledge_base_name" in sql
    assert "WHERE name = '既定ナレッジベース'" in sql
    assert "name = 'DEFAULT'" in sql
    assert "-- migration: 20260630_002_default_business_view" in sql
    assert "JSON_MERGEPATCH" in sql
    assert "JSON_ARRAY(kb.knowledge_base_id RETURNING JSON)" in sql
    assert "INSERT INTO rag_business_views" in sql
    assert "-- migration: 20260630_003_document_recipes" in sql
    assert "CREATE TABLE rag_document_recipes" in sql
    assert "RAG_CHUNK_SETS_RECIPE_ACTIVE_UIDX" in sql
    assert len(statements) == 45
    assert all(
        statement.startswith(("-- migration:", "DECLARE", "INSERT", "MERGE", "UPDATE", "COMMIT"))
        for statement in statements
    )


def test_oracle_schema_migration_manifest_is_deterministic() -> None:
    """migration manifest は artifact hash と migration 単位の hash を含む。"""
    sql = oracle_schema.oracle_schema_migration_sql()
    manifest = oracle_schema.oracle_schema_migration_manifest()

    assert manifest == oracle_schema.oracle_schema_migration_manifest()
    assert manifest["schema_name"] == "production-ready-rag-oracle-26ai"
    assert manifest["schema_version"] == "1"
    assert manifest["artifact_type"] == "migration"
    assert manifest["migration_artifact_version"] == "20260630_003"
    assert manifest["sha256"] == hashlib.sha256(sql.encode("utf-8")).hexdigest()
    assert manifest["statement_count"] == len(oracle_schema.split_sql_statements(sql))
    assert [migration["name"] for migration in manifest["migrations"]] == [
        "20260615_001_ingestion_jobs_attempt_counters",
        "20260616_001_search_audit_search_mode",
        "20260616_002_evaluation_runs_result_sha256",
        "20260616_003_ingestion_jobs_cancelled_status",
        "20260616_004_ingestion_segments",
        "20260616_005_search_audit_memory_engineering",
        "20260616_006_agent_memories",
        "20260617_001_ingestion_audit_file_processing_metrics",
        "20260617_002_search_audit_adaptive_context",
        "20260617_003_search_audit_dependency_context",
        "20260618_001_documents_review_status",
        "20260618_002_ingestion_jobs_phase",
        "20260619_001_business_views",
        "20260621_001_chunk_sets",
        "20260621_002_document_extractions",
        "20260623_001_nullable_chunk_embeddings",
        "20260625_001_chunks_text_world_lexer",
        "20260625_002_preprocess_artifact",
        "20260627_001_documents_preprocessed_status",
        "20260629_001_chunk_sets_serving",
        "20260629_002_drop_kb_chunk_set_bindings",
        "20260629_003_ingestion_jobs_settings_overrides",
        "20260629_004_documents_processing_config",
        "20260630_001_default_knowledge_base_name",
        "20260630_002_default_business_view",
        "20260630_003_document_recipes",
    ]


def test_oracle_schema_cli_writes_sql_and_manifest(tmp_path: Path) -> None:
    """CLI は SQL と manifest を staging artifact として保存できる。"""
    sql_output = tmp_path / "artifacts" / "oracle-schema.sql"
    manifest_output = tmp_path / "artifacts" / "oracle-schema.manifest.json"

    exit_code = oracle_schema.main(
        [
            "--output",
            str(sql_output),
            "--manifest-output",
            str(manifest_output),
        ]
    )

    assert exit_code == 0
    assert "CREATE TABLE rag_documents" in sql_output.read_text(encoding="utf-8")
    manifest = json.loads(manifest_output.read_text(encoding="utf-8"))
    assert (
        manifest["sha256"]
        == hashlib.sha256(sql_output.read_text(encoding="utf-8").encode("utf-8")).hexdigest()
    )


def test_oracle_schema_cli_writes_migration_sql_and_manifest(tmp_path: Path) -> None:
    """CLI は既存 schema 用 migration artifact も保存できる。"""
    sql_output = tmp_path / "artifacts" / "oracle-schema-migration.sql"
    manifest_output = tmp_path / "artifacts" / "oracle-schema-migration.manifest.json"

    exit_code = oracle_schema.main(
        [
            "--migration",
            "--output",
            str(sql_output),
            "--manifest-output",
            str(manifest_output),
        ]
    )

    assert exit_code == 0
    migration_sql = sql_output.read_text(encoding="utf-8")
    assert "MAX_ATTEMPTS" in migration_sql
    assert "SEARCH_MODE" in migration_sql
    assert "RESULT_SHA256" in migration_sql
    manifest = json.loads(manifest_output.read_text(encoding="utf-8"))
    assert manifest["artifact_type"] == "migration"
    assert (
        manifest["sha256"]
        == hashlib.sha256(sql_output.read_text(encoding="utf-8").encode("utf-8")).hexdigest()
    )


def test_oracle_schema_cli_manifest_only_prints_json(
    capsys: CaptureFixture[str],
) -> None:
    """--manifest-only は SQL を出さず manifest JSON だけを stdout に出す。"""
    exit_code = oracle_schema.main(["--manifest-only"])

    captured = capsys.readouterr()
    assert exit_code == 0
    assert captured.err == ""
    assert "CREATE TABLE" not in captured.out
    manifest = json.loads(captured.out)
    assert manifest["vector_contract"] == "VECTOR(1536, FLOAT32)"


def test_oracle_schema_cli_migration_manifest_only_prints_json(
    capsys: CaptureFixture[str],
) -> None:
    """--migration --manifest-only は migration manifest だけを stdout に出す。"""
    exit_code = oracle_schema.main(["--migration", "--manifest-only"])

    captured = capsys.readouterr()
    assert exit_code == 0
    assert captured.err == ""
    assert "ALTER TABLE" not in captured.out
    manifest = json.loads(captured.out)
    assert manifest["artifact_type"] == "migration"


def test_vector_index_reindex_sql_reflects_profile_build_params() -> None:
    """再作成 SQL は渡された profile のビルド推奨値を反映する(DROP + CREATE)。"""
    sql = oracle_schema.vector_index_reindex_sql(
        target_accuracy=98,
        neighbors=48,
        efconstruction=800,
    )

    assert "DROP INDEX rag_chunks_embedding_hnsw_idx;" in sql
    assert "CREATE VECTOR INDEX rag_chunks_embedding_hnsw_idx" in sql
    assert "ON rag_chunks (embedding)" in sql
    assert "WITH TARGET ACCURACY 98" in sql
    assert "NEIGHBORS 48" in sql
    assert "EFCONSTRUCTION 800" in sql
    assert "DISTANCE COSINE" in sql


def test_vector_index_reindex_sql_balanced_uses_current_build() -> None:
    """balanced 既定値では現行 HNSW ビルド(32/500)を出力する。"""
    sql = oracle_schema.vector_index_reindex_sql(
        target_accuracy=95,
        neighbors=32,
        efconstruction=500,
    )

    assert "WITH TARGET ACCURACY 95" in sql
    assert "NEIGHBORS 32" in sql
    assert "EFCONSTRUCTION 500" in sql
