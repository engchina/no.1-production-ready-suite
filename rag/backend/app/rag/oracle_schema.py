"""Oracle AI Database schema artifact generator.

Oracle DDL は staging / production でレビュー済み artifact として適用する。
この CLI はアプリ内の DDL 契約から deterministic な SQL と manifest を生成する。
"""

import argparse
import hashlib
import json
import re
import sys
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from app.clients.document_category_store import oracle_document_category_schema_sql
from app.clients.document_cleanup_store import oracle_document_cleanup_schema_sql
from app.clients.entity_store import oracle_entity_schema_sql
from app.clients.evaluation_testset_store import oracle_evaluation_testset_schema_sql
from app.clients.evaluation_workbook_store import oracle_evaluation_source_workbook_schema_sql
from app.clients.extraction_revision_store import oracle_extraction_revision_schema_sql
from app.clients.graph_reference_store import graph_reference_schema_sql
from app.clients.language_pack_release_store import language_pack_release_statements
from app.clients.native_hybrid_schema import (
    native_hybrid_ivf_migration_sql,
    native_hybrid_migration_sql,
    native_hybrid_missing_scope_migration_sql,
    native_hybrid_objects_sql,
    native_hybrid_scope_migration_sql,
    native_hybrid_scope_objects_sql,
    native_hybrid_sync_columns_migration_sql,
    native_hybrid_text_search_migration_sql,
)
from app.clients.native_maintenance_schema import (
    native_maintenance_activation_sql,
    native_maintenance_tables_sql,
)
from app.clients.oracle import (
    oracle_answer_prompt_schema_sql,
    oracle_answer_record_schema_sql,
    oracle_chunk_set_schema_sql,
    oracle_conversation_schema_sql,
    oracle_document_recipe_schema_sql,
    oracle_document_schema_sql,
    oracle_document_sections_schema_sql,
    oracle_evaluation_artifact_schema_sql,
    oracle_evaluation_job_case_result_schema_sql,
    oracle_evaluation_job_schema_sql,
    oracle_feedback_details_schema_sql,
    oracle_feedback_schema_sql,
    oracle_graph_name_character_migration_sql,
    oracle_ingestion_audit_schema_sql,
    oracle_ingestion_job_schema_sql,
    oracle_ingestion_segment_schema_sql,
    oracle_knowledge_base_schema_sql,
    oracle_knowledge_graph_schema_sql,
    oracle_message_schema_sql,
    oracle_role_access_schema_sql,
    oracle_search_answer_profile_knowledge_schema_sql,
    oracle_search_answer_profile_schema_sql,
    oracle_search_audit_schema_sql,
    oracle_vector_index_create_sql,
    oracle_vector_schema_sql,
)
from app.clients.oracle_run_history import oracle_run_history_schema_sql
from app.clients.recipe_version_log import recipe_version_log_schema_sql
from app.clients.section_map_store import oracle_section_map_schema_sql
from app.clients.section_revision_store import oracle_section_revision_schema_sql
from app.clients.structure_index_store import structure_index_schema_sql
from app.clients.support_guide_store import (
    oracle_support_guide_embedding_schema_sql,
    oracle_support_guide_revision_schema_sql,
    oracle_support_guide_schema_sql,
)
from app.rag.ingestion_language import language_pack_artifact_statements
from app.rag.language_pack_retired_fields import (
    RETIRED_4784_FIELDS,
    RETIRED_ANSWER_CHECK_CUE_FIELDS,
    RETIRED_ANSWER_LABEL_FIELDS,
    RETIRED_CRAG_ANSWER_LABEL_FIELDS,
    RETIRED_EVIDENCE_TAG_FIELDS,
    RETIRED_QUESTION_CUE_FIELDS,
    RETIRED_QUESTION_WORD_FIELDS,
    RETIRED_SECTIONS,
    RETIRED_UNREAD_FIELDS,
    RETIRED_UNREAD_SECTIONS,
    remove_retired_language_pack_fields,
)
from app.rag.prompt_overrides_4782_generation_migration import removed_prompt_overrides_sql
from app.rag.request_context import with_system_access
from app.rag.runtime_rules_4799_migration import removed_runtime_rules_sql
from app.rag.search_answer_profile_migration import rename_sql as search_answer_profile_rename_sql
from app.rag.search_answer_profile_retired_fields import (
    KEPT_LANGUAGE_PACK_SECTIONS,
    KEPT_QUERY_FIELDS,
    remove_retired_search_answer_profile_fields,
)
from app.rag.vector_index_adapter import default_vector_index_build

