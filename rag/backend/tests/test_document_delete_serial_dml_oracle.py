"""RAG の接続が並列 DML を使わないことと、関係情報の多い文書の削除を実 Oracle で確かめる（#5314）。

ADB の HIGH / MEDIUM のサービスは並列 DML が既定で有効で、関係情報の entity を多く持つ文書の削除が
並列の実行サーバーどうしの行ロックの待ち（ORA-12860）で失敗した。Oracle AI Database Free は並列の
実行を持たない（ORA-12860 は再現しない）ので、pool の接続の初期化（並列 DML を無効にする SQL）が
実 Oracle で通ることと、その接続で動く削除の transaction が並列 DML を使わずに、FK でつながる
関係情報の行（entity と、それを参照する 2 つの表）を消し切ることを確かめる。合成の文書だけを使う。
"""

from __future__ import annotations

from typing import Any, cast
from uuid import uuid4

import pytest

from app.clients.oracle import (
    OracleClient,
    OracleConnectionProtocol,
    _fetch_all,
    _fetch_one,
    delete_document_rows,
)
from app.rag.chunking import Chunk
from app.rag.graph_index import build_graph_index
from app.schemas.document import FileStatus
from app.schemas.extraction import StructuredExtraction

_SESSION_STATUS_SQL = """
SELECT pdml_status, pq_status
FROM v$session
WHERE sid = SYS_CONTEXT('USERENV', 'SID')
"""
_EMBEDDING = [0.1] * 1536


def _session_status(connection: Any) -> tuple[str, str]:
    row = _fetch_one(cast(OracleConnectionProtocol, connection), _SESSION_STATUS_SQL, {})
    assert row is not None
    return str(row["pdml_status"]), str(row["pq_status"])


def _chunks(section_count: int, per_section: int) -> list[Chunk]:
    chunks: list[Chunk] = []
    for section in range(section_count):
        path = f"章 {section + 1:03d} > 節 {section % 7 + 1}"
        for part in range(per_section):
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


async def _document_with_graph(client: OracleClient, knowledge_base_id: str) -> tuple[str, str]:
    """関係情報の entity を多く持つ合成の文書を作り、文書 ID と chunk_set ID を返す。"""
    file_name = f"serial-dml-{uuid4().hex[:8]}.pdf"
    detail = await client.create_document(
        file_name=file_name,
        object_storage_path=f"local://{file_name}",
        content_type="application/pdf",
        knowledge_base_ids=[knowledge_base_id],
    )
    recipes = await client.list_document_recipes(detail.id)
    recipe_id = str(recipes[0]["recipe_id"])
    chunk_set_id = f"cs_sdml_{uuid4().hex[:16]}"
    await client.upsert_chunk_set(
        chunk_set_id=chunk_set_id, document_id=detail.id, recipe_id=recipe_id
    )
    chunks = _chunks(section_count=190, per_section=2)
    extraction = StructuredExtraction(raw_text="本文", confidence=0.9, document_type="手順書")
    await client.save_index(
        detail.id, extraction, chunks, [_EMBEDDING] * len(chunks), chunk_set_id=chunk_set_id
    )
    await client.mark_chunk_set_indexed(
        chunk_set_id=chunk_set_id, chunk_count=len(chunks), vector_count=len(chunks)
    )
    await client.activate_recipe_chunk_set(
        recipe_id=recipe_id,
        chunk_set_id=chunk_set_id,
        expected_base_chunk_set_id=None,
        materialized_revision=int(str(recipes[0].get("config_revision") or 1)),
    )
    await client.update_document_status(detail.id, FileStatus.INDEXED)
    graph = build_graph_index(
        document_id=detail.id,
        knowledge_base_ids=[knowledge_base_id],
        extraction=extraction,
        chunks=chunks,
        chunk_set_id=chunk_set_id,
        document_name=file_name,
    )
    await client.replace_document_graph_index(detail.id, graph, chunk_set_id=chunk_set_id)
    return detail.id, chunk_set_id


def _graph_counts(connection: OracleConnectionProtocol, chunk_set_id: str) -> dict[str, int]:
    rows = _fetch_all(
        connection,
        """
        SELECT 'entities' AS name, COUNT(*) AS n FROM rag_graph_entities
        WHERE chunk_set_id = :chunk_set_id
        UNION ALL
        SELECT 'relationships', COUNT(*) FROM rag_graph_relationships
        WHERE chunk_set_id = :chunk_set_id
        UNION ALL
        SELECT 'entity_chunks', COUNT(*) FROM rag_graph_entity_chunks
        WHERE chunk_set_id = :chunk_set_id
        """,
        {"chunk_set_id": chunk_set_id},
    )
    return {str(row["name"]): int(str(row["n"])) for row in rows}


@pytest.mark.usefixtures("oracle_db")
async def test_document_with_many_graph_entities_is_deleted_without_parallel_dml() -> None:
    client = OracleClient()
    knowledge_base = await client.create_knowledge_base(name=f"KB serial dml {uuid4().hex[:8]}")
    document_id, chunk_set_id = await _document_with_graph(client, knowledge_base.id)
    try:
        before = await client._run_transaction(
            lambda connection: _graph_counts(connection, chunk_set_id)
        )
        # 親の表（entity）と FK で参照する 2 つの表の行がそろって多い文書にする。
        assert before["entities"] >= 180
        assert before["relationships"] >= 180
        assert before["entity_chunks"] >= 360

        def delete(connection: OracleConnectionProtocol) -> tuple[tuple[str, str], bool]:
            # 削除と同じ transaction（同じ pool の接続）で並列 DML の状態を読む。
            return _session_status(connection), delete_document_rows(connection, document_id)

        (pdml_status, _), deleted = await client._run_transaction(delete, retry_deadlocks=True)

        assert pdml_status == "DISABLED"
        assert deleted is True
        assert await client.get_document(document_id) is None
        after = await client._run_transaction(
            lambda connection: _graph_counts(connection, chunk_set_id)
        )
        assert after == {"entities": 0, "relationships": 0, "entity_chunks": 0}
    finally:
        if await client.get_document(document_id) is not None:
            await client.delete_document(document_id)
