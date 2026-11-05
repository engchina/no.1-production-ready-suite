"""画像用途の継承・解析版・キャッシュ境界を合成データで検証する。"""

import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from pydantic import ValidationError
from rag_engine.parsing.vision_checkpoints import VisionCheckpoints
from rag_parser_core.image_purpose import recorded_image_purpose

from app.config import Settings, get_settings
from app.rag.kb_adapter_config import (
    KnowledgeBaseAdapterConfig,
    KnowledgeBaseIngestionConfig,
    resolve_effective_settings,
)
from app.rag.variant_keys import compute_extraction_recipe_id
from app.rag.vision import stamp_image_purpose
from app.schemas.extraction import DocumentElement, ExtractionAsset, StructuredExtraction
from app.schemas.settings import ParserAdapterSettingsUpdate


@pytest.mark.parametrize(
    "global_purpose,override,expected",
    [
        ("record", None, "record"),
        ("record", "record", "record"),
        ("record", "example", "example"),
        ("example", None, "example"),
        ("example", "record", "record"),
        ("example", "example", "example"),
    ],
)
def test_image_purpose_recipe_priority(global_purpose, override, expected):
    global_settings = get_settings().model_copy(update={"rag_vision_image_purpose": global_purpose})
    recipe = KnowledgeBaseAdapterConfig(
        ingestion=KnowledgeBaseIngestionConfig(vision_image_purpose=override)
    )
    resolved = resolve_effective_settings(global_settings, recipe, scope="ingestion")
    assert resolved.rag_vision_image_purpose == expected
    assert global_settings.rag_vision_image_purpose == global_purpose


@pytest.mark.parametrize("value", ["unknown", "invalid", None, True, 0])
def test_global_image_purpose_is_strict(value):
    with pytest.raises(ValidationError):
        Settings(rag_vision_image_purpose=value)


@pytest.mark.parametrize("value", ["unknown", "invalid", True, 0])
def test_recipe_image_purpose_is_strict(value):
    with pytest.raises(ValidationError):
        KnowledgeBaseIngestionConfig(vision_image_purpose=value)


def test_new_default_and_global_update_null():
    assert Settings.model_fields["rag_vision_image_purpose"].default == "record"
    assert KnowledgeBaseIngestionConfig().vision_image_purpose is None
    assert ParserAdapterSettingsUpdate().vision_image_purpose is None
    assert ParserAdapterSettingsUpdate(vision_image_purpose=None).vision_image_purpose is None


@pytest.mark.parametrize(
    "metadata,expected",
    [
        ({}, "unknown"),
        ({"vision_image_purpose": None}, "unknown"),
        ({"vision_image_purpose": "invalid"}, "unknown"),
        ({"vision_image_purpose": "record"}, "record"),
        ({"vision_image_purpose": "example"}, "example"),
    ],
)
def test_historical_purpose_reader_does_not_rewrite(metadata, expected):
    before = json.dumps(metadata)
    assert recorded_image_purpose(metadata) == expected
    assert json.dumps(metadata) == before


def test_new_parse_snapshot_is_not_reinterpreted_by_global_changes():
    settings = get_settings().model_copy(update={"rag_vision_image_purpose": "record"})
    extraction = stamp_image_purpose(StructuredExtraction(), settings.rag_vision_image_purpose)
    changed = settings.model_copy(update={"rag_vision_image_purpose": "example"})
    assert recorded_image_purpose(extraction.parser_artifacts) == "record"
    next_extraction = stamp_image_purpose(StructuredExtraction(), changed.rag_vision_image_purpose)
    assert recorded_image_purpose(next_extraction.parser_artifacts) == "example"


def test_effective_purpose_separates_extraction_and_checkpoint(tmp_path: Path):
    image = tmp_path / "image"
    image.write_bytes(b"synthetic-image-source")
    provider = SimpleNamespace(
        provider_id="test", model="test", region="test", base_url="test", project_id="test"
    )
    extraction_keys = []
    checkpoint_keys = []
    for purpose in ("record", "record", "example"):
        settings = get_settings().model_copy(update={"rag_vision_image_purpose": purpose})
        extraction_keys.append(compute_extraction_recipe_id("a" * 64, settings))
        checkpoint_keys.append(
            VisionCheckpoints(tmp_path, "a" * 64, image_purpose=purpose).key(
                crop=image,
                context=image,
                prompt="same",
                system_prompt="same",
                provider=provider,
                max_tokens=100,
                kind="picture",
                api_mode="chat_completions",
            )
        )
    assert extraction_keys[0] == extraction_keys[1] != extraction_keys[2]
    assert checkpoint_keys[0] == checkpoint_keys[1] != checkpoint_keys[2]


