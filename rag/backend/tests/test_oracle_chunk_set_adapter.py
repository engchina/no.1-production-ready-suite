"""variant chunk_set / KB binding 永続化の実 Oracle 統合テスト(dedup / refcount / GC)。

実 Oracle 26ai を使い、共有(refcount)・他 KB 参照中は GC しない・refcount 0 で GC、を検証する。
未到達なら oracle_db fixture が skip し、作成行は cleanup_to_baseline で後始末する。
"""

import pytest

from app.clients.oracle import OracleClient
from app.rag.chunking import Chunk
from app.schemas.document import FileStatus
from app.schemas.extraction import StructuredExtraction

_EMBEDDING = [0.1] * 1536


def _chunks(prefix: str, count: int) -> list[Chunk]:
    return [
        Chunk(index=i, text=f"{prefix}{i}", start_offset=i * 4, end_offset=i * 4 + 2)
        for i in range(count)
    ]


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


@pytest.mark.usefixtures("oracle_db")
async def test_save_index_chunk_set_scope_keeps_other_chunk_sets() -> None:
    """save_index(chunk_set_id=...) はその chunk_set だけ置換し、他 chunk_set の chunk を残す。"""
    client = OracleClient()
    document_id = await _new_document(client)
    extraction = StructuredExtraction(raw_text="本文", confidence=0.9)
    cs_a = "cs_scope_aaaaaaaaaa"
    cs_b = "cs_scope_bbbbbbbbbb"

    # chunk_set A: 2 chunk、chunk_set B: 3 chunk を共存させる。
    await client.save_index(
        document_id, extraction, _chunks("A", 2), [_EMBEDDING] * 2, chunk_set_id=cs_a
    )
    await client.save_index(
        document_id, extraction, _chunks("B", 3), [_EMBEDDING] * 3, chunk_set_id=cs_b
    )
    # count_document_chunks は INDEXED 文書のみ数えるため状態を進める。
    await client.update_document_status(document_id, FileStatus.INDEXED)
    # B 保存で A は消えていない(scoped delete)= 2 + 3 = 5。
    assert await client.count_document_chunks(document_id) == 5

    # A を 1 chunk で置換 → A の 2 は消え 1 追加、B の 3 は不変 = 4。
    await client.save_index(
        document_id, extraction, _chunks("A", 1), [_EMBEDDING], chunk_set_id=cs_a
    )
    assert await client.count_document_chunks(document_id) == 4


@pytest.mark.usefixtures("oracle_db")
async def test_save_index_without_chunk_set_replaces_all_chunks() -> None:
    """chunk_set_id 未指定(現行挙動)は文書の全 chunk を置換する(後方互換)。"""
    client = OracleClient()
    document_id = await _new_document(client)
    extraction = StructuredExtraction(raw_text="本文", confidence=0.9)

    await client.save_index(document_id, extraction, _chunks("X", 3), [_EMBEDDING] * 3)
    await client.update_document_status(document_id, FileStatus.INDEXED)
    assert await client.count_document_chunks(document_id) == 3
    # 再保存は全置換(2 件)= 2(加算されない)。
    await client.save_index(document_id, extraction, _chunks("Y", 2), [_EMBEDDING] * 2)
    assert await client.count_document_chunks(document_id) == 2
