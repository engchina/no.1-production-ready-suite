"""画像説明の本文形式が変わったとき、完成artifactを無条件に使い回さない。"""

from __future__ import annotations

import pytest
from rag_parser_core.language import current_pack

from app.config import Settings
from app.rag import variant_keys
from app.rag.ingestion import IngestionPipeline, _extraction_with_artifact_cache_metadata
from app.schemas.document import IngestionSegment
from app.schemas.extraction import StructuredExtraction


def legacy_key(settings: Settings) -> str:
    subset = variant_keys.extraction_recipe_subset(settings)
    subset.pop("vision_text_format_version", None)
    return variant_keys._digest(
        "er",
        {
            "v": variant_keys.KEY_VERSION,
            "src": "a" * 64,
            "language_pack_ingest_hash": current_pack().ingest_hash,
            **subset,
            "rag_parser_asr_enabled": True,
        },
    )


@pytest.mark.parametrize("enabled", [False, True])
@pytest.mark.parametrize("kind", ["full", "segment"])
async def test_real_artifact_loader_honors_format_version(enabled: bool, kind: str) -> None:
    settings = Settings.model_construct(rag_vision_enabled=enabled)
    old_key = legacy_key(settings)
    new_key = variant_keys.compute_extraction_recipe_id("a" * 64, settings)
    segment = IngestionSegment(
        segment_id="synthetic:p1",
        document_id="synthetic",
        status="SUCCEEDED",
        page_start=1,
        page_end=1,
        artifact_path="synthetic://old.json",
    )
    extraction = _extraction_with_artifact_cache_metadata(
        StructuredExtraction(raw_text="固定された画像説明本文"),
        artifact_kind=kind,
        document_id="synthetic",
        trace_id="synthetic",
        segment=segment if kind == "segment" else None,
        parse_key=old_key,
    )
    original = extraction.model_dump_json().encode()

    class Storage:
        async def get(self, path: str) -> bytes:
            assert path == "synthetic://old.json"
            return original

    pipeline = IngestionPipeline(object_storage=Storage(), settings=settings)  # type: ignore[arg-type]
    if kind == "full":
        assert await pipeline._load_cached_full_extraction([segment], parse_key=old_key) is not None
        restored = await pipeline._load_cached_full_extraction([segment], parse_key=new_key)
        assert (restored is None) is enabled
    else:
        positive = await pipeline._load_cached_segment_extractions(
            [segment], target_segments=[], parse_keys={(1, 1): old_key}
        )
        assert len(positive.cached) == 1
        restored_segments = await pipeline._load_cached_segment_extractions(
            [segment], target_segments=[], parse_keys={(1, 1): new_key}
        )
        assert len(restored_segments.cached) == (0 if enabled else 1)
        assert restored_segments.missing_ranges == ({(1, 1)} if enabled else set())
    assert original == extraction.model_dump_json().encode()
    assert (old_key != new_key) is enabled
