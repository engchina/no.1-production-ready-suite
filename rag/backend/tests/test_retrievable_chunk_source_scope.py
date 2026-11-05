"""最終検証の単件読取は、保存metadataではなく正式行の出典を返す。"""

from __future__ import annotations

import json
from typing import Any

import pytest

from app.clients.oracle import OracleClient, _oracle_retrieval_where
from app.schemas.search import INCLUDE_SUPERSEDED_FILTER_KEY
from tests.test_oracle_adapter import FakeOraclePool, _oci_settings, _run_inline


def chunk_row(**changes: Any) -> dict[str, Any]:
    return {
        "document_id": "document-a",
        "chunk_id": "chunk-a",
        "chunk_set_id": "set-a",
        "recipe_id": "recipe-a",
        "chunk_text": "合成の原文は42です。",
        "chunk_index": 0,
        "file_name": "synthetic.txt",
        "metadata_json": json.dumps(
            {
                "document_id": "stale-doc",
                "chunk_set_id": "stale-set",
                "recipe_id": "stale-recipe",
                "retained": "original",
            }
        ),
        **changes,
    }


def client_for(row: dict[str, Any] | None) -> tuple[OracleClient, FakeOraclePool]:
    pool = FakeOraclePool(execute_results=[[row] if row is not None else []])
    return OracleClient(settings=_oci_settings(), pool=pool, db_call_runner=_run_inline), pool


async def test_single_chunk_reads_formal_recipe_and_overrides_stale_scope() -> None:
    client, pool = client_for(chunk_row())
    hit = await client.retrievable_chunk("document-a", "chunk-a")
    assert hit is not None
    assert hit.metadata["document_id"] == "document-a"
    assert hit.metadata["chunk_set_id"] == "set-a"
    assert hit.metadata["recipe_id"] == "recipe-a"
    assert hit.metadata["retained"] == "original"
    assert hit.text == "合成の原文は42です。"
    assert len(pool.connection.calls) == 1
    statement = pool.connection.calls[0].statement
    assert "cs.recipe_id" in statement.partition(" FROM ")[0]
    assert (
        "LEFT JOIN rag_chunk_sets cs ON cs.chunk_set_id = c.chunk_set_id "
        "AND cs.document_id = c.document_id" in statement
    )
    assert pool.connection.commits == 0
    assert not pool.connection.many_calls


@pytest.mark.parametrize("recipe", [None, ""])
async def test_missing_formal_recipe_never_falls_back_to_saved_metadata(recipe: str | None) -> None:
    client, _ = client_for(chunk_row(recipe_id=recipe))
    hit = await client.retrievable_chunk("document-a", "chunk-a")
    assert hit is not None
    assert "recipe_id" not in hit.metadata
    assert hit.metadata["chunk_set_id"] == "set-a"
    assert hit.text == "合成の原文は42です。"


async def test_legacy_no_chunk_set_preserves_text_but_not_forged_scope() -> None:
    client, _ = client_for(chunk_row(chunk_set_id=None, recipe_id=None))
    hit = await client.retrievable_chunk("document-a", "chunk-a")
    assert hit is not None
    assert hit.metadata["document_id"] == "document-a"
    assert "chunk_set_id" not in hit.metadata
    assert "recipe_id" not in hit.metadata
    assert hit.text == "合成の原文は42です。"


@pytest.mark.parametrize("present", [True, False])
async def test_existing_acl_and_serving_filter_and_bindings_are_unchanged(present: bool) -> None:
    client, pool = client_for(chunk_row() if present else None)
    hit = await client.retrievable_chunk("document-a", "chunk-a")
    assert (hit is not None) is present
    assert len(pool.connection.calls) == 1
    sql = pool.connection.calls[0]
    where, binds = _oracle_retrieval_where(
        {INCLUDE_SUPERSEDED_FILTER_KEY: "true"}, effective_period=False
    )
    expected = " ".join(where.split())
    assert (
        sql.statement.split(" WHERE ", 1)[1]
        == expected + " AND c.document_id = :read_document_id AND c.chunk_id = :read_chunk_id"
    )
    assert sql.parameters == {**binds, "read_document_id": "document-a", "read_chunk_id": "chunk-a"}
    assert pool.connection.commits == 0
    assert not pool.connection.many_calls
