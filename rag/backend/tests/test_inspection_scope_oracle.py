"""開始位置の帰属と、跨るChunkの重複除去を実Oracle SQLで確認する。"""

from uuid import uuid4

import pytest

from app.clients.oracle import OracleClient
from app.rag.chunking import Chunk
from tests.test_answer_scope_oracle import _indexed_document


@pytest.mark.usefixtures("oracle_db")
async def test_chunk_window_uses_start_page_and_first_element() -> None:
    oracle = OracleClient()
    document_id = await _indexed_document(
        oracle,
        file_name=f"synthetic-{uuid4().hex}.txt",
        chunks=[
            Chunk(
                index=0,
                text="最初の要素から次の要素まで",
                start_offset=0,
                end_offset=20,
                metadata={"element_ids": ["first", "second"], "page_start": 1, "page_end": 2},
            ),
            Chunk(
                index=1,
                text="次の要素",
                start_offset=20,
                end_offset=25,
                metadata={"element_ids": ["second"], "page_start": 2, "page_end": 2},
            ),
        ],
    )
    recipe = (await oracle.list_document_recipes(document_id))[0]
    chunk_set = str(recipe["active_chunk_set_id"])
    revision = "synthetic-extraction"
    await oracle.upsert_document_extraction_artifact(
        document_id=document_id,
        extraction_recipe_id=revision,
        source_sha256=None,
        status="materialized",
        extraction={
            "elements": [
                {"element_id": "first", "page_number": 1},
                {"element_id": "second", "page_number": 2},
            ]
        },
    )
    for kwargs in ({"page": 2}, {"scope_element_ids": ["second"]}):
        chunks, total = await oracle.list_chunk_set_chunk_window(
            chunk_set, extraction_recipe_id=revision, **kwargs
        )
        assert total == 1
        assert chunks[0].chunk_index == 1
    chunks, total = await oracle.list_chunk_set_chunk_window(
        chunk_set, extraction_recipe_id=revision, scope_element_ids=[]
    )
    assert total == 0 and chunks == []
