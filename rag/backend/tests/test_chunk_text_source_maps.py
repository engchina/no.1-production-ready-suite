"""同一の合成レコードで本文不変・画像部位の位置・保存版を検証する。"""

import copy
import json
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest
from rag_engine.chunking import builder, records
from rag_engine.chunking.constants import SOURCE_TABLE_CROP_RELATIONSHIP, DocumentChunk
from rag_engine.chunking.metadata import _project_chunk_metadata

from app.rag.chunking_small_to_big import _backend_chunk

FIXTURES = json.loads((Path(__file__).parent / "fixtures/image_text_source_maps.json").read_text())


def child_chunk(row: dict[str, Any]) -> DocumentChunk:
    return builder._child_chunk(
        1,
        row["records"],
        source_run_id="version-a",
        source_file_name="synthetic.pdf",
        source_engine_id="docling",
        source_engine_label="Docling",
        metadata_by_page={},
        section_path=(),
        atomic=True,
    )


@pytest.mark.parametrize("row", FIXTURES, ids=lambda row: row["case"])
def test_source_map_same_frozen_producer_bytes_and_roundtrip(row: dict[str, Any]) -> None:
    original = copy.deepcopy(row["records"])
    child = child_chunk(row)
    assert child.text.encode() == row["expected_text"].encode()
    assert child.retrieval_text.encode() == row["expected_retrieval_text"].encode()
    assert records._join_records_text(row["records"]) == row["expected_record_text"]
    assert (
        records._join_records_text(row["records"], for_retrieval=True)
        == row["expected_retrieval_record_text"]
    )
    assert child.metadata["layout"]["native_text_ranges"] == row["expected_native_text_ranges"]
    assert row["records"] == original
    source_map = child.metadata["layout"]["text_source_map"]
    assert source_map["version"] == 1
    assert source_map["source_run_id"] == child.source_run_id
    assert source_map["source_engine_id"] == child.source_engine_id
    for item in source_map["ranges"]:
        assert 0 <= item["start"] < item["end"] <= len(child.text)
        if not item["role"].startswith("image_"):
            assert "vision_image_purpose" not in item
    child.metadata = _project_chunk_metadata(child.metadata)
    restored = builder._chunk_from_payload(
        json.loads(json.dumps(child.to_dict(), ensure_ascii=False))
    )
    assert restored.metadata["layout"]["text_source_map"] == source_map
    assert restored.text == child.text
    backend = _backend_chunk(restored, 0, None, {})
    assert (
        json.loads(str(backend.metadata["engine_metadata_json"]))["layout"]["text_source_map"]
        == source_map
    )
    duplicate = replace(child, chunk_id="repeated-child", chunk_seq=2)
    parent = builder._parent_chunk(
        "parent",
        1,
        [child, duplicate],
        source_run_id="version-a",
        source_file_name="synthetic.pdf",
        source_engine_id="docling",
        source_engine_label="Docling",
    )
    assert parent.text.encode() == (child.text + "\n\n" + child.text).encode()
    parent_map = parent.metadata["layout"]["text_source_map"]
    n = len(source_map["ranges"])
    assert len(parent_map["ranges"]) == 2 * n
    for first, second in zip(parent_map["ranges"][:n], parent_map["ranges"][n:], strict=True):
        assert second["start"] == first["start"] + len(child.text) + 2
        assert second["end"] == first["end"] + len(child.text) + 2
        assert first["child_chunk_id"] == child.chunk_id
        assert second["child_chunk_id"] == duplicate.chunk_id
    parent.metadata = _project_chunk_metadata(parent.metadata)
    assert (
        builder._chunk_from_payload(json.loads(json.dumps(parent.to_dict()))).metadata
        == parent.metadata
    )


def test_duplicate_text_keeps_own_purpose_and_native_unclassified() -> None:
    source_map = child_chunk(FIXTURES[0]).metadata["layout"]["text_source_map"]
    rows = source_map["ranges"]
    assert [item.get("vision_image_purpose") for item in rows] == [
        None,
        "record",
        "example",
        "unknown",
    ]
    assert rows[0]["role"] == "unclassified"
    assert len({item["start"] for item in rows}) == 4


@pytest.mark.parametrize(
    "field,value",
    [("source_run_id", "other"), ("source_file_name", "other.pdf"), ("source_engine_id", "other")],
)
def test_parent_does_not_copy_another_source(field: str, value: str) -> None:
    child = child_chunk(FIXTURES[0])
    changes: dict[str, Any] = {field: value}
    mismatched = replace(child, **changes)
    parent = builder._parent_chunk(
        "parent",
        1,
        [mismatched],
        source_run_id="version-a",
        source_file_name="synthetic.pdf",
        source_engine_id="docling",
        source_engine_label="Docling",
    )
    source_map = parent.metadata["layout"]["text_source_map"]
    assert source_map["ranges"] == []
    assert source_map["unknown_sources"] == [child.chunk_id]
    assert parent.text == child.text


