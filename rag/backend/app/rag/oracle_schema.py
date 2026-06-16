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
    oracle_document_schema_sql,
    oracle_evaluation_artifact_schema_sql,
    oracle_feedback_schema_sql,
    oracle_ingestion_audit_schema_sql,
    oracle_ingestion_job_schema_sql,
    oracle_knowledge_base_schema_sql,
    oracle_knowledge_graph_schema_sql,
    oracle_search_audit_schema_sql,
    oracle_vector_schema_sql,
)

SCHEMA_NAME = "production-ready-rag-oracle-26ai"
SCHEMA_VERSION = "1"
MIGRATION_ARTIFACT_VERSION = "20260616_001"
VECTOR_CONTRACT = "VECTOR(1536, FLOAT32)"
VECTOR_INDEX_CONTRACT = {
    "type": "HNSW",
    "distance": "COSINE",
    "target_accuracy": 95,
    "neighbors": 32,
    "efconstruction": 500,
}


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
            name="ingestion_jobs",
            table_name="rag_ingestion_jobs",
            sql=oracle_ingestion_job_schema_sql(),
        ),
        OracleSchemaSection(
            name="chunks",
            table_name="rag_chunks",
            sql=oracle_vector_schema_sql(),
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
