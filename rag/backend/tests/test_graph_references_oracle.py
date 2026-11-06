"""版付きの参照記録と双方向のページングを、合成データで実Oracle上に確認する。"""

import os
from typing import Any, cast
from uuid import uuid4

import pytest

from app.clients.graph_reference_store import GraphReferenceStore
from app.clients.oracle import OracleClient
from app.clients.structure_graph_store import StructureGraphStore
from app.rag.graph_index import _clean_label, _graph_id, graph_section_node_id
from app.rag.structure_index import rebuild_document_structure_index
from tests.test_graph_expansion_oracle import _chunks, _indexed_document


async def _reference_pairs(
    store: StructureGraphStore, kb_id: str, chunk_set_id: str
) -> list[tuple[str, str]]:
    """図の参照の、端の節点の組。"""
    items = (await store.references(kb_id, chunk_set_id))["items"]
    return [(item["source_chain"][-1], item["target_chain"][-1]) for item in items]


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
    graph = StructureGraphStore(client)
    overview = await graph.overview(kb.id)
    by_set = {item["chunk_set_id"]: item for item in overview["documents"]}
    assert [(edge["source"], edge["target"], edge["count"]) for edge in overview["edges"]] == [
        (by_set[source_set]["id"], by_set[target_set]["id"], 1)
    ]
    assert by_set[source_set]["outgoing_count"] == 1
    assert by_set[target_set]["incoming_count"] == 1
    assert await _reference_pairs(graph, kb.id, source_set) == [(source_node, target_node)]
    assert await _reference_pairs(graph, kb.id, target_set) == [(source_node, target_node)]
    # 100 件の章節の木は段ごとに読め、位置から開くページが分かる。
    first_page = await graph.children(
        kb.id, _target_id, target_set, parent_id=None, offset=0, limit=25
    )
    assert first_page["total"] == 100 and len(first_page["items"]) == 25
    assert (await graph.locate(kb.id, _target_id, target_set, target_node))["path"] == [
        {"id": target_node, "index": 99}
    ]
    found = await graph.search(kb.id, "第100条", offset=0, limit=25)
    assert [item["id"] for item in found["items"]] == [target_node]
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
    assert await _reference_pairs(StructureGraphStore(client), kb.id, chunk_set_id) == [
        (first_node, second_node)
    ]
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
    assert await _reference_pairs(StructureGraphStore(client), kb.id, chunk_set_id) == [
        (first_node, second_node)
    ]
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
    assert await _reference_pairs(StructureGraphStore(client), kb.id, source_set) == [
        (source_node, target_node)
    ]
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


@pytest.mark.usefixtures("oracle_db")
async def test_refresh_resolves_references_after_target_document_is_added() -> None:
    """起点の後に取り込んだ文書への参照が、解決のやり直しで図に出る（#4310）。"""
    from app.rag.reference_refresh import refresh_knowledge_base_references

    client = OracleClient()
    kb = await client.create_knowledge_base(name=f"synthetic-refresh-{uuid4().hex[:8]}")
    await client.set_knowledge_base_structure_index(kb.id, True)
    chunks = _chunks([("第1条", 1)])
    chunks[0].text = "合成後続規程の第2条を参照する。"
    source_id, source_set = await _indexed_document(
        client, file_name="A001_合成の起点.pdf", chunks=chunks, knowledge_base_id=kb.id
    )
    await rebuild_document_structure_index(client, kb.id, source_id)
    store = StructureGraphStore(client)
    assert (await store.overview(kb.id))["edges"] == []

    target_chunks = _chunks([("合成後続規程 > 第2条", 1), ("合成後続規程 > 第3条", 1)])
    for chunk in target_chunks:
        chunk.text = "合成の本文。"
    _target_id, target_set = await _indexed_document(
        client, file_name="B002_題名と違う名前.pdf", chunks=target_chunks, knowledge_base_id=kb.id
    )
    first = await refresh_knowledge_base_references(client, kb.id)
    assert first["updated"] == 1
    overview = await store.overview(kb.id)
    assert [edge["count"] for edge in overview["edges"]] == [1]
    assert (await _reference_pairs(store, kb.id, source_set))[0][1] == graph_section_node_id(
        target_set, kb.id, "合成後続規程 > 第2条"
    )
    # 同じ目録なら何も変わらない。
    assert (await refresh_knowledge_base_references(client, kb.id))["updated"] == 0


