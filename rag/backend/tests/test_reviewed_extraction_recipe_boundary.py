"""保存済み抽出の続行は、明示したレシピの版だけを読む。"""

from datetime import UTC, datetime
from typing import Any, cast

import pytest

from app.rag import ingestion as module
from app.schemas.document import DocumentDetail, FileStatus
from app.schemas.extraction import StructuredExtraction


class ExtractionStore:
    def __init__(self, state: str) -> None:
        self.state = state
        self.calls: list[str] = []
        self.payload = StructuredExtraction(raw_text="合成の正式版").model_dump(mode="json")

    async def get_document_recipe(self, document_id: str, recipe_id: str) -> Any:
        self.calls.append("recipe")
        assert document_id == "synthetic-document"
        assert recipe_id == "synthetic-recipe"
        if self.state == "recipe-error":
            raise RuntimeError("synthetic recipe failure")
        if self.state == "recipe-missing":
            return None
        return {
            "active_extraction_recipe_id": None
            if self.state == "pointer-missing"
            else "synthetic-extraction"
        }

    async def get_document_extraction_artifact(self, **kwargs: Any) -> Any:
        self.calls.append("extraction")
        assert kwargs == {
            "document_id": "synthetic-document",
            "extraction_recipe_id": "synthetic-extraction",
        }
        if self.state == "extraction-error":
            raise RuntimeError("synthetic extraction failure")
        if self.state == "extraction-missing":
            return None
        if self.state == "payload-missing":
            return {"extraction_json": None}
        return {"extraction_json": self.payload}


def pipeline_and_detail(
    store: ExtractionStore, recipe: bool, source_hash: bool
) -> tuple[module.IngestionPipeline, DocumentDetail]:
    pipeline = object.__new__(module.IngestionPipeline)
    pipeline._recipe_id = "synthetic-recipe" if recipe else None
    pipeline._oracle = cast(Any, store)
    pipeline._settings = cast(Any, object())
    detail = DocumentDetail(
        id="synthetic-document",
        file_name="synthetic.txt",
        status=FileStatus.REVIEW,
        uploaded_at=datetime(2026, 1, 1, tzinfo=UTC),
        content_sha256="a" * 64 if source_hash else None,
        extraction=StructuredExtraction(raw_text="合成の旧列").model_dump(mode="json"),
    )
    return pipeline, detail


@pytest.mark.parametrize("source_hash", [False, True])
@pytest.mark.parametrize("state", ["normal", "extraction-missing", "extraction-error"])
async def test_no_recipe_rejects_before_reading_old_or_recomputed_source(
    source_hash: bool, state: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = ExtractionStore(state)
    pipeline, detail = pipeline_and_detail(store, False, source_hash)
    monkeypatch.setattr(module, "compute_extraction_recipe_id", lambda *_: "synthetic-extraction")
    before = detail.model_dump(mode="json")
    with pytest.raises(module.IngestionUserError, match="レシピ"):
        await pipeline._load_reviewed_extraction(detail)
    assert store.calls == []
    assert detail.model_dump(mode="json") == before


@pytest.mark.parametrize(
    "state", ["recipe-missing", "pointer-missing", "extraction-missing", "payload-missing"]
)
async def test_formal_missing_source_never_uses_document_column(state: str) -> None:
    store = ExtractionStore(state)
    pipeline, detail = pipeline_and_detail(store, True, True)
    with pytest.raises(module.IngestionUserError, match="抽出結果が見つかりません"):
        await pipeline._load_reviewed_extraction(detail)


@pytest.mark.parametrize("state", ["recipe-error", "extraction-error"])
async def test_formal_store_error_propagates(state: str) -> None:
    store = ExtractionStore(state)
    pipeline, detail = pipeline_and_detail(store, True, True)
    with pytest.raises(RuntimeError, match="synthetic"):
        await pipeline._load_reviewed_extraction(detail)


async def test_formal_recipe_returns_only_its_saved_extraction() -> None:
    store = ExtractionStore("normal")
    pipeline, detail = pipeline_and_detail(store, True, True)
    loaded = await pipeline._load_reviewed_extraction(detail)
    assert loaded.model_dump(mode="json") == store.payload
    assert store.calls == ["recipe", "extraction"]
    assert detail.extraction != store.payload