def test_old_chunk_has_no_invented_source_map() -> None:
    child = child_chunk(FIXTURES[0])
    child.metadata["layout"].pop("text_source_map", None)
    child.metadata = _project_chunk_metadata(child.metadata)
    assert "text_source_map" not in builder._chunk_from_payload(child.to_dict()).metadata["layout"]


def test_enhanced_html_and_preview_do_not_gain_purpose() -> None:
    enhanced = child_chunk(next(row for row in FIXTURES if row["case"] == "enhanced-html"))
    rows = enhanced.metadata["layout"]["text_source_map"]["ranges"]
    assert rows[0]["role"] == "unclassified"
    assert "vision_image_purpose" not in rows[0]
    preview = child_chunk(next(row for row in FIXTURES if row["case"] == "preview-only"))
    assert all(
        item["vision_image_purpose"] == "unknown"
        for item in preview.metadata["layout"]["text_source_map"]["ranges"]
        if item["role"] == "image_description"
    )


@pytest.mark.parametrize(
    "mutation",
    [
        "hash",
        "bounds",
        "boolean_offset",
        "purpose",
        "role",
        "purpose_type",
        "role_type",
        "source",
        "image",
        "unexpected_field",
    ],
)
def test_damaged_map_is_not_transferred_or_loaded(mutation: str) -> None:
    child = child_chunk(FIXTURES[0])
    value = child.metadata["layout"]["text_source_map"]
    image_row = value["ranges"][1]
    if mutation == "hash":
        value["text_sha256"] = "0" * 64
    elif mutation == "bounds":
        image_row["end"] = len(child.text) + 1
    elif mutation == "boolean_offset":
        image_row["start"] = True
    elif mutation == "purpose":
        image_row["vision_image_purpose"] = "unexpected"
    elif mutation == "role":
        image_row["role"] = "unexpected"
    elif mutation == "purpose_type":
        image_row["vision_image_purpose"] = []
    elif mutation == "role_type":
        image_row["role"] = {}
    elif mutation == "source":
        value["source_run_id"] = "other"
    elif mutation == "image":
        image_row["image_id"] = ""
    else:
        image_row["unexpected"] = "ignored-if-not-validated"
    parent = builder._parent_chunk(
        "parent",
        1,
        [child],
        source_run_id="version-a",
        source_file_name="synthetic.pdf",
        source_engine_id="docling",
        source_engine_label="Docling",
    )
    assert parent.metadata["layout"]["text_source_map"]["ranges"] == []
    assert parent.metadata["layout"]["text_source_map"]["unknown_sources"] == [child.chunk_id]
    with pytest.raises(ValueError):
        child.metadata = _project_chunk_metadata(child.metadata)
        builder._chunk_from_payload(child.to_dict())


def test_unverified_ocr_and_form_text_are_unclassified() -> None:
    for item in [
        {
            "id": "unlinked-ocr",
            "category": "Text",
            "text": "合成OCR",
            "raw_type": "picture_ocr_text",
            "raw": {"vision_image_purpose": "record"},
        },
        {
            "id": "form",
            "category": "Form",
            "text": "合成フォーム",
            "raw": {"vision_image_purpose": "example"},
        },
        {
            "id": "text",
            "category": "Text",
            "text": "合成全文OCR",
            "raw": {"vision_image_purpose": "record"},
        },
    ]:
        projection = records.record_text_projection(item)
        assert projection.ranges[0]["role"] == "unclassified"
        assert "vision_image_purpose" not in projection.ranges[0]


@pytest.mark.parametrize(
    "same_id,second_purpose", [(False, "example"), (False, "record"), (True, "record")]
)
def test_ambiguous_table_crops_do_not_attribute_supplement_to_first(
    same_id: bool, second_purpose: str
) -> None:
    row = copy.deepcopy(next(item for item in FIXTURES if item["case"] == "table-mixed"))
    table = row["records"][0]
    raw = table["raw"]
    crops = [
        item
        for item in raw["table_visual_evidence"]
        if item["relationship"] == SOURCE_TABLE_CROP_RELATIONSHIP
    ]
    assert len(crops) == 1
    another = copy.deepcopy(crops[0])
    if not same_id:
        another["image_id"] = "another-table-crop"
        another["record_id"] = "another-table-crop"
    another["vision_image_purpose"] = second_purpose
    raw["table_visual_evidence"].append(another)
    projection = records.record_text_projection(table)
    supplement = raw["table_vision_text"]
    rows = [
        item
        for item in projection.ranges
        if projection.text[item["start"] : item["end"]] == supplement
    ]
    assert len(rows) == 1
    assert rows[0]["role"] == "unclassified"
    assert "vision_image_purpose" not in rows[0]
    assert "image_id" not in rows[0]
    # cropを増やしても説明/OCRが無い自身cropの本文は増えず、元本文を保つ。
    assert projection.text == row["expected_record_text"]