@pytest.mark.skipif(
    os.environ.get("GITHUB_ACTIONS") != "true", reason="CI専用Oracleの匿名合成データだけで検証する"
)
@pytest.mark.usefixtures("oracle_db")
async def test_body_chunks_read_actual_saved_links_in_version_scope() -> None:
    """CI隔離schemaのfixtureが作成行を後始末する。手元/配備先へは接続しない。"""
    from app.rag.chunking import Chunk
    from app.rag.graph_index import graph_body_node_id

    client = OracleClient()
    kb = await client.create_knowledge_base(name=f"synthetic-body-{uuid4().hex[:8]}")
    chunks = [
        Chunk(
            text=f"合成本文{index}",
            index=index,
            start_offset=index * 10,
            end_offset=index * 10 + 5,
            metadata={
                "page_number": index + 1,
                **(
                    {"section_path": "合成章 > 本文", "section_title": "本文"} if index == 1 else {}
                ),
            },
        )
        for index in range(28)
    ]
    document, chunk_set = await _indexed_document(
        client, file_name="synthetic-body.pdf", chunks=chunks, knowledge_base_id=kb.id
    )
    store = StructureGraphStore(client)
    tree = await store.children(kb.id, document, chunk_set, parent_id=None, offset=0, limit=25)
    body = graph_body_node_id(chunk_set, kb.id)
    assert any(node["id"] == body and not node["virtual"] for node in tree["items"])
    found = await store.search(kb.id, "本文", offset=0, limit=25)
    assert {item["id"] for item in found["items"]} == {
        body,
        graph_section_node_id(chunk_set, kb.id, "合成章 > 本文"),
    }
    first = await store.body_chunks(kb.id, document, chunk_set, offset=0, limit=25)
    last = await store.body_chunks(kb.id, document, chunk_set, offset=25, limit=25)
    empty = await store.body_chunks(kb.id, document, chunk_set, offset=1000001, limit=25)
    assert first["total"] == last["total"] == empty["total"] == 27
    assert empty["items"] == []
    assert [row["chunk_id"] for row in first["items"] + last["items"]] == [
        f"{document}:{chunk_set}:{index}" for index in range(28) if index != 1
    ]
    assert last["items"][-1]["page_number"] == 28
    original = await client.readable_version_chunk(document, last["items"][-1]["chunk_id"])
    assert original is not None and original.text == "合成本文27"
    assert original.metadata["page_number"] == 28
    other = await client.create_knowledge_base(name=f"synthetic-body-other-{uuid4().hex[:8]}")
    with pytest.raises(KeyError):
        await store.body_chunks(other.id, document, chunk_set, offset=0, limit=25)


def _body_fixture_chunks(prefix: str, count: int = 4) -> list[Any]:
    """見出しの無い chunk と、見出しのある chunk 1 件（index 1）の合成文書。"""
    from app.rag.chunking import Chunk

    return [
        Chunk(
            text=f"{prefix}{index}",
            index=index,
            start_offset=0,
            end_offset=len(f"{prefix}{index}"),
            metadata={
                "page_number": index + 1,
                **(
                    {"section_path": "合成章 > 合成節", "section_title": "合成節"}
                    if index == 1
                    else {}
                ),
            },
        )
        for index in range(count)
    ]


async def _chunk_snapshot(client: OracleClient, document_id: str) -> list[dict[str, str]]:
    """本文・metadata・embedding の保存値（補建が変えないことの比較に使う）。"""
    rows = await client._fetch_all(
        """SELECT chunk_id,chunk_set_id,chunk_text,
        JSON_SERIALIZE(metadata_json RETURNING CLOB) AS metadata_text,
        VECTOR_SERIALIZE(embedding RETURNING CLOB) AS embedding_text
        FROM rag_chunks WHERE document_id=:id ORDER BY chunk_set_id,chunk_index""",
        {"id": document_id},
    )
    return [{key: str(value) for key, value in row.items()} for row in rows]


