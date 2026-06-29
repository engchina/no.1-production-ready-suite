"""Oracle 26ai schema artifact generator.

Oracle DDL は staging / production でレビュー済み artifact として適用する。
この CLI はアプリ内の DDL 契約から deterministic な SQL と manifest を生成する。
"""

import argparse
import hashlib
import json
import sys
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from app.clients.oracle import (
    ORACLE_TEXT_LEXER,
    oracle_agent_memory_schema_sql,
    oracle_business_view_schema_sql,
    oracle_chunk_set_schema_sql,
    oracle_conversation_schema_sql,
    oracle_document_extractions_schema_sql,
    oracle_document_schema_sql,
    oracle_evaluation_artifact_schema_sql,
    oracle_feedback_schema_sql,
    oracle_ingestion_audit_schema_sql,
    oracle_ingestion_job_schema_sql,
    oracle_ingestion_segment_schema_sql,
    oracle_knowledge_base_schema_sql,
    oracle_knowledge_graph_schema_sql,
    oracle_message_schema_sql,
    oracle_search_audit_schema_sql,
    oracle_text_index_parameters_sql,
    oracle_text_preferences_sql,
    oracle_vector_schema_sql,
)

SCHEMA_NAME = "production-ready-rag-oracle-26ai"
SCHEMA_VERSION = "1"
MIGRATION_ARTIFACT_VERSION = "20260629_003"
VECTOR_CONTRACT = "VECTOR(1536, FLOAT32)"
VECTOR_INDEX_CONTRACT = {
    "type": "HNSW",
    "distance": "COSINE",
    "target_accuracy": 95,
    "neighbors": 32,
    "efconstruction": 500,
}


def vector_index_reindex_sql(
    *,
    target_accuracy: int,
    neighbors: int,
    efconstruction: int,
    distance: str = "COSINE",
    table: str = "rag_chunks",
    index: str = "rag_chunks_embedding_hnsw_idx",
) -> str:
    """選択 profile のビルド推奨値で HNSW 索引を再作成する DDL を返す。

    backend は実行時に DDL を実行しないため、ここでは適用用の SQL を生成するだけにする
    (DBA がレビュー済み artifact として適用する)。引数は ``resolve_vector_index_adapter``
    で解決済みの値を呼び出し側が渡す。
    ponytail: 主検索索引 rag_chunks のみ。agent memory 索引(同形状)は必要時に追従。
    """
    return (
        f"DROP INDEX {index};\n"
        f"CREATE VECTOR INDEX {index}\n"
        f"    ON {table} (embedding)\n"
        f"    ORGANIZATION INMEMORY NEIGHBOR GRAPH\n"
        f"    DISTANCE {distance}\n"
        f"    WITH TARGET ACCURACY {int(target_accuracy)}\n"
        f"    PARAMETERS (\n"
        f"        TYPE HNSW,\n"
        f"        NEIGHBORS {int(neighbors)},\n"
        f"        EFCONSTRUCTION {int(efconstruction)}\n"
        f"    );"
    )


@dataclass(frozen=True)
class OracleSchemaSection:
    """Oracle schema artifact の論理セクション。"""

    name: str
    table_name: str
    sql: str


