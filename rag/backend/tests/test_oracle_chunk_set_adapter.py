"""variant chunk_set / KB binding 永続化の実 Oracle 統合テスト(dedup / refcount / GC)。

実 Oracle 26ai を使い、共有(refcount)・他 KB 参照中は GC しない・refcount 0 で GC、を検証する。
未到達なら oracle_db fixture が skip し、作成行は cleanup_to_baseline で後始末する。
"""

import pytest

from app.clients.oracle import OracleClient


async def _new_document(client: OracleClient) -> str:
    detail = await client.create_document(
        file_name="variant-doc.txt",
        object_storage_path="oci://bucket/variant-doc.txt",
        content_type="text/plain",
    )
    return detail.id


@pytest.mark.usefixtures("oracle_db")
async def test_chunk_set_dedup_refcount_and_gc() -> None:
    """共有 chunk_set の refcount と、他 KB 参照中は GC しない / refcount 0 で GC を検証。"""
    client = OracleClient()
    document_id = await _new_document(client)

    cs_a = "cs_test_shared_aaaaaa"
    cs_b = "cs_test_other_bbbbbbb"
    await client.upsert_chunk_set(chunk_set_id=cs_a, document_id=document_id)
    await client.upsert_chunk_set(chunk_set_id=cs_b, document_id=document_id)

    # kb-1, kb-2 が cs_a を共有、kb-3 が cs_b を参照。
    await client.upsert_chunk_set_binding(
        knowledge_base_id="kb-1", document_id=document_id, chunk_set_id=cs_a
    )
    await client.upsert_chunk_set_binding(
        knowledge_base_id="kb-2", document_id=document_id, chunk_set_id=cs_a
    )
    await client.upsert_chunk_set_binding(
        knowledge_base_id="kb-3", document_id=document_id, chunk_set_id=cs_b
    )

    assert await client.chunk_set_refcount(cs_a) == 2
    assert await client.chunk_set_refcount(cs_b) == 1
    assert set(await client.list_document_chunk_set_ids(document_id)) == {cs_a, cs_b}

    # すべて参照中 → GC は何も消さない。
    assert await client.collect_unreferenced_chunk_sets(document_id) == []

    # kb-1 を外しても cs_a は kb-2 が参照中 → GC されない。
    await client.delete_chunk_set_binding(
        knowledge_base_id="kb-1", document_id=document_id, chunk_set_id=cs_a
    )
    assert await client.chunk_set_refcount(cs_a) == 1
    assert await client.collect_unreferenced_chunk_sets(document_id) == []

    # kb-2 も外すと refcount 0 → GC で cs_a だけ削除、cs_b は残る。
    await client.delete_chunk_set_binding(
        knowledge_base_id="kb-2", document_id=document_id, chunk_set_id=cs_a
    )
    assert await client.chunk_set_refcount(cs_a) == 0
    assert await client.collect_unreferenced_chunk_sets(document_id) == [cs_a]
    assert set(await client.list_document_chunk_set_ids(document_id)) == {cs_b}


@pytest.mark.usefixtures("oracle_db")
async def test_upsert_chunk_set_is_idempotent_and_mark_indexed() -> None:
    """upsert は冪等(重複行を作らない)、mark_chunk_set_indexed が status/件数を更新する。"""
    client = OracleClient()
    document_id = await _new_document(client)
    cs = "cs_test_idempotent000"

    await client.upsert_chunk_set(chunk_set_id=cs, document_id=document_id)
    await client.upsert_chunk_set(chunk_set_id=cs, document_id=document_id)
    assert await client.list_document_chunk_set_ids(document_id) == [cs]

    before = await client.get_chunk_set(cs)
    assert before is not None
    assert before["status"] == "INGESTING"

    await client.mark_chunk_set_indexed(chunk_set_id=cs, chunk_count=12, vector_count=12)
    after = await client.get_chunk_set(cs)
    assert after is not None
    assert after["status"] == "INDEXED"
    assert after["chunk_count"] == 12
    assert after["vector_count"] == 12
