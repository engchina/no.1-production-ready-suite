"""保存済み画像用途を回答入力の投影で失わず、未記録を補完しない。"""

from copy import deepcopy
from pathlib import Path
from typing import Any

import pytest

from rag_engine.generation.answer_images import _answer_image_metadata, answer_image_evidence
from rag_engine.generation.answer_models import AnswerRecord
from rag_engine.retrieval.evidence_selection import evidence_spans

PURPOSE_CASES = [
    ({"vision_image_purpose": "record"}, "record"),
    ({"vision_image_purpose": "example"}, "example"),
    ({"vision_image_purpose": "unknown"}, "unknown"),
    ({}, "unknown"),
    ({"vision_image_purpose": None}, "unknown"),
    ({"vision_image_purpose": "invalid"}, "unknown"),
]
TEXT = "合成画像 A の表示値は 123 です。"


def _record(metadata: dict[str, Any]) -> AnswerRecord:
    return AnswerRecord(
        id="synthetic-record",
        engine="docling",
        engine_label="Docling",
        page=1,
        seq_no=1,
        category="Chunk",
        text=TEXT,
        source="synthetic.png",
        source_run_id="synthetic-version",
        metadata=metadata,
    )


@pytest.mark.parametrize(("saved", "expected"), PURPOSE_CASES)
def test_image_selection_retains_saved_purpose(saved: dict[str, Any], expected: str, tmp_path: Path) -> None:
    image = {"image_id": "image-a", "raw_type": "picture", **saved}
    before = deepcopy(image)
    [selected] = answer_image_evidence([_record({"image_evidence": [image]})], tmp_path)
    assert selected["vision_image_purpose"] == expected
    assert image == before


@pytest.mark.parametrize(("saved", "expected"), PURPOSE_CASES)
def test_prompt_image_metadata_retains_saved_purpose(saved: dict[str, Any], expected: str) -> None:
    image = {"image_id": "image-a", **saved}
    before = deepcopy(image)
    [projected] = _answer_image_metadata([], image_evidence=[image])["prompt_images"]
    assert projected["vision_image_purpose"] == expected
    assert image == before


@pytest.mark.parametrize(("saved", "expected"), PURPOSE_CASES)
def test_evidence_span_retains_only_its_saved_purpose(saved: dict[str, Any], expected: str) -> None:
    metadata = {**saved, "layout": {"native_text_ranges": [{"start": 0, "end": len(TEXT)}]}}
    before = deepcopy(metadata)
    [span] = evidence_spans("合成画像 A の表示値", [_record(metadata)], preserve_records=True)
    assert span["vision_image_purpose"] == expected
    assert span["origin"] == "document_text"
    assert span["value_context"] == "reference_document"
    assert span["text"] == TEXT
    assert metadata == before


def test_span_does_not_take_purpose_from_first_image_or_other_record() -> None:
    mixed = _record({"image_evidence": [{"vision_image_purpose": "record"}, {"vision_image_purpose": "example"}]})
    [span] = evidence_spans("合成画像 A の表示値", [mixed], preserve_records=True)
    assert span["vision_image_purpose"] == "unknown"


def test_mixed_images_keep_individual_purpose_through_both_projections(tmp_path: Path) -> None:
    images = [
        {"image_id": "image-record", "vision_image_purpose": "record"},
        {"image_id": "image-example", "vision_image_purpose": "example"},
        {"image_id": "image-old"},
    ]
    metadata = {"vision_image_purpose": "record", "image_evidence": images}
    before = deepcopy(metadata)
    selected = answer_image_evidence([_record(metadata)], tmp_path)
    prompt_images = _answer_image_metadata([], image_evidence=selected)["prompt_images"]
    expected = {"image-record": "record", "image-example": "example", "image-old": "unknown"}
    assert {image["image_id"]: image["vision_image_purpose"] for image in selected} == expected
    assert {image["image_id"]: image["vision_image_purpose"] for image in prompt_images} == expected
    assert metadata == before