def oracle_schema_sections() -> list[OracleSchemaSection]:
    """production RAG に必要な Oracle schema section を順序付きで返す。"""
    return [
        OracleSchemaSection(
            name="documents",
            table_name="rag_documents",
            sql=oracle_document_schema_sql(),
        ),
        OracleSchemaSection(
            name="knowledge_bases",
            table_name="rag_knowledge_bases",
            sql=oracle_knowledge_base_schema_sql(),
        ),
        OracleSchemaSection(
            name="business_views",
            table_name="rag_business_views",
            sql=oracle_business_view_schema_sql(),
        ),
        OracleSchemaSection(
            name="conversations",
            table_name="rag_conversations",
            sql=oracle_conversation_schema_sql(),
        ),
        OracleSchemaSection(
            name="messages",
            table_name="rag_messages",
            sql=oracle_message_schema_sql(),
        ),
        OracleSchemaSection(
            name="ingestion_jobs",
            table_name="rag_ingestion_jobs",
            sql=oracle_ingestion_job_schema_sql(),
        ),
        OracleSchemaSection(
            name="ingestion_segments",
            table_name="rag_ingestion_segments",
            sql=oracle_ingestion_segment_schema_sql(),
        ),
        OracleSchemaSection(
            name="chunks",
            table_name="rag_chunks",
            sql=oracle_vector_schema_sql(),
        ),
        OracleSchemaSection(
            name="chunk_sets",
            table_name="rag_chunk_sets",
            sql=oracle_chunk_set_schema_sql(),
        ),
        OracleSchemaSection(
            name="document_extractions",
            table_name="rag_document_extractions",
            sql=oracle_document_extractions_schema_sql(),
        ),
        OracleSchemaSection(
            name="search_audit",
            table_name="rag_search_audit",
            sql=oracle_search_audit_schema_sql(),
        ),
        OracleSchemaSection(
            name="ingestion_audit",
            table_name="rag_ingestion_audit",
            sql=oracle_ingestion_audit_schema_sql(),
        ),
        OracleSchemaSection(
            name="knowledge_graph",
            table_name="rag_graph_entities",
            sql=oracle_knowledge_graph_schema_sql(),
        ),
        OracleSchemaSection(
            name="agent_memory",
            table_name="rag_agent_memories",
            sql=oracle_agent_memory_schema_sql(),
        ),
        OracleSchemaSection(
            name="citation_feedback",
            table_name="rag_citation_feedback",
            sql=oracle_feedback_schema_sql(),
        ),
        OracleSchemaSection(
            name="evaluation_artifacts",
            table_name="rag_evaluation_runs",
            sql=oracle_evaluation_artifact_schema_sql(),
        ),
    ]


def oracle_schema_sql(sections: Sequence[OracleSchemaSection] | None = None) -> str:
    """SQLcl 等で適用できる Oracle schema SQL artifact を返す。"""
    resolved_sections = list(sections or oracle_schema_sections())
    return (
        "\n\n".join(
            f"-- section: {section.name}\n{section.sql.rstrip()}" for section in resolved_sections
        )
        + "\n"
    )


def oracle_schema_migration_sections() -> list[OracleSchemaSection]:
    """既存 Oracle schema を現行 DDL 契約へ寄せる migration section を返す。"""
    return [
        OracleSchemaSection(
            name="20260615_001_ingestion_jobs_attempt_counters",
            table_name="rag_ingestion_jobs",
            sql=_ingestion_jobs_attempt_counters_migration_sql(),
        ),
        OracleSchemaSection(
            name="20260616_001_search_audit_search_mode",
            table_name="rag_search_audit",
            sql=_search_audit_search_mode_migration_sql(),
        ),
        OracleSchemaSection(
            name="20260616_002_evaluation_runs_result_sha256",
            table_name="rag_evaluation_runs",
            sql=_evaluation_runs_result_sha256_migration_sql(),
        ),
        OracleSchemaSection(
            name="20260616_003_ingestion_jobs_cancelled_status",
            table_name="rag_ingestion_jobs",
            sql=_ingestion_jobs_cancelled_status_migration_sql(),
        ),
        OracleSchemaSection(
            name="20260616_004_ingestion_segments",
            table_name="rag_ingestion_segments",
            sql=_ingestion_segments_migration_sql(),
        ),
        OracleSchemaSection(
            name="20260616_005_search_audit_memory_engineering",
            table_name="rag_search_audit",
            sql=_search_audit_memory_engineering_migration_sql(),
        ),
        OracleSchemaSection(
            name="20260616_006_agent_memories",
            table_name="rag_agent_memories",
            sql=_agent_memories_migration_sql(),
        ),
        OracleSchemaSection(
            name="20260617_001_ingestion_audit_file_processing_metrics",
            table_name="rag_ingestion_audit",
            sql=_ingestion_audit_file_processing_metrics_migration_sql(),
        ),
        OracleSchemaSection(
            name="20260617_002_search_audit_adaptive_context",
            table_name="rag_search_audit",
            sql=_search_audit_adaptive_context_migration_sql(),
        ),
        OracleSchemaSection(
            name="20260617_003_search_audit_dependency_context",
            table_name="rag_search_audit",
            sql=_search_audit_dependency_context_migration_sql(),
        ),
        OracleSchemaSection(
            name="20260618_001_documents_review_status",
            table_name="rag_documents",
            sql=_documents_review_status_migration_sql(),
        ),
        OracleSchemaSection(
            name="20260618_002_ingestion_jobs_phase",
            table_name="rag_ingestion_jobs",
            sql=_ingestion_jobs_phase_migration_sql(),
        ),
        OracleSchemaSection(
            name="20260619_001_business_views",
            table_name="rag_business_views",
            sql=_business_views_migration_sql(),
        ),
        OracleSchemaSection(
            name="20260621_001_chunk_sets",
            table_name="rag_chunk_sets",
            sql=_chunk_sets_migration_sql(),
        ),
        OracleSchemaSection(
            name="20260621_002_document_extractions",
            table_name="rag_document_extractions",
            sql=_document_extractions_migration_sql(),
        ),
        OracleSchemaSection(
            name="20260623_001_nullable_chunk_embeddings",
            table_name="rag_chunks",
            sql=_nullable_chunk_embeddings_migration_sql(),
        ),
        OracleSchemaSection(
            name="20260625_001_chunks_text_world_lexer",
            table_name="rag_chunks",
            sql=_chunks_text_world_lexer_migration_sql(),
        ),
        OracleSchemaSection(
            name="20260625_002_preprocess_artifact",
            table_name="rag_documents",
            sql=_documents_preprocess_artifact_migration_sql(),
        ),
        OracleSchemaSection(
            name="20260627_001_documents_preprocessed_status",
            table_name="rag_documents",
            sql=_documents_preprocessed_status_migration_sql(),
        ),
        OracleSchemaSection(
            name="20260629_001_chunk_sets_serving",
            table_name="rag_chunk_sets",
            sql=_chunk_sets_serving_migration_sql(),
        ),
        OracleSchemaSection(
            name="20260629_002_drop_kb_chunk_set_bindings",
            table_name="rag_kb_chunk_set_bindings",
            sql=_drop_kb_chunk_set_bindings_migration_sql(),
        ),
        OracleSchemaSection(
            name="20260629_003_ingestion_jobs_settings_overrides",
            table_name="rag_ingestion_jobs",
            sql=_ingestion_jobs_settings_overrides_migration_sql(),
        ),
    ]


