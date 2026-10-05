"""Oracle AI Database schema artifact generator.

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
    oracle_answer_prompt_schema_sql,
    oracle_answer_record_schema_sql,
    oracle_chunk_set_schema_sql,
    oracle_conversation_schema_sql,
    oracle_document_recipe_schema_sql,
    oracle_document_schema_sql,
    oracle_document_sections_schema_sql,
    oracle_evaluation_artifact_schema_sql,
    oracle_evaluation_job_schema_sql,
    oracle_feedback_details_schema_sql,
    oracle_feedback_schema_sql,
    oracle_ingestion_audit_schema_sql,
    oracle_ingestion_job_schema_sql,
    oracle_ingestion_segment_schema_sql,
    oracle_knowledge_base_schema_sql,
    oracle_knowledge_graph_schema_sql,
    oracle_message_schema_sql,
    oracle_query_history_schema_sql,
    oracle_role_access_schema_sql,
    oracle_search_answer_profile_knowledge_schema_sql,
    oracle_search_answer_profile_schema_sql,
    oracle_search_audit_schema_sql,
    oracle_vector_schema_sql,
)
from app.rag.search_answer_profile_migration import rename_sql as search_answer_profile_rename_sql

SCHEMA_NAME = "production-ready-rag-oracle-26ai"
SCHEMA_VERSION = "3"
MIGRATION_ARTIFACT_VERSION = "20261005_001"
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
    対象は主検索索引 rag_chunks だけ。
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
    """Oracle schema artifact の論理セクション。

    `destructive_note` は、データを消す（テーブルの DROP・行の DELETE を含む）migration の印と、
    消えるデータ・適用の前にすることの説明（#619）。空でない migration が未適用なら、
    「作成・更新」（画面・`system_schema_cli initialize`）は明示の承認が無ければ止まり、
    実 Oracle のテストの fixture は当てない。checksum（SQL だけから作る）には含めない。
    """

    name: str
    table_name: str
    sql: str
    destructive_note: str = ""

    @property
    def destructive(self) -> bool:
        return bool(self.destructive_note)


def oracle_system_schema_control_sql() -> str:
    """明示的な schema 操作の lease / migration ledger 用 DDL を返す。"""
    return """
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
""".strip()


def oracle_schema_sections() -> list[OracleSchemaSection]:
    """production RAG に必要な Oracle schema section を順序付きで返す。"""
    return [
        OracleSchemaSection(
            name="system_schema_control",
            table_name="rag_schema_operations",
            sql=oracle_system_schema_control_sql(),
        ),
        OracleSchemaSection(
            name="documents",
            table_name="rag_documents",
            sql=oracle_document_schema_sql(),
        ),
        OracleSchemaSection(
            name="document_recipes",
            table_name="rag_document_recipes",
            sql=oracle_document_recipe_schema_sql(),
        ),
        OracleSchemaSection(
            name="knowledge_bases",
            table_name="rag_knowledge_bases",
            sql=oracle_knowledge_base_schema_sql(),
        ),
        OracleSchemaSection(
            name="search_answer_profiles",
            table_name="rag_search_answer_profiles",
            sql=oracle_search_answer_profile_schema_sql(),
        ),
        OracleSchemaSection(
            name="answer_records",
            table_name="rag_answer_records",
            sql=oracle_answer_record_schema_sql(),
        ),
        OracleSchemaSection(
            name="answer_prompts",
            table_name="rag_answer_prompts",
            sql=oracle_answer_prompt_schema_sql(),
        ),
        OracleSchemaSection(
            name="query_history",
            table_name="rag_query_history",
            sql=oracle_query_history_schema_sql(),
        ),
        OracleSchemaSection(
            name="search_answer_profile_knowledge",
            table_name="rag_search_answer_profile_knowledge",
            sql=oracle_search_answer_profile_knowledge_schema_sql(),
        ),
        OracleSchemaSection(
            name="document_sections",
            table_name="rag_document_sections",
            sql=oracle_document_sections_schema_sql(),
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
            name="feedback_details",
            table_name="rag_feedback_details",
            sql=oracle_feedback_details_schema_sql(),
        ),
        OracleSchemaSection(
            name="evaluation_artifacts",
            table_name="rag_evaluation_runs",
            sql=oracle_evaluation_artifact_schema_sql(),
        ),
        # 品質評価の job（非同期の実行・進捗・取り消し。#390）。
        OracleSchemaSection(
            name="evaluation_jobs",
            table_name="rag_evaluation_jobs",
            sql=oracle_evaluation_job_schema_sql(),
        ),
        # ロールの RAG 権限と対象範囲（#214）。
        # PLATFORM_ROLES と検索・回答プロファイル・KB を参照する。
        OracleSchemaSection(
            name="role_access",
            table_name="rag_role_permissions",
            sql=oracle_role_access_schema_sql(),
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
    """過去の SQL/checksum は archive で固定し、新 migration だけを追加する。"""
    archive = json.loads(
        (Path(__file__).parent / "schema_migrations/pre_profile_rename.json").read_text()
    )
    sections = [OracleSchemaSection(**item) for item in archive["migrations"]]
    sections.append(
        OracleSchemaSection(
            name="20261003_001_search_answer_profiles",
            table_name="rag_search_answer_profiles",
            sql=search_answer_profile_rename_sql(),
            destructive_note=(
                "旧権限 code を新 code へコピーした後、旧 code の grant 行だけを削除します。"
                "実効権限と対象 ID・業務データは保持します。"
                "更新前に schema / 権限を export してください。"
            ),
        )
    )
    sections.append(
        OracleSchemaSection(
            name="20261005_001_message_answer_runs",
            table_name="rag_messages",
            sql=message_answer_runs_migration_sql(),
        )
    )
    return sections


def message_answer_runs_migration_sql() -> str:
    """作成中の回答の段階・プロセス・heartbeat の列と、停止（CANCELLED）の状態を足す（#1175）。

    既存の rag_messages は created_at を含む index を 2 つ持ち、どちらも同じ式
    SYS_EXTRACT_UTC("CREATED_AT") の隠し列を作る。その表に JSON の列を足すと ORA-54015 になるため、
    JSON の列が無いときだけ conversation_created_idx を一時的に消し、列を足してから
    同じ定義で作り直す（#1193）。
    """
    columns = (
        ("LEASE_OWNER", "lease_owner VARCHAR2(128)"),
        ("HEARTBEAT_AT", "heartbeat_at TIMESTAMP WITH TIME ZONE"),
    )
    column_blocks = "\n".join(
        f"""    SELECT COUNT(*) INTO v_count
    FROM user_tab_columns
    WHERE table_name = 'RAG_MESSAGES' AND column_name = '{name}';
    IF v_count = 0 THEN
        EXECUTE IMMEDIATE 'ALTER TABLE rag_messages ADD ({definition})';
    END IF;