@pytest.mark.skipif(
    os.environ.get("GITHUB_ACTIONS") != "true", reason="CI専用Oracleの匿名合成データだけで検証する"
)
@pytest.mark.usefixtures("oracle_db")
async def test_index_built_before_body_nodes_is_stale_and_rebuild_links_body() -> None:
    """#4543: 「本文」の節点の前の形の索引を、計画が STALE にし、補建が本文へ結び直す。"""
    from dataclasses import replace

    from rag_parser_core.language import current_pack

    from app.clients.structure_index_store import StructureIndexStore
    from app.rag.graph_index import build_graph_index, graph_body_node_id
    from app.rag.structure_index import source_fingerprint, structure_index_plan
    from app.schemas.extraction import StructuredExtraction

    client = OracleClient()
    kb = await client.create_knowledge_base(name=f"synthetic-body-old-{uuid4().hex[:8]}")
    await client.set_knowledge_base_structure_index(kb.id, True)
    chunks = _body_fixture_chunks("合成本文")
    document, chunk_set = await _indexed_document(
        client, file_name="synthetic-body-old.pdf", chunks=chunks, knowledge_base_id=kb.id
    )
    # #4507 の前の形: 見出しの無い chunk を「文書全体」の章節に結び、本文の節点を作らない。
    whole = current_pack().text("labels", "whole_document")
    old_chunks = [
        chunk
        if "section_path" in chunk.metadata
        else replace(chunk, metadata={**chunk.metadata, "section_path": whole})
        for chunk in chunks
    ]
    old_graph = build_graph_index(
        document_id=document,
        knowledge_base_ids=[kb.id],
        extraction=StructuredExtraction(),
        chunks=old_chunks,
        chunk_set_id=chunk_set,
        document_name="synthetic-body-old.pdf",
    )
    await StructureIndexStore(client).publish(
        kb.id,
        document,
        source_fingerprint([chunk_set]),
        [chunk_set],
        {chunk_set: old_graph},
        "COMPLETE",
        {"quality": "COMPLETE"},
    )
    body = graph_body_node_id(chunk_set, kb.id)
    whole_node = graph_section_node_id(chunk_set, kb.id, whole)
    store = StructureGraphStore(client)
    # 症状: 木は仮想の本文を出すが、本文の一覧は 0 件。
    roots = await store.children(kb.id, document, chunk_set, parent_id=None, offset=0, limit=25)
    assert any(node["id"] == body and node["virtual"] for node in roots["items"])
    assert (await store.body_chunks(kb.id, document, chunk_set, offset=0, limit=25))["total"] == 0
    before = await _chunk_snapshot(client, document)

    [item] = await structure_index_plan(client, kb.id, document_ids=[document])
    assert item["status"] == "STALE" and item["body_links_missing"] is True

    await rebuild_document_structure_index(client, kb.id, document)
    page = await store.body_chunks(kb.id, document, chunk_set, offset=0, limit=25)
    assert [row["chunk_id"] for row in page["items"]] == [
        f"{document}:{chunk_set}:{index}" for index in (0, 2, 3)
    ]
    entities = await client._fetch_all(
        "SELECT entity_id,entity_type FROM rag_graph_entities WHERE chunk_set_id=:id",
        {"id": chunk_set},
    )
    kinds = {str(row["entity_id"]): str(row["entity_type"]) for row in entities}
    assert kinds[body] == "body" and whole_node not in kinds
    assert await _chunk_snapshot(client, document) == before
    [after] = await structure_index_plan(client, kb.id, document_ids=[document])
    assert after["status"] == "CURRENT" and after["body_links_missing"] is False