def test_trusted_image_purpose_overwrites_model_claim_without_mutation():
    extraction = StructuredExtraction(
        elements=[
            DocumentElement(
                element_id="figure-a",
                kind="figure",
                text="合成の画像説明",
                metadata={"vision_image_purpose": "example"},
            )
        ],
        assets=[
            ExtractionAsset(
                asset_id="image-a", kind="image", metadata={"vision_image_purpose": "example"}
            )
        ],
        parser_artifacts={
            "layout_records": {
                "records": [
                    {
                        "id": "layout-a",
                        "category": "Picture",
                        "raw": {"vision_image_purpose": "example"},
                    }
                ]
            }
        },
    )
    before = extraction.model_dump_json()
    result = stamp_image_purpose(extraction, "record")
    assert recorded_image_purpose(result.parser_artifacts) == "record"
    assert result.elements[0].metadata["vision_image_purpose"] == "record"
    assert result.assets[0].metadata["vision_image_purpose"] == "record"
    assert (
        result.parser_artifacts["layout_records"]["records"][0]["raw"]["vision_image_purpose"]
        == "record"
    )
    assert extraction.model_dump_json() == before


@pytest.mark.parametrize("purpose", ["record", "example"])
def test_image_purpose_reaches_plain_chunks_and_image_evidence(purpose):
    from rag_engine.chunking.evidence import _image_evidence, _source_record_ref
    from rag_pipeline_core.chunking import chunk_extraction

    extraction = stamp_image_purpose(
        StructuredExtraction(
            elements=[
                DocumentElement(
                    element_id="figure-a",
                    kind="figure",
                    text="合成の画像説明",
                    metadata={"vision_status": "succeeded"},
                )
            ]
        ),
        purpose,
    )
    chunks = chunk_extraction(extraction, chunk_size=100, overlap=0)
    assert chunks
    assert all(chunk.metadata["vision_image_purpose"] == purpose for chunk in chunks)
    record = {
        "id": "layout-a",
        "category": "Picture",
        "page": 1,
        "seq_no": 1,
        "raw_type": "picture",
        "raw": {"vision_image_purpose": purpose},
    }
    ref = _source_record_ref(record)
    assert ref["vision_image_purpose"] == purpose
    evidence = _image_evidence(
        [ref], source_run_id="synthetic-run", source_file_name="synthetic.png"
    )
    assert evidence[0]["vision_image_purpose"] == purpose


def test_old_image_evidence_is_unknown_without_modifying_source():
    from rag_engine.chunking.evidence import _image_evidence

    ref = {
        "record_id": "layout-a",
        "category": "Picture",
        "page": 1,
        "seq_no": 1,
        "raw_type": "picture",
    }
    before = json.dumps(ref)
    evidence = _image_evidence(
        [ref], source_run_id="synthetic-run", source_file_name="synthetic.png"
    )
    assert evidence[0]["vision_image_purpose"] == "unknown"
    assert json.dumps(ref) == before


async def test_actual_pipeline_freezes_purpose_at_start():
    from app.rag.ingestion import IngestionPipeline

    settings = get_settings().model_copy(
        update={"rag_vision_enabled": False, "rag_vision_image_purpose": "record"}
    )
    pipeline = IngestionPipeline(
        settings=settings,
        vlm=object(),
        genai=object(),
        oracle=object(),
        object_storage=object(),
        document_understanding=object(),
    )
    started_key = pipeline._segment_parse_key(b"synthetic-image-source")
    settings.rag_vision_image_purpose = "example"
    result = await pipeline._attach_vision(
        "synthetic-trace",
        StructuredExtraction(),
        source_bytes=b"",
        content_type="image/png",
        file_name="synthetic.png",
        parser_backend="unstructured",
        cancel_checker=None,
    )
    assert recorded_image_purpose(result.parser_artifacts) == "record"
    assert pipeline._segment_parse_key(b"synthetic-image-source") == started_key
    next_pipeline = IngestionPipeline(
        settings=settings,
        vlm=object(),
        genai=object(),
        oracle=object(),
        object_storage=object(),
        document_understanding=object(),
    )
    assert next_pipeline._settings.rag_vision_image_purpose == "example"
    assert next_pipeline._segment_parse_key(b"synthetic-image-source") != started_key


