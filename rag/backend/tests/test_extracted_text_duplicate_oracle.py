"""解析後の本文で見る文書の重複の SQL（#5313。実 Oracle）。fixture の本文はすべて合成。"""

from __future__ import annotations

from uuid import uuid4

import pytest

from app.clients.oracle import OracleClient, _execute
from app.rag.extracted_text_duplicate import extracted_text_sha256


async def _document(
    client: OracleClient, name: str, knowledge_base_ids: list[str], *, minutes_ago: int
) -> str:
    """登録の順を固定した文書。uploaded_at は秒の単位で、続けて作ると同じ値になる。"""
    document = await client.create_document(
        file_name=f"synthetic-{name}.json",
        object_storage_path=f"local://text-duplicate/{uuid4().hex}.json",
        content_type="application/json",
        knowledge_base_ids=knowledge_base_ids,
    )
    await client._run_transaction(
        lambda connection: _execute(
            connection,
            "UPDATE rag_documents"
            " SET uploaded_at = SYSTIMESTAMP - NUMTODSINTERVAL(:minutes_ago, 'MINUTE')"
            " WHERE document_id = :document_id",
            {"minutes_ago": minutes_ago, "document_id": document.id},
        )
    )
    return document.id


async def _duplicate_of(client: OracleClient, document_id: str) -> str | None:
    detail = await client.get_document(document_id)
    assert detail is not None
    return detail.duplicate_of_document_id


@pytest.mark.usefixtures("oracle_db")
async def test_later_document_in_shared_kb_with_same_text_becomes_duplicate() -> None:
    client = OracleClient()
    suffix = uuid4().hex[:12]
    kb = await client.create_knowledge_base(name=f"text-dup-{suffix}", description="合成")
    other_kb = await client.create_knowledge_base(name=f"text-dup-other-{suffix}")
    text_hash = extracted_text_sha256(f"合成の本文 {suffix}")
    first = await _document(client, f"first-{suffix}", [kb.id], minutes_ago=40)
    other = await _document(client, f"other-kb-{suffix}", [other_kb.id], minutes_ago=30)
    differing = await _document(client, f"differing-{suffix}", [kb.id], minutes_ago=20)
    second = await _document(client, f"second-{suffix}", [kb.id], minutes_ago=10)

    assert await client.record_extracted_text_hash(first, text_hash, judge_duplicate=True) is None
    # 別のナレッジベースの文書は正本にしない。
    assert await client.record_extracted_text_hash(other, text_hash, judge_duplicate=True) is None
    # 本文が 1 字違えば重複にしない。
    assert (
        await client.record_extracted_text_hash(
            differing, extracted_text_sha256(f"合成の本文 {suffix}。"), judge_duplicate=True
        )
        is None
    )
    # 判定しない記録(索引のある文書の再処理)は、重複にしない。
    assert await client.record_extracted_text_hash(second, text_hash, judge_duplicate=False) is None
    assert await _duplicate_of(client, second) is None

    assert await client.record_extracted_text_hash(second, text_hash, judge_duplicate=True) == first

    assert await _duplicate_of(client, second) == first
    assert await _duplicate_of(client, first) is None
    assert await _duplicate_of(client, other) is None
    assert await _duplicate_of(client, differing) is None
    # 既に重複の文書は判定し直さない。
    assert await client.record_extracted_text_hash(second, text_hash, judge_duplicate=True) is None
    assert await _duplicate_of(client, second) == first


@pytest.mark.usefixtures("oracle_db")
async def test_later_or_superseded_or_duplicate_documents_are_not_canonical() -> None:
    client = OracleClient()
    suffix = uuid4().hex[:12]
    kb = await client.create_knowledge_base(name=f"text-dup-order-{suffix}")
    text_hash = extracted_text_sha256(f"合成の本文 {suffix}")
    old_version = await _document(client, f"old-version-{suffix}", [kb.id], minutes_ago=40)
    new_version = await _document(client, f"new-version-{suffix}", [kb.id], minutes_ago=30)
    earlier = await _document(client, f"earlier-{suffix}", [kb.id], minutes_ago=20)
    later = await _document(client, f"later-{suffix}", [kb.id], minutes_ago=10)
    await client.set_document_superseded_by(old_version, new_version)

    # 旧版は正本にしない(同じ本文の新しい版は、旧版の重複にならない)。
    assert (
        await client.record_extracted_text_hash(old_version, text_hash, judge_duplicate=False)
        is None
    )
    assert (
        await client.record_extracted_text_hash(new_version, text_hash, judge_duplicate=True)
        is None
    )
    # 後から登録された文書は正本にしない(先に解析が終わっても、先の文書を重複にしない)。
    assert await client.record_extracted_text_hash(later, text_hash, judge_duplicate=False) is None
    assert (
        await client.record_extracted_text_hash(earlier, text_hash, judge_duplicate=True)
        == new_version
    )
    # 重複の文書(earlier)は正本にせず、先の正本を指す。
    assert (
        await client.record_extracted_text_hash(later, text_hash, judge_duplicate=True)
        == new_version
    )
    assert await _duplicate_of(client, old_version) is None
    assert await _duplicate_of(client, new_version) is None


@pytest.mark.usefixtures("oracle_db")
async def test_documents_with_same_extracted_text_are_found_for_evaluation() -> None:
    """品質評価の同一視（#5325）: 同じ hash の別の文書だけを返し、hash の無い文書は含めない。"""
    client = OracleClient()
    suffix = uuid4().hex[:12]
    kb = await client.create_knowledge_base(name=f"text-same-{suffix}")
    text_hash = extracted_text_sha256(f"合成の本文 {suffix}")
    first = await _document(client, f"same-first-{suffix}", [kb.id], minutes_ago=30)
    second = await _document(client, f"same-second-{suffix}", [kb.id], minutes_ago=20)
    differing = await _document(client, f"same-differing-{suffix}", [kb.id], minutes_ago=10)
    unhashed = await _document(client, f"same-unhashed-{suffix}", [kb.id], minutes_ago=5)
    await client.record_extracted_text_hash(first, text_hash, judge_duplicate=False)
    await client.record_extracted_text_hash(second, text_hash, judge_duplicate=False)
    await client.record_extracted_text_hash(
        differing, extracted_text_sha256(f"合成の本文 {suffix}。"), judge_duplicate=False
    )

    found = await client.same_extracted_text_document_ids([first, second, differing, unhashed])

    assert found == {first: [second], second: [first]}
