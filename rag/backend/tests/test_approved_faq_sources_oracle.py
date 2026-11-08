"""承認済み FAQ が引用した文書の今の状態を、実 Oracle AI Database で確かめる（#4621）。

版の印と所属 KB を読む。未到達なら oracle_db fixture が skip し、作成行は後始末する。
"""

from uuid import uuid4

import pytest

from app.clients.oracle import OracleClient


@pytest.mark.usefixtures("oracle_db")
async def test_source_document_states_follow_membership_version_and_deletion() -> None:
    client = OracleClient()
    kb = await client.create_knowledge_base(name=f"synthetic-faq-{uuid4().hex[:8]}")
    old = await client.create_document(
        file_name=f"synthetic-faq-old-{uuid4().hex[:8]}.pdf",
        object_storage_path="local://synthetic-faq-old.pdf",
        content_type="application/pdf",
        knowledge_base_ids=[kb.id],
    )
    new = await client.create_document(
        file_name=f"synthetic-faq-new-{uuid4().hex[:8]}.pdf",
        object_storage_path="local://synthetic-faq-new.pdf",
        content_type="application/pdf",
        knowledge_base_ids=[kb.id],
    )

    first = await client.approved_faq_source_document_states([old.id, "missing-document"])
    assert set(first) == {old.id}
    assert first[old.id].knowledge_base_ids == frozenset({kb.id})

    # 新しい版の文書を設定すると版の印が変わる（FAQ は「原文が更新されました」）。
    await client.set_document_superseded_by(old.id, new.id)
    superseded = await client.approved_faq_source_document_states([old.id])
    assert superseded[old.id].version != first[old.id].version

    # 文書を削除すると返らない（FAQ は停止）。
    assert await client.delete_document(old.id)
    assert await client.approved_faq_source_document_states([old.id]) == {}
    await client.delete_document(new.id)
    await client.archive_knowledge_base(kb.id)
