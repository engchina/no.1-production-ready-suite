"""レシピ読取は保存された行だけを返し、旧文書列から補完しない。"""

from __future__ import annotations

import pytest

from app.clients.oracle import OracleClient
from tests.test_oracle_adapter import FakeOraclePool, _oci_settings, _run_inline
from tests.test_oracle_db_paths import (
    _default_recipe_id,
    _recipe_row,
    _statements,
    _write_statements,
)


@pytest.mark.parametrize("status", ["UPLOADED", "REVIEW", "INDEXED"])
async def test_visible_document_without_recipes_has_no_virtual_recipe(status: str) -> None:
    legacy = {
        "document_id": "doc-1",
        "status": status,
        "recipe_count": 0,
        "processing_config": '{"parser_backend":"local"}',
        "preprocess_artifact": '{"object_storage_path":"synthetic/old-prepared"}',
    }
    pool = FakeOraclePool(execute_results=[[], [legacy]])
    client = OracleClient(settings=_oci_settings(), pool=pool, db_call_runner=_run_inline)

    assert await client.list_document_recipes("doc-1") == []
    document_selects = [sql for sql in _statements(pool) if "FROM rag_documents" in sql]
    assert document_selects
    for sql in document_selects:
        projection = sql.split("FROM rag_documents", 1)[0]
        assert "processing_config" not in projection
        assert "preprocess_artifact" not in projection
        assert "extraction" not in projection
        assert "AND 1 = 0" in sql
    assert _write_statements(pool) == []
    assert pool.connection.commits == 0


async def test_missing_saved_default_recipe_does_not_read_legacy_document() -> None:
    pool = FakeOraclePool(execute_results=[[], [{"document_id": "doc-1", "recipe_count": 0}]])
    client = OracleClient(settings=_oci_settings(), pool=pool, db_call_runner=_run_inline)

    assert await client.get_document_recipe("doc-1", _default_recipe_id()) is None
    assert len(pool.connection.calls) == 1
    assert _write_statements(pool) == []
    assert pool.connection.commits == 0


@pytest.mark.parametrize("missing_or_invisible", ["missing", "invisible"])
async def test_recipe_list_keeps_missing_document_boundary(missing_or_invisible: str) -> None:
    pool = FakeOraclePool(execute_results=[[], []])
    client = OracleClient(settings=_oci_settings(), pool=pool, db_call_runner=_run_inline)

    with pytest.raises(KeyError):
        await client.list_document_recipes("doc-1")
    assert "AND 1 = 0" in _statements(pool)[-1]
    assert _write_statements(pool) == []


@pytest.mark.parametrize("slot", [1, 2])
async def test_actual_saved_recipe_id_and_scope_are_preserved(slot: int) -> None:
    row = _recipe_row(recipe_id="saved-recipe-uuid", slot_no=slot)
    pool = FakeOraclePool(execute_results=[[row], [row]])
    client = OracleClient(settings=_oci_settings(), pool=pool, db_call_runner=_run_inline)

    listed = await client.list_document_recipes("doc-1")
    actual = await client.get_document_recipe("doc-1", "saved-recipe-uuid")
    assert [item["recipe_id"] for item in listed] == ["saved-recipe-uuid"]
    assert listed[0]["slot_no"] == slot
    assert actual is not None and actual["recipe_id"] == "saved-recipe-uuid"
    assert "AND 1 = 0" in _statements(pool)[0]
    assert "AND 1 = 0" in _statements(pool)[1]
    assert _write_statements(pool) == []