def test_image_source_ocr_inherits_purpose_but_native_document_text_does_not():
    extraction = StructuredExtraction(
        elements=[DocumentElement(element_id="text-a", kind="text", text="合成OCR本文")]
    )
    image = stamp_image_purpose(extraction, "example", source_is_image=True)
    document = stamp_image_purpose(extraction, "example", source_is_image=False)
    assert recorded_image_purpose(image.elements[0].metadata) == "example"
    assert recorded_image_purpose(document.elements[0].metadata) == "unknown"
    assert extraction.elements[0].metadata == {}


def test_recipe_snapshot_stores_effective_purpose_and_requires_reparse_for_drift():
    from app.api.routes.documents import (
        _merge_document_processing_config,
        _processing_config_drift_groups,
        _processing_recipe_snapshot,
    )
    from app.schemas.document import DocumentProcessingConfig

    global_settings = get_settings().model_copy(update={"rag_vision_image_purpose": "example"})
    recipe = DocumentProcessingConfig(vision_image_purpose=None)
    _, effective = _merge_document_processing_config(recipe, global_settings)
    snapshot = _processing_recipe_snapshot(recipe, effective)
    assert "vision_image_purpose" not in snapshot["processing_config"]
    assert snapshot["effective_processing_config"]["vision_image_purpose"] == "example"
    observed = effective.model_dump(mode="json")
    current = {**observed, "vision_image_purpose": "record"}
    assert _processing_config_drift_groups(observed, current) == ["parser_adapter_backend"]
    historical = {key: value for key, value in observed.items() if key != "vision_image_purpose"}
    assert _processing_config_drift_groups(historical, current) == ["parser_adapter_backend"]
    assert "vision_image_purpose" not in historical


@pytest.mark.parametrize("claimed_purpose", [None, "record"])
def test_native_table_is_not_reclassified_as_image_content(claimed_purpose):
    from rag_engine.chunking.evidence import _source_record_ref
    from rag_pipeline_core.chunking import chunk_extraction

    native = {
        "id": "table-a",
        "category": "Table",
        "page": 1,
        "seq_no": 1,
        "raw_type": "table",
        "raw": {},
    }
    if claimed_purpose is not None:
        native["raw"]["vision_image_purpose"] = claimed_purpose
    extraction = StructuredExtraction(
        elements=[
            DocumentElement(
                element_id="table-a", kind="table", text="| 項目 | 値 |\n| --- | --- |\n| A | 123 |"
            )
        ],
        parser_artifacts={"layout_records": {"records": [native]}},
    )
    result = stamp_image_purpose(extraction, "example")
    assert recorded_image_purpose(result.parser_artifacts) == "example"
    saved = result.parser_artifacts["layout_records"]["records"][0]
    assert "vision_image_purpose" not in saved["raw"]
    assert "vision_image_purpose" not in _source_record_ref(saved)
    assert "vision_image_purpose" not in result.elements[0].metadata
    chunks = chunk_extraction(result, chunk_size=200, overlap=0)
    assert any("123" in chunk.text for chunk in chunks)
    assert all("vision_image_purpose" not in chunk.metadata for chunk in chunks)


@pytest.mark.parametrize(
    "raw",
    [
        {"vision_status": "failed"},
        {"table_missing_picture_regions": [[1, 2, 3, 4]]},
        {"table_vision_text": "合成の画像補足"},
    ],
)
def test_table_with_actual_visual_supplement_retains_image_purpose(raw):
    extraction = StructuredExtraction(
        parser_artifacts={
            "layout_records": {"records": [{"id": "table-a", "category": "Table", "raw": raw}]}
        }
    )
    result = stamp_image_purpose(extraction, "example")
    assert (
        result.parser_artifacts["layout_records"]["records"][0]["raw"]["vision_image_purpose"]
        == "example"
    )