"""
        for name, definition in columns
    )
    return f"""DECLARE
    v_count NUMBER;
    v_index_count NUMBER;
BEGIN
    SELECT COUNT(*) INTO v_count
    FROM user_tab_columns
    WHERE table_name = 'RAG_MESSAGES' AND column_name = 'PROGRESS_JSON';
    IF v_count = 0 THEN
        SELECT COUNT(*) INTO v_index_count
        FROM user_indexes
        WHERE index_name = 'RAG_MESSAGES_CONVERSATION_CREATED_IDX';
        IF v_index_count > 0 THEN
            EXECUTE IMMEDIATE 'DROP INDEX rag_messages_conversation_created_idx';
        END IF;
        EXECUTE IMMEDIATE 'ALTER TABLE rag_messages ADD (progress_json JSON)';
        EXECUTE IMMEDIATE
            'CREATE INDEX rag_messages_conversation_created_idx '
            || 'ON rag_messages (conversation_id, created_at)';
    END IF;
{column_blocks}
    SELECT COUNT(*) INTO v_count
    FROM user_constraints
    WHERE table_name = 'RAG_MESSAGES'
      AND constraint_name = 'RAG_MESSAGES_STATUS_CK';
    IF v_count > 0 THEN
        EXECUTE IMMEDIATE 'ALTER TABLE rag_messages DROP CONSTRAINT rag_messages_status_ck';
    END IF;
    EXECUTE IMMEDIATE
        'ALTER TABLE rag_messages ADD CONSTRAINT rag_messages_status_ck CHECK '
        || '(status IN (''STREAMING'', ''COMPLETE'', ''ERROR'', ''CANCELLED''))';