def oracle_schema_migration_sql(
    sections: Sequence[OracleSchemaSection] | None = None,
) -> str:
    """SQLcl 等で適用できる Oracle schema migration artifact を返す。"""
    resolved_sections = list(sections or oracle_schema_migration_sections())
    return (
        "\n\n".join(
            f"-- migration: {section.name}\n{section.sql.rstrip()}" for section in resolved_sections
        )
        + "\n"
    )


def oracle_schema_manifest(sections: Sequence[OracleSchemaSection] | None = None) -> dict[str, Any]:
    """schema artifact の監査用 manifest を返す。"""
    resolved_sections = list(sections or oracle_schema_sections())
    sql = oracle_schema_sql(resolved_sections)
    return {
        "schema_name": SCHEMA_NAME,
        "schema_version": SCHEMA_VERSION,
        "vector_contract": VECTOR_CONTRACT,
        "vector_index": VECTOR_INDEX_CONTRACT,
        "sha256": _sha256(sql),
        "statement_count": len(split_sql_statements(sql)),
        "sections": [
            {
                "name": section.name,
                "table_name": section.table_name,
                "sha256": _sha256(section.sql),
                "statement_count": len(split_sql_statements(section.sql)),
            }
            for section in resolved_sections
        ],
    }


def oracle_schema_migration_manifest(
    sections: Sequence[OracleSchemaSection] | None = None,
) -> dict[str, Any]:
    """schema migration artifact の監査用 manifest を返す。"""
    resolved_sections = list(sections or oracle_schema_migration_sections())
    sql = oracle_schema_migration_sql(resolved_sections)
    return {
        "schema_name": SCHEMA_NAME,
        "schema_version": SCHEMA_VERSION,
        "artifact_type": "migration",
        "migration_artifact_version": MIGRATION_ARTIFACT_VERSION,
        "sha256": _sha256(sql),
        "statement_count": len(split_sql_statements(sql)),
        "migrations": [
            {
                "name": section.name,
                "table_name": section.table_name,
                "sha256": _sha256(section.sql),
                "statement_count": len(split_sql_statements(section.sql)),
            }
            for section in resolved_sections
        ],
    }


