"""再取込の候補を作っている間も、検索は旧版だけを見て、切替で新版だけになる（#4115。実 Oracle）。"""

from uuid import uuid4

import pytest

from app.clients.oracle import OracleClient
from app.rag.chunking import Chunk
from app.schemas.document import FileStatus
from app.schemas.extraction import StructuredExtraction


async def _chunk_set(
    client: OracleClient, document_id: str, recipe_id: str, chunk_set_id: str, extraction_id: str
) -> None:
    await client.upsert_document_extraction_artifact(
        document_id=document_id,
        extraction_recipe_id=extraction_id,
        source_sha256="a" * 64,
        extraction=StructuredExtraction(raw_text=chunk_set_id).to_document_payload(),
        status="materialized",
    )
    await client.upsert_chunk_set(
        chunk_set_id=chunk_set_id,
        document_id=document_id,
        recipe_id=recipe_id,
        extraction_recipe_id=extraction_id,
    )
    await client.save_chunk_preview(
        document_id,
        StructuredExtraction(raw_text=chunk_set_id),
        [Chunk(index=0, text=chunk_set_id, start_offset=0, end_offset=len(chunk_set_id))],
        chunk_set_id=chunk_set_id,
    )
    await client.mark_chunk_set_indexed(chunk_set_id=chunk_set_id, chunk_count=1, vector_count=0)


@pytest.mark.usefixtures("oracle_db")
async def test_candidate_stays_hidden_until_switch_and_old_extraction_is_collected() -> None:
    client = OracleClient()
    document = await client.create_document(
        file_name="synthetic-reingest.txt",
        object_storage_path="synthetic-reingest.txt",
        content_type="text/plain",
    )
    recipe = await client.ensure_default_document_recipe(document.id)
    recipe_id = str(recipe["recipe_id"])
    suffix = uuid4().hex[:12]
    old_set, new_set = f"old_{suffix}", f"new_{suffix}"
    old_extraction, new_extraction = f"ex_old_{suffix}", f"ex_new_{suffix}"

    assert await client.document_has_served_output(document.id) is False

    await _chunk_set(client, document.id, recipe_id, old_set, old_extraction)
    await client.activate_recipe_chunk_set(
        recipe_id=recipe_id, chunk_set_id=old_set, extraction_recipe_id=old_extraction
    )
    activated = await client.get_document(document.id)
    assert activated is not None and activated.status == FileStatus.INDEXED
    first_indexed_at = activated.indexed_at
    [old_chunk] = await client.list_chunk_set_chunks(old_set)

    assert await client.document_has_served_output(document.id) is True
    assert await client.served_extraction_ids(document.id) == {old_extraction}

    # 再構築の途中: 候補はレシピに結び付くが、検索には出ない。
    await _chunk_set(client, document.id, recipe_id, new_set, new_extraction)
    [new_chunk] = await client.list_chunk_set_chunks(new_set)
    assert await client.retrievable_chunk(document.id, old_chunk.chunk_id) is not None
    assert await client.retrievable_chunk(document.id, new_chunk.chunk_id) is None
    assert await client.served_extraction_ids(document.id) == {old_extraction}
    corpus = await client.evaluation_corpus_rows([])
    corpus_sets = {row["chunk_set_id"] for row in corpus if row["document_id"] == document.id}
    assert corpus_sets == {old_set}

    # 切替: 新版だけになり、旧版だけが使っていた抽出も消える。
    await client.activate_recipe_chunk_set(
        recipe_id=recipe_id, chunk_set_id=new_set, extraction_recipe_id=new_extraction
    )
    assert await client.retrievable_chunk(document.id, new_chunk.chunk_id) is not None
    assert await client.retrievable_chunk(document.id, old_chunk.chunk_id) is None
    assert await client.served_extraction_ids(document.id) == {new_extraction}
    assert (
        await client.get_document_extraction_artifact(
            document_id=document.id, extraction_recipe_id=old_extraction
        )
        is None
    )
    switched = await client.get_document(document.id)
    assert switched is not None and switched.status == FileStatus.INDEXED
    assert switched.indexed_at is not None and first_indexed_at is not None
    assert switched.indexed_at >= first_indexed_at


@pytest.mark.usefixtures("oracle_db")
async def test_recipe_checkpoints_are_cleared_only_for_that_recipe() -> None:
    """既定レシピの区間の checkpoint だけを消す SQL が実 Oracle で通る。"""
    client = OracleClient()
    document = await client.create_document(
        file_name="synthetic-reingest-checkpoint.txt",
        object_storage_path="synthetic-reingest-checkpoint.txt",
        content_type="text/plain",
    )
    recipe = await client.ensure_default_document_recipe(document.id)
    await client.delete_recipe_segment_checkpoints(document.id, str(recipe["recipe_id"]))