END;
/"""


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
                # データを消す migration（#619）。SQLcl などで直接当てる前に書き出しを確かめる。
                "destructive": section.destructive,
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


# #599 で改名した、保存値の中の名前(旧, 新)。JSON を文字列にしてこの順に置き換える
# (長い名前を先にし、短い名前が長い名前の一部を置き換えないようにする)。
# JSON_SERIALIZE は空白を入れないので、`"key":"value"` の形で key と値の組を書ける。
STORED_NAME_RENAMES_599: tuple[tuple[str, str], ...] = (
    # 分割方式の値
    ("docrag_small_to_big", "small_to_big"),
    # Settings の属性名(取込ジョブの上書き・chunk set の recipe の記録)
    ("rag_docrag_table_child_target_chars", "rag_chunk_table_child_target_chars"),
    ("rag_docrag_child_target_chars", "rag_chunk_child_target_chars"),
    ("rag_docrag_parent_target_chars", "rag_chunk_parent_target_chars"),
    ("rag_docrag_parent_max_children", "rag_chunk_parent_max_children"),
    ("rag_docrag_parent_max_pages", "rag_chunk_parent_max_pages"),
    ("rag_docrag_answer_vision_enabled", "rag_answer_vision_enabled"),
    ("rag_docrag_history_rewrite_enabled", "rag_history_rewrite_enabled"),
    ("rag_docrag_screen_linking_enabled", "rag_screen_linking_enabled"),
    ("rag_docrag_neighbor_child_count", "rag_neighbor_child_count"),
    ("rag_docrag_query_strategy", "rag_query_strategy"),
    ("rag_docrag_rerank_enabled", "rag_rerank_enabled"),
    ("rag_docrag_answer_flow", "rag_answer_flow"),
    ("rag_docrag_profile", "rag_answer_profile"),
    # 文書・レシピの処理設定、KB の構築設定、検索・回答プロファイルの検索・回答設定の key
    ("docrag_table_child_target_chars", "chunk_table_child_target_chars"),
    ("docrag_child_target_chars", "chunk_child_target_chars"),
    ("docrag_parent_target_chars", "chunk_parent_target_chars"),
    ("docrag_parent_max_children", "chunk_parent_max_children"),
    ("docrag_parent_max_pages", "chunk_parent_max_pages"),
    ("docrag_screen_linking_enabled", "screen_linking_enabled"),
    ("docrag_neighbor_child_count", "neighbor_child_count"),
    ("docrag_query_strategy", "query_strategy"),
    ("docrag_rerank_enabled", "rerank_enabled"),
    ("docrag_answer_flow", "answer_flow"),
    # 親子階層の chunk の metadata
    ("docrag_first_page_context_json", "first_page_context_json"),
    ("docrag_source_record_refs_json", "source_record_refs_json"),
    ("docrag_source_seq_ranges_json", "source_seq_ranges_json"),
    ("docrag_metadata_json", "engine_metadata_json"),
    ("docrag_search_text", "engine_search_text"),
    ("docrag_parent_text", "parent_text"),
    ("docrag_chunk_seq", "engine_chunk_seq"),
    ("docrag_chunk_id", "engine_chunk_id"),
    ("docrag_model_used", "evidence_model_used"),
    ("docrag_role", "evidence_role"),
    ("docrag_parent", "small_to_big_parent"),
    ("docling_docrag", "docling_layout"),
    ("docrag_instruction_callout", "instruction_callout"),
    # Docling の解析結果(parser_artifacts)と、縮退の理由
    ("docrag_layout_records", "layout_records"),
    ("docrag_layout_missing", "layout_missing"),
    ("docrag_layout", "layout_records"),
    # 派生情報レイヤーの指紋の入力
    ("docrag_chunk_contract", "chunk_metadata_contract"),
    # 回答の経路・段の名前・診断(品質評価の結果・回答の記録)
    ('"docrag_retrieval_only"', '"retrieval_only"'),
    ('"docrag_grounded"', '"grounded"'),
    ('"docrag_history_rewrite"', '"history_rewrite"'),
    ('"docrag_answer"', '"answer"'),
    ('"retrieval_strategy":"docrag"', '"retrieval_strategy":"hybrid"'),
    ('"answer_engine":"docrag"', '"answer_engine":"grounded"'),
    ('"docrag":', '"answer":'),
)

# 保存値に #599 の前の名前を持ちうる JSON の列(表, 列)。
STORED_NAME_COLUMNS_599: tuple[tuple[str, str], ...] = (
    ("RAG_DOCUMENTS", "PROCESSING_CONFIG"),
    ("RAG_DOCUMENTS", "EXTRACTION"),
    ("RAG_DOCUMENT_RECIPES", "PROCESSING_CONFIG"),
    ("RAG_KNOWLEDGE_BASES", "RETRIEVAL_CONFIG"),
    ("RAG_BUSINESS_VIEWS", "VIEW_CONFIG"),
    ("RAG_INGESTION_JOBS", "SETTINGS_OVERRIDES"),
    ("RAG_INGESTION_JOBS", "QUALITY_WARNINGS"),
    ("RAG_CHUNK_SETS", "RECIPE_SUBSET"),
    ("RAG_CHUNK_SETS", "METRICS_JSON"),
    ("RAG_CHUNK_SETS", "FIRST_PAGE_CONTEXT"),
    ("RAG_DOCUMENT_EXTRACTIONS", "RECIPE_SUBSET"),
    ("RAG_DOCUMENT_EXTRACTIONS", "EXTRACTION_JSON"),
    ("RAG_DOCUMENT_EXTRACTIONS", "METRICS_JSON"),
    ("RAG_ARTIFACT_LAYERS", "INPUT_FINGERPRINT"),
    ("RAG_ARTIFACT_LAYERS", "METRICS_JSON"),
    ("RAG_CHUNKS", "METADATA_JSON"),
    ("RAG_ANSWER_RECORDS", "CITATIONS_JSON"),
    ("RAG_ANSWER_RECORDS", "DIAGNOSTICS_JSON"),
    ("RAG_ANSWER_RECORDS", "EVALUATION_INPUT_JSON"),
    ("RAG_ANSWER_RECORDS", "EVALUATION_JSON"),
    ("RAG_MESSAGES", "CITATIONS_JSON"),
    ("RAG_FEEDBACK_DETAILS", "CITATIONS_JSON"),
    ("RAG_EVALUATION_RUNS", "REQUEST_JSON"),
    ("RAG_EVALUATION_RUNS", "RESULT_JSON"),
    ("RAG_EVALUATION_JOBS", "RESULT_JSON"),
)


def apply_stored_name_renames_599(text: str) -> str:
    """``STORED_NAME_RENAMES_599`` を migration の SQL と同じ順に当てる(テストで照合する)。"""
    for old, new in STORED_NAME_RENAMES_599:
        text = text.replace(old, new)
    return text


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
        description="Oracle AI Database 用 RAG schema SQL と監査 manifest を生成します。",
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
