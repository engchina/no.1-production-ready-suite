"""関係情報で検索の候補を足す SQL（#2055）を、実 Oracle AI Database で確かめる。

合成の文書（架空の手順書）を chunk として保存し、取込と同じ ``build_graph_index`` で関係情報の表を
作り、当たった chunk と同じ狭い章節の chunk・質問に見出しの語が含まれる章節の chunk が SQL の join
で返ること、範囲の外（別のナレッジベース）の文書を返さないことを確かめる。未到達なら oracle_db
fixture が skip し、作成行は後始末する（AI は使わない）。
"""

from __future__ import annotations

from dataclasses import replace
from uuid import uuid4

import pytest

from app.clients.oracle import OracleClient
from app.clients.oracle_graph_store import OracleGraphStore
from app.clients.structure_graph_store import StructureGraphStore
from app.rag.chunking import Chunk
from app.rag.graph_expansion import GRAPH_KIND_NAME, GRAPH_KIND_NEIGHBOR, plan_graph_expansion
from app.rag.graph_index import GraphIndex, build_graph_index
from app.rag.request_context import (
    current_audit_request_context,
    reset_audit_request_context,
    set_audit_request_context,
)
from app.schemas.document import FileStatus
from app.schemas.extraction import StructuredExtraction
from app.schemas.search import RetrievedChunk

_EMBEDDING = [0.1] * 1536


@pytest.mark.usefixtures("oracle_db")
async def test_graph_visibility_read_keeps_exact_id_scope_and_archived() -> None:
    """図の存在確認も、詳細と同じID・利用範囲・アーカイブ状態で読む。"""
    client = OracleClient()
    kb = await client.create_knowledge_base(name=f"synthetic-visibility-{uuid4().hex[:8]}")
    assert await client.knowledge_base_is_visible(kb.id)
    for missing in ("", "   ", f" {kb.id} ", uuid4().hex):
        assert not await client.knowledge_base_is_visible(missing)
    for allowed, expected in (
        (frozenset({kb.id}), True),
        (frozenset({uuid4().hex}), False),
        (frozenset(), False),
    ):
        token = set_audit_request_context(
            replace(current_audit_request_context(), allowed_knowledge_base_ids=allowed)
        )
        try:
            assert await client.knowledge_base_is_visible(kb.id) is expected
            assert (await client.get_knowledge_base(kb.id) is not None) is expected
        finally:
            reset_audit_request_context(token)
    await client.archive_knowledge_base(kb.id)
    assert await client.knowledge_base_is_visible(kb.id)
    assert await client.get_knowledge_base(kb.id) is not None


@pytest.mark.usefixtures("oracle_db")
async def test_multibyte_structure_heading_is_saved_without_byte_truncation() -> None:
    client = OracleClient()
    kb = await client.create_knowledge_base(name=f"合成文字数KB {uuid4().hex[:8]}")
    heading = ("合成の長い見出し" * 100)[:512]
    _, chunk_set_id = await _indexed_document(
        client,
        file_name="synthetic-multibyte.pdf",
        chunks=_chunks([(heading, 1)]),
        knowledge_base_id=kb.id,
    )
    rows = await client._fetch_all(
        "SELECT canonical_name FROM rag_graph_entities WHERE chunk_set_id=:id",
        {"id": chunk_set_id},
    )
    assert heading in [row["canonical_name"] for row in rows]


