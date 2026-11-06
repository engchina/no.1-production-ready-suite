"""ナレッジベースの詳細の検索対象の chunk 数を、実 Oracle AI Database で確かめる(#4553)。

検索対象の chunk 数は、所属する INDEXED の文書の配信中の chunk_set(``_SERVED_CHUNK_SET_SQL``)の
chunk だけを数える。再取込で作っている途中の候補(#4115)・INDEXED でない文書・別の KB の文書・
利用範囲の外の文書は数えず、レシピの無い旧データの ``is_serving = 1`` の chunk_set は数える。
合成の文書だけを使い、未到達なら oracle_db fixture が skip し、作成行は後始末する(AI は使わない)。
"""

from __future__ import annotations

from dataclasses import replace
from uuid import uuid4

import pytest

from app.clients.oracle import OracleClient
from app.rag.chunking import Chunk
from app.rag.request_context import (
    current_audit_request_context,
    reset_audit_request_context,
    set_audit_request_context,
)
from app.schemas.document import FileStatus
from app.schemas.extraction import StructuredExtraction
from app.schemas.knowledge_base import KnowledgeBaseStatus

pytestmark = pytest.mark.usefixtures("oracle_db")

_EMBEDDING = [0.1] * 1536


def _chunks(count: int) -> list[Chunk]:
    return [
        Chunk(
            text=f"合成の本文 {index + 1}",
            index=index,
            start_offset=0,
            end_offset=len(f"合成の本文 {index + 1}"),
            metadata={},
        )
        for index in range(count)
    ]


async def _document(client: OracleClient, knowledge_base_id: str) -> tuple[str, str, int]:
    name = f"synthetic-count-{uuid4().hex[:8]}.pdf"
    detail = await client.create_document(
        file_name=name,
        object_storage_path=f"local://{name}",
        content_type="application/pdf",
        knowledge_base_ids=[knowledge_base_id],
    )
    recipe = (await client.list_document_recipes(detail.id))[0]
    return detail.id, str(recipe["recipe_id"]), int(str(recipe.get("config_revision") or 1))


async def _chunk_set(
    client: OracleClient, document_id: str, recipe_id: str | None, count: int
) -> str:
    chunk_set_id = f"cs_count_{uuid4().hex[:16]}"
    await client.upsert_chunk_set(
        chunk_set_id=chunk_set_id, document_id=document_id, recipe_id=recipe_id
    )
    await client.save_index(
        document_id,
        StructuredExtraction(raw_text="合成", confidence=0.9, document_type="合成"),
        _chunks(count),
        [_EMBEDDING] * count,
        chunk_set_id=chunk_set_id,
    )
    await client.mark_chunk_set_indexed(
        chunk_set_id=chunk_set_id, chunk_count=count, vector_count=count
    )
    return chunk_set_id


async def _served_document(
    client: OracleClient, knowledge_base_id: str, count: int, *, status: FileStatus
) -> str:
    document_id, recipe_id, revision = await _document(client, knowledge_base_id)
    chunk_set_id = await _chunk_set(client, document_id, recipe_id, count)
    await client.activate_recipe_chunk_set(
        recipe_id=recipe_id,
        chunk_set_id=chunk_set_id,
        expected_base_chunk_set_id=None,
        materialized_revision=revision,
    )
    await client.update_document_status(document_id, status)
    return document_id


async def test_detail_counts_only_chunks_of_served_chunk_sets_in_scope() -> None:
    client = OracleClient()
    kb = await client.create_knowledge_base(name=f"synthetic-count-{uuid4().hex[:8]}")
    other_kb = await client.create_knowledge_base(name=f"synthetic-count-{uuid4().hex[:8]}")

    # 配信中の 3 件と、同じレシピで作っている途中の候補 2 件(数えない。#4115)。
    served_id, served_recipe, served_revision = await _document(client, kb.id)
    served_set = await _chunk_set(client, served_id, served_recipe, 3)
    await client.activate_recipe_chunk_set(
        recipe_id=served_recipe,
        chunk_set_id=served_set,
        expected_base_chunk_set_id=None,
        materialized_revision=served_revision,
    )
    await _chunk_set(client, served_id, served_recipe, 2)
    await client.update_document_status(served_id, FileStatus.INDEXED)
    # レシピの無い旧データの chunk_set は is_serving = 1 なら数える(4 件)。
    legacy_id, _, _ = await _document(client, kb.id)
    legacy_set = await _chunk_set(client, legacy_id, None, 4)
    await client.set_document_serving_chunk_set(document_id=legacy_id, chunk_set_id=legacy_set)
    await client.update_document_status(legacy_id, FileStatus.INDEXED)
    # INDEXED でない文書(5 件)と、別の KB の文書(6 件)は数えない。
    await _served_document(client, kb.id, 5, status=FileStatus.ERROR)
    await _served_document(client, other_kb.id, 6, status=FileStatus.INDEXED)

    detail = await client.get_knowledge_base(kb.id)
    assert detail is not None
    assert detail.searchable_chunk_count == 3 + 4
    assert detail.document_count == 3
    assert detail.indexed_document_count == 2
    other = await client.get_knowledge_base(other_kb.id)
    assert other is not None
    assert other.searchable_chunk_count == 6

    # 利用範囲の外の文書の chunk は数えない。
    token = set_audit_request_context(
        replace(current_audit_request_context(), allowed_document_ids=frozenset({legacy_id}))
    )
    try:
        scoped = await client.get_knowledge_base(kb.id)
    finally:
        reset_audit_request_context(token)
    assert scoped is not None
    assert scoped.searchable_chunk_count == 4

    empty = await client.create_knowledge_base(name=f"synthetic-count-{uuid4().hex[:8]}")
    empty_detail = await client.get_knowledge_base(empty.id)
    assert empty_detail is not None
    assert empty_detail.searchable_chunk_count == 0


async def test_knowledge_base_config_reads_row_with_the_detail_scope() -> None:
    client = OracleClient()
    kb = await client.create_knowledge_base(
        name=f"synthetic-config-{uuid4().hex[:8]}", retrieval_config={"top_k": 7}
    )
    config = await client.get_knowledge_base_config(kb.id)
    assert config is not None
    assert (config.id, config.status, config.retrieval_config["top_k"]) == (
        kb.id,
        KnowledgeBaseStatus.ACTIVE,
        7,
    )
    assert await client.get_knowledge_base_config(uuid4().hex) is None
    token = set_audit_request_context(
        replace(current_audit_request_context(), allowed_knowledge_base_ids=frozenset())
    )
    try:
        assert await client.get_knowledge_base_config(kb.id) is None
    finally:
        reset_audit_request_context(token)
    await client.archive_knowledge_base(kb.id)
    archived = await client.get_knowledge_base_config(kb.id)
    assert archived is not None
    assert archived.status == KnowledgeBaseStatus.ARCHIVED
