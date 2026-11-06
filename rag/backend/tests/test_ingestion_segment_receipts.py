"""受理済み段の版束縛を再利用時も維持する。"""

from types import SimpleNamespace
from typing import Any, cast

import pytest

from app.rag.ingestion import IngestionPipeline
from app.rag.ingestion_segment_receipts import (
    SEGMENT_RECEIPT_KEY,
    accepted_segment_receipt,
    read_segment_receipt,
)
from app.schemas.document import IngestionSegment
from app.schemas.extraction import StructuredExtraction


def segment(**changes: Any) -> IngestionSegment:
    return IngestionSegment(
        segment_id="s1",
        document_id="d1",
        recipe_id="r1",
        status="SUCCEEDED",
        page_start=1,
        page_end=2,
        artifact_path="local://s1",
    ).model_copy(update=changes)


async def test_actual_producer_records_accepted_reused_segment_receipt() -> None:
    pipeline: Any = object.__new__(IngestionPipeline)
    pipeline._recipe_id = "r1"
    from app.config import Settings

    pipeline._settings = Settings.model_construct()
    row = segment()
    extraction = StructuredExtraction(raw_text="合成の本文", elements=[])

    async def cached(*args: Any, **kwargs: Any) -> Any:
        assert kwargs["parse_keys"] == {(1, 2): "a" * 64}
        return SimpleNamespace(
            cached=[SimpleNamespace(segment=row, extraction=extraction)], missing_ranges=[]
        )

    async def latest(rows: Any) -> Any:
        return rows

    pipeline._load_cached_segment_extractions = cached
    pipeline._segments_with_latest_status = latest
    pipeline._segment_parse_key = lambda value: "a" * 64
    result, rows = await pipeline._parse_pdf_segments(
        trace_id="t1",
        document_id="d1",
        parser_backend="docling",
        pdf_segments=[SimpleNamespace(page_start=1, page_end=2, content=b"pdf")],
        checkpoint_segments=[row],
        source_profile=None,
        content_type="application/pdf",
        attributes={},
        progress=None,
        cancel_checker=None,
    )
    receipt = read_segment_receipt(result.extraction.to_document_payload(), "d1", "r1")
    assert receipt is not None
    assert receipt.segments[0].parse_key == "a" * 64
    assert receipt.segments[0].segment == row
    assert rows == [row]


@pytest.mark.parametrize("scope", ["wrong-document", "wrong-recipe", "missing", "duplicate"])
def test_old_or_invalid_receipt_is_not_filled(scope: str) -> None:
    raw = accepted_segment_receipt("d1", "r1", [segment()], {(1, 2): "a" * 64})
    if scope == "wrong-document":
        raw["document_id"] = "other"
    if scope == "wrong-recipe":
        raw["recipe_id"] = "other"
    if scope == "missing":
        raw = {}
    if scope == "duplicate":
        entries = raw["segments"]
        assert isinstance(entries, list)
        raw["segments"] = entries * 2
    assert (
        read_segment_receipt({"parser_artifacts": {SEGMENT_RECEIPT_KEY: raw}}, "d1", "r1") is None
    )


@pytest.mark.parametrize("matches", [False, True])
async def test_staging_checks_receipt_parse_key_against_actual_artifact(matches: bool) -> None:
    import json

    from app.rag.file_processing_staging import _verify_artifact_integrity

    row = segment()
    receipt = accepted_segment_receipt("d1", "r1", [row], {(1, 2): "current-key"})
    extraction = {"parser_artifacts": {SEGMENT_RECEIPT_KEY: receipt}}
    payload = {
        "parser_artifacts": {
            "extraction_artifact_schema_version": 1,
            "extraction_artifact_kind": "segment",
            "extraction_artifact_document_id": "d1",
            "extraction_artifact_segment_id": "s1",
            "extraction_artifact_page_start": 1,
            "extraction_artifact_page_end": 2,
            "extraction_artifact_parse_key": "current-key" if matches else "old-key",
        }
    }

    class Storage:
        async def get(self, path: str) -> bytes:
            return json.dumps(payload).encode()

    report = await _verify_artifact_integrity(
        storage=cast(Any, Storage()), document_id="d1", extraction=extraction, segments=[row]
    )
    assert report["artifact_segment_identity_verified_count"] == int(matches)
    assert report["artifact_integrity_error_count"] == int(not matches)


async def test_reader_uses_receipt_and_never_current_same_recipe_segments() -> None:
    from app.rag.staging_recipes import read_staging_recipe

    row = segment()
    receipt = accepted_segment_receipt("d1", "r1", [row], {(1, 2): "accepted-key"})

    class Oracle:
        async def ensure_default_document_recipe(self, *args: Any) -> Any:
            return {"recipe_id": "r1"}

        async def get_document_recipe(self, *args: Any) -> Any:
            return {"recipe_id": "r1", "status": "INDEXED", "active_chunk_set_id": "cs1"}

        async def get_chunk_set(self, *args: Any) -> Any:
            return {
                "document_id": "d1",
                "recipe_id": "r1",
                "status": "INDEXED",
                "extraction_recipe_id": "e1",
            }

        async def list_chunk_set_chunks(self, *args: Any) -> Any:
            return []

        async def get_document_extraction_artifact(self, **kwargs: Any) -> Any:
            return {"extraction_json": {"parser_artifacts": {SEGMENT_RECEIPT_KEY: receipt}}}

        async def list_ingestion_segments(self, *args: Any) -> Any:
            raise AssertionError("可変のcheckpointを読んだ")

    state = await read_staging_recipe(Oracle(), "d1")
    assert state.segments == (row,)


@pytest.mark.parametrize("condition", ["missing", "empty", "path_missing"])
async def test_strict_segment_verification_never_completes_unknown(condition: str) -> None:
    from app.rag.file_processing_staging import _verify_artifact_integrity

    row = segment(artifact_path=None) if condition == "path_missing" else segment()
    raw = accepted_segment_receipt("d1", "r1", [row], {(1, 2): "key"})
    if condition == "empty":
        raw["segments"] = []
    extraction = {"parser_artifacts": {SEGMENT_RECEIPT_KEY: raw}} if condition != "missing" else {}

    class Storage:
        async def get(self, path: str) -> bytes:
            raise KeyError(path)

    report = await _verify_artifact_integrity(
        storage=cast(Any, Storage()),
        document_id="d1",
        extraction=extraction,
        segments=[] if condition in {"missing", "empty"} else [row],
    )
    errors = report["artifact_integrity_error_count"]
    assert isinstance(errors, int)
    assert errors > 0
    assert report["artifact_segment_identity_verified_count"] == 0


async def test_checkpoint_write_failure_does_not_stop_accepted_normal_parse(
    monkeypatch: Any,
) -> None:
    from tests import test_ingestion_pdf_segments as fixtures
    from tests.test_ingestion_strategy import FakeObjectStorage, FakeOracle

    class UnavailableCheckpoint(FakeOracle):
        async def update_ingestion_segment(self, *args: Any, **kwargs: Any) -> Any:
            raise RuntimeError("synthetic_checkpoint_unavailable")

    oracle = UnavailableCheckpoint()
    storage = FakeObjectStorage()
    runner = fixtures._Runner()
    fixtures._install(monkeypatch, runner)
    pipeline = fixtures._pipeline(oracle, storage, rag_pdf_max_pages_per_segment=1)
    result = await fixtures._ingest(pipeline, fixtures._pdf(2))
    assert result is not None
    assert len(runner.calls) == 2