@pytest.mark.usefixtures("oracle_db")
async def test_subgraph_hides_documents_outside_access_scope() -> None:
    """KB を使えても、権限の外の文書のノードと関係を返さない(#4308)。"""
    client = OracleClient()
    token_suffix = uuid4().hex[:8]
    kb = await client.create_knowledge_base(name=f"graph-access-{token_suffix}")
    allowed_document, _ = await _indexed_document(
        client,
        file_name=f"graph-access-allowed-{token_suffix}.pdf",
        chunks=_chunks([("allowed-section", 1)]),
        knowledge_base_id=kb.id,
    )
    hidden_name = f"graph-access-hidden-{token_suffix}.pdf"
    hidden_document, hidden_set = await _indexed_document(
        client,
        file_name=hidden_name,
        chunks=_chunks([("hidden-section", 1)]),
        knowledge_base_id=kb.id,
    )
    try:
        everything, _ = await _visible_graph(client, kb.id)
        assert any(hidden_name in str(node["name"]) for node in everything)

        context = replace(
            current_audit_request_context(), allowed_document_ids=frozenset({allowed_document})
        )
        token = set_audit_request_context(context)
        try:
            nodes, _edges = await _visible_graph(client, kb.id)
            found = await StructureGraphStore(client).search(kb.id, "section", offset=0, limit=25)
            with pytest.raises(KeyError):
                await StructureGraphStore(client).assert_document(
                    kb.id, hidden_document, hidden_set
                )
        finally:
            reset_audit_request_context(token)
        names = {str(node["name"]) for node in nodes}
        assert "allowed-section" in names
        assert not any("hidden" in name for name in names)
        assert {item["name"] for item in found["items"]} == {"allowed-section"}
    finally:
        await client.delete_document(allowed_document)
        await client.delete_document(hidden_document)


async def _visible_graph(
    client: OracleClient, kb_id: str
) -> tuple[list[dict[str, object]], list[dict[str, object]]]:
    """図の総覧と各文書の全段（画面が開いて読む範囲をまとめて読む）。"""
    store = StructureGraphStore(client)
    overview = await store.overview(kb_id)
    nodes: list[dict[str, object]] = list(overview["documents"])
    for document in overview["documents"]:
        parents: list[str | None] = [None]
        while parents:
            page = await store.children(
                kb_id,
                document["document_id"],
                document["chunk_set_id"],
                parent_id=parents.pop(),
                offset=0,
                limit=1000,
            )
            nodes.extend(page["items"])
            parents.extend(item["id"] for item in page["items"] if item["child_count"])
    return nodes, overview["edges"]


def _chunks(sections: list[tuple[str, int]]) -> list[Chunk]:
    chunks: list[Chunk] = []
    for path, count in sections:
        for part in range(count):
            text = f"{path} の本文 {part + 1}"
            chunks.append(
                Chunk(
                    text=text,
                    index=len(chunks),
                    start_offset=0,
                    end_offset=len(text),
                    metadata={"section_path": path},
                )
            )
    return chunks


async def _indexed_document(
    client: OracleClient,
    *,
    file_name: str,
    chunks: list[Chunk],
    knowledge_base_id: str,
    activate: bool = True,
) -> tuple[str, str]:
    """文書と関係情報を作り、文書 ID と chunk_set ID を返す。activate=False は切替前。"""
    detail = await client.create_document(
        file_name=file_name,
        object_storage_path=f"local://{file_name}",
        content_type="application/pdf",
        knowledge_base_ids=[knowledge_base_id],
    )
    document_id = detail.id
    recipes = await client.list_document_recipes(document_id)
    recipe_id = str(recipes[0]["recipe_id"])
    chunk_set_id = f"cs_graph_{uuid4().hex[:16]}"
    await client.upsert_chunk_set(
        chunk_set_id=chunk_set_id, document_id=document_id, recipe_id=recipe_id
    )
    extraction = StructuredExtraction(raw_text="本文", confidence=0.9, document_type="手順書")
    await client.save_index(
        document_id,
        extraction,
        chunks,
        [_EMBEDDING] * len(chunks),
        chunk_set_id=chunk_set_id,
    )
    await client.mark_chunk_set_indexed(
        chunk_set_id=chunk_set_id, chunk_count=len(chunks), vector_count=len(chunks)
    )
    if activate:
        await client.activate_recipe_chunk_set(
            recipe_id=recipe_id,
            chunk_set_id=chunk_set_id,
            expected_base_chunk_set_id=None,
            materialized_revision=int(str(recipes[0].get("config_revision") or 1)),
        )
    await client.update_document_status(document_id, FileStatus.INDEXED)
    graph = build_graph_index(
        document_id=document_id,
        knowledge_base_ids=[knowledge_base_id],
        extraction=extraction,
        chunks=chunks,
        chunk_set_id=chunk_set_id,
        document_name=file_name,
    )
    await client.replace_document_graph_index(document_id, graph, chunk_set_id=chunk_set_id)
    return document_id, chunk_set_id


