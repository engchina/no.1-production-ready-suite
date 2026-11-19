"""実 Oracle で KB 詳細の Wiki の図の読み取り（#5502）の SQL を確かめる。

AI は使わない（合成の案内）。
"""

from dataclasses import replace
from uuid import uuid4

import pytest

from app.clients.oracle import OracleClient
from app.clients.wiki_graph_store import WikiGraphStore, section_id
from app.clients.wiki_store import WikiSnapshot, WikiStore
from app.rag.wiki_models import WikiContext, WikiDependency, WikiPage, fingerprint
from tests.test_document_summary_oracle import _EMBEDDING, _indexed_document

pytestmark = pytest.mark.usefixtures("oracle_db")

LINKS = fingerprint("graph-links-1")


async def _publish(
    store: WikiStore, did: str, context: str, *, chain: bool = False
) -> tuple[WikiSnapshot, list[WikiPage]]:
    """合成の案内を公開した文書。``chain`` は各単位が次の単位を「続く手順」として結ぶ。"""
    revision = await store.revision_fingerprint(did)
    source = await store.source_units(did)
    assert revision is not None and source is not None
    source = replace(
        source, compiler_fingerprint=fingerprint("compiler-a"), revision_fingerprint=revision
    )
    await store.enqueue(source)
    lease = await store.claim(source, lease_seconds=300)
    assert lease
    units = list(source.units)
    pages = [
        WikiPage(
            unit=unit,
            navigation=WikiContext(
                unit_id=unit.unit_id,
                context=context,
                possible_questions=["合成の質問？"],
                dependencies=[
                    WikiDependency(unit_id=units[index + 1].unit_id, relation="continuation")
                ]
                if chain and index + 1 < len(units)
                else [],
                unresolved=["合成の未確認の点。"],
            ),
            input_fingerprint="input-a",
        )
        for index, unit in enumerate(units)
    ]
    await store.checkpoint(source, lease, pages, vectors=[_EMBEDDING] * len(pages))
    await store.finish(source, lease)
    return source, pages


async def test_wiki_graph_reads_published_guidance_in_kb_scope_with_links() -> None:
    oracle, token = OracleClient(), uuid4().hex[:12]
    store, graph = WikiStore(oracle), WikiGraphStore(oracle)
    kb = await oracle.create_knowledge_base(name=f"synthetic-wiki-graph-{token}")
    other = await oracle.create_knowledge_base(name=f"synthetic-wiki-graph-other-{token}")
    a, _, _ = await _indexed_document(oracle, knowledge_base_id=kb.id, token=f"{token}a")
    b, _, _ = await _indexed_document(oracle, knowledge_base_id=kb.id, token=f"{token}b")
    # 範囲の外（別の KB だけに所属する）文書。
    c, _, _ = await _indexed_document(oracle, knowledge_base_id=other.id, token=f"{token}c")
    assert (await graph.overview(kb.id))["status"] == "empty"
    await store.set_enabled(kb.id, True)
    await store.set_enabled(other.id, True)
    source_a, pages_a = await _publish(store, a, "合成の案内 syntheticgraphalpha。", chain=True)
    _, pages_b = await _publish(store, b, "合成の案内 syntheticgraphbeta。")
    _, pages_c = await _publish(store, c, "合成の案内 syntheticgraphgamma。")
    first_a, first_b, first_c = (pages[0].unit.unit_id for pages in (pages_a, pages_b, pages_c))
    # a → b（b は a の例外）と、範囲の外への a → c。
    claimed = await store.claim_links(a, source_a.source_fingerprint, LINKS, lease_seconds=300)
    assert claimed is not None
    await store.save_links(
        a,
        claimed[0],
        sources=[first_a],
        candidates=[first_b, first_c],
        links=[
            (first_a, first_b, "exception", "合成の理由"),
            (first_a, first_c, "reference", "合成の理由"),
        ],
        done=[f"forward:{first_a}"],
    )
    await store.finish_links(a, claimed[0])

    overview = await graph.overview(kb.id)
    docs = {doc["document_id"]: doc for doc in overview["documents"]}
    assert set(docs) == {a, b}
    assert docs[a]["unit_count"] == len(pages_a)
    assert (docs[a]["outgoing_links"], docs[b]["incoming_links"]) == (1, 1)

    # 文書 → 章節（section_path の階層）→ 単位を段階的に開く。
    path = pages_a[0].unit.section_path
    root = await graph.children(kb.id, a, parent_id=None, offset=0, limit=100)
    assert [item["id"] for item in root["items"]] == [section_id(a, path[:1])]
    leaf = section_id(a, path)
    units = await graph.children(kb.id, a, parent_id=leaf, offset=0, limit=100)
    assert [item["id"] for item in units["items"]] == [page.unit.unit_id for page in pages_a]
    assert units["items"][0]["outgoing"] == [
        {"target": pages_a[1].unit.unit_id, "relation": "continuation", "scope": "same_document"},
        {"target": first_b, "relation": "exception", "scope": "cross_document"},
    ]
    located = await graph.locate(kb.id, a, pages_a[-1].unit.unit_id)
    assert located["path"][-1] == {"id": pages_a[-1].unit.unit_id, "index": len(pages_a) - 1}

    detail = await graph.unit(kb.id, first_a)
    assert detail["context"] == "合成の案内 syntheticgraphalpha。"
    assert [chunk["chunk_id"] for chunk in detail["source_chunks"]] == (
        pages_a[0].unit.source_chunk_ids
    )
    assert {(r["direction"], r["scope"], r["unit"]["id"]) for r in detail["relations"]} == {
        ("outgoing", "same_document", pages_a[1].unit.unit_id),
        ("outgoing", "cross_document", first_b),
    }
    found = await graph.search(kb.id, "SyntheticGraphBeta", offset=0, limit=25)
    assert found["total"] == len(pages_b) and {i["document_id"] for i in found["items"]} == {b}
    assert (await graph.search(kb.id, "syntheticgraphgamma", offset=0, limit=25))["total"] == 0
    with pytest.raises(KeyError):
        await graph.children(kb.id, c, parent_id=None, offset=0, limit=100)
    with pytest.raises(KeyError):
        await graph.unit(kb.id, first_c)

    # Wiki の構築を無効にしても、保存済みの案内は図で見られる。
    await store.set_enabled(kb.id, False)
    assert {doc["document_id"] for doc in (await graph.overview(kb.id))["documents"]} == {a, b}
    # 文書を KB から外すと、図とその文書への関連が消える。
    await oracle.remove_document_from_knowledge_base(kb.id, b)
    overview = await graph.overview(kb.id)
    assert [doc["document_id"] for doc in overview["documents"]] == [a]
    assert overview["documents"][0]["outgoing_links"] == 0
    assert all(
        r["scope"] == "same_document" for r in (await graph.unit(kb.id, first_a))["relations"]
    )
    for did in (a, b, c):
        await oracle.delete_document(did)