@pytest.mark.skipif(
    os.environ.get("GITHUB_ACTIONS") != "true", reason="CI専用Oracleの匿名合成データだけで検証する"
)
@pytest.mark.usefixtures("oracle_db")
async def test_body_chunk_chain_is_closed_at_the_first_gate_for_restricted_users() -> None:
    """#4543: 本文の一覧 → 原文の経路は、権限の外・別の KB・別の tenant で件数を出さず 404。"""
    from dataclasses import replace

    from fastapi import HTTPException

    from app.api.routes import documents as document_routes
    from app.api.routes import knowledge_bases as kb_routes
    from app.rag.request_context import (
        current_audit_request_context,
        reset_audit_request_context,
        set_audit_request_context,
    )

    client = OracleClient()
    kb = await client.create_knowledge_base(name=f"synthetic-body-scope-{uuid4().hex[:8]}")
    other = await client.create_knowledge_base(name=f"synthetic-body-scope-o-{uuid4().hex[:8]}")
    document, chunk_set = await _indexed_document(
        client,
        file_name="synthetic-body-scope.pdf",
        chunks=_body_fixture_chunks("合成範囲"),
        knowledge_base_id=kb.id,
    )
    store = StructureGraphStore(client)
    listed = await store.body_chunks(kb.id, document, chunk_set, offset=0, limit=25)
    assert listed["total"] == 3
    chunk_id = listed["items"][0]["chunk_id"]
    assert (await document_routes.get_document_citation_chunk(document, chunk_id)).data

    with pytest.raises(HTTPException) as other_kb:
        await kb_routes.get_structure_graph_body_chunks(
            other.id, document, chunk_set_id=chunk_set, offset=0, limit=25
        )
    assert other_kb.value.status_code == 404
    base = current_audit_request_context()
    for restricted in (
        replace(base, allowed_document_ids=frozenset({"synthetic-other-document"})),
        replace(base, tenant_id_hash="synthetic-other-tenant"),
    ):
        token = set_audit_request_context(restricted)
        try:
            with pytest.raises(KeyError):
                await store.body_chunks(kb.id, document, chunk_set, offset=0, limit=25)
            with pytest.raises(HTTPException) as body_error:
                await kb_routes.get_structure_graph_body_chunks(
                    kb.id, document, chunk_set_id=chunk_set, offset=0, limit=25
                )
            assert body_error.value.status_code == 404
            assert "3" not in str(body_error.value.detail)
            with pytest.raises(HTTPException) as source_error:
                await document_routes.get_document_citation_chunk(document, chunk_id)
            assert source_error.value.status_code == 404
        finally:
            reset_audit_request_context(token)


@pytest.mark.skipif(
    os.environ.get("GITHUB_ACTIONS") != "true", reason="CI専用Oracleの匿名合成データだけで検証する"
)
@pytest.mark.usefixtures("oracle_db")
async def test_body_chunk_opened_after_version_switch_keeps_listed_version() -> None:
    """#4543: 版 A で一覧した本文の chunk は、版 B へ切り替えた後も版 A の内容で開く。"""
    from app.api.routes import documents as document_routes
    from app.schemas.extraction import StructuredExtraction
    from tests.test_graph_expansion_oracle import _EMBEDDING

    client = OracleClient()
    kb = await client.create_knowledge_base(name=f"synthetic-body-version-{uuid4().hex[:8]}")
    document, set_a = await _indexed_document(
        client,
        file_name="synthetic-body-version.pdf",
        chunks=_body_fixture_chunks("合成版A"),
        knowledge_base_id=kb.id,
    )
    store = StructureGraphStore(client)
    listed = await store.body_chunks(kb.id, document, set_a, offset=0, limit=25)
    chunk_id = listed["items"][0]["chunk_id"]

    [recipe] = await client.list_document_recipes(document)
    set_b = f"cs_graph_{uuid4().hex[:16]}"
    chunks_b = _body_fixture_chunks("合成版B")
    await client.upsert_chunk_set(
        chunk_set_id=set_b, document_id=document, recipe_id=str(recipe["recipe_id"])
    )
    await client.save_index(
        document,
        StructuredExtraction(raw_text="本文", confidence=0.9),
        chunks_b,
        [_EMBEDDING] * len(chunks_b),
        chunk_set_id=set_b,
    )
    await client.mark_chunk_set_indexed(
        chunk_set_id=set_b, chunk_count=len(chunks_b), vector_count=len(chunks_b)
    )
    await client.activate_recipe_chunk_set(
        recipe_id=str(recipe["recipe_id"]),
        chunk_set_id=set_b,
        expected_base_chunk_set_id=set_a,
        materialized_revision=int(str(recipe.get("config_revision") or 1)),
    )

    # 一覧した版 A の内容で開く（版 B の同じ位置の内容に差し替えない）。
    opened = (await document_routes.get_document_citation_chunk(document, chunk_id)).data
    assert opened is not None and opened.text == "合成版A0"
    assert opened.metadata["chunk_set_id"] == set_a
    # 版 A の本文の一覧は、active でなくなったので 404。
    with pytest.raises(KeyError):
        await store.body_chunks(kb.id, document, set_a, offset=0, limit=25)