@pytest.mark.usefixtures("oracle_db")
async def test_graph_expansion_joins_on_real_oracle() -> None:
    client = OracleClient()
    store = OracleGraphStore(client)
    token = uuid4().hex[:8]
    kb = await client.create_knowledge_base(name=f"graph-{token}")
    other_kb = await client.create_knowledge_base(name=f"graph-other-{token}")
    sections = [
        ("手順書 > 第 1 章 目的", 1),
        ("手順書 > 第 2 章 作業の準備", 3),
        ("手順書 > 第 3 章 作業の記録の保管", 2),
    ]
    saved = [
        await _indexed_document(
            client,
            file_name=f"graph-{token}-a.pdf",
            chunks=_chunks(sections),
            knowledge_base_id=kb.id,
        ),
        # 別のナレッジベースの同じ見出しの文書（範囲の外）。
        await _indexed_document(
            client,
            file_name=f"graph-{token}-b.pdf",
            chunks=_chunks(sections),
            knowledge_base_id=other_kb.id,
        ),
    ]
    try:
        document_id, chunk_set_id = saved[0]
        ids = [f"{document_id}:{chunk_set_id}:{index}" for index in range(6)]
        seed_chunk = RetrievedChunk(
            document_id=document_id,
            chunk_id=ids[2],
            text="手順書 > 第 2 章 作業の準備 の本文 2",
            score=1.0,
            metadata={"chunk_index": 2},
        )

        plan = await plan_graph_expansion(
            store,
            {"knowledge_base_id": kb.id},
            question="作業の記録の保管の期間は？",
            seed_chunks=[seed_chunk],
            exclude_chunk_ids={ids[2]},
            max_chunks=6,
        )

        # 名前の一致（第 3 章）と、当たった chunk と同じ第 2 章の chunk（近い順）。
        assert [item.chunk.chunk_id for item in plan] == [ids[4], ids[5], ids[1], ids[3]]
        assert [item.info["kind"] for item in plan] == [GRAPH_KIND_NAME] * 2 + [
            GRAPH_KIND_NEIGHBOR
        ] * 2
        assert plan[0].chunk.text == "手順書 > 第 3 章 作業の記録の保管 の本文 1"
        # 範囲の外のナレッジベースの文書は返さない。
        assert {item.chunk.document_id for item in plan} == {document_id}
    finally:
        for document_id, chunk_set_id in saved:
            await client.replace_document_graph_index(
                document_id, GraphIndex(), chunk_set_id=chunk_set_id
            )


@pytest.mark.usefixtures("oracle_db")
async def test_subgraph_hides_pending_graph_until_activation() -> None:
    """グラフを保存しても有効化までは表示せず、切替後に node と edge を表示する。"""
    client = OracleClient()
    token = uuid4().hex[:8]
    kb = await client.create_knowledge_base(name=f"graph-activation-{token}")
    document_id, chunk_set_id = await _indexed_document(
        client,
        file_name=f"graph-activation-{token}.pdf",
        chunks=_chunks([("section-a", 1)]),
        knowledge_base_id=kb.id,
        activate=False,
    )
    try:
        nodes, edges = await _visible_graph(client, kb.id)
        assert nodes == []
        assert edges == []

        recipes = await client.list_document_recipes(document_id)
        await client.activate_recipe_chunk_set(
            recipe_id=str(recipes[0]["recipe_id"]),
            chunk_set_id=chunk_set_id,
            expected_base_chunk_set_id=None,
        )
        nodes, _edges = await _visible_graph(client, kb.id)
        assert {str(node["name"]) for node in nodes} >= {"section-a"}
    finally:
        await client.delete_document(document_id)


