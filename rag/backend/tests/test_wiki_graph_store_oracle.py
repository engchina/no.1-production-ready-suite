"""実 Oracle で KB 詳細の Wiki の図の読み取り（#5502）の SQL を確かめる。

AI は使わない（合成の案内）。
"""

from dataclasses import replace
from uuid import uuid4

import pytest

from app.clients.oracle import OracleClient, _execute
from app.clients.wiki_graph_index import backfill_graph_index
from app.clients.wiki_graph_store import WikiGraphStore, section_id
from app.clients.wiki_store import WikiSnapshot, WikiStore
from app.config import get_settings
from app.rag.wiki_explorer import WikiExplorer
from app.rag.wiki_models import WikiContext, WikiDependency, WikiPage, fingerprint
from app.schemas.knowledge_base import KnowledgeBaseRef
from tests.test_document_summary_oracle import _EMBEDDING, _indexed_document
from tests.test_wiki_store_oracle import annotate_references

pytestmark = pytest.mark.usefixtures("oracle_db")


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
    # a → b と、範囲の外の文書 c への参照（取込の参照。#5784）。
    await annotate_references(oracle, a, [f"summary-{token}b.pdf", f"summary-{token}c.pdf"])
    _, pages_a = await _publish(store, a, "合成の案内 syntheticgraphalpha。", chain=True)
    _, pages_b = await _publish(store, b, "合成の案内 syntheticgraphbeta。")
    _, pages_c = await _publish(store, c, "合成の案内 syntheticgraphgamma。")
    first_a, first_b, first_c = (pages[0].unit.unit_id for pages in (pages_a, pages_b, pages_c))

    # 既存の公開版の状態を再現し、移行を繰り返しても同じ章節と依存が読めることを確かめる。
    await oracle._run_transaction(
        lambda connection: _execute(
            connection, "DELETE FROM rag_wiki_graph_nodes WHERE document_id=:doc", {"doc": a}
        )
    )
    await oracle._run_transaction(backfill_graph_index)
    await oracle._run_transaction(backfill_graph_index)

    overview = await graph.overview(kb.id)
    docs = {doc["document_id"]: doc for doc in overview["documents"]}
    assert set(docs) == {a, b}
    assert await graph.document_count(kb.id) == {"total": 2}
    assert not overview["has_more"]
    assert (await graph.overview(kb.id, limit=1))["has_more"]

    # 文書 → 章節（section_path の階層）→ 単位を段階的に開く。
    path = pages_a[0].unit.section_path
    root = await graph.children(kb.id, a, parent_id=None, offset=0, limit=100)
    assert [item["id"] for item in root["items"]] == [section_id(a, path[:1])]
    leaf = section_id(a, path)
    units = await graph.children(kb.id, a, parent_id=leaf, offset=0, limit=100)
    assert [item["id"] for item in units["items"]] == [page.unit.unit_id for page in pages_a]
    assert "outgoing" not in units["items"][0]
    located = await graph.locate(kb.id, a, pages_a[-1].unit.unit_id)
    assert located["path"][-1] == {"id": pages_a[-1].unit.unit_id, "index": len(pages_a) - 1}

    detail = await graph.unit(kb.id, first_a)
    assert detail["context"] == "合成の案内 syntheticgraphalpha。"
    assert [chunk["chunk_id"] for chunk in detail["source_chunks"]] == (
        pages_a[0].unit.source_chunk_ids
    )
    relations = (await graph.relations(kb.id, first_a, 0, 25))["items"]
    assert {
        (r["direction"], r["scope"], r["unit"]["id"] if r["unit"] else None) for r in relations
    } == {
        ("outgoing", "same_document", pages_a[1].unit.unit_id),
        # 参照先が範囲にある文書は開ける単位（文書の最初の単位）、範囲の外の文書は表記だけ。
        ("outgoing", "cross_document", first_b),
        ("outgoing", "cross_document", None),
    }
    # 参照される側（文書 b）からは、入る向きの参照として見える。
    assert ("incoming", "cross_document", first_a) in {
        (r["direction"], r["scope"], r["unit"]["id"] if r["unit"] else None)
        for r in (await graph.relations(kb.id, first_b, 0, 25))["items"]
    }
    # 参照の多い文書の見本と文書の間の参照、選んだ単位の間の関係（#5916）。
    sample = await graph.document_graph(kb.id, offset=0, limit=25)
    assert {doc["document_id"] for doc in sample["documents"]} == {a, b}
    assert {(link["source_id"], link["target_id"]) for link in sample["links"]} == {(a, b)}
    around = await graph.document_graph(kb.id, offset=0, limit=25, document=b)
    assert [doc["document_id"] for doc in around["documents"]] == [a]
    second_a = pages_a[1].unit.unit_id
    connections = await graph.connections(kb.id, [first_a, second_a, first_b, first_c])
    assert {(c["source_id"], c["target_id"], c["scope"]) for c in connections} == {
        (first_a, second_a, "same_document"),
        (first_a, first_b, "cross_document"),
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
    # 2 KB を対象にした手動検索は構築停止後も保存済みの原文を読める。
    explorer = WikiExplorer(
        oracle, [KnowledgeBaseRef(id=k.id, name=k.name) for k in (kb, other)], get_settings()
    )
    hits = await explorer.search("syntheticgraph", "keyword", 0, 50)
    assert {h.document_id for h in hits.items} == {a, b, c}
    assert [kb.id] in [h.knowledge_base_ids for h in hits.items]
    original = await explorer.original(first_a)
    assert original.chunks and await explorer.selected([original.source])
    groups = await graph.relations(explorer.ids, first_a, 0, 1, grouped=True)
    assert len(groups["items"]) == 1 and sum(groups["counts"].values()) == groups["total"]
    # 文書を KB から外すと図から消え、その文書への参照は開けない参照になる（#5784）。
    await oracle.remove_document_from_knowledge_base(kb.id, b)
    overview = await graph.overview(kb.id)
    assert [doc["document_id"] for doc in overview["documents"]] == [a]
    assert await graph.document_count(kb.id) == {"total": 1}
    assert not overview["has_more"]
    assert all(
        r["unit"] is None
        for r in (await graph.relations(kb.id, first_a, 0, 25))["items"]
        if r["scope"] == "cross_document"
    )
    for did in (a, b, c):
        await oracle.delete_document(did)
