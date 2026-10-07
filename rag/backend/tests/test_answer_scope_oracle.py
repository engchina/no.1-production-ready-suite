"""回答が検索範囲から読む語の一覧を、実 Oracle AI Database で確かめる。

未到達なら oracle_db fixture が skip し、作成行は cleanup_to_baseline で後始末する。
AI は決定論スタブ(oracle_db fixture)で、SQL だけを実 DB で評価する。
"""

from uuid import uuid4

import pytest

from app.clients.oracle import OracleClient
from app.rag.chunking import Chunk
from app.schemas.document import DocumentClassification, FileStatus
from app.schemas.extraction import StructuredExtraction

_EMBEDDING = [0.1] * 1536


async def _indexed_document(
    client: OracleClient,
    *,
    file_name: str,
    chunks: list[Chunk],
    large_category: str | None = None,
) -> str:
    """検索対象(INDEXED・active な chunk_set)の文書を 1 件作る。"""
    detail = await client.create_document(
        file_name=file_name,
        object_storage_path=f"local://{file_name}",
        content_type="application/pdf",
    )
    document_id = detail.id
    recipes = await client.list_document_recipes(document_id)
    recipe_id = str(recipes[0]["recipe_id"])
    chunk_set_id = f"cs_scope_{uuid4().hex[:16]}"
    await client.upsert_chunk_set(
        chunk_set_id=chunk_set_id, document_id=document_id, recipe_id=recipe_id
    )
    await client.save_index(
        document_id,
        StructuredExtraction(raw_text="本文", confidence=0.9),
        chunks,
        [_EMBEDDING] * len(chunks),
        chunk_set_id=chunk_set_id,
    )
    await client.mark_chunk_set_indexed(
        chunk_set_id=chunk_set_id, chunk_count=len(chunks), vector_count=len(chunks)
    )
    # materialized_revision は取込と同じくレシピの版を渡す(None の bind は型が決まらない)。
    await client.activate_recipe_chunk_set(
        recipe_id=recipe_id,
        chunk_set_id=chunk_set_id,
        materialized_revision=int(str(recipes[0].get("config_revision") or 1)),
    )
    await client.update_document_status(document_id, FileStatus.INDEXED)
    if large_category is not None:
        await client.save_document_classification(
            document_id, DocumentClassification(large_category=large_category)
        )
    return document_id


def _chunk(index: int, text: str, section_path: str | None = None) -> Chunk:
    return Chunk(
        index=index,
        text=text,
        start_offset=0,
        end_offset=len(text),
        metadata={"section_path": section_path} if section_path else {},
    )


@pytest.mark.usefixtures("oracle_db")
async def test_retrieval_large_categories_on_real_oracle() -> None:
    """検索範囲の文書の大分類を DISTINCT で返す。範囲外・分類の無い文書は含めない(#553)。"""
    client = OracleClient()
    token = uuid4().hex[:12]
    await _indexed_document(
        client,
        file_name=f"scope-{token}-a1.pdf",
        chunks=[_chunk(0, "A")],
        large_category="10_業務A",
    )
    await _indexed_document(
        client,
        file_name=f"scope-{token}-a2.pdf",
        chunks=[_chunk(0, "A")],
        large_category="10_業務A",
    )
    await _indexed_document(
        client, file_name=f"scope-{token}-b.pdf", chunks=[_chunk(0, "B")], large_category="業務B"
    )
    await _indexed_document(client, file_name=f"scope-{token}-none.pdf", chunks=[_chunk(0, "N")])
    # 範囲外(ファイル名の条件に合わない)の文書の大分類は含めない。
    await _indexed_document(
        client, file_name=f"other-{token}.pdf", chunks=[_chunk(0, "C")], large_category="業務C"
    )

    assert await client.retrieval_large_categories({"file_name": f"scope-{token}"}) == [
        "10_業務A",
        "業務B",
    ]


