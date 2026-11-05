"""画像用途を表・フォームの画像根拠への変換で失わない。"""

import pytest

from rag_engine.chunking import CHILD_CHUNK_LEVEL, ChunkingConfig, build_small_to_big_chunks
from rag_engine.chunking.evidence import _form_context, _form_image_evidence
from rag_engine.chunking.tables import _source_table_crop_evidence


def _record(seq, category, text, bbox, raw):
    return {
        "id": f"record-{seq}",
        "engine": "docling",
        "page": 1,
        "seq_no": seq,
        "category": category,
        "raw_type": "picture" if category == "Picture" else "table",
        "text": text,
        "bbox": bbox,
        "coord_system": "image_top_left",
        "page_width": 100,
        "page_height": 100,
        "raw": raw,
    }


@pytest.mark.parametrize("purpose", ["record", "example"])
def test_actual_small_to_big_contained_picture_retains_purpose(purpose):
    table = _record(1, "Table", "<table><tr><th>項目</th><th>値</th></tr><tr><td>A</td><td>123</td></tr></table>", [0, 0, 100, 100], {})
    image = _record(
        2,
        "Picture",
        "合成の画像補足",
        [20, 20, 70, 70],
        {"vision_image_purpose": purpose, "vision_crop": "image.png", "visual_role": "content"},
    )
    chunks = build_small_to_big_chunks(
        {
            "run_id": "synthetic",
            "pdf_name": "synthetic.pdf",
            "records": [table, image],
            "engines": [{"engine": "docling", "label": "Docling"}],
        },
        source_run_id="synthetic",
        selected_engine_ids=["docling"],
        config=ChunkingConfig(),
    )
    children = [c for c in chunks if c.chunk_level == CHILD_CHUNK_LEVEL]
    assert len(children) == 1
    assert "123" in children[0].text
    assert children[0].metadata["image_evidence"][0]["vision_image_purpose"] == purpose
    assert children[0].metadata["table_context"][0]["visual_evidence"][0]["vision_image_purpose"] == purpose
    assert "vision_image_purpose" not in children[0].source_record_refs[0]


@pytest.mark.parametrize("purpose", ["record", "example"])
def test_saved_table_crop_retains_own_purpose(purpose):
    [image] = _source_table_crop_evidence(
        _record(1, "Table", "native", [0, 0, 100, 100], {"crop_path": "table.png", "vision_image_purpose": purpose})
    )
    assert image["vision_image_purpose"] == purpose


@pytest.mark.parametrize("purpose", ["record", "example"])
def test_form_crop_retains_region_purpose(purpose):
    region = {
        "record_id": "region-a",
        "page": 1,
        "category": "Form",
        "raw_type": "form",
        "bbox": [0, 0, 100, 100],
        "crop_path": "form.png",
        "vision_image_purpose": purpose,
    }
    [image] = _form_image_evidence(
        [region], [{"page": 1, "bbox": [10, 10, 20, 20]}], source_run_id="synthetic", source_file_name="synthetic.pdf"
    )
    assert image["asset_kind"] == "crop"
    assert image["vision_image_purpose"] == purpose


@pytest.mark.parametrize("purpose", ["record", "example"])
def test_form_page_preserves_field_source_purpose(purpose):
    ref = {
        "record_id": "field-a",
        "page": 1,
        "category": "Text",
        "raw_type": "selection_element",
        "bbox": [10, 10, 20, 20],
        "form_field": {"key": "A", "selected": True},
        "vision_image_purpose": purpose,
    }
    fields = _form_context([ref])
    [image] = _form_image_evidence([ref], fields, source_run_id="synthetic", source_file_name="synthetic.pdf")
    assert image["asset_kind"] == "page"
    assert fields[0]["vision_image_purpose"] == purpose
    assert image["vision_image_purpose"] == purpose


def test_mixed_or_missing_form_sources_are_not_promoted_to_record():
    refs = [
        {"record_id": f"field-{i}", "page": 1, "form_field": {"key": str(i)}, "vision_image_purpose": purpose}
        for i, purpose in enumerate(["record", "example"])
    ]
    fields = _form_context(refs)
    [image] = _form_image_evidence(refs, fields, source_run_id="synthetic", source_file_name="synthetic.pdf")
    assert image["vision_image_purpose"] == "unknown"
    assert [f["vision_image_purpose"] for f in image["visual_structure"]["form_fields"]] == ["record", "example"]
    [old] = _form_image_evidence([{"page": 1}], [{"page": 1}], source_run_id="synthetic", source_file_name="synthetic.pdf")
    assert old["vision_image_purpose"] == "unknown"


@pytest.mark.parametrize("purpose", ["record", "example"])
def test_materialized_table_row_crop_keeps_caller_purpose(tmp_path, purpose):
    from PIL import Image

    from rag_engine.models.layout import LayoutRecord, PageImage
    from rag_engine.parsing.table_visual_rows import _save_row_crops

    source = tmp_path / "source.png"
    Image.new("RGB", (16, 16), "white").save(source)
    record = LayoutRecord(
        "table-a",
        "docling",
        1,
        1,
        [0, 0, 16, 16],
        "image_top_left",
        16,
        16,
        "Table",
        raw_type="table",
        raw={"vision_image_purpose": purpose},
    )
    page = PageImage(1, 16, 16, 16, 16, str(source))
    _save_row_crops(record, page, tmp_path, {1: {"text": "合成の画像補足", "image_regions": [[1, 1, 8, 8]]}})
    assert record.raw["table_row_image_evidence"][0]["vision_image_purpose"] == purpose
