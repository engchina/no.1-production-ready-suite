"""各分割方式で画像の回答本文と索引専用語を別に保つ。"""

import pytest
from rag_parser_core.extraction import DocumentElement, StructuredExtraction
from rag_parser_core.vision_text import VisionTextProjection
from rag_pipeline_core.chunking import CHUNKING_STRATEGIES, chunk_extraction_with_strategy


@pytest.mark.parametrize("strategy", CHUNKING_STRATEGIES)
def test_visual_search_only_marker_reaches_citable_body_in_each_strategy(strategy: str) -> None:
    projection = VisionTextProjection(
        "画像Aには保存ボタンがあります。", "画像Aには保存ボタンがあります。\n\n索引専用甲"
    )
    extraction = StructuredExtraction(
        raw_text=projection.answer_description,
        elements=[
            DocumentElement(
                kind="figure",
                element_id="image-a",
                text=projection.answer_description,
                page_number=1,
                metadata={"vision_text_projection": projection.dumps()},
            )
        ],
    )
    chunks = chunk_extraction_with_strategy(
        extraction, strategy=strategy, chunk_size=200, overlap=0, delimiter="\n\n"
    )
    assert chunks
    hits = [
        chunk
        for chunk in chunks
        if "索引専用甲" in str(chunk.metadata.get("engine_search_text") or chunk.text)
    ]
    assert hits
    assert all("保存ボタン" in chunk.text and "索引専用甲" not in chunk.text for chunk in hits)


@pytest.mark.parametrize("strategy", CHUNKING_STRATEGIES)
def test_native_literal_search_words_stay_native_in_each_strategy(strategy: str) -> None:
    native = "「検索語」と「言い換え」は設定画面の実際の項目名です。"
    extraction = StructuredExtraction(
        raw_text=native,
        elements=[DocumentElement(kind="text", element_id="native-a", text=native, page_number=1)],
    )
    chunks = chunk_extraction_with_strategy(
        extraction, strategy=strategy, chunk_size=200, overlap=0, delimiter="\n\n"
    )
    assert chunks and any(native in chunk.text for chunk in chunks)
    assert all("answer_text_projection" not in chunk.metadata for chunk in chunks)


@pytest.mark.parametrize("strategy", CHUNKING_STRATEGIES)
def test_actual_producer_and_backend_read_keep_ocr_as_answer_and_auxiliary_as_index(
    strategy: str,
) -> None:
    from rag_engine.generation.answer_records import _stored_chunk_answer_record

    from app.clients.oracle import OracleClient
    from app.rag.answer_engine import _stored_child
    from app.rag.vision import _Layout, _write_back_docling
    from tests.test_vision_producer_bindings import record
    from tests.test_vision_text_projection import stored_document

    source = record("Picture")
    native = "画像内OCRの値123です。"
    extraction = _write_back_docling(
        StructuredExtraction(
            raw_text=native,
            elements=[
                DocumentElement(kind="figure", element_id=source.id, text=native, page_number=1)
            ],
        ),
        _Layout(records=[source]),
    )
    chunks = chunk_extraction_with_strategy(
        extraction, strategy=strategy, chunk_size=200, overlap=0, delimiter="\n\n"
    )
    rows = OracleClient._chunk_insert_rows(
        "document-a", stored_document(), chunks, [None] * len(chunks), "set-a"
    )
    hits = OracleClient._retrieved_chunks_from_insert_rows("document-a", stored_document(), rows)
    assert hits
    for hit in hits:
        assert native == hit.text
        assert "索引専用甲" in str(hit.metadata.get("engine_search_text"))
        answer = _stored_chunk_answer_record(_stored_child(hit, rrf_score=1.0))
        assert answer.text == native
        assert "索引専用甲" not in answer.text


