"""実 Oracle で保存版・有効 KB・公開前検査と削除を確認する。AI は合成応答。"""

from dataclasses import replace
from uuid import uuid4

import pytest

from app.clients.oracle import OracleClient
from app.clients.wiki_store import WikiStore
from app.rag.wiki_models import WikiContext, WikiPage, fingerprint
from tests.test_document_summary_oracle import _EMBEDDING, _indexed_document

pytestmark = pytest.mark.usefixtures("oracle_db")


async def test_shared_document_cache_requires_enabled_current_membership_and_cascades() -> None:
    oracle, token = OracleClient(), uuid4().hex[:12]
    store = WikiStore(oracle)
    first = await oracle.create_knowledge_base(name=f"synthetic-wiki-a-{token}")
    second = await oracle.create_knowledge_base(name=f"synthetic-wiki-b-{token}")
    did, _, _ = await _indexed_document(oracle, knowledge_base_id=first.id, token=token)
    await oracle.assign_documents_to_knowledge_base(second.id, [did])
    await store.set_enabled(first.id, True)
    await store.set_enabled(second.id, True)
    source = await store.source_units(did)
    assert source is not None
    compiler = fingerprint("compiler-a")
    source = replace(
        source, compiler_fingerprint=compiler, revision_fingerprint=fingerprint("revision-a")
    )
    assert await store.enqueue(source)
    lease = await store.claim(source, lease_seconds=300)
    assert lease
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
    await store.checkpoint(source, lease, pages, vectors=[_EMBEDDING] * len(pages))
    assert not await store.catalog({"knowledge_base_id": first.id}, compiler)
    await store.finish(source, lease)
    filters = {"knowledge_base_id": first.id}
    assert len(await store.catalog(filters, compiler)) == 1
    assert len(await store.available_pages(filters, query_vector=_EMBEDDING, limit=10)) == len(
        pages
    )
    assert (await store.status_counts(filters))["READY"] == 1
    assert len(
        await store.text_pages("syntheticnavigation", filters, compiler=compiler, limit=10)
    ) == len(pages)
    assert len(await store.read_sources(pages, filters)) == len(pages)
    await store.set_enabled(first.id, False)
    assert not await store.available_pages(filters)
    other = {"knowledge_base_id": second.id}
    assert len(await store.available_pages(other)) == len(pages)
    assert (await store.status_counts(filters))["READY"] == 1
    await oracle.remove_document_from_knowledge_base(second.id, did)
    assert not await store.read_sources(pages, other)
    await store.set_enabled(first.id, True)
    assert len(await store.available_pages(filters)) == len(pages)
    await oracle.delete_document(did)
    assert not await store.available_pages(filters)
