"""chunk_set 永続化の実 Oracle 統合テスト(3 層モデル: 文書単位 serving)。

実 Oracle 26ai を使い、文書単位 serving(is_serving)の確定/付け替え、所属 KB の
membership 由来導出、save_index の chunk_set スコープ、extraction 層の永続化を検証する。
未到達なら oracle_db fixture が skip し、作成行は cleanup_to_baseline で後始末する。
"""

import pytest

from app.clients.oracle import OracleClient
from app.rag.chunking import Chunk
from app.rag.ingestion import _coerce_extraction_payload, _validate_structured_extraction_payload
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
async def test_set_document_serving_chunk_set_marks_exactly_one_serving() -> None:
    """3 層モデル: set_document_serving_chunk_set は指定 1 つだけ is_serving=1、他は 0。"""
    client = OracleClient()
    document_id = await _new_document(client)
    cs_a = "cs_serving_aaaaaaaaaa"
    cs_b = "cs_serving_bbbbbbbbbb"
    await client.upsert_chunk_set(chunk_set_id=cs_a, document_id=document_id)
    await client.upsert_chunk_set(chunk_set_id=cs_b, document_id=document_id)

    # 既定は両方 serving(DEFAULT 1)。cs_a を serving に確定すると cs_b は 0 になる。
    await client.set_document_serving_chunk_set(document_id=document_id, chunk_set_id=cs_a)
    got_a = await client.get_chunk_set(cs_a)
    got_b = await client.get_chunk_set(cs_b)
    assert got_a is not None and int(str(got_a["is_serving"])) == 1
    assert got_b is not None and int(str(got_b["is_serving"])) == 0

    # serving を cs_b へ付け替える(Phase 3 の昇格相当)と入れ替わる。
    await client.set_document_serving_chunk_set(document_id=document_id, chunk_set_id=cs_b)
    swapped_a = await client.get_chunk_set(cs_a)
    swapped_b = await client.get_chunk_set(cs_b)
    assert swapped_a is not None and int(str(swapped_a["is_serving"])) == 0
    assert swapped_b is not None and int(str(swapped_b["is_serving"])) == 1


@pytest.mark.usefixtures("oracle_db")
async def test_list_document_chunk_sets_derives_membership_and_serving() -> None:
    """3 層モデル: 所属 KB は文書 membership、配信は cs.is_serving から導出する(binding 非依存)。"""
    client = OracleClient()
    document_id = await _new_document(client)
    cs_a = "cs_list_aaaaaaaaaaaa"
    cs_b = "cs_list_bbbbbbbbbbbb"
    await client.upsert_chunk_set(chunk_set_id=cs_a, document_id=document_id)
    await client.upsert_chunk_set(chunk_set_id=cs_b, document_id=document_id)

    kb_1 = await client.create_knowledge_base(name="一覧KB-1")
    kb_2 = await client.create_knowledge_base(name="一覧KB-2")
    await client.assign_documents_to_knowledge_base(kb_1.id, [document_id])
    await client.assign_documents_to_knowledge_base(kb_2.id, [document_id])

    # cs_a を serving に確定(cs_b は demote)。
    await client.set_document_serving_chunk_set(document_id=document_id, chunk_set_id=cs_a)

    rows = {
        str(row["chunk_set_id"]): row for row in await client.list_document_chunk_sets(document_id)
    }
    member_ids = {kb_1.id, kb_2.id}

    def _ids(value: object) -> set[str]:
        assert isinstance(value, list)
        return {str(item) for item in value}

    # 所属 KB は全 chunk_set 共通(文書 membership。既定 KB を含み得る)。
    a_members = _ids(rows[cs_a]["knowledge_base_ids"])
    b_members = _ids(rows[cs_b]["knowledge_base_ids"])
    assert member_ids <= a_members
    assert a_members == b_members
    # 配信中の chunk_set だけ全 membership が serving、非配信は空。
    assert _ids(rows[cs_a]["serving_knowledge_base_ids"]) == a_members
    assert _ids(rows[cs_b]["serving_knowledge_base_ids"]) == set()


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


@pytest.mark.usefixtures("oracle_db")
async def test_document_extraction_upsert_get_list_and_gc() -> None:
    """extraction 層(#6 P1b)の永続化: upsert/get/list/mark/GC が実 Oracle で動く。

    migration が rag_document_extractions 表 + rag_chunk_sets.extraction_id を作る前提
    (ensure_schema が適用)。1 文書が複数抽出(preprocess×parser)を持てることの永続層。
    """
    client = OracleClient()
    document_id = await _new_document(client)
    extraction = StructuredExtraction(raw_text="抽出本文の一文目です。")

    ex_a = "ex_test_aaaaaaaaaa01"
    ex_b = "ex_test_bbbbbbbbbb02"
    await client.upsert_document_extraction(
        extraction_id=ex_a,
        document_id=document_id,
        extraction=extraction,
        recipe_subset={"preprocess": "none", "parser": "docling"},
        status="EXTRACTED",
    )
    await client.upsert_document_extraction(
        extraction_id=ex_b,
        document_id=document_id,
        extraction=extraction,
        recipe_subset={"preprocess": "none", "parser": "unstructured"},
        status="EXTRACTED",
    )

    got = await client.get_document_extraction(ex_a)
    assert got is not None
    assert got["status"] == "EXTRACTED"
    assert got["document_id"] == document_id
    assert got["extraction_json"]  # 抽出 payload が保存されている
    # #6 P1c の index 読み経路(get → coerce → validate)が実 Oracle JSON 列を往復できる。
    payload = _coerce_extraction_payload(got["extraction_json"])
    assert payload is not None
    assert _validate_structured_extraction_payload(payload).raw_text == "抽出本文の一文目です。"

    assert set(await client.list_document_extraction_ids(document_id)) == {ex_a, ex_b}

    await client.mark_document_extraction(extraction_id=ex_a, status="ERROR")
    reloaded = await client.get_document_extraction(ex_a)
    assert reloaded is not None and reloaded["status"] == "ERROR"

    # ex_b 以外を残す GC → ex_a が消える。
    removed = await client.delete_document_extractions_except(
        document_id=document_id, keep_extraction_ids=[ex_b]
    )
    assert removed == [ex_a]
    assert await client.list_document_extraction_ids(document_id) == [ex_b]
