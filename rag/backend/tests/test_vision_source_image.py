"""画像原本の領域が文字だけでも読図へ到達し、既存の図を二重に読まない。"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import pytest
from PIL import Image

from app.rag.vision import read_figures_with_vision
from app.schemas.extraction import StructuredExtraction
from tests.test_vision import _pdf

FIXTURES = Path(__file__).with_name("fixtures")


class Describer:
    api_mode = "test"
    max_tokens = 1

    def __init__(self, *, fail: bool = False) -> None:
        self.calls: list[dict[str, Any]] = []
        self.fail = fail

    def provider(self) -> SimpleNamespace:
        return SimpleNamespace(
            provider_id="fake", model="fake", region="", base_url="", project_id=""
        )

    def describe(
        self,
        crop_path: Path,
        metadata: Mapping[str, Any],
        *,
        context_image_paths: Sequence[Path],
        target_kind: str,
        rendered_prompt: str,
    ) -> dict[str, Any]:
        with Image.open(crop_path) as im:
            self.calls.append(
                {"size": im.size, "kind": target_kind, "context_count": len(context_image_paths)}
            )
        if self.fail:
            raise RuntimeError("synthetic failure")
        return {
            "main_topic": "合成の接続図",
            "diagram_nodes": [
                {"id": "A", "kind": "node", "label": "A"},
                {"id": "B", "kind": "node", "label": "B"},
            ],
            "diagram_edges": [
                {"source": "A", "target": None, "label": "r", "direction": "uncertain"}
            ],
            "retrieval_text": "合成の図の説明",
        }


def extraction() -> StructuredExtraction:
    return StructuredExtraction.model_validate(
        json.loads((FIXTURES / "vision-source-text-only.json").read_text())
    )


def read(
    value: StructuredExtraction,
    *,
    backend: str = "docling",
    source: bytes | None = None,
    content_type: str = "image/png",
    describer: Describer | None = None,
) -> tuple[StructuredExtraction, Describer]:
    model = describer or Describer()
    result = read_figures_with_vision(
        value,
        source_bytes=source or (FIXTURES / "vision-source-text-only.png").read_bytes(),
        content_type=content_type,
        file_name="synthetic.png" if content_type.startswith("image/") else "synthetic.pdf",
        parser_backend=backend,
        describer=model,
        prompt_template="## Step 1: 対象の図を読む",
        image_purpose="record",
    )
    return result, model


@pytest.mark.parametrize("backend", ["docling", "unstructured"])
def test_text_only_source_image_is_read_once_and_keeps_original_text(backend: str) -> None:
    original = extraction()
    if backend != "docling":
        original.parser_artifacts = {}
    before = original.model_dump_json()
    result, model = read(original, backend=backend)
    assert len(model.calls) == 1
    assert model.calls[0]["size"] == (1000, 650)
    assert model.calls[0]["context_count"] == 1
    assert original.model_dump_json() == before
    for element in original.elements:
        assert any(
            e.element_id == element.element_id and e.text == element.text for e in result.elements
        )
    figures = [e for e in result.elements if e.kind == "figure"]
    assert len(figures) == 1
    assert figures[0].metadata["vision_status"] == "succeeded"
    assert "A ?" in figures[0].text and "…" not in figures[0].text
    assert result.assets[-1].metadata["vision_status"] == "succeeded"
    if backend == "docling":
        records = cast(dict[str, Any], result.parser_artifacts["layout_records"])["records"]
        assert (
            len([r for r in records if r["category"] == "Picture" and r["raw_type"] == "picture"])
            == 1
        )
        assert any(r["raw"].get("vision_description", {}).get("diagram_edges") for r in records)
    second, again = read(result, backend=backend)
    assert not again.calls
    assert second.raw_text == result.raw_text


def test_source_image_without_any_ocr_elements_is_still_read_once() -> None:
    result, model = read(StructuredExtraction(raw_text=""))
    assert len(model.calls) == 1
    assert len(result.elements) == 1 and result.elements[0].kind == "figure"


def test_text_only_pdf_does_not_gain_whole_page_vision() -> None:
    result, model = read(extraction(), source=_pdf(), content_type="application/pdf")
    assert not model.calls
    assert result.raw_text == extraction().raw_text


def test_source_image_with_an_existing_region_does_not_gain_an_extra_target() -> None:
    value = extraction()
    records = cast(dict[str, Any], value.parser_artifacts["layout_records"])["records"]
    records.append(
        {
            **records[0],
            "id": "synthetic-existing-picture",
            "seq_no": 4,
            "category": "Picture",
            "raw_type": "picture",
            "text": "",
            "bbox": [0, 0, 1000, 650],
            "raw": {},
        }
    )
    result, model = read(value)
    assert len(model.calls) == 1
    assert not any(a.kind == "source_image" for a in result.assets)


def test_failed_source_image_preserves_ocr_and_records_failure() -> None:
    result, model = read(extraction(), describer=Describer(fail=True))
    assert len(model.calls) == 1
    assert result.assets[-1].metadata["vision_status"] == "failed"
    assert result.raw_text == extraction().raw_text