def split_sql_statements(sql: str) -> list[str]:
    """SQL artifact を statement ごとに分割する。

    通常 DDL はセミコロン終端で分割する。SQLcl 向け PL/SQL block は
    行単独の `/` までを 1 statement として扱う。
    """
    statements: list[str] = []
    current: list[str] = []
    in_plsql_block = False
    for line in sql.splitlines():
        if not line.strip():
            continue
        stripped = line.strip()
        if (
            not in_plsql_block
            and not _current_statement_has_sql(current)
            and _starts_plsql_block(stripped)
        ):
            in_plsql_block = True
        if in_plsql_block and stripped == "/":
            statement = "\n".join(current).strip()
            if statement:
                statements.append(statement)
            current = []
            in_plsql_block = False
            continue
        current.append(line.rstrip())
        if not in_plsql_block and line.rstrip().endswith(";"):
            statement = "\n".join(current).strip()
            statements.append(statement.removesuffix(";").rstrip())
            current = []
    if current:
        statements.append("\n".join(current).strip())
    return statements


def _ingestion_jobs_attempt_counters_migration_sql() -> str:
    """rag_ingestion_jobs の試行回数列を現行 DDL 契約へ合わせる migration SQL。"""
    return """
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
""".strip()


def _search_audit_search_mode_migration_sql() -> str:
    """rag_search_audit の Oracle 予約語 mode 列を search_mode へ寄せる。"""
    return """
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
""".strip()


def _ingestion_jobs_cancelled_status_migration_sql() -> str:
    """rag_ingestion_jobs.status の CHECK constraint に CANCELLED を追加する。"""
    return """
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
""".strip()


def _documents_review_status_migration_sql() -> str:
    """rag_documents.status の CHECK constraint を段階レビュー状態へ更新する。"""
    return """
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
""".strip()


def _ingestion_jobs_phase_migration_sql() -> str:
    """rag_ingestion_jobs に phase 列と PREPROCESS/EXTRACT/CHUNK/INDEX 制約を追加・更新する。"""
    return """
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
""".strip()


def _documents_preprocess_artifact_migration_sql() -> str:
    """rag_documents にファイル準備 artifact JSON と PREPROCESSING 状態を追加する。"""
    return """
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
""".strip()


def _documents_preprocessed_status_migration_sql() -> str:
    """rag_documents.status に PREPROCESSED(ファイル準備後の停止状態)を追加する。"""
    return """
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
""".strip()


def _chunk_sets_serving_migration_sql() -> str:
    """rag_chunk_sets に文書単位 serving フラグ(is_serving)を追加する(冪等)。

    3 層モデル: 配信中(serving)の判定を per-KB binding から文書単位の chunk_set へ移す。
    既存行は per-KB binding の serving 状態から backfill する(別 chunk_set が serving の
    chunk_set だけ 0、それ以外は既定の 1 を維持=安全側で配信を残す)。
    """
    return """
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
""".strip()


def _ingestion_jobs_settings_overrides_migration_sql() -> str:
    """rag_ingestion_jobs に候補レシピ上書き(settings_overrides JSON)列を追加する(冪等)。

    Phase 3b: parser/前処理を変えた実験ジョブが候補レシピを持ち回るための列。
    通常取込ジョブは NULL。
    """
    return """
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
""".strip()


def _drop_kb_chunk_set_bindings_migration_sql() -> str:
    """per-KB binding 表 rag_kb_chunk_set_bindings を退役(冪等 DROP)する。

    3 層モデル: serving は文書単位の rag_chunk_sets.is_serving(20260629_001 で backfill 済)へ
    一本化したため、KB→chunk_set の参照表は不要。DROP TABLE が索引/制約も同時に落とす。
    """
    return """
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
""".strip()


def _evaluation_runs_result_sha256_migration_sql() -> str:
    """rag_evaluation_runs に artifact hash 列を追加する。"""
    return """
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
""".strip()


def _business_views_migration_sql() -> str:
    """rag_business_views(業務ビュー)table を追加する。"""
    return """
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
""".strip()


def _chunk_sets_migration_sql() -> str:
    """variant の chunk_set / KB binding table と rag_chunks.chunk_set_id 列を追加する(冪等)。"""
    return """
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
    FROM user_indexes WHERE index_name = 'RAG_DOC_EXT_STATUS_IDX';

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
""".strip()


