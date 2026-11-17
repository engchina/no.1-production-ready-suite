"""字のとおりの検索（MCP の ``rag_grep``。#5293）の実 Oracle のテスト。

``SectionMapStore.grep_chunks`` の SQL が、検索と同じ見え方の条件（範囲の外の文書を返さない）・
文書を読むツールと同じ chunk_set（今の版）・本文の chunk（派生の chunk を除く）・所有の章節で
読むことを、実 Oracle で確かめる。内容はすべて合成。
"""

from __future__ import annotations

from uuid import uuid4

import pytest
from pr_backend_core.mcp import McpToolError

from app.clients.oracle import OracleClient
from app.clients.section_map_store import SectionMapStore
from app.mcp import tools as mcp_tools
from app.rag.chunking import Chunk
from app.rag.request_context import (
    AuditRequestContext,
    reset_audit_request_context,
    set_audit_request_context,
)
from app.rag.section_chunk_map import ROOT_SECTION_ID
from app.rag.section_map_service import rebuild_document_section_map
from app.schemas.extraction import StructuredExtraction

pytestmark = pytest.mark.usefixtures("oracle_db")


async def _document(client: OracleClient, texts: list[str], *, derived: str | None = None) -> str:
    """有効な chunk_set（本文の chunk と、任意の派生の chunk）を持つ合成の文書。"""
    document = await client.create_document(
        file_name=f"synthetic-grep-{uuid4().hex[:8]}.txt",
        object_storage_path=f"synthetic-grep-{uuid4().hex[:8]}.txt",
        content_type="text/plain",
    )
    recipe = await client.ensure_default_document_recipe(document.id)
    return await _version(client, document.id, str(recipe["recipe_id"]), texts, derived=derived)


async def _version(
    client: OracleClient,
    document_id: str,
    recipe_id: str,
    texts: list[str],
    *,
    derived: str | None = None,
    base: str | None = None,
) -> str:
    suffix = uuid4().hex[:12]
    chunk_set_id, extraction_id = f"grep_{suffix}", f"ex_grep_{suffix}"
    raw = "\n".join(texts)
    await client.upsert_document_extraction_artifact(
        document_id=document_id,
        extraction_recipe_id=extraction_id,
        source_sha256="b" * 64,
        extraction=StructuredExtraction(raw_text=raw).to_document_payload(),
        status="materialized",
    )
    await client.upsert_chunk_set(
        chunk_set_id=chunk_set_id,
        document_id=document_id,
        recipe_id=recipe_id,
        extraction_recipe_id=extraction_id,
    )
    chunks = [
        Chunk(index=index, text=text, start_offset=0, end_offset=len(text))
        for index, text in enumerate(texts)
    ]
    if derived is not None:
        chunks.append(
            Chunk(
                index=len(texts),
                text=derived,
                start_offset=0,
                end_offset=len(derived),
                metadata={"content_kind": "section_summary"},
            )
        )
    await client.save_chunk_preview(
        document_id, StructuredExtraction(raw_text=raw), chunks, chunk_set_id=chunk_set_id
    )
    await client.mark_chunk_set_indexed(
        chunk_set_id=chunk_set_id, chunk_count=len(chunks), vector_count=0
    )
    await client.activate_recipe_chunk_set(
        recipe_id=recipe_id,
        chunk_set_id=chunk_set_id,
        expected_base_chunk_set_id=base,
        extraction_recipe_id=extraction_id,
    )
    return document_id


async def test_grep_reads_body_chunks_of_the_readable_version_with_sections() -> None:
    client = OracleClient()
    document_id = await _document(
        client, ["前の行\n合成の語ＡＢＣ\n後の行", "関係の無い行"], derived="合成の語の要約"
    )
    store = SectionMapStore(client)

    # 章節の対応がまだ無い chunk は章節が None（ツールは文書全体の章節にする）。
    unmapped = await store.grep_chunks(
        document_ids=[document_id], contains=None, after=None, limit=10
    )
    assert [row.text for row in unmapped] == ["前の行\n合成の語ＡＢＣ\n後の行", "関係の無い行"]
    assert {row.section_id for row in unmapped} == {None}

    await rebuild_document_section_map(document_id, oracle=client)
    rows = await store.grep_chunks(
        document_ids=[document_id], contains="合成の語", after=None, limit=10
    )
    # 派生の chunk（要約）は語を含んでも読まない。
    assert [row.chunk_index for row in rows] == [0]
    assert rows[0].section_id == ROOT_SECTION_ID
    assert rows[0].section_title == rows[0].file_name

    # 続きの位置の次から読む。
    rest = await store.grep_chunks(
        document_ids=[document_id], contains=None, after=(document_id, 0), limit=10
    )
    assert [row.chunk_index for row in rest] == [1]

    result = await mcp_tools.grep_documents(
        mcp_tools.GrepInput(text="合成の語abc", document_ids=[document_id])
    )
    assert [(match.line, match.before, match.after) for match in result.matches] == [
        ("合成の語ＡＢＣ", ["前の行"], ["後の行"])
    ]


async def test_grep_never_reads_documents_outside_the_scope() -> None:
    client = OracleClient()
    visible = await _document(client, ["合成の語の行"])
    hidden = await _document(client, ["合成の語の行"])
    store = SectionMapStore(client)
    token = set_audit_request_context(
        AuditRequestContext(allowed_document_ids=frozenset({visible}))
    )
    try:
        named = await store.grep_chunks(
            document_ids=[visible, hidden], contains="合成の語", after=None, limit=10
        )
        everything = await store.grep_chunks(
            document_ids=None, contains="合成の語", after=None, limit=1000
        )
        with pytest.raises(McpToolError) as error:
            await mcp_tools.grep_documents(
                mcp_tools.GrepInput(text="合成の語", document_ids=[visible, hidden])
            )
    finally:
        reset_audit_request_context(token)

    assert {row.document_id for row in named} == {visible}
    assert {row.document_id for row in everything} == {visible}
    assert error.value.code == mcp_tools.SOURCE_NOT_FOUND_CODE


async def test_grep_reads_only_the_current_version() -> None:
    client = OracleClient()
    document_id = await _document(client, ["旧い版の合成の語"])
    recipe = await client.ensure_default_document_recipe(document_id)
    detail = await client.get_document_recipe(document_id, str(recipe["recipe_id"]))
    assert detail is not None
    await _version(
        client,
        document_id,
        str(recipe["recipe_id"]),
        ["新しい版の合成の語"],
        base=str(detail["active_chunk_set_id"]),
    )

    rows = await SectionMapStore(client).grep_chunks(
        document_ids=[document_id], contains="合成の語", after=None, limit=10
    )

    assert [row.text for row in rows] == ["新しい版の合成の語"]
