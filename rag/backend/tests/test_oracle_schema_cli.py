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
    assert "-- section: ingestion_jobs" in sql
    assert "CREATE TABLE rag_ingestion_jobs" in sql
    assert "CREATE TABLE rag_chunks" in sql
    assert "embedding       VECTOR(1536, FLOAT32)" in sql
    assert "CREATE VECTOR INDEX rag_chunks_embedding_hnsw_idx" in sql
    assert "TYPE HNSW" in sql
    assert "WITH TARGET ACCURACY 95" in sql
    assert "CREATE TABLE rag_search_audit" in sql
    assert "CREATE TABLE rag_ingestion_audit" in sql
    assert "-- section: knowledge_graph" in sql
    assert "CREATE TABLE rag_graph_entities" in sql
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
        "knowledge_bases",
        "ingestion_jobs",
        "chunks",
        "search_audit",
        "ingestion_audit",
        "knowledge_graph",
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
    assert len(statements) == 6
    assert all(statement.startswith(("-- migration:", "DECLARE")) for statement in statements)


def test_oracle_schema_migration_manifest_is_deterministic() -> None:
    """migration manifest は artifact hash と migration 単位の hash を含む。"""
    sql = oracle_schema.oracle_schema_migration_sql()
    manifest = oracle_schema.oracle_schema_migration_manifest()

    assert manifest == oracle_schema.oracle_schema_migration_manifest()
    assert manifest["schema_name"] == "production-ready-rag-oracle-26ai"
    assert manifest["schema_version"] == "1"
    assert manifest["artifact_type"] == "migration"
    assert manifest["migration_artifact_version"] == "20260616_003"
    assert manifest["sha256"] == hashlib.sha256(sql.encode("utf-8")).hexdigest()
    assert manifest["statement_count"] == len(oracle_schema.split_sql_statements(sql))
    assert [migration["name"] for migration in manifest["migrations"]] == [
        "20260615_001_ingestion_jobs_attempt_counters",
        "20260616_001_search_audit_search_mode",
        "20260616_002_evaluation_runs_result_sha256",
        "20260616_003_ingestion_jobs_cancelled_status",
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
