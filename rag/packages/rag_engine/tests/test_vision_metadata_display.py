"""画像の内部分類と、人に読ませる説明本文を分離する。入力は合成画像の録画応答。"""

from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from rag_engine.parsing.content_labels import content_labels
from rag_engine.parsing.picture_descriptions import _visual_structure, vision_description_text

FIXTURE = Path(__file__).parent / "fixtures/vision_metadata_recorded_response.json"
KINDS = (
    "flowchart",
    "architecture_diagram",
    "chart",
    "table",
    "form",
    "screenshot",
    "photo",
    "logo",
    "watermark",
    "background",
    "decorative",
    "other",
)


@pytest.mark.parametrize("kind", KINDS)
def test_visual_classification_is_metadata_not_explanatory_text(kind: str) -> None:
    payload = json.loads(FIXTURE.read_text())
    payload["visual_kind"] = kind
    original = copy.deepcopy(payload)
    rendered = vision_description_text(payload)
    assert f"{content_labels().field('visual_kind')}:" not in rendered
    assert "試料Aの測定値=42" in rendered
    assert _visual_structure(payload, kind)["visual_kind"] == kind
    assert payload == original


def test_only_classification_cannot_become_picture_content() -> None:
    assert vision_description_text({"visual_kind": "other"}) == ""


def test_same_word_in_visible_content_is_preserved() -> None:
    rendered = vision_description_text(
        {"visual_kind": "other", "retrieval_text": "画像の識別子は other です。", "codes_and_errors": ["other"]}
    )
    assert "画像の識別子は other です。" in rendered
    assert "other" in rendered
    assert f"{content_labels().field('visual_kind')}:" not in rendered


@pytest.mark.parametrize("kind", ["select", "text", "checkbox", "radio", "date", "button", "unknown"])
def test_existing_form_kind_labels_remain_localized(kind: str) -> None:
    payload = {"form_fields": [{"name": "合成項目", "value": "42", "state": "選択済み", "kind": kind}]}
    rendered = vision_description_text(payload)
    label = content_labels().form_field_kind(kind)
    assert f"({label})" in rendered if label else "(unknown)" not in rendered
    assert "選択済み" in rendered


def test_cached_raw_response_is_reformatted_without_another_model_call(tmp_path: Path) -> None:
    from unittest.mock import patch

    from PIL import Image

    from rag_engine.config import get_settings
    from rag_engine.models.layout import LayoutRecord, PageImage
    from rag_engine.parsing import picture_descriptions as pictures

    image_path = tmp_path / "synthetic.png"
    Image.new("RGB", (200, 200), "white").save(image_path)
    page = PageImage(1, 200, 200, 200, 200, str(image_path))
    original = LayoutRecord("synthetic", "docling", 1, 1, [10, 10, 180, 180], "image_top_left", 200, 200, "Picture", raw_type="picture")
    payload = json.loads(FIXTURE.read_text())
    old_groups = tuple(
        (name, (*fields, "visual_kind")) if name == "summary" else (name, fields) for name, fields in pictures.VISION_SENTENCE_GROUPS
    )
    first = copy.deepcopy(original)
    with (
        patch.object(pictures, "VISION_SENTENCE_GROUPS", old_groups),
        patch.object(pictures, "describe_picture", return_value=copy.deepcopy(payload)) as model,
    ):
        first_stats = pictures.describe_docling_pictures(
            [first], [page], run_dir=tmp_path, pdf_name="synthetic.png", settings=get_settings(environ={}, dotenv_path=None)
        )
    assert first_stats.succeeded == 1 and model.call_count == 1
    assert "視覚種別: other" in first.text
    second = copy.deepcopy(original)
    with patch.object(pictures, "describe_picture", side_effect=AssertionError("raw checkpoint must be reused")) as model:
        second_stats = pictures.describe_docling_pictures(
            [second], [page], run_dir=tmp_path, pdf_name="synthetic.png", settings=get_settings(environ={}, dotenv_path=None)
        )
    model.assert_not_called()
    assert second_stats.succeeded == 1
    assert "視覚種別:" not in second.text
    assert second.raw["visual_kind"] == "other"
    assert second.raw["vision_description"] == payload
