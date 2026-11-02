"""版付きの参照記録と双方向のページングを、合成データで実Oracle上に確認する。"""

from typing import Any, cast
from uuid import uuid4

import pytest

from app.clients.graph_reference_store import GraphReferenceStore
from app.clients.oracle import OracleClient
from app.rag.graph_index import _clean_label, _graph_id, graph_section_node_id
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
    target_chunks = _chunks([(f"第{index + 1}条", 1) for index in range(100)])
    for chunk in target_chunks:
        # 対象側の本文に自己参照を混ぜず、起点の明示参照だけを検証する。
        chunk.text = "合成の本文。"
    _target_id, target_set = await _indexed_document(
        client,
        file_name="合成参照規程.pdf",
        chunks=target_chunks,
        knowledge_base_id=kb.id,
    )
    # JSON nullのsection_pathを持つ文書が同じKBにあっても、目録の取得を止めない。
    null_path_chunks = _chunks([("第1条", 1)])
    null_path_chunks[0].metadata["section_path"] = None
    null_path_id, _null_path_set = await _indexed_document(
        client,
        file_name="synthetic-reference-null-path.pdf",
        chunks=null_path_chunks,
        knowledge_base_id=kb.id,
    )
    stored_null = await client._fetch_all(
        """SELECT 1 FROM rag_chunks WHERE document_id=:id
        AND JSON_QUERY(metadata_json,'$.section_path' RETURNING VARCHAR2(4000))='null'""",
        {"id": null_path_id},
    )
    assert stored_null
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
    assert {item["source"]["id"] for item in incoming["items"]} == {source_node}
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


@pytest.mark.usefixtures("oracle_db")
async def test_long_section_paths_keep_distinct_nodes_and_references() -> None:
    """#4051: 先頭が共通の長いパスでも、補建後のノード・参照の辺・参照元が章節ごとに分かれる。"""
    client = OracleClient()
    kb = await client.create_knowledge_base(name=f"synthetic-long-path-{uuid4().hex[:8]}")
    await client.set_knowledge_base_structure_index(kb.id, True)
    common = "合成の長い親階層" * 80
    first, second = f"{common} > 第1条", f"{common} > 第2条"
    assert first[:512] == second[:512]
    chunks = _chunks([(first, 1), (second, 1)])
    chunks[0].text = "第2条を参照する。"
    chunks[1].text = "合成の本文。"
    document_id, chunk_set_id = await _indexed_document(
        client,
        file_name="synthetic-long-path.pdf",
        chunks=chunks,
        knowledge_base_id=kb.id,
    )
    await rebuild_document_structure_index(client, kb.id, document_id)
    rows = await client._fetch_all(
        "SELECT entity_id,canonical_name FROM rag_graph_entities WHERE chunk_set_id=:id",
        {"id": chunk_set_id},
    )
    names = {str(row["entity_id"]): str(row["canonical_name"]) for row in rows}
    first_node = graph_section_node_id(chunk_set_id, kb.id, first)
    second_node = graph_section_node_id(chunk_set_id, kb.id, second)
    # SQL の表示名の式と Python の表示名が同じ規則であることも、辺の結合で確かめる。
    assert names[first_node] == _clean_label(first)
    assert names[second_node] == _clean_label(second)

    store = GraphReferenceStore(client)
    edges = await store.visible_edges(kb.id, [first_node, second_node], limit=25)
    assert [(edge["source"], edge["target"]) for edge in edges] == [(first_node, second_node)]
    outgoing = await store.page(kb.id, first_node, direction="outgoing", offset=0, limit=25)
    assert [item["target"]["id"] for item in outgoing["items"]] == [second_node]
    incoming = await store.page(kb.id, second_node, direction="incoming", offset=0, limit=25)
    assert [item["source"]["id"] for item in incoming["items"]] == [first_node]
    assert (await store.page(kb.id, first_node, direction="incoming", offset=0, limit=25))[
        "items"
    ] == []


