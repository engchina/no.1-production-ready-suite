"""実producerの画像OCR・説明・表補足の境界を固定する。"""

import json

import pytest
from rag_engine.models.layout import LayoutRecord
from rag_parser_core.extraction import DocumentElement, StructuredExtraction
from rag_parser_core.vision_text import VisionTextProjection, text_sha256

from app.rag.vision import _Layout, _write_back_docling


def record(category: str) -> LayoutRecord:
    projection = VisionTextProjection("図の説明です。", "図の説明です。\n索引専用甲")
    return LayoutRecord(
        id="image-a",
        engine="docling",
        page=1,
        seq_no=1,
        bbox=[0, 0, 10, 10],
        coord_system="image_top_left",
        page_width=100,
        page_height=100,
        category=category,
        text=projection.answer_description,
        raw={
            "vision_status": "succeeded",
            "vision_text_projection": projection.dumps(),
            "table_vision_text": projection.answer_description if category == "Table" else "",
        },
    )


@pytest.mark.parametrize(
    "body,category,role",
    [
        ("画像内OCRをそのまま保持", "Picture", "image_ocr"),
        ("", "Picture", "image_description"),
        ("<table><tr><td>原生値123</td></tr></table>", "Table", "image_table_supplement"),
    ],
)
def test_actual_docling_producer_binds_own_source_without_replacing_native(
    body: str, category: str, role: str
) -> None:
    source = record(category)
    elements = (
        [
            DocumentElement(
                kind="table" if category == "Table" else "figure", element_id="image-a", text=body
            )
        ]
        if body
        else []
    )
    extraction = StructuredExtraction(
        raw_text=body if category != "Table" else "", elements=elements
    )
    actual = _write_back_docling(extraction, _Layout(records=[source]))
    element = actual.elements[0]
    assert element.text == (body or source.text)
    binding = json.loads(str(element.metadata["vision_text_binding"]))
    assert binding["element_id"] == source.id
    assert binding["body_sha256"] == text_sha256(element.text)
    assert binding["role"] == role
    assert binding["record_id"] == source.id
    assert "索引専用甲" not in element.text
    if role == "image_table_supplement":
        assert binding["ranges"] == []
        assert binding["supplement_sha256"] == text_sha256(source.text)
    else:
        assert binding["ranges"] == [{"start": 0, "end": len(element.text)}]


@pytest.mark.parametrize("category", ["Picture", "Table"])
def test_actual_adapter_records_only_its_appended_image_range(category: str) -> None:
    from rag_parser_core.vision_text_map import projected_element_text

    from app.rag.vision import _Target, _write_back_adapter

    source = record(category)
    original = "<table><tr><td>原生値123</td></tr></table>" if category == "Table" else "既存OCR"
    extraction = StructuredExtraction(
        raw_text=original,
        elements=[
            DocumentElement(
                kind="table" if category == "Table" else "figure",
                element_id="image-a",
                text=original,
            )
        ],
    )
    updated = _write_back_adapter(
        extraction, _Layout(records=[source], targets=[_Target(source, element_index=0)])
    )
    element = updated.elements[0]
    projected = projected_element_text(element.element_id or "", element.text, element.metadata)
    assert projected.ranges
    assert "索引専用甲" not in projected.text
    assert all(
        "原生値123" not in projected.text[item.start : item.end] for item in projected.ranges
    )
    if category == "Table":
        assert element.text.startswith(original)
        assert projected.ranges[0].start == len(original) + 1


@pytest.mark.parametrize("category", ["Picture", "Table"])
def test_new_adapter_does_not_replace_ambiguous_native_occurrences(category: str) -> None:
    from rag_parser_core.vision_text_map import (
        VISION_CHUNK_MAP_KEY,
        VISION_RAW_PROJECTION_KEY,
        chunk_projected_text,
    )

    from app.rag.vision import _Target, _write_back_adapter

    source = record(category)
    original = "<table><tr><td>同じ原生値</td></tr></table>" if category == "Table" else "同じOCR"
    raw = "原生prefix:" + original + "\n画像側:" + original + "\n原生suffix:" + original
    extraction = StructuredExtraction(
        raw_text=raw,
        elements=[
            DocumentElement(
                kind="table" if category == "Table" else "figure",
                element_id="image-a",
                text=original,
            )
        ],
    )
    updated = _write_back_adapter(
        extraction, _Layout(records=[source], targets=[_Target(source, element_index=0)])
    )
    assert updated.raw_text.startswith(raw + "\n\n")
    assert updated.raw_text.count(original) == 3
    mapped = chunk_projected_text(
        updated.raw_text,
        {VISION_CHUNK_MAP_KEY: updated.parser_artifacts[VISION_RAW_PROJECTION_KEY]},
    )
    assert mapped.ranges
    assert all(item.start >= len(raw) for item in mapped.ranges)
    assert all(
        "同じ原生値" not in mapped.text[item.start : item.end]
        and "同じOCR" not in mapped.text[item.start : item.end]
        for item in mapped.ranges
    )


def test_valid_new_image_cannot_certify_opaque_old_vlm_helper_text() -> None:
    from app.rag.vision import _Target, _write_back_adapter

    source = record("Picture")
    old = "旧生成の索引専用情報です。"
    extraction = StructuredExtraction(
        raw_text=old + "\n新しい画像OCR",
        elements=[
            DocumentElement(
                kind="figure",
                element_id="old-image",
                text=old,
                metadata={
                    "figure_text_source": "vision",
                    "vision_text_projection_status": "unrecorded",
                },
            ),
            DocumentElement(kind="figure", element_id="image-a", text="新しい画像OCR"),
        ],
    )
    with pytest.raises(ValueError, match="再解析"):
        _write_back_adapter(
            extraction, _Layout(records=[source], targets=[_Target(source, element_index=1)])
        )


@pytest.mark.parametrize("field", ["record_id", "element_body_sha256"])
def test_saved_raw_map_cannot_change_its_formal_source_identity(field: str) -> None:
    from rag_parser_core.vision_text_map import VISION_RAW_PROJECTION_KEY
    from rag_pipeline_core.chunking import _extraction_vision_projection

    source = record("Picture")
    extraction = StructuredExtraction(
        raw_text="画像OCR",
        elements=[DocumentElement(kind="figure", element_id="image-a", text="画像OCR")],
    )
    actual = _write_back_docling(extraction, _Layout(records=[source]))
    artifact = actual.parser_artifacts[VISION_RAW_PROJECTION_KEY]
    assert isinstance(artifact, str)
    payload = json.loads(artifact)
    payload["sources"][0][field] = "other-record" if field == "record_id" else "0" * 64
    altered = actual.model_copy(
        update={
            "parser_artifacts": {
                **actual.parser_artifacts,
                VISION_RAW_PROJECTION_KEY: json.dumps(payload),
            }
        }
    )
    with pytest.raises(ValueError, match="元要素の版"):
        _extraction_vision_projection(altered)


def test_docling_resume_keeps_unknown_ocr_separate_without_duplicate_description() -> None:
    source = record("Picture")
    raw = "原生prefix:同じOCR\n画像側:同じOCR\n原生suffix:同じOCR"
    extraction = StructuredExtraction(
        raw_text=raw,
        elements=[DocumentElement(kind="figure", element_id="image-a", text="同じOCR")],
    )
    first = _write_back_docling(extraction, _Layout(records=[source]))
    second = _write_back_docling(first, _Layout(records=[source]))
    assert first.raw_text == second.raw_text
    assert second.raw_text.startswith(raw)
    assert second.raw_text.count("図の説明です。") == 1
