"""開始位置の帰属と、跨るChunkの重複除去を実Oracle SQLで確認する。"""

from typing import Any
from uuid import uuid4

import pytest

from app.clients.oracle import OracleClient
from app.rag.chunking import Chunk, ChunkMetadata
from tests.test_answer_scope_oracle import _indexed_document


@pytest.mark.usefixtures("oracle_db")
async def test_chunk_window_uses_start_page_and_first_element() -> None:
    oracle = OracleClient()
    document_id = await _indexed_document(
        oracle,
        file_name=f"synthetic-{uuid4().hex}.txt",
        chunks=[
            Chunk(
                index=0,
                text="最初の要素から次の要素まで",
                start_offset=0,
                end_offset=20,
                metadata={
                    "element_ids": "first,second",
                    "page_start": 1,
                    "page_end": 2,
                },
            ),
            Chunk(
                index=1,
                text="次の要素",
                start_offset=20,
                end_offset=25,
                metadata={"element_ids": "second", "page_start": 2, "page_end": 2},
            ),
        ],
    )
    recipe = (await oracle.list_document_recipes(document_id))[0]
    chunk_set = str(recipe["active_chunk_set_id"])
    revision = "synthetic-extraction"
    await oracle.upsert_document_extraction_artifact(
        document_id=document_id,
        extraction_recipe_id=revision,
        source_sha256=None,
        status="materialized",
        extraction={
            "elements": [
                {"element_id": "first", "page_number": 1},
                {"element_id": "second", "page_number": 2},
            ]
        },
    )
    await oracle.upsert_chunk_set(
        chunk_set_id=chunk_set, document_id=document_id, extraction_recipe_id=revision
    )
    scopes: list[dict[str, Any]] = [{"page": 2}, {"scope_element_ids": ["second"]}]
    for kwargs in scopes:
        chunks, total = await oracle.list_chunk_set_chunk_window(
            chunk_set, extraction_recipe_id=revision, **kwargs
        )
        assert total == 1
        assert chunks[0].chunk_index == 1
    chunks, total = await oracle.list_chunk_set_chunk_window(
        chunk_set, extraction_recipe_id=revision, scope_element_ids=[]
    )
    assert total == 0 and chunks == []


@pytest.mark.usefixtures("oracle_db")
async def test_unlocated_partition_keeps_unknown_ids_and_version_boundaries() -> None:
    from app.rag.request_context import (
        AuditRequestContext,
        reset_audit_request_context,
        set_audit_request_context,
    )

    oracle = OracleClient()
    metadata_rows: list[ChunkMetadata] = [
        {"element_ids": "first,second"},
        {"element_ids": "second"},
        {},
        {"element_ids": "unknown"},
    ]
    document_id = await _indexed_document(
        oracle,
        file_name=f"synthetic-{uuid4().hex}.txt",
        chunks=[
            Chunk(
                index=index,
                text=f"合成本文{index}",
                start_offset=0,
                end_offset=5,
                metadata=metadata,
            )
            for index, metadata in enumerate(metadata_rows)
        ],
    )
    recipe = (await oracle.list_document_recipes(document_id))[0]
    chunk_set = str(recipe["active_chunk_set_id"])
    revision = f"synthetic-{uuid4().hex}"
    await oracle.upsert_document_extraction_artifact(
        document_id=document_id,
        extraction_recipe_id=revision,
        source_sha256=None,
        status="materialized",
        extraction={"elements": [{"element_id": "first"}, {"element_id": "second"}]},
    )
    # 親抽出版が不明な旧 set では、ID の一致だけで位置を推測しない。
    assert await oracle.count_chunk_set_inspection_locations(
        chunk_set, extraction_recipe_id=revision
    ) == (4, 4)
    await oracle.upsert_chunk_set(
        chunk_set_id=chunk_set, document_id=document_id, extraction_recipe_id=revision
    )
    assert await oracle.count_chunk_set_inspection_locations(
        chunk_set, extraction_recipe_id=revision
    ) == (4, 2)
    located, total = await oracle.list_chunk_set_chunk_window(
        chunk_set, extraction_recipe_id=revision, scope_element_ids=["second"]
    )
    assert total == 1 and located[0].chunk_index == 1
    chunks, total = await oracle.list_chunk_set_chunk_window(
        chunk_set, extraction_recipe_id=revision, unlocated_only=True, offset=1, limit=1
    )
    assert total == 2 and [chunk.chunk_index for chunk in chunks] == [3]
    detail, _ = await oracle.list_chunk_set_chunk_window(
        chunk_set, extraction_recipe_id=revision, unlocated_only=True, chunk_id=chunks[0].chunk_id
    )
    assert detail[0].text == "合成本文3"
    assert await oracle.count_chunk_set_inspection_locations(
        chunk_set, extraction_recipe_id="different-revision"
    ) == (4, 4)
    assert await oracle.count_chunk_set_inspection_locations(
        chunk_set, extraction_recipe_id=revision, location_unavailable=True
    ) == (4, 4)
    token = set_audit_request_context(AuditRequestContext(allowed_document_ids=frozenset()))
    try:
        assert await oracle.count_chunk_set_inspection_locations(
            chunk_set, extraction_recipe_id=revision
        ) == (0, 0)
        assert await oracle.list_chunk_set_chunk_window(
            chunk_set,
            extraction_recipe_id=revision,
            unlocated_only=True,
            chunk_id=chunks[0].chunk_id,
        ) == ([], 0)
    finally:
        reset_audit_request_context(token)
