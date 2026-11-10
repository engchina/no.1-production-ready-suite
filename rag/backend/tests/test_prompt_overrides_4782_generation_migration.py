"""編集欄を外した指示の保存値だけを消す migration を確かめる（#4782）。"""

from typing import Any
from uuid import uuid4

import pytest

from app.clients.oracle import OracleClient, _execute, _fetch_all
from app.rag.answer_prompts import EDITABLE_PROMPT_KEYS, PROMPT_STAGES
from app.rag.oracle_schema import oracle_schema_migration_sections
from app.rag.prompt_overrides_4782_generation_migration import (
    REMOVED_PROMPT_KEYS,
    removed_prompt_overrides_sql,
)

# 編集欄を残す key と、指示の統合が残した控え。消さない。
KEPT_KEYS = (*EDITABLE_PROMPT_KEYS, "raptor_summary_archived")


def test_migration_is_registered_as_destructive_and_removes_no_editable_key() -> None:
    section = next(
        s
        for s in oracle_schema_migration_sections()
        if s.name == "20261110_4782_generation_prompt_overrides"
    )
    assert section.sql == removed_prompt_overrides_sql()
    assert section.destructive_note
    assert set(REMOVED_PROMPT_KEYS).isdisjoint(EDITABLE_PROMPT_KEYS)
    # 段の一覧にある部分で編集欄の無いものは、取込の key で保存する部分を除いてすべて消す対象。
    for key in ("generation.role", "faq_answer.system", "image_retrieval"):
        assert key in EDITABLE_PROMPT_KEYS
    listed = {f"{stage.id}.{part}" for stage in PROMPT_STAGES for part, _ref in stage.parts}
    assert {key for key in REMOVED_PROMPT_KEYS if "." in key} - listed == {
        # 役割の文と固定の部分に分けて無くなった部分。
        "generation.system",
    }


@pytest.mark.usefixtures("oracle_db")
async def test_migration_removes_only_the_rows_without_an_edit_field() -> None:
    oracle = OracleClient()
    table = "RAG_PROMPT_TEST_" + uuid4().hex[:16].upper()

    def verify(connection: Any) -> None:
        _execute(
            connection,
            f"CREATE TABLE {table} (prompt_key VARCHAR2(64) PRIMARY KEY, "
            "content CLOB, updated_at TIMESTAMP)",
            {},
        )
        try:
            for key in (*REMOVED_PROMPT_KEYS, *KEPT_KEYS):
                _execute(
                    connection,
                    f"INSERT INTO {table} (prompt_key, content) VALUES (:key, :content)",
                    {"key": key, "content": f"合成の保存値 {key}"},
                )
            sql = removed_prompt_overrides_sql().replace("RAG_ANSWER_PROMPTS", table)
            for _ in range(2):
                _execute(connection, sql, {})
            rows = {
                str(row["prompt_key"]): str(row["content"])
                for row in _fetch_all(connection, f"SELECT prompt_key, content FROM {table}", {})
            }
            assert rows == {key: f"合成の保存値 {key}" for key in KEPT_KEYS}
        finally:
            _execute(connection, f"DROP TABLE {table} PURGE", {})

    await oracle._run_transaction(verify)