@pytest.mark.usefixtures("oracle_db")
async def test_retrieval_screen_catalog_on_real_oracle() -> None:
    """検索範囲の chunk の(文書名、見出しの列)を DISTINCT で集計し、画面の chunk を読む(#554)。

    範囲の状態は文書の追加で変わる(画面目録の cache を作り直す)。
    """
    client = OracleClient()
    token = uuid4().hex[:12]
    screen = "（２）帳票印字設定"
    filters = {"file_name": f"screen-{token}"}
    setting = f"screen-{token}-setting.pdf"
    await _indexed_document(
        client,
        file_name=setting,
        chunks=[
            _chunk(0, "設定の概要", "設定"),
            _chunk(1, "印字の有無を切り替えます。", f"設定 > {screen}"),
            _chunk(2, "印字の位置を変えます。", f"設定 > {screen}"),
            _chunk(3, "履歴を表示します。", f"設定 > {screen}の履歴"),
            _chunk(4, "見出しの無い本文"),
        ],
    )
    state_before = await client.retrieval_scope_state(filters)

    sections = await client.retrieval_screen_sections(filters)

    assert sections == [
        (setting, "設定", 1),
        (setting, f"設定 > {screen}", 2),
        (setting, f"設定 > {screen}の履歴", 1),
    ]
    chunks = await client.retrieval_screen_chunks(
        filters, file_name=setting, heading=screen, limit=10
    )
    # SQL は見出しの部分一致(「…の履歴」も含む)。要素としての一致は呼び出し側で確かめる。
    assert [chunk.text for chunk in chunks] == [
        "印字の有無を切り替えます。",
        "印字の位置を変えます。",
        "履歴を表示します。",
    ]
    assert all(chunk.file_name == setting for chunk in chunks)
    assert (
        len(
            await client.retrieval_screen_chunks(
                filters, file_name=setting, heading=screen, limit=1
            )
        )
        == 1
    )

    await _indexed_document(
        client, file_name=f"screen-{token}-other.pdf", chunks=[_chunk(0, "別文書", "（１）一覧")]
    )
    assert await client.retrieval_scope_state(filters) != state_before


@pytest.mark.usefixtures("oracle_db")
async def test_retrievable_chunk_follows_search_visibility_on_real_oracle() -> None:
    """MCP の rag_read_source が読む chunk は検索と同じ見え方の条件に従う（#1219）。

    有効な chunk_set の chunk だけを返し、古い版・利用できる範囲の外は返さない。古い版は
    ``accessible_chunk_exists`` で見分ける。
    """
    from dataclasses import replace

    from app.rag.request_context import (
        current_audit_request_context,
        reset_audit_request_context,
        set_audit_request_context,
    )

    client = OracleClient()
    token = uuid4().hex[:12]
    document_id = await _indexed_document(
        client,
        file_name=f"read-{token}.pdf",
        chunks=[_chunk(0, "立替経費は翌月10日までに申請する。", "規程 > 第2条")],
    )
    [old] = await client.list_document_chunks(document_id)

    chunk = await client.retrievable_chunk(document_id, old.chunk_id)
    assert chunk is not None
    assert chunk.text == "立替経費は翌月10日までに申請する。"
    assert chunk.metadata["section_path"] == "規程 > 第2条"
    assert chunk.metadata["chunk_set_id"]
    assert await client.retrievable_chunk(document_id, "missing-chunk") is None
    assert await client.accessible_chunk_exists(document_id, "missing-chunk") is False

    # 利用できるナレッジベースの外の文書は、検索と同じく見えない。
    scoped = set_audit_request_context(
        replace(current_audit_request_context(), allowed_knowledge_base_ids=frozenset({"kb-none"}))
    )
    try:
        assert await client.retrievable_chunk(document_id, old.chunk_id) is None
        assert await client.accessible_chunk_exists(document_id, old.chunk_id) is False
    finally:
        reset_audit_request_context(scoped)

    # 新しい版を有効にすると、古い版の chunk は検索では見えない（行が残っていれば stale）。
    recipes = await client.list_document_recipes(document_id)
    recipe_id = str(recipes[0]["recipe_id"])
    new_chunk_set_id = f"cs_read_{uuid4().hex[:16]}"
    await client.upsert_chunk_set(
        chunk_set_id=new_chunk_set_id, document_id=document_id, recipe_id=recipe_id
    )
    await client.save_index(
        document_id,
        StructuredExtraction(raw_text="本文", confidence=0.9),
        [_chunk(0, "改訂後の本文", "規程 > 第2条")],
        [_EMBEDDING],
        chunk_set_id=new_chunk_set_id,
    )
    await client.mark_chunk_set_indexed(
        chunk_set_id=new_chunk_set_id, chunk_count=1, vector_count=1
    )
    await client.activate_recipe_chunk_set(
        recipe_id=recipe_id,
        chunk_set_id=new_chunk_set_id,
        materialized_revision=int(str(recipes[0].get("config_revision") or 1)),
    )
    assert await client.retrievable_chunk(document_id, old.chunk_id) is None
    remaining = {view.chunk_id for view in await client.list_document_chunks(document_id)}
    assert await client.accessible_chunk_exists(document_id, old.chunk_id) is (
        old.chunk_id in remaining
    )
