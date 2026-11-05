"""正式な分割結果scopeと用途位置の実Oracle往復を検証する。"""

import json
from uuid import uuid4

import pytest

from app.clients.oracle import OracleClient
from app.rag.chunking import Chunk
from app.schemas.extraction import StructuredExtraction
from tests.test_image_text_source_storage import synthetic_chunks


@pytest.mark.usefixtures("oracle_db")
async def test_image_source_scope_survives_save_and_embedding_update() -> None:
    client = OracleClient()
    document = await client.create_document(
        file_name="synthetic.pdf",
        object_storage_path="oci://bucket/synthetic.pdf",
        content_type="application/pdf",
    )
    recipe = await client.ensure_default_document_recipe(document.id)
    chunk_set_id = f"source_scope_{uuid4().hex}"
    await client.upsert_chunk_set(
        chunk_set_id=chunk_set_id,
        document_id=document.id,
        recipe_id=str(recipe["recipe_id"]),
        status="CHUNKED",
    )
    chunks, _ = synthetic_chunks()
    await client.save_chunk_preview(
        document.id, StructuredExtraction(raw_text="合成本文"), chunks, chunk_set_id=chunk_set_id
    )
    saved = await client.list_chunk_set_chunks(chunk_set_id)
    assert len(saved) == len(chunks)
    scopes = {}
    updates = []
    for row in saved:
        scope = json.loads(str(row.metadata["image_text_source_scope_json"]))
        assert scope["document_id"] == document.id
        assert scope["recipe_id"] == recipe["recipe_id"]
        assert scope["chunk_set_id"] == chunk_set_id
        scopes[row.chunk_id] = scope
        updates.append(
            Chunk(
                index=row.chunk_index,
                text=row.text,
                start_offset=0,
                end_offset=len(row.text),
                metadata={
                    **{
                        key: value
                        for key, value in row.metadata.items()
                        if isinstance(value, (str, int, float)) or value is None
                    },
                    "document_id": document.id,
                    "chunk_id": row.chunk_id,
                },
            )
        )
    await client.update_chunk_set_embeddings(
        chunk_set_id=chunk_set_id, chunks=updates, embeddings=[[0.1] * 1536] * len(updates)
    )
    reread = await client.list_chunk_set_chunks(chunk_set_id)
    assert {
        row.chunk_id: json.loads(str(row.metadata["image_text_source_scope_json"]))
        for row in reread
    } == scopes