@pytest.mark.usefixtures("oracle_db")
async def test_rebuild_reads_section_paths_over_4000_bytes() -> None:
    """補建の読み取りで長いパスを失わない（同一性が取込と変わらない）。"""
    client = OracleClient()
    kb = await client.create_knowledge_base(name=f"synthetic-long-read-{uuid4().hex[:8]}")
    await client.set_knowledge_base_structure_index(kb.id, True)
    path = "合成の長い親階層" * 170 + " > 第1条"
    assert len(path.encode()) > 4000
    chunks = _chunks([(path, 1)])
    chunks[0].text = "合成の本文。"
    document_id, chunk_set_id = await _indexed_document(
        client,
        file_name="synthetic-long-read.pdf",
        chunks=chunks,
        knowledge_base_id=kb.id,
    )
    await rebuild_document_structure_index(client, kb.id, document_id)
    assert await GraphReferenceStore(client).node(
        kb.id, graph_section_node_id(chunk_set_id, kb.id, path)
    )


# #4126: 参照の section path の列（VARCHAR2(4000 CHAR)）を超える 3 つの長さ。
_OVER_COLUMN_LIMIT = [
    pytest.param("合成の長い親階層" * 170, id="over_4000_bytes_under_4000_chars"),
    pytest.param("synthetic-long-parent-" * 230, id="over_4000_chars"),
    pytest.param("合成の長い親階層" * 400, id="over_the_clob_bind_size"),
]


@pytest.mark.usefixtures("oracle_db")
@pytest.mark.parametrize("parent", _OVER_COLUMN_LIMIT)
async def test_same_document_reference_over_the_column_limit_does_not_fail_the_build(
    parent: str,
) -> None:
    """#4126: 参照元・参照先のパスが列を超えても構築でき、辺と参照元・参照先が節ごとに分かれる。"""
    client = OracleClient()
    kb = await client.create_knowledge_base(name=f"synthetic-over-limit-{uuid4().hex[:8]}")
    await client.set_knowledge_base_structure_index(kb.id, True)
    first, second = f"{parent} > 第1条", f"{parent} > 第2条"
    chunks = _chunks([(first, 1), (second, 1)])
    chunks[0].text = "第2条を参照する。"
    chunks[1].text = "合成の本文。"
    document_id, chunk_set_id = await _indexed_document(
        client,
        file_name="synthetic-over-limit.pdf",
        chunks=chunks,
        knowledge_base_id=kb.id,
    )
    await rebuild_document_structure_index(client, kb.id, document_id)

    first_node = graph_section_node_id(chunk_set_id, kb.id, first)
    second_node = graph_section_node_id(chunk_set_id, kb.id, second)
    store = GraphReferenceStore(client)
    edges = await store.visible_edges(kb.id, [first_node, second_node], limit=25)
    assert [(edge["source"], edge["target"]) for edge in edges] == [(first_node, second_node)]
    outgoing = await store.page(kb.id, first_node, direction="outgoing", offset=0, limit=25)
    assert [item["status"] for item in outgoing["items"]] == ["resolved"]
    item = outgoing["items"][0]
    assert item["source"]["id"] == first_node
    assert item["target"]["id"] == second_node
    assert item["target"]["name"] == _clean_label(second)
    incoming = await store.page(kb.id, second_node, direction="incoming", offset=0, limit=25)
    assert [row["source"]["id"] for row in incoming["items"]] == [first_node]
    assert (await store.page(kb.id, first_node, direction="incoming", offset=0, limit=25))[
        "items"
    ] == []


@pytest.mark.usefixtures("oracle_db")
@pytest.mark.parametrize(
    ("parent", "stored_whole"),
    [
        pytest.param("合" * 1329 + "aa", True, id="3999_bytes"),
        pytest.param("合" * 1329 + "aaa", False, id="4000_bytes"),
    ],
)
async def test_reference_path_at_the_bind_boundary_is_written(
    parent: str, stored_whole: bool
) -> None:
    """#4126: VARCHAR の bind で入る最長のパスは完全なまま、次の長さは表示名で保存する。"""
    client = OracleClient()
    kb = await client.create_knowledge_base(name=f"synthetic-boundary-{uuid4().hex[:8]}")
    await client.set_knowledge_base_structure_index(kb.id, True)
    first, second = f"{parent} > 第1条", f"{parent} > 第2条"
    assert len(second.encode()) == (3999 if stored_whole else 4000)
    chunks = _chunks([(first, 1), (second, 1)])
    chunks[0].text = "第2条を参照する。"
    chunks[1].text = "合成の本文。"
    _document_id, chunk_set_id = await _indexed_document(
        client,
        file_name="synthetic-boundary.pdf",
        chunks=chunks,
        knowledge_base_id=kb.id,
    )
    first_node = graph_section_node_id(chunk_set_id, kb.id, first)
    second_node = graph_section_node_id(chunk_set_id, kb.id, second)
    outgoing = await GraphReferenceStore(client).page(
        kb.id, first_node, direction="outgoing", offset=0, limit=25
    )
    target = outgoing["items"][0]["target"]
    assert target["id"] == second_node
    assert target["name"] == (second if stored_whole else _clean_label(second))


