"""DocRAG 回答が検索範囲から読む語の一覧を、実 Oracle 26ai で確かめる。

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