SCHEMA_NAME = "production-ready-rag-oracle-26ai"
SCHEMA_VERSION = "3"
MIGRATION_ARTIFACT_VERSION = "20261009_001"
VECTOR_CONTRACT = "VECTOR(1536, FLOAT32)"
# 新しい環境の主検索索引の契約(作成の DDL と同じく、既定の検索精度の推奨ビルドから作る。#1990)。
_DEFAULT_VECTOR_INDEX_BUILD = default_vector_index_build()
VECTOR_INDEX_CONTRACT = {
    "type": "HNSW",
    "distance": _DEFAULT_VECTOR_INDEX_BUILD.distance,
    "target_accuracy": _DEFAULT_VECTOR_INDEX_BUILD.target_accuracy,
    "neighbors": _DEFAULT_VECTOR_INDEX_BUILD.neighbors,
    "efconstruction": _DEFAULT_VECTOR_INDEX_BUILD.efconstruction,
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
    create_sql = oracle_vector_index_create_sql(
        table=table,
        index=index,
        target_accuracy=target_accuracy,
        neighbors=neighbors,
        efconstruction=efconstruction,
        distance=distance,
    )
    return f"DROP INDEX {index};\n{create_sql}"


@dataclass(frozen=True)
class OracleSchemaSection:
    """Oracle schema artifact の論理セクション。

    `destructive_note` は、データを消す（テーブルの DROP・行の DELETE を含む）migration の印と、
    消えるデータ・適用の前にすることの説明（#619）。空でない migration が未適用なら、
    「作成・更新」（画面・`system_schema_cli initialize`）は明示の承認が無ければ止まり、
    実 Oracle のテストの fixture は当てない。checksum（SQL だけから作る）には含めない。

    `data_step` は SQL だけでは書けないデータの移行（保存値から hash を計算し直すなど）。
    `sql` の後に同じ接続で呼ぶ。途中で止まって流し直しても同じ結果になるように書く。
    未初期化の DB では呼ばない。
    """

    name: str
    table_name: str
    sql: str
    destructive_note: str = ""
    data_step: Callable[[Any], None] | None = None

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
            name="language_pack_releases",
            table_name="rag_language_pack_releases",
            sql=language_pack_release_statements(),
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
            name="structure_index_jobs",
            table_name="rag_structure_index_jobs",
            sql=structure_index_schema_sql(),
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
        # 実行履歴（チャット・RAG 検索の実行。#1890）。
        OracleSchemaSection(
            name="run_history",
            table_name="rag_run_history",
            sql=oracle_run_history_schema_sql(),
        ),
        # 文書の分類の一覧（大分類 → 中分類 → 小分類。#2131）。
        OracleSchemaSection(
            name="document_categories",
            table_name="rag_document_categories",
            sql=oracle_document_category_schema_sql(),
        ),
        # 文書の削除の後始末（保存先から消せなかった原本・成果物）の記録（#2452）。
        OracleSchemaSection(
            name="document_cleanups",
            table_name="rag_document_cleanups",
            sql=oracle_document_cleanup_schema_sql(),
        ),
        OracleSchemaSection(
            name="search_answer_profile_knowledge",
            table_name="rag_search_answer_profile_knowledge",
            sql=oracle_search_answer_profile_knowledge_schema_sql(),
        ),
        # 業務ガイド（#1237）。頭（下書き）と公開の版。
        OracleSchemaSection(
            name="support_guides",
            table_name="rag_support_guides",
            sql=oracle_support_guide_schema_sql(),
        ),
        OracleSchemaSection(
            name="support_guide_revisions",
            table_name="rag_support_guide_revisions",
            sql=oracle_support_guide_revision_schema_sql(),
        ),
        # 業務ガイドの公開の版の質問の例の埋め込み（意味の照合。#2398）。
        OracleSchemaSection(
            name="support_guide_embeddings",
            table_name="rag_support_guide_embeddings",
            sql=oracle_support_guide_embedding_schema_sql(),
        ),
        OracleSchemaSection(
            name="document_sections",
            table_name="rag_document_sections",
            sql=oracle_document_sections_schema_sql(),
        ),
        OracleSchemaSection(
            name="document_section_revisions",
            table_name="rag_document_section_revisions",
            sql=oracle_section_revision_schema_sql(),
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
            name="native_hybrid", table_name="rag_chunks", sql=native_hybrid_objects_sql()
        ),
        OracleSchemaSection(
            name="chunk_sets",
            table_name="rag_chunk_sets",
            sql=oracle_chunk_set_schema_sql(),
        ),
        OracleSchemaSection(
            name="native_maintenance",
            table_name="rag_native_index_maintenance",
            sql=native_maintenance_tables_sql() + "\n\n" + native_maintenance_activation_sql(),
        ),
        OracleSchemaSection(
            name="native_search_scopes",
            table_name="rag_native_search_scopes",
            sql=native_hybrid_scope_objects_sql(),
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
            name="graph_references",
            table_name="rag_graph_references",
            sql=graph_reference_schema_sql(),
        ),
        # 親の行を消す FK の子の列の索引（#4628）。親の表をすべて作った後に置く。
        OracleSchemaSection(
            name="foreign_key_lock_indexes",
            table_name="rag_graph_references",
            sql=foreign_key_lock_index_sql(),
        ),
        OracleSchemaSection(
            name="document_extraction_revisions",
            table_name="rag_document_extraction_revisions",
            sql=oracle_extraction_revision_schema_sql(),
        ),
        # 実体の層（#1362）。実体・別名・実体と chunk の関連。rag_chunk_sets / rag_chunks
        # を参照する。
        OracleSchemaSection(
            name="entities",
            table_name="rag_entities",
            sql=oracle_entity_schema_sql(),
        ),
        # 章節と chunk の対応（#1438）。rag_chunks を参照する。
        OracleSchemaSection(
            name="section_map",
            table_name="rag_chunk_set_sections",
            sql=oracle_section_map_schema_sql(),
        ),
        # 切替の履歴（#4167）。文書とレシピを参照する。
        OracleSchemaSection(
            name="recipe_version_log",
            table_name="rag_recipe_version_log",
            sql=recipe_version_log_schema_sql(),
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
        # 品質評価にアップロードした Excel（#4212）。job が参照するので先に作る。
        OracleSchemaSection(
            name="evaluation_source_workbooks",
            table_name="rag_evaluation_source_workbooks",
            sql=oracle_evaluation_source_workbook_schema_sql(),
        ),
        # 品質評価の job（非同期の実行・進捗・取り消し。#390）。
        OracleSchemaSection(
            name="evaluation_jobs",
            table_name="rag_evaluation_jobs",
            sql=oracle_evaluation_job_schema_sql(),
        ),
        # 品質評価の job のケースごとの結果（#4208）。
        OracleSchemaSection(
            name="evaluation_job_case_results",
            table_name="rag_evaluation_job_case_results",
            sql=oracle_evaluation_job_case_result_schema_sql(),
        ),
        OracleSchemaSection(
            name="evaluation_testsets",
            table_name="rag_evaluation_testsets",
            sql=oracle_evaluation_testset_schema_sql(),
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
    sections.append(
        OracleSchemaSection(
            name="20261007_001_document_superseded",
            table_name="rag_documents",
            sql=document_superseded_migration_sql(),
        )
    )
    sections.append(
        OracleSchemaSection(
            name="20261009_001_entity_layer",
            table_name="rag_entities",
            sql=entity_layer_migration_sql(),
        )
    )
    sections.append(
        OracleSchemaSection(
            name="20261011_001_section_chunk_map",
            table_name="rag_chunk_set_sections",
            sql=section_chunk_map_migration_sql(),
        )
    )
    sections.append(
        OracleSchemaSection(
            name="20261015_001_message_clarification",
            table_name="rag_messages",
            sql=message_clarification_migration_sql(),
        )
    )
    sections.append(
        OracleSchemaSection(
            name="20261015_002_run_history",
            table_name="rag_run_history",
            sql=run_history_migration_sql(),
        )
    )
    sections.append(
        OracleSchemaSection(
            name="20261015_003_run_history_conversation_idx",
            table_name="rag_run_history",
            sql=run_history_conversation_index_migration_sql(),
        )
    )
    sections.append(
        OracleSchemaSection(
            name="20261015_004_ingestion_job_progress",
            table_name="rag_ingestion_jobs",
            sql=ingestion_job_progress_migration_sql(),
        )
    )
    sections.append(
        OracleSchemaSection(
            name="20261016_001_retire_evaluation_compare",
            table_name="rag_evaluation_jobs",
            sql=retire_evaluation_compare_migration_sql(),
            destructive_note=(
                "品質評価の設定比較（#2088 で削除）の job の行（kind = 'compare'）を削除し、"
                "設定比較だけが使った列（rag_evaluation_runs.best_experiment_id・"
                "rag_evaluation_jobs.current_experiment_id）と索引を削除します。"
                "評価の結果（rag_evaluation_runs の行と result_json）は残ります。"
                "設定比較の job の記録が要るなら、"
                "更新前に rag_evaluation_jobs を書き出してください。"
            ),
        )
    )
    sections.append(
        OracleSchemaSection(
            name="20261016_003_document_categories",
            table_name="rag_document_categories",
            sql=document_categories_migration_sql(),
        )
    )
    sections.append(
        OracleSchemaSection(
            name="20261018_001_support_guide_embeddings",
            table_name="rag_support_guide_embeddings",
            sql=support_guide_embeddings_migration_sql(),
        )
    )
    sections.append(
        OracleSchemaSection(
            name="20261018_002_message_answer_notice",
            table_name="rag_messages",
            sql=answer_notice_column_migration_sql("RAG_MESSAGES"),
        )
    )
    sections.append(
        OracleSchemaSection(
            name="20261018_003_answer_record_notice",
            table_name="rag_answer_records",
            sql=answer_notice_column_migration_sql("RAG_ANSWER_RECORDS"),
        )
    )
    sections.append(
        OracleSchemaSection(
            name="20261018_004_support_guide_schema_v2",
            table_name="rag_support_guides",
            sql=support_guide_schema_v2_migration_sql(),
        )
    )
    sections.append(
        OracleSchemaSection(
            name="20261019_001_restore_document_classification",
            table_name="rag_documents",
            sql=restore_document_classification_migration_sql(),
        )
    )
    sections.append(
        OracleSchemaSection(
            name="20261019_002_document_cleanups",
            table_name="rag_document_cleanups",
            sql=document_cleanups_migration_sql(),
        )
    )
    sections.append(
        OracleSchemaSection(
            name="20261019_003_structured_records_jsonl_profiles",
            table_name="rag_document_recipes",
            sql=structured_records_jsonl_profiles_migration_sql(),
        )
    )
    sections.append(
        OracleSchemaSection(
            name="20261019_010_message_attachments",
            table_name="rag_messages",
            sql=message_attachments_migration_sql(),
        )
    )
    sections.append(
        OracleSchemaSection(
            name="20261021_001_answer_prompt_placeholders",
            table_name="rag_answer_prompts",
            sql=answer_prompt_placeholders_migration_sql(),
        )
    )
    sections.append(
        OracleSchemaSection(
            name="20261021_002_document_delete_fk_indexes",
            table_name="rag_documents",
            sql="""
DECLARE
    v_count NUMBER;
BEGIN
    SELECT COUNT(*) INTO v_count FROM user_indexes
    WHERE index_name = 'RAG_ARTIFACT_LAYERS_DOCUMENT_IDX';
    IF v_count = 0 THEN
        EXECUTE IMMEDIATE 'CREATE INDEX rag_artifact_layers_document_idx '
            || 'ON rag_artifact_layers (document_id)';
    END IF;
    SELECT COUNT(*) INTO v_count FROM user_indexes
    WHERE index_name = 'RAG_DOCUMENTS_DUPLICATE_IDX';
    IF v_count = 0 THEN
        EXECUTE IMMEDIATE 'CREATE INDEX rag_documents_duplicate_idx '
            || 'ON rag_documents (duplicate_of_document_id)';
    END IF;
END;
/
""".strip(),
        )
    )
    sections.append(
        OracleSchemaSection(
            name="20261022_001_cited_evidence",
            table_name="rag_answer_records",
            sql=cited_evidence_migration_sql(),
        )
    )
    sections.append(
        OracleSchemaSection(
            name="20261027_001_raptor_prompt_consolidation",
            table_name="rag_answer_prompts",
            sql=raptor_prompt_consolidation_sql(),
        )
    )
    sections.append(
        OracleSchemaSection(
            name="20261028_001_language_pack_releases",
            table_name="rag_language_pack_releases",
            sql=language_pack_release_statements(),
        )
    )
    sections.append(
        OracleSchemaSection(
            name="20261028_002_language_pack_artifacts",
            table_name="rag_chunk_sets",
            sql=language_pack_artifact_statements(),
        )
    )
    sections.append(
        OracleSchemaSection(
            name="20261029_001_retire_query_history",
            table_name="rag_query_history",
            sql=retire_query_history_sql(),
            destructive_note=(
                "廃止する質問候補専用の RAG_QUERY_HISTORY と全行、同表の索引・制約を削除します。"
                "会話・メッセージ・回答の記録・実行履歴は削除しません。"
                "必要な履歴は適用前に同表を export してください。削除後は復元できません。"
            ),
        )
    )
    sections.append(
        OracleSchemaSection(
            name="20261101_001_native_hybrid",
            table_name="rag_chunks",
            sql=native_hybrid_migration_sql(),
        )
    )
    sections.append(
        OracleSchemaSection(
            name="20261101_002_native_hybrid_ivf",
            table_name="rag_chunks",
            sql=native_hybrid_ivf_migration_sql(),
        )
    )
    # 既存の移行SQLのchecksumを保ち、範囲列の補完より先に同期を限定する。
    sections.append(
        OracleSchemaSection(
            name="20261101_004_native_hybrid_sync_columns",
            table_name="rag_chunks",
            sql=native_hybrid_sync_columns_migration_sql(),
        )
    )
    sections.append(
        OracleSchemaSection(
            name="20261101_003_native_hybrid_scope",
            table_name="rag_chunks",
            sql=native_hybrid_scope_migration_sql(),
        )
    )
    sections.append(
        OracleSchemaSection(
            name="20261102_001_graph_name_characters",
            table_name="rag_graph_entities",
            sql=oracle_graph_name_character_migration_sql(),
        )
    )
    sections.append(
        OracleSchemaSection(
            name="20261102_002_native_missing_scopes",
            table_name="rag_chunks",
            sql=native_hybrid_missing_scope_migration_sql(),
        )
    )
    sections.append(
        OracleSchemaSection(
            name="20261102_003_native_text_search_only",
            table_name="rag_chunks",
            sql=native_hybrid_text_search_migration_sql(),
        )
    )
    sections.append(
        OracleSchemaSection(
            name="20261102_004_native_index_maintenance",
            table_name="rag_native_index_maintenance",
            sql=_create_missing_objects_sql(native_maintenance_tables_sql(), "原生索引の整理要求")
            + "\n\n"
            + native_maintenance_activation_sql(),
        )
    )
    sections.append(
        OracleSchemaSection(
            name="20261102_005_graph_references",
            table_name="rag_graph_references",
            sql=_create_missing_objects_sql(graph_reference_schema_sql(), "明示参照"),
        )
    )
    sections.append(
        OracleSchemaSection(
            name="20261102_006_recipe_version_log",
            table_name="rag_recipe_version_log",
            sql=_create_missing_objects_sql(recipe_version_log_schema_sql(), "版の切替の履歴"),
        )
    )
    sections.append(
        OracleSchemaSection(
            name="20261102_007_chunk_set_retired_at",
            table_name="rag_chunk_sets",
            sql=chunk_set_retired_at_migration_sql(),
        )
    )
    sections.append(
        OracleSchemaSection(
            name="20261102_008_document_extraction_revisions",
            table_name="rag_document_extraction_revisions",
            sql=_create_missing_objects_sql(
                oracle_extraction_revision_schema_sql(), "抽出の修訂の版"
            ),
        )
    )
    # 番号は Issue の番号。並行の PR の連番（20261102_006 など）と重ならない。
    sections.append(
        OracleSchemaSection(
            name="20261102_4171_document_section_revisions",
            table_name="rag_document_section_revisions",
            sql=_create_missing_objects_sql(oracle_section_revision_schema_sql(), "章節の修正の版"),
        )
    )
    sections.append(
        OracleSchemaSection(
            name="20261103_4208_evaluation_job_case_results",
            table_name="rag_evaluation_job_case_results",
            sql=evaluation_job_case_results_migration_sql(),
        )
    )
    sections.append(
        OracleSchemaSection(
            name="20261103_4212_evaluation_source_workbooks",
            table_name="rag_evaluation_source_workbooks",
            sql=evaluation_source_workbooks_migration_sql(),
        )
    )
    sections.append(
        OracleSchemaSection(
            name="20261105_4396_evaluation_testsets",
            table_name="rag_evaluation_testsets",
            sql=_create_missing_objects_sql(
                oracle_evaluation_testset_schema_sql(), "テストセットの下書き"
            ),
        )
    )
    sections.append(
        OracleSchemaSection(
            name="20261107_4185_drop_document_preprocess_artifact",
            table_name="rag_documents",
            sql=drop_document_preprocess_artifact_sql(),
            destructive_note=(
                "RAG_DOCUMENTS の旧列 PREPROCESS_ARTIFACT（文書単位の処理後のファイルの情報）を"
                "削除します。処理後のファイルの正本は RAG_DOCUMENT_RECIPES.PREPROCESS_ARTIFACT で、"
                "現行のコードはこの列を読み書きしません。保存先のファイル・レシピ・抽出・chunk は"
                "削除しません。値が残る環境は適用前に同列を export してください。"
                "削除後は復元できません。"
            ),
        )
    )
    sections.append(
        OracleSchemaSection(
            name="20261108_4617_drop_document_extraction_processing_config",
            table_name="rag_documents",
            sql=drop_document_extraction_processing_config_sql(),
            destructive_note=(
                "RAG_DOCUMENTS の旧列 EXTRACTION（文書単位の旧い抽出の JSON）と PROCESSING_CONFIG"
                "（文書単位の旧い処理設定）を削除します。抽出の正本は RAG_DOCUMENT_EXTRACTIONS、"
                "処理設定の正本は RAG_DOCUMENT_RECIPES.PROCESSING_CONFIG で、現行のコードは"
                "この 2 列を読み書きしません。レシピの抽出の版が無い文書では EXTRACTION が"
                "唯一の旧い抽出の記録なので、残す必要があれば適用前に同列を export してください。"
                "保存先のファイル・"
                "レシピ・抽出の版・chunk は削除しません。削除後は復元できません。"
            ),
        )
    )
    sections.append(
        OracleSchemaSection(
            name="20261108_4628_foreign_key_lock_indexes",
            table_name="rag_graph_references",
            sql=_create_missing_objects_sql(foreign_key_lock_index_sql(), "FK の子の列の索引"),
        )
    )
    sections.append(
        OracleSchemaSection(
            name="20261109_4666_language_pack_retired_question_cues",
            table_name="rag_language_pack_releases",
            # 処理は data_step（公開版の hash の計算は SQL では書けない）。SQL は何もしない。
            # 項目の一覧を書くので、項目を変えると checksum も変わる。
            sql=f"BEGIN\n  NULL; -- {','.join(sorted(RETIRED_QUESTION_CUE_FIELDS))}\nEND;\n/",
            data_step=remove_retired_language_pack_fields,
            destructive_note=(
                "言語資源の公開版（RAG_LANGUAGE_PACK_RELEASES.EFFECTIVE_JSON）・下書き"
                "（RAG_LANGUAGE_PACK_STATE.DRAFT_EFFECTIVE_JSON）・検索・回答プロファイルの上書き"
                "（RAG_SEARCH_ANSWER_PROFILES.PROFILE_CONFIG の language_pack）から、廃止した質問の"
                "手がかりの 11 項目（どの処理も読まない項目）の値を削除し、公開版の CONTENT_HASH を"
                "計算し直します。ほかの項目・版の番号・公開の履歴・INGEST_HASH は変えません。"
                "項目に保存した語を残す必要があれば適用前に同列を export してください。"
                "削除後は復元できません。"
            ),
        )
    )
    sections.append(
        OracleSchemaSection(
            name="20261109_4712_answer_prompt_reading_rules",
            table_name="rag_answer_prompts",
            sql=answer_prompt_reading_rules_migration_sql(),
        )
    )
    sections.append(
        OracleSchemaSection(
            name="20261109_4714_language_pack_retired_answer_checks",
            table_name="rag_language_pack_releases",
            # 処理は data_step。項目の一覧を書くので、項目を変えると checksum も変わる。
            sql=(
                "BEGIN\n  NULL; -- "
                + ",".join(
                    sorted(
                        RETIRED_ANSWER_LABEL_FIELDS
                        | RETIRED_ANSWER_CHECK_CUE_FIELDS
                        | RETIRED_SECTIONS
                    )
                )
                + "\nEND;\n/"
            ),
            data_step=remove_retired_language_pack_fields,
            destructive_note=(
                "言語資源の公開版（RAG_LANGUAGE_PACK_RELEASES.EFFECTIVE_JSON）・下書き"
                "（RAG_LANGUAGE_PACK_STATE.DRAFT_EFFECTIVE_JSON）・検索・回答プロファイルの上書き"
                "（RAG_SEARCH_ANSWER_PROFILES.PROFILE_CONFIG の language_pack）から、回答の生成後の"
                "確認をやめて読まれなくなった項目（answer_labels の一部・answer_check_cues の一部・"
                "guide_check_cues の節）の値を削除し、公開版の CONTENT_HASH を計算し直します。"
                "ほかの項目・版の番号・公開の履歴・INGEST_HASH は変えません。"
                "項目に保存した語を残す必要があれば適用前に同列を export してください。"
                "削除後は復元できません。"
            ),
        )
    )
    sections.append(
        OracleSchemaSection(
            name="20261109_4668_language_pack_retired_unread_checks",
            table_name="rag_language_pack_releases",
            # 処理は data_step。項目の一覧を書くので、項目を変えると checksum も変わる。
            sql=(
                "BEGIN\n  NULL; -- "
                + ",".join(sorted(RETIRED_UNREAD_FIELDS | RETIRED_UNREAD_SECTIONS))
                + "\nEND;\n/"
            ),
            data_step=remove_retired_language_pack_fields,
            destructive_note=(
                "言語資源の公開版（RAG_LANGUAGE_PACK_RELEASES.EFFECTIVE_JSON）・下書き"
                "（RAG_LANGUAGE_PACK_STATE.DRAFT_EFFECTIVE_JSON）・検索・回答プロファイルの上書き"
                "（RAG_SEARCH_ANSWER_PROFILES.PROFILE_CONFIG の language_pack）から、動いていない"
                "語の判定を消して読まれなくなった項目（search_terms・evidence_cues・answer_labels・"
                "answer_check_cues の一部と operation_audit_cues の節）の値を削除し、公開版の "
                "CONTENT_HASH を計算し直します。ほかの項目・版の番号・公開の履歴・INGEST_HASH は"
                "変えません。項目に保存した語を残す必要があれば適用前に同列を export してください。"
                "削除後は復元できません。"
            ),
        )
    )
    sections.append(
        OracleSchemaSection(
            name="20261110_4668_language_pack_retired_evidence_tags",
            table_name="rag_language_pack_releases",
            # 処理は data_step。項目の一覧を書くので、項目を変えると checksum も変わる。
            sql="BEGIN\n  NULL; -- " + ",".join(sorted(RETIRED_EVIDENCE_TAG_FIELDS)) + "\nEND;\n/",
            data_step=remove_retired_language_pack_fields,
            destructive_note=(
                "言語資源の公開版（RAG_LANGUAGE_PACK_RELEASES.EFFECTIVE_JSON）・下書き"
                "（RAG_LANGUAGE_PACK_STATE.DRAFT_EFFECTIVE_JSON）・検索・回答プロファイルの上書き"
                "（RAG_SEARCH_ANSWER_PROFILES.PROFILE_CONFIG の language_pack）から、根拠の語の印と"
                "本文の語の書き換えをやめて読まれなくなった項目（question_cues・evidence_cues・"
                "answer_check_cues・grounding_cues の一部）の値を削除し、公開版の CONTENT_HASH を"
                "計算し直します。ほかの項目・版の番号・公開の履歴・INGEST_HASH は変えません。"
                "項目に保存した語を残す必要があれば適用前に同列を export してください。"
                "削除後は復元できません。"
            ),
        )
    )
    sections.append(
        OracleSchemaSection(
            name="20261110_4668_language_pack_retired_question_word_checks",
            table_name="rag_language_pack_releases",
            # 処理は data_step。項目の一覧を書くので、項目を変えると checksum も変わる。
            sql="BEGIN\n  NULL; -- " + ",".join(sorted(RETIRED_QUESTION_WORD_FIELDS)) + "\nEND;\n/",
            data_step=remove_retired_language_pack_fields,
            destructive_note=(
                "言語資源の公開版（RAG_LANGUAGE_PACK_RELEASES.EFFECTIVE_JSON）・下書き"
                "（RAG_LANGUAGE_PACK_STATE.DRAFT_EFFECTIVE_JSON）・検索・回答プロファイルの上書き"
                "（RAG_SEARCH_ANSWER_PROFILES.PROFILE_CONFIG の language_pack）から、質問の種類の"
                "語の判定と根拠の順位づけの語をやめて読まれなくなった項目（question_cues・"
                "evidence_cues・grounding_cues・answer_labels の一部）と節（enumeration_cues・"
                "definition_cues）の値を削除し、公開版の CONTENT_HASH を計算し直します。"
                "ほかの項目・版の番号・公開の履歴・INGEST_HASH は変えません。項目に保存した語を"
                "残す必要があれば適用前に同列を export してください。削除後は復元できません。"
            ),
        )
    )
    sections.append(
        OracleSchemaSection(
            name="20261110_4749_language_pack_retired_crag_labels",
            table_name="rag_language_pack_releases",
            # 処理は data_step。項目の一覧を書くので、項目を変えると checksum も変わる。
            sql=(
                "BEGIN\n  NULL; -- "
                + ",".join(sorted(RETIRED_CRAG_ANSWER_LABEL_FIELDS))
                + "\nEND;\n/"
            ),
            data_step=remove_retired_language_pack_fields,
            destructive_note=(
                "言語資源の公開版（RAG_LANGUAGE_PACK_RELEASES.EFFECTIVE_JSON）・下書き"
                "（RAG_LANGUAGE_PACK_STATE.DRAFT_EFFECTIVE_JSON）・検索・回答プロファイルの上書き"
                "（RAG_SEARCH_ANSWER_PROFILES.PROFILE_CONFIG の language_pack）から、CRAG の工程を"
                "消して読まれなくなった answer_labels の項目の値を削除し、公開版の CONTENT_HASH を"
                "計算し直します。ほかの項目・版の番号・公開の履歴・INGEST_HASH は変えません。"
                "項目に保存した語を残す必要があれば適用前に同列を export してください。"
                "削除後は復元できません。"
            ),
        )
    )
    sections.append(
        OracleSchemaSection(
            name="20261110_4782_generation_prompt_overrides",
            table_name="rag_answer_prompts",
            sql=removed_prompt_overrides_sql(),
            destructive_note=(
                "RAG_ANSWER_PROMPTS から、編集欄を外した指示の保存値（PROMPT_KEY が vlm_answer・"
                "generation.system・generation.batchReading・generation.assumptionSystem・"
                "generation.evidenceReading・generation.guideRules・support_guide_draft.*・"
                "support_guide_conditions.*・rule_selection.*・history_rewrite.system・"
                "field_filter.system・screen_linking.system・article_catalog.*・"
                "section_extraction.system の行）を削除します。ほかの行（generation.role・"
                "faq_answer.system・evaluation.*・picture_description.*・image_retrieval・"
                "取込の指示）は変えません。編集した文を残す必要があれば、適用前に該当の行の "
                "CONTENT を export してください。削除後は復元できず、新しい編集欄へは写しません。"
            ),
        )
    )
    sections.append(
        OracleSchemaSection(
            name="20261110_4781_retire_evaluation_calibration",
            table_name="rag_evaluation_jobs",
            sql=retire_evaluation_calibration_sql(),
            destructive_note=(
                "品質評価の判定の校正をやめたため、校正の表（RAG_EVALUATION_CALIBRATIONS・"
                "RAG_EVALUATION_LABELS・RAG_EVALUATION_LABEL_ITEMS・RAG_EVALUATION_LABEL_SETS）を"
                "行ごと削除し、校正の job の行（RAG_EVALUATION_JOBS の kind が calibration）と、"
                "ロールの権限（RAG_ROLE_PERMISSIONS）の「判定の校正の管理」の行を削除します。"
                "品質評価の job・ケースごとの結果・回答の記録は変えません。人手の判定を残す必要が"
                "あれば適用前に同表を export してください。削除後は復元できません。"
            ),
        )
    )
    sections.append(
        OracleSchemaSection(
            name="20261110_4781_retire_removed_answer_prompts",
            table_name="rag_answer_prompts",
            sql=retire_removed_answer_prompts_sql(),
            destructive_note=(
                "保存した指示（RAG_ANSWER_PROMPTS）から、工程を消して読まれなくなったキーの行を"
                "削除します: " + "・".join(RETIRED_ANSWER_PROMPT_KEYS) + "。ほかのキーの行と、"
                "品質評価の結果・回答の記録は変えません。保存した文を残す必要があれば適用前に"
                "同表を export してください。削除後は復元できません。"
            ),
        )
    )
    sections.append(
        OracleSchemaSection(
            name="20261110_4784_search_answer_profile_retired_fields",
            table_name="rag_search_answer_profiles",
            # 処理は data_step。残す欄と節の一覧を書くので、一覧を変えると checksum も変わる。
            sql=(
                "BEGIN\n  NULL; -- query: "
                + ",".join(sorted(KEPT_QUERY_FIELDS))
                + " / language_pack: "
                + ",".join(sorted(KEPT_LANGUAGE_PACK_SECTIONS))
                + "\nEND;\n/"
            ),
            data_step=remove_retired_search_answer_profile_fields,
            destructive_note=(
                "検索・回答プロファイル（RAG_SEARCH_ANSWER_PROFILES.PROFILE_CONFIG）の query から、"
                "max_anchors_per_document と support_guide_enabled 以外の欄（適用していなかった"
                "検索方法の欄・業務ガイドの意味の照合と近い候補の確認の欄・旧データの欄）の値を"
                "削除し、language_pack から、検索と回答で読まない節（search_terms・question_cues・"
                "evidence_cues・answer_labels・answer_check_cues・grounding_cues 以外。業務ガイドの"
                "取込の見出しの語 guide_import_cues など）の上書きを削除します。ナレッジベース"
                "（RAG_KNOWLEDGE_BASES.RETRIEVAL_CONFIG）からは、読まれていない query を削除"
                "します。"
                "ほかの欄・節の値は変えません。業務ガイドの意味の照合・近い候補の確認・取込の"
                "見出しの語をプロファイルごとに変えていた環境は、適用前に同列を export し、"
                "全体の設定・全体の言語資源へ写してください。削除後は復元できません。"
            ),
        )
    )
    sections.append(
        OracleSchemaSection(
            name="20261110_4784_language_pack_retired_unread",
            table_name="rag_language_pack_releases",
            # 処理は data_step。項目の一覧を書くので、項目を変えると checksum も変わる。
            sql="BEGIN\n  NULL; -- " + ",".join(sorted(RETIRED_4784_FIELDS)) + "\nEND;\n/",
            data_step=remove_retired_language_pack_fields,
            destructive_note=(
                "言語資源の公開版（RAG_LANGUAGE_PACK_RELEASES.EFFECTIVE_JSON）・下書き"
                "（RAG_LANGUAGE_PACK_STATE.DRAFT_EFFECTIVE_JSON）・検索・回答プロファイルの上書き"
                "（RAG_SEARCH_ANSWER_PROFILES.PROFILE_CONFIG の language_pack）から、どの処理も"
                "読まない項目（numbers・grounding_cues・answer_labels の一部）と、語の一覧をやめた"
                "根拠の手がかり（evidence_cues の 2 項目）と、NFKC に固定した"
                "文字の正規化（symbols.normalization）と、読み飛ばしていた古い節（inquiry_cues）の"
                "値を削除し、公開版の CONTENT_HASH と INGEST_HASH を計算し直します。取込の項目を"
                "消した版は、その版で作った索引の集合（RAG_CHUNK_SETS）・版の切り替えの記録"
                "（RAG_RECIPE_VERSION_LOG）・抽出の成果物（RAG_DOCUMENT_EXTRACTIONS・"
                "RAG_DOCUMENT_EXTRACTION_REVISIONS の parser_artifacts）に残した取込 hash も新しい"
                "値に書き換えます（内容は変わらないので、取込済みの文書の再処理は要りません。"
                "NFKC 以外の正規化を公開していた版の索引は書き換えず「再処理を推奨」になります）。"
                "ほかの項目・版の番号・公開の履歴は変えません。項目に保存した語を残す必要があれば"
                "適用前に同列を export してください。削除後は復元できません。"
            ),
        )
    )
    sections.append(
        OracleSchemaSection(
            name="20261110_4782_remove_raptor",
            table_name="rag_chunks",
            sql=remove_raptor_sql(),
            destructive_note=(
                "取込の RAPTOR の階層の要約を消したので、保存済みの RAPTOR の要約の chunk"
                "（RAG_CHUNKS の METADATA_JSON に raptor_summary の印がある行）と、RAPTOR の要約の"
                "プロンプトの保存値（RAG_ANSWER_PROMPTS の raptor_summary_system・"
                "raptor_summary_archived）を削除します。原文の chunk・章節の要約・項目抽出の値・"
                "ほかのプロンプトは変えません。保存したプロンプトを残す必要があれば適用前に同表を "
                "export してください。削除後は復元できません（要約の chunk は再取込でも"
                "作りません）。"
            ),
        )
    )
    sections.append(
        OracleSchemaSection(
            name="20261110_4799_search_answer_profile_runtime_rules",
            table_name="rag_search_answer_profile_knowledge",
            sql=removed_runtime_rules_sql(),
            destructive_note=(
                "検索・回答プロファイルの知識（RAG_SEARCH_ANSWER_PROFILE_KNOWLEDGE の KIND が "
                "runtime_knowledge の行の PAYLOAD_JSON）から、登録ルール（$.rules。確認の質問と"
                "選択肢を含む）を削除します。用語・同義語（$.terms）と、ドメインキーワード・"
                "承認済み FAQ の行は変えません。登録ルールは確認・回答に使わなくなり、回答の規則は"
                "回答プロンプトの役割の文に書きます。ルールの文を残す必要があれば、適用前に同列を "
                "export し、役割の文へ手で写してください。削除後は復元できません。"
            ),
        )
    )
    return sections


def foreign_key_lock_index_sql() -> str:
    """親の行を消す FK の、子の列を先頭に持つ索引（#4628。#2865 と同じ扱い）。

    子の列を先頭に持つ索引が無い FK は、親の行の DELETE のたびに子の表全体の lock を取る。
    別の transaction が子の表を変更中（SX）なら待ち、2 つの transaction が同時に親の行を消すと
    ORA-00060 になる（別々の文書の行でも起きる）。既存の (tenant / KB, 列) の索引は FK の判定に
    使えないため、列だけの索引を足す。
    """
    return """
CREATE INDEX rag_graph_refs_source_set_idx
    ON rag_graph_references (source_chunk_set_id);

CREATE INDEX rag_graph_rel_source_entity_idx
    ON rag_graph_relationships (source_entity_id);

CREATE INDEX rag_graph_rel_target_entity_idx
    ON rag_graph_relationships (target_entity_id);

CREATE INDEX rag_structure_builds_doc_idx
    ON rag_structure_index_builds (document_id);
""".strip()


def chunk_set_retired_at_migration_sql() -> str:
    """旧版を残すための退役の時刻の列と索引を無ければ足す（#4168）。

    列を足すだけで既存の行は変えない。既存の非 active の行は NULL のまま（版ではなく候補の扱い）。
    """
    return """DECLARE
    v_count NUMBER;
BEGIN
    SELECT COUNT(*) INTO v_count
    FROM user_tab_columns
    WHERE table_name = 'RAG_CHUNK_SETS' AND column_name = 'RETIRED_AT';
    IF v_count = 0 THEN
        EXECUTE IMMEDIATE 'ALTER TABLE rag_chunk_sets ADD (retired_at TIMESTAMP WITH TIME ZONE)';
    END IF;
    SELECT COUNT(*) INTO v_count
    FROM user_indexes
    WHERE index_name = 'RAG_CHUNK_SETS_RETIRED_IDX';
    IF v_count = 0 THEN
        EXECUTE IMMEDIATE 'CREATE INDEX rag_chunk_sets_retired_idx ON rag_chunk_sets (retired_at)';
    END IF;
END;
/"""


def drop_document_preprocess_artifact_sql() -> str:
    """文書の行の旧列 preprocess_artifact を削除する。列が無い環境でも適用できる（#4185）。"""
    return """
DECLARE
    v_count NUMBER;
BEGIN
    SELECT COUNT(*) INTO v_count
    FROM user_tab_columns
    WHERE table_name = 'RAG_DOCUMENTS' AND column_name = 'PREPROCESS_ARTIFACT';
    IF v_count > 0 THEN
        EXECUTE IMMEDIATE 'ALTER TABLE rag_documents DROP COLUMN preprocess_artifact';
    END IF;
END;
/
""".strip()


def drop_document_extraction_processing_config_sql() -> str:
    """文書の行の旧列 extraction・processing_config を削除する。列が無くても適用できる（#4617）。"""
    return """
DECLARE
    v_count NUMBER;
BEGIN
    SELECT COUNT(*) INTO v_count
    FROM user_tab_columns
    WHERE table_name = 'RAG_DOCUMENTS' AND column_name = 'EXTRACTION';
    IF v_count > 0 THEN
        EXECUTE IMMEDIATE 'ALTER TABLE rag_documents DROP COLUMN extraction';
    END IF;
    SELECT COUNT(*) INTO v_count
    FROM user_tab_columns
    WHERE table_name = 'RAG_DOCUMENTS' AND column_name = 'PROCESSING_CONFIG';
    IF v_count > 0 THEN
        EXECUTE IMMEDIATE 'ALTER TABLE rag_documents DROP COLUMN processing_config';
    END IF;
END;
/
""".strip()


def retire_query_history_sql() -> str:
    """質問候補専用表だけを削除し、未作成・削除済みの環境でも適用する。"""
    return """
BEGIN
    EXECUTE IMMEDIATE 'DROP TABLE rag_query_history PURGE';
EXCEPTION
    WHEN OTHERS THEN
        IF SQLCODE != -942 THEN
            RAISE;
        END IF;
END;
/
""".strip()


def remove_raptor_sql() -> str:
    """消した RAPTOR の階層の要約の、保存済みの chunk とプロンプトを消す（#4782）。

    残すと、要約の chunk が原文の chunk として検索・引用に出る（派生の chunk の印を読まなくなる）。
    何度当てても同じ結果になる。
    """
    return """
DELETE FROM rag_chunks
WHERE JSON_VALUE(metadata_json, '$.raptor_summary') = 'true';

DELETE FROM rag_answer_prompts
WHERE prompt_key IN ('raptor_summary_system', 'raptor_summary_archived');
""".strip()


def raptor_prompt_consolidation_sql() -> str:
    """保存した両方の指示を結合し、旧指示を監査用に残す。適用は migration ledger で一度だけ。"""
    return """
MERGE INTO rag_answer_prompts target
USING (
    SELECT 'raptor_summary_system' AS prompt_key,
        COALESCE(
            (SELECT content FROM rag_answer_prompts WHERE prompt_key = 'raptor_summary_system'),
            TO_CLOB('## 目的' || CHR(10) || CHR(10)
                || 'あなたは文書を階層的に要約するアシスタントです。')
        ) || CHR(10) || CHR(10) || COALESCE(
            (SELECT content FROM rag_answer_prompts WHERE prompt_key = 'raptor_summary'),
            TO_CLOB('## 目的' || CHR(10) || CHR(10)
                || '次のテキスト群の要点を簡潔な日本語で要約してください。')
        ) AS content
    FROM dual
    WHERE EXISTS (
        SELECT 1 FROM rag_answer_prompts
        WHERE prompt_key IN ('raptor_summary_system', 'raptor_summary')
    )
) source
ON (target.prompt_key = source.prompt_key)
WHEN MATCHED THEN UPDATE SET target.content = source.content, target.updated_at = SYSTIMESTAMP
WHEN NOT MATCHED THEN INSERT (prompt_key, content) VALUES (source.prompt_key, source.content);

UPDATE rag_answer_prompts
SET prompt_key = 'raptor_summary_archived'
WHERE prompt_key = 'raptor_summary';
""".strip()


def evaluation_job_case_results_migration_sql() -> str:
    """品質評価の job のケースごとの結果の表を無ければ作る（#4208）。

    表を足すだけでデータは変えない。DDL は正本
    （``oracle_evaluation_job_case_result_schema_sql``）から作る。
    """
    return _create_missing_objects_sql(
        oracle_evaluation_job_case_result_schema_sql(), "品質評価のケースごとの結果"
    )


def evaluation_source_workbooks_migration_sql() -> str:
    """品質評価にアップロードした Excel の表と、job からの参照の列を無ければ足す（#4212）。

    表・列・制約・索引を足すだけで、既存の行は変えない（既存の job の列は NULL）。
    """
    objects = _create_missing_objects_sql(
        oracle_evaluation_source_workbook_schema_sql(), "品質評価にアップロードした Excel"
    )
    column = """DECLARE
    v_count NUMBER;
BEGIN
    SELECT COUNT(*) INTO v_count
    FROM user_tab_columns
    WHERE table_name = 'RAG_EVALUATION_JOBS' AND column_name = 'SOURCE_WORKBOOK_ID';
    IF v_count = 0 THEN
        EXECUTE IMMEDIATE 'ALTER TABLE rag_evaluation_jobs ADD (source_workbook_id VARCHAR2(64))';
    END IF;
    SELECT COUNT(*) INTO v_count
    FROM user_constraints
    WHERE table_name = 'RAG_EVALUATION_JOBS'
      AND constraint_name = 'RAG_EVALUATION_JOBS_SOURCE_WORKBOOK_FK';
    IF v_count = 0 THEN
        EXECUTE IMMEDIATE 'ALTER TABLE rag_evaluation_jobs '
            || 'ADD CONSTRAINT rag_evaluation_jobs_source_workbook_fk '
            || 'FOREIGN KEY (source_workbook_id) '
            || 'REFERENCES rag_evaluation_source_workbooks (workbook_id) ON DELETE SET NULL';
    END IF;
    SELECT COUNT(*) INTO v_count
    FROM user_indexes
    WHERE index_name = 'RAG_EVALUATION_JOBS_SOURCE_WORKBOOK_IDX';
    IF v_count = 0 THEN
        EXECUTE IMMEDIATE 'CREATE INDEX rag_evaluation_jobs_source_workbook_idx '
            || 'ON rag_evaluation_jobs (source_workbook_id)';
    END IF;
END;
/"""
    return f"{objects}\n\n{column}"


# 工程を消して読まれなくなった指示の保存キー（#4749 の検索の振り分け・質問の広げ直し・旧い生成の
# 指示と、#4781 の評価の判定 5 本）。一覧を変えると migration の checksum も変わる。
RETIRED_ANSWER_PROMPT_KEYS: tuple[str, ...] = (
    "evaluation.scope",
    "evaluation.coverage",
    "evaluation.claim",
    "evaluation.evidenceCoverage",
    "evaluation.absenceScope",
    "crag.system",
    "crag.template",
    "routing.system",
    "routing.template",
    "expansion.system",
    "expansion.template",
    "generation.instruction",
    "generation.plannedSystem",
)


def retire_removed_answer_prompts_sql() -> str:
    """読まれなくなった指示の保存値だけを消す。ほかのキーの行は変えない（#4781）。"""
    keys = ", ".join(f"'{key}'" for key in RETIRED_ANSWER_PROMPT_KEYS)
    return f"DELETE FROM rag_answer_prompts WHERE prompt_key IN ({keys});"


def retire_evaluation_calibration_sql() -> str:
    """判定の校正の表・job・権限の行を消し、job の kind を run だけに戻す（#4781）。

    表・制約が無い環境でも適用できる（冪等）。品質評価の job と結果の行は変えない。
    """
    return """DECLARE
    v_count NUMBER;
BEGIN
    FOR retired IN (
        SELECT table_name FROM user_tables
        WHERE table_name IN (
            'RAG_EVALUATION_CALIBRATIONS', 'RAG_EVALUATION_LABELS',
            'RAG_EVALUATION_LABEL_ITEMS', 'RAG_EVALUATION_LABEL_SETS'
        )
    ) LOOP
        EXECUTE IMMEDIATE 'DROP TABLE ' || retired.table_name || ' CASCADE CONSTRAINTS PURGE';
    END LOOP;
    SELECT COUNT(*) INTO v_count FROM user_tables WHERE table_name = 'RAG_ROLE_PERMISSIONS';
    IF v_count > 0 THEN
        EXECUTE IMMEDIATE
            'DELETE FROM rag_role_permissions WHERE permission_code = :1'
            USING 'rag.evaluation_calibration.manage';
    END IF;
    SELECT COUNT(*) INTO v_count FROM user_tables WHERE table_name = 'RAG_EVALUATION_JOBS';
    IF v_count > 0 THEN
        EXECUTE IMMEDIATE 'DELETE FROM rag_evaluation_jobs WHERE kind = :1' USING 'calibration';
        SELECT COUNT(*) INTO v_count
        FROM user_constraints
        WHERE table_name = 'RAG_EVALUATION_JOBS'
          AND constraint_name = 'RAG_EVALUATION_JOBS_KIND_CK';
        IF v_count > 0 THEN
            EXECUTE IMMEDIATE
                'ALTER TABLE rag_evaluation_jobs DROP CONSTRAINT rag_evaluation_jobs_kind_ck';
        END IF;
        EXECUTE IMMEDIATE
            'ALTER TABLE rag_evaluation_jobs ADD CONSTRAINT rag_evaluation_jobs_kind_ck '
            || 'CHECK (kind IN (''run''))';
    END IF;
END;
/"""


# #2457 で改名したファイル準備の値(旧, 新)。JSON を文字列にして置き換える(値と、抽出の ID の入力の
# key の両方。引用符まで含めて書き、ほかの名前の一部を置き換えない)。
STORED_PREPROCESS_RENAMES_2457: tuple[tuple[str, str], ...] = (
    ('"csv_to_json"', '"csv_to_jsonl"'),
    ('"excel_to_json"', '"excel_to_jsonl"'),
    ('"csv_to_json_format"', '"csv_to_jsonl_format"'),
    ('"excel_to_json_format"', '"excel_to_jsonl_format"'),
)
# ファイル準備の値を持ちうる JSON の列(表, 列)。設定・レシピ・その記録だけで、抽出の本文は変えない。
STORED_PREPROCESS_COLUMNS_2457: tuple[tuple[str, str], ...] = (
    ("RAG_DOCUMENTS", "PROCESSING_CONFIG"),
    ("RAG_DOCUMENT_RECIPES", "PROCESSING_CONFIG"),
    ("RAG_KNOWLEDGE_BASES", "RETRIEVAL_CONFIG"),
    ("RAG_INGESTION_JOBS", "SETTINGS_OVERRIDES"),
    ("RAG_CHUNK_SETS", "RECIPE_SUBSET"),
    ("RAG_DOCUMENT_EXTRACTIONS", "RECIPE_SUBSET"),
)
# 文書レシピの処理設定の列(解析エンジンと分割符も書き換える)。
STORED_RECIPE_COLUMNS_2457: tuple[tuple[str, str], ...] = (
    ("RAG_DOCUMENTS", "PROCESSING_CONFIG"),
    ("RAG_DOCUMENT_RECIPES", "PROCESSING_CONFIG"),
)
# #2457 の前の固定分割符の既定(escape 表現)。
PREVIOUS_DEFAULT_CHUNK_DELIMITER_2457 = "\\n\\n"


def apply_stored_preprocess_renames_2457(text: str) -> str:
    """``STORED_PREPROCESS_RENAMES_2457`` を migration の SQL と同じ順に当てる(テストで照合)。"""
    for old, new in STORED_PREPROCESS_RENAMES_2457:
        text = text.replace(old, new)
    return text


def structured_records_jsonl_profiles_migration_sql() -> str:
    """ファイル準備 csv_to_json / excel_to_json の保存値を ``*_jsonl`` に移す(#2457)。

    - 設定・レシピ・取込ジョブ・その記録の JSON の ``"csv_to_json"`` / ``"excel_to_json"`` を
      ``*_jsonl`` に書き換える(抽出の ID の入力の key ``*_format`` も)。
    - 文書レシピでファイル準備が ``*_jsonl`` で、解析エンジンが Docling / Unstructured か未指定
      (global を継承)なら、解析エンジンを構造化レコード JSONL にする(Docling / Unstructured は
      構造化レコードの JSONL を受けなくなった)。分割の方式は書き換えない。
    - 文書レシピの分割方式が固定分割で分割符が未指定なら、旧い既定の ``\\n\\n`` を明示する
      (既定の分割符を記録の境界のマーカーに変えても、分割の結果を変えない)。
    表・列が無ければ何もしない。何度当てても同じ結果になる(冪等)。
    """
    targets = ", ".join(f"'{table}.{column}'" for table, column in STORED_PREPROCESS_COLUMNS_2457)
    recipe_targets = ", ".join(
        f"'{table}.{column}'" for table, column in STORED_RECIPE_COLUMNS_2457
    )
    replaces = "\n".join(
        f"            v_expr := 'REPLACE(' || v_expr || ', ''{old}'', ''{new}'')';"
        for old, new in STORED_PREPROCESS_RENAMES_2457
    )
    delimiter = PREVIOUS_DEFAULT_CHUNK_DELIMITER_2457
    return f"""DECLARE
    TYPE t_names IS TABLE OF VARCHAR2(261);
    v_targets t_names := t_names({targets});
    v_recipes t_names := t_names({recipe_targets});
    v_table   VARCHAR2(128);
    v_column  VARCHAR2(128);
    v_type    VARCHAR2(128);
    v_text    VARCHAR2(512);
    v_json    VARCHAR2(512);
    v_expr    VARCHAR2(32767);
    FUNCTION stored(p_expr VARCHAR2) RETURN VARCHAR2 IS
    BEGIN
        IF v_type = 'JSON' THEN
            RETURN p_expr;
        END IF;
        RETURN 'JSON_SERIALIZE(' || p_expr || ' RETURNING CLOB)';
    END;
BEGIN
    FOR i IN 1 .. v_targets.COUNT LOOP
        v_table := SUBSTR(v_targets(i), 1, INSTR(v_targets(i), '.') - 1);
        v_column := SUBSTR(v_targets(i), INSTR(v_targets(i), '.') + 1);
        SELECT MAX(data_type) INTO v_type
        FROM user_tab_columns
        WHERE table_name = v_table AND column_name = v_column;
        IF v_type IS NOT NULL THEN
            IF v_type = 'JSON' THEN
                v_text := 'JSON_SERIALIZE(' || v_column || ' RETURNING CLOB)';
            ELSE
                v_text := v_column;
            END IF;
            v_expr := v_text;
{replaces}
            IF v_type = 'JSON' THEN
                v_expr := 'JSON(' || v_expr || ')';
            END IF;
            EXECUTE IMMEDIATE
                'UPDATE ' || v_table || ' SET ' || v_column || ' = ' || v_expr
                || ' WHERE ' || v_text || ' LIKE ''%_to_json"%'''
                || ' OR ' || v_text || ' LIKE ''%_to_json_format"%''';
            COMMIT;
        END IF;
    END LOOP;
    FOR i IN 1 .. v_recipes.COUNT LOOP
        v_table := SUBSTR(v_recipes(i), 1, INSTR(v_recipes(i), '.') - 1);
        v_column := SUBSTR(v_recipes(i), INSTR(v_recipes(i), '.') + 1);
        SELECT MAX(data_type) INTO v_type
        FROM user_tab_columns
        WHERE table_name = v_table AND column_name = v_column;
        IF v_type IS NOT NULL THEN
            IF v_type = 'JSON' THEN
                v_json := v_column;
            ELSE
                v_json := 'JSON(' || v_column || ')';
            END IF;
            EXECUTE IMMEDIATE
                'UPDATE ' || v_table || ' SET ' || v_column || ' = '
                || stored('JSON_TRANSFORM(' || v_json
                || ', SET ''$.parser_adapter_backend'' = ''structured_records_jsonl'')')
                || ' WHERE ' || v_column || ' IS NOT NULL'
                || ' AND JSON_VALUE(' || v_json || ', ''$.preprocess_profile'')'
                || ' IN (''csv_to_jsonl'', ''excel_to_jsonl'')'
                || ' AND NVL(JSON_VALUE(' || v_json || ', ''$.parser_adapter_backend''),'
                || ' ''docling'') IN (''docling'', ''unstructured'')';
            EXECUTE IMMEDIATE
                'UPDATE ' || v_table || ' SET ' || v_column || ' = '
                || stored('JSON_TRANSFORM(' || v_json
                || ', SET ''$.chunk_delimiter'' = ''{delimiter}'')')
                || ' WHERE ' || v_column || ' IS NOT NULL'
                || ' AND JSON_VALUE(' || v_json || ', ''$.chunking_strategy'')'
                || ' = ''fixed_delimiter'''
                || ' AND JSON_VALUE(' || v_json || ', ''$.chunk_delimiter'') IS NULL';
            COMMIT;
        END IF;
    END LOOP;
END;
/"""


def document_cleanups_migration_sql() -> str:
    """文書の削除の後始末の記録の表（rag_document_cleanups）と索引を無ければ作る（#2452）。

    表と索引を足すだけでデータは変えない。DDL は正本
    （``oracle_document_cleanup_schema_sql``）から作る。
    """
    return _create_missing_objects_sql(
        oracle_document_cleanup_schema_sql(), "文書の削除の後始末の記録"
    )


def restore_document_classification_migration_sql() -> str:
    """取り消した migration ``20261016_002_document_effective_period`` を当てた DB の列を戻す。

    #2450。取り消した migration は ``rag_documents.classification`` を ``effective_period`` に
    改名して有効期間以外の key を消し、``rag_query_history.classification_filter`` を削除した。
    台帳には適用済みで残るので、ここで今の正本の形にそろえる（新しい DB・当てていない DB では
    何もしない）。

    - ``rag_documents.classification``・``rag_query_history.classification_filter`` が無ければ
      JSON の列を足す。
    - ``effective_period`` があれば、``classification`` が NULL の行にだけ、有効期間
      （``effective_from`` / ``effective_to``。YYYY-MM-DD の日付で、開始日 < 終了日）を写す。
      列の改名はしない。``effective_period`` の列と値は消さず、形の合わない値は写さない。
      取り消した migration が消した分類（大分類・中分類・小分類）と検索の履歴の分類の条件は DB に
      残っていないので戻らない（要るなら、そのときの書き出し・バックアップから戻す）。
    どの文も、表・列があるときだけ実行し、何度当てても同じ結果になる（冪等）。
    """
    period_date = (
        "TO_DATE(CASE WHEN REGEXP_LIKE(JSON_VALUE(effective_period, '$.{key}'), "
        "'^[0-9]{{4}}-[0-9]{{2}}-[0-9]{{2}}$') "
        "THEN JSON_VALUE(effective_period, '$.{key}') END "
        "DEFAULT NULL ON CONVERSION ERROR, 'YYYY-MM-DD')"
    )
    effective_from = period_date.format(key="effective_from")
    effective_to = period_date.format(key="effective_to")
    return f"""DECLARE
    v_count NUMBER;
BEGIN
    SELECT COUNT(*) INTO v_count FROM user_tables WHERE table_name = 'RAG_DOCUMENTS';
    IF v_count > 0 THEN
        SELECT COUNT(*) INTO v_count
        FROM user_tab_columns
        WHERE table_name = 'RAG_DOCUMENTS' AND column_name = 'CLASSIFICATION';
        IF v_count = 0 THEN
            EXECUTE IMMEDIATE 'ALTER TABLE rag_documents ADD (classification JSON)';
        END IF;
        SELECT COUNT(*) INTO v_count
        FROM user_tab_columns
        WHERE table_name = 'RAG_DOCUMENTS' AND column_name = 'EFFECTIVE_PERIOD';
        IF v_count > 0 THEN
            EXECUTE IMMEDIATE q'[MERGE INTO rag_documents target
USING (
    SELECT document_id, period_from, period_to
    FROM (
        SELECT document_id,
            {effective_from} AS period_from,
            {effective_to} AS period_to,
            JSON_VALUE(effective_period, '$.effective_from') AS raw_from,
            JSON_VALUE(effective_period, '$.effective_to') AS raw_to
        FROM rag_documents
        WHERE effective_period IS NOT NULL AND classification IS NULL
    )
    WHERE (period_from IS NOT NULL OR period_to IS NOT NULL)
        AND (raw_from IS NULL OR period_from IS NOT NULL)
        AND (raw_to IS NULL OR period_to IS NOT NULL)
        AND (period_from IS NULL OR period_to IS NULL OR period_from < period_to)
) source
ON (target.document_id = source.document_id)
WHEN MATCHED THEN UPDATE SET target.classification = JSON_OBJECT(
    'effective_from' VALUE TO_CHAR(source.period_from, 'YYYY-MM-DD'),
    'effective_to' VALUE TO_CHAR(source.period_to, 'YYYY-MM-DD')
    ABSENT ON NULL RETURNING JSON)
WHERE target.classification IS NULL]';
        END IF;
    END IF;
    SELECT COUNT(*) INTO v_count FROM user_tables WHERE table_name = 'RAG_QUERY_HISTORY';
    IF v_count > 0 THEN
        SELECT COUNT(*) INTO v_count
        FROM user_tab_columns
        WHERE table_name = 'RAG_QUERY_HISTORY' AND column_name = 'CLASSIFICATION_FILTER';
        IF v_count = 0 THEN
            EXECUTE IMMEDIATE 'ALTER TABLE rag_query_history ADD (classification_filter JSON)';
        END IF;
    END IF;
END;
/"""


def support_guide_embeddings_migration_sql() -> str:
    """業務ガイドの質問の例の埋め込みの表（rag_support_guide_embeddings）を無ければ作る（#2398）。

    表を足すだけでデータは変えない。DDL は正本（``oracle_support_guide_embedding_schema_sql``）から
    作る。埋め込みは意味の照合を使うときに作るので、既存の公開の版の行は足さない。
    """
    return _create_missing_objects_sql(
        oracle_support_guide_embedding_schema_sql(), "業務ガイドの埋め込み"
    )


def message_attachments_migration_sql() -> str:
    """質問に添付したファイルの列（USER のメッセージの attachments_json）を無ければ足す（#2517）。

    列を足すだけでデータは変えない。この機能より前の質問は NULL（添付なし）。JSON 型の列は既存の
    index の隠し列と ORA-54015 になる（#1193）ため、JSON の文字列を CLOB で持つ。
    """
    return """DECLARE
    v_count NUMBER;
BEGIN
    SELECT COUNT(*) INTO v_count
    FROM user_tab_columns
    WHERE table_name = 'RAG_MESSAGES' AND column_name = 'ATTACHMENTS_JSON';
    IF v_count = 0 THEN
        EXECUTE IMMEDIATE 'ALTER TABLE rag_messages ADD (attachments_json CLOB)';
    END IF;
END;
/"""


def answer_notice_column_migration_sql(table_name: str) -> str:
    """回答に付けた注意書きの列（answer_notice）を無ければ足す（#2405）。

    列を足すだけでデータは変えない。この機能より前の回答は NULL（注意書きを出していない）。
    """
    return f"""DECLARE
    v_count NUMBER;
BEGIN
    SELECT COUNT(*) INTO v_count
    FROM user_tab_columns
    WHERE table_name = '{table_name}' AND column_name = 'ANSWER_NOTICE';
    IF v_count = 0 THEN
        EXECUTE IMMEDIATE 'ALTER TABLE {table_name.lower()} ADD (answer_notice CLOB)';
    END IF;
END;
/"""


def support_guide_schema_v2_migration_sql() -> str:
    """業務ガイドの内容の形の版を 1 から 2 にする（#2395）。

    版 2 は資料の頁の範囲（``references[].page_ranges``）と手順の参照（``steps[].references``）
    を足しただけで、版 1 の内容はそのまま版 2 として読める。下書きと公開の版の
    ``schema_version`` だけを書き換え、ほかの値・内容の指紋の列は変えない（冪等。表が無ければ
    何もしない）。
    """
    updates = (
        ("RAG_SUPPORT_GUIDES", "rag_support_guides", "draft_json"),
        ("RAG_SUPPORT_GUIDE_REVISIONS", "rag_support_guide_revisions", "content_json"),
    )
    blocks = "\n".join(
        f"""    SELECT COUNT(*) INTO v_count FROM user_tables WHERE table_name = '{table}';
    IF v_count > 0 THEN
        EXECUTE IMMEDIATE q'[UPDATE {name} SET {column} = JSON_TRANSFORM({column}, """
        f"""SET '$.schema_version' = 2) WHERE JSON_VALUE({column}, '$.schema_version' """
        f"""RETURNING NUMBER) = 1]';
    END IF;"""
        for table, name, column in updates
    )
    return f"""DECLARE
    v_count NUMBER;
BEGIN
{blocks}
    COMMIT;
END;
/"""


def document_categories_migration_sql() -> str:
    """文書の分類の一覧の表（rag_document_categories）と索引を無ければ作る（#2131）。

    表と索引を足すだけでデータは変えない。DDL は正本
    （``oracle_document_category_schema_sql``）から作る。
    """
    return _create_missing_objects_sql(oracle_document_category_schema_sql(), "文書の分類の一覧")


def retire_evaluation_compare_migration_sql() -> str:
    """品質評価の設定比較（#2088 で削除）の名残を消す（#2099）。

    - 設定比較の job の行（kind <> 'run'）を消し、job の kind の CHECK を 'run' だけにする。
    - 設定比較だけが書いた列（rag_evaluation_jobs.current_experiment_id・
      rag_evaluation_runs.best_experiment_id）と、その索引（rag_evaluation_runs_best_experiment_idx）
      を消す。
    どの文も、表・列・索引・制約があるときだけ実行し、何度当てても同じ結果になる（冪等）。
    """
    return """DECLARE
    v_count NUMBER;
BEGIN
    SELECT COUNT(*) INTO v_count FROM user_tables WHERE table_name = 'RAG_EVALUATION_JOBS';
    IF v_count > 0 THEN
        EXECUTE IMMEDIATE 'DELETE FROM rag_evaluation_jobs WHERE kind <> ''run''';
        SELECT COUNT(*) INTO v_count
        FROM user_constraints
        WHERE table_name = 'RAG_EVALUATION_JOBS'
          AND constraint_name = 'RAG_EVALUATION_JOBS_KIND_CK';
        IF v_count > 0 THEN
            EXECUTE IMMEDIATE
                'ALTER TABLE rag_evaluation_jobs DROP CONSTRAINT rag_evaluation_jobs_kind_ck';
        END IF;
        EXECUTE IMMEDIATE
            'ALTER TABLE rag_evaluation_jobs ADD CONSTRAINT rag_evaluation_jobs_kind_ck '
            || 'CHECK (kind IN (''run''))';
        SELECT COUNT(*) INTO v_count
        FROM user_tab_columns
        WHERE table_name = 'RAG_EVALUATION_JOBS' AND column_name = 'CURRENT_EXPERIMENT_ID';
        IF v_count > 0 THEN
            EXECUTE IMMEDIATE 'ALTER TABLE rag_evaluation_jobs DROP COLUMN current_experiment_id';
        END IF;
    END IF;
    SELECT COUNT(*) INTO v_count
    FROM user_indexes
    WHERE index_name = 'RAG_EVALUATION_RUNS_BEST_EXPERIMENT_IDX';
    IF v_count > 0 THEN
        EXECUTE IMMEDIATE 'DROP INDEX rag_evaluation_runs_best_experiment_idx';
    END IF;
    SELECT COUNT(*) INTO v_count
    FROM user_tab_columns
    WHERE table_name = 'RAG_EVALUATION_RUNS' AND column_name = 'BEST_EXPERIMENT_ID';
    IF v_count > 0 THEN
        EXECUTE IMMEDIATE 'ALTER TABLE rag_evaluation_runs DROP COLUMN best_experiment_id';
    END IF;
END;
/"""


def run_history_migration_sql() -> str:
    """実行履歴の表（rag_run_history）と索引を無ければ作る（#1890）。

    表と索引を足すだけでデータは変えない。DDL は正本（``oracle_run_history_schema_sql``）から作る。
    """
    return _create_missing_objects_sql(oracle_run_history_schema_sql(), "実行履歴")


def run_history_conversation_index_migration_sql() -> str:
    """実行履歴の会話の索引（rag_run_history_conversation_idx）を無ければ作る（#1954）。

    会話の持ち主にも、持ち主の会話で system_admin が実行した行を見せる条件に使う。索引を足すだけで
    データは変えない。DDL は正本（``oracle_run_history_schema_sql``）の該当の文から作る。
    """
    statements = [
        statement
        for statement in split_sql_statements(oracle_run_history_schema_sql())
        if "_conversation_idx" in statement
    ]
    return _create_missing_objects_sql("\n".join(f"{item};" for item in statements), "実行履歴")


def message_clarification_migration_sql() -> str:
    """回答前に確認した内容の列（USER のメッセージ）を無ければ足す（#1839）。

    列を足すだけでデータは変えない。JSON 型の列を足すと既存の index の隠し列と ORA-54015 になる
    （#1193）ため、JSON の文字列を CLOB で持つ。
    """
    return """DECLARE
    v_count NUMBER;
BEGIN
    SELECT COUNT(*) INTO v_count
    FROM user_tab_columns
    WHERE table_name = 'RAG_MESSAGES' AND column_name = 'CLARIFICATION_JSON';
    IF v_count = 0 THEN
        EXECUTE IMMEDIATE 'ALTER TABLE rag_messages ADD (clarification_json CLOB)';
    END IF;
END;
/"""


def ingestion_job_progress_migration_sql() -> str:
    """取込 job の抽出の段の進捗の列（rag_ingestion_jobs.progress_json）を無ければ足す（#1999）。

    列を足すだけでデータは変えない。JSON 型の列は既存の index の隠し列と ORA-54015 になりうる
    （#1193）ため、JSON の文字列を CLOB で持つ。
    """
    return """DECLARE
    v_count NUMBER;
BEGIN
    SELECT COUNT(*) INTO v_count
    FROM user_tab_columns
    WHERE table_name = 'RAG_INGESTION_JOBS' AND column_name = 'PROGRESS_JSON';
    IF v_count = 0 THEN
        EXECUTE IMMEDIATE 'ALTER TABLE rag_ingestion_jobs ADD (progress_json CLOB)';
    END IF;
END;
/"""


def entity_layer_migration_sql() -> str:
    """実体の層の表(rag_entities / rag_entity_aliases /
    rag_entity_chunks)と索引を無ければ作る(#1362)。

    表と索引を足すだけでデータは変えない。DDL は正本(``oracle_entity_schema_sql``)から作る。
    """
    return _create_missing_objects_sql(oracle_entity_schema_sql(), "実体の層")


def section_chunk_map_migration_sql() -> str:
    """章節と chunk の対応の表(rag_chunk_set_sections / rag_section_chunks)と索引を無ければ
    作る(#1438)。

    表と索引を足すだけでデータは変えない。DDL は正本(``oracle_section_map_schema_sql``)から作る。
    対応は取込の後・章節の保存の後と、MCP で章節を読むときに無ければ作る。
    """
    return _create_missing_objects_sql(oracle_section_map_schema_sql(), "章節と chunk の対応")


def _create_missing_objects_sql(ddl: str, label: str) -> str:
    """表・索引の DDL を、無いものだけを作る PL/SQL にする。"""
    blocks: list[str] = []
    for statement in split_sql_statements(ddl):
        match = _CREATE_OBJECT_PATTERN.match(statement)
        if match is None:
            raise ValueError(f"{label}の DDL に表・索引以外の文があります: {statement[:60]}")
        kind, name = match.group(1).upper(), match.group(2).upper()
        dictionary = (
            "user_tables WHERE table_name" if kind == "TABLE" else "user_indexes WHERE index_name"
        )
        literal_lines = _ddl_literal_lines(statement.replace("'", "''"))
        lines = "\n".join(
            f"            {'|| ' if index else ''}'{line}'"
            for index, line in enumerate(literal_lines)
        )
        blocks.append(
            f"""    SELECT COUNT(*) INTO v_count FROM {dictionary} = '{name}';
    IF v_count = 0 THEN
        EXECUTE IMMEDIATE
{lines};
    END IF;"""
        )
    body = "\n".join(blocks)
    return f"""DECLARE
    v_count NUMBER;
BEGIN
{body}
END;
/"""


_CREATE_OBJECT_PATTERN = re.compile(
    r"^CREATE\s+(?:UNIQUE\s+)?(TABLE|INDEX)\s+([A-Za-z0-9_]+)", re.IGNORECASE
)


def _ddl_literal_lines(statement: str) -> list[str]:
    """DDL を EXECUTE IMMEDIATE の文字列の行にする(行の間は空白 1 つでつなぐ)。"""
    lines = [line.strip() for line in statement.splitlines() if line.strip()]
    return [line + " " if index < len(lines) - 1 else line for index, line in enumerate(lines)]


def document_superseded_migration_sql() -> str:
    """文書の版(置き換えた新しい版の文書と、置き換えた日時)の列を無ければ足す(#1248)。

    列を足すだけでデータは変えない。自己参照の FK は付けない(存在・循環はアプリで検証する)。
    """
    columns = (
        ("SUPERSEDED_BY_DOCUMENT_ID", "superseded_by_document_id VARCHAR2(64)"),
        ("SUPERSEDED_AT", "superseded_at TIMESTAMP WITH TIME ZONE"),
    )
    column_blocks = "\n".join(
        f"""    SELECT COUNT(*) INTO v_count
    FROM user_tab_columns
    WHERE table_name = 'RAG_DOCUMENTS' AND column_name = '{name}';
    IF v_count = 0 THEN
        EXECUTE IMMEDIATE 'ALTER TABLE rag_documents ADD ({definition})';
    END IF;
"""
        for name, definition in columns
    )
    return f"""DECLARE
    v_count NUMBER;
BEGIN
{column_blocks}END;
/"""


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
            f"-- migration: {section.name}\n{section.sql.rstrip()}"
            + ("" if section.sql.rstrip().endswith((";", "/")) else ";")
            for section in resolved_sections
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


@with_system_access
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


def answer_prompt_placeholders_migration_sql() -> str:
    """編集済みの指示を保ち、回答用の placeholder だけを改名する。"""
    return """
UPDATE RAG_ANSWER_PROMPTS
SET CONTENT = REPLACE(REPLACE(CONTENT,
    '{{images}}', '{{answer_context}}'),
    '{{image_metadata}}', '{{evidence_metadata}}')
WHERE PROMPT_KEY = 'vlm_answer'
""".strip()


def answer_prompt_reading_rules_migration_sql() -> str:
    """保存済みの回答テンプレートの本文を保ち、読み方の placeholder を根拠の前に足す。"""
    return """
UPDATE RAG_ANSWER_PROMPTS
SET CONTENT = REPLACE(CONTENT,
    '{{answer_context}}', '{{reading_rules}}' || CHR(10) || CHR(10) || '{{answer_context}}')
WHERE PROMPT_KEY = 'vlm_answer' AND INSTR(CONTENT, '{{reading_rules}}') = 0
""".strip()


def cited_evidence_migration_sql() -> str:
    """保存済み評価入力の引用キーを変更し、画像だけの ID は変更しない。"""
    return """
DECLARE
    v_conflicts NUMBER;
BEGIN
    SELECT COUNT(*) INTO v_conflicts FROM rag_answer_records
    WHERE (JSON_EXISTS(evaluation_input_json, '$.used_images')
           AND JSON_EXISTS(evaluation_input_json, '$.cited_evidence'))
       OR (JSON_EXISTS(evaluation_input_json, '$.used_image_ids')
           AND JSON_EXISTS(evaluation_input_json, '$.cited_evidence_ids'))
       OR JSON_EXISTS(evaluation_input_json, '$.used_images[*].evidence_id');
    IF v_conflicts > 0 THEN
        RAISE_APPLICATION_ERROR(-20001,
            '引用証拠の新旧キーが重複しています。重複を解消してください。');
    END IF;
    UPDATE rag_answer_records
    SET evaluation_input_json = JSON_TRANSFORM(
        evaluation_input_json,
        RENAME '$.used_images' = 'cited_evidence',
        RENAME '$.used_image_ids' = 'cited_evidence_ids',
        RENAME '$.cited_evidence[*].image_id' = 'evidence_id'
    )
    WHERE JSON_EXISTS(evaluation_input_json, '$.used_images')
       OR JSON_EXISTS(evaluation_input_json, '$.used_image_ids');
END;
/
""".strip()


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