@pytest.mark.usefixtures("oracle_db")
async def test_kb_structure_override_and_rebuild_preserve_shared_document(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.config import get_settings
    from app.rag.structure_index import rebuild_document_structure_index, structure_index_targets

    settings = get_settings()
    monkeypatch.setattr(settings, "rag_graph_profile", "off")
    client = OracleClient(settings)
    token = uuid4().hex[:8]
    a = await client.create_knowledge_base(name=f"合成構造A {token}")
    b = await client.create_knowledge_base(name=f"合成構造B {token}")
    text = "合成章節の本文"
    doc, cs = await _indexed_document(
        client,
        knowledge_base_id=a.id,
        file_name=f"synthetic-structure-{token}.txt",
        chunks=[
            Chunk(
                text=text,
                index=0,
                start_offset=0,
                end_offset=len(text),
                metadata={"section_title": "合成節", "section_path": "合成章 / 合成節"},
            )
        ],
    )
    await client.assign_documents_to_knowledge_base(b.id, [doc])
    await client.set_knowledge_base_structure_index(a.id, False)
    await client.set_knowledge_base_structure_index(b.id, True)
    assert await structure_index_targets(client, doc, default=False) == [b.id]
    before_a = await _visible_graph(client, a.id)
    before = await client._fetch_all(
        "SELECT chunk_id,chunk_text,embedding FROM rag_chunks WHERE chunk_set_id=:id", {"id": cs}
    )
    one = await rebuild_document_structure_index(client, b.id, doc)
    two = await rebuild_document_structure_index(client, b.id, doc)
    assert one == two and one["entities"] > 1
    from app.clients.structure_index_store import StructureIndexStore
    from app.rag.structure_index import source_fingerprint, structure_index_plan
    from app.rag.structure_index_jobs import StructureIndexJobs

    plan = await structure_index_plan(client, b.id)
    assert len(plan) == 1 and plan[0]["status"] == "CURRENT"
    assert plan[0]["quality"] == "COMPLETE"
    unchanged_graph = await _visible_graph(client, b.id)
    with pytest.raises(ValueError, match="source_changed"):
        await StructureIndexStore(client).publish(
            b.id, doc, source_fingerprint(["old"]), ["old"], {}, "COMPLETE", {}
        )
    assert await _visible_graph(client, b.id) == unchanged_graph
    from app.rag.request_context import current_audit_request_context

    async def synthetic_actor() -> object:
        return current_audit_request_context()

    # DB結合テストの実行者を固定し、認証サービス自体は既存のsecurity回帰で確認する。
    monkeypatch.setattr("app.rag.structure_index_jobs.refresh_actor", synthetic_actor)
    service = StructureIndexJobs()
    await service.start(client, b.id, "missing")
    await __import__("asyncio").gather(*service.tasks)
    persisted = await StructureIndexStore(client).get(b.id)
    assert persisted and persisted["status"] == "COMPLETED"
    assert persisted["items"][0]["status"] == "SKIPPED"
    await service.start(client, b.id, "rebuild")
    await __import__("asyncio").gather(*service.tasks)
    published = await StructureIndexStore(client).get(b.id)
    assert published and published["items"][0]["status"] == "SUCCEEDED"
    assert await _visible_graph(client, a.id) == before_a
    assert (await _visible_graph(client, b.id))[0]
    after = await client._fetch_all(
        "SELECT chunk_id,chunk_text,embedding FROM rag_chunks WHERE chunk_set_id=:id", {"id": cs}
    )
    assert before == after
    state = await client.get_chunk_set(cs)
    assert state is not None and state["is_active"] == 1
    await client.set_knowledge_base_structure_index(b.id, False)
    with pytest.raises(ValueError, match="disabled"):
        await rebuild_document_structure_index(client, b.id, doc)
    await client.set_knowledge_base_structure_index(b.id, None)
    assert await structure_index_targets(client, doc, default=False) == []


@pytest.mark.usefixtures("oracle_db")
async def test_bounded_structure_graph_keeps_document_parents_and_connections() -> None:
    client = OracleClient()
    token = uuid4().hex[:8]
    kb = await client.create_knowledge_base(name=f"合成表示範囲 {token}")
    documents = []
    try:
        for number in range(2):
            document_id, _chunk_set = await _indexed_document(
                client,
                file_name=f"synthetic-graph-{token}-{number}.txt",
                chunks=_chunks([(f"合成節 {number}-{index:03}", 1) for index in range(60)]),
                knowledge_base_id=kb.id,
            )
            documents.append(document_id)
        overview = await StructureGraphStore(client).overview(kb.id)
        assert {item["document_id"] for item in overview["documents"]} == set(documents)
    finally:
        for document_id in documents:
            await client.delete_document(document_id)