@pytest.mark.usefixtures("oracle_db")
@pytest.mark.parametrize("parent", _OVER_COLUMN_LIMIT)
async def test_cross_document_reference_resolves_a_section_over_the_column_limit(
    parent: str,
) -> None:
    """#4126: 目録が長いパスを失わず、別文書の長いパスの節へ解決できる。"""
    client = OracleClient()
    kb = await client.create_knowledge_base(name=f"synthetic-cross-limit-{uuid4().hex[:8]}")
    await client.set_knowledge_base_structure_index(kb.id, True)
    wanted = f"{parent} > 第2条"
    target_chunks = _chunks([(f"{parent} > 第1条", 1), (wanted, 1)])
    for chunk in target_chunks:
        chunk.text = "合成の本文。"
    _target_id, target_set = await _indexed_document(
        client,
        file_name="合成長大規程.pdf",
        chunks=target_chunks,
        knowledge_base_id=kb.id,
    )
    source_chunks = _chunks([("第1条", 1)])
    source_chunks[0].text = "合成長大規程の第2条を参照する。"
    source_id, source_set = await _indexed_document(
        client,
        file_name="synthetic-cross-limit-source.pdf",
        chunks=source_chunks,
        knowledge_base_id=kb.id,
    )
    await rebuild_document_structure_index(client, kb.id, source_id)

    source_node = _graph_id("entity", source_set, kb.id, "第1条")
    target_node = graph_section_node_id(target_set, kb.id, wanted)
    store = GraphReferenceStore(client)
    page = await store.page(kb.id, source_node, direction="outgoing", offset=0, limit=25)
    assert [item["status"] for item in page["items"]] == ["resolved"]
    assert page["items"][0]["target"]["id"] == target_node
    assert page["items"][0]["target"]["name"] == _clean_label(wanted)
    edges = await store.visible_edges(kb.id, [source_node, target_node], limit=25)
    assert [(edge["source"], edge["target"]) for edge in edges] == [(source_node, target_node)]
    incoming = await store.page(kb.id, target_node, direction="incoming", offset=0, limit=25)
    assert [item["source"]["id"] for item in incoming["items"]] == [source_node]


@pytest.mark.usefixtures("oracle_db")
async def test_reference_catalog_keeps_string_and_array_paths_over_4000_bytes() -> None:
    """#4126: 目録が文字列・配列の長いパスを読み、短いパスの文書はこれまでどおり読む。"""
    client = OracleClient()
    kb = await client.create_knowledge_base(name=f"synthetic-catalog-{uuid4().hex[:8]}")
    parent = "合成の長い親階層" * 170
    string_chunks = _chunks([(f"{parent} > 第1条", 2), ("第2条", 1)])
    string_id, _string_set = await _indexed_document(
        client,
        file_name="synthetic-catalog-string.pdf",
        chunks=string_chunks,
        knowledge_base_id=kb.id,
    )
    array_chunks = _chunks([("placeholder", 1)])
    # 配列の section_path は ChunkMetadata の型の外だが、目録と補建は読める形として扱う。
    array_chunks[0].metadata["section_path"] = cast(Any, [parent, "第3条"])
    array_id, _array_set = await _indexed_document(
        client,
        file_name="synthetic-catalog-array.pdf",
        chunks=array_chunks,
        knowledge_base_id=kb.id,
    )
    short_id, _short_set = await _indexed_document(
        client,
        file_name="synthetic-catalog-short.pdf",
        chunks=_chunks([("第4条", 1)]),
        knowledge_base_id=kb.id,
    )

    catalog = {
        item.document_id: sorted(item.section_paths)
        for item in await GraphReferenceStore(client).documents(kb.id)
    }
    assert catalog[string_id] == sorted([f"{parent} > 第1条", "第2条"])
    assert catalog[array_id] == [f"{parent} > 第3条"]
    assert catalog[short_id] == ["第4条"]
