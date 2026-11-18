"""実 Oracle で原文の参照・有効 KB・公開前の検査と削除を確かめる。AI は合成応答。"""

from dataclasses import replace
from uuid import uuid4

import pytest

from app.clients.oracle import OracleClient, OracleConnectionProtocol, _execute
from app.clients.wiki_store import WikiStore
from app.rag.wiki_models import WikiContext, WikiPage, fingerprint
from tests.test_document_summary_oracle import _EMBEDDING, _indexed_document

pytestmark = pytest.mark.usefixtures("oracle_db")


async def test_virtual_parent_persists_all_original_bindings_and_rejects_changed_member() -> None:
    oracle, token = OracleClient(), uuid4().hex[:12]
    store = WikiStore(oracle)
    kb = await oracle.create_knowledge_base(name=f"synthetic-group-{token}")
    did, _, _ = await _indexed_document(oracle, knowledge_base_id=kb.id, token=token)

    def group(connection: OracleConnectionProtocol) -> None:
        _execute(
            connection,
            """UPDATE rag_chunks SET metadata_json=JSON_MERGEPATCH(metadata_json,
                JSON('{"parent_chunk_id":"virtual-parent"}')) WHERE document_id=:id""",
            {"id": did},
        )

    await oracle._run_transaction(group)
    await store.set_enabled(kb.id, True)
    source = await store.source_units(did)
    assert source is not None and len(source.units) == 1
    unit = source.units[0]
    assert len(unit.source_chunk_ids) == len(unit.source_chunk_hashes) == 3
    source = replace(source, compiler_fingerprint=fingerprint("compiler-a"))
    assert await store.enqueue(source)
    lease = await store.claim(source, lease_seconds=300)
    assert lease
    page = WikiPage(
        unit=unit,
        navigation=WikiContext(
            unit_id=unit.unit_id,
            context="合成の案内。",
            possible_questions=[],
            dependencies=[],
            unresolved=[],
        ),
        input_fingerprint="input-a",
    )
    await store.checkpoint(source, lease, [page], vectors=[_EMBEDDING])
    await store.finish(source, lease)
    restored = await store.saved_pages(source)
    assert restored[0].unit == unit
    filters = {"knowledge_base_id": kb.id}
    assert len(await store.read_sources(restored, filters)) == 3

    def change(connection: OracleConnectionProtocol) -> None:
        _execute(
            connection,
            "UPDATE rag_chunks SET chunk_text=:text WHERE chunk_id=:id",
            {"text": "変更された合成の原文。", "id": unit.source_chunk_ids[-1]},
        )

    await oracle._run_transaction(change)
    assert not await store.read_sources(restored, filters)


async def test_shared_document_cache_requires_enabled_current_membership_and_cascades() -> None:
    oracle, token = OracleClient(), uuid4().hex[:12]
    store = WikiStore(oracle)
    first = await oracle.create_knowledge_base(name=f"synthetic-wiki-a-{token}")
    second = await oracle.create_knowledge_base(name=f"synthetic-wiki-b-{token}")
    did, _, _ = await _indexed_document(oracle, knowledge_base_id=first.id, token=token)
    await oracle.assign_documents_to_knowledge_base(second.id, [did])
    filters = {"knowledge_base_id": first.id}
    other = {"knowledge_base_id": second.id}
    assert not await store.document_ids(filters)
    assert (await store.status_counts(filters)) == {"NOT_BUILT": 1}
    await store.set_enabled(first.id, True)
    await store.set_enabled(second.id, True)
    assert {kb for kb, _ in await store.enabled_knowledge_bases()} >= {first.id, second.id}
    assert await store.document_ids(filters) == [did]
    revision = await store.revision_fingerprint(did)
    source = await store.source_units(did)
    assert revision is not None and source is not None
    source = replace(
        source, compiler_fingerprint=fingerprint("compiler-a"), revision_fingerprint=revision
    )
    assert await store.enqueue(source)
    # 同じ入力の再投入は QUEUED / RUNNING / READY を作り直さない。
    assert not await store.enqueue(source)
    lease = await store.claim(source, lease_seconds=300)
    assert lease and await store.claim(source, lease_seconds=300) is None
    pages = [
        WikiPage(
            unit=u,
            navigation=WikiContext(
                unit_id=u.unit_id,
                context="架空の案内 syntheticnavigation。",
                possible_questions=[],
                dependencies=[],
                unresolved=[],
            ),
            input_fingerprint="input-a",
        )
        for u in source.units
    ]
    await store.checkpoint(source, lease, pages)
    # 埋め込みが揃うまで公開しない。
    with pytest.raises(ValueError, match="未完了"):
        await store.finish(source, lease)
    await store.checkpoint(source, lease, pages, vectors=[_EMBEDDING] * len(pages))
    await store.finish(source, lease)
    assert (await store.status_counts(filters))["READY"] == 1
    assert len(await store.saved_pages(source)) == len(pages)
    assert len(await store.read_sources(pages, filters)) == len(pages)
    await store.set_enabled(first.id, False)
    # 無効にした KB からは読めないが、保存済みの案内は残り、別の有効な KB では使える。
    assert not await store.read_sources(pages, filters)
    assert not await store.document_ids(filters)
    assert len(await store.read_sources(pages, other)) == len(pages)
    assert (await store.status_counts(filters))["READY"] == 1
    await oracle.remove_document_from_knowledge_base(second.id, did)
    assert not await store.read_sources(pages, other)
    await store.set_enabled(first.id, True)
    assert len(await store.read_sources(pages, filters)) == len(pages)
    await oracle.delete_document(did)
    assert not await store.saved_pages(source)
    assert await store.document_state(did) is None