@pytest.mark.parametrize("strategy", CHUNKING_STRATEGIES)
def test_long_mixed_native_image_split_offsets_are_not_guessed_from_repeated_words(
    strategy: str,
) -> None:
    from rag_parser_core.vision_text_map import chunk_projected_text

    first = "原生の検索語です。" * 5
    body = "同じ表示です。" * 14
    tail = "原生末尾です。" * 5
    projection = VisionTextProjection(body, body + "\n画像専用索引")
    elements = [
        DocumentElement(kind="text", element_id="native-a", text=first, page_number=1),
        DocumentElement(
            kind="figure",
            element_id="image-a",
            text=body,
            page_number=1,
            metadata={"vision_text_projection": projection.dumps()},
        ),
        DocumentElement(kind="text", element_id="native-b", text=tail, page_number=1),
    ]
    extraction = StructuredExtraction(
        raw_text="\n\n".join(e.text for e in elements), elements=elements
    )
    chunks = chunk_extraction_with_strategy(
        extraction, strategy=strategy, chunk_size=50, overlap=8, min_chars=20, delimiter="\n\n"
    )
    assert chunks
    linked = 0
    for chunk in chunks:
        value = chunk_projected_text(chunk.text, chunk.metadata)
        if "画像専用索引" in str(chunk.metadata.get("engine_search_text") or ""):
            linked += 1
            assert value.ranges
            for item in value.ranges:
                assert item.source.element_id == "image-a"
                assert "原生" not in value.text[item.start : item.end]
        assert "画像専用索引" not in chunk.text
    assert linked


@pytest.mark.parametrize("strategy", CHUNKING_STRATEGIES)
def test_actual_native_table_supplement_is_not_assigned_to_native_html(strategy: str) -> None:
    from rag_parser_core.vision_text_map import chunk_projected_text

    from app.rag.vision import _Layout, _write_back_docling
    from tests.test_vision_producer_bindings import record

    source = record("Table")
    native = (
        "<table><tr><th>項目</th></tr>"
        + "".join("<tr><td>原生値123</td></tr>" for _ in range(8))
        + "</table>"
    )
    extraction = _write_back_docling(
        StructuredExtraction(
            raw_text="",
            elements=[
                DocumentElement(kind="table", element_id=source.id, text=native, page_number=1)
            ],
        ),
        _Layout(records=[source]),
    )
    chunks = chunk_extraction_with_strategy(
        extraction, strategy=strategy, chunk_size=80, overlap=0, delimiter="\n\n"
    )
    assert chunks
    linked = [
        chunk
        for chunk in chunks
        if "索引専用甲" in str(chunk.metadata.get("engine_search_text") or "")
    ]
    assert linked
    for chunk in linked:
        value = chunk_projected_text(chunk.text, chunk.metadata)
        assert value.ranges
        assert all("原生値123" not in value.text[item.start : item.end] for item in value.ranges)
        assert "索引専用甲" not in value.text
    if strategy in ("structure_aware", "page_level"):
        assert any("原生値123" in chunk.text for chunk in chunks)


@pytest.mark.parametrize("strategy", CHUNKING_STRATEGIES)
def test_new_producer_preserves_opaque_native_prefix_suffix_using_parser_offsets(
    strategy: str,
) -> None:
    from rag_parser_core.vision_text import text_sha256
    from rag_parser_core.vision_text_map import chunk_projected_text

    from app.rag.vision import _Layout, _write_back_docling
    from tests.test_vision_producer_bindings import record

    body = "画像OCRの原文です。"
    prefix = "要素に含まれない原生prefixです。\n"
    suffix = "\n要素に含まれない原生suffixです。"
    raw = prefix + body + suffix
    metadata: dict[str, str | int | float | bool | None] = {
        "raw_start": len(prefix),
        "raw_end": len(prefix) + len(body),
        "raw_source_sha256": text_sha256(raw),
    }
    extraction = _write_back_docling(
        StructuredExtraction(
            raw_text=raw,
            elements=[
                DocumentElement(
                    kind="figure", element_id="image-a", text=body, page_number=1, metadata=metadata
                )
            ],
        ),
        _Layout(records=[record("Picture")]),
    )
    assert extraction.raw_text == raw
    assembly = extraction.parser_artifacts["vision_text_assembly"]
    assert isinstance(assembly, dict)
    assert assembly["position_basis"] == "producer_offsets"
    chunks = chunk_extraction_with_strategy(
        extraction, strategy=strategy, chunk_size=200, overlap=0, delimiter="\n\n"
    )
    linked = [
        chunk
        for chunk in chunks
        if "索引専用甲" in str(chunk.metadata.get("engine_search_text") or "")
    ]
    assert linked
    for chunk in linked:
        projection = chunk_projected_text(chunk.text, chunk.metadata)
        assert all(
            "prefix" not in projection.text[item.start : item.end]
            and "suffix" not in projection.text[item.start : item.end]
            for item in projection.ranges
        )
    if strategy in ("fixed_size", "recursive_character", "markdown_heading", "fixed_delimiter"):
        assert any("prefix" in chunk.text for chunk in chunks)
        assert any("suffix" in chunk.text for chunk in chunks)


