"""版付きの参照記録と双方向のページングを、合成データで実Oracle上に確認する。"""

from uuid import uuid4

import pytest

from app.clients.graph_reference_store import GraphReferenceStore
from app.clients.oracle import OracleClient
from app.rag.graph_index import _graph_id
from app.rag.structure_index import rebuild_document_structure_index
from tests.test_graph_expansion_oracle import _chunks, _indexed_document


@pytest.mark.usefixtures("oracle_db")
async def test_cross_document_reference_rebuild_and_directional_pages() -> None:
    client = OracleClient()
    kb = await client.create_knowledge_base(name=f"synthetic-reference-{uuid4().hex[:8]}")
    await client.set_knowledge_base_structure_index(kb.id, True)
    source_chunks = _chunks([("第1条", 1)])
    source_chunks[0].text = "合成参照規程の第100条を参照する。"
    source_id, source_set = await _indexed_document(
        client,
        file_name="synthetic-reference-source.pdf",
        chunks=source_chunks,
        knowledge_base_id=kb.id,
    )
    _target_id, target_set = await _indexed_document(
        client,
        file_name="合成参照規程.pdf",
        chunks=_chunks([(f"第{index + 1}条", 1) for index in range(100)]),
        knowledge_base_id=kb.id,
    )
    await rebuild_document_structure_index(client, kb.id, source_id)
    store = GraphReferenceStore(client)
    source_node = _graph_id("entity", source_set, kb.id, "第1条")
    target_node = _graph_id("entity", target_set, kb.id, "第100条")
    page = await store.page(kb.id, source_node, direction="outgoing", offset=0, limit=25)
    assert len(page["items"]) == 1
    assert page["items"][0]["status"] == "resolved"
    assert page["items"][0]["target"]["id"] == target_node
    assert await store.node(kb.id, target_node) is not None
    incoming = await store.page(kb.id, target_node, direction="incoming", offset=0, limit=25)
    assert incoming["items"][0]["source"]["id"] == source_node
    assert await store.page(kb.id, source_node, direction="outgoing", offset=25, limit=25) == {
        "items": [],
        "offset": 25,
        "has_more": False,
        "direction": "outgoing",
    }
    assert len(await store.visible_edges(kb.id, [source_node, target_node], limit=25)) == 1
    # 別のKBの図・参照元をIDの直接指定で読めない。
    other = await client.create_knowledge_base(name=f"synthetic-other-{uuid4().hex[:8]}")
    with pytest.raises(KeyError):
        await store.page(other.id, source_node, direction="outgoing", offset=0, limit=25)