def _document_extractions_migration_sql() -> str:
    """extraction 層(rag_document_extractions)+ rag_chunk_sets.extraction_id 列を追加する。

    冪等。データ無し前提のため backfill は不要(extraction_id は preprocess+parser の SHA1 で
    PL/SQL では計算できないため、既存データがある環境ではアプリ側 backfill が必要だが、本環境は
    データ無しのため no-op)。
    """
    return """
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
""".strip()


def _nullable_chunk_embeddings_migration_sql() -> str:
    """CHUNK preview 保存用に rag_chunks.embedding の NOT NULL を外す。"""
    return """
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
""".strip()


def _chunks_text_world_lexer_migration_sql() -> str:
    """rag_chunks の Oracle Text index を多言語向け WORLD_LEXER で再構築する。"""
    parameters = oracle_text_index_parameters_sql().replace("'", "''")
    return f"""
{oracle_text_preferences_sql()}

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
              AND o.ixo_object = '{ORACLE_TEXT_LEXER}'
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
                || '{parameters}';
        END IF;
    END IF;
END;
/
""".strip()


def _ingestion_segments_migration_sql() -> str:
    """rag_ingestion_segments checkpoint table を追加する。"""
    return """
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
    WHERE index_name = 'RAG_INGESTION_SEGMENTS_DOC_STATUS_IDX';

    IF v_index_count = 0 THEN
        EXECUTE IMMEDIATE
            'CREATE INDEX rag_ingestion_segments_doc_status_idx '
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
""".strip()


def _search_audit_memory_engineering_migration_sql() -> str:
    """rag_search_audit に AIDB Memory Engineering の低機密集計列を追加する。"""
    return """
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
    """.strip()


def _search_audit_adaptive_context_migration_sql() -> str:
    """rag_search_audit に adaptive context expansion の低機密集計列を追加する。"""
    return """
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
""".strip()


def _search_audit_dependency_context_migration_sql() -> str:
    """rag_search_audit に dependency-linked context promotion の集計列を追加する。"""
    return """
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
""".strip()


def _agent_memories_migration_sql() -> str:
    """Oracle 26ai 内に scoped Agent Memory table / index を追加する。"""
    return """
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
""".strip()


def _ingestion_audit_file_processing_metrics_migration_sql() -> str:
    """rag_ingestion_audit に file-processing 観測用の低機密集計列を追加する。"""
    return """
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
""".strip()


def _current_statement_has_sql(lines: Sequence[str]) -> bool:
    return any(line.strip() and not line.strip().startswith("--") for line in lines)


def _starts_plsql_block(stripped_line: str) -> bool:
    upper = stripped_line.upper()
    return upper == "DECLARE" or upper == "BEGIN" or upper.startswith(("DECLARE ", "BEGIN "))


def main(argv: Sequence[str] | None = None) -> int:
    """CLI entrypoint。"""
    parser = _build_parser()
    args = parser.parse_args(argv)
    if args.migration:
        sections = oracle_schema_migration_sections()
        sql = oracle_schema_migration_sql(sections)
        manifest = oracle_schema_migration_manifest(sections)
    else:
        sections = oracle_schema_sections()
        sql = oracle_schema_sql(sections)
        manifest = oracle_schema_manifest(sections)

    if args.manifest_only:
        _write_json(manifest, args.manifest_output)
        return 0

    _write_text(sql, args.output)
    if args.manifest_output is not None:
        _write_json(manifest, args.manifest_output)
    return 0


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="rag-oracle-schema",
        description="Oracle 26ai 用 RAG schema SQL と監査 manifest を生成します。",
    )
    parser.add_argument(
        "--output",
        type=Path,
        help="SQL artifact の保存先。未指定なら stdout に出力します。",
    )
    parser.add_argument(
        "--manifest-output",
        type=Path,
        help="manifest JSON の保存先。--manifest-only では未指定なら stdout に出力します。",
    )
    parser.add_argument(
        "--manifest-only",
        action="store_true",
        help="SQL ではなく manifest JSON だけを出力します。",
    )
    parser.add_argument(
        "--migration",
        action="store_true",
        help="新規 schema DDL ではなく既存 schema 用 migration SQL を出力します。",
    )
    return parser


def _write_text(content: str, output_path: Path | None) -> None:
    if output_path is None:
        print(content, end="")
        return
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(content, encoding="utf-8")


def _write_json(payload: dict[str, Any], output_path: Path | None) -> None:
    serialized = json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    _write_text(serialized, output_path)


def _sha256(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
