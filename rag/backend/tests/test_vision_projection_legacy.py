"""旧VLMの形式を推測で原文へ戻さず、native OCRは保つ。"""

from dataclasses import replace

import pytest
from rag_engine.generation.answer_records import _answer_record, _stored_chunk_answer_record

from app.rag.answer_engine import _stored_child
from app.schemas.search import RetrievedChunk


def test_saved_backend_explicit_vision_without_projection_requires_reparse() -> None:
    hit = RetrievedChunk(
        document_id="document-a",
        chunk_id="chunk-a",
        text="旧画像説明",
        score=1.0,
        metadata={"figure_text_source": "vision", "engine_search_text": "旧画像説明と索引補助"},
    )
    with pytest.raises(ValueError, match="再解析"):
        _stored_child(hit, rrf_score=1.0)


def test_local_generation_explicit_unrecorded_source_requires_reparse() -> None:
    hit = RetrievedChunk(
        document_id="document-a", chunk_id="chunk-a", text="旧画像説明", score=1.0, metadata={}
    )
    chunk = replace(
        _stored_child(hit, rrf_score=1.0),
        source_record_refs=(
            {"category": "Picture", "vision_text_projection_status": "unrecorded"},
        ),
        retrieval_text="旧画像説明と索引補助",
    )
    with pytest.raises(ValueError, match="再解析"):
        _stored_chunk_answer_record(chunk)


def test_raw_generation_successful_picture_without_raw_projection_requires_reparse() -> None:
    source = {
        "id": "image-a",
        "engine": "docling",
        "page": 1,
        "seq_no": 1,
        "category": "Picture",
        "raw_type": "picture",
        "text": "旧画像説明",
        "raw": {"vision_status": "succeeded", "vision_model": "synthetic-model"},
    }
    with pytest.raises(ValueError, match="再解析"):
        _answer_record(source, {"docling": "Docling"})


def test_native_ocr_is_not_rejected_by_model_name_or_literal_search_words() -> None:
    source = {
        "id": "ocr-a",
        "engine": "docling",
        "page": 1,
        "seq_no": 1,
        "category": "Picture",
        "raw_type": "picture_ocr_text",
        "text": "検索語は入力欄の項目です。",
        "raw": {"vision_model": "synthetic-model"},
    }
    record = _answer_record(source, {"docling": "Docling"})
    assert record is not None and record.text == source["text"]
    hit = RetrievedChunk(
        document_id="document-a",
        chunk_id="chunk-a",
        text=record.text,
        score=1.0,
        metadata={"figure_text_source": "ocr"},
    )
    assert _stored_child(hit, rrf_score=1.0).text == record.text


def test_one_valid_marker_does_not_override_an_explicit_unrecorded_image_source() -> None:
    from rag_parser_core.vision_text import require_projected_vision_source, text_sha256

    with pytest.raises(ValueError, match="再解析"):
        require_projected_vision_source(
            {
                "answer_text_projection": {
                    "version": 1,
                    "body_sha256": text_sha256("旧範囲と新範囲"),
                }
            },
            [{"category": "Picture", "vision_text_projection_status": "unrecorded"}],
        )


@pytest.mark.parametrize("top", [{}, {"figure_text_source": "ocr"}])
def test_decoded_vision_origin_is_not_erased_by_missing_or_other_top_level_value(
    top: dict[str, str],
) -> None:
    import json

    hit = RetrievedChunk(
        document_id="document-a",
        chunk_id="chunk-a",
        text="旧画像説明",
        score=1.0,
        metadata={
            **top,
            "engine_metadata_json": json.dumps(
                {"schema_version": 4, "figure_text_source": "vision"}
            ),
        },
    )
    with pytest.raises(ValueError, match="再解析"):
        _stored_child(hit, rrf_score=1.0)
