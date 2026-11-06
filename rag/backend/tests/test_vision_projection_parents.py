"""親の本文と見出し追加後も、検索補助を回答本文へ戻さない。"""

from rag_engine.generation.answer_records import _stored_chunk_answer_record
from rag_parser_core.extraction import DocumentElement, StructuredExtraction
from rag_parser_core.vision_text import VisionTextProjection

from app.clients.oracle import OracleClient
from app.rag.answer_engine import _SearchState, _stored_child, _stored_parents
from app.rag.chunking_small_to_big import build_structure_parent_child_chunks
from app.rag.chunking_strategy import SmallToBigParams
from tests.test_vision_text_projection import stored_document


def test_actual_nonlayout_parent_and_heading_restore_own_body_projection() -> None:
    text = "画像には保存ボタンがあります。" * 5
    projection = VisionTextProjection(text, text + "\n索引専用甲")
    elements = [
        DocumentElement(kind="title", element_id="heading-a", text="# 合成見出し", page_number=1),
        DocumentElement(
            kind="figure",
            element_id="image-a",
            text=text,
            page_number=1,
            metadata={"vision_text_projection": projection.dumps()},
        ),
        DocumentElement(
            kind="text", element_id="native-a", text="原生の説明文です。" * 35, page_number=1
        ),
    ]
    extraction = StructuredExtraction(
        raw_text="\n\n".join(element.text for element in elements), elements=elements
    )
    chunks = build_structure_parent_child_chunks(
        extraction, params=SmallToBigParams(child_target_chars=200, parent_target_chars=2000)
    )
    rows = OracleClient._chunk_insert_rows(
        "document-a", stored_document(), chunks, [None] * len(chunks), "set-a"
    )
    hits = OracleClient._retrieved_chunks_from_insert_rows("document-a", stored_document(), rows)
    children = [_stored_child(hit, rrf_score=1.0) for hit in hits]
    state = _SearchState()
    state.chunks = {hit.chunk_id: hit for hit in hits}
    for child in children:
        record = _stored_chunk_answer_record(child)
        assert "索引専用甲" not in record.text
    parents = _stored_parents(children, state)
    assert parents
    for parent in parents:
        record = _stored_chunk_answer_record(parent)
        assert record.text == parent.text
        assert "索引専用甲" not in record.text
        assert "原生の説明文です。" in record.text
