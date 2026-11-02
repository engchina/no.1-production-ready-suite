"""文書レシピの版の切替の履歴（#4167）。Iceberg の snapshot log に当たる。

active の切替と同じ transaction で 1 行を足す。どの版からどの版へ、何を入力に変わったかを、
旧版の行が消えた後も追えるようにする。そのため chunk_set には FK を張らない（文書・レシピの削除では
一緒に消える）。

入力は結果を変えるものの識別子だけを持ち、本文・質問などの業務の内容を入れない。
"""

from __future__ import annotations

from enum import StrEnum

RECIPE_VERSION_LOG_TABLE = "rag_recipe_version_log"


class RecipeVersionOperation(StrEnum):
    """切替の種類。``ROLLBACK`` は戻す操作（#4170）が使う。"""

    INITIAL = "INITIAL"
    REPROCESS = "REPROCESS"
    ROLLBACK = "ROLLBACK"


def recipe_version_log_schema_sql() -> str:
    return """
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
""".strip()


INSERT_RECIPE_VERSION_LOG_SQL = """
INSERT INTO rag_recipe_version_log (
    log_id, document_id, recipe_id, chunk_set_id, parent_chunk_set_id, operation,
    source_sha256, extraction_recipe_id, chunk_set_input_id, recipe_revision,
    language_pack_release_id, language_pack_ingest_hash, chunk_count, job_id
)
SELECT
    :log_id, cs.document_id, r.recipe_id, cs.chunk_set_id, :parent_chunk_set_id, :operation,
    d.content_sha256, COALESCE(:extraction_recipe_id, cs.extraction_recipe_id),
    :chunk_set_input_id, r.materialized_revision,
    cs.language_pack_release_id, cs.language_pack_ingest_hash, cs.chunk_count, :job_id
FROM rag_chunk_sets cs
JOIN rag_document_recipes r ON r.recipe_id = :recipe_id
JOIN rag_documents d ON d.document_id = cs.document_id
WHERE cs.chunk_set_id = :chunk_set_id
"""
