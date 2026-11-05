"""保存済み抽出の続行では、保存した品質報告を必須にする。"""

from datetime import UTC, datetime
from typing import Any, cast

import pytest

from app.rag import ingestion as module
from app.rag.ingestion_quality import build_ingestion_quality_report
from app.schemas.document import DocumentDetail, FileStatus
from app.schemas.extraction import DocumentElement, StructuredExtraction


class ContinuedPastQualityGate(Exception):
    """品質契約の次の境界で止め、実際の状態更新や索引作成は行わない。"""


class SavedExtractionOracle:
    def __init__(self, payload: dict[str, Any], recipe: bool) -> None:
        self.payload = payload
        self.recipe = recipe
        self.write_attempts: list[str] = []
        self.detail = DocumentDetail(
            id="synthetic-document",
            file_name="synthetic.txt",
            status=FileStatus.REVIEW,
            uploaded_at=datetime(2026, 1, 1, tzinfo=UTC),
            extraction=payload,
        )

    async def get_document(self, document_id: str) -> DocumentDetail:
        assert document_id == self.detail.id
        return self.detail

    async def get_document_recipe(self, document_id: str, recipe_id: str) -> dict[str, str]:
        assert self.recipe and document_id == self.detail.id and recipe_id == "synthetic-recipe"
        return {"active_extraction_recipe_id": "synthetic-extraction"}

    async def get_document_extraction_artifact(self, **kwargs: Any) -> dict[str, Any]:
        assert kwargs == {
            "document_id": self.detail.id,
            "extraction_recipe_id": "synthetic-extraction",
        }
        return {"extraction_json": self.payload}

    async def update_document_status(self, *args: Any, **kwargs: Any) -> None:
        self.write_attempts.append("document-status")
        raise ContinuedPastQualityGate

    async def update_document_recipe_status(self, **kwargs: Any) -> None:
        self.write_attempts.append("recipe-status")
        raise ContinuedPastQualityGate


def saved_payload(state: str) -> dict[str, Any]:
    extraction = StructuredExtraction(
        raw_text="合成の保存済み本文",
        elements=[DocumentElement(kind="text", text="合成の保存済み本文")],
        confidence=0.1 if state == "low-confidence" else 0.95,
        warnings=["excel_header_low_confidence"] if state == "review-warning" else [],
    )
    if state not in {"absent", "null"}:
        report = build_ingestion_quality_report(
            extraction,
            parser_profile="synthetic-parser",
            parser_backend="synthetic",
            parser_version="synthetic-v1",
        )
        extraction = extraction.model_copy(update={"quality_report": report})
    payload = extraction.model_dump(mode="json")
    if state == "absent":
        del payload["quality_report"]
    return payload


@pytest.mark.parametrize("method", ["index_reviewed", "chunk_reviewed", "index_chunked"])
@pytest.mark.parametrize("recipe", [False, True], ids=["legacy-document", "recipe"])
@pytest.mark.parametrize("state", ["absent", "null", "normal", "low-confidence", "review-warning"])
async def test_saved_resume_never_reconstructs_a_missing_quality_report(
    method: str, recipe: bool, state: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    payload = saved_payload(state)
    oracle = SavedExtractionOracle(payload, recipe)
    pipeline = object.__new__(module.IngestionPipeline)
    pipeline._recipe_id = "synthetic-recipe" if recipe else None
    pipeline._oracle = cast(Any, oracle)
    report_builds: list[str] = []
    original_builder = build_ingestion_quality_report

    def traced_builder(*args: Any, **kwargs: Any) -> Any:
        report_builds.append("rebuilt")
        return original_builder(*args, **kwargs)

    monkeypatch.setattr(module, "build_ingestion_quality_report", traced_builder)
    before = oracle.detail.model_dump(mode="json")
    kwargs = {"chunk_set_id": "synthetic-chunk-set"} if method == "index_chunked" else {}
    if state in {"absent", "null"}:
        with pytest.raises(module.IngestionUserError, match="品質レポート.*再解析"):
            await getattr(pipeline, method)(oracle.detail.id, **kwargs)
        assert oracle.write_attempts == []
    else:
        with pytest.raises(ContinuedPastQualityGate):
            await getattr(pipeline, method)(oracle.detail.id, **kwargs)
        assert oracle.write_attempts == ["recipe-status" if recipe else "document-status"]
        loaded = await pipeline._load_reviewed_extraction(oracle.detail)
        assert loaded.quality_report is not None
        assert loaded.quality_report.model_dump(mode="json") == payload["quality_report"]
        if state == "low-confidence":
            assert loaded.quality_report.risk_level == "high"
            assert "low_extraction_confidence" in loaded.quality_report.quality_warnings
        if state == "review-warning":
            assert "excel_header_low_confidence" in loaded.quality_report.quality_warnings
    assert report_builds == []
    assert oracle.detail.model_dump(mode="json") == before