@pytest.mark.parametrize(
    "strategy", ["fixed_size", "recursive_character", "markdown_heading", "fixed_delimiter"]
)
def test_unproven_legacy_prefix_cannot_be_recovered_with_substring_search(strategy: str) -> None:
    projection = VisionTextProjection("同じ画像", "同じ画像\n索引専用甲")
    extraction = StructuredExtraction(
        raw_text="位置不明prefix\n同じ画像\n同じ画像",
        elements=[
            DocumentElement(
                kind="figure",
                element_id="image-a",
                text="同じ画像",
                metadata={"vision_text_projection": projection.dumps()},
            )
        ],
    )
    with pytest.raises(ValueError, match="再解析"):
        chunk_extraction_with_strategy(
            extraction, strategy=strategy, chunk_size=200, overlap=0, delimiter="\n\n"
        )


@pytest.mark.parametrize("strategy", ["structure_aware", "page_level"])
def test_native_table_literal_equal_to_image_description_is_not_a_saved_supplement(
    strategy: str,
) -> None:
    from app.rag.vision import _Layout, _write_back_docling
    from tests.test_vision_producer_bindings import record

    source = record("Table")
    native = "<table><tr><td>" + source.text + "</td></tr></table>"
    extraction = _write_back_docling(
        StructuredExtraction(
            raw_text="",
            elements=[
                DocumentElement(kind="table", element_id=source.id, text=native, page_number=1)
            ],
        ),
        _Layout(records=[source]),
    )
    chunks = chunk_extraction_with_strategy(
        extraction, strategy=strategy, chunk_size=200, overlap=0
    )
    assert any(
        "索引専用甲" in str(chunk.metadata.get("engine_search_text") or "") for chunk in chunks
    )
    assert all("索引専用甲" not in chunk.text for chunk in chunks)


def test_opaque_raw_is_not_certified_as_native_by_new_projection_hash() -> None:
    from rag_engine.generation.answer_records import _stored_chunk_answer_record
    from rag_engine.retrieval.evidence_selection import _span_origin

    from app.clients.oracle import OracleClient
    from app.rag.answer_engine import _stored_child
    from app.rag.vision import _Layout, _Target, _write_back_adapter
    from tests.test_vision_producer_bindings import record
    from tests.test_vision_text_projection import stored_document

    raw = "原生prefixに検索語という表示があります。\n位置不明OCR\n原生suffix"
    source = record("Picture")
    extraction = _write_back_adapter(
        StructuredExtraction(
            raw_text=raw,
            elements=[DocumentElement(kind="figure", element_id="image-a", text="位置不明OCR")],
        ),
        _Layout(records=[source], targets=[_Target(source, element_index=0)]),
    )
    chunks = chunk_extraction_with_strategy(
        extraction, strategy="fixed_size", chunk_size=200, overlap=0
    )
    rows = OracleClient._chunk_insert_rows(
        "document-a", stored_document(), chunks, [None] * len(chunks), "set-a"
    )
    hit = OracleClient._retrieved_chunks_from_insert_rows("document-a", stored_document(), rows)[0]
    answer = _stored_chunk_answer_record(_stored_child(hit, rrf_score=1.0))
    assert "検索語" in answer.text and "索引専用甲" not in answer.text
    assert _span_origin(answer, 0, len("原生prefix"), []) == "unclassified"
