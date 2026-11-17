"""登録済みQ&Aを質問と回答だけにした保存値の migration を確かめる（#5107）。"""

import json
from typing import Any
from uuid import uuid4

import pytest

from app.clients.oracle import OracleClient, _execute, _fetch_all
from app.rag.answer_prompts import EDITABLE_PROMPT_KEYS
from app.rag.approved_faq_5107_migration import (
    REMOVED_PROMPT_KEY,
    removed_approved_faq_fields_sql,
    removed_faq_answer_prompt_sql,
)
from app.rag.oracle_schema import oracle_schema_migration_sections

OLD_FAQ_PAYLOAD = {
    "schema_version": 1,
    "enabled": True,
    "direct_answer_enabled": True,
    "semantic_index_cache": {"items": []},
    "records": [
        {
            "id": "faq-1",
            "question": "合成の質問",
            "approved_answer": "合成の回答",
            "status": "approved",
            "negative_questions": ["合成の別の質問"],
            "target_knowledge_base_ids": ["kb-1"],
            "source_documents": [{"document_id": "doc-1", "version": "v1"}],
            "approval": {"approved_by": "user-1", "approved_at": "2026-11-01T00:00:00+00:00"},
        }
    ],
}


def test_migrations_are_registered_as_destructive() -> None:
    sections = {s.name: s for s in oracle_schema_migration_sections()}
    fields = sections["20261115_5107_approved_faq_fields"]
    prompt = sections["20261115_5107_faq_answer_prompt"]

    assert fields.sql == removed_approved_faq_fields_sql() and fields.destructive_note
    assert prompt.sql == removed_faq_answer_prompt_sql() and prompt.destructive_note
    # FAQ の回答の指示は編集欄にも無い。
    assert REMOVED_PROMPT_KEY not in EDITABLE_PROMPT_KEYS


@pytest.mark.usefixtures("oracle_db")
async def test_migration_removes_only_the_retired_faq_fields() -> None:
    oracle = OracleClient()
    table = "RAG_FAQ_TEST_" + uuid4().hex[:16].upper()

    def verify(connection: Any) -> None:
        _execute(
            connection,
            f"CREATE TABLE {table} (search_answer_profile_id VARCHAR2(64), kind VARCHAR2(32), "
            "payload_json JSON NOT NULL, revision NUMBER(19) DEFAULT 1 NOT NULL, "
            "updated_at TIMESTAMP WITH TIME ZONE)",
            {},
        )
        try:
            for kind in ("approved_faq", "runtime_knowledge"):
                _execute(
                    connection,
                    f"INSERT INTO {table} (search_answer_profile_id, kind, payload_json) "
                    "VALUES ('bv-1', :kind, JSON(:payload))",
                    {"kind": kind, "payload": json.dumps(OLD_FAQ_PAYLOAD, ensure_ascii=False)},
                )
            sql = removed_approved_faq_fields_sql().replace(
                "rag_search_answer_profile_knowledge", table
            )
            # 流し直しても同じ結果（2 回目は当たる行が無い）。
            for _ in range(2):
                _execute(connection, sql, {})
            rows = {
                str(row["kind"]): (row["payload"], int(str(row["revision"])))
                for row in _fetch_all(
                    connection,
                    f"SELECT kind, JSON_SERIALIZE(payload_json RETURNING CLOB) AS payload, "
                    f"revision FROM {table}",
                    {},
                )
            }
        finally:
            _execute(connection, f"DROP TABLE {table} PURGE", {})
        faq_payload, faq_revision = rows["approved_faq"]
        faq = json.loads(str(faq_payload))
        assert faq == {
            "schema_version": 1,
            "enabled": True,
            "records": [
                {
                    "id": "faq-1",
                    "question": "合成の質問",
                    "approved_answer": "合成の回答",
                    "status": "approved",
                }
            ],
        }
        assert faq_revision == 2
        # ほかの種別の行は変えない。
        other_payload, other_revision = rows["runtime_knowledge"]
        assert json.loads(str(other_payload)) == OLD_FAQ_PAYLOAD
        assert other_revision == 1

    await oracle._run_transaction(verify)
