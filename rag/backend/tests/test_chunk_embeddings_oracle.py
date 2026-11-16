"""埋め込みの別表と原生索引の JSON・整理の要求の同期の実 Oracle 統合テスト（#5241）。

埋め込みは ``rag_chunk_embeddings`` にあり、別表の trigger が ``rag_chunks.hybrid_payload`` に
足す。整理の要求は、JSON に埋め込みがあった行の削除・書き換えだけで出る。データは合成。
"""

import json
from typing import Any, cast
from uuid import uuid4

import pytest

from app.clients.oracle import OracleClient
from app.rag.chunking import Chunk
from app.schemas.document import FileStatus
from app.schemas.extraction import StructuredExtraction

_EMBEDDING = [0.1] * 1536


async def _payloads(client: OracleClient, chunk_set_id: str) -> list[dict[str, Any]]:
    rows = await client._fetch_all(
        "SELECT JSON_SERIALIZE(hybrid_payload RETURNING VARCHAR2(32767)) AS payload "
        "FROM rag_chunks WHERE chunk_set_id = :chunk_set_id ORDER BY chunk_index",
        {"chunk_set_id": chunk_set_id},
    )
    return [cast(dict[str, Any], json.loads(str(row["payload"]))) for row in rows]


async def _count(client: OracleClient, sql: str, binds: dict[str, object]) -> int:
    row = await client._fetch_one(sql, binds)
    assert row is not None
    return int(str(next(iter(row.values()))))


async def _events(client: OracleClient) -> int:
    return await _count(client, "SELECT COUNT(*) AS n FROM rag_native_index_events", {})


async def _embedded(client: OracleClient, chunk_set_id: str) -> int:
    return await _count(
        client,
        "SELECT COUNT(*) AS n FROM rag_chunks c JOIN rag_chunk_embeddings e "
        "ON e.chunk_id = c.chunk_id WHERE c.chunk_set_id = :chunk_set_id",
        {"chunk_set_id": chunk_set_id},
    )


@pytest.mark.usefixtures("oracle_db")
async def test_embedding_table_keeps_hybrid_payload_and_maintenance_requests_in_sync() -> None:
    client = OracleClient()
    detail = await client.create_document(
        file_name="synthetic-embedding-table.txt",
        object_storage_path="local://synthetic-embedding-table.txt",
        content_type="text/plain",
    )
    document_id = detail.id
    chunk_set_id = f"cs_embed_{uuid4().hex[:16]}"
    await client.upsert_chunk_set(chunk_set_id=chunk_set_id, document_id=document_id)
    texts = ["合成の本文その一", "合成の本文その二"]
    chunks = [
        Chunk(index=i, text=text, start_offset=0, end_offset=len(text))
        for i, text in enumerate(texts)
    ]
    extraction = StructuredExtraction(raw_text="\n".join(texts))

    # 埋め込みの無い保存: 別表に行は無く、JSON は本文だけ。
    await client.save_chunk_preview(document_id, extraction, chunks, chunk_set_id=chunk_set_id)
    assert await _embedded(client, chunk_set_id) == 0
    assert all("embedding" not in payload for payload in await _payloads(client, chunk_set_id))

    # 初めて埋め込みを入れる: JSON に埋め込みが入り、整理は要求しない。
    views = await client.list_chunk_set_chunks(chunk_set_id)
    saved = [
        Chunk(
            index=view_index,
            text=texts[view_index],
            start_offset=0,
            end_offset=len(texts[view_index]),
            metadata={"document_id": document_id, "chunk_id": view.chunk_id},
        )
        for view_index, view in enumerate(views)
    ]
    before = await _events(client)
    await client.update_chunk_set_embeddings(
        chunk_set_id=chunk_set_id, chunks=saved, embeddings=[_EMBEDDING, _EMBEDDING]
    )
    assert await _embedded(client, chunk_set_id) == 2
    payloads = await _payloads(client, chunk_set_id)
    assert [len(payload["embedding"]) for payload in payloads] == [1536, 1536]
    assert await _events(client) == before

    # 埋め込みのある行の書き換え: 整理を要求する。
    await client.update_chunk_set_embeddings(
        chunk_set_id=chunk_set_id,
        chunks=saved,
        embeddings=[[0.2] * 1536, [0.2] * 1536],
    )
    assert await _events(client) > before
    assert (await _payloads(client, chunk_set_id))[0]["embedding"][0] == pytest.approx(0.2)

    # 再 index の前の初期化: 別表の行を消し、JSON を本文だけに戻す。
    before = await _events(client)
    await client.reset_document_index_outputs(document_id, status=FileStatus.CHUNKED)
    assert await _embedded(client, chunk_set_id) == 0
    assert all("embedding" not in payload for payload in await _payloads(client, chunk_set_id))
    assert await _events(client) > before

    # 埋め込みのある chunk の行の削除は別表の行も消す(cascade)。
    await client.save_index(
        document_id, extraction, chunks, [_EMBEDDING, _EMBEDDING], chunk_set_id=chunk_set_id
    )
    assert await _embedded(client, chunk_set_id) == 2
    before = await _events(client)
    await client.save_chunk_preview(document_id, extraction, chunks, chunk_set_id=chunk_set_id)
    assert await _embedded(client, chunk_set_id) == 0
    assert await _events(client) > before
